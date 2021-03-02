"""
Manage AWS Elastic Container Registry (ECR) resources.

Use ``aws ecr create-repository`` and ``aws ecr delete-repository`` to manage ECR repositories.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import json
from typing import Dict, List

from .ls import register_parser, register_listing_parser
from .util import paginate
from .util.printing import page_output, tabulate
from .util.aws import clients, AegeaException, ARN

def ecr(args):
    ecr_parser.print_help()

ecr_parser = register_parser(ecr, help="Manage Elastic Container Registry resources", description=__doc__)

def ls(args):
    table = []  # type: List[Dict]
    describe_repositories_args = dict(repositoryNames=args.repositories) if args.repositories else {}
    for repo in paginate(clients.ecr.get_paginator("describe_repositories"), **describe_repositories_args):
        try:
            res = clients.ecr.get_repository_policy(repositoryName=repo["repositoryName"])
            repo["policy"] = json.loads(res["policyText"])
        except clients.ecr.exceptions.RepositoryPolicyNotFoundException:
            pass
        orig_len = len(table)
        for image in paginate(clients.ecr.get_paginator("describe_images"), repositoryName=repo["repositoryName"]):
            table.append(dict(image, **repo))
        if len(table) == orig_len:
            table.append(repo)
    table = sorted(table, key=lambda r: r["repositoryName"] + str(r.get("imagePushedAt")))
    page_output(tabulate(table, args))

ls_parser = register_listing_parser(ls, parent=ecr_parser, help="List ECR repos and images")
ls_parser.add_argument("repositories", nargs="*")

def ecr_image_name_completer(**kwargs):
    return (r["repositoryName"] for r in paginate(clients.ecr.get_paginator("describe_repositories")))

def retag(args):
    if "dkr.ecr" in args.repository and "amazonaws.com" in args.repository:
        if not args.repository.startswith("{}.dkr.ecr.{}.amazonaws.com/".format(ARN.get_account_id(),
                                                                                clients.ecr.meta.region_name)):
            raise AegeaException("Unexpected repository ID {}".format(args.repository))
        args.repository = args.repository.split("/", 1)[1]
    image_id_key = "imageDigest" if len(args.existing_tag_or_digest) == 64 else "imageTag"
    batch_get_image_args = dict(repositoryName=args.repository, imageIds=[{image_id_key: args.existing_tag_or_digest}])
    for image in clients.ecr.batch_get_image(**batch_get_image_args)["images"]:
        if "imageManifest" in image:
            break
    else:
        raise AegeaException("No image found for tag or digest {}".format(args.existing_tag_or_digest))
    return clients.ecr.put_image(repositoryName=args.repository,
                                 imageManifest=image["imageManifest"],
                                 imageTag=args.new_tag)

retag_parser = register_parser(retag, parent=ecr_parser, help="Add a new tag to an existing image")
retag_parser.add_argument("repository").completer = ecr_image_name_completer
retag_parser.add_argument("existing_tag_or_digest", help="Tag or digest of an existing image in the registry")
retag_parser.add_argument("new_tag", help="Tag to apply to existing image")
