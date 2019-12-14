import io, json, base64, hashlib, argparse

import yaml
from botocore.exceptions import ClientError
from botocore.paginate import Paginator

from . import ARN, resources, clients, expect_error_codes, ensure_s3_bucket, ensure_iam_role, instance_storage_shellcode
from .. import paginate, get_mkfs_command
from ..exceptions import AegeaException
from ... import __version__

bash_cmd_preamble = ["/bin/bash", "-c", 'for i in "$@"; do eval "$i"; done; cd /', __name__]

env_mgr_shellcode = """
set -a
if [ -f /etc/environment ]; then source /etc/environment; fi
if [ -f /etc/default/locale ]; then source /etc/default/locale; else export LC_ALL=C.UTF-8 LANG=C.UTF-8; fi
export AWS_DEFAULT_REGION={region}
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
        bucket = ensure_s3_bucket(args.staging_s3_bucket or "aegea-batch-jobs-" + ARN.get_account_id())

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
    elif args.cwl:
        ensure_dynamodb_table("aegea-batch-jobs", hash_key_name="job_id")
        bucket = ensure_s3_bucket(args.staging_s3_bucket or "aegea-batch-jobs-" + ARN.get_account_id())
        args.environment.append(dict(name="AEGEA_BATCH_S3_BASE_URL", value="s3://" + bucket.name))

        from cwltool.main import main as cwltool_main
        with io.BytesIO() as preprocessed_cwl:
            if cwltool_main(["--print-pre", args.cwl], stdout=preprocessed_cwl) != 0:
                raise AegeaException("Error while running cwltool")
            cwl_spec = yaml.load(preprocessed_cwl.getvalue())
            payload = base64.b64encode(preprocessed_cwl.getvalue()).decode()
            args.environment.append(dict(name="AEGEA_BATCH_CWL_DEF_B64", value=payload))
            payload = base64.b64encode(args.cwl_input.read()).decode()
            args.environment.append(dict(name="AEGEA_BATCH_CWL_JOB_B64", value=payload))

        for requirement in cwl_spec.get("requirements", []):
            if requirement["class"] == "DockerRequirement":
                # FIXME: dockerFile support: ensure_ecr_image(...)
                # container_props["image"] = requirement["dockerPull"]
                pass

        shellcode += [
            # 'sed -i -e "s|http://archive.ubuntu.com|http://us-east-1.ec2.archive.ubuntu.com|g" /etc/apt/sources.list',
            # "apt-get update -qq",
            # "apt-get install -qqy --no-install-suggests --no-install-recommends --force-yes python-pip python-requests python-yaml python-lockfile python-pyparsing awscli", # noqa
            # "pip install ruamel.yaml==0.13.4 cwltool==1.0.20161227200419 dynamoq tractorbeam",
            "cwltool --no-container --preserve-entire-environment <(echo $AEGEA_BATCH_CWL_DEF_B64 | base64 -d) <(echo $AEGEA_BATCH_CWL_JOB_B64 | base64 -d | tractor pull) | tractor push $AEGEA_BATCH_S3_BASE_URL/$AWS_BATCH_JOB_ID | dynamoq update aegea-batch-jobs $AWS_BATCH_JOB_ID" # noqa
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

def set_volumes(args, container_props):
    if args.volumes:
        for i, (host_path, guest_path) in enumerate(args.volumes):
            container_props["volumes"].append({"host": {"sourcePath": host_path}, "name": "vol%d" % i})
            container_props["mountPoints"].append({"sourceVolume": "vol%d" % i, "containerPath": guest_path})

def ensure_job_definition(args):
    if args.ecs_image:
        args.image = get_ecr_image_uri(args.ecs_image)
    container_props = {k: getattr(args, k) for k in ("image", "vcpus", "memory", "privileged")}
    container_props.update(volumes=[], mountPoints=[], environment=[], command=[], resourceRequirements=[])
    set_volumes(args, container_props)
    set_ulimits(args, container_props)
    if args.gpus:
        container_props["resourceRequirements"] = [{"type": "GPU", "value": str(args.gpus)}]
    iam_role = ensure_iam_role(args.job_role, trust=["ecs-tasks"],
                               policies=["AmazonEC2FullAccess", "AmazonDynamoDBFullAccess", "AmazonS3FullAccess"])
    container_props.update(jobRoleArn=iam_role.arn)
    expect_job_defn = dict(status="ACTIVE", type="container", parameters={},
                           retryStrategy={'attempts': args.retry_attempts}, containerProperties=container_props)
    job_hash = hashlib.sha256(json.dumps(container_props, sort_keys=True).encode()).hexdigest()[:8]
    job_defn_name = __name__.replace(".", "_") + "_jd_" + job_hash
    describe_job_definitions_paginator = Paginator(method=clients.batch.describe_job_definitions,
                                                   pagination_config=dict(result_key="jobDefinitions",
                                                                          input_token="nextToken",
                                                                          output_token="nextToken",
                                                                          limit_key="maxResults"),
                                                   model=None)
    for job_defn in paginate(describe_job_definitions_paginator, jobDefinitionName=job_defn_name):
        job_defn_desc = {k: job_defn.pop(k) for k in ("jobDefinitionName", "jobDefinitionArn", "revision")}
        if job_defn == expect_job_defn:
            return job_defn_desc
    return clients.batch.register_job_definition(jobDefinitionName=job_defn_name,
                                                 type="container",
                                                 containerProperties=container_props,
                                                 retryStrategy=dict(attempts=args.retry_attempts))
