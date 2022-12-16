"""
Utilities to manage AWS Route 53 DNS zones and records.
"""

import argparse
import os
import sys

from . import register_parser
from .util import paginate
from .util.aws import clients
from .util.aws.dns import DNSZone
from .util.printing import format_table, get_field, page_output


def zones(args):
    zones_parser.print_help()

zones_parser = register_parser(zones, help="Manage Route53 DNS zones", description=__doc__)

def ls(args):
    table = []
    rrs_cols = ["Name", "Type", "TTL"]
    record_cols = ["Value"]
    for zone in paginate(clients.route53.get_paginator("list_hosted_zones")):
        if args.zones and zone["Name"] not in args.zones + [z + "." for z in args.zones]:
            continue
        for rrs in paginate(clients.route53.get_paginator("list_resource_record_sets"), HostedZoneId=zone["Id"]):
            for record in rrs.get("ResourceRecords", [rrs.get("AliasTarget", {})]):
                row = [rrs.get(f) for f in rrs_cols]
                row += [record.get(f, record.get("DNSName")) for f in record_cols]
                row += [get_field(zone, "Config.PrivateZone"), zone["Id"].rpartition("/")[-1]]
                table.append(row)
    column_names = rrs_cols + record_cols + ["Private", "Id"]
    page_output(format_table(table, column_names=column_names, max_col_width=args.max_col_width))

parser = register_parser(ls, parent=zones_parser, help="List Route53 DNS zones and records")
parser.add_argument("zones", nargs="*")

def update(args):
    return DNSZone(args.zone).update(*zip(*args.updates), record_type=args.record_type)  # type: ignore

parser = register_parser(update, parent=zones_parser, help="Update Route53 DNS records")
parser.add_argument("zone")
parser.add_argument("updates", nargs="+", metavar="NAME=VALUE", type=lambda x: x.split("=", 1))
parser.add_argument("--record-type", default="CNAME")

def delete(args):
    return DNSZone(args.zone).delete(name=args.name, record_type=args.record_type, missing_ok=False)

parser = register_parser(delete, parent=zones_parser, help="Delete Route53 DNS records")
parser.add_argument("zone")
parser.add_argument("name", help=r'Enter a "\052" literal to represent a wildcard.')
parser.add_argument("--record-type", default="CNAME")
