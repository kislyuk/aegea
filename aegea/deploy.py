"""Manage deployments from a GitHub repository.

Aegea deploy is a deployment manager daemon. It uses an SNS-SQS bus
to notify instances about updates to a GitHub repo. When
an update is detected, the deploy daemon pulls a new copy of the repo,
builds it, swaps it with the existing copy, and restarts your service.

To set up your AWS account for aegea deploy to track a particular
repo, run ``aegea deploy configure <repo URL>``. This will create an
SNS queue for notifications about the repo, an IAM user dedicated to
writing messages to this queue, and a GitHub post-commit hook to send
messages to the queue using the user's IAM credentials.

To set up instances to track deployments, create a systemd service
symlink like so:

    cd /etc/systemd/system/multi-user.target.wants
    ln -s /lib/systemd/system/aegea-deploy@.service aegea-deploy@<owner>-<repo>-<branch>.service

Replace <owner>, <repo>, and <branch> with your GitHub user or org name,
repo name, and branch to deploy from.

The instance using aegea deploy must have permissions to access the
SNS-SQS bus and the S3 bucket containing Aegea secrets (see ``aegea
secrets --help`` for more information). Run ``aegea deploy grant`` to
grant appropriate permissions to an IAM role or instance.

Any updates to the branch will trigger a rebuild. By
default, the build location is /opt/<owner>/<repo>. Each update is
pulled and built in a separate timestamped subdirectory by running
``make`` in the repo root, and symlinked upon success. Once the update
is successfully built, the daemon will run ``make reload`` in the repo
root to reload any services the app needs to run.

You can also manually trigger a rebuild with a service reload:

    service aegea-deploy@<owner>-<repo>-<branch> reload

"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, json, argparse
from datetime import datetime, timedelta
from dateutil.tz import tzutc
from botocore.exceptions import ClientError

from . import register_parser, logger, secrets
from .util.git import parse_repo_name, get_repo, private_submodules
from .util.crypto import key_fingerprint
from .util.printing import format_table, page_output, get_field, get_cell, tabulate, BOLD
from .util.compat import lru_cache
from .util.aws import (ARN, resources, clients, IAMPolicyBuilder, resolve_instance_id, get_iam_role_for_instance,
                       expect_error_codes, ensure_iam_policy)

def deploy(args):
    deploy_parser.print_help()

deploy_parser = register_parser(deploy, help="Manage deployments of GitHub repositories", description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)

def configure(args):
    repo = get_repo(args.repo)
    gh_owner_name, gh_repo_name = parse_repo_name(args.repo)
    iam_user_name = __name__ + "-github-event-relay"
    try:
        user = resources.iam.User(iam_user_name)
        user.load()
    except ClientError:
        user = resources.iam.create_user(UserName=iam_user_name)

    topic = resources.sns.create_topic(Name="github-{}-{}-events".format(gh_owner_name, gh_repo_name))
    policy = IAMPolicyBuilder(action="sns:Publish", resource=topic.arn)
    user.create_policy(PolicyName="sns_send_message", PolicyDocument=str(policy))

    for key in user.access_keys.all():
        key.delete()
    key = user.create_access_key_pair()
    repo.create_hook("amazonsns", dict(sns_topic=topic.arn,
                                       sns_region=ARN(topic.arn).region,
                                       aws_key=key.id,
                                       aws_secret=key.secret))
    logger.info("Created SNS topic %s and GitHub hook for repo %s", topic, repo)
    status_bucket = resources.s3.create_bucket(Bucket="deploy-status-" + ARN(topic.arn).account_id)
    logger.info("Created %s", status_bucket)
    grant(args)
    return dict(topic_arn=topic.arn)

parser = register_parser(configure, parent=deploy_parser)
parser.add_argument("repo", help="URL of GitHub repo, e.g. git@github.com:kislyuk/aegea.git")
parser.add_argument("iam_role_or_instance")

def ls(args):
    """
    List status of all configured SNS-SQS message buses and instances subscribed to them.
    """
    table = []
    queues = list(resources.sqs.queues.filter(QueueNamePrefix="github"))
    max_age = datetime.now(tzutc()) - timedelta(days=15)
    for topic in resources.sns.topics.all():
        account_id = ARN(topic.arn).account_id
        try:
            bucket = resources.s3.Bucket("deploy-status-{}".format(account_id))
            status_objects = bucket.objects.filter(Prefix=ARN(topic.arn).resource)
            recent_status_objects = {o.key: o for o in status_objects if o.last_modified > max_age}
        except ClientError:
            continue
        if ARN(topic.arn).resource.startswith("github"):
            for queue in queues:
                queue_name = os.path.basename(queue.url)
                if queue_name.startswith(ARN(topic.arn).resource):
                    row = dict(Topic=topic, Queue=queue)
                    status_object = bucket.Object(os.path.join(queue_name, "status"))
                    if status_object.key not in recent_status_objects:
                        continue
                    try:
                        github, owner, repo, events, instance = os.path.dirname(status_object.key).split("-", 4)
                        status = json.loads(status_object.get()["Body"].read().decode("utf-8"))
                        row.update(status, Owner=owner, Repo=repo, Instance=instance,
                                   Updated=status_object.last_modified)
                    except Exception:
                        pass
                    table.append(row)
    args.columns = ["Owner", "Repo", "Instance", "Status", "Ref", "Commit", "Updated", "Topic", "Queue"]
    page_output(tabulate(table, args))

parser = register_parser(ls, parent=deploy_parser)

def ensure_deploy_iam_policy():
    sqs_arn = ARN(service="sqs", region="*", resource="github-*")
    policy_doc = IAMPolicyBuilder(action="sqs:*", resource=str(sqs_arn))
    sns_arn = ARN(service="sns", resource="github-*")
    policy_doc.add_statement(action="sns:Subscribe", resource=str(sns_arn))
    s3_arn = ARN(service="s3", region="", account_id="", resource="deploy-status-{}/*".format(ARN.get_account_id()))
    policy_doc.add_statement(action="s3:PutObject", resource=str(s3_arn))
    return ensure_iam_policy(__name__, policy_doc)

def grant(args):
    """
    Given an IAM role or instance name, attach an IAM policy granting
    appropriate permissions to subscribe to deployments. Given a
    GitHub repo URL, create and record deployment keys for the repo
    and any of its private submodules, making the keys accessible to
    the IAM role.
    """
    try:
        role = resources.iam.Role(args.iam_role_or_instance)
        role.load()
    except ClientError:
        role = get_iam_role_for_instance(args.iam_role_or_instance)
    role.attach_policy(PolicyArn=ensure_deploy_iam_policy().arn)
    for private_repo in [args.repo] + list(private_submodules(args.repo)):
        gh_owner_name, gh_repo_name = parse_repo_name(private_repo)
        secret = secrets.put(argparse.Namespace(secret_name="deploy.{}.{}".format(gh_owner_name, gh_repo_name),
                                                iam_role=role.name,
                                                instance_profile=None,
                                                iam_group=None,
                                                iam_user=None,
                                                generate_ssh_key=True))
        get_repo(private_repo).create_key(role.name, secret["ssh_public_key"])
        logger.info("Created deploy key %s for IAM role %s to access GitHub repo %s",
                    secret["ssh_key_fingerprint"], role.name, private_repo)

parser = register_parser(grant, parent=deploy_parser)
parser.add_argument("repo", help="URL of GitHub repo, e.g. git@github.com:kislyuk/aegea.git")
parser.add_argument("iam_role_or_instance")
