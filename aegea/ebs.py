"""
Utilities to manage AWS Elastic Block Store volumes and snapshots.

To delete EBS volumes or snapshots, use ``aegea rm``.
"""

import json
import os
import re
import subprocess
import sys
import time
from functools import lru_cache

from botocore.exceptions import ClientError

from . import logger, register_parser
from .ls import add_name, filter_and_tabulate, filter_collection, register_filtering_parser
from .util import get_mkfs_command
from .util.aws import ARN, clients, encode_tags, ensure_subnet, ensure_vpc, get_metadata, resolve_instance_id, resources
from .util.printing import get_cell, page_output, tabulate


def complete_volume_id(**kwargs):
    return [i["VolumeId"] for i in clients.ec2.describe_volumes()["Volumes"]]

def ebs(args):
    ebs_parser.print_help()

ebs_parser = register_parser(ebs, help="Manage Elastic Block Store resources", description=__doc__)

def ls(args):
    @lru_cache()
    def instance_id_to_name(i):
        return add_name(resources.ec2.Instance(i)).name
    table = [{f: get_cell(i, f) for f in args.columns} for i in filter_collection(resources.ec2.volumes, args)]
    if "attachments" in args.columns:
        for row in table:
            row["attachments"] = ", ".join(instance_id_to_name(a["InstanceId"]) for a in row["attachments"])
    page_output(tabulate(table, args))

parser = register_filtering_parser(ls, parent=ebs_parser, help="List EC2 EBS volumes")

def snapshots(args):
    page_output(filter_and_tabulate(resources.ec2.snapshots.filter(OwnerIds=[ARN.get_account_id()]), args))

parser = register_filtering_parser(snapshots, parent=ebs_parser, help="List EC2 EBS snapshots")

def create(args):
    if (args.format or args.mount) and not args.attach:
        raise SystemExit("Arguments --format and --mount require --attach")
    if not args.size:
        raise SystemExit("Argument --size-gb is required")
    create_args = dict(Size=args.size, Encrypted=True)
    if args.tags:
        create_args.update(TagSpecifications=[dict(ResourceType="volume", Tags=encode_tags(args.tags))])
    for arg in "dry_run snapshot_id availability_zone volume_type iops kms_key_id".split():
        if getattr(args, arg) is not None:
            create_args["".join(x.capitalize() for x in arg.split("_"))] = getattr(args, arg)
    if "AvailabilityZone" not in create_args:
        if args.attach:
            create_args["AvailabilityZone"] = get_metadata("placement/availability-zone")
        else:
            create_args["AvailabilityZone"] = ensure_subnet(ensure_vpc()).availability_zone
    res = clients.ec2.create_volume(**create_args)
    clients.ec2.get_waiter("volume_available").wait(VolumeIds=[res["VolumeId"]])
    if args.attach:
        try:
            attach(parser_attach.parse_args([res["VolumeId"]], namespace=args))
        except Exception:
            print(json.dumps(res, indent=2, default=lambda x: str(x)))
            raise
    return res

parser_create = register_parser(create, parent=ebs_parser, help="Create an EBS volume")
parser_create.add_argument("--dry-run", action="store_true")
parser_create.add_argument("--snapshot-id")
parser_create.add_argument("--availability-zone")
parser_create.add_argument("--kms-key-id")
parser_create.add_argument("--tags", nargs="+", metavar="TAG_NAME=VALUE")
parser_create.add_argument("--attach", action="store_true",
                           help="Attach volume to this instance (only valid when running on EC2)")

def snapshot(args):
    return clients.ec2.create_snapshot(DryRun=args.dry_run, VolumeId=args.volume_id)
parser_snapshot = register_parser(snapshot, parent=ebs_parser, help="Create an EBS snapshot")
parser_snapshot.add_argument("volume_id").completer = complete_volume_id

def attach_volume(args):
    return clients.ec2.attach_volume(DryRun=args.dry_run,
                                     VolumeId=args.volume_id,
                                     InstanceId=args.instance,
                                     Device=args.device)

def find_volume_id(mountpoint):
    with open("/proc/mounts") as fh:
        for line in fh:
            devnode, mount, _ = line.split(" ", 2)
            if mountpoint == mount:
                break
        else:
            raise Exception(f"Mountpoint {mountpoint} not found in /proc/mounts")
    for devnode_link in os.listdir("/dev/disk/by-id"):
        if "Elastic_Block_Store" in devnode_link and os.path.realpath("/dev/disk/by-id/" + devnode_link) == devnode:
            break
    else:
        raise Exception(f"EBS volume ID not found for mountpoint {mountpoint} (devnode {devnode})")
    return re.search(r"Elastic_Block_Store_(vol[\w]+)", devnode_link).group(1).replace("vol", "vol-")

def find_devnode(volume_id):
    if os.path.exists("/dev/disk/by-id"):
        for devnode in os.listdir("/dev/disk/by-id"):
            if "Elastic_Block_Store" in devnode and volume_id.replace("-", "") in devnode:
                return "/dev/disk/by-id/" + devnode
    if os.path.exists("/dev/disk/by-label/" + get_fs_label(volume_id)):
        return "/dev/disk/by-label/" + get_fs_label(volume_id)
    attachment = resources.ec2.Volume(volume_id).attachments[0]
    if get_metadata("instance-id") == attachment["InstanceId"] and os.path.exists("/dev/" + attachment["Device"]):
        return "/dev/" + attachment["Device"]
    raise Exception(f"Could not find devnode for {volume_id}")

def get_fs_label(volume_id):
    return "aegv" + volume_id[4:12]

