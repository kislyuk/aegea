"""
Manage IAM users, groups, roles, and policies
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, argparse, collections, random, string

import botocore

from . import config, logger
from .ls import register_parser, register_listing_parser
from .util.printing import page_output, tabulate, BOLD
from .util.aws import resources, clients
from .util.aws.iam import ensure_iam_group, IAMPolicyBuilder

def iam(args):
    iam_parser.print_help()

iam_parser = register_parser(iam)

def configure(args):
    for group, policies in config.managed_iam_groups.items():
        print("Creating group", group)
        formatted_policies = [(IAMPolicyBuilder(**p) if isinstance(p, collections.Mapping) else p) for p in policies]
        ensure_iam_group(group, policies=formatted_policies)
        msg = 'Created group {g}. Use the AWS console or "aws iam add-user-to-group --user-name USER --group-name {g}" to add users to it.' # noqa
        print(BOLD(msg.format(g=group)))

parser_configure = register_parser(configure, parent=iam_parser, help="Set up aegea-specific IAM groups and policies")

def get_policies_for_principal(cell, row):
    try:
        policies = [p.policy_name for p in row.policies.all()] + [p.policy_name for p in row.attached_policies.all()]
        return ", ".join(policies)
    except botocore.exceptions.ClientError as e:
        if getattr(e, "response", None) and e.response.get("Error", {}).get("Code", {}) == "AccessDenied":
            return "[Access denied]"
        raise

def users(args):
    try:
        current_user_id = resources.iam.CurrentUser().user_id
    except botocore.exceptions.ClientError:
        current_user_id = None

    def mark_cur_user(cell, row):
        return ">>>" if row.user_id == current_user_id else ""

    def describe_mfa(cell, row):
        try:
            return "Enabled" if list(row.mfa_devices.all()) else "Disabled"
        except botocore.exceptions.ClientError:
            return "Unknown"

    def describe_access_keys(cell):
        return ", ".join([k.id + ": " + k.status for k in cell.all()])

    users = list(resources.iam.users.all())
    for user in users:
        user.cur, user.mfa = "", ""
    cell_transforms = {
        "cur": mark_cur_user,
        "policies": get_policies_for_principal,
        "mfa": describe_mfa,
        "access_keys": describe_access_keys
    }
    page_output(tabulate(users, args, cell_transforms=cell_transforms))

parser = register_listing_parser(users, parent=iam_parser, help="List IAM users")

def groups(args):
    page_output(tabulate(resources.iam.groups.all(), args, cell_transforms={"policies": get_policies_for_principal}))

parser = register_listing_parser(groups, parent=iam_parser, help="List IAM groups")

def roles(args):
    page_output(tabulate(resources.iam.roles.all(), args, cell_transforms={"policies": get_policies_for_principal}))

parser = register_listing_parser(roles, parent=iam_parser, help="List IAM roles")

def policies(args):
    page_output(tabulate(resources.iam.policies.all(), args))

parser = register_listing_parser(policies, parent=iam_parser, help="List IAM policies")

def generate_password(length=16):
    while True:
        password = [random.SystemRandom().choice(string.ascii_letters + string.digits) for _ in range(length)]
        password.insert(8, "-")
        if not any(c in string.ascii_uppercase for c in password):
            continue
        if not any(c in string.ascii_lowercase for c in password):
            continue
        if not any(c in string.digits for c in password):
            continue
        return ''.join(password)

def create_user(args):
    if args.prompt_for_password:
        from getpass import getpass
        args.password = getpass(prompt="Password for IAM user {}:".format(args.username))
    else:
        args.password = generate_password()
    try:
        user = resources.iam.create_user(UserName=args.username)
        clients.iam.get_waiter('user_exists').wait(UserName=args.username)
        logger.info("Created new IAM user %s", user)
        print(BOLD("Generated new password for IAM user {}: {}".format(args.username, args.password)))
    except resources.iam.meta.client.exceptions.EntityAlreadyExistsException:
        user = resources.iam.User(args.username)
        logger.info("Updating existing IAM user %s", user)
    try:
        user.create_login_profile(UserName=user.name, Password=args.password, PasswordResetRequired=True)
    except resources.iam.meta.client.exceptions.EntityAlreadyExistsException:
        if args.reset_password:
            clients.iam.update_login_profile(UserName=user.name, Password=args.password, PasswordResetRequired=True)
            print(BOLD("Generated reset password for IAM user {}: {}".format(args.username, args.password)))
    for group in args.groups:
        try:
            group = resources.iam.create_group(GroupName=group)
            logger.info("Created new IAM group %s", group)
        except resources.iam.meta.client.exceptions.EntityAlreadyExistsException:
            group = resources.iam.Group(group)
        user.add_group(GroupName=group.name)
        logger.info("Added %s to %s", user, group)

parser = register_listing_parser(create_user, parent=iam_parser, help="Create a new IAM user")
parser.add_argument("username")
parser.add_argument("--reset-password", action="store_true")
parser.add_argument("--prompt-for-password",
                    help="Display an interactive prompt for new user password instead of autogenerating")
parser.add_argument("--groups", nargs="*", default=[], help="IAM groups to add the user to")
