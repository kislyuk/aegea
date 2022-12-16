"""
Utilities to manage AWS Relational Database Service instances and snapshots.
"""

import argparse
import getpass
import os
import sys
from datetime import datetime

from . import register_parser
from .util import paginate
from .util.aws import ARN, clients, encode_tags
from .util.printing import page_output, tabulate


def rds(args):
    rds_parser.print_help()

rds_parser = register_parser(rds, help="Manage RDS resources", description=__doc__)

def add_tags(resource, prefix, key):
    resource_id = ":".join([prefix, resource[key]])
    arn = ARN(service="rds", resource=resource_id)
    resource["tags"] = clients.rds.list_tags_for_resource(ResourceName=str(arn))["TagList"]
    return resource

def list_rds_clusters():
    paginator = clients.rds.get_paginator("describe_db_clusters")
    return [add_tags(i, "cluster", "DBClusterIdentifier") for i in paginate(paginator)]

def list_rds_instances():
    paginator = clients.rds.get_paginator("describe_db_instances")
    return [add_tags(i, "db", "DBInstanceIdentifier") for i in paginate(paginator)]

def ls(args):
    page_output(tabulate(list_rds_clusters(), args))

parser = register_parser(ls, parent=rds_parser, help="List RDS clusters")

def instances(args):
    page_output(tabulate(list_rds_instances(), args))

parser = register_parser(instances, parent=rds_parser, help="List RDS instances")

def snapshots(args):
    paginator = clients.rds.get_paginator("describe_db_snapshots")
    table = [add_tags(i, "snapshot", "DBSnapshotIdentifier") for i in paginate(paginator)]
    page_output(tabulate(table, args))

parser = register_parser(snapshots, parent=rds_parser, help="List RDS snapshots")

def create(args):
    create_args = dict(DBInstanceIdentifier=args.name,
                       AllocatedStorage=args.storage,
                       Engine=args.engine,
                       StorageType=args.storage_type,
                       StorageEncrypted=True,
                       AutoMinorVersionUpgrade=True,
                       MultiAZ=False,
                       MasterUsername=args.master_username or getpass.getuser(),
                       MasterUserPassword=args.master_user_password,
                       VpcSecurityGroupIds=args.security_groups,
                       DBInstanceClass=args.db_instance_class,
                       Tags=encode_tags(args.tags),
                       CopyTagsToSnapshot=True)
    if args.db_name:
        create_args.update(DBName=args.db_name)
    if args.engine_version:
        create_args.update(EngineVersion=args.engine_version)
    clients.rds.create_db_instance(**create_args)
    clients.rds.get_waiter("db_instance_available").wait(DBInstanceIdentifier=args.name)
    instance = clients.rds.describe_db_instances(DBInstanceIdentifier=args.name)["DBInstances"][0]
    return {k: instance[k] for k in ("Endpoint", "DbiResourceId", "DBInstanceStatus")}

parser = register_parser(create, parent=rds_parser, help="Create an RDS instance")
parser.add_argument("name")
parser.add_argument("--db-name")
parser.add_argument("--engine")
parser.add_argument("--engine-version")
parser.add_argument("--storage", type=int)
parser.add_argument("--storage-type")
parser.add_argument("--master-username", "--username")
parser.add_argument("--master-user-password", "--password", required=True)
parser.add_argument("--db-instance-class")
parser.add_argument("--tags", nargs="+", default=[], metavar="TAG_NAME=VALUE")
parser.add_argument("--security-groups", nargs="+", default=[])

def delete(args):
    clients.rds.delete_db_instance(DBInstanceIdentifier=args.name, SkipFinalSnapshot=True)

parser = register_parser(delete, parent=rds_parser, help="Delete an RDS instance")
parser.add_argument("name").completer = lambda **kw: [i["DBInstanceIdentifier"] for i in list_rds_instances()]

def snapshot(args):
    return clients.rds.create_db_snapshot(DBInstanceIdentifier=args.instance_name,
                                          DBSnapshotIdentifier=args.snapshot_name,
                                          Tags=encode_tags(args.tags))

parser = register_parser(snapshot, parent=rds_parser, help="Create an RDS snapshot")
parser.add_argument("instance_name")
parser.add_argument("snapshot_name")
parser.add_argument("--tags", nargs="+", default=[])

def restore(args):
    tags = dict(tag.split("=", 1) for tag in args.tags)
    clients.rds.restore_db_instance_from_db_snapshot(DBInstanceIdentifier=args.instance_name,
                                                     DBSnapshotIdentifier=args.snapshot_name,
                                                     StorageType=args.storage_type,
                                                     AutoMinorVersionUpgrade=True,
                                                     MultiAZ=False,
                                                     DBInstanceClass=args.db_instance_class,
                                                     Tags=[dict(Key=k, Value=v) for k, v in tags.items()],
                                                     CopyTagsToSnapshot=True)
    clients.rds.get_waiter("db_instance_available").wait(DBInstanceIdentifier=args.instance_name)
    instance = clients.rds.describe_db_instances(DBInstanceIdentifier=args.instance_name)["DBInstances"][0]
    return {k: instance[k] for k in ("Endpoint", "DbiResourceId")}

parser = register_parser(restore, parent=rds_parser, help="Restore an RDS instance from a snapshot")
parser.add_argument("snapshot_name")
parser.add_argument("instance_name")
parser.add_argument("--storage-type")
parser.add_argument("--db-instance-class")
parser.add_argument("--tags", nargs="+", default=[])
