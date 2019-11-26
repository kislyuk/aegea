from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, json, io, gzip, time, socket, hashlib, uuid
import requests
from warnings import warn
from datetime import datetime, timedelta

import boto3, botocore.session
from botocore.exceptions import ClientError
from botocore.utils import parse_to_aware_datetime

from ... import logger
from .. import paginate
from ..exceptions import AegeaException
from ..compat import str
from . import clients, resources

def locate_ami(product, region=None, channel="releases", stream="released", root_store="ssd", virt="hvm"):
    """
    Examples::
        locate_ami(product="com.ubuntu.cloud:server:16.04:amd64", channel="daily", stream="daily", region="us-west-2")
        locate_ami(product="Amazon Linux AMI 2016.09")
    """
    if region is None:
        region = clients.ec2.meta.region_name
    if product.startswith("com.ubuntu.cloud"):
        partition = "aws"
        if region.startswith("cn-"):
            partition = "aws-cn"
        elif region.startswith("us-gov-"):
            partition = "aws-govcloud"
        if partition not in {"aws", "aws-cn", "aws-govcloud"}:
            raise AegeaException("Unrecognized partition {}".format(partition))
        manifest_url = "https://cloud-images.ubuntu.com/{channel}/streams/v1/com.ubuntu.cloud:{stream}:{partition}.json"
        manifest_url = manifest_url.format(partition=partition, channel=channel, stream=stream)
        manifest = requests.get(manifest_url).json()
        if product not in manifest["products"]:
            raise AegeaException("Ubuntu version {} not found in Ubuntu cloud image manifest".format(product))
        versions = manifest["products"][product]["versions"]
        for version in sorted(versions.keys(), reverse=True)[:8]:
            for ami in versions[version]["items"].values():
                if ami["crsn"] == region and ami["root_store"] == root_store and ami["virt"] == virt:
                    logger.info("Found %s for %s", ami["id"], ":".join([product, version, region, root_store, virt]))
                    return ami["id"]
    elif product.startswith("Amazon Linux"):
        filters = {"root-device-type": "ebs" if root_store == "ssd" else root_store, "virtualization-type": virt,
                   "architecture": "x86_64", "owner-alias": "amazon", "state": "available"}
        images = resources.ec2.images.filter(Filters=[dict(Name=k, Values=[v]) for k, v in filters.items()])
        for image in sorted(images, key=lambda i: i.creation_date, reverse=True):
            if root_store == "ebs" and not image.name.endswith("x86_64-gp2"):
                continue
            if image.name.startswith("amzn-ami-" + virt) and image.description.startswith(product):
                return image.image_id
    raise AegeaException("No AMI found for {} {} {} {}".format(product, region, root_store, virt))

def ensure_vpc():
    for vpc in resources.ec2.vpcs.filter(Filters=[dict(Name="isDefault", Values=["true"])]):
        break
    else:
        for vpc in resources.ec2.vpcs.all():
            break
        else:
            from ... import config
            logger.info("Creating VPC with CIDR %s", config.vpc.cidr[ARN.get_region()])
            vpc = resources.ec2.create_vpc(CidrBlock=config.vpc.cidr[ARN.get_region()])
            clients.ec2.get_waiter("vpc_available").wait(VpcIds=[vpc.id])
            add_tags(vpc, Name=__name__)
            vpc.modify_attribute(EnableDnsSupport=dict(Value=config.vpc.enable_dns_support))
            vpc.modify_attribute(EnableDnsHostnames=dict(Value=config.vpc.enable_dns_hostnames))
            internet_gateway = resources.ec2.create_internet_gateway()
            vpc.attach_internet_gateway(InternetGatewayId=internet_gateway.id)
            for route_table in vpc.route_tables.all():
                route_table.create_route(DestinationCidrBlock="0.0.0.0/0", GatewayId=internet_gateway.id)
            ensure_subnet(vpc)
    return vpc

