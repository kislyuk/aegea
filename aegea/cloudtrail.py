"""
List CloudTrail trails. Query, filter, and print trail events.
"""
import json
from datetime import datetime

from . import register_parser
from .util import Timestamp, add_time_bound_args, paginate
from .util.aws import ARN, clients, resolve_instance_id, resources
from .util.printing import BLUE, GREEN, page_output, tabulate


def cloudtrail(args):
    cloudtrail_parser.print_help()

cloudtrail_parser = register_parser(cloudtrail, help="List CloudTrail trails and print trail events",
                                    description=__doc__)

def ls(args):
    page_output(tabulate(clients.cloudtrail.describe_trails()["trailList"], args))

parser = register_parser(ls, parent=cloudtrail_parser, help="List CloudTrail trails")

def print_cloudtrail_event(event):
    log_record = json.loads(event["CloudTrailEvent"])
    user_identity = log_record["userIdentity"]
    if "arn" in user_identity:
        user_identity = ARN(log_record["userIdentity"]["arn"]).resource
    elif user_identity.get("type") == "AWSService":
        user_identity = user_identity.get("invokedBy")
    request_params = json.dumps(log_record.get("requestParameters"))
    print(event["EventTime"], user_identity, log_record["eventType"], log_record["eventName"], request_params)

def lookup(args):
    lookup_args = dict(LookupAttributes=[{"AttributeKey": k, "AttributeValue": v} for k, v in args.attributes])
    if args.start_time:
        lookup_args.update(StartTime=args.start_time)
    if args.end_time:
        lookup_args.update(EndTime=args.end_time)
    if args.category:
        lookup_args.update(EventCategory=args.category)
    for event in paginate(clients.cloudtrail.get_paginator('lookup_events'), **lookup_args):
        print_cloudtrail_event(event)

parser = register_parser(lookup, parent=cloudtrail_parser, help="Query and print CloudTrail events")
parser.add_argument("--attributes", nargs="+", metavar="NAME=VALUE", type=lambda x: x.split("=", 1), default=[])
parser.add_argument("--category")
add_time_bound_args(parser, start="-24h")
