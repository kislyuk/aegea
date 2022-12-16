import os
import sys

from . import register_parser
from .util.aws import add_tags, clients, resolve_instance_id, resources
from .util.aws.dns import DNSZone


def resolve_instance_ids(input_names):
    ids = [n for n in input_names if n.startswith("i-")]
    names = [n for n in input_names if not n.startswith("i-")]
    if names:
        descriptions = clients.ec2.describe_instances(Filters=[dict(Name="tag:Name", Values=names)])
        for reservation in descriptions["Reservations"]:
            for instance in reservation["Instances"]:
                ids.append(instance["InstanceId"])
    if len(ids) != len(input_names):
        raise Exception("Unable to resolve one or more of the instance names")
    return ids, names

def start(args):
    ids, names = resolve_instance_ids(args.names)
    clients.ec2.start_instances(InstanceIds=ids, DryRun=args.dry_run)

def stop(args):
    ids, names = resolve_instance_ids(args.names)
    clients.ec2.stop_instances(InstanceIds=ids, DryRun=args.dry_run)

def reboot(args):
    ids, names = resolve_instance_ids(args.names)
    clients.ec2.reboot_instances(InstanceIds=ids, DryRun=args.dry_run)

def terminate(args):
    ids, names = resolve_instance_ids(args.names)
    clients.ec2.terminate_instances(InstanceIds=ids, DryRun=args.dry_run)
    for name in names:
        # FIXME: when terminating by id, look up and delete DNS name
        if not args.dry_run:
            DNSZone().delete(name)

def rename(args):
    """Supply two names: Existing instance name or ID, and new name to assign to the instance."""
    old_name, new_name = args.names
    add_tags(resources.ec2.Instance(resolve_instance_id(old_name)), Name=new_name, dry_run=args.dry_run)

for action in (start, stop, reboot, terminate, rename):
    parser = register_parser(action, help="{} EC2 instances".format(action.__name__.capitalize()),
                             description=action.__doc__)
    parser.add_argument("--dry-run", "--dryrun", action="store_true")
    parser.add_argument("names", nargs="+")
