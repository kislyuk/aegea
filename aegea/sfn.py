"""
Manage AWS Step Functions state machines and executions.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, argparse, json, time
from typing import Set, Dict, Any

from botocore.exceptions import ClientError

from . import batch, logger
from .ls import register_parser, register_listing_parser
from .ecr import ecr_image_name_completer
from .util import Timestamp, paginate, get_mkfs_command, ThreadPoolExecutor
from .util.aws import clients, ARN
from .util.printing import page_output, tabulate, YELLOW, RED, GREEN, BOLD, ENDC

sfn_status_colors = dict(RUNNING=GREEN(), SUCCEEDED=BOLD() + GREEN(),
                         FAILED=BOLD() + RED(), TIMED_OUT=BOLD() + RED(), ABORTED=BOLD() + RED())

def complete_state_machine_name(**kwargs):
    return [c["name"] for c in paginate(clients.stepfunctions.get_paginator("list_state_machines"))]

def sfn(args):
    sfn_parser.print_help()

sfn_parser = register_parser(sfn, help="Manage AWS Step Functions", description=__doc__)

def state_machines(args):
    page_output(tabulate(paginate(clients.stepfunctions.get_paginator("list_state_machines")), args))

state_machines_parser = register_listing_parser(state_machines, parent=sfn_parser, help="List state machines")

def ls(args):
    if args.state_machine:
        sm_arn = ARN(service="states", resource="stateMachine:" + args.state_machine)
        state_machines = [dict(stateMachineArn=str(sm_arn))]
    else:
        state_machines = paginate(clients.stepfunctions.get_paginator("list_state_machines"))

    def list_executions(state_machine):
        list_executions_paginator = clients.stepfunctions.get_paginator("list_executions")
        list_executions_args = dict(stateMachineArn=state_machine["stateMachineArn"])
        if args.status:
            list_executions_args.update(statusFilter=args.status)
        return list(paginate(list_executions_paginator, **list_executions_args))

    with ThreadPoolExecutor() as executor:
        executions = sum(executor.map(list_executions, state_machines), [])  # type: ignore

    page_output(tabulate(executions, args))

ls_parser = register_listing_parser(ls, parent=sfn_parser, help="List executions for state machines in this account")
ls_parser.add_argument("--state-machine").completer = complete_state_machine_name
ls_parser.add_argument("--status", choices=list(sfn_status_colors))

def describe(args):
    if ARN(args.resource_arn).resource.startswith("execution"):
        desc = clients.stepfunctions.describe_execution(executionArn=args.resource_arn)
        desc["input"] = json.loads(desc.get("input", "null"))
        desc["output"] = json.loads(desc.get("output", "null"))
    else:
        desc = clients.stepfunctions.describe_state_machine(stateMachineArn=args.resource_arn)
        desc["definition"] = json.loads(desc.get("definition", "null"))
    return desc

describe_parser = register_parser(describe, parent=sfn_parser, help="Describe a state machine or execution")
describe_parser.add_argument("resource_arn")

def watch(args, print_event_fn=batch.print_event):
    seen_events = set()  # type: Set[str]
    previous_status = None
    while True:
        exec_desc = clients.stepfunctions.describe_execution(executionArn=str(args.execution_arn))
        if exec_desc["status"] == previous_status:
            sys.stderr.write(".")
            sys.stderr.flush()
        else:
            logger.info("%s %s", exec_desc["executionArn"],
                        sfn_status_colors[exec_desc["status"]] + exec_desc["status"] + ENDC())
            previous_status = exec_desc["status"]
        history = clients.stepfunctions.get_execution_history(executionArn=str(args.execution_arn))
        for event in sorted(history["events"], key=lambda x: x["id"]):
            if event["id"] not in seen_events:
                details = {}  # type: Dict[str, Any]
                for key in event.keys():
                    if key.endswith("EventDetails") and event[key]:
                        details = event[key]
                logger.info("%s %s %s %s %s %s", event["timestamp"], event["type"],
                            details.get("resourceType", ""), details.get("resource", ""), details.get("name", ""),
                            json.loads(details.get("parameters", "{}")).get("FunctionName", ""))
                if "taskSubmittedEventDetails" in event:
                    if event.get("taskSubmittedEventDetails", {}).get("resourceType") == "batch":
                        job_id = json.loads(event["taskSubmittedEventDetails"]["output"])["JobId"]
                        logger.info("Batch job ID %s", job_id)
                        batch.watch(batch.watch_parser.parse_args([job_id]), print_event_fn=print_event_fn)
                seen_events.add(event["id"])
        if exec_desc["status"] in {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}:
            break
        time.sleep(1)

    if exec_desc["status"] == "SUCCEEDED":
        return json.loads(exec_desc["output"])
    else:
        history = clients.stepfunctions.get_execution_history(executionArn=str(args.execution_arn))
        last_event = sorted(history["events"], key=lambda x: x["id"])[-1]
        logger.error("%s %s", args.execution_arn, sfn_status_colors[exec_desc["status"]] + exec_desc["status"] + ENDC())
        return SystemExit(json.dumps(last_event, indent=4, default=str))


watch_parser = register_parser(watch, parent=sfn_parser,
                               help="Monitor a state machine execution and stream its execution history")
watch_parser.add_argument("execution_arn")

event_colors = dict(ExecutionStarted=GREEN(), ExecutionSucceeded=BOLD() + GREEN(), ExecutionFailed=BOLD() + RED(),
                    ExecutionAborted=BOLD() + RED(), TaskSucceeded=GREEN(), TaskFailed=RED())

def history(args):
    history = clients.stepfunctions.get_execution_history(executionArn=str(args.execution_arn))
    events = []
    execution_started = None
    for event in sorted(history["events"], key=lambda x: x["id"]):
        event.setdefault("details", {})
        if event["type"].startswith("Task") and event["type"] not in {"TaskStateEntered", "TaskStateExited"}:
            event["type"] = "  " + event["type"]
        if event["type"].strip() in event_colors:
            event["type"] = event_colors[event["type"].strip()] + event["type"] + ENDC()
        if execution_started is None:
            execution_started = event["timestamp"]
        event["elapsed"] = str(event["timestamp"] - execution_started).replace("000", "")
        for key in list(event):
            if key.endswith("EventDetails") and event[key]:
                event["details"] = event[key]
        event["name"] = event["details"].get("name", ":".join([event["details"].get(k, "")
                                                               for k in ["resourceType", "resource"]]))
        if event["name"] == ":":
            event["name"] = ARN(args.execution_arn).resource.split(":", 1)[-1]
        elif "FunctionName" in event["details"].get("parameters", ""):
            event["name"] += "({})".format(json.loads(event["details"]["parameters"]).get("FunctionName"))
        elif "JobId" in event["details"].get("output", ""):
            event["name"] += "({})".format(json.loads(event["details"]["output"]).get("JobId"))
        events.append(event)
    page_output(tabulate(events, args))

history_parser = register_listing_parser(history, parent=sfn_parser, help="List event history for a given execution")
history_parser.add_argument("execution_arn")

def stop(args):
    return clients.stepfunctions.stop_execution(executionArn=args.execution_arn)

stop_parser = register_listing_parser(stop, parent=sfn_parser, help="Stop an execution")
stop_parser.add_argument("execution_arn")
