import os
import sys
from datetime import datetime
from typing import List

import boto3
import botocore.exceptions

from . import register_parser
from .util import ThreadPoolExecutor
from .util.printing import format_table, page_output


def get_stats_for_region(region):
    try:
        session = boto3.Session(region_name=region)
        num_instances = len(list(session.resource("ec2").instances.all()))
        num_amis = len(list(session.resource("ec2").images.filter(Owners=["self"])))
        num_vpcs = len(list(session.resource("ec2").vpcs.all()))
        num_enis = len(list(session.resource("ec2").network_interfaces.all()))
        num_volumes = len(list(session.resource("ec2").volumes.all()))
    except botocore.exceptions.ClientError:
        num_instances, num_amis, num_vpcs, num_enis, num_volumes = ["Access denied"] * 5  # type: ignore
    return [region, num_instances, num_amis, num_vpcs, num_enis, num_volumes]

def top(args):
    table = []  # type: List[List]
    columns = ["Region", "Instances", "AMIs", "VPCs", "Network interfaces", "EBS volumes"]
    executor = ThreadPoolExecutor()
    table = list(executor.map(get_stats_for_region, boto3.Session().get_available_regions("ec2")))
    page_output(format_table(table, column_names=columns, max_col_width=args.max_col_width))

parser = register_parser(top, help='Show an overview of AWS resources per region')
