import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError

from . import logger, register_parser
from .ls import filter_collection, register_filtering_parser, register_listing_parser
from .util import ThreadPoolExecutor
from .util.aws import ARN, clients, expect_error_codes, get_cloudwatch_metric_stats, resources
from .util.exceptions import AegeaException
from .util.printing import format_number, page_output, tabulate


def s3(args):
    s3_parser.print_help()

s3_parser = register_parser(s3, help="Manage S3 buckets and query s3 objects", description=__doc__)

def describe_bucket_worker(bucket):
    bucket.LocationConstraint = clients.s3.get_bucket_location(Bucket=bucket.name)["LocationConstraint"]
    cloudwatch = resources.cloudwatch
    bucket_region = bucket.LocationConstraint or "us-east-1"
    if bucket_region != cloudwatch.meta.client.meta.region_name:
        cloudwatch = boto3.Session(region_name=bucket_region).resource("cloudwatch")
    data = get_cloudwatch_metric_stats("AWS/S3", "NumberOfObjects",
                                       start_time=datetime.utcnow() - timedelta(days=2),
                                       end_time=datetime.utcnow(), period=3600, BucketName=bucket.name,
                                       StorageType="AllStorageTypes", resource=cloudwatch)
    bucket.NumberOfObjects = int(data["Datapoints"][-1]["Average"]) if data["Datapoints"] else None
    data = get_cloudwatch_metric_stats("AWS/S3", "BucketSizeBytes",
                                       start_time=datetime.utcnow() - timedelta(days=2),
                                       end_time=datetime.utcnow(), period=3600, BucketName=bucket.name,
                                       StorageType="StandardStorage", resource=cloudwatch)
    bucket.BucketSizeBytes = format_number(data["Datapoints"][-1]["Average"]) if data["Datapoints"] else None
    try:
        res = clients.s3.get_bucket_encryption(Bucket=bucket.name)
        enc = res["ServerSideEncryptionConfiguration"]["Rules"][0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
        bucket.Encryption = enc
    except ClientError as e:
        expect_error_codes(e, "ServerSideEncryptionConfigurationNotFoundError", "AccessDenied")
        bucket.Encryption = None
    return bucket

def buckets(args):
    """
    List S3 buckets. See also "aws s3 ls". Use "aws s3 ls NAME" to list bucket contents.
    """
    with ThreadPoolExecutor() as executor:
        table = executor.map(describe_bucket_worker, filter_collection(resources.s3.buckets, args))
    page_output(tabulate(table, args))

buckets_parser = register_filtering_parser(buckets, parent=s3_parser)

def lifecycle(args):
    if args.delete:
        return resources.s3.BucketLifecycle(args.bucket_name).delete()
    rule = defaultdict(list, Prefix=args.prefix, Status="Enabled")  # type: Dict[str, Any]
    if args.transition_to_infrequent_access is not None:
        rule["Transitions"].append(dict(StorageClass="STANDARD_IA", Days=args.transition_to_infrequent_access))
    if args.transition_to_glacier is not None:
        rule["Transitions"].append(dict(StorageClass="GLACIER", Days=args.transition_to_glacier))
    if args.expire is not None:
        rule["Expiration"] = dict(Days=args.expire)
    if args.abort_incomplete_multipart_upload is not None:
        rule["AbortIncompleteMultipartUpload"] = dict(DaysAfterInitiation=args.abort_incomplete_multipart_upload)
    if len(rule) > 2:
        clients.s3.put_bucket_lifecycle_configuration(Bucket=args.bucket_name,
                                                      LifecycleConfiguration=dict(Rules=[rule]))
    try:
        for rule in resources.s3.BucketLifecycle(args.bucket_name).rules:
            print(json.dumps(rule))
    except ClientError as e:
        expect_error_codes(e, "NoSuchLifecycleConfiguration")
        logger.error("No lifecycle configuration for bucket %s", args.bucket_name)

lifecycle_parser = register_parser(lifecycle, parent=s3_parser)
lifecycle_parser.add_argument("bucket_name")
lifecycle_parser.add_argument("--delete", action="store_true")
lifecycle_parser.add_argument("--prefix", default="")
lifecycle_parser.add_argument("--transition-to-infrequent-access", type=int, metavar="DAYS")
lifecycle_parser.add_argument("--transition-to-glacier", type=int, metavar="DAYS")
lifecycle_parser.add_argument("--expire", type=int, metavar="DAYS")
lifecycle_parser.add_argument("--abort-incomplete-multipart-upload", type=int, metavar="DAYS")

def cors(args):
    raise NotImplementedError()

cors_parser = register_parser(cors, parent=s3_parser)
cors_parser.add_argument("bucket_name")

def select(args):
    """
    Select data from an S3 object using AWS S3 Select.

    See https://docs.aws.amazon.com/AmazonS3/latest/dev/selecting-content-from-objects.html.

    Example:

        aegea s3 select s3://my-bucket/data.json 'select * from S3Object[*].path'
    """
    _, _, bucket, key = args.s3_url.split("/", 3)
    input_serialization = {"JSON": {"Type": args.json_type.upper()}}
    if args.compression_type:
        input_serialization.update(CompressionType=args.compression_type)

    res = clients.s3.select_object_content(Bucket=bucket,
                                           Key=key,
                                           Expression=args.expression,
                                           ExpressionType="SQL",
                                           InputSerialization=input_serialization,
                                           OutputSerialization={"JSON": {"RecordDelimiter": "\n"}})
    for event in res["Payload"]:
        if "Records" in event:
            sys.stdout.buffer.write(event["Records"]["Payload"])
        elif "Stats" in event or "Progress" in event:
            logger.info(event)

select_parser = register_parser(select, parent=s3_parser)
select_parser.add_argument("s3_url")
select_parser.add_argument("expression")
select_parser.add_argument("--json-type", choices={"document", "lines"}, default="document")
select_parser.add_argument("--compression-type", choices={"gzip", "bzip2"})

def versions(args):
    """
    List versions of an object in a versioned S3 bucket.

    See https://docs.aws.amazon.com/AmazonS3/latest/dev/ObjectVersioning.html.

    Example:

        aegea s3 versions s3://my-bucket/data.json
    """
    _, _, bucket, key = args.s3_url.split("/", 3)
    table = resources.s3.Bucket(bucket).object_versions.filter(Prefix=key)
    page_output(tabulate(table, args))


versions_parser = register_listing_parser(versions, parent=s3_parser)
versions_parser.add_argument("s3_url")

def restore(args):
    """
    Restore a deleted object in a versioned S3 bucket.

    See https://docs.aws.amazon.com/AmazonS3/latest/dev/ObjectVersioning.html.

    Examples:

        aegea s3 restore s3://my-bucket/data.json
        aegea s3 restore s3://my-bucket/data.json Uecl6ZZze034000K5B3oHYAYHvhhla2H
    """
    _, _, bucket, key = args.s3_url.split("/", 3)
    for version in resources.s3.Bucket(bucket).object_versions.filter(Prefix=key):
        if args.version_id and args.version_id == version.id:
            logger.critical("Deleting %s", version)
            version.delete()
            return
        elif version.is_latest and version.size is None:
            logger.critical("Deleting %s", version)
            version.delete()
            return
    else:
        raise AegeaException("No matching version found")

restore_parser = register_parser(restore, parent=s3_parser)
restore_parser.add_argument("s3_url")
restore_parser.add_argument("version_id", nargs="?")
