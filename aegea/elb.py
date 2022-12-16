"""
Manage AWS EC2 Elastic Load Balancers (ELBs) and Application Load Balancers (ALBs).
"""

import argparse
import os
import sys
from functools import lru_cache

from botocore.exceptions import ClientError

from .ls import register_listing_parser, register_parser
from .util import Timestamp, hashabledict, paginate
from .util.aws import (
    ARN,
    availability_zones,
    clients,
    ensure_vpc,
    expect_error_codes,
    get_elb_dns_aliases,
    resolve_instance_id,
    resolve_security_group,
    resources,
)
from .util.aws.dns import DNSZone
from .util.exceptions import AegeaException
from .util.printing import page_output, tabulate


def elb(args):
    elb_parser.print_help()

elb_parser = register_parser(elb, help="Manage Elastic Load Balancers", description=__doc__)

def ls(args):
    @lru_cache()
    def sgid_to_name(i):
        return resources.ec2.SecurityGroup(i).group_name
    table = []
    dns_aliases = get_elb_dns_aliases()
    for row in paginate(clients.elb.get_paginator("describe_load_balancers")):
        row.update(alias=dns_aliases.get(row["DNSName"]), type="ELB")
        if args.elbs and row["LoadBalancerName"] not in args.elbs and (row["alias"] or "").rstrip(".") not in args.elbs:
            continue
        instances = clients.elb.describe_instance_health(LoadBalancerName=row["LoadBalancerName"])["InstanceStates"]
        table.extend([dict(row, **instance) for instance in instances] if instances else [row])
    for row in paginate(clients.elbv2.get_paginator("describe_load_balancers")):
        row.update(alias=dns_aliases.get(row["DNSName"]), type="ALB")
        if args.elbs and row["LoadBalancerName"] not in args.elbs and (row["alias"] or "").rstrip(".") not in args.elbs:
            continue
        target_groups = clients.elbv2.describe_target_groups(LoadBalancerArn=row["LoadBalancerArn"])["TargetGroups"]
        for tg in target_groups:
            targets = get_targets(tg)
            table.extend([dict(row, **target) for target in targets] if targets else [row])
    page_output(tabulate(table, args, cell_transforms={"SecurityGroups": lambda x, r: ", ".join(map(sgid_to_name, x))}))

parser = register_listing_parser(ls, parent=elb_parser, help="List ELBs")
parser.add_argument("elbs", nargs="*")

def get_target_group(alb_name, target_group_name):
    alb = clients.elbv2.describe_load_balancers(Names=[alb_name])["LoadBalancers"][0]
    target_groups = clients.elbv2.describe_target_groups(LoadBalancerArn=alb["LoadBalancerArn"])["TargetGroups"]
    for target_group in target_groups:
        if target_group["TargetGroupName"] == target_group_name:
            return dict(alb, **target_group)
    m = "Target group {} not found in {} (target groups found: {})"
    raise AegeaException(m.format(target_group_name, alb_name, ", ".join(t["TargetGroupName"] for t in target_groups)))

def get_targets(target_group):
    res = clients.elbv2.describe_target_health(TargetGroupArn=target_group["TargetGroupArn"])
    return res["TargetHealthDescriptions"]

def register(args):
    if args.type == "ELB":
        instances = [dict(InstanceId=i) for i in args.instances]
        res = clients.elb.register_instances_with_load_balancer(LoadBalancerName=args.elb_name, Instances=instances)
        return dict(registered=instances, current=res["Instances"])
    elif args.type == "ALB":
        target_group = get_target_group(args.elb_name, args.target_group.format(elb_name=args.elb_name))
        instances = [dict(Id=i, Port=target_group.get("Port", args.instance_port)) for i in args.instances]
        clients.elbv2.register_targets(TargetGroupArn=target_group["TargetGroupArn"], Targets=instances)
        return dict(registered=instances, current=[t["Target"] for t in get_targets(target_group)])

parser_register = register_parser(register, parent=elb_parser, help="Add EC2 instances to an ELB")

def deregister(args):
    if args.type == "ELB":
        instances = [dict(InstanceId=i) for i in args.instances]
        res = clients.elb.deregister_instances_from_load_balancer(LoadBalancerName=args.elb_name, Instances=instances)
        return dict(deregistered=instances, current=res["Instances"])
    elif args.type == "ALB":
        target_group = get_target_group(args.elb_name, args.target_group.format(elb_name=args.elb_name))
        instances = [dict(Id=i, Port=target_group.get("Port", args.instance_port)) for i in args.instances]
        clients.elbv2.deregister_targets(TargetGroupArn=target_group["TargetGroupArn"], Targets=instances)
        return dict(deregistered=instances, current=[t["Target"] for t in get_targets(target_group)])

parser_deregister = register_parser(deregister, parent=elb_parser, help="Remove EC2 instances from an ELB")

def replace(args):
    result = register(args)
    old_instances = set(hashabledict(d) for d in result["current"]) - set(hashabledict(d) for d in result["registered"])
    if old_instances:
        args.instances = [i.get("Id", i.get("InstanceId")) for i in old_instances]
        result.update(deregister(args))
    return result

parser_replace = register_parser(replace, parent=elb_parser,
                                 help="Replace all EC2 instances in an ELB with the ones given")

def find_acm_cert(dns_name):
    for cert in paginate(clients.acm.get_paginator("list_certificates")):
        cert.update(clients.acm.describe_certificate(CertificateArn=cert["CertificateArn"])["Certificate"])
        for name in cert["SubjectAlternativeNames"]:
            if name in [dns_name, ".".join(["*"] + dns_name.split(".")[1:])]:
                return cert
    raise AegeaException("Unable to find ACM certificate for {}".format(dns_name))

