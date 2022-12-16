"""
Configure and view AWS cost and usage reports.

Run ``aegea billing configure`` to enable collection of reports in your AWS account. Reports can take up to 24 hours to
generate. After generation, view reports with ``aegea billing ls``. See ``aegea billing ls --help`` for more options.

Reports can also be configured and viewed at https://console.aws.amazon.com/billing/home.
"""

import argparse
import csv
import gzip
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta
from io import BytesIO, TextIOWrapper

import dateutil
from botocore.exceptions import ClientError

from . import config, register_parser
from .util.aws import ARN, clients, ensure_s3_bucket, resources
from .util.aws.iam import IAMPolicyBuilder
from .util.exceptions import AegeaException
from .util.printing import format_table, get_cell, get_field, page_output, tabulate


def billing(args):
    billing_parser.print_help()

billing_parser = register_parser(billing, help="Configure and view AWS cost and usage reports", description=__doc__)

def configure(args):
    bucket_name = args.billing_reports_bucket.format(account_id=ARN.get_account_id())
    bucket_policy = IAMPolicyBuilder(principal="arn:aws:iam::386209384616:root",
                                     action=["s3:GetBucketAcl", "s3:GetBucketPolicy"],
                                     resource="arn:aws:s3:::{}".format(bucket_name))
    bucket_policy.add_statement(principal="arn:aws:iam::386209384616:root",
                                action=["s3:PutObject"],
                                resource="arn:aws:s3:::{}/*".format(bucket_name))
    bucket = ensure_s3_bucket(bucket_name, policy=bucket_policy)
    try:
        clients.cur.put_report_definition(ReportDefinition=dict(ReportName=__name__,
                                                                TimeUnit="HOURLY",
                                                                Format="textORcsv",
                                                                Compression="GZIP",
                                                                S3Bucket=bucket.name,
                                                                S3Prefix="aegea",
                                                                S3Region=clients.cur.meta.region_name,
                                                                AdditionalSchemaElements=["RESOURCES"]))
    except clients.cur.exceptions.DuplicateReportNameException:
        pass
    print("Configured cost and usage reports. Enable cost allocation tags: http://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/activate-built-in-tags.html.") # noqa

parser = register_parser(configure, parent=billing_parser)

def filter_line_items(items, args):
    for item in items:
        if args.min_cost and float(item["lineItem/BlendedCost"]) < args.min_cost:
            continue
        if args.days and item["lineItem/UsageStartDate"]:
            window_start = datetime.utcnow() - timedelta(days=args.days)
            if dateutil.parser.parse(item["lineItem/UsageStartDate"]) < window_start:
                continue
        yield item

def ls(args):
    bucket = resources.s3.Bucket(args.billing_reports_bucket.format(account_id=ARN.get_account_id()))
    now = datetime.utcnow()
    year = args.year or now.year
    month = str(args.month or now.month).zfill(2)
    next_year = year + ((args.month or now.month) + 1) // 12
    next_month = str(((args.month or now.month) + 1) % 12).zfill(2)
    manifest_name = "aegea/{report}/{yr}{mo}01-{next_yr}{next_mo}01/{report}-Manifest.json"
    manifest_name = manifest_name.format(report=__name__, yr=year, mo=month, next_yr=next_year, next_mo=next_month)
    try:
        manifest = json.loads(bucket.Object(manifest_name).get().get("Body").read())
        for report_key in manifest["reportKeys"]:
            report = BytesIO(bucket.Object(report_key).get().get("Body").read())
            with gzip.GzipFile(fileobj=report) as fh:
                reader = csv.DictReader(fh)  # type: ignore
                for line in reader:
                    page_output(tabulate(filter_line_items(reader, args), args))
    except ClientError as e:
        msg = 'Unable to get report {} from {}: {}. Run "aegea billing configure" to enable reports.'
        raise AegeaException(msg.format(manifest_name, bucket, e))

parser = register_parser(ls, parent=billing_parser, help="List contents of AWS cost and usage reports")
parser.add_argument("--columns", nargs="+")
parser.add_argument("--year", type=int, help="Year to get billing reports for. Defaults to current year")
parser.add_argument("--month", type=int, help="Month (numeral) to get billing reports for. Defaults to current month")
parser.add_argument("--billing-reports-bucket", help="Name of S3 bucket to retrieve billing reports from",
                    default=config.billing_configure.billing_reports_bucket)  # type: ignore
parser.add_argument("--min-cost", type=float, help="Omit billing line items below this cost")
parser.add_argument("--days", type=float, help="Only look at line items from this many past days")
parser.add_argument("--by-user", action="store_true")