def availability_zones():
    for az in clients.ec2.describe_availability_zones()["AvailabilityZones"]:
        yield az["ZoneName"]

def ensure_subnet(vpc, availability_zone=None):
    if availability_zone is not None and availability_zone not in availability_zones():
        msg = "Unknown availability zone {} (choose from {})"
        raise AegeaException(msg.format(availability_zone, list(availability_zones())))
    for subnet in vpc.subnets.all():
        if availability_zone is not None and subnet.availability_zone != availability_zone:
            continue
        break
    else:
        from ipaddress import ip_network
        from ... import config
        subnet_cidrs = ip_network(str(config.vpc.cidr[ARN.get_region()])).subnets(new_prefix=config.vpc.subnet_prefix)
        subnets = {}
        for az, subnet_cidr in zip(availability_zones(), subnet_cidrs):
            logger.info("Creating subnet with CIDR %s in %s, %s", subnet_cidr, vpc, az)
            subnets[az] = resources.ec2.create_subnet(VpcId=vpc.id, CidrBlock=str(subnet_cidr), AvailabilityZone=az)
            clients.ec2.get_waiter("subnet_available").wait(SubnetIds=[subnets[az].id])
            add_tags(subnets[az], Name=__name__)
            clients.ec2.modify_subnet_attribute(SubnetId=subnets[az].id,
                                                MapPublicIpOnLaunch=dict(Value=config.vpc.map_public_ip_on_launch))
        subnet = subnets[availability_zone] if availability_zone is not None else list(subnets.values())[0]
    return subnet

def ensure_ingress_rule(security_group, **kwargs):
    cidr_ip, source_security_group_id = kwargs.pop("CidrIp"), kwargs.pop("SourceSecurityGroupId")
    for rule in security_group.ip_permissions:
        ip_range_matches = any(cidr_ip == ip_range["CidrIp"] for ip_range in rule["IpRanges"])
        source_sg_matches = any(source_security_group_id == sg["GroupId"] for sg in rule["UserIdGroupPairs"])
        opts_match = all(rule.get(arg) == kwargs[arg] for arg in kwargs)
        if opts_match and (ip_range_matches or source_sg_matches):
            break
    else:
        authorize_ingress_args = dict(IpPermissions=[kwargs])
        if cidr_ip:
            authorize_ingress_args["IpPermissions"][0]["IpRanges"] = [dict(CidrIp=cidr_ip)]
        elif source_security_group_id:
            authorize_ingress_args["IpPermissions"][0]["UserIdGroupPairs"] = [dict(GroupId=source_security_group_id)]
        security_group.authorize_ingress(**authorize_ingress_args)

def resolve_security_group(name, vpc=None):
    if vpc is None:
        vpc = ensure_vpc()
    sgs = vpc.security_groups.filter(GroupNames=[name]) if vpc.is_default else vpc.security_groups.all()
    for security_group in sgs:
        if security_group.group_name == name:
            return security_group
    raise KeyError(name)

def ensure_security_group(name, vpc, tcp_ingress=[dict(port=socket.getservbyname("ssh"), cidr="0.0.0.0/0")]):
    try:
        security_group = resolve_security_group(name, vpc)
    except (ClientError, KeyError):
        logger.info("Creating security group %s for %s", name, vpc)
        security_group = vpc.create_security_group(GroupName=name, Description=name)
        for i in range(90):
            try:
                clients.ec2.describe_security_groups(GroupIds=[security_group.id])
            except ClientError:
                time.sleep(1)
    for rule in tcp_ingress:
        source_security_group_id = None
        if "source_security_group_name" in rule:
            source_security_group_id = resolve_security_group(rule["source_security_group_name"], vpc).id
        ensure_ingress_rule(security_group, IpProtocol="tcp", FromPort=rule["port"], ToPort=rule["port"],
                            CidrIp=rule.get("cidr"), SourceSecurityGroupId=source_security_group_id)
    return security_group

