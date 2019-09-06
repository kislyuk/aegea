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
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, time, datetime, base64, json

from . import register_parser, logger, config

from .util import wait_for_port, validate_hostname, paginate
from .util.cloudinit import get_user_data
from .util.aws import (ensure_vpc, ensure_subnet, ensure_security_group, ensure_log_group, ensure_instance_profile,
                       add_tags, resolve_security_group, get_bdm, resolve_instance_id, expect_error_codes, resolve_ami,
                       get_ondemand_price_usd, resources, clients, ARN)
from .util.aws.dns import DNSZone, get_client_token
from .util.aws.spot import SpotFleetBuilder
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

def launch(args):
    if args.spot_price or args.duration_hours or args.cores or args.min_mem_per_core_gb:
        args.spot = True
    if args.use_dns:
        dns_zone = DNSZone(config.dns.get("private_zone"))
        config.dns.private_zone = dns_zone.zone["Name"]
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
    args.ami = resolve_ami(args.ami, **ami_tags)
    if args.subnet:
        subnet = resources.ec2.Subnet(args.subnet)
        vpc = resources.ec2.Vpc(subnet.vpc_id)
    else:
        vpc = ensure_vpc()
        subnet = ensure_subnet(vpc, availability_zone=args.availability_zone)
    if not subnet.map_public_ip_on_launch:
        raise AegeaException("Subnets without public IP mapping are not supported")

    if args.security_groups:
        security_groups = [resolve_security_group(sg, vpc) for sg in args.security_groups]
    else:
        security_groups = [ensure_security_group(__name__, vpc)]

    ssh_host_key = new_ssh_key()
    user_data_args = dict(host_key=ssh_host_key,
                          commands=get_startup_commands(args, ARN.get_iam_username()),
                          packages=args.packages,
                          storage=args.storage)
    user_data_args.update(dict(args.cloud_config_data))
    launch_spec = dict(ImageId=args.ami,
                       KeyName=ssh_key_name,
                       SecurityGroupIds=[sg.id for sg in security_groups],
                       InstanceType=args.instance_type,
                       BlockDeviceMappings=get_bdm(ebs_storage=args.storage),
                       UserData=get_user_data(**user_data_args))
    logger.info("Launch spec user data is %i bytes long", len(launch_spec["UserData"]))
    if args.iam_role:
        instance_profile = ensure_instance_profile(args.iam_role, policies=args.iam_policies)
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
                spot_fleet_args = dict(launch_spec=launch_spec, client_token=args.client_token)
                for arg in "cores", "min_mem_per_core_gb", "spot_price", "duration_hours", "dry_run":
                    if getattr(args, arg, None):
                        spot_fleet_args[arg] = getattr(args, arg)
                if "cores" in spot_fleet_args:
                    spot_fleet_args["min_cores_per_instance"] = spot_fleet_args["cores"]
                if args.instance_type != parser.get_default("instance_type"):
                    msg = "Using --instance-type with spot fleet may unnecessarily constrain available instances. Consider using --cores and --min-mem-per-core-gb instead" # noqa
                    logger.warn(msg)

                    class InstanceSpotFleetBuilder(SpotFleetBuilder):
                        def instance_types(self, **kwargs):
                            yield args.instance_type, 1
                    spot_fleet_builder = InstanceSpotFleetBuilder(**spot_fleet_args)
                else:
                    spot_fleet_builder = SpotFleetBuilder(**spot_fleet_args)
                logger.info("Launching {}".format(spot_fleet_builder))
                sfr_id = spot_fleet_builder()
                instances = []
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
        else:
            instances = resources.ec2.create_instances(MinCount=1, MaxCount=1, ClientToken=args.client_token,
                                                       DryRun=args.dry_run, **launch_spec)
            instance = instances[0]
    except ClientError as e:
        expect_error_codes(e, "DryRunOperation")
        logger.info("Dry run succeeded")
        exit()
    instance.wait_until_running()
    hkl = hostkey_line(hostnames=[], key=ssh_host_key).strip()
    tags = dict(tag.split("=", 1) for tag in args.tags)
    add_tags(instance, Name=args.hostname, Owner=ARN.get_iam_username(),
             SSHHostPublicKeyPart1=hkl[:255], SSHHostPublicKeyPart2=hkl[255:],
             OwnerSSHKeyName=ssh_key_name, **tags)
    if args.use_dns:
        dns_zone.update(args.hostname, instance.private_dns_name)
    while not instance.public_dns_name:
        instance = resources.ec2.Instance(instance.id)
        time.sleep(1)
    add_ssh_host_key_to_known_hosts(hostkey_line([instance.public_dns_name], ssh_host_key))
    if args.wait_for_ssh:
        wait_for_port(instance.public_dns_name, 22)
    logger.info("Launched %s in %s using %s", instance, subnet, args.ami)
    return dict(instance_id=instance.id)

