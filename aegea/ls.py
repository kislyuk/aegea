# coding: utf-8
from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, copy

from . import register_parser
from .util import paginate, describe_cidr
from .util.printing import page_output, tabulate, GREEN, BLUE
from .util.aws import resolve_instance_id, resources, clients

def column_completer(parser, **kwargs):
    resource = getattr(resources, parser.get_default("resource"))
    subresource = getattr(resource, parser.get_default("subresource"))
    return [attr for attr in dir(subresource("")) if not attr.startswith("_")]

def register_listing_parser(function, **kwargs):
    col_def = dict(default=kwargs.pop("column_defaults")) if "column_defaults" in kwargs else {}
    parser = register_parser(function, **kwargs)
    parser.add_argument("--sort-by", help='Sort by this column/field (add ":reverse" to invert the order)')
    col_arg = parser.add_argument("-c", "--columns", nargs="+", help="Names of columns to print", **col_def)
    col_arg.completer = column_completer
    return parser

def register_filtering_parser(function, **kwargs):
    parser = register_listing_parser(function, **kwargs)
    parser.add_argument("-f", "--filter", nargs="+", default=[], metavar="FILTER_NAME=VALUE",
                        help="Filter(s) to apply to output, e.g. --filter state=available")
    parser.add_argument("-t", "--tag", nargs="+", default=[], metavar="TAG_NAME=VALUE",
                        help="Tag(s) to filter output by")
    return parser

def filter_collection(collection, args):
    filters = []
    # TODO: shlex?
    for f in getattr(args, "filter", []):
        name, value = f.split("=", 1)
        if collection.__class__.__name__ == "ec2.instancesCollectionManager":
            name = name.replace("_", "-")
            if name == "state":
                name = "instance-state-name"
        filters.append(dict(Name=name, Values=[value]))
    for t in getattr(args, "tag", []):
        name, value = t.split("=", 1)
        filters.append(dict(Name="tag:" + name, Values=[value]))
    return collection.filter(Filters=filters)

def filter_and_tabulate(collection, args, **kwargs):
    return tabulate(filter_collection(collection, args), args, **kwargs)

def add_name(instance):
    instance.name = instance.id
    for tag in instance.tags or []:
        if tag["Key"] == "Name":
            instance.name = tag["Value"]
    return instance

def ls(args):
    for col in "tags", "launch_time":
        if col not in args.columns:
            args.columns.append(col)
    instances = [add_name(i) for i in filter_collection(resources.ec2.instances, args)]
    args.columns = ["name"] + args.columns
    cell_transforms = {
        "state": lambda x, r: x["Name"],
        "security_groups": lambda x, r: ", ".join(sg["GroupName"] for sg in x),
        "iam_instance_profile": lambda x, r: x.get("Arn", "").split("/")[-1] if x else None,
        "instance_lifecycle": lambda x, r: "" if x is None else x
    }
    page_output(tabulate(instances, args, cell_transforms=cell_transforms))

parser = register_filtering_parser(ls, help="List EC2 instances")

def console(args):
    instance_id = resolve_instance_id(args.instance)
    err = "[No console output received for {}. Console output may lag by several minutes.]".format(instance_id)
    page_output(resources.ec2.Instance(instance_id).console_output().get("Output", err))

parser = register_parser(console, help="Get console output for an EC2 instance")
parser.add_argument("instance")

def images(args):
    page_output(filter_and_tabulate(resources.ec2.images.filter(Owners=["self"]), args))

parser = register_filtering_parser(images, help="List EC2 AMIs")

peer_desc_cache = {}
def describe_peer(peer):
    if "CidrIp" in peer:
        if peer["CidrIp"] not in peer_desc_cache:
            peer_desc_cache[peer["CidrIp"]] = describe_cidr(peer["CidrIp"])
        return peer["CidrIp"], peer_desc_cache[peer["CidrIp"]]
    else:
        if peer["GroupId"] not in peer_desc_cache:
            peer_desc_cache[peer["GroupId"]] = resources.ec2.SecurityGroup(peer["GroupId"])
        return peer_desc_cache[peer["GroupId"]].group_name, peer_desc_cache[peer["GroupId"]].description

def security_groups(args):
    def format_rule(row, perm, peer, egress=False):
        peer_desc, row.peer_description = describe_peer(peer)
        port_range = str(perm.get("FromPort", 1)) + "-" + str(perm.get("ToPort", 65535))
        row.rule = BLUE("●") + ":" + ("*" if egress else port_range)
        row.rule += GREEN("▶") if egress else GREEN("◀")
        row.rule += peer_desc + ":" + (port_range if egress else "*")
        row.proto = "*" if perm["IpProtocol"] == "-1" else perm["IpProtocol"]
    table = []
    for sg in resources.ec2.security_groups.all():
        for i, perm in enumerate(sg.ip_permissions + sg.ip_permissions_egress):
            for peer in perm["IpRanges"] + perm["UserIdGroupPairs"]:
                table.append(copy.copy(sg))
                format_rule(table[-1], perm, peer, egress=True if i > len(sg.ip_permissions) - 1 else False)
    page_output(tabulate(table, args))

