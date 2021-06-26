"""
Utilities to manage AWS Elastic Filesystem resources.

To delete EFS filesystems, use ``aegea rm``.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, argparse, base64, socket

from . import register_parser, logger
from .ls import register_listing_parser
from .util.printing import page_output, tabulate
from .util.aws import (clients, resources, ensure_vpc, encode_tags, make_waiter, ensure_security_group,
                       resolve_security_group)

def efs(args):
    efs_parser.print_help()

efs_parser = register_parser(efs, help="Manage Elastic Filesystem resources", description=__doc__)

def ls(args):
    table = []
    for filesystem in clients.efs.describe_file_systems()["FileSystems"]:
        for mount_target in clients.efs.describe_mount_targets(FileSystemId=filesystem["FileSystemId"])["MountTargets"]:
            mount_target.update(filesystem)
            table.append(mount_target)
    args.columns += args.mount_target_columns
    page_output(tabulate(table, args, cell_transforms={"SizeInBytes": lambda x, r: x.get("Value") if x else None}))

parser = register_listing_parser(ls, parent=efs_parser, help="List EFS filesystems")
parser.add_argument("--mount-target-columns", nargs="+")

def create(args):
    vpc = resources.ec2.Vpc(args.vpc) if args.vpc else ensure_vpc()
    if args.security_groups is None:
        args.security_groups = [__name__]
        ensure_security_group(__name__, vpc, tcp_ingress=[dict(port=socket.getservbyname("nfs"),
                                                               source_security_group_name=__name__)])
    creation_token = base64.b64encode(bytearray(os.urandom(24))).decode()
    args.tags.append("Name=" + args.name)
    create_file_system_args = dict(CreationToken=creation_token,
                                   PerformanceMode=args.performance_mode,
                                   ThroughputMode=args.throughput_mode,
                                   Tags=encode_tags(args.tags))
    if args.throughput_mode == "provisioned":
        create_file_system_args.update(ProvisionedThroughputInMibps=args.provisioned_throughput_in_mibps)
    fs = clients.efs.create_file_system(**create_file_system_args)
    logger.info("Created EFS filesystem %s (%s)", fs["Name"], fs["FileSystemId"])
    waiter = make_waiter(clients.efs.describe_file_systems, "FileSystems[].LifeCycleState", "available", "pathAny")
    waiter.wait(FileSystemId=fs["FileSystemId"])
    security_groups = [resolve_security_group(g, vpc).id for g in args.security_groups]
    for subnet in vpc.subnets.all():
        mount_target = clients.efs.create_mount_target(FileSystemId=fs["FileSystemId"],
                                                       SubnetId=subnet.id,
                                                       SecurityGroups=security_groups)
        logger.info("Created EFS mount target %s in %s", mount_target["MountTargetId"], mount_target["SubnetId"])
    return fs

parser_create = register_parser(create, parent=efs_parser, help="Create an EFS filesystem")
parser_create.add_argument("name")
parser_create.add_argument("--performance-mode", choices={"generalPurpose", "maxIO"}, default="generalPurpose")
parser_create.add_argument("--throughput-mode", choices={"bursting", "provisioned"}, default="bursting")
parser_create.add_argument("--provisioned-throughput-in-mibps", type=float)
parser_create.add_argument("--tags", nargs="+", default=[], metavar="NAME=VALUE")
parser_create.add_argument("--security-groups", nargs="+")
parser_create.add_argument("--vpc", metavar="VPC_ID")
