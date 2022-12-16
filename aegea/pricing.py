import json
import os
import re
import sys
from datetime import datetime, timedelta
from statistics import median

import boto3
import requests

from . import register_parser
from .util import Timestamp, paginate
from .util.aws import clients, get_products, instance_type_completer, offers_api, region_name
from .util.printing import format_datetime, format_table, page_output, tabulate


def describe_services():
    client = boto3.client("pricing", region_name="us-east-1")
    return paginate(client.get_paginator("describe_services"))

def get_instance_type_sort_key(args):
    def instance_type_sort_key(row):
        instance_type = re.match(r"(.+)\.(\d*)(.+)", row[args.columns.index("instanceType")])
        family, mx, size = instance_type.groups() if instance_type else (0, 0, 0)  # type: ignore
        if size == "metal":  # type: ignore
            mx = sys.maxsize
        size_order = ["nano", "micro", "small", "medium", "large", "xlarge"]
        return family, int(mx) if mx else 1, size_order.index(size) if size in size_order else sys.maxsize # type: ignore # noqa
    return instance_type_sort_key

def pricing(args):
    if args.spot:
        args.columns = args.columns_spot
        paginator = clients.ec2.get_paginator("describe_spot_price_history")
        page_output(tabulate(paginate(paginator, StartTime=args.spot_start_time), args))
    elif args.service_code:
        if args.region is None:
            args.region = clients.ec2.meta.region_name
        args.columns += getattr(args, "columns_" + args.service_code, [])
        args.filters += getattr(args, "filters_" + args.service_code, [])
        if hasattr(args, "sort_by_" + args.service_code):
            args.sort_by = getattr(args, "sort_by_" + args.service_code)
        if args.sort_by == "instanceType":
            args.sort_by = get_instance_type_sort_key(args)
        filters = [("location", region_name(args.region))] + args.filters
        table = get_products(args.service_code, region=args.region, filters=filters, terms=args.terms,
                             max_cache_age_days=args.max_cache_age_days)
        page_output(tabulate(table, args))
    else:
        args.columns = ["ServiceCode", "AttributeNames"]
        page_output(tabulate(describe_services(), args))

parser = register_parser(pricing, help="List AWS prices")
pricing_arg = parser.add_argument("service_code", nargs="?", help="""
AWS product offer to list prices for. Run without this argument to see the list of available product service codes.""")
pricing_arg.completer = lambda **kwargs: [service["ServiceCode"] for service in describe_services()]
parser.add_argument("--columns", nargs="+")
parser.add_argument("--filters", nargs="+", metavar="NAME=VALUE", type=lambda x: x.split("=", 1), default=[])
parser.add_argument("--terms", nargs="+", default=["OnDemand"])
parser.add_argument("--sort-by")
parser.add_argument("--spot", action="store_true", help="Display AWS EC2 Spot Instance pricing history")
parser.add_argument("--spot-start-time", type=Timestamp, default=Timestamp("-1h"), metavar="START",
                    help="Time to start spot price history." + Timestamp.__doc__)  # type: ignore
parser.add_argument("--columns-spot")
