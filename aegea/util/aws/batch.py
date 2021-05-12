import os, sys, io, json, base64, hashlib, argparse
from typing import Dict, Any

import yaml
from botocore.exceptions import ClientError
from botocore.paginate import Paginator

from ... import __version__
from ..exceptions import AegeaException
from .. import paginate, get_mkfs_command, logger
from . import ARN, resources, clients, expect_error_codes, ensure_s3_bucket, instance_storage_shellcode
from .iam import ensure_iam_role

bash_cmd_preamble = ["/bin/bash", "-c", 'for i in "$@"; do eval "$i"; done; cd /', __name__]

env_mgr_shellcode = """
set -a
if [ -f /etc/environment ]; then source /etc/environment; fi
if [ -f /etc/default/locale ]; then source /etc/default/locale; else export LC_ALL=C.UTF-8 LANG=C.UTF-8; fi
export AWS_DEFAULT_REGION={region} DEBIAN_FRONTEND=noninteractive
set +a
if [ -f /etc/profile ]; then source /etc/profile; fi
set -euo pipefail
"""

apt_mgr_shellcode = """
sed -i -e "s|/archive.ubuntu.com|/{region}.ec2.archive.ubuntu.com|g" /etc/apt/sources.list
apt-get update -qq"""

ebs_vol_mgr_shellcode = apt_mgr_shellcode + """
apt-get install -qqy --no-install-suggests --no-install-recommends httpie awscli jq lsof python3-virtualenv > /dev/null
python3 -m virtualenv -q --python=python3 /opt/aegea-venv
/opt/aegea-venv/bin/pip install -q argcomplete requests boto3 tweak pyyaml
/opt/aegea-venv/bin/pip install -q --no-deps aegea=={aegea_version}
aegea_ebs_cleanup() {{ echo Detaching EBS volume $aegea_ebs_vol_id; cd /; /opt/aegea-venv/bin/aegea ebs detach --unmount --force --delete $aegea_ebs_vol_id; }}
trap aegea_ebs_cleanup EXIT
aegea_ebs_vol_id=$(/opt/aegea-venv/bin/aegea ebs create --size-gb {size_gb} --volume-type {volume_type} --tags managedBy=aegea batchJobId=$AWS_BATCH_JOB_ID --attach --format ext4 --mount {mountpoint} | jq -r .VolumeId)
"""  # noqa

efs_vol_shellcode = """mkdir -p {efs_mountpoint}
MAC=$(curl http://169.254.169.254/latest/meta-data/mac)
export SUBNET_ID=$(curl http://169.254.169.254/latest/meta-data/network/interfaces/macs/$MAC/subnet-id)
NFS_ENDPOINT=$(echo "$AEGEA_EFS_DESC" | jq -r ".[] | select(.SubnetId == env.SUBNET_ID) | .IpAddress")
mount -t nfs -o nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2 $NFS_ENDPOINT:/ {efs_mountpoint}"""

instance_storage_mgr_shellcode = apt_mgr_shellcode + """
apt-get install -qqy --no-install-suggests --no-install-recommends mdadm""" + instance_storage_shellcode

def ensure_dynamodb_table(name, hash_key_name, read_capacity_units=5, write_capacity_units=5):
    try:
        table = resources.dynamodb.create_table(TableName=name,
                                                KeySchema=[dict(AttributeName=hash_key_name, KeyType="HASH")],
                                                AttributeDefinitions=[dict(AttributeName=hash_key_name,
                                                                           AttributeType="S")],
                                                ProvisionedThroughput=dict(ReadCapacityUnits=read_capacity_units,
                                                                           WriteCapacityUnits=write_capacity_units))
    except ClientError as e:
        expect_error_codes(e, "ResourceInUseException")
        table = resources.dynamodb.Table(name)
    table.wait_until_exists()
    return table


