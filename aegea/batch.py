"""
Manage AWS Batch jobs, queues, and compute environments.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, argparse, base64, collections, io, subprocess, json, time
from datetime import datetime

from botocore.exceptions import ClientError
import yaml

from . import logger
from .ls import register_parser, register_listing_parser
from .ecr import ecr_image_name_completer
from .util import Timestamp, paginate
from .util.crypto import ensure_ssh_key
from .util.exceptions import AegeaException
from .util.printing import page_output, tabulate, YELLOW, RED, GREEN, BOLD, ENDC
from .util.aws import (ARN, resources, clients, expect_error_codes, ensure_iam_role, ensure_instance_profile,
                       make_waiter, ensure_vpc, ensure_security_group, ensure_s3_bucket, ensure_log_group,
                       IAMPolicyBuilder)
from .util.aws.spot import SpotFleetBuilder

bash_cmd_preamble = ["/bin/bash", "-c", 'for i in "$@"; do eval "$i"; done', __name__]
ebs_vol_mgr_shellcode = """iid=$(http http://169.254.169.254/latest/dynamic/instance-identity/document)
aws configure set default.region $(echo "$iid" | jq -r .region)
az=$(echo "$iid" | jq -r .availabilityZone)
vid=$(aws ec2 create-volume --availability-zone $az --size %s --volume-type st1 | jq -r .VolumeId)
aws ec2 create-tags --resource $vid --tags Key=aegea_batch_job,Value=$AWS_BATCH_JOB_ID
trap "umount /mnt || umount -l /mnt; aws ec2 detach-volume --volume-id $vid; while ! aws ec2 describe-volumes --volume-ids $vid | jq -re .Volumes[0].Attachments==[]; do sleep 1; done; aws ec2 delete-volume --volume-id $vid" EXIT
while [[ $(aws ec2 describe-volumes --volume-ids $vid | jq -r .Volumes[0].State) != available ]]; do sleep 1; done
for try in {1..9}; do if [[ $try == 9 ]]; then echo "Unable to mount $vid on $devnode"; exit 1; fi; for devnode in /dev/xvd{a..z}; do [[ -e $devnode ]] || break; done; aws ec2 attach-volume --instance-id $(echo "$iid" | jq -r .instanceId) --volume-id $vid --device $devnode || continue; break; done
while [[ $(aws ec2 describe-volumes --volume-ids $vid | jq -r .Volumes[0].State) != in-use ]]; do sleep 1; done
while [[ ! -e $devnode ]]; do sleep 1; done
mkfs.ext4 $devnode
mount $devnode %s""" # noqa

efs_vol_shellcode = """ mkdir -p %s
NFS_END_POINT=$(getent hosts fs-79369430.efs.us-east-1.amazonaws.com | cut -f 1 -d " ")
mount -t nfs -o nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2 $NFS_END_POINT:/ %s"""
def batch(args):
    batch_parser.print_help()

batch_parser = register_parser(batch, help="Manage AWS Batch resources", description=__doc__,
                               formatter_class=argparse.RawTextHelpFormatter)

def queues(args):
    table = clients.batch.describe_job_queues()["jobQueues"]
    page_output(tabulate(table, args))

parser = register_listing_parser(queues, parent=batch_parser, help="List Batch queues")

def create_queue(args):
    ces = [dict(computeEnvironment=e, order=i) for i, e in enumerate(args.compute_environments)]
    logger.info("Creating queue %s in %s", args.name, ces)
    queue = clients.batch.create_job_queue(jobQueueName=args.name, priority=args.priority, computeEnvironmentOrder=ces)
    make_waiter(clients.batch.describe_job_queues, "jobQueues[].status", "VALID", "pathAny").wait(jobQueues=[args.name])
    return queue

parser = register_parser(create_queue, parent=batch_parser, help="Create a Batch queue")
parser.add_argument("name")
parser.add_argument("--priority", type=int, default=5)
parser.add_argument("--compute-environments", nargs="+", required=True)

def delete_queue(args):
    clients.batch.update_job_queue(jobQueue=args.name, state="DISABLED")
    make_waiter(clients.batch.describe_job_queues, "jobQueues[].status", "VALID", "pathAny").wait(jobQueues=[args.name])
    clients.batch.delete_job_queue(jobQueue=args.name)

parser = register_parser(delete_queue, parent=batch_parser, help="Delete a Batch queue")
parser.add_argument("name")
def compute_environments(args):
    table = clients.batch.describe_compute_environments()["computeEnvironments"]
    page_output(tabulate(table, args))

parser = register_listing_parser(compute_environments, parent=batch_parser, help="List Batch compute environments")
def create_compute_environment(args):
    batch_iam_role = ensure_iam_role(args.service_role, trust=["batch"], policies=["service-role/AWSBatchServiceRole"])
    vpc = ensure_vpc()
    ssh_key_name = ensure_ssh_key(args.ssh_key_name, base_name=__name__)
    instance_profile = ensure_instance_profile(args.instance_role,
                                               policies={"service-role/AmazonAPIGatewayPushToCloudWatchLogs",
                                                         "service-role/AmazonEC2ContainerServiceforEC2Role",
                                                         IAMPolicyBuilder(action="sts:AssumeRole", resource="*")})
    compute_resources = dict(type=args.compute_type,
                             minvCpus=args.min_vcpus, desiredvCpus=args.desired_vcpus, maxvCpus=args.max_vcpus,
                             instanceTypes=args.instance_types,
                             subnets=[subnet.id for subnet in vpc.subnets.all()],
                             securityGroupIds=[ensure_security_group("aegea.launch", vpc).id],
                             instanceRole=instance_profile.name,
                             bidPercentage=100,
                             spotIamFleetRole=SpotFleetBuilder.get_iam_fleet_role().name,
                             ec2KeyPair=ssh_key_name)
    logger.info("Creating compute environment %s in %s", args.name, vpc)
    compute_environment = clients.batch.create_compute_environment(computeEnvironmentName=args.name,
                                                                   type=args.type,
                                                                   computeResources=compute_resources,
                                                                   serviceRole=batch_iam_role.arn)
    wtr = make_waiter(clients.batch.describe_compute_environments, "computeEnvironments[].status", "VALID", "pathAny",
                      delay=2, max_attempts=300)
    wtr.wait(computeEnvironments=[args.name])
    return compute_environment

cce_parser = register_parser(create_compute_environment, parent=batch_parser, help="Create a Batch compute environment")
cce_parser.add_argument("name")
cce_parser.add_argument("--type", choices={"MANAGED", "UNMANAGED"})
cce_parser.add_argument("--compute-type", choices={"EC2", "SPOT"})
cce_parser.add_argument("--min-vcpus", type=int)
cce_parser.add_argument("--desired-vcpus", type=int)
cce_parser.add_argument("--max-vcpus", type=int)
cce_parser.add_argument("--instance-types", nargs="+")
cce_parser.add_argument("--ssh-key-name")
cce_parser.add_argument("--instance-role", default=__name__ + ".ecs_container_instance")
cce_parser.add_argument("--service-role", default=__name__ + ".service")

def delete_compute_environment(args):
    clients.batch.update_compute_environment(computeEnvironment=args.name, state="DISABLED")
    wtr = make_waiter(clients.batch.describe_compute_environments, "computeEnvironments[].status", "VALID", "pathAny")
    wtr.wait(computeEnvironments=[args.name])
    clients.batch.delete_compute_environment(computeEnvironment=args.name)

parser = register_parser(delete_compute_environment, parent=batch_parser, help="Delete a Batch compute environment")
parser.add_argument("name")

def get_ecr_image_uri(tag):
    return "{}.dkr.ecr.{}.amazonaws.com/{}".format(ARN.get_account_id(), ARN.get_region(), tag)

def ensure_ecr_image(tag):
    pass

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
    shellcode = ["set -a",
                 "if [ -f /etc/environment ]; then source /etc/environment; fi",
                 "if [ -f /etc/default/locale ]; then source /etc/default/locale; fi",
                 "set +a",
                 "if [ -f /etc/profile ]; then source /etc/profile; fi",
                 "set -euo pipefail"]
    if args.restart:
        restart_policy = dict(tries=args.restart, prior_failures=[])
        args.environment.append(dict(name="AEGEA_RESTART_POLICY", value=json.dumps(restart_policy)))
    if args.storage:
        args.privileged = True
        args.volumes.append(["/dev", "/dev"])
        for mountpoint, size_gb in args.storage:
            shellcode += (ebs_vol_mgr_shellcode % (size_gb, mountpoint)).splitlines()
    elif args.efs_storage:
        args.privileged = True
        commands = (efs_vol_shellcode % (args.efs_storage, args.efs_storage)).splitlines()
        shellcode += commands

    if args.execute:
        payload = base64.b64encode(args.execute.read()).decode()
        args.environment.append(dict(name="BATCH_SCRIPT_B64", value=payload))
        shellcode += ['BATCH_SCRIPT=$(mktemp --tmpdir "$AWS_BATCH_CE_NAME.$AWS_BATCH_JQ_NAME.$AWS_BATCH_JOB_ID.XXXXX")',
                      "echo $BATCH_SCRIPT_B64 | base64 -d > $BATCH_SCRIPT",
                      "chmod +x $BATCH_SCRIPT",
                      "$BATCH_SCRIPT"]
    elif args.cwl:
        ensure_dynamodb_table("aegea-batch-jobs", hash_key_name="job_id")
        bucket = ensure_s3_bucket("aegea-batch-jobs-{}".format(ARN.get_account_id()))
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

def ensure_job_definition(args):
    if args.ecs_image:
        args.image = get_ecr_image_uri(args.ecs_image)
    container_props = {k: getattr(args, k) for k in ("image", "vcpus", "memory", "privileged")}
    if args.volumes:
        container_props.update(volumes=[], mountPoints=[])
        for i, (host_path, guest_path) in enumerate(args.volumes):
            container_props["volumes"].append({"host": {"sourcePath": host_path}, "name": "vol%d" % i})
            container_props["mountPoints"].append({"sourceVolume": "vol%d" % i, "containerPath": guest_path})
    iam_role = ensure_iam_role(args.job_role, trust=["ecs-tasks"],
                               policies=["AmazonEC2FullAccess", "AmazonDynamoDBFullAccess", "AmazonS3FullAccess"])
    container_props.update(jobRoleArn=iam_role.arn)
    return clients.batch.register_job_definition(jobDefinitionName=__name__.replace(".", "_"),
                                                 type="container",
                                                 containerProperties=container_props)

def ensure_queue(name):
    cq_args = argparse.Namespace(name=name, priority=5, compute_environments=[name])
    try:
        return create_queue(cq_args)
    except ClientError:
        create_compute_environment(cce_parser.parse_args(args=[name]))
        return create_queue(cq_args)

def submit(args):
    ensure_log_group("docker")
    ensure_log_group("syslog")
    command, environment = get_command_and_env(args)
    if args.job_definition_arn is None:
        args.job_definition_arn = ensure_job_definition(args)["jobDefinitionArn"]
    submit_args = dict(jobName=args.name,
                       jobQueue=args.queue,
                       dependsOn=[dict(jobId=dep) for dep in args.depends_on],
                       jobDefinition=args.job_definition_arn,
                       parameters={k: v for k, v in args.parameters},
                       containerOverrides=dict(command=command, environment=environment))
    if args.dry_run:
        return {"Dry run succeeded": True}
    try:
        job = clients.batch.submit_job(**submit_args)
    except ClientError:
        # FIXME: only catch "no queue" error
        ensure_queue(args.queue)
        job = clients.batch.submit_job(**submit_args)
    if args.watch:
        watch(watch_parser.parse_args([job["jobId"]]))
        if args.cwl:
            job.update(resources.dynamodb.Table("aegea-batch-jobs").get_item(Key={"job_id": job["jobId"]})["Item"])
    elif args.wait:
        raise NotImplementedError()
    return job

submit_parser = register_parser(submit, parent=batch_parser, help="Submit a job to a Batch queue")
submit_parser.add_argument("--name", default=__name__.replace(".", "_"))
submit_parser.add_argument("--queue", default=__name__.replace(".", "_"))
submit_parser.add_argument("--depends-on", nargs="+", metavar="JOB_ID", default=[])
submit_parser.add_argument("--job-definition-arn")
group = submit_parser.add_mutually_exclusive_group()
group.add_argument("--watch", action="store_true", help="Monitor submitted job, stream log until job completes")
group.add_argument("--wait", action="store_true", help="Block on job. Exit with code 0 if job succeeded, 1 if failed")
group = submit_parser.add_mutually_exclusive_group(required=True)
group.add_argument("--command", nargs="+", help="Run these commands as the job (using " + BOLD("bash -c") + ")")
group.add_argument("--execute", type=argparse.FileType("rb"), metavar="EXECUTABLE",
                   help="Read this executable file and run it as the job")
group.add_argument("--cwl", metavar="CWL_DEFINITION",
                   help="Read this Common Workflow Language definition file and run it as the job")
submit_parser.add_argument("--cwl-input", type=argparse.FileType("rb"), metavar="CWLINPUT", default=sys.stdin,
                           help="With --cwl, use this file as the CWL job input (default: stdin)")
group = submit_parser.add_argument_group(title="job definition parameters", description="""
See http://docs.aws.amazon.com/batch/latest/userguide/job_definitions.html""")
img_group = group.add_mutually_exclusive_group()
img_group.add_argument("--image", default="ubuntu", help="Docker image URL to use for running Batch job")
ecs_img_arg = img_group.add_argument("--ecs-image", "--ecr-image", "-i", metavar="REPO[:TAG]",
                                     help="Name of Docker image residing in this account's Elastic Container Registry")
ecs_img_arg.completer = ecr_image_name_completer
group.add_argument("--vcpus", type=int, default=1)
group.add_argument("--memory-mb", dest="memory", type=int, default=1024)
group.add_argument("--privileged", action="store_true", default=False)
group.add_argument("--volumes", nargs="+", metavar="HOST_PATH=GUEST_PATH", type=lambda x: x.split("=", 1), default=[])
group.add_argument("--environment", nargs="+", metavar="NAME=VALUE",
                   type=lambda x: dict(zip(["name", "value"], x.split("=", 1))), default=[])
group.add_argument("--parameters", nargs="+", metavar="NAME=VALUE", type=lambda x: x.split("=", 1), default=[])
group.add_argument("--job-role", metavar="IAM_ROLE", default=__name__ + ".worker",
                   help="Name of IAM role to grant to the job")
group.add_argument("--storage", nargs="+", metavar="MOUNTPOINT=SIZE_GB",
                   type=lambda x: x.rstrip("GBgb").split("=", 1), default=[])
group.add_argument("--efs-storage", action="store", dest="efs_storage", default=False,
                   help="mount nfs drive to the mount point specified. i.e. --efs-storage /mnt")
submit_parser.add_argument("--restart", help="Number of times to restart the job upon failure", type=int, default=0)
submit_parser.add_argument("--dry-run", action="store_true", help="Gather arguments and stop short of submitting job")

def terminate(args):
    return clients.batch.terminate_job(jobId=args.job_id, reason="Terminated by {}".format(__name__))

parser = register_parser(terminate, parent=batch_parser, help="Terminate a Batch job")
parser.add_argument("job_id")

def ls(args, page_size=100):
    table, job_ids = [], []
    for q in args.queues or [q["jobQueueName"] for q in clients.batch.describe_job_queues()["jobQueues"]]:
        for s in args.status:
            job_ids.extend(j["jobId"] for j in clients.batch.list_jobs(jobQueue=q, jobStatus=s)["jobSummaryList"])
    for i in range(0, len(job_ids), page_size):
        table.extend(clients.batch.describe_jobs(jobs=job_ids[i:i+page_size])["jobs"])
    page_output(tabulate(table, args, cell_transforms={"createdAt": lambda cell, row: Timestamp(cell)}))

parser = register_listing_parser(ls, parent=batch_parser, help="List Batch jobs")
parser.add_argument("--queues", nargs="+")
parser.add_argument("--status", nargs="+",
                    default="SUBMITTED PENDING RUNNABLE STARTING RUNNING SUCCEEDED FAILED".split())

def describe(args):
    return clients.batch.describe_jobs(jobs=[args.job_id])["jobs"][0]

parser = register_parser(describe, parent=batch_parser, help="Describe a Batch job")
parser.add_argument("job_id")

job_status_colors = dict(SUBMITTED=YELLOW(), PENDING=YELLOW(), RUNNABLE=BOLD()+YELLOW(),
                         STARTING=GREEN(), RUNNING=GREEN(),
                         SUCCEEDED=BOLD()+GREEN(), FAILED=BOLD()+RED())

def format_job_status(status):
    return job_status_colors[status] + status + ENDC()

class LogReader:
    log_group_name, start_time, end_time = "/aws/batch/job", 0, int(time.time() + 3600*720*12)
    seen_events, next_seen_events = collections.deque(), collections.deque()
    def __init__(self, job_name, job_id, head=None, tail=None):
        self.log_stream_name_prefix = "{}/{}".format(job_name, job_id)
        self.describe_log_streams = clients.logs.get_paginator("describe_log_streams")
        self.head, self.tail = head, tail

    def __iter__(self):
        log_stream_args = dict(logGroupName=self.log_group_name, logStreamNamePrefix=self.log_stream_name_prefix)
        for log_stream in paginate(self.describe_log_streams, **log_stream_args):
            get_args = dict(logGroupName=self.log_group_name, logStreamName=log_stream["logStreamName"],
                            startTime=self.start_time, limit=min(self.head or 10000, self.tail or 10000))
            get_args["startFromHead"] = True if self.tail is None else False
            next_page_token = "nextForwardToken" if self.tail is None else "nextBackwardToken"
            while True:
                page = clients.logs.get_log_events(**get_args)
                for event in page["events"]:
                    if "timestamp" in event and "message" in event:
                        if event["timestamp"] != self.start_time:
                            self.next_seen_events.clear()
                            LogReader.start_time = event["timestamp"]
                        self.next_seen_events.append(event)
                        if self.seen_events and event == self.seen_events[0]:
                            self.seen_events.popleft()
                            continue
                        yield event
                if self.head is not None or self.tail is not None or next_page_token not in page:
                    break
                get_args["nextToken"] = page[next_page_token]
        self.seen_events.clear()
        LogReader.next_seen_events, LogReader.seen_events = LogReader.seen_events, LogReader.next_seen_events

def get_logs(args):
    for event in LogReader(args.job_name, args.job_id, head=args.head, tail=args.tail):
        print(str(Timestamp(event["timestamp"])), event["message"])

def watch(args):
    args.job_name = clients.batch.describe_jobs(jobs=[args.job_id])["jobs"][0]["jobName"]
    logger.info("Watching job %s (%s)", args.job_id, args.job_name)
    last_status = None
    while last_status not in {"SUCCEEDED", "FAILED"}:
        job_desc = clients.batch.describe_jobs(jobs=[args.job_id])["jobs"][0]
        if job_desc["status"] != last_status:
            logger.info("Job %s %s", args.job_id, format_job_status(job_desc["status"]))
            last_status = job_desc["status"]
        if job_desc["status"] in {"RUNNING", "SUCCEEDED", "FAILED"}:
            get_logs(args)
        if "statusReason" in job_desc:
            logger.info("Job %s: %s", args.job_id, job_desc["statusReason"])

get_logs_parser = register_parser(get_logs, parent=batch_parser, help="Retrieve logs for a Batch job")
get_logs_parser.add_argument("job_id")
get_logs_parser.add_argument("job_name", nargs="?", default=__name__.replace(".", "_"))
watch_parser = register_parser(watch, parent=batch_parser, help="Monitor a running Batch job and stream its logs")
watch_parser.add_argument("job_id")
for parser in get_logs_parser, watch_parser:
    lines_group = parser.add_mutually_exclusive_group()
    lines_group.add_argument("--head", type=int, nargs="?", const=10,
                             help="Retrieve this number of lines from the beginning of the log (default 10)")
    lines_group.add_argument("--tail", type=int, nargs="?", const=10,
                             help="Retrieve this number of lines from the end of the log (default 10)")

def ssh(args):
    job_desc = clients.batch.describe_jobs(jobs=[args.job_id])["jobs"][0]
    job_queue_desc = clients.batch.describe_job_queues(jobQueues=[job_desc["jobQueue"]])["jobQueues"][0]
    ce = job_queue_desc["computeEnvironmentOrder"][0]["computeEnvironment"]
    ce_desc = clients.batch.describe_compute_environments(computeEnvironments=[ce])["computeEnvironments"][0]
    ecs_ci_arn = job_desc["container"]["containerInstanceArn"]
    ecs_ci_desc = clients.ecs.describe_container_instances(cluster=ce_desc["ecsClusterArn"],
                                                           containerInstances=[ecs_ci_arn])["containerInstances"][0]
    ecs_ci_ec2_id = ecs_ci_desc["ec2InstanceId"]
    for reservation in paginate(clients.ec2.get_paginator("describe_instances"), InstanceIds=[ecs_ci_ec2_id]):
        ecs_ci_address = reservation["Instances"][0]["PublicDnsName"]
    ssh_args = ["ssh", "-l", "ec2-user", ecs_ci_address,
                "docker", "ps", "--filter", "name=" + args.job_id, "--format", "{{.ID}}"]
    container_id = subprocess.check_output(ssh_args).decode().strip()
    subprocess.call(["ssh", "-t", "-l", "ec2-user", ecs_ci_address,
                     "docker", "exec", "--interactive", "--tty", container_id] + args.ssh_args)

ssh_parser = register_parser(ssh, parent=batch_parser, help="Log in to a running Batch job via SSH")
ssh_parser.add_argument("job_id")
ssh_parser.add_argument("ssh_args", nargs=argparse.REMAINDER, default=["/bin/bash", "-l"])
