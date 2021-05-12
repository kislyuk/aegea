import os, sys, time, hashlib, json, gzip, re
from datetime import datetime

import requests

from ... import logger
from .. import Timestamp, paginate, ThreadPoolExecutor
from ..compat import timestamp
from . import ARN, S3BucketLifecycleBuilder, ensure_s3_bucket, clients
from .iam import IAMPolicyBuilder

class CloudwatchLogReader:
    def __init__(self, log_stream_name, log_group_name="/aws/batch/job", head=None, tail=None):
        self.log_group_name = log_group_name
        self.log_stream_name = log_stream_name
        self.head, self.tail = head, tail
        self.next_page_key = "nextForwardToken" if self.tail is None else "nextBackwardToken"
        self.next_page_token = None

    def __iter__(self):
        page = None
        get_args = dict(logGroupName=self.log_group_name, logStreamName=self.log_stream_name,
                        limit=min(self.head or 10000, self.tail or 10000))
        get_args["startFromHead"] = True if self.tail is None else False
        if self.next_page_token:
            get_args["nextToken"] = self.next_page_token
        while True:
            page = clients.logs.get_log_events(**get_args)
            for event in page["events"]:
                if "timestamp" in event and "message" in event:
                    yield event
            get_args["nextToken"] = page[self.next_page_key]
            if self.head is not None or self.tail is not None or len(page["events"]) == 0:
                break
        if page:
            self.next_page_token = page[self.next_page_key]

def export_log_files(args):
    bucket_name = "aegea-cloudwatch-log-export-{}-{}".format(ARN.get_account_id(), clients.logs.meta.region_name)
    bucket_arn = ARN(service="s3", region="", account_id="", resource=bucket_name)
    logs_principal = {"Service": "logs.amazonaws.com"}
    policy = IAMPolicyBuilder(action="s3:GetBucketAcl", resource=str(bucket_arn), principal=logs_principal)
    policy.add_statement(action="s3:PutObject", resource=str(bucket_arn) + "/*", principal=logs_principal)
    lifecycle = S3BucketLifecycleBuilder(expiration=dict(Days=30))
    lifecycle.add_rule(abort_incomplete_multipart_upload=20)
    bucket = ensure_s3_bucket(bucket_name, policy=policy, lifecycle=lifecycle)
    if not args.end_time:
        args.end_time = Timestamp.match_precision(Timestamp("-0s"), args.start_time)
    export_task_args = dict(logGroupName=args.log_group,
                            fromTime=int(timestamp(args.start_time) * 1000),
                            to=int(timestamp(args.end_time) * 1000),
                            destination=bucket.name)
    if args.log_stream:
        export_task_args.update(logStreamNamePrefix=args.log_stream)
    cache_key = hashlib.sha256(json.dumps(export_task_args, sort_keys=True).encode()).hexdigest()[:32]
    export_task_args.update(destinationPrefix=cache_key)
    for log_object in bucket.objects.filter(Prefix=cache_key):
        logger.debug("Reusing completed export task %s", log_object.key)
        break
    else:
        logger.debug("Starting new log export task %s", export_task_args)
        task_desc = clients.logs.create_export_task(**export_task_args)
        try:
            while task_desc.get("status", {}).get("code") != "COMPLETED":
                res = clients.logs.describe_export_tasks(taskId=task_desc["taskId"])
                assert len(res["exportTasks"]) == 1
                task_desc = res["exportTasks"][0]
                if task_desc["status"]["code"] in {"CANCELLED", "FAILED"}:
                    raise Exception("Log export task failed: " + task_desc["status"]["message"])
                msg = "log export task: {logGroupName} {from}..{to} -> s3://{destination}/{destinationPrefix} %s"
                logger.info(msg.format(**task_desc), task_desc["status"]["code"])
                time.sleep(1)
        finally:
            try:
                clients.logs.cancel_export_task(taskId=task_desc["taskId"])
                # TODO: if cancel successful, clean up s3 prefix
            except Exception:
                pass
    return bucket.objects.filter(Prefix=cache_key)

def get_lines_for_log_file(log_file):
    if not log_file.key.endswith(".gz"):
        return []
    pu = clients.s3.generate_presigned_url("get_object", Params=dict(Bucket=log_file.bucket_name, Key=log_file.key))
    log_lines = []
    with gzip.open(requests.get(pu, stream=True).raw, mode="rt") as fh:
        for line in fh:
            log_lines.append(line)
    return log_lines

def export_and_print_log_events(args):
    with ThreadPoolExecutor() as executor:
        for lines in executor.map(get_lines_for_log_file, export_log_files(args)):
            for line in lines:
                sys.stdout.write(line)

def print_log_event(event):
    if "@timestamp" in event:
        print(str(Timestamp(event["@timestamp"])), event["@message"])
    elif "timestamp" in event:
        print(str(Timestamp(event["timestamp"])), event["message"])
    else:
        print(json.dumps(event, indent=4))

def print_log_events(args):
    streams = []
    if args.log_stream:
        describe_log_streams_args = dict(logGroupName=args.log_group, logStreamNamePrefix=args.log_stream)
    else:
        describe_log_streams_args = dict(logGroupName=args.log_group, orderBy="LastEventTime", descending=True)
    for stream in paginate(clients.logs.get_paginator("describe_log_streams"), **describe_log_streams_args):
        stream_name = stream["arn"].split(":")[-1]
        first_event_ts = datetime.utcfromtimestamp(stream.get("firstEventTimestamp", 0) // 1000)
        last_event_ts = datetime.utcfromtimestamp(stream.get("lastEventTimestamp", 0) // 1000)
        if args.end_time and first_event_ts > args.end_time:
            continue
        if args.start_time and last_event_ts < args.start_time:
            break
        streams.append(stream_name)
    for stream in streams:
        get_log_events_args = dict(logGroupName=args.log_group, startFromHead=True, limit=100)
        if args.start_time:
            get_log_events_args.update(startTime=int(timestamp(args.start_time) * 1000))
        if args.end_time:
            get_log_events_args.update(endTime=int(timestamp(args.end_time) * 1000))
        while True:
            page = clients.logs.get_log_events(logStreamName=stream, **get_log_events_args)
            for event in page["events"]:
                if "timestamp" not in event or "message" not in event:
                    continue
                print_log_event(event)
            if len(page["events"]) == 0 or "nextForwardToken" not in page:
                break
            get_log_events_args.update(nextToken=page["nextForwardToken"], limit=10000)

def print_log_event_with_context(log_record_pointer, before=10, after=10):
    res = clients.logs.get_log_record(logRecordPointer=log_record_pointer)
    log_record = res["logRecord"]
    account_id, log_group_name = log_record["@log"].split(":")
    before_ctx = clients.logs.get_log_events(logGroupName=log_group_name,
                                             logStreamName=log_record["@logStream"],
                                             endTime=int(log_record["@timestamp"]),
                                             limit=before,
                                             startFromHead=False)
    for event in before_ctx["events"]:
        print_log_event(event)
    after_ctx = clients.logs.get_log_events(logGroupName=log_group_name,
                                            logStreamName=log_record["@logStream"],
                                            startTime=int(log_record["@timestamp"]),
                                            limit=after,
                                            startFromHead=True)
    for event in after_ctx["events"]:
        print_log_event(event)
    print("---")
