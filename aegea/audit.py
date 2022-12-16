import csv
import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta
from typing import Any, Dict

import dateutil.parser
from botocore.exceptions import ClientError
from dateutil.tz import tzutc

from . import config, logger, register_parser
from .util import natural_sort
from .util.aws import ARN, clients, expect_error_codes, resources
from .util.printing import GREEN, RED, WHITE, format_table, page_output


class Auditor(unittest.TestCase):
    cache = {}  # type: Dict[str, Any]
    email = None

    def runTest(self):
        pass

    @property
    def credential_report(self):
        if "credential_report" not in self.cache:
            iam = clients.iam
            iam.generate_credential_report()
            while True:
                try:
                    self.cache["credential_report"] = iam.get_credential_report()
                    break
                except ClientError as e:
                    expect_error_codes(e, "ReportInProgress")
        return csv.DictReader(self.cache["credential_report"]["Content"].decode("utf-8").splitlines())

    @property
    def account_password_policy(self):
        if "account_password_policy" not in self.cache:
            self.cache["account_password_policy"] = resources.iam.AccountPasswordPolicy()
        return self.cache["account_password_policy"]

    @property
    def trails(self):
        if "trails" not in self.cache:
            self.cache["trails"] = clients.cloudtrail.describe_trails()["trailList"]
        return self.cache["trails"]

    @property
    def alarms(self):
        if "alarms" not in self.cache:
            self.cache["alarms"] = list(resources.cloudwatch.alarms.all())
        return self.cache["alarms"]

    def parse_date(self, d):
        return dateutil.parser.parse(d)

    def audit_1_1(self):
        """1.1 Avoid the use of the "root" account (Scored)"""
        for row in self.credential_report:
            if row["user"] == "<root_account>":
                for field in "password_last_used", "access_key_1_last_used_date", "access_key_2_last_used_date":
                    if row[field] != "N/A" and self.parse_date(row[field]) > datetime.now(tzutc()) - timedelta(days=1):
                        raise Exception("Root account last used less than a day ago ({})".format(field))

    def audit_1_2(self):
        """1.2 Ensure multi-factor authentication (MFA) is enabled for all IAM users that have a console password (Scored)"""  # noqa
        for row in self.credential_report:
            if row["user"] == "<root_account>" or json.loads(row["password_enabled"]):
                if not json.loads(row["mfa_active"]):
                    raise Exception("Account {} has a console password but no MFA".format(row["user"]))

    def audit_1_3(self):
        """1.3 Ensure credentials unused for 90 days or greater are disabled (Scored)"""
        for row in self.credential_report:
            for access_key in "1", "2":
                if json.loads(row["access_key_{}_active".format(access_key)]):
                    last_used = row["access_key_{}_last_used_date".format(access_key)]
                    if last_used != "N/A" and self.parse_date(last_used) < datetime.now(tzutc()) - timedelta(days=90):
                        msg = "Active access key {} in account {} last used over 90 days ago"
                        raise Exception(msg.format(access_key, row["user"]))

    def audit_1_4(self):
        """1.4 Ensure access keys are rotated every 90 days or less (Scored)"""
        for row in self.credential_report:
            for access_key in "1", "2":
                if json.loads(row["access_key_{}_active".format(access_key)]):
                    last_rotated = row["access_key_{}_last_rotated".format(access_key)]
                    if self.parse_date(last_rotated) < datetime.now(tzutc()) - timedelta(days=90):
                        msg = "Active access key {} in account {} last rotated over 90 days ago"
                        raise Exception(msg.format(access_key, row["user"]))

    def audit_1_5(self):
        """1.5 Ensure IAM password policy requires at least one uppercase letter (Scored)"""
        self.assertTrue(self.account_password_policy.require_uppercase_characters)

    def audit_1_6(self):
        """1.6 Ensure IAM password policy require at least one lowercase letter (Scored)"""
        self.assertTrue(self.account_password_policy.require_lowercase_characters)

    def audit_1_7(self):
        """1.7 Ensure IAM password policy require at least one symbol (Scored)"""
        self.assertTrue(self.account_password_policy.require_symbols)

    def audit_1_8(self):
        """1.8 Ensure IAM password policy require at least one number (Scored)"""
        self.assertTrue(self.account_password_policy.require_numbers)

    def audit_1_9(self):
        """1.9 Ensure IAM password policy requires minimum length of 14 or greater (Scored)"""
        self.assertGreaterEqual(self.account_password_policy.minimum_password_length, 14)

    def audit_1_10(self):
        """1.10 Ensure IAM password policy prevents password reuse (Scored)"""
        self.assertTrue(self.account_password_policy.password_reuse_prevention)

    def audit_1_11(self):
        """1.11 Ensure IAM password policy expires passwords within 90 days or less (Scored)"""
        self.assertLessEqual(self.account_password_policy.max_password_age, 90)

    def audit_1_12(self):
        """1.12 Ensure no root account access key exists (Scored)"""
        for row in self.credential_report:
            if row["user"] == "<root_account>":
                self.assertFalse(json.loads(row["access_key_1_active"]))
                self.assertFalse(json.loads(row["access_key_2_active"]))

    def audit_1_13(self):
        """1.13 Ensure hardware MFA is enabled for the "root" account (Scored)"""
        for row in self.credential_report:
            if row["user"] == "<root_account>":
                self.assertTrue(json.loads(row["mfa_active"]))

    def audit_1_14(self):
        """1.14 Ensure security questions are registered in the AWS account (Not Scored)"""

    def audit_1_15(self):
        """1.15 Ensure IAM policies are attached only to groups or roles (Scored)"""
        for policy in resources.iam.policies.all():
            self.assertEqual(len(list(policy.attached_users.all())), 0, "{} has users attached to it".format(policy))

    def audit_2_1(self):
        """2.1 Ensure CloudTrail is enabled in all regions (Scored)"""
        self.assertTrue(any(trail["IsMultiRegionTrail"] for trail in self.trails), "No multi-region CloudTrail trails")

    def audit_2_2(self):
        """2.2 Ensure CloudTrail log file validation is enabled (Scored)"""
        self.assertGreater(len(self.trails), 0, "No CloudTrail trails configured")
        self.assertTrue(all(trail["LogFileValidationEnabled"] for trail in self.trails),
                        "Some CloudTrail trails don't have log file validation enabled")

    def audit_2_3(self):
        """2.3 Ensure the S3 bucket CloudTrail logs to is not publicly accessible (Scored)"""
        raise NotImplementedError()
        import boto3
        s3 = boto3.session.Session(region_name="us-east-1").resource("s3")
        # s3 = boto3.resource("s3")
        # for trail in self.trails:
        #    for grant in s3.Bucket(trail["S3BucketName"]).Acl().grants:
        #    print(s3.Bucket(trail["S3BucketName"]).Policy().policy)
        for bucket in s3.buckets.all():
            print(bucket)
            try:
                print("    Policy:", bucket.Policy().policy)
            except Exception:
                pass
            for grant in bucket.Acl().grants:
                try:
                    print("    Grant:", grant)
                except Exception:
                    pass

    def audit_2_4(self):
        """2.4 Ensure CloudTrail trails are integrated with CloudWatch Logs (Scored)"""
        for trail in self.trails:
            self.assertIn("CloudWatchLogsLogGroupArn", trail)
            trail_status = clients.cloudtrail.get_trail_status(Name=trail["TrailARN"])
            self.assertGreater(trail_status["LatestCloudWatchLogsDeliveryTime"],
                               datetime.now(tzutc()) - timedelta(days=1))

    def audit_2_5(self):
        """2.5 Ensure AWS Config is enabled in all regions (Scored)"""
        import boto3
        for region in boto3.Session().get_available_regions("config"):
            aws_config = boto3.session.Session(region_name=region).client("config")
            res = aws_config.describe_configuration_recorder_status()
            self.assertGreater(len(res["ConfigurationRecordersStatus"]), 0)

    def audit_2_6(self):
        """2.6 Ensure S3 bucket access logging is enabled on the CloudTrail S3 bucket (Scored)"""
        raise NotImplementedError()

    def audit_2_7(self):
        """2.7 Ensure CloudTrail logs are encrypted at rest using KMS CMKs (Scored)"""
        raise NotImplementedError()

    def audit_2_8(self):
        """2.8 Ensure rotation for customer created CMKs is enabled (Scored)"""
        raise NotImplementedError()

    def ensure_alarm(self, name, pattern, log_group_name):
        # See http://docs.aws.amazon.com/AmazonCloudWatch/latest/DeveloperGuide/CW_Support_For_AWS.html
        sns = resources.sns
        logs = clients.logs
        cloudwatch = clients.cloudwatch
        topic = sns.create_topic(Name=name)
        topic.subscribe(Protocol='email', Endpoint=self.email)
        logs.put_metric_filter(logGroupName=log_group_name,
                               filterName=name,
                               filterPattern=pattern,
                               metricTransformations=[dict(metricName=name,
                                                           metricNamespace=__name__,
                                                           metricValue="1")])
        cloudwatch.put_metric_alarm(AlarmName=name,
                                    MetricName=name,
                                    Namespace=__name__,
                                    Statistic="Sum",
                                    Period=300,
                                    Threshold=1,
                                    ComparisonOperator="GreaterThanOrEqualToThreshold",
                                    EvaluationPeriods=1,
                                    AlarmActions=[topic.arn])

    def assert_alarm(self, name, pattern, remediate=False):
        logs = clients.logs
        sns = resources.sns
        alarm_ok = False
        for trail in self.trails:
            log_group_name = ARN(trail["CloudWatchLogsLogGroupArn"]).resource.split(":")[1]
            for metric_filter in logs.describe_metric_filters(logGroupName=log_group_name)["metricFilters"]:
                if metric_filter["filterPattern"] == pattern:
                    for alarm in self.alarms:
                        try:
                            self.assertEqual(alarm.metric_name, metric_filter["metricTransformations"][0]["metricName"])
                            self.assertGreater(len(list(sns.Topic(alarm.alarm_actions[0]).subscriptions.all())), 0)
                            alarm_ok = True
                        except Exception:
                            pass
        if remediate and not alarm_ok:
            self.ensure_alarm(name=name,
                              pattern=pattern,
                              log_group_name=log_group_name)
            alarm_ok = True
        self.assertTrue(alarm_ok)

    def audit_3_1(self):
        """3.1 Ensure a log metric filter and alarm exist for unauthorized API calls (Scored)"""
        self.assert_alarm("UnauthorizedAPICalls",
                          '{ ($.errorCode = "*UnauthorizedOperation") || ($.errorCode = "AccessDenied*") }')

    def audit_3_2(self):
        """3.2 Ensure a log metric filter and alarm exist for Management Console sign-in without MFA (Scored)"""
        self.assert_alarm("ConsoleUseWithoutMFA",
                          '{ $.userIdentity.sessionContext.attributes.mfaAuthenticated != "true" }')

    def audit_3_3(self):
        """3.3 Ensure a log metric filter and alarm exist for usage of "root" account (Scored)"""
        self.assert_alarm("RootAccountUsed",
                          '{ $.userIdentity.type = \"Root\" && $.userIdentity.invokedBy NOT EXISTS && $.eventType != \"AwsServiceEvent\" }')  # noqa

    def audit_3_4(self):
        """3.4 Ensure a log metric filter and alarm exist for IAM policy changes (Scored)"""
        self.assert_alarm("IAMPolicyChanged",
                          '{($.eventName=DeleteGroupPolicy)||($.eventName=DeleteRolePolicy)||($.eventName=DeleteUserPolicy)||($.eventName=PutGroupPolicy)||($.eventName=PutRolePolicy)||($.eventName=PutUserPolicy)||($.eventName=CreatePolicy)||($.eventName=DeletePolicy)||($.eventName=CreatePolicyVersion)||($.eventName=DeletePolicyVersion)||($.eventName=AttachRolePolicy)||($.eventName=DetachRolePolicy)||($.eventName=AttachUserPolicy)||($.eventName=DetachUserPolicy)||($.eventName=AttachGroupPolicy)||($.eventName=DetachGroupPolicy)}')  # noqa

    def audit_3_5(self):
        """3.5 Ensure a log metric filter and alarm exist for CloudTrail configuration changes (Scored)"""
        self.assert_alarm("CloudTrailConfigChanged",
                          '{ ($.eventName = CreateTrail) || ($.eventName = UpdateTrail) || ($.eventName = DeleteTrail) || ($.eventName = StartLogging) || ($.eventName = StopLogging) }')  # noqa

    def audit_3_6(self):
        """3.6 Ensure a log metric filter and alarm exist for AWS Management Console authentication failures (Scored)"""
        self.assert_alarm("ConsoleLoginFailed",
                          '{ ($.eventName = ConsoleLogin) && ($.errorMessage = \"Failed authentication\") }')

    def audit_3_7(self):
        """3.7 Ensure a log metric filter and alarm exist for disabling or scheduled deletion of customer created CMKs (Scored)"""  # noqa
        self.assert_alarm("KMSCMKDisabled",
                          '{($.eventSource = kms.amazonaws.com) && (($.eventName=DisableKey)||($.eventName=ScheduleKeyDeletion))}')  # noqa

    def audit_3_8(self):
        """3.8 Ensure a log metric filter and alarm exist for S3 bucket policy changes (Scored)"""
        self.assert_alarm("S3BucketPolicyChanged",
                          '{ ($.eventSource = s3.amazonaws.com) && (($.eventName = PutBucketAcl) || ($.eventName = PutBucketPolicy) || ($.eventName = PutBucketCors) || ($.eventName = PutBucketLifecycle) || ($.eventName = PutBucketReplication) || ($.eventName = DeleteBucketPolicy) || ($.eventName = DeleteBucketCors) || ($.eventName = DeleteBucketLifecycle) || ($.eventName = DeleteBucketReplication)) }')  # noqa

    def audit_3_9(self):
        """3.9 Ensure a log metric filter and alarm exist for AWS Config configuration changes (Scored)"""
        self.assert_alarm("AWSConfigServiceChanged",
                          '{($.eventSource = config.amazonaws.com) && (($.eventName=StopConfigurationRecorder)||($.eventName=DeleteDeliveryChannel)||($.eventName=PutDeliveryChannel)||($.eventName=PutConfigurationRecorder))}')  # noqa

    def audit_3_10(self):
        """3.10 Ensure a log metric filter and alarm exist for security group changes (Scored)"""
        self.assert_alarm("EC2SecurityGroupChanged",
                          '{ ($.eventName = AuthorizeSecurityGroupIngress) || ($.eventName = AuthorizeSecurityGroupEgress) || ($.eventName = RevokeSecurityGroupIngress) || ($.eventName = RevokeSecurityGroupEgress) || ($.eventName = CreateSecurityGroup) || ($.eventName = DeleteSecurityGroup)}')  # noqa

    def audit_3_11(self):
        """3.11 Ensure a log metric filter and alarm exist for changes to Network Access Control Lists (NACL) (Scored)"""  # noqa
        self.assert_alarm("EC2NACLChanged",
                          '{ ($.eventName = CreateNetworkAcl) || ($.eventName = CreateNetworkAclEntry) || ($.eventName = DeleteNetworkAcl) || ($.eventName = DeleteNetworkAclEntry) || ($.eventName = ReplaceNetworkAclEntry) || ($.eventName = ReplaceNetworkAclAssociation) }')  # noqa

    def audit_3_12(self):
        """3.12 Ensure a log metric filter and alarm exist for changes to network gateways (Scored)"""
        self.assert_alarm("EC2NetworkGatewayChanged",
                          '{ ($.eventName = CreateCustomerGateway) || ($.eventName = DeleteCustomerGateway) || ($.eventName = AttachInternetGateway) || ($.eventName = CreateInternetGateway) || ($.eventName = DeleteInternetGateway) || ($.eventName = DetachInternetGateway) }')  # noqa

    def audit_3_13(self):
        """3.13 Ensure a log metric filter and alarm exist for route table changes (Scored)"""
        self.assert_alarm("EC2RouteTableChanged",
                          '{ ($.eventName = CreateRoute) || ($.eventName = CreateRouteTable) || ($.eventName = ReplaceRoute) || ($.eventName = ReplaceRouteTableAssociation) || ($.eventName = DeleteRouteTable) || ($.eventName = DeleteRoute) || ($.eventName = DisassociateRouteTable) }')  # noqa

    def audit_3_14(self):
        """3.14 Ensure a log metric filter and alarm exist for VPC changes (Scored)"""
        self.assert_alarm("EC2VPCChanged",
                          '{ ($.eventName = CreateVpc) || ($.eventName = DeleteVpc) || ($.eventName = ModifyVpcAttribute) || ($.eventName = AcceptVpcPeeringConnection) || ($.eventName = CreateVpcPeeringConnection) || ($.eventName = DeleteVpcPeeringConnection) || ($.eventName = RejectVpcPeeringConnection) || ($.eventName = AttachClassicLinkVpc) || ($.eventName = DetachClassicLinkVpc) || ($.eventName = DisableVpcClassicLink) || ($.eventName = EnableVpcClassicLink) }')  # noqa

    def audit_3_15(self):
        """3.15 Ensure security contact information is registered (Scored)"""
        raise NotImplementedError()

    def audit_3_16(self):
        """3.16 Ensure appropriate subscribers to each SNS topic (Not Scored)"""
        raise NotImplementedError()

    def audit_4_1(self):
        """4.1 Ensure no security groups allow ingress from 0.0.0.0/0 to port 22 (Scored)"""
        raise NotImplementedError()

    def audit_4_2(self):
        """4.2 Ensure no security groups allow ingress from 0.0.0.0/0 to port 3389 (Scored)"""
        raise NotImplementedError()

    def audit_4_3(self):
        """4.3 Ensure VPC Flow Logging is Enabled in all Applicable Regions (Scored)"""
        raise NotImplementedError()

    def audit_4_4(self):
        """4.4 Ensure the default security group restricts all traffic (Scored)"""
        raise NotImplementedError()

def audit(args):
    auditor = Auditor()
    auditor.__dict__.update(vars(args))
    table = []
    for method_name in natural_sort(dir(auditor)):
        if method_name.startswith("audit"):
            method = getattr(auditor, method_name)
            try:
                method()
                table.append([GREEN("PASS"), method.__doc__])
            except Exception as e:
                logger.warn("%s: %s", method, e)
                table.append([RED("FAIL"), method.__doc__])
    # TODO: WHITE("NO TEST")
    page_output(format_table(table, column_names=["Result", "Test"], max_col_width=120))

parser = register_parser(audit, help='Generate a security report using the CIS AWS Foundations Benchmark')
parser.add_argument('--email', help="Administrative contact email")