parser = register_filtering_parser(security_groups, help="List EC2 security groups")

def acls(args):
    page_output(filter_and_tabulate(resources.ec2.network_acls, args))

parser = register_filtering_parser(acls, help="List EC2 network ACLs")

def clusters(args):
    cluster_arns = sum([p["clusterArns"] for p in clients.ecs.get_paginator("list_clusters").paginate()], [])
    page_output(tabulate(clients.ecs.describe_clusters(clusters=cluster_arns)["clusters"], args))

parser = register_listing_parser(clusters, help="List ECS clusters")

def tasks(args):
    cluster_arns = sum([p["clusterArns"] for p in clients.ecs.get_paginator("list_clusters").paginate()], [])
    table = []
    for cluster_arn in cluster_arns:
        list_tasks_args = dict(cluster=cluster_arn, desiredStatus=args.desired_status)
        paginator = clients.ecs.get_paginator("list_tasks")
        task_arns = sum([p["taskArns"] for p in paginator.paginate(**list_tasks_args)], [])
        if task_arns:
            for task in clients.ecs.describe_tasks(cluster=cluster_arn, tasks=task_arns)["tasks"]:
                table.append(task)
    page_output(tabulate(table, args))

parser = register_listing_parser(tasks, help="List ECS tasks")
parser.add_argument("--desired-status", choices={"RUNNING", "PENDING", "STOPPED"}, default="RUNNING")

def taskdefs(args):
    table = []
    for taskdef_arn in clients.ecs.list_task_definitions()["taskDefinitionArns"]:
        table.append(clients.ecs.describe_task_definition(taskDefinition=taskdef_arn)["taskDefinition"])
    page_output(tabulate(table, args))

parser = register_listing_parser(taskdefs, help="List ECS task definitions",
                                 column_defaults=["family", "revision", "containerDefinitions"])

def sirs(args):
    page_output(tabulate(clients.ec2.describe_spot_instance_requests()["SpotInstanceRequests"], args))

parser = register_listing_parser(sirs, help="List EC2 spot instance requests")

def sfrs(args):
    page_output(tabulate(paginate(clients.ec2.get_paginator("describe_spot_fleet_requests")), args))

parser = register_listing_parser(sfrs, help="List EC2 spot fleet requests")
parser.add_argument("--trim-col-names", nargs="+", default=["SpotFleetRequestConfig.", "SpotFleetRequest"])

def key_pairs(args):
    page_output(tabulate(resources.ec2.key_pairs.all(), args))

parser = register_listing_parser(key_pairs, help="List EC2 SSH key pairs", column_defaults=["name", "key_fingerprint"])

def subnets(args):
    page_output(filter_and_tabulate(resources.ec2.subnets, args))

parser = register_filtering_parser(subnets, help="List EC2 VPCs and subnets")

def tables(args):
    page_output(tabulate(resources.dynamodb.tables.all(), args))

parser = register_listing_parser(tables, help="List DynamoDB tables")

def subscriptions(args):
    page_output(tabulate(paginate(clients.sns.get_paginator("list_subscriptions")), args))

parser = register_listing_parser(subscriptions, help="List SNS subscriptions",
                                 column_defaults=["SubscriptionArn", "Protocol", "Endpoint"])

def limits(args):
    """
    Describe limits in effect on your AWS account. See also https://console.aws.amazon.com/ec2/v2/home#Limits:
    """
    # https://aws.amazon.com/about-aws/whats-new/2014/06/19/amazon-ec2-service-limits-report-now-available/
    # Console-only APIs: getInstanceLimits, getAccountLimits, getAutoscalingLimits, getHostLimits
    # http://boto3.readthedocs.io/en/latest/reference/services/dynamodb.html#DynamoDB.Client.describe_limits
    attrs = ["max-instances", "vpc-max-security-groups-per-interface", "vpc-max-elastic-ips"]
    table = clients.ec2.describe_account_attributes(AttributeNames=attrs)["AccountAttributes"]
    page_output(tabulate(table, args))

parser = register_parser(limits)

def cmks(args):
    aliases = {alias.get("TargetKeyId"): alias for alias in paginate(clients.kms.get_paginator("list_aliases"))}
    table = []
    for key in paginate(clients.kms.get_paginator("list_keys")):
        key.update(aliases.get(key["KeyId"], {}))
        table.append(key)
    page_output(tabulate(table, args))

parser = register_parser(cmks, help="List KMS Customer Master Keys")

def certificates(args):
    page_output(tabulate(paginate(clients.acm.get_paginator("list_certificates")), args))

parser = register_parser(certificates, help="List Amazon Certificate Manager SSL certificates")