class S3BucketLifecycleBuilder:
    def __init__(self, **kwargs):
        self.rules = []
        self.add_rule(abort_incomplete_multipart_upload=30)
        if kwargs:
            self.add_rule(**kwargs)

    def add_rule(self, prefix="", tags=None, expiration=None, transitions=None, abort_incomplete_multipart_upload=None):
        rule = dict(ID=__name__ + "." + str(uuid.uuid4()), Status="Enabled", Filter=dict(Prefix=prefix))
        if tags:
            rule.update(Filter=dict(And=dict(Prefix=prefix, Tags=[dict(Key=k, Value=v) for k, v in tags.items()])))
        if expiration:
            rule.update(Expiration=expiration)
        if transitions:
            rule.update(Transitions=transitions)
        if abort_incomplete_multipart_upload:
            rule.update(AbortIncompleteMultipartUpload=dict(DaysAfterInitiation=abort_incomplete_multipart_upload))
        self.rules.append(rule)

    def __iter__(self):
        yield ("Rules", self.rules)

def ensure_s3_bucket(name=None, policy=None, lifecycle=None):
    if name is None:
        name = "aegea-assets-{}".format(ARN.get_account_id())
    bucket = resources.s3.Bucket(name)
    if not bucket.creation_date:
        if ARN.get_region() == "us-east-1":
            bucket.create()
        else:
            bucket.create(CreateBucketConfiguration=dict(LocationConstraint=ARN.get_region()))
    bucket.wait_until_exists()
    if policy:
        bucket.Policy().put(Policy=str(policy))
    if lifecycle:
        bucket.LifecycleConfiguration().put(LifecycleConfiguration=dict(lifecycle))
    return bucket

class ARN:
    fields = "arn partition service region account_id resource".split()
    _default_region, _default_account_id, _default_iam_username = None, None, None

    def __init__(self, arn="arn:aws::::", **kwargs):
        self.__dict__.update(dict(zip(self.fields, arn.split(":", 5)), **kwargs))
        if "region" not in kwargs and not self.region:
            self.region = self.get_region()
        if "account_id" not in kwargs and not self.account_id:
            self.account_id = self.get_account_id()

    @classmethod
    def get_region(cls):
        if cls._default_region is None:
            cls._default_region = botocore.session.Session().get_config_variable("region")
        return cls._default_region

    # TODO: for these two methods, introspect instance metadata without hanging if API not available
    @classmethod
    def get_account_id(cls):
        if cls._default_account_id is None:
            cls._default_account_id = clients.sts.get_caller_identity()["Account"]
        return cls._default_account_id

    @classmethod
    def get_iam_username(cls):
        if cls._default_iam_username is None:
            try:
                user = resources.iam.CurrentUser().user
                cls._default_iam_username = getattr(user, "name", ARN(user.arn).resource.split("/")[-1])
            except Exception as e:
                try:
                    if "Must specify userName" in str(e) or ("assumed-role" in str(e) and "botocore-session" in str(e)):
                        cur_session = boto3.Session()._session
                        src_profile = cur_session.full_config["profiles"][cur_session.profile]["source_profile"]
                        src_session = boto3.Session(profile_name=src_profile)
                        cls._default_iam_username = src_session.resource("iam").CurrentUser().user.name
                    else:
                        caller_arn = ARN(clients.sts.get_caller_identity()["Arn"])
                        cls._default_iam_username = caller_arn.resource.split("/")[-1]
                except Exception:
                    cls._default_iam_username = "unknown"
        return cls._default_iam_username

    def __str__(self):
        return ":".join(getattr(self, field) for field in self.fields)

