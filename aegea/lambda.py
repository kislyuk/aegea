"""
Manage AWS Lambda functions and their event sources
"""

import argparse
import base64
import hashlib
import os
import sys

from .ls import register_listing_parser, register_parser
from .util import Timestamp, paginate
from .util.aws import clients, resources
from .util.printing import page_output, tabulate


def _lambda(args):
    lambda_parser.print_help()

lambda_parser = register_parser(_lambda, name="lambda")

def ls(args):
    paginator = getattr(clients, "lambda").get_paginator("list_functions")
    page_output(tabulate(paginate(paginator), args, cell_transforms={"LastModified": Timestamp}))

parser_ls = register_listing_parser(ls, parent=lambda_parser, help="List AWS Lambda functions")

def event_source_mappings(args):
    paginator = getattr(clients, "lambda").get_paginator("list_event_source_mappings")
    page_output(tabulate(paginate(paginator), args))

parser_event_source_mappings = register_listing_parser(event_source_mappings, parent=lambda_parser,
                                                       help="List event source mappings")

def update_code(args):
    with open(args.zip_file, "rb") as fh:
        payload = fh.read()
    payload_sha = hashlib.sha256(payload).digest()
    res = getattr(clients, "lambda").update_function_code(FunctionName=args.function_name, ZipFile=payload)
    assert base64.b64decode(res["CodeSha256"]) == payload_sha
    return res

update_code_parser = register_parser(update_code, parent=lambda_parser, help="Update function code")
update_code_parser.add_argument("function_name")
update_code_parser.add_argument("zip_file")

def update_config(args):
    update_args = dict(FunctionName=args.function_name)
    if args.role:
        update_args.update(Role=args.role)
    if args.timeout:
        update_args.update(Timeout=args.timeout)
    if args.memory_size:
        update_args.update(MemorySize=args.memory_size)
    if args.environment:
        cfg = getattr(clients, "lambda").get_function_configuration(FunctionName=args.function_name)
        cfg["Environment"]["Variables"].update(args.environment)
        update_args.update(Environment=cfg["Environment"])
    return getattr(clients, "lambda").update_function_configuration(**update_args)

def role_name_completer(**kwargs):
    return [r.name for r in resources.iam.roles.all()]

update_config_parser = register_parser(update_config, parent=lambda_parser, help="Update function configuration")
update_config_parser.add_argument("function_name")
update_config_parser.add_argument("--role", help="IAM role for the function").completer = role_name_completer
update_config_parser.add_argument("--timeout", type=int,
                                  help="The amount of time that Lambda allows a function to run before stopping it")
update_config_parser.add_argument("--memory-size", type=int,
                                  help="The amount of memory that your function has access to")
update_config_parser.add_argument("--environment", nargs="+", metavar="NAME=VALUE", type=lambda x: x.split("=", 1),
                                  help="Read environment variables for function, update with given values, write back")
