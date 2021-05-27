"""
Connect to an EC2 instance via SSH, by name or instance ID.

Security groups, network ACLs, interfaces, VPC routing tables, VPC
Internet Gateways, and internal firewalls for the instance must be
configured to allow SSH connections.

To facilitate SSH connections, ``aegea ssh`` resolves instance names
to public DNS names assigned by AWS, and securely retrieves SSH host
public keys from instance metadata before connecting. This avoids both
the prompt to save the instance public key and the resulting transient
MITM vulnerability.

``aegea ssh`` also supports Bless, via the --bless-config CONFIG_FILE
option or the BLESS_CONFIG environment variable. This should point to a
YAML file with the format described in
https://github.com/chanzuckerberg/blessclient/blob/master/examples/config.yml.
"""

import os, sys, argparse, string, datetime, json, base64, time, fnmatch, subprocess, hashlib

import boto3, yaml

from . import register_parser, logger
from .util.aws import resolve_instance_id, resources, clients, ARN
from .util.crypto import (add_ssh_host_key_to_known_hosts, ensure_local_ssh_key, get_public_key_from_pair,
                          add_ssh_key_to_agent, get_ssh_key_path, get_ssh_id)
from .util.printing import BOLD
from .util.exceptions import AegeaException
from .util.compat import lru_cache
from .util.aws.ssm import ensure_session_manager_plugin, run_command

opts_by_nargs = {
    "ssh": {0: "46AaCfGgKkMNnqsTtVvXxYy", 1: "BbcDEeFIiJLlmOopQRSW"},
    "scp": {0: "346BCpqrv", 1: "cFiloPS"}
}

def add_bless_and_passthrough_opts(parser, program):
    parser.add_argument("--bless-config", default=os.environ.get("BLESS_CONFIG"),
                        help="Path to a Bless configuration file (or pass via the BLESS_CONFIG environment variable)")
    parser.add_argument("--use-kms-auth", help=argparse.SUPPRESS)
    for opt in opts_by_nargs[program][0]:
        parser.add_argument("-" + opt, action="store_true", help=argparse.SUPPRESS)
    for opt in opts_by_nargs[program][1]:
        parser.add_argument("-" + opt, action="append", help=argparse.SUPPRESS)

def extract_passthrough_opts(args, program):
    opts = []
    for opt in opts_by_nargs[program][0]:
        if getattr(args, opt):
            opts.append("-" + opt)
    for opt in opts_by_nargs[program][1]:
        for value in getattr(args, opt) or []:
            opts.extend(["-" + opt, value])
    return opts

@lru_cache(8)
def get_instance(name):
    return resources.ec2.Instance(resolve_instance_id(name))

def save_instance_public_key(name, use_ssm=False):
    instance = get_instance(name)
    tags = {tag["Key"]: tag["Value"] for tag in instance.tags or []}
    ssh_host_key = tags.get("SSHHostPublicKeyPart1", "") + tags.get("SSHHostPublicKeyPart2", "")
    if ssh_host_key:
        # FIXME: this results in duplicates.
        # Use paramiko to detect if the key is already listed and not insert it then (or only insert if different)
        hostname = instance.id if use_ssm else instance.public_dns_name
        add_ssh_host_key_to_known_hosts(hostname + " " + ssh_host_key + "\n")

def resolve_instance_public_dns(name):
    instance = get_instance(name)
    if not getattr(instance, "public_dns_name", None):
        msg = "Unable to resolve public DNS name for {} (state: {})"
        raise AegeaException(msg.format(instance, getattr(instance, "state", {}).get("Name")))
    return instance.public_dns_name