class IAMPolicyBuilder:
    def __init__(self, *args, **kwargs):
        self.policy = dict(Version="2012-10-17", Statement=[])
        if args:
            if len(args) > 1 or not isinstance(args[0], dict):
                raise AegeaException("IAMPolicyBuilder: Expected one policy document")
            self.policy = json.loads(json.dumps(args[0]))
        if kwargs:
            self.add_statement(**kwargs)

    def contains(self, principal, action, effect, resource):
        for statement in self.policy["Statement"]:
            if "Condition" in statement or "NotAction" in statement or "NotResource" in statement:
                continue

            if statement.get("Principal") != principal or statement.get("Effect") != effect:
                continue

            if isinstance(statement.get("Action"), list):
                actions = set(action) if isinstance(action, list) else set([action])
                if not actions.issubset(statement["Action"]):
                    continue
            elif action != statement.get("Action"):
                continue

            if isinstance(statement.get("Resource"), list):
                resources = set(resource) if isinstance(resource, list) else set([resource])
                if not resources.issubset(statement["Resource"]):
                    continue
            elif resource != statement.get("Resource"):
                continue

            return True

    def add_statement(self, principal=None, action=None, effect="Allow", resource=None):
        if principal and not isinstance(principal, dict):
            principal = dict(AWS=principal)
        if self.contains(principal=principal, action=action, effect=effect, resource=resource):
            return
        statement = dict(Action=[], Effect=effect)
        if principal:
            statement["Principal"] = principal
        self.policy["Statement"].append(statement)
        if action:
            for action in (action if isinstance(action, list) else [action]):
                self.add_action(action)
        if resource:
            for resource in (resource if isinstance(resource, list) else [resource]):
                self.add_resource(resource)

    def add_action(self, action):
        self.policy["Statement"][-1]["Action"].append(action)

    def add_resource(self, resource):
        self.policy["Statement"][-1].setdefault("Resource", [])
        self.policy["Statement"][-1]["Resource"].append(resource)

    def add_assume_role_principals(self, principals):
        # See http://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_elements.html#Principal
        for principal in principals:
            if isinstance(principal, dict):
                self.add_statement(principal=principal, action="sts:AssumeRole")
            elif hasattr(principal, "arn"):
                self.add_statement(principal={"AWS": principal.arn}, action="sts:AssumeRole")
            else:
                self.add_statement(principal={"Service": principal + ".amazonaws.com"}, action="sts:AssumeRole")

    def __str__(self):
        return json.dumps(self.policy)

def ensure_iam_role(name, policies=frozenset(), trust=frozenset()):
    assume_role_policy = IAMPolicyBuilder()
    assume_role_policy.add_assume_role_principals(trust)
    role = ensure_iam_entity(name, policies=policies, collection=resources.iam.roles,
                             constructor=resources.iam.create_role, RoleName=name,
                             AssumeRolePolicyDocument=str(assume_role_policy))
    trust_policy = IAMPolicyBuilder(role.assume_role_policy_document)
    trust_policy.add_assume_role_principals(trust)
    if trust_policy.policy != role.assume_role_policy_document:
        logger.debug("Updating trust policy for %s", role)
        role.AssumeRolePolicy().update(PolicyDocument=str(trust_policy))
    return role

def ensure_iam_group(name, policies=frozenset()):
    return ensure_iam_entity(name, policies=policies, collection=resources.iam.groups,
                             constructor=resources.iam.create_group, GroupName=name)

def ensure_iam_entity(iam_entity_name, policies, collection, constructor, **constructor_args):
    for entity in collection.all():
        if entity.name == iam_entity_name:
            break
    else:
        entity = constructor(**constructor_args)
    attached_policies = [policy.arn for policy in entity.attached_policies.all()]
    for policy in policies:
        if isinstance(policy, IAMPolicyBuilder):
            entity.Policy(__name__).put(PolicyDocument=str(policy))
        else:
            policy_arn = "arn:aws:iam::aws:policy/{}".format(policy)
            if policy_arn not in attached_policies:
                entity.attach_policy(PolicyArn="arn:aws:iam::aws:policy/{}".format(policy))
    # TODO: accommodate IAM eventual consistency
    return entity

