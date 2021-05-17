"""
Remove or deprovision AWS EC2, IAM, and other resources. By default, this command performs a dry run; the -f/--force
option is required to actually execute the operation.

List resources to be removed by their ID or ARN, such as ami-eb957a8b, AIDAJYZD67Q2SUMUA2JBC, or
arn:aws:iam::123456789012:user/foo.

EC2 key pairs have no ARNs and no distingiushing ID prefix. To delete them by name, use the --key-pair option.
"""

import os, sys, argparse, subprocess, time

from botocore.exceptions import ClientError

from . import register_parser, logger
from .util.aws import expect_error_codes, resources, clients, paginate

def delete_vpc(name, args):
    vpc = resources.ec2.Vpc(name)
    for eigw in paginate(clients.ec2.get_paginator('describe_egress_only_internet_gateways')):
        for attachment in eigw["Attachments"]:
            if attachment.get("VpcId") == vpc.id:
                logger.info("Will delete %s", eigw["EgressOnlyInternetGatewayId"])
                clients.ec2.delete_egress_only_internet_gateway(
                    EgressOnlyInternetGatewayId=eigw["EgressOnlyInternetGatewayId"],
                    DryRun=not args.force
                )
    for igw in vpc.internet_gateways.all():
        logger.info("Will delete %s", igw)
        igw.detach_from_vpc(VpcId=vpc.id, DryRun=not args.force)
        igw.delete(DryRun=not args.force)
    for security_group in vpc.security_groups.all():
        if security_group.group_name != "default":
            logger.info("Will delete %s", security_group)
            security_group.delete(DryRun=not args.force)
    for nacl in vpc.network_acls.all():
        if not nacl.is_default:
            logger.info("Will delete %s", nacl)
            nacl.delete(DryRun=not args.force)
    for route_table in vpc.route_tables.all():
        logger.info("Will delete %s", route_table)
        for route in route_table.routes:
            try:
                route.delete(DryRun=not args.force)
            except ClientError:
                pass
        try:
            route_table.delete(DryRun=not args.force)
        except ClientError:
            pass
    for subnet in vpc.subnets.all():
        logger.info("Will delete %s", subnet)
        subnet.delete(DryRun=not args.force)
    logger.info("Will delete %s", vpc)
    vpc.delete(DryRun=not args.force)

def rm(args):
    for name in args.names:
        try:
            if args.key_pair:
                resources.ec2.KeyPair(name).delete(DryRun=not args.force)
            elif args.elb:
                if args.force:
                    clients.elb.delete_load_balancer(LoadBalancerName=name)
                else:
                    clients.elb.describe_load_balancer_attributes(LoadBalancerName=name)
            elif getattr(args, "lambda"):
                if args.force:
                    getattr(clients, "lambda").delete_function(FunctionName=name)
                else:
                    getattr(clients, "lambda").get_function(FunctionName=name)
            elif name.startswith("sg-"):
                resources.ec2.SecurityGroup(name).delete(DryRun=not args.force)
            elif name.startswith("vol-"):
                resources.ec2.Volume(name).delete(DryRun=not args.force)
            elif name.startswith("snap-"):
                resources.ec2.Snapshot(name).delete(DryRun=not args.force)
            elif name.startswith("subnet-"):
                resources.ec2.Subnet(name).delete(DryRun=not args.force)
            elif name.startswith("vpc-"):
                delete_vpc(name, args)
            elif name.startswith("lt-"):
                clients.ec2.delete_launch_template(LaunchTemplateId=name, DryRun=not args.force)
            elif name.startswith("igw-"):
                clients.ec2.delete_internet_gateway(InternetGatewayId=name, DryRun=not args.force)
            elif name.startswith("eigw-"):
                clients.ec2.delete_egress_only_internet_gateway(EgressOnlyInternetGatewayId=name, DryRun=not args.force)
            elif name.startswith("fl-"):
                if args.force:
                    clients.ec2.delete_flow_logs(FlowLogIds=[name])
                else:
                    res = clients.ec2.describe_flow_logs(Filters=[dict(Name="flow-log-id", Values=[name])])
                    assert res["FlowLogs"], "Unknown flow log ID"
            elif name.startswith("ami-"):
                image = resources.ec2.Image(name)
                snapshot_id = image.block_device_mappings[0].get("Ebs", {}).get("SnapshotId")
                image.deregister(DryRun=not args.force)
                if snapshot_id:
                    resources.ec2.Snapshot(snapshot_id).delete(DryRun=not args.force)
            elif name.startswith("sir-"):
                clients.ec2.cancel_spot_instance_requests(SpotInstanceRequestIds=[name], DryRun=not args.force)
            elif name.startswith("sfr-"):
                clients.ec2.cancel_spot_fleet_requests(SpotFleetRequestIds=[name],
                                                       TerminateInstances=False,
                                                       DryRun=not args.force)
            elif name.startswith("fs-"):
                efs = clients.efs
                for mount_target in efs.describe_mount_targets(FileSystemId=name)["MountTargets"]:
                    if args.force:
                        efs.delete_mount_target(MountTargetId=mount_target["MountTargetId"])
                        try:
                            while efs.describe_mount_targets(MountTargetId=mount_target["MountTargetId"]):
                                time.sleep(1)
                        except ClientError as e:
                            expect_error_codes(e, "MountTargetNotFound")
                efs.delete_file_system(FileSystemId=name) if args.force else True
            elif name.startswith("AKIA") and len(name) == 20 and name.upper() == name:
                clients.iam.delete_access_key(AccessKeyId=name) if args.force else True
            elif name.startswith("AROA") and len(name) == 21 and name.upper() == name:
                for role in resources.iam.roles.all():
                    if role.role_id == name:
                        logger.info("Deleting %s", role)
                        for instance_profile in role.instance_profiles.all():
                            instance_profile.remove_role(RoleName=role.name) if args.force else True
                            instance_profile.delete() if args.force else True
                        for policy in role.attached_policies.all():
                            role.detach_policy(PolicyArn=policy.arn) if args.force else True
                        for policy in role.policies.all():
                            policy.delete() if args.force else True
                        role.delete() if args.force else True
                        break
                else:
                    raise Exception("Role {} not found".format(name))
            else:
                raise Exception("Name {} not recognized as an AWS resource".format(name))
        except ClientError as e:
            expect_error_codes(e, "DryRunOperation")
    if not args.force:
        logger.info("Dry run succeeded on %s. Run %s again with --force (-f) to actually remove.", args.names, __name__)

parser = register_parser(rm, help="Remove or deprovision resources", description=__doc__)
parser.add_argument("names", nargs="+")
parser.add_argument("-f", "--force", action="store_true")
parser.add_argument("--key-pair", action="store_true", help="""
Assume input names are EC2 SSH key pair names (required when deleting key pairs, since they have no ID or ARN)""")
parser.add_argument("--elb", action="store_true", help="""
Assume input names are Elastic Load Balancer names (required when deleting ELBs, since they have no ID or ARN)""")
parser.add_argument("--lambda", action="store_true", help="""
Assume input names are Lambda function names (required when deleting Lambdas, since they have no ID or ARN)""")