def get_command_and_env(args):
    # shellcode = ['for var in ${{!AWS_BATCH_@}}; do echo "{}.env.$var=${{!var}}"; done'.format(__name__)]
    shellcode = env_mgr_shellcode.strip().format(region=ARN.get_region()).splitlines()
    if args.mount_instance_storage or args.storage:
        args.privileged = True
        args.volumes.append(["/dev", "/dev"])
    if args.mount_instance_storage:
        shellcode += instance_storage_mgr_shellcode.strip().format(region=ARN.get_region(),
                                                                   mountpoint=args.mount_instance_storage,
                                                                   mkfs=get_mkfs_command(fs_type="ext4")).splitlines()
    if args.storage:
        for mountpoint, size_gb in args.storage:
            volume_type = "st1"
            if args.volume_type:
                volume_type = args.volume_type
            shellcode += ebs_vol_mgr_shellcode.strip().format(region=ARN.get_region(),
                                                              aegea_version=__version__,
                                                              size_gb=size_gb,
                                                              volume_type=volume_type,
                                                              mountpoint=mountpoint).splitlines()
    elif args.efs_storage:
        args.privileged = True
        if "=" in args.efs_storage:
            mountpoint, efs_id = args.efs_storage.split("=")
        else:
            mountpoint, efs_id = args.efs_storage, __name__
        if not efs_id.startswith("fs-"):
            for filesystem in clients.efs.describe_file_systems()["FileSystems"]:
                if filesystem["Name"] == efs_id:
                    efs_id = filesystem["FileSystemId"]
                    break
            else:
                raise AegeaException('Could not resolve "{}" to a valid EFS filesystem ID'.format(efs_id))
        mount_targets = clients.efs.describe_mount_targets(FileSystemId=efs_id)["MountTargets"]
        args.environment.append(dict(name="AEGEA_EFS_DESC", value=json.dumps(mount_targets)))
        commands = efs_vol_shellcode.format(efs_mountpoint=args.efs_storage, efs_id=efs_id).splitlines()
        shellcode += commands

    if args.execute:
        bucket = ensure_s3_bucket(args.staging_s3_bucket)

        key_name = hashlib.sha256(args.execute.read()).hexdigest()
        args.execute.seek(0)
        bucket.upload_fileobj(args.execute, key_name)
        payload_url = clients.s3.generate_presigned_url(
            ClientMethod='get_object',
            Params=dict(Bucket=bucket.name, Key=key_name),
            ExpiresIn=3600 * 24 * 7
        )
        tmpdir_fmt = "${AWS_BATCH_CE_NAME:-$AWS_EXECUTION_ENV}.${AWS_BATCH_JQ_NAME:-}.${AWS_BATCH_JOB_ID:-}.XXXXX"
        shellcode += ['BATCH_SCRIPT=$(mktemp --tmpdir "{tmpdir_fmt}")'.format(tmpdir_fmt=tmpdir_fmt),
                      "apt-get update -qq",
                      "apt-get install -qqy --no-install-suggests --no-install-recommends curl ca-certificates gnupg",
                      "curl -L '{payload_url}' > $BATCH_SCRIPT".format(payload_url=payload_url),
                      "chmod +x $BATCH_SCRIPT",
                      "$BATCH_SCRIPT"]
    elif args.wdl:
        bucket = ensure_s3_bucket(args.staging_s3_bucket)
        wdl_key_name = "{}.wdl".format(hashlib.sha256(args.wdl.read()).hexdigest())
        args.wdl.seek(0)
        bucket.upload_fileobj(args.wdl, wdl_key_name)
        wdl_input = args.wdl_input.read().encode()
        wdl_input_key_name = "{}.json".format(hashlib.sha256(wdl_input).hexdigest())
        bucket.Object(wdl_input_key_name).put(Body=wdl_input)
        shellcode += [
            "sed -i s/archive.ubuntu.com/{}.ec2.archive.ubuntu.com/ /etc/apt/sources.list".format(ARN.get_region()),
            "apt-get -qq update",
            "apt-get -qq install --no-install-suggests --no-install-recommends --yes python3-{pip,setuptools,wheel}",
            "pip3 install miniwdl awscli",
            "cd /mnt",
            "aws s3 cp s3://{bucket}/{key} .".format(bucket=bucket.name, key=wdl_key_name),
            "aws s3 cp s3://{bucket}/{key} wdl_input.json".format(bucket=bucket.name, key=wdl_input_key_name),
            "miniwdl run --dir /mnt --verbose --error-json {} --input wdl_input.json > wdl_output.json".format(wdl_key_name),  # noqa
            "aws s3 cp wdl_output.json s3://{bucket}/wdl_output/${{AWS_BATCH_JOB_ID}}.json".format(bucket=bucket.name)
        ]
    args.command = bash_cmd_preamble + shellcode + (args.command or [])
    return args.command, args.environment

def get_ecr_image_uri(tag):
    return "{}.dkr.ecr.{}.amazonaws.com/{}".format(ARN.get_account_id(), ARN.get_region(), tag)

def ensure_ecr_image(tag):
    pass

def set_ulimits(args, container_props):
    if args.ulimits:
        container_props.setdefault("ulimits", [])
        for ulimit in args.ulimits:
            name, value = ulimit.split(":", 1)
            container_props["ulimits"].append(dict(name=name, hardLimit=int(value), softLimit=int(value)))

def get_volumes_and_mountpoints(args):
    volumes, mount_points = [], []
    if args.wdl and ["/var/run/docker.sock", "/var/run/docker.sock"] not in args.volumes:
        args.volumes.append(["/var/run/docker.sock", "/var/run/docker.sock"])
    if args.volumes:
        for i, (host_path, guest_path) in enumerate(args.volumes):
            vol_spec = {"name": "vol%d" % i}  # type: Dict[str, Any]
            mount_spec = {"sourceVolume": "vol%d" % i, "containerPath": guest_path}
            if host_path.startswith("fs-"):
                fs_id, _, root_directory = host_path.partition(":")
                vol_spec["efsVolumeConfiguration"] = dict(fileSystemId=fs_id)
                if root_directory:
                    vol_spec["efsVolumeConfiguration"]["rootDirectory"] = root_directory
            else:
                vol_spec["host"] = dict(sourcePath=host_path)
            volumes.append(vol_spec)
            mount_points.append(mount_spec)
    return volumes, mount_points