def attach(args):
    if args.instance is None:
        args.instance = get_metadata("instance-id")
    devices = args.device if args.device else ["xvd" + chr(i + 1) for i in reversed(range(ord("a"), ord("z")))]
    for i, device in enumerate(devices):
        try:
            args.device = devices[i]
            res = attach_volume(args)
            break
        except ClientError as e:
            if re.search("VolumeInUse.+already attached to an instance", str(e)):
                if resources.ec2.Volume(args.volume_id).attachments[0]["InstanceId"] == args.instance:
                    logger.warn("Volume %s is already attached to instance %s", args.volume_id, args.instance)
                    break
            if i + 1 < len(devices) and re.search("InvalidParameterValue.+Attachment point.+is already in use", str(e)):
                logger.warn("BDM node %s is already in use, looking for next available node", devices[i])
                continue
            raise
    res = clients.ec2.get_waiter("volume_in_use").wait(VolumeIds=[args.volume_id])
    if args.format or args.mount:
        for i in range(30):
            try:
                find_devnode(args.volume_id)
                break
            except Exception:
                logger.debug("Waiting for device node to appear for %s", args.volume_id)
                time.sleep(1)
    if args.format:
        logger.info("Formatting %s (%s)", args.volume_id, find_devnode(args.volume_id))
        label = get_fs_label(args.volume_id)
        command = get_mkfs_command(fs_type=args.format, label=label) + find_devnode(args.volume_id)
        subprocess.check_call(command, shell=True, stdout=sys.stderr.buffer)
    if args.mount:
        logger.info("Mounting %s at %s", args.volume_id, args.mount)
        subprocess.check_call(["mount", find_devnode(args.volume_id), args.mount], stdout=sys.stderr.buffer)
    return res
parser_attach = register_parser(attach, parent=ebs_parser, help="Attach an EBS volume to an EC2 instance")
parser_attach.add_argument("volume_id").completer = complete_volume_id
parser_attach.add_argument("instance", type=resolve_instance_id, nargs="?")
parser_attach.add_argument("--device", choices=["xvd" + chr(i + 1) for i in range(ord("a"), ord("z"))],
                           help="Device node to attach volume to. Default: auto-select the first available node")
for parser in parser_create, parser_attach:
    parser.add_argument("--format", nargs="?", const="xfs",
                        help="Use this command and arguments to format volume after attaching (only valid on EC2)")
    parser.add_argument("--mount", nargs="?", const="/mnt", help="Mount volume on given mountpoint (only valid on EC2)")

def detach(args):
    """
    Detach an EBS volume from an EC2 instance.

    If *volume_id* does not start with "vol-", it is interpreted as a mountpoint on the local instance,
    mapped to its underlying EBS volume, unmounted and detached.
    """
    if args.volume_id.startswith("vol-"):
        volume_id = args.volume_id
    else:
        volume_id = find_volume_id(mountpoint=args.volume_id)
        args.unmount = True
    if args.unmount:
        cmd = "umount {devnode} || (kill -9 $(lsof -t +f -- $(readlink -f {devnode}) | sort | uniq); umount {devnode} || umount -l {devnode})"  # noqa
        subprocess.call(cmd.format(devnode=find_devnode(volume_id)), shell=True)
    attachment = resources.ec2.Volume(volume_id).attachments[0]
    res = clients.ec2.detach_volume(DryRun=args.dry_run,
                                    VolumeId=volume_id,
                                    InstanceId=attachment["InstanceId"],
                                    Device=attachment["Device"],
                                    Force=args.force)
    clients.ec2.get_waiter("volume_available").wait(VolumeIds=[volume_id])
    if args.delete:
        logger.info("Deleting EBS volume %s", volume_id)
        clients.ec2.delete_volume(VolumeId=volume_id, DryRun=args.dry_run)
    return res
parser_detach = register_parser(detach, parent=ebs_parser)
parser_detach.add_argument("volume_id", help="EBS volume ID or mountpoint").completer = complete_volume_id
parser_detach.add_argument("--unmount", action="store_true", help="Unmount the volume before detaching")
parser_detach.add_argument("--delete", action="store_true", help="Delete the volume after detaching")
parser_detach.add_argument("--force", action="store_true")

def modify(args):
    modify_args = dict(VolumeId=args.volume_id, DryRun=args.dry_run)
    if args.size:
        modify_args.update(Size=args.size)
    if args.volume_type:
        modify_args.update(VolumeType=args.volume_type)
    if args.iops:
        modify_args.update(Iops=args.iops)
    res = clients.ec2.modify_volume(**modify_args)["VolumeModification"]
    # if args.wait:
    #     waiter = make_waiter(clients.ec2.describe_volumes_modifications, "VolumesModifications[].ModificationState",
    #                          "optimizing", "pathAny")
    #     waiter.wait(VolumeIds=[args.volume_id])
    return res
parser_modify = register_parser(modify, parent=ebs_parser, help="Change the size, type, or IOPS of an EBS volume")
parser_modify.add_argument("volume_id").completer = complete_volume_id

for parser in parser_create, parser_modify:
    parser.add_argument("--size-gb", dest="size", type=int, help="Volume size in gigabytes")
    parser.add_argument("--volume-type", choices={"standard", "io1", "gp2", "sc1", "st1"},
                        help="io1, PIOPS SSD; gp2, general purpose SSD; sc1, cold HDD; st1, throughput optimized HDD")
    parser.add_argument("--iops", type=int)

for parser in parser_snapshot, parser_attach, parser_detach, parser_modify:
    parser.add_argument("--dry-run", action="store_true")
