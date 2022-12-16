import json
import time
from typing import Any, Dict, List

from botocore.exceptions import ClientError

from ... import logger
from ..exceptions import AegeaException
from . import ARN, clients, expect_error_codes, resources


class IAMPolicyBuilder:
    def __init__(self, *args, **kwargs):
        self.policy = dict(Version="2012-10-17", Statement=[])  # type: Dict[str, Any]
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
            try:
                assert entity.Policy(__name__).policy_document == policy.policy
            except Exception:
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

def compose_managed_policies(policy_names):
    policy = IAMPolicyBuilder()
    for policy_name in policy_names:
        doc = resources.iam.Policy(arn="arn:aws:iam::aws:policy/" + policy_name).default_version.document
        for i, statement in enumerate(doc["Statement"]):
            policy.policy["Statement"].append(statement)
            policy.policy["Statement"][-1]["Sid"] = policy_name + str(i)
    return policy

def ensure_fargate_execution_role(name):
    return ensure_iam_role(name, trust=["ecs-tasks"],
                           policies=["service-role/AmazonEC2ContainerServiceforEC2Role",
                                     "service-role/AWSBatchServiceRole"])