def ensure_job_definition(args):
    def get_jd_arn_and_job_name(jd_res):
        job_name = args.name or "{}_{}".format(jd_res["jobDefinitionName"], jd_res["revision"])
        return jd_res["jobDefinitionArn"], job_name

    if args.ecs_image:
        args.image = get_ecr_image_uri(args.ecs_image)
    container_props = dict(image=args.image, user=args.user, privileged=args.privileged)
    container_props.update(volumes=[], mountPoints=[], environment=[], command=[], resourceRequirements=[], ulimits=[],
                           secrets=[])
    if args.platform_capabilities == ["FARGATE"]:
        container_props["resourceRequirements"].append(dict(type="VCPU", value="0.25"))
        container_props["resourceRequirements"].append(dict(type="MEMORY", value="512"))
        container_props["executionRoleArn"] = args.execution_role_arn
    else:
        container_props["vcpus"] = args.vcpus
        container_props["memory"] = 4
        set_ulimits(args, container_props)
    container_props["volumes"], container_props["mountPoints"] = get_volumes_and_mountpoints(args)
    if args.gpus:
        container_props["resourceRequirements"].append(dict(type="GPU", value=str(args.gpus)))
    if args.log_driver:
        container_props.setdefault("logConfiguration", {})
        container_props["logConfiguration"]["logDriver"] = args.log_driver
    if args.log_options:
        container_props.setdefault("logConfiguration", {})
        container_props["logConfiguration"]["options"] = {k: v for k, v in args.log_options}
    iam_role = ensure_iam_role(args.job_role, trust=["ecs-tasks"], policies=args.default_job_role_iam_policies)
    container_props.update(jobRoleArn=iam_role.arn)
    expect_job_defn = dict(status="ACTIVE", type="container", parameters={}, tags={},
                           retryStrategy=dict(attempts=args.retry_attempts, evaluateOnExit=[]),
                           containerProperties=container_props, platformCapabilities=args.platform_capabilities)
    job_hash = hashlib.sha256(json.dumps(container_props, sort_keys=True).encode()).hexdigest()[:8]
    job_defn_name = __name__.replace(".", "_") + "_jd_" + job_hash
    if args.platform_capabilities == ["FARGATE"]:
        job_defn_name += "_FARGATE"
        container_props["fargatePlatformConfiguration"] = dict(platformVersion="LATEST")
        container_props["networkConfiguration"] = dict(assignPublicIp="ENABLED")
    describe_job_definitions_paginator = Paginator(method=clients.batch.describe_job_definitions,
                                                   pagination_config=dict(result_key="jobDefinitions",
                                                                          input_token="nextToken",
                                                                          output_token="nextToken",
                                                                          limit_key="maxResults"),
                                                   model=None)
    for job_defn in paginate(describe_job_definitions_paginator, jobDefinitionName=job_defn_name):
        job_defn_desc = {k: job_defn.pop(k) for k in ("jobDefinitionName", "jobDefinitionArn", "revision")}
        if job_defn == expect_job_defn:
            logger.info("Found existing Batch job definition %s", job_defn_desc["jobDefinitionArn"])
            return get_jd_arn_and_job_name(job_defn_desc)
    logger.info("Creating new Batch job definition %s", job_defn_name)
    jd_res = clients.batch.register_job_definition(jobDefinitionName=job_defn_name,
                                                   type="container",
                                                   containerProperties=container_props,
                                                   retryStrategy=dict(attempts=args.retry_attempts),
                                                   platformCapabilities=args.platform_capabilities)
    return get_jd_arn_and_job_name(jd_res)

def ensure_lambda_helper():
    awslambda = getattr(clients, "lambda")
    try:
        helper_desc = awslambda.get_function(FunctionName="aegea-dev-process_batch_event")
        logger.info("Using Batch helper Lambda %s", helper_desc["Configuration"]["FunctionArn"])
    except awslambda.exceptions.ResourceNotFoundException:
        logger.info("Batch helper Lambda not found, installing")
        import chalice.cli  # type: ignore
        orig_argv = sys.argv
        orig_wd = os.getcwd()
        try:
            os.chdir(os.path.join(os.path.dirname(__file__), "batch_events_lambda"))
            sys.argv = ["chalice", "deploy", "--no-autogen-policy"]
            chalice.cli.main()
        except SystemExit:
            pass
        finally:
            os.chdir(orig_wd)
            sys.argv = orig_argv
