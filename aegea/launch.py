"""
Launch a new EC2 instance.

Depending on the options given, this command may use the EC2
RunInstances, RequestSpotInstances, or RequestSpotFleet API. Run
"aegea ls", "aegea sirs" and "aegea sfrs" to see the status of the
instances and related spot instance and fleet requests.

The --spot and --spot-price options trigger the use of the
RequestSpotInstances API. The --duration-hours, --cores, and
--min-mem-per-core-gb options trigger the use of the RequestSpotFleet
API.

The return value (stdout) is a JSON object with one key, ``instance_id``.

Examples:

- Launch an on-demand t3.micro instance with the root volume enlarged to 64GB and another 64GB volume attached at /mnt:
    aegea launch my-instance --storage /=64GB /mnt=64GB

- Launch a spot r5d.xlarge instance with the home directory mounted on EFS:
    aegea launch my-instance --spot-price 1 --instance-type r5d.xlarge --efs-home
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, time, datetime, base64, json
from typing import Dict, List

import yaml

from . import register_parser, logger, config
from .efs import create as create_efs, parser_create as parser_create_efs, __name__ as efs_security_group_name
from .util import wait_for_port, validate_hostname, paginate
from .util.cloudinit import get_user_data
from .util.aws import (ensure_vpc, ensure_subnet, ensure_security_group, ensure_log_group,
                       add_tags, resolve_security_group, get_bdm, resolve_instance_id, expect_error_codes, resolve_ami,
                       locate_ami, get_ondemand_price_usd, resources, clients, ARN, instance_type_completer,
                       get_ssm_parameter, encode_tags)
from .util.aws.dns import DNSZone, get_client_token
from .util.aws.spot import SpotFleetBuilder
from .util.aws.iam import ensure_instance_profile, compose_managed_policies
from .util.crypto import new_ssh_key, add_ssh_host_key_to_known_hosts, ensure_ssh_key, hostkey_line
from .util.exceptions import AegeaException
from botocore.exceptions import ClientError

def get_spot_bid_price(instance_type, ondemand_multiplier=1.2):
    ondemand_price = get_ondemand_price_usd(clients.ec2.meta.region_name, instance_type)
    return float(ondemand_price) * ondemand_multiplier

def get_startup_commands(args, username):
    hostname = ".".join([args.hostname, config.dns.private_zone.rstrip(".")]) if args.use_dns else args.hostname
    return [
        "hostnamectl set-hostname " + hostname,
        "service awslogs restart",
        "echo tsc > /sys/devices/system/clocksource/clocksource0/current_clocksource",
        "ssh -oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no {}@localhost -N || true".format(username)
    ] + args.commands

def get_ssh_ca_keys(bless_config):
    for lambda_regional_config in bless_config["lambda_config"]["regions"]:
        if lambda_regional_config["aws_region"] == clients.ec2.meta.region_name:
            break
    ca_keys_secret_arn = ARN(service="secretsmanager",
                             region=lambda_regional_config["aws_region"],
                             account_id=ARN(bless_config["lambda_config"]["role_arn"]).account_id,
                             resource="secret:" + bless_config["lambda_config"]["function_name"])
    ca_keys_secret = clients.secretsmanager.get_secret_value(SecretId=str(ca_keys_secret_arn))
    ca_keys = json.loads(ca_keys_secret["SecretString"])["ssh_ca_keys"]
    return "\n".join(ca_keys)

def infer_architecture(instance_type):
    if any((instance_type.startswith(f) for f in ("x2g", "m6g", "c6g", "r6g", "t4g", "a1"))):
        return "arm64"
    return "x86_64"

def launch(args):
    if args.spot_price or args.duration_hours or args.cores or args.min_mem_per_core_gb:
        args.spot = True
    if args.use_dns:
        dns_zone = DNSZone()
    ssh_key_name = ensure_ssh_key(name=args.ssh_key_name, base_name=__name__,
                                  verify_pem_file=args.verify_ssh_key_pem_file)
    # TODO: move all account init checks into init helper with region-specific semaphore on s3
    try:
        ensure_log_group("syslog")
    except ClientError:
        logger.warn("Unable to query or create cloudwatch syslog group. Logs may be undeliverable")
    try:
        i = resolve_instance_id(args.hostname)
        msg = "The hostname {} is being used by {} (state: {})"
        raise Exception(msg.format(args.hostname, i, resources.ec2.Instance(i).state["Name"]))
    except AegeaException:
        validate_hostname(args.hostname)
        assert not args.hostname.startswith("i-")
    ami_tags = dict(tag.split("=", 1) for tag in args.ami_tags or [])
    arch = infer_architecture(instance_type=args.instance_type)
    if args.ubuntu_linux_ami:
        args.ami = locate_ami("Ubuntu", release="20.04", architecture=arch)
    elif args.amazon_linux_ami:
        args.ami = locate_ami("Amazon Linux", release="2", architecture=arch)
    else:
        try:
            args.ami = resolve_ami(args.ami, tags=ami_tags, arch=arch)
        except AegeaException as e:
            if args.ami is None and len(ami_tags) == 0 and "Could not resolve AMI" in str(e):
                raise AegeaException("No AMI was given, and no " + arch + " AMIs were found in this account. "
                                     "To build an aegea AMI, use aegea build-ami --architecture " + arch + ". "
                                     "To use the default Ubuntu Linux LTS AMI, use --ubuntu-linux-ami. "
                                     "To use the default Amazon Linux 2 AMI, use --amazon-linux-ami. ")
            raise
    if args.subnet:
        subnet = resources.ec2.Subnet(args.subnet)
        vpc = resources.ec2.Vpc(subnet.vpc_id)
    else:
        vpc = ensure_vpc()
        subnet = ensure_subnet(vpc, availability_zone=args.availability_zone)

    if args.security_groups:
        security_groups = [resolve_security_group(sg, vpc) for sg in args.security_groups]
    else:
        security_groups = [ensure_security_group(__name__, vpc)]

    if args.efs_home:
        for filesystem in clients.efs.describe_file_systems()["FileSystems"]:
            if {"Key": "mountpoint", "Value": "/home"} in filesystem["Tags"]:
                break
        else:
            create_efs_args = ["aegea_home", "--tags", "mountpoint=/home", "managedBy=aegea"]
            create_efs(parser_create_efs.parse_args(create_efs_args))
        security_groups.append(resolve_security_group(efs_security_group_name, vpc))

    ssh_host_key = new_ssh_key()
    user_data_args = dict(host_key=ssh_host_key,
                          commands=get_startup_commands(args, ARN.get_iam_username()),
                          packages=args.packages,
                          storage=args.storage)
    if args.bless_config:
        with open(args.bless_config) as fh:
            bless_config = yaml.safe_load(fh)
        user_data_args["ssh_ca_keys"] = get_ssh_ca_keys(bless_config)
        user_data_args["provision_users"] = bless_config["client_config"]["remote_users"]

    hkl = hostkey_line(hostnames=[], key=ssh_host_key).strip()
    instance_tags = dict(Name=args.hostname, Owner=ARN.get_iam_username(),
                         SSHHostPublicKeyPart1=hkl[:255], SSHHostPublicKeyPart2=hkl[255:],
                         OwnerSSHKeyName=ssh_key_name, **dict(args.tags))
    user_data_args.update(dict(args.cloud_config_data))
    launch_spec = dict(ImageId=args.ami,
                       KeyName=ssh_key_name,
                       SecurityGroupIds=[sg.id for sg in security_groups],
                       InstanceType=args.instance_type,
                       BlockDeviceMappings=get_bdm(ami=args.ami, ebs_storage=args.storage),
                       UserData=get_user_data(**user_data_args))
    tag_spec = dict(ResourceType="instance", Tags=encode_tags(instance_tags))
    logger.info("Launch spec user data is %i bytes long", len(launch_spec["UserData"]))
    if args.iam_role:
        umbrella_policy = compose_managed_policies(args.iam_policies)
        instance_profile = ensure_instance_profile(args.iam_role, policies=[umbrella_policy])
        launch_spec["IamInstanceProfile"] = dict(Arn=instance_profile.arn)
    if not args.spot:
        launch_spec["SubnetId"] = subnet.id
    if args.availability_zone:
        launch_spec["Placement"] = dict(AvailabilityZone=args.availability_zone)
    if args.client_token is None:
        args.client_token = get_client_token(ARN.get_iam_username(), __name__)
    try:
        if args.spot:
            launch_spec["UserData"] = base64.b64encode(launch_spec["UserData"]).decode()
            if args.duration_hours or args.cores or args.min_mem_per_core_gb:
                spot_fleet_args = dict(launch_spec=dict(launch_spec, TagSpecifications=[tag_spec]),
                                       client_token=args.client_token)
                for arg in "cores", "min_mem_per_core_gb", "spot_price", "duration_hours", "dry_run":
                    if getattr(args, arg, None):
                        spot_fleet_args[arg] = getattr(args, arg)
                if "cores" in spot_fleet_args:
                    spot_fleet_args["min_cores_per_instance"] = spot_fleet_args["cores"]
                if args.instance_type != parser.get_default("instance_type"):
                    msg = ("Using --instance-type with spot fleet may unnecessarily constrain available instances. "
                           "Consider using --cores and --min-mem-per-core-gb instead")
                    logger.warn(msg)

                    class InstanceSpotFleetBuilder(SpotFleetBuilder):
                        def instance_types(self, **kwargs):
                            yield args.instance_type, 1
                    spot_fleet_builder = InstanceSpotFleetBuilder(**spot_fleet_args)  # type: SpotFleetBuilder
                else:
                    spot_fleet_builder = SpotFleetBuilder(**spot_fleet_args)
                logger.info("Launching {}".format(spot_fleet_builder))
                sfr_id = spot_fleet_builder()
                instances = []  # type: List[Dict]
                while not instances:
                    res = clients.ec2.describe_spot_fleet_instances(SpotFleetRequestId=sfr_id)
                    instances = res["ActiveInstances"]
                    time.sleep(0 if instances else 1)
                # FIXME: there may be multiple instances, and spot fleet provides no indication of whether the SFR is
                # fulfilled
                instance = resources.ec2.Instance(instances[0]["InstanceId"])
            else:
                if args.spot_price is None:
                    args.spot_price = get_spot_bid_price(args.instance_type)
                logger.info("Bidding ${}/hour for a {} spot instance".format(args.spot_price, args.instance_type))
                res = clients.ec2.request_spot_instances(
                    SpotPrice=str(args.spot_price),
                    ValidUntil=datetime.datetime.utcnow() + datetime.timedelta(hours=1),
                    LaunchSpecification=launch_spec,
                    ClientToken=args.client_token,
                    DryRun=args.dry_run
                )
                sir_id = res["SpotInstanceRequests"][0]["SpotInstanceRequestId"]
                clients.ec2.get_waiter("spot_instance_request_fulfilled").wait(SpotInstanceRequestIds=[sir_id])
                res = clients.ec2.describe_spot_instance_requests(SpotInstanceRequestIds=[sir_id])
                instance = resources.ec2.Instance(res["SpotInstanceRequests"][0]["InstanceId"])
                add_tags(instance, **instance_tags)
        else:
            launch_spec = dict(launch_spec, TagSpecifications=[tag_spec])
            instances = resources.ec2.create_instances(MinCount=1, MaxCount=1, ClientToken=args.client_token,
                                                       DryRun=args.dry_run, **launch_spec)
            instance = instances[0]
    except ClientError as e:
        expect_error_codes(e, "DryRunOperation")
        logger.info("Dry run succeeded")
        exit()
    while instance.state["Name"] != "running":
        logger.debug("%s %s", instance, instance.state["Name"])
        instance = resources.ec2.Instance(instance.id)
        time.sleep(1)
    if args.use_dns:
        dns_zone.update(args.hostname, instance.private_dns_name)
    add_ssh_host_key_to_known_hosts(hostkey_line([instance.public_dns_name or instance.id], ssh_host_key))
    if args.wait_for_ssh:
        wait_for_port(instance.public_dns_name, 22)
    logger.info("Launched %s in %s using %s", instance, subnet, args.ami)
    return dict(instance_id=instance.id)

parser = register_parser(launch)
parser.add_argument("hostname")
parser.add_argument("--storage", nargs="+", metavar="MOUNTPOINT=SIZE_GB",
                    type=lambda x: x.rstrip("GBgb").split("=", 1),
                    help="At launch time, attach EBS volume(s) of this size, format and mount them.")
parser.add_argument("--efs-home", action="store_true",
                    help="Create and manage an EFS filesystem that the instance will use for user home directories")
parser.add_argument("--commands", nargs="+", metavar="COMMAND", help="Commands to run on host upon startup")
parser.add_argument("--packages", nargs="+", metavar="PACKAGE", help="APT packages to install on host upon startup")
parser.add_argument("--ssh-key-name")
parser.add_argument("--no-verify-ssh-key-pem-file", dest="verify_ssh_key_pem_file", action="store_false")
parser.add_argument("--bless-config", default=os.environ.get("BLESS_CONFIG"),
                    help="Path to a Bless configuration file (or pass via the BLESS_CONFIG environment variable)")
parser.add_argument("--ami", help="AMI to use for the instance. Default: " + resolve_ami.__doc__)  # type: ignore
parser.add_argument("--ami-tags", nargs="+", metavar="NAME=VALUE", help="Use the most recent AMI with these tags")
parser.add_argument("--ubuntu-linux-ami", action="store_true", help="Use the most recent Ubuntu Linux LTS AMI")
parser.add_argument("--amazon-linux-ami", action="store_true", help="Use the most recent Amazon Linux 2 AMI")
parser.add_argument("--spot", action="store_true",
                    help="Launch a preemptible spot instance, which is cheaper but could be forced to shut down")
parser.add_argument("--duration-hours", type=float, help="Terminate the spot instance after this number of hours")
parser.add_argument("--cores", type=int, help="Minimum number of cores to request (spot fleet API)")
parser.add_argument("--min-mem-per-core-gb", type=float)
parser.add_argument("--instance-type", "-t", help="See https://ec2instances.info/").completer = instance_type_completer
parser.add_argument("--spot-price", type=float,
                    help="Maximum bid price for spot instances. Defaults to 1.2x the ondemand price.")
parser.add_argument("--no-dns", dest="use_dns", action="store_false", help="""
Skip registering instance name in private DNS (if you don't want launching principal to have Route53 write access)""")
parser.add_argument("--client-token", help="Token used to identify your instance, SIR or SFR")
parser.add_argument("--subnet")
parser.add_argument("--availability-zone", "--az")
parser.add_argument("--security-groups", nargs="+", metavar="SECURITY_GROUP")
parser.add_argument("--tags", nargs="+", metavar="NAME=VALUE", type=lambda x: x.split("=", 1),
                    help="Tags to apply to launched instances.")
parser.add_argument("--wait-for-ssh", action="store_true",
                    help="Wait for launched instance to begin accepting SSH connections. Security groups and NACLs must permit SSH from launching host.")  # noqa
parser.add_argument("--iam-role", default=__name__,
                    help="Pass this IAM role to the launched instance through an instance profile. Role credentials will become available in the instance metadata.")  # noqa
parser.add_argument("--iam-policies", nargs="+", metavar="IAM_POLICY_NAME",
                    help="Ensure the default or specified IAM role has the listed IAM managed policies attached")
parser.add_argument("--cloud-config-data", type=json.loads)
parser.add_argument("--dry-run", "--dryrun", action="store_true")