def get_user_info():
    res = clients.sts.get_caller_identity()
    iam_username = ARN(res["Arn"]).resource.split("/")[-1]
    linux_username, at, domain = iam_username.partition("@")
    iam_user_id = res["UserId"]
    iam_user_id, colon, session = iam_user_id.partition(":")
    user_id_bytes = hashlib.sha256(iam_user_id.encode()).digest()[-2:]
    linux_user_id = str(2000 + (int.from_bytes(user_id_bytes, byteorder=sys.byteorder) // 2))
    return dict(iam_username=iam_username, linux_username=linux_username, linux_user_id=linux_user_id)

def get_kms_auth_token(session, bless_config, lambda_regional_config):
    logger.info("Requesting new KMS auth token in %s", lambda_regional_config["aws_region"])
    token_not_before = datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
    token_not_after = token_not_before + datetime.timedelta(hours=1)
    token = dict(not_before=token_not_before.strftime("%Y%m%dT%H%M%SZ"),
                 not_after=token_not_after.strftime("%Y%m%dT%H%M%SZ"))
    encryption_context = {
        "from": session.resource("iam").CurrentUser().user_name,
        "to": bless_config["lambda_config"]["function_name"],
        "user_type": "user"
    }
    kms = session.client('kms', region_name=lambda_regional_config["aws_region"])
    res = kms.encrypt(KeyId=lambda_regional_config["kms_auth_key_id"],
                      Plaintext=json.dumps(token),
                      EncryptionContext=encryption_context)
    return base64.b64encode(res["CiphertextBlob"]).decode()

def get_awslambda_client(region_name, credentials):
    return boto3.client("lambda",
                        region_name=region_name,
                        aws_access_key_id=credentials['AccessKeyId'],
                        aws_secret_access_key=credentials['SecretAccessKey'],
                        aws_session_token=credentials['SessionToken'])

def ensure_bless_ssh_cert(ssh_key_name, bless_config, use_kms_auth, max_cert_age=1800):
    ssh_key = ensure_local_ssh_key(ssh_key_name)
    ssh_key_filename = get_ssh_key_path(ssh_key_name)
    ssh_cert_filename = ssh_key_filename + "-cert.pub"
    if os.path.exists(ssh_cert_filename) and time.time() - os.stat(ssh_cert_filename).st_mtime < max_cert_age:
        logger.info("Using cached Bless SSH certificate %s", ssh_cert_filename)
        return ssh_cert_filename
    logger.info("Requesting new Bless SSH certificate")

    for lambda_regional_config in bless_config["lambda_config"]["regions"]:
        if lambda_regional_config["aws_region"] == clients.ec2.meta.region_name:
            break

    if "oidc_client_id" in bless_config["client_config"]:
        from cryptography.hazmat.primitives import serialization
        aws_oidc_args = ["--client-id", bless_config["client_config"]["oidc_client_id"],
                         "--issuer-url", bless_config["client_config"]["oidc_issuer_url"]]
        aws_role_arn_arg = ["--aws-role-arn", bless_config["client_config"]["role_arn"]]
        token = json.loads(subprocess.check_output(["aws-oidc", "token"] + aws_oidc_args))["access_token"]
        creds = json.loads(subprocess.check_output(["aws-oidc", "creds-process"] + aws_oidc_args + aws_role_arn_arg))
        awslambda = get_awslambda_client(region_name=lambda_regional_config["aws_region"], credentials=creds)
        public_key = ssh_key.key.public_key().public_bytes(encoding=serialization.Encoding.PEM,
                                                           format=serialization.PublicFormat.SubjectPublicKeyInfo)
        bless_input = dict(public_key_to_sign=dict(publicKey="".join(public_key.decode().splitlines()[1:-1])),
                           identity=dict(okta_identity=dict(AccessToken=token)))
    else:
        session = boto3.Session(profile_name=bless_config["client_config"]["aws_user_profile"])
        iam = session.resource("iam")
        sts = session.client("sts")
        assume_role_res = sts.assume_role(RoleArn=bless_config["lambda_config"]["role_arn"], RoleSessionName=__name__)
        awslambda = get_awslambda_client(region_name=lambda_regional_config["aws_region"],
                                         credentials=assume_role_res["Credentials"])
        bless_input = dict(bastion_user=iam.CurrentUser().user_name,
                           bastion_user_ip="0.0.0.0/0",
                           bastion_ips=",".join(bless_config["client_config"]["bastion_ips"]),
                           remote_usernames=",".join(bless_config["client_config"]["remote_users"]),
                           public_key_to_sign=get_public_key_from_pair(ssh_key),
                           command="*")
        if use_kms_auth:
            bless_input["kmsauth_token"] = get_kms_auth_token(session=session,
                                                              bless_config=bless_config,
                                                              lambda_regional_config=lambda_regional_config)

    res = awslambda.invoke(FunctionName=bless_config["lambda_config"]["function_name"], Payload=json.dumps(bless_input))
    bless_output = json.loads(res["Payload"].read().decode())
    if "certificate" not in bless_output:
        raise AegeaException("Error while requesting Bless SSH certificate: {}".format(bless_output))
    with open(ssh_cert_filename, "w") as fh:
        if isinstance(bless_output["certificate"], dict):
            fh.write("ssh-rsa-cert-v01@openssh.com " + bless_output["certificate"]["cert"])
        else:
            fh.write(bless_output["certificate"])
    return ssh_cert_filename

def match_instance_to_bastion(instance, bastions):
    for bastion_config in bastions:
        for ipv4_pattern in bastion_config["hosts"]:
            if fnmatch.fnmatch(instance.private_ip_address, ipv4_pattern["pattern"]):
                logger.info("Using %s to connect to %s", bastion_config["pattern"], instance)
                return bastion_config

def prepare_ssh_host_opts(username, hostname, bless_config_filename=None, ssh_key_name=__name__, use_kms_auth=True,
                          use_ssm=True, use_ec2_instance_connect=True):
    if bless_config_filename:
        with open(bless_config_filename) as fh:
            bless_config = yaml.safe_load(fh)
        ensure_bless_ssh_cert(ssh_key_name=ssh_key_name,
                              bless_config=bless_config,
                              use_kms_auth=use_kms_auth)
        add_ssh_key_to_agent(ssh_key_name)
        instance = get_instance(hostname)
        bastion_config = match_instance_to_bastion(instance=instance, bastions=bless_config["ssh_config"]["bastions"])
        if not username:
            username = bastion_config["user"]
        if use_ssm:
            return [], username + "@" + instance.id
        elif bastion_config:
            jump_host = bastion_config["user"] + "@" + bastion_config["pattern"]
            return ["-o", "ProxyJump=" + jump_host], username + "@" + instance.private_ip_address
        elif instance.public_dns_name:
            logger.warn("No bastion host found for %s, trying direct connection", instance.private_ip_address)
            return [], username + "@" + instance.public_dns_name
        else:
            raise AegeaException("No bastion host or public route found for {}".format(instance))
    else:
        if get_instance(hostname).key_name is not None:
            add_ssh_key_to_agent(get_instance(hostname).key_name)
        if not username:
            username = get_user_info()["linux_username"]
        save_instance_public_key(hostname, use_ssm=use_ssm)
        if use_ec2_instance_connect:
            ssh_public_key = get_ssh_id()
            logger.info("Sending SSH public key %s for %s to %s", ssh_public_key.split()[-1], username, hostname)
            clients.ec2_instance_connect.send_ssh_public_key(
                InstanceId=get_instance(hostname).id,
                InstanceOSUser=username,
                SSHPublicKey=ssh_public_key,
                AvailabilityZone=get_instance(hostname).subnet.availability_zone
            )
        return [], username + "@" + (get_instance(hostname).id if use_ssm else resolve_instance_public_dns(hostname))

def init_ssm(instance_id):
    ssm_plugin_path = ensure_session_manager_plugin()
    os.environ["PATH"] = os.environ["PATH"] + ":" + os.path.dirname(ssm_plugin_path)
    return ["-o", "ProxyCommand=aws ssm start-session --document-name AWS-StartSSHSession --target " + instance_id]

def ssh(args):
    ssh_opts = ["-o", "ServerAliveInterval={}".format(args.server_alive_interval)]
    ssh_opts += ["-o", "ServerAliveCountMax={}".format(args.server_alive_count_max)]
    ssh_opts += extract_passthrough_opts(args, "ssh")
    prefix, at, name = args.name.rpartition("@")

    if args.use_ssm:
        ssh_opts += init_ssm(get_instance(name).id)

    host_opts, hostname = prepare_ssh_host_opts(username=prefix, hostname=name,
                                                bless_config_filename=args.bless_config,
                                                use_kms_auth=args.use_kms_auth,
                                                use_ssm=args.use_ssm,
                                                use_ec2_instance_connect=args.use_ec2_instance_connect)
    os.execvp("ssh", ["ssh"] + ssh_opts + host_opts + [hostname] + args.ssh_args)

ssh_parser = register_parser(ssh, help="Connect to an EC2 instance", description=__doc__)
ssh_parser.add_argument("name")
ssh_parser.add_argument("ssh_args", nargs=argparse.REMAINDER,
                        help="Arguments to pass to ssh; please see " + BOLD("man ssh") + " for details")
ssh_parser.add_argument("--server-alive-interval", help=argparse.SUPPRESS)
ssh_parser.add_argument("--server-alive-count-max", help=argparse.SUPPRESS)
ssh_parser.add_argument("--no-ssm", action="store_false", dest="use_ssm")
ssh_parser.add_argument("--no-ec2-instance-connect", action="store_false", dest="use_ec2_instance_connect")
add_bless_and_passthrough_opts(ssh_parser, "ssh")

def scp(args):
    """
    Transfer files to or from EC2 instance.
    """
    scp_opts, host_opts = extract_passthrough_opts(args, "scp"), []  # type: ignore
    user_or_hostname_chars = string.ascii_letters + string.digits
    ssm_init_complete = False
    for i, arg in enumerate(args.scp_args):
        if arg[0] in user_or_hostname_chars and ":" in arg:
            hostname, colon, path = arg.partition(":")
            username, at, hostname = hostname.rpartition("@")
            if args.use_ssm and not ssm_init_complete:
                scp_opts += init_ssm(get_instance(hostname).id)
                ssm_init_complete = True
            host_opts, hostname = prepare_ssh_host_opts(username=username, hostname=hostname,
                                                        bless_config_filename=args.bless_config,
                                                        use_kms_auth=args.use_kms_auth, use_ssm=args.use_ssm)
            args.scp_args[i] = hostname + colon + path
    os.execvp("scp", ["scp"] + scp_opts + host_opts + args.scp_args)

scp_parser = register_parser(scp, help="Transfer files to or from EC2 instance", description=scp.__doc__)
scp_parser.add_argument("scp_args", nargs=argparse.REMAINDER,
                        help="Arguments to pass to scp; please see " + BOLD("man scp") + " for details")
scp_parser.add_argument("--no-ssm", action="store_false", dest="use_ssm")
add_bless_and_passthrough_opts(scp_parser, "scp")

def run(args):
    run_command(args.command, instance_ids=[get_instance(args.instance).id])

run_parser = register_parser(run, help="Run a command on an EC2 instance", description=run_command.__doc__)
run_parser.add_argument("instance")
run_parser.add_argument("command")

def ssh_to_ecs_container(instance_id, container_id, ssh_args, use_ssm):
    ssh_args = ["-t", instance_id, "sudo", "docker", "exec", "--interactive", "--tty", container_id] + ssh_args
    if "BLESS_CONFIG" not in os.environ:  # bless will provide the username, otherwise use Amazon Linux default
        ssh_args = ["-l", "ec2-user"] + ssh_args
    parsed_args = ssh_parser.parse_args(ssh_args)
    parsed_args.use_ssm = use_ssm
    ssh(parsed_args)