parser = register_parser(launch)
parser.add_argument("hostname")
parser.add_argument("--commands", nargs="+", metavar="COMMAND", help="Commands to run on host upon startup")
parser.add_argument("--packages", nargs="+", metavar="PACKAGE", help="APT packages to install on host upon startup")
parser.add_argument("--ssh-key-name")
parser.add_argument("--no-verify-ssh-key-pem-file", dest="verify_ssh_key_pem_file", action="store_false")
parser.add_argument("--ami", help="AMI to use for the instance. Default: " + resolve_ami.__doc__)
parser.add_argument("--ami-tags", nargs="+", metavar="NAME=VALUE", help="Use the most recent AMI with these tags")
parser.add_argument("--spot", action="store_true")
parser.add_argument("--duration-hours", type=float, help="Terminate the spot instance after this number of hours")
parser.add_argument("--cores", type=int, help="Minimum number of cores to request (spot fleet API)")
parser.add_argument("--min-mem-per-core-gb", type=float)
parser.add_argument("--instance-type", "-t", default="t2.micro")
parser.add_argument("--spot-price", type=float,
                    help="Maximum bid price for spot instances. Defaults to 1.2x the ondemand price.")
parser.add_argument("--no-dns", dest="use_dns", action="store_false", help="""
Skip registering instance name in private DNS (if you don't want launching principal to have Route53 write access)""")
parser.add_argument("--client-token", help="Token used to identify your instance, SIR or SFR")
parser.add_argument("--subnet")
parser.add_argument("--availability-zone", "--az")
parser.add_argument("--security-groups", nargs="+", metavar="SECURITY_GROUP")
parser.add_argument("--tags", nargs="+", default=[], metavar="NAME=VALUE", help="Tags to apply to launched instances.")
parser.add_argument("--wait-for-ssh", action="store_true",
                    help="Wait for launched instance to begin accepting SSH connections. Security groups and NACLs must permit SSH from launching host.")  # noqa
parser.add_argument("--storage", nargs="+", metavar="MOUNTPOINT=SIZE_GB",
                    type=lambda x: x.rstrip("GBgb").split("=", 1), default=[],
                    help="At launch time, attach EBS volume(s) of this size, format and mount them.")
parser.add_argument("--iam-role", default=__name__,
                    help="Pass this IAM role to the launched instance through an instance profile. Role credentials will become available in the instance metadata.")  # noqa
parser.add_argument("--iam-policies", nargs="+", metavar="IAM_POLICY_NAME",
                    default=["IAMReadOnlyAccess",
                             "AmazonElasticFileSystemFullAccess",
                             "service-role/AmazonAPIGatewayPushToCloudWatchLogs"],
                    help="Ensure the default or specified IAM role has the listed IAM managed policies attached")
parser.add_argument("--cloud-config-data", type=json.loads)
parser.add_argument("--dry-run", "--dryrun", action="store_true")
