"""
Manage AWS Elastic Container Service (ECS) resources, including Fargate tasks.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, argparse, time, json, hashlib
from itertools import product
from functools import partial
from typing import Dict, List

from botocore.exceptions import ClientError

from . import logger
from .batch import add_command_args, add_job_defn_args, print_event
from .ls import register_parser, register_listing_parser
from .ssh import ssh_to_ecs_container
from .util import Timestamp, paginate, ThreadPoolExecutor
from .util.compat import USING_PYTHON2
from .util.exceptions import AegeaException
from .util.printing import page_output, tabulate, YELLOW, RED, GREEN, BOLD, ENDC
from .util.aws import (ARN, clients, ensure_security_group, ensure_vpc, ensure_log_group,
                       ensure_ecs_cluster, expect_error_codes, encode_tags)
from .util.aws.logs import CloudwatchLogReader
from .util.aws.batch import get_command_and_env, set_ulimits, get_volumes_and_mountpoints, get_ecr_image_uri
from .util.aws.iam import ensure_iam_role, ensure_fargate_execution_role

def complete_cluster_name(**kwargs):
    return [ARN(c).resource.partition("/")[2] for c in paginate(clients.ecs.get_paginator("list_clusters"))]

def ecs(args):
    ecs_parser.print_help()

ecs_parser = register_parser(ecs, help="Manage Elastic Container Service resources", description=__doc__)

def clusters(args):
    if not args.clusters:
        args.clusters = list(paginate(clients.ecs.get_paginator("list_clusters")))
    cluster_desc = clients.ecs.describe_clusters(clusters=args.clusters)["clusters"]
    page_output(tabulate(cluster_desc, args))

clusters_parser = register_listing_parser(clusters, parent=ecs_parser, help="List ECS clusters")
clusters_parser.add_argument("clusters", nargs="*").completer = complete_cluster_name

def get_task_descs(cluster_names, task_names=None, desired_status=frozenset(["RUNNING", "STOPPED"])):
    list_tasks = clients.ecs.get_paginator("list_tasks")

    def list_tasks_worker(worker_args):
        _cluster, _status = worker_args
        return _cluster, _status, list(paginate(list_tasks, cluster=_cluster, desiredStatus=_status))

    def describe_tasks_worker(t, cluster=None):
        return clients.ecs.describe_tasks(cluster=cluster, tasks=t)["tasks"] if t else []

    task_descs = []  # type: List[Dict]
    if task_names:
        task_descs = describe_tasks_worker(task_names, cluster=cluster_names[0])
    else:
        with ThreadPoolExecutor() as executor:
            for cluster, status, tasks in executor.map(list_tasks_worker, product(cluster_names, desired_status)):
                worker = partial(describe_tasks_worker, cluster=cluster)
                descs = executor.map(worker, (tasks[pos:pos + 100] for pos in range(0, len(tasks), 100)))
                task_descs += sum(descs, [])
    return task_descs

def tasks(args):
    list_clusters = clients.ecs.get_paginator("list_clusters")
    if args.clusters is None:
        args.clusters = [__name__.replace(".", "_")] if args.tasks else list(paginate(list_clusters))
    task_descs = get_task_descs(cluster_names=args.clusters, task_names=args.tasks, desired_status=args.desired_status)
    page_output(tabulate(task_descs, args))

tasks_parser = register_listing_parser(tasks, parent=ecs_parser, help="List ECS tasks")
tasks_parser.add_argument("tasks", nargs="*")
tasks_parser.add_argument("--clusters", nargs="*").completer = complete_cluster_name
tasks_parser.add_argument("--desired-status", nargs=1, choices={"RUNNING", "STOPPED"}, default=["RUNNING", "STOPPED"])
tasks_parser.add_argument("--launch-type", nargs=1, choices={"EC2", "FARGATE"}, default=["EC2", "FARGATE"])

def run(args):
    args.storage = args.efs_storage = args.mount_instance_storage = None
    command, environment = get_command_and_env(args)
    vpc = ensure_vpc()
    ensure_ecs_cluster(args.cluster)
    log_config = {
        "logDriver": "awslogs",
        "options": {
            "awslogs-region": clients.ecs.meta.region_name,
            "awslogs-group": args.task_name,
            "awslogs-stream-prefix": args.task_name
        }
    }
    ensure_log_group(log_config["options"]["awslogs-group"])  # type: ignore

    if args.ecs_image:
        args.image = get_ecr_image_uri(args.ecs_image)

    volumes, mount_points = get_volumes_and_mountpoints(args)
    if args.memory is None:
        if args.fargate_memory.endswith("GB"):
            args.memory = int(args.fargate_memory[:-len("GB")]) * 1024
        else:
            args.memory = int(args.fargate_memory)

    container_defn = dict(name=args.task_name,
                          image=args.image,
                          cpu=0,
                          memory=args.memory,
                          user=args.user,
                          command=[],
                          environment=[],
                          portMappings=[],
                          essential=True,
                          logConfiguration=log_config,
                          mountPoints=[dict(sourceVolume="scratch", containerPath="/mnt")] + mount_points,
                          volumesFrom=[])
    set_ulimits(args, container_defn)
    exec_role = ensure_fargate_execution_role(args.execution_role)
    task_role = ensure_iam_role(args.task_role, trust=["ecs-tasks"])

    expect_task_defn = dict(containerDefinitions=[container_defn],
                            requiresCompatibilities=["FARGATE"],
                            taskRoleArn=task_role.arn,
                            executionRoleArn=exec_role.arn,
                            networkMode="awsvpc",
                            cpu=args.fargate_cpu,
                            memory=args.fargate_memory,
                            volumes=[dict(name="scratch", host={})] + volumes)

    task_hash = hashlib.sha256(json.dumps(expect_task_defn, sort_keys=True).encode()).hexdigest()[:8]
    task_defn_name = __name__.replace(".", "_") + "_" + task_hash

    try:
        task_defn = clients.ecs.describe_task_definition(taskDefinition=task_defn_name)["taskDefinition"]
        assert task_defn["status"] == "ACTIVE"
        assert "FARGATE" in task_defn["compatibilities"]
        desc_keys = ["family", "revision", "taskDefinitionArn", "status", "compatibilities", "placementConstraints",
                     "requiresAttributes"]
        task_desc = {key: task_defn.pop(key) for key in desc_keys}
        if expect_task_defn["cpu"].endswith(" vCPU"):
            expect_task_defn["cpu"] = str(int(expect_task_defn["cpu"][:-len(" vCPU")]) * 1024)
        if expect_task_defn["memory"].endswith("GB"):
            expect_task_defn["memory"] = str(int(expect_task_defn["memory"][:-len("GB")]) * 1024)
        assert task_defn == expect_task_defn
        logger.debug("Reusing task definition %s", task_desc["taskDefinitionArn"])
    except (ClientError, AssertionError):
        logger.debug("Registering new ECS task definition %s", task_defn_name)
        task_desc = clients.ecs.register_task_definition(family=task_defn_name, **expect_task_defn)["taskDefinition"]

    network_config = {
        "awsvpcConfiguration": {
            "subnets": [
                subnet.id for subnet in vpc.subnets.all()
            ],
            "securityGroups": [ensure_security_group(args.security_group, vpc).id],
            "assignPublicIp": "ENABLED"
        }
    }
    container_overrides = [dict(name=args.task_name, command=command, environment=environment)]
    run_args = dict(cluster=args.cluster,
                    taskDefinition=task_desc["taskDefinitionArn"],
                    launchType="FARGATE",
                    platformVersion=args.fargate_platform_version,
                    networkConfiguration=network_config,
                    overrides=dict(containerOverrides=container_overrides))
    if args.tags:
        run_args["tags"] = encode_tags(args.tags, case="lower")
    if args.dry_run:
        logger.info("The following command would be run:")
        sys.stderr.write(json.dumps(run_args, indent=4) + "\n")
        return {"Dry run succeeded": True}
    res = clients.ecs.run_task(**run_args)
    task_arn = res["tasks"][0]["taskArn"]
    if args.watch:
        watch(watch_parser.parse_args([task_arn, "--cluster", args.cluster, "--task-name", args.task_name]))
    elif args.wait:
        raise NotImplementedError()
    if args.watch or args.wait:
        res = clients.ecs.describe_tasks(cluster=args.cluster, tasks=[task_arn])
        print(json.dumps(res["tasks"][0], indent=2, default=lambda x: str(x)))
        return SystemExit(res["tasks"][0]["containers"][0]["exitCode"])
    else:
        return res["tasks"][0]

register_parser_args = dict(parent=ecs_parser, help="Run a Fargate task")
if not USING_PYTHON2:
    register_parser_args["aliases"] = ["launch"]

run_parser = register_parser(run, **register_parser_args)
add_command_args(run_parser)
add_job_defn_args(run_parser)
run_parser.add_argument("--execution-role", metavar="IAM_ROLE", default=__name__)
run_parser.add_argument("--task-role", metavar="IAM_ROLE", default=__name__)
run_parser.add_argument("--security-group", default=__name__)
run_parser.add_argument("--cluster", default=__name__.replace(".", "_"))
run_parser.add_argument("--task-name", default=__name__.replace(".", "_"))
run_parser.add_argument("--tags", nargs="+", metavar="TAG_NAME=VALUE", help="Tag the Fargate task with these tags")
run_parser.add_argument("--dry-run", action="store_true", help="Gather arguments and stop short of running task")

fargate_group = run_parser.add_argument_group(
    description=("Resource allocation for the Fargate task VM, which runs the task Docker container(s): "
                 "(See also https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html)")
)
fargate_group.add_argument(
    "--fargate-cpu", help="vCPUs to allocate to the Fargate task",
    choices=[".25 vCPU", ".5 vCPU", "1 vCPU", "2 vCPU", "4 vCPU", "256", "512", "1024", "2048", "4096"]
)
fargate_group.add_argument(
    "--fargate-memory", help="Memory to allocate to the Fargate task",
    choices=["0.5GB"] + ["{}GB".format(i) for i in range(1, 31)] + ["512"] + list(map(str, range(1024, 30721, 1024)))
)

task_status_colors = dict(PROVISIONING=YELLOW(), PENDING=BOLD() + YELLOW(), ACTIVATING=BOLD() + YELLOW(),
                          RUNNING=GREEN(),
                          DEACTIVATING=BOLD() + GREEN(), STOPPING=BOLD() + GREEN(), DEPROVISIONING=BOLD() + GREEN(),
                          STOPPED=BOLD() + GREEN())

def format_task_status(status):
    return task_status_colors[status] + status + ENDC()

def watch(args):
    logger.info("Watching task %s (%s)", args.task_id, args.cluster)
    last_status, events_received = None, 0
    log_reader = CloudwatchLogReader(
        log_group_name=args.task_name,
        log_stream_name="/".join([args.task_name, args.task_name, os.path.basename(args.task_id)])
    )
    while last_status != "STOPPED":
        res = clients.ecs.describe_tasks(cluster=args.cluster, tasks=[args.task_id])
        if len(res["tasks"]) == 1:
            task_desc = res["tasks"][0]
            if task_desc["lastStatus"] != last_status:
                logger.info("Task %s %s", args.task_id, format_task_status(task_desc["lastStatus"]))
                last_status = task_desc["lastStatus"]
        try:
            for event in log_reader:
                print_event(event)
                events_received += 1
        except ClientError as e:
            expect_error_codes(e, "ResourceNotFoundException")
        if last_status is None and events_received > 0:
            break  # Logs retrieved successfully but task record is no longer in ECS
        time.sleep(1)

watch_parser = register_parser(watch, parent=ecs_parser, help="Monitor a running ECS Fargate task and stream its logs")
watch_parser.add_argument("task_id")
watch_parser.add_argument("--cluster", default=__name__.replace(".", "_"))
watch_parser.add_argument("--task-name", default=__name__.replace(".", "_"))
lines_group = watch_parser.add_mutually_exclusive_group()
lines_group.add_argument("--head", type=int, nargs="?", const=10,
                         help="Retrieve this number of lines from the beginning of the log (default 10)")
lines_group.add_argument("--tail", type=int, nargs="?", const=10,
                         help="Retrieve this number of lines from the end of the log (default 10)")

def stop(args):
    return clients.ecs.stop_task(cluster=args.cluster,
                                 task=args.task_id,
                                 reason="Stopped by {}".format(__name__))

stop_parser = register_parser(stop, parent=ecs_parser, help="Stop a running ECS Fargate task")
stop_parser.add_argument("task_id")
stop_parser.add_argument("--cluster", default=__name__.replace(".", "_"))

def ssh(args):
    if not args.ssh_args:
        args.ssh_args = ["/bin/bash", "-l"]
    for task_desc in get_task_descs(cluster_names=[args.cluster_name], desired_status=["RUNNING"]):
        if task_desc["taskArn"] == args.task_name:
            break
        task_name = ARN(task_desc["taskDefinitionArn"]).resource.split("/", 1)[-1].split(":", 1)[0]
        if task_name == args.task_name:
            break
    else:
        raise AegeaException('No task found with name "{}" in cluster "{}"'.format(args.task_name, args.cluster_name))

    ecs_ci_arn = task_desc["containerInstanceArn"]
    ecs_ci_desc = clients.ecs.describe_container_instances(cluster=task_desc["clusterArn"],
                                                           containerInstances=[ecs_ci_arn])["containerInstances"][0]
    ecs_ci_ec2_id = ecs_ci_desc["ec2InstanceId"]
    logger.info("Task {} is on EC2 instance {}".format(args.task_name, ecs_ci_ec2_id))
    container_id = task_desc["containers"][0]["runtimeId"]
    logger.info("Task {} is in container {}".format(args.task_name, container_id))
    ssh_to_ecs_container(instance_id=ecs_ci_ec2_id, container_id=container_id, ssh_args=args.ssh_args,
                         use_ssm=args.use_ssm)

ssh_parser = register_parser(ssh, parent=ecs_parser, help="Log in to a running ECS container via SSH")
ssh_parser.add_argument("cluster_name")
ssh_parser.add_argument("task_name")
ssh_parser.add_argument("--no-ssm", action="store_false", dest="use_ssm")
ssh_parser.add_argument("ssh_args", nargs=argparse.REMAINDER)
