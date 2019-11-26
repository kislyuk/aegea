from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, argparse
from botocore.exceptions import ClientError

from .ls import register_parser, register_listing_parser
from .logs import grep, add_time_bound_args
from .util.printing import page_output, tabulate
from .util.exceptions import AegeaException
from .util.aws import ARN, resources, clients, ensure_iam_role, ensure_vpc, expect_error_codes

def flow_logs(args):
    flow_logs_parser.print_help()

flow_logs_parser = register_parser(flow_logs, help="Manage EC2 VPC flow logs", description=__doc__)

def create(args):
    if args.resource and args.resource.startswith("vpc-"):
        resource_type = "VPC"
    elif args.resource and args.resource.startswith("subnet-"):
        resource_type = "Subnet"
    elif args.resource and args.resource.startswith("eni-"):
        resource_type = "NetworkInterface"
    elif args.resource:
        raise AegeaException('Unrecognized resource type: "{}"'.format(args.resource))
    else:
        args.resource = ensure_vpc().id
        resource_type = "VPC"
    flow_logs_iam_role = ensure_iam_role(__name__,
                                         policies=["service-role/AmazonAPIGatewayPushToCloudWatchLogs"],
                                         trust=["vpc-flow-logs"])
    try:
        return clients.ec2.create_flow_logs(ResourceIds=[args.resource],
                                            ResourceType=resource_type,
                                            TrafficType=args.traffic_type,
                                            LogGroupName=__name__,
                                            DeliverLogsPermissionArn=flow_logs_iam_role.arn)
    except ClientError as e:
        expect_error_codes(e, "FlowLogAlreadyExists")
        return dict(FlowLogAlreadyExists=True)

parser = register_parser(create, parent=flow_logs_parser, help="Create VPC flow logs")
parser.add_argument("--resource")
parser.add_argument("--traffic_type", choices=["ACCEPT", "REJECT", "ALL"], default="ALL")

def ls(args):
    describe_flow_logs_args = dict(Filters=[dict(Name="resource-id", Values=[args.resource])]) if args.resource else {}
    page_output(tabulate(clients.ec2.describe_flow_logs(**describe_flow_logs_args)["FlowLogs"], args))

parser = register_listing_parser(ls, parent=flow_logs_parser, help="List VPC flow logs")
parser.add_argument("--resource")

def get(args):
    args.log_group, args.pattern = __name__, None
    args.log_stream = "-".join([args.network_interface, args.traffic_type]) if args.network_interface else None
    grep(args)

parser = register_parser(get, parent=flow_logs_parser, help="Get VPC flow logs")
parser.add_argument("--network-interface")
parser.add_argument("--traffic_type", choices=["ACCEPT", "REJECT", "ALL"], default="ALL")
add_time_bound_args(parser)
