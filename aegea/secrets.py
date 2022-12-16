"""
Manage secrets (credentials) using AWS Secrets Manager.

Secrets are credentials (private SSH keys, API keys, passwords, etc.)  for use
by services that run in your AWS account. This utility does not manage AWS
credentials, since the AWS IAM API provides a way to do so through IAM roles,
instance profiles, and instance metadata. Instead, instance role credentials are
used as primary credentials to access any other credentials needed by your
services.

Upload and manage credentials with ``aegea secrets``. On an EC2 instance, read
credentials with ``aws secretsmanager get-secret-value --secret-id SECRETNAME``.
Once you retrieve a secret, try to avoid saving it on the filesystem or passing
it in process arguments. Instead, try passing it as an environment variable
value or on process standard input.

For more information about credential storage best practices, see
http://docs.aws.amazon.com/general/latest/gr/aws-access-keys-best-practices.html
and https://www.vaultproject.io/.

Examples
========
Using ``aegea secrets`` to generate and save an SSH key pair accessible by instances launched by ``aegea launch``::

    aegea secrets put deploy.foo.bar --generate-ssh-key --iam-role aegea.launch > secrets.out.json
    jq --raw-output .ssh_public_key < secrets.out.json > deploy.foo.bar.pub

    eval $(ssh-agent -s)
    aws secretsmanager get-secret-value --secret-id deploy.bitbucket.my-private-repo | ssh-add /dev/stdin
    git clone git@bitbucket.org:my-org/my-private-repo.git

Using ``aegea secrets`` to save an API key (password) accessible by the IAM group ``space_marines``::

    RAILGUN_PASSWORD=passw0rd aegea secrets put RAILGUN_PASSWORD --iam-group space_marines

"""

import argparse
import copy
import io
import json
import os
import subprocess
import sys

from botocore.exceptions import ClientError
from botocore.paginate import Paginator

from . import logger
from .ls import register_listing_parser, register_parser
from .util import paginate
from .util.aws import ARN, clients, expect_error_codes, resources
from .util.aws.iam import IAMPolicyBuilder, ensure_iam_policy
from .util.crypto import hostkey_line, key_fingerprint, new_ssh_key
from .util.exceptions import AegeaException
from .util.printing import page_output, tabulate


def parse_principal(args):
    if args.instance_profile:
        return resources.iam.Role(resources.iam.InstanceProfile(args.instance_profile).roles[0])
    elif args.iam_role:
        return resources.iam.Role(args.iam_role)
    elif args.iam_group:
        return resources.iam.Group(args.iam_group)
    elif args.iam_user:
        return resources.iam.User(args.iam_user)
    else:
        logger.warn('You did not specify anyone to grant access to this secret. '
                    'You can specify a principal with "--instance-profile" or "--iam-{role,user,group}".')

def ensure_policy(principal, secret_arn):
    policy_name = "{}.{}.{}".format(__name__,
                                    ARN(principal.arn).resource.replace("/", "."),
                                    ARN(secret_arn).resource.split(":")[1].replace("/", "."))
    policy_doc = IAMPolicyBuilder(action="secretsmanager:GetSecretValue", resource=secret_arn)
    policy = ensure_iam_policy(policy_name, policy_doc)
    principal.attach_policy(PolicyArn=policy.arn)

def secrets(args):
    secrets_parser.print_help()

secrets_parser = register_parser(secrets, help="Manage application credentials (secrets)", description=__doc__)

def ls(args):
    list_secrets_paginator = Paginator(method=clients.secretsmanager.list_secrets,
                                       pagination_config=dict(result_key="SecretList",
                                                              input_token="NextToken",
                                                              output_token="NextToken",
                                                              limit_key="MaxResults"),
                                       model=None)
    page_output(tabulate(paginate(list_secrets_paginator), args))

ls_parser = register_listing_parser(ls, parent=secrets_parser)

def put(args):
    if args.generate_ssh_key:
        ssh_key = new_ssh_key()
        buf = io.StringIO()
        ssh_key.write_private_key(buf)
        secret_value = buf.getvalue()
    elif args.secret_name in os.environ:
        secret_value = os.environ[args.secret_name]
    else:
        secret_value = sys.stdin.read()
    try:
        res = clients.secretsmanager.create_secret(Name=args.secret_name, SecretString=secret_value)
    except clients.secretsmanager.exceptions.ResourceExistsException:
        res = clients.secretsmanager.put_secret_value(SecretId=args.secret_name, SecretString=secret_value)
    if parse_principal(args):
        ensure_policy(parse_principal(args), res["ARN"])
    if args.generate_ssh_key:
        return dict(ssh_public_key=hostkey_line(hostnames=[], key=ssh_key).strip(),
                    ssh_key_fingerprint=key_fingerprint(ssh_key))

put_parser = register_parser(put, parent=secrets_parser)
put_parser.add_argument("--generate-ssh-key", action="store_true",
                        help="Generate a new SSH key pair and write the private key as the secret value; write the public key to stdout")  # noqa

def get(args):
    sys.stdout.write(clients.secretsmanager.get_secret_value(SecretId=args.secret_name)["SecretString"])

get_parser = register_parser(get, parent=secrets_parser)

def delete(args):
    return clients.secretsmanager.delete_secret(SecretId=args.secret_name)

delete_parser = register_parser(delete, parent=secrets_parser)

for parser in put_parser, get_parser, delete_parser:
    parser.add_argument("secret_name",
                        help="List the secret name. For put, pass the secret value on stdin, or via an environment variable with the same name as the secret.")  # noqa
    parser.add_argument("--instance-profile")
    parser.add_argument("--iam-role")
    parser.add_argument("--iam-group")
    parser.add_argument("--iam-user",
                        help="Name of IAM instance profile, role, group, or user who will be granted access to secret")
