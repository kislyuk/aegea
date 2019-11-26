"""
List CloudWatch Logs groups and streams.

List log groups, streams and their attributes:

    aegea logs

Print all CloudWatch Logs messages from LOG_GROUP in the past 2 days:

    aegea logs LOG_GROUP --start-time=-2d [--end-time=...]

Print logs from a specific set of log streams:

    aegea logs LOG_GROUP LOG_STREAM_PREFIX

Search logs for a string in the past week (using CloudWatch Logs Insights query syntax):

    aegea grep STRING LOG_GROUP --start-time=-1w

Run the same search, but retrieve 10 lines of context for each match:

    aegea grep STRING LOG_GROUP --start-time=-1w -C 10
"""

import os, sys, time, concurrent.futures
from datetime import datetime, timedelta
from functools import partial

from . import register_parser, logger
from .util import Timestamp, paginate, add_time_bound_args
from .util.compat import timestamp
from .util.exceptions import AegeaException
from .util.printing import page_output, tabulate
from .util.aws import clients
from .util.aws.logs import export_and_print_log_events, print_log_event, print_log_events

def logs(args):
    if args.log_group and (args.log_stream or args.start_time or args.end_time):
        if args.export:
            return export_and_print_log_events(args)
        else:
            return print_log_events(args)
    table = []
    group_cols = ["logGroupName"]
    stream_cols = ["logStreamName", "lastIngestionTime", "storedBytes"]
    args.columns = group_cols + stream_cols
    for group in paginate(clients.logs.get_paginator("describe_log_groups")):
        if args.log_group and group["logGroupName"] != args.log_group:
            continue
        n = 0
        for stream in paginate(clients.logs.get_paginator("describe_log_streams"),
                               logGroupName=group["logGroupName"], orderBy="LastEventTime", descending=True):
            now = datetime.utcnow().replace(microsecond=0)
            stream["lastIngestionTime"] = now - datetime.utcfromtimestamp(stream.get("lastIngestionTime", 0) // 1000)
            table.append(dict(group, **stream))
            n += 1
            if n >= args.max_streams_per_group:
                break
    page_output(tabulate(table, args))

logs_parser = register_parser(logs)
logs_parser.add_argument("--max-streams-per-group", "-n", type=int, default=8)
logs_parser.add_argument("--sort-by", default="lastIngestionTime:reverse")
logs_parser.add_argument("--no-export", action="store_false", dest="export")
logs_parser.add_argument("log_group", nargs="?", help="CloudWatch log group")
logs_parser.add_argument("log_stream", nargs="?", help="CloudWatch log stream")
add_time_bound_args(logs_parser, snap=2)

def filter(args):
    filter_args = dict(logGroupName=args.log_group)
    if args.log_stream:
        filter_args.update(logStreamNames=[args.log_stream])
    if args.pattern:
        filter_args.update(filterPattern=args.pattern)
    if args.start_time:
        filter_args.update(startTime=int(timestamp(args.start_time) * 1000))
    if args.end_time:
        filter_args.update(endTime=int(timestamp(args.end_time) * 1000))
    num_results = 0
    while True:
        for event in paginate(clients.logs.get_paginator("filter_log_events"), **filter_args):
            if "timestamp" not in event or "message" not in event:
                continue
            print_log_event(event)
            num_results += 1
        if args.follow:
            time.sleep(1)
        else:
            return SystemExit(os.EX_OK if num_results > 0 else os.EX_DATAERR)

filter_parser = register_parser(filter, help="Filter and print events in a CloudWatch Logs stream or group of streams")
filter_parser.add_argument("pattern", help="""CloudWatch filter pattern to use. Case-sensitive. See
http://docs.aws.amazon.com/AmazonCloudWatch/latest/DeveloperGuide/FilterAndPatternSyntax.html""")
filter_parser.add_argument("log_group", help="CloudWatch log group")
filter_parser.add_argument("log_stream", nargs="?", help="CloudWatch log stream")
filter_parser.add_argument("--follow", "-f", help="Repeat search continuously instead of running once",
                           action="store_true")
add_time_bound_args(filter_parser)

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

def grep(args):
    if args.context:
        args.before = args.after = args.context
    if not args.end_time:
        args.end_time = Timestamp("-0s")
    query = clients.logs.start_query(logGroupName=args.log_group,
                                     startTime=int(timestamp(args.start_time) * 1000),
                                     endTime=int(timestamp(args.end_time) * 1000),
                                     queryString=args.query)
    seen_results = {}
    try:
        with concurrent.futures.ThreadPoolExecutor() as executor:
            while True:
                res = clients.logs.get_query_results(queryId=query["queryId"])
                log_record_pointers = []
                for record in res["results"]:
                    event = {r["field"]: r["value"] for r in record}
                    if event["@ptr"] in seen_results:
                        continue
                    if args.before or args.after:
                        log_record_pointers.append(event["@ptr"])
                    else:
                        print_log_event(event)
                    seen_results[event["@ptr"]] = event
                if log_record_pointers:
                    processor = partial(print_log_event_with_context, before=args.before, after=args.after)
                    executor.map(processor, log_record_pointers)
                if res["status"] == "Complete":
                    break
                elif res["status"] in {"Failed", "Cancelled"}:
                    raise AegeaException("Query status: {}".format(res["status"]))
                time.sleep(1)
    finally:
        try:
            clients.logs.stop_query(queryId=query["queryId"])
        except clients.logs.exceptions.InvalidParameterException:
            pass
    logger.debug("Query %s: %s", query["queryId"], res["statistics"])
    return SystemExit(os.EX_OK if seen_results else os.EX_DATAERR)

grep_parser = register_parser(grep, help="Run a CloudWatch Logs Insights query")
grep_parser.add_argument("query", help="""CloudWatch Logs Insights query to use. See
https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AnalyzingLogData.html""")
grep_parser.add_argument("log_group", help="CloudWatch log group")
grep_parser.add_argument("--before", type=int, default=0)
grep_parser.add_argument("--after", type=int, default=0)
grep_parser.add_argument("--context", type=int, default=0)
add_time_bound_args(grep_parser)
