"""
Utilities to manage AWS Route 53 DNS zones and records.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, argparse

from . import register_parser
from .util import paginate
from .util.aws import DNSZone, clients
from .util.printing import get_field, page_output, format_table

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
    return DNSZone(args.zone).update(name=args.name, value=args.value, record_type=args.record_type)

parser = register_parser(update, parent=zones_parser, help="Update a Route53 DNS record")
parser.add_argument("zone")
parser.add_argument("name")
parser.add_argument("value")
parser.add_argument("--record-type", default="CNAME")

def delete(args):
    return DNSZone(args.zone).delete(name=args.name, record_type=args.record_type, missing_ok=False)

parser = register_parser(delete, parent=zones_parser, help="Delete a Route53 DNS record")
parser.add_argument("zone")
parser.add_argument("name", help=r'Enter a "\052" literal to represent a wildcard.')
parser.add_argument("--record-type", default="CNAME")