def ensure_instance_profile(iam_role_name, policies=frozenset()):
    for instance_profile in resources.iam.instance_profiles.all():
        if instance_profile.name == iam_role_name:
            break
    else:
        instance_profile = resources.iam.create_instance_profile(InstanceProfileName=iam_role_name)
        clients.iam.get_waiter("instance_profile_exists").wait(InstanceProfileName=iam_role_name)
        # IAM eventual consistency is really bad on this one
        print("Waiting for IAM instance profile to become available...")
        time.sleep(8)
    role = ensure_iam_role(iam_role_name, policies=policies, trust=["ec2"])
    if not any(r.name == iam_role_name for r in instance_profile.roles):
        instance_profile.add_role(RoleName=role.name)
    return instance_profile

def encode_tags(tags):
    if isinstance(tags, (list, tuple)):
        tags = dict(tag.split("=", 1) for tag in tags)
    return [dict(Key=k, Value=v) for k, v in tags.items()]

def decode_tags(tags):
    return {tag["Key"]: tag["Value"] for tag in tags}

def add_tags(resource, dry_run=False, **tags):
    return resource.create_tags(Tags=encode_tags(tags), DryRun=dry_run)

def filter_by_tags(collection, **tags):
    return collection.filter(Filters=[dict(Name="tag:" + k, Values=[v]) for k, v in tags.items()])

def resolve_instance_id(name):
    filter_name = "dns-name" if name.startswith("ec2") and name.endswith("compute.amazonaws.com") else "tag:Name"
    if name.startswith("i-"):
        return name
    try:
        desc = clients.ec2.describe_instances(Filters=[dict(Name=filter_name, Values=[name])])
        return desc["Reservations"][0]["Instances"][0]["InstanceId"]
    except IndexError:
        raise AegeaException('Could not resolve "{}" to a known instance'.format(name))

def get_bdm(max_devices=12, ebs_storage=frozenset()):
    # Note: d2.8xl and hs1.8xl have 24 devices
    bdm = [dict(VirtualName="ephemeral" + str(i), DeviceName="xvd" + chr(ord("b") + i)) for i in range(max_devices)]
    ebs_bdm = []
    for i, (mountpoint, size_gb) in enumerate(ebs_storage):
        ebs_spec = dict(Encrypted=True, DeleteOnTermination=True, VolumeType="st1", VolumeSize=int(size_gb))
        ebs_bdm.insert(0, dict(DeviceName="xvd" + chr(ord("z") - i), Ebs=ebs_spec))
    bdm.extend(ebs_bdm)
    return bdm

def get_metadata(path):
    res = requests.get("http://169.254.169.254/latest/meta-data/{}".format(path))
    res.raise_for_status()
    return res.content.decode()

def get_ecs_task_metadata(path="/task"):
    res = requests.get(os.environ["ECS_CONTAINER_METADATA_URI"] + path)
    res.raise_for_status()
    return res.content.decode()

def expect_error_codes(exception, *codes):
    if getattr(exception, "response", None) and exception.response.get("Error", {}).get("Code", {}) not in codes:
        raise

def resolve_ami(ami=None, **tags):
    """
    Find an AMI by ID, name, or tags.
    - If an ID is given, it is returned with no validation; otherwise, selects the most recent AMI from:
    - All available AMIs in this account with the Owner tag equal to this user's IAM username (filtered by tags given);
    - If no AMIs found, all available AMIs in this account with the AegeaVersion tag present (filtered by tags given);
    - If no AMIs found, all available AMIs in this account (filtered by tags given).
    Return the AMI with the most recent creation date.
    """
    if ami is None or not ami.startswith("ami-"):
        if ami is None:
            filters = dict(Owners=["self"], Filters=[dict(Name="state", Values=["available"])])
        else:
            filters = dict(Owners=["self"], Filters=[dict(Name="name", Values=[ami])])
        all_amis = resources.ec2.images.filter(**filters)
        if tags:
            all_amis = filter_by_tags(all_amis, **tags)

        current_user_amis = all_amis.filter(Filters=[dict(Name="tag:Owner", Values=[ARN.get_iam_username()])])
        amis = sorted(current_user_amis, key=lambda x: x.creation_date)
        if len(amis) == 0:
            aegea_amis = all_amis.filter(Filters=[dict(Name="tag-key", Values=["AegeaVersion"])])
            amis = sorted(aegea_amis, key=lambda x: x.creation_date)
            if len(amis) == 0:
                amis = sorted(all_amis, key=lambda x: x.creation_date)
        if not amis:
            raise AegeaException("Could not resolve AMI {}".format(dict(tags, ami=ami)))
        ami = amis[-1].id
    return ami

