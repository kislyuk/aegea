import os
import sys

from . import register_parser
from .ls import register_listing_parser
from .util.aws import ARN, clients, resolve_instance_id, resources
from .util.printing import page_output, tabulate


def alarms(args):
    page_output(tabulate(resources.cloudwatch.alarms.all(), args))

parser = register_listing_parser(alarms, help="List CloudWatch alarms")

def put_alarm(args):
    sns = resources.sns
    logs = clients.logs
    cloudwatch = clients.cloudwatch
    topic = sns.create_topic(Name=args.alarm_name)
    topic.subscribe(Protocol="email", Endpoint=args.email)
    logs.put_metric_filter(logGroupName=args.log_group_name,
                           filterName=args.alarm_name,
                           filterPattern=args.pattern,
                           metricTransformations=[dict(metricName=args.alarm_name,
                                                       metricNamespace=__name__,
                                                       metricValue="1")])
    cloudwatch.put_metric_alarm(AlarmName=args.alarm_name,
                                MetricName=args.alarm_name,
                                Namespace=__name__,
                                Statistic="Sum",
                                Period=300,
                                Threshold=1,
                                ComparisonOperator="GreaterThanOrEqualToThreshold",
                                EvaluationPeriods=1,
                                AlarmActions=[topic.arn])

parser = register_parser(put_alarm, help="Configure a CloudWatch alarm")
parser.add_argument("--log-group-name", required=True)
parser.add_argument("--alarm-name", required=True)
parser.add_argument("--pattern", required=True)
parser.add_argument("--email", required=True)