def ensure_target_group(name, **kwargs):
    # TODO: delete and re-create action and TG if settings don't match
    try:
        for tg in paginate(clients.elbv2.get_paginator("describe_target_groups"), Names=[name]):
            return tg
    except ClientError as e:
        expect_error_codes(e, "TargetGroupNotFound")
        res = clients.elbv2.create_target_group(Name=name, **kwargs)
        return res["TargetGroups"][0]

def create(args):
    for zone in paginate(clients.route53.get_paginator("list_hosted_zones")):
        if args.dns_alias.endswith("." + zone["Name"].rstrip(".")):
            break
    else:
        raise AegeaException("Unable to find Route53 DNS zone for {}".format(args.dns_alias))
    cert = find_acm_cert(args.dns_alias)
    if args.type == "ELB":
        listener = dict(Protocol="https",
                        LoadBalancerPort=443,
                        SSLCertificateId=cert["CertificateArn"],
                        InstanceProtocol="http",
                        InstancePort=args.instance_port or 80)
        elb = clients.elb.create_load_balancer(LoadBalancerName=args.elb_name,
                                               Listeners=[listener],
                                               AvailabilityZones=list(availability_zones()),
                                               SecurityGroups=[sg.id for sg in args.security_groups])
    elif args.type == "ALB":
        vpc = ensure_vpc()
        res = clients.elbv2.create_load_balancer(Name=args.elb_name,
                                                 Subnets=[subnet.id for subnet in vpc.subnets.all()],
                                                 SecurityGroups=[sg.id for sg in args.security_groups])
        elb = res["LoadBalancers"][0]
        target_group = ensure_target_group(args.target_group.format(elb_name=args.elb_name),
                                           Protocol="HTTP",
                                           Port=args.instance_port,
                                           VpcId=vpc.id,
                                           HealthCheckProtocol=args.health_check_protocol,
                                           HealthCheckPort=args.health_check_port,
                                           HealthCheckPath=args.health_check_path,
                                           Matcher=dict(HttpCode=args.ok_http_codes))
        listener_params = dict(Protocol="HTTPS",
                               Port=443,
                               Certificates=[dict(CertificateArn=cert["CertificateArn"])],
                               DefaultActions=[dict(Type="forward", TargetGroupArn=target_group["TargetGroupArn"])])
        res = clients.elbv2.describe_listeners(LoadBalancerArn=elb["LoadBalancerArn"])
        if res["Listeners"]:
            res = clients.elbv2.modify_listener(ListenerArn=res["Listeners"][0]["ListenerArn"], **listener_params)
        else:
            res = clients.elbv2.create_listener(LoadBalancerArn=elb["LoadBalancerArn"], **listener_params)
        listener = res["Listeners"][0]
        if args.path_pattern:
            rules = clients.elbv2.describe_rules(ListenerArn=listener["ListenerArn"])["Rules"]
            clients.elbv2.create_rule(ListenerArn=listener["ListenerArn"],
                                      Conditions=[dict(Field="path-pattern", Values=[args.path_pattern])],
                                      Actions=[dict(Type="forward", TargetGroupArn=target_group["TargetGroupArn"])],
                                      Priority=len(rules))
    replace(args)
    DNSZone(zone["Name"]).update(args.dns_alias.replace("." + zone["Name"].rstrip("."), ""), elb["DNSName"])
    return dict(elb_name=args.elb_name, dns_name=elb["DNSName"], dns_alias=args.dns_alias)

parser_create = register_parser(create, parent=elb_parser, help="Create a new ELB")
parser_create.add_argument("--security-groups", nargs="+", type=resolve_security_group, required=True, help="""
Security groups to assign the ELB. You must allow TCP traffic to flow between clients and the ELB on ports 80/443
and allow TCP traffic to flow between the ELB and the instances on INSTANCE_PORT.""")
parser_create.add_argument("--dns-alias", required=True, help="Fully qualified DNS name that will point to the ELB")
parser_create.add_argument("--path-pattern")
parser_create.add_argument("--health-check-protocol", default="HTTP", choices={"HTTP", "HTTPS"})
parser_create.add_argument("--health-check-port", default="traffic-port", help="Port to be queried by ELB health check")
parser_create.add_argument("--health-check-path", default="/", help="Path to be queried by ELB health check")
parser_create.add_argument("--ok-http-codes", default="200-399",
                           help="Comma or dash-separated HTTP response codes considered healthy by ELB health check")

def delete(args):
    if args.type == "ELB":
        clients.elb.delete_load_balancer(LoadBalancerName=args.elb_name)
    elif args.type == "ALB":
        elbs = clients.elbv2.describe_load_balancers(Names=[args.elb_name])["LoadBalancers"]
        assert len(elbs) == 1
        clients.elbv2.delete_load_balancer(LoadBalancerArn=elbs[0]["LoadBalancerArn"])

parser_delete = register_parser(delete, parent=elb_parser, help="Delete an ELB")

def list_load_balancers():
    elbs = paginate(clients.elb.get_paginator("describe_load_balancers"))
    albs = paginate(clients.elbv2.get_paginator("describe_load_balancers"))
    return list(elbs) + list(albs)

for parser in parser_register, parser_deregister, parser_replace, parser_create, parser_delete:
    parser.add_argument("elb_name").completer = lambda **kw: [i["LoadBalancerName"] for i in list_load_balancers()]
    parser.add_argument("--type", choices={"ELB", "ALB"}, default="ALB")
    if parser != parser_delete:
        parser.add_argument("instances", nargs="+", type=resolve_instance_id)
        parser.add_argument("--target-group", default="{elb_name}-default-tg")
        parser.add_argument("--instance-port", type=int, default=80)