offers_api = "https://pricing.us-east-1.amazonaws.com/offers/v1.0"

def region_name(region_id):
    region_names, region_ids = {}, {}
    from botocore import loaders
    for partition_data in loaders.create_loader().load_data("endpoints")["partitions"]:
        region_names.update({k: v["description"] for k, v in partition_data["regions"].items()})
        region_ids.update({v: k for k, v in region_names.items()})
    return region_names[region_id]

def get_pricing_data(service_code, filters=None, max_cache_age_days=30):
    from ... import config

    if filters is None:
        filters = [("location", region_name(clients.ec2.meta.region_name))]

    get_products_args = dict(ServiceCode=service_code,
                             Filters=[dict(Type="TERM_MATCH", Field=k, Value=v) for k, v in filters])
    cache_key = hashlib.sha256(json.dumps(get_products_args, sort_keys=True).encode()).hexdigest()[:32]
    service_code_filename = os.path.join(config.user_config_dir, "pricing_cache_{}.json.gz".format(cache_key))
    try:
        cache_date = datetime.fromtimestamp(os.path.getmtime(service_code_filename))
        if cache_date < datetime.now() - timedelta(days=max_cache_age_days):
            raise Exception("Cache is too old, discard")
        with gzip.open(service_code_filename) as gz_fh:
            with io.BufferedReader(gz_fh) as buf_fh:
                pricing_data = json.loads(buf_fh.read().decode())
    except Exception:
        logger.info("Fetching pricing data for %s", service_code)
        client = boto3.client("pricing", region_name="us-east-1")
        pricing_data = [json.loads(p) for p in paginate(client.get_paginator("get_products"), **get_products_args)]
        try:
            with gzip.open(service_code_filename, "w") as fh:
                fh.write(json.dumps(pricing_data).encode())
        except Exception as e:
            print(e, file=sys.stderr)
    return pricing_data

def get_products(service_code, region=None, filters=None, terms=None, max_cache_age_days=30):
    from ... import config

    if region is None:
        region = clients.ec2.meta.region_name
    if terms is None:
        terms = ["OnDemand"]
    if filters is None:
        filters = [("location", region_name(clients.ec2.meta.region_name))]
        filters += getattr(config.pricing, "filters_" + service_code, [])
    pricing_data = get_pricing_data(service_code, filters=filters, max_cache_age_days=max_cache_age_days)
    for product in pricing_data:
        product.update(product["product"].pop("attributes"))
        for term_name, term_value in product.pop("terms").items():
            if term_name not in terms:
                continue
            term = list(term_value.values())[0]
            for price_dimension in term["priceDimensions"].values():
                yield dict(dict(product, **term["termAttributes"]), **price_dimension)

def get_ondemand_price_usd(region, instance_type, **kwargs):
    from ... import config

    filters = [("location", region_name(region)), ("InstanceType", instance_type)] + config.pricing.filters_AmazonEC2
    for product in get_products("AmazonEC2", region=region, filters=filters, **kwargs):
        if float(product["pricePerUnit"]["USD"]) == 0:
            continue
        return product["pricePerUnit"]["USD"]

def get_iam_role_for_instance(instance):
    instance = resources.ec2.Instance(resolve_instance_id(instance))
    profile = resources.iam.InstanceProfile(ARN(instance.iam_instance_profile["Arn"]).resource.split("/")[1])
    assert len(profile.roles) <= 1
    return profile.roles[0] if profile.roles else None

