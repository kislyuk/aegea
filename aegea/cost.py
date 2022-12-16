import collections
import json
import os
import sys
from datetime import datetime, timedelta

import boto3
import requests

from . import register_parser
from .util import Timestamp, paginate
from .util.aws import clients, get_products, instance_type_completer, offers_api, region_name
from .util.printing import format_datetime, format_table, page_output, tabulate


def format_float(f):
    try:
        return "{:.2f}".format(float(f))
    except Exception:
        return f

def get_common_method_args(args):
    return dict(Granularity=args.granularity,
                TimePeriod=dict(Start=args.time_period_start.date().isoformat(),
                                End=args.time_period_end.date().isoformat()))

def cost(args):
    if not (args.group_by or args.group_by_tag):
        args.group_by = ["SERVICE"]
    get_cost_and_usage_args = dict(get_common_method_args(args), Metrics=args.metrics)
    get_cost_and_usage_args["GroupBy"] = [dict(Type="DIMENSION", Key=k) for k in args.group_by]
    get_cost_and_usage_args["GroupBy"] += [dict(Type="TAG", Key=k) for k in args.group_by_tag]
    rows = collections.defaultdict(dict)  # type: ignore
    try:
        account_name = clients.iam.list_account_aliases()["AccountAliases"][0]
    except Exception:
        account_name = boto3.session.Session().profile_name
    title = "{} ({})".format(args.group_by[0] if args.group_by else "Tag:" + args.group_by_tag[0], account_name)
    args.columns, cell_transforms = [title], {"TOTAL": format_float}
    for page in clients.ce.get_cost_and_usage(**get_cost_and_usage_args)["ResultsByTime"]:
        args.columns.append(page["TimePeriod"]["Start"])
        cell_transforms[page["TimePeriod"]["Start"]] = format_float
        for i, group in enumerate(page["Groups"]):
            value = group["Metrics"][args.metrics[0]]
            if isinstance(value, dict) and "Amount" in value:
                value = float(value["Amount"])
            rows[group["Keys"][0]].setdefault(title, group["Keys"][0])
            rows[group["Keys"][0]].setdefault("TOTAL", 0)
            rows[group["Keys"][0]]["TOTAL"] += value
            rows[group["Keys"][0]][page["TimePeriod"]["Start"]] = value
    args.columns.append("TOTAL")
    rows = [row for row in rows.values() if row["TOTAL"] > args.min_total]  # type: ignore
    rows = sorted(rows, key=lambda row: -row["TOTAL"])  # type: ignore
    page_output(tabulate(rows, args, cell_transforms=cell_transforms))

parser_cost = register_parser(cost, help="List AWS costs")
parser_cost.add_argument("--time-period-start", type=Timestamp, default=Timestamp("-7d"),
                         help="Time to start cost history." + Timestamp.__doc__)  # type: ignore
parser_cost.add_argument("--time-period-end", type=Timestamp, default=Timestamp("-0d"),
                         help="Time to end cost history." + Timestamp.__doc__)  # type: ignore
parser_cost.add_argument("--granularity", choices={"HOURLY", "DAILY", "MONTHLY"}, help="AWS cost granularity")
parser_cost.add_argument("--metrics", nargs="+", default=["AmortizedCost"],
                         choices={"AmortizedCost", "BlendedCost", "NetAmortizedCost", "NetUnblendedCost",
                                  "NormalizedUsageAmount", "UnblendedCost", "UsageQuantity"})
parser_cost.add_argument("--group-by", nargs="+", default=[],
                         choices={"AZ", "INSTANCE_TYPE", "LINKED_ACCOUNT", "OPERATION", "PURCHASE_TYPE", "SERVICE",
                                  "REGION", "USAGE_TYPE", "PLATFORM", "TENANCY", "RECORD_TYPE", "LEGAL_ENTITY_NAME",
                                  "DEPLOYMENT_OPTION", "DATABASE_ENGINE", "CACHE_ENGINE", "INSTANCE_TYPE_FAMILY",
                                  "BILLING_ENTITY", "RESERVATION_ID", "SAVINGS_PLANS_TYPE", "SAVINGS_PLAN_ARN"})
parser_cost.add_argument("--group-by-tag", nargs="+", default=[])
parser_cost.add_argument("--min-total", type=int, help="Omit rows that total below this number")

def cost_forecast(args):
    get_cost_forecast_args = dict(get_common_method_args(args), Metric=args.metric, PredictionIntervalLevel=75)
    res = clients.ce.get_cost_forecast(**get_cost_forecast_args)
    args.columns = ["TimePeriod.Start", "MeanValue", "PredictionIntervalLowerBound", "PredictionIntervalUpperBound"]
    cell_transforms = {col: format_float
                       for col in ["MeanValue", "PredictionIntervalLowerBound", "PredictionIntervalUpperBound"]}
    title = "TOTAL ({})".format(boto3.session.Session().profile_name)
    table = res["ForecastResultsByTime"] + [{"TimePeriod": {"Start": title}, "MeanValue": res["Total"]["Amount"]}]
    page_output(tabulate(table, args, cell_transforms=cell_transforms))

parser_cost_forecast = register_parser(cost_forecast, help="List AWS cost forecasts")
parser_cost_forecast.add_argument("--time-period-start", type=Timestamp, default=Timestamp("1d"),
                                  help="Time to start cost forecast." + Timestamp.__doc__)  # type: ignore
parser_cost_forecast.add_argument("--time-period-end", type=Timestamp, default=Timestamp("7d"),
                                  help="Time to end cost forecast." + Timestamp.__doc__)  # type: ignore
parser_cost_forecast.add_argument("--granularity", choices={"HOURLY", "DAILY", "MONTHLY"},
                                  help="Up to 3 months of DAILY forecasts or 12 months of MONTHLY forecasts")
parser_cost_forecast.add_argument("--metric", help="Which metric Cost Explorer uses to create your forecast",
                                  choices={"USAGE_QUANTITY", "UNBLENDED_COST", "NET_UNBLENDED_COST", "AMORTIZED_COST",
                                           "NET_AMORTIZED_COST", "BLENDED_COST", "NORMALIZED_USAGE_AMOUNT"})
