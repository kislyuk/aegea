"""
This AWS Lambda function, installed by the `aegea batch` command, subscribes to AWS Batch job state change events and
records job descriptions, so they can be referenced later when they disappear from the Batch API.

Fields like "command" and "environment" are redacted to avoid storing potentially sensitive information.
"""
import json
import os

import boto3
from chalice import Chalice

s3 = boto3.resource("s3")

app = Chalice(app_name="aegea-batch-events")

@app.on_cw_event({"source": ["aws.batch"]})
def process_batch_event(event):
    job_id = event.detail["jobId"]
    if "container" in event.detail:
        for redact_field in "command", "environment", "volumes", "mountPoints":
            event.detail["container"][redact_field] = None
    account_id = event.detail["jobDefinition"].split(":")[4]
    bucket = s3.Bucket("aegea-batch-jobs-{}".format(account_id))
    bucket.put_object(Key="job_descriptions/{}".format(job_id), Body=json.dumps(event.detail).encode())