def ensure_iam_policy(name, doc):
    try:
        return resources.iam.create_policy(PolicyName=name, PolicyDocument=str(doc))
    except ClientError as e:
        expect_error_codes(e, "EntityAlreadyExists")
        policy = resources.iam.Policy(str(ARN(service="iam", region="", resource="policy/" + name)))
        policy.create_version(PolicyDocument=str(doc), SetAsDefault=True)
        for version in policy.versions.all():
            if not version.is_default_version:
                version.delete()
        return policy

def get_elb_dns_aliases():
    dns_aliases = {}
    for zone in paginate(clients.route53.get_paginator("list_hosted_zones")):
        for rrs in paginate(clients.route53.get_paginator("list_resource_record_sets"), HostedZoneId=zone["Id"]):
            for record in rrs.get("ResourceRecords", [rrs.get("AliasTarget", {})]):
                value = record.get("Value", record.get("DNSName"))
                if value.endswith("elb.amazonaws.com") or value.endswith("elb.amazonaws.com."):
                    dns_aliases[value.rstrip(".").replace("dualstack.", "")] = rrs["Name"]
    return dns_aliases

ip_ranges_api = "https://ip-ranges.amazonaws.com/ip-ranges.json"

def get_public_ip_ranges(service="AMAZON", region=None):
    if region is None:
        region = ARN.get_region()
    ranges = requests.get(ip_ranges_api).json()["prefixes"]
    return [r for r in ranges if r["service"] == service and r["region"] == region]

def make_waiter(op, path, expected, matcher="path", delay=1, max_attempts=30):
    from botocore.waiter import Waiter, SingleWaiterConfig
    acceptor = dict(matcher=matcher, argument=path, expected=expected, state="success")
    waiter_cfg = dict(operation=op.__name__, delay=delay, maxAttempts=max_attempts, acceptors=[acceptor])
    return Waiter(op.__name__, SingleWaiterConfig(waiter_cfg), op)

def resolve_log_group(name):
    for log_group in paginate(clients.logs.get_paginator("describe_log_groups"), logGroupNamePrefix=name):
        if log_group["logGroupName"] == name:
            return log_group
    else:
        raise AegeaException("Log group {} not found".format(name))

def ensure_log_group(name):
    try:
        return resolve_log_group(name)
    except AegeaException:
        try:
            clients.logs.create_log_group(logGroupName=name)
        except clients.logs.exceptions.ResourceAlreadyExistsException:
            pass
        return resolve_log_group(name)

def get_cloudwatch_metric_stats(namespace, name, start_time=None, end_time=None, period=None, statistic="Average",
                                resource=None, **kwargs):
    start_time = datetime.utcnow() - period * 60 if start_time is None else start_time
    end_time = datetime.utcnow() if end_time is None else end_time
    cloudwatch = resources.cloudwatch if resource is None else resource
    metric = cloudwatch.Metric(namespace, name)
    get_stats_args = dict(StartTime=start_time, EndTime=end_time, Statistics=[statistic],
                          Dimensions=[dict(Name=k, Value=v) for k, v in kwargs.items()])
    if period is not None:
        get_stats_args.update(Period=period)
    return metric.get_statistics(**get_stats_args)

def instance_type_completer(max_cache_age_days=30, **kwargs):
    return [p["instanceType"] for p in get_products("AmazonEC2")]

instance_storage_shellcode = """
aegea_bd=(/dev/disk/by-id/nvme-Amazon_EC2_NVMe_Instance_Storage_AWS?????????????????)
if [ ! -e /dev/md0 ]; then mdadm --create /dev/md0 --force --auto=yes --level=0 --chunk=256 --raid-devices=${{#aegea_bd[@]}} ${{aegea_bd[@]}}; {mkfs} /dev/md0; fi
mount -L aegveph {mountpoint}
"""  # noqa
