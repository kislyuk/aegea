"""
Manage AWS Lambda functions and their event sources
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, argparse, collections, random, string, hashlib, base64

from . import config, logger
from .ls import register_parser, register_listing_parser
from .util import Timestamp, paginate
from .util.printing import page_output, tabulate
from .util.aws import resources, clients

def _lambda(args):
    lambda_parser.print_help()

lambda_parser = register_parser(_lambda, name="lambda")

def ls(args):
    paginator = getattr(clients, "lambda").get_paginator("list_functions")
    page_output(tabulate(paginate(paginator), args, cell_transforms={"LastModified": Timestamp}))

parser_ls = register_listing_parser(ls, parent=lambda_parser)

def event_source_mappings(args):
    paginator = getattr(clients, "lambda").get_paginator("list_event_source_mappings")
    page_output(tabulate(paginate(paginator), args))

parser_event_source_mappings = register_listing_parser(event_source_mappings, parent=lambda_parser)

def update(args):
    with open(args.zip_file, "rb") as fh:
        payload = fh.read()
    payload_sha = hashlib.sha256(payload).digest()
    res = getattr(clients, "lambda").update_function_code(FunctionName=args.function_name, ZipFile=payload)
    assert base64.b64decode(res["CodeSha256"]) == payload_sha
    return res

update_parser = register_parser(update, parent=lambda_parser)
update_parser.add_argument("function_name")
update_parser.add_argument("zip_file")
