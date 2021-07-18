from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, json, time, base64, argparse, io, gzip, tempfile
from collections import OrderedDict

from botocore.exceptions import ClientError

from . import register_parser, logger, config, __version__
from .util.aws import ARN, clients, resources, expect_error_codes
from .util.aws.iam import ensure_iam_role, IAMPolicyBuilder
from .util.aws.batch import bash_cmd_preamble
from .util.cloudinit import get_bootstrap_files, get_rootfs_skel_dirs, encode_cloud_config_payload
from .batch import submit, submit_parser

dockerfile = """
FROM {base_image}
MAINTAINER {maintainer}
LABEL {label}
ENV CLOUD_CONFIG_B64 {cloud_config_b64}
RUN {run}
"""

def get_dockerfile(args):
    if args.dockerfile:
        return io.open(args.dockerfile, "rb").read()
    else:
        cmd = bash_cmd_preamble + [
            "apt-get update -qq",
            "apt-get install -qqy cloud-init net-tools",
            "echo $CLOUD_CONFIG_B64 | base64 --decode > /etc/cloud/cloud.cfg.d/99_aegea.cfg",
            "cloud-init init",
            "cloud-init modules --mode=config",
            "cloud-init modules --mode=final"
        ]
        return dockerfile.format(base_image=args.base_image,
                                 maintainer=ARN.get_iam_username(),
                                 label=" ".join(args.tags),
                                 cloud_config_b64=base64.b64encode(get_cloud_config(args)).decode(),
                                 run=json.dumps(cmd)).encode()

def encode_dockerfile(args):
    with io.BytesIO() as buf:
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(get_dockerfile(args))
            gz.close()
        return base64.b64encode(buf.getvalue()).decode()

def get_cloud_config(args):
    cloud_config_data = OrderedDict(packages=args.packages,
                                    write_files=get_bootstrap_files(get_rootfs_skel_dirs(args)),
                                    runcmd=args.commands)
    cloud_config_data.update(dict(args.cloud_config_data))
    cloud_cfg_d = {
        "datasource_list": ["None"],
        "datasource": {
            "None": {
                "userdata_raw": encode_cloud_config_payload(cloud_config_data, gzip=False)
            }
        }
    }
    return json.dumps(cloud_cfg_d).encode()

def ensure_ecr_repo(name, read_access=None):
    try:
        clients.ecr.create_repository(repositoryName=name)
    except clients.ecr.exceptions.RepositoryAlreadyExistsException:
        pass
    policy = IAMPolicyBuilder(principal=dict(AWS=read_access),
                              action=["ecr:GetDownloadUrlForLayer",
                                      "ecr:BatchGetImage",
                                      "ecr:BatchCheckLayerAvailability"])
    if read_access:
        clients.ecr.set_repository_policy(repositoryName=name, policyText=str(policy))

build_docker_image_shellcode = """#!/bin/bash
set -euo pipefail
apt-get update -qq
apt-get -qq install -y --no-install-suggests --no-install-recommends docker.io python3-pip python3-wheel python3-setuptools
pip3 install -q awscli
cd $(mktemp -d)
aws configure set default.region $AWS_DEFAULT_REGION
$(aws ecr get-login --no-include-email)
DOCKERFILE_B64GZ="{dockerfile}"
echo "$DOCKERFILE_B64GZ" | base64 --decode | gunzip > Dockerfile
TAG="${{AWS_ACCOUNT_ID}}.dkr.ecr.${{AWS_DEFAULT_REGION}}.amazonaws.com/${{REPO}}:${{TAG}}"
CACHE_FROM=""
if {use_cache}; then docker pull "$TAG" && CACHE_FROM="--cache-from $TAG"; fi
docker build $CACHE_FROM -t "$TAG" .
docker push "$TAG"
"""  # noqa
def build_docker_image(args):
    for key, value in config.build_image.items():
        getattr(args, key).extend(value)
    args.tags += ["AegeaVersion={}".format(__version__),
                  'description="Built by {} for {}"'.format(__name__, ARN.get_iam_username())]
    ensure_ecr_repo(args.name, read_access=args.read_access)
    with tempfile.NamedTemporaryFile(mode="wt") as exec_fh:
        exec_fh.write(build_docker_image_shellcode.format(dockerfile=encode_dockerfile(args),
                                                          use_cache=json.dumps(args.use_cache)))
        exec_fh.flush()
        submit_args = submit_parser.parse_args(["--execute", exec_fh.name])
        submit_args.volumes = [["/var/run/docker.sock", "/var/run/docker.sock"]]
        submit_args.privileged = True
        submit_args.watch = True
        submit_args.dry_run = args.dry_run
        submit_args.image = args.builder_image
        submit_args.environment = [
            dict(name="TAG", value=args.tag),
            dict(name="REPO", value=args.name),
            dict(name="AWS_DEFAULT_REGION", value=ARN.get_region()),
            dict(name="AWS_ACCOUNT_ID", value=ARN.get_account_id())
        ]
        builder_iam_role = ensure_iam_role(__name__, trust=["ecs-tasks"], policies=args.builder_iam_policies)
        submit_args.job_role = builder_iam_role.name
        job = submit(submit_args)
    return dict(job=job)

parser = register_parser(build_docker_image, help="Build an Elastic Container Registry Docker image")
parser.add_argument("name")
parser.add_argument("--tag", help="Docker image tag", default="latest")
parser.add_argument("--read-access", nargs="*",
                    help="AWS account IDs or IAM principal ARNs to grant read access. Use '*' to grant to all.")
parser.add_argument("--builder-image", default="ubuntu:18.04", help=argparse.SUPPRESS)
parser.add_argument("--builder-iam-policies", nargs="+",
                    default=["AmazonEC2FullAccess", "AmazonS3FullAccess", "AmazonEC2ContainerRegistryPowerUser"])
parser.add_argument("--tags", nargs="+", default=[], metavar="NAME=VALUE", help="Tag resulting image with these tags")
parser.add_argument("--cloud-config-data", type=json.loads)
parser.add_argument("--dockerfile")
parser.add_argument("--no-cache", dest="use_cache", action="store_false",
                    help="Build image from scratch without re-using layers from build steps of prior versions")
parser.add_argument("--dry-run", action="store_true", help="Gather arguments and stop short of building the image")
