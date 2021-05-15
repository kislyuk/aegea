from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, io, stat, shutil, platform, subprocess, tempfile, zipfile, time

import boto3

from ... import logger, config
from .. import Timestamp
from ..exceptions import AegeaException
from . import resolve_instance_id, resources, clients, ARN, paginate
from .logs import CloudwatchLogReader

sm_plugin_bucket = "session-manager-downloads"

def download_session_manager_plugin_macos(target_path):
    sm_archive = io.BytesIO()
    clients.s3.download_fileobj(sm_plugin_bucket, "plugin/latest/mac/sessionmanager-bundle.zip", sm_archive)
    with zipfile.ZipFile(sm_archive) as zf, open(target_path, "wb") as fh:
        fh.write(zf.read("sessionmanager-bundle/bin/session-manager-plugin"))

def download_session_manager_plugin_linux(target_path, pkg_format="deb"):
    assert pkg_format in {"deb", "rpm"}
    if pkg_format == "deb":
        sm_plugin_key = "plugin/latest/ubuntu_64bit/session-manager-plugin.deb"
    else:
        sm_plugin_key = "plugin/latest/linux_64bit/session-manager-plugin.rpm"
    with tempfile.TemporaryDirectory() as td:
        sm_archive_path = os.path.join(td, os.path.basename(sm_plugin_key))
        clients.s3.download_file(sm_plugin_bucket, sm_plugin_key, sm_archive_path)
        if pkg_format == "deb":
            subprocess.check_call(["dpkg", "-x", sm_archive_path, td])
        elif pkg_format == "rpm":
            command = "rpm2cpio '{}' | cpio --extract --make-directories --directory '{}'"
            subprocess.check_call(command.format(sm_archive_path, td), shell=True)
        shutil.move(os.path.join(td, "usr/local/sessionmanagerplugin/bin/session-manager-plugin"), target_path)

def ensure_session_manager_plugin():
    session_manager_dir = os.path.join(config.user_config_dir, "bin")
    PATH = os.environ.get("PATH", "") + ":" + session_manager_dir
    if shutil.which("session-manager-plugin", path=PATH):
        subprocess.check_call(["session-manager-plugin"], env=dict(os.environ, PATH=PATH))
    else:
        os.makedirs(session_manager_dir, exist_ok=True)
        target_path = os.path.join(session_manager_dir, "session-manager-plugin")
        if platform.system() == "Darwin":
            download_session_manager_plugin_macos(target_path=target_path)
        elif "Ubuntu" in subprocess.run(["uname", "-a"], capture_output=True).stdout.decode():  # type: ignore
            download_session_manager_plugin_linux(target_path=target_path)
        else:
            download_session_manager_plugin_linux(target_path=target_path, pkg_format="rpm")
        os.chmod(target_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        subprocess.check_call(["session-manager-plugin"], env=dict(os.environ, PATH=PATH))
    return shutil.which("session-manager-plugin", path=PATH)

def run_command(command, instance_ids=None, targets=None, timeout=900):
    """
    Sends a command to specified instances using AWS Systems Manager. Waits for the command to complete.
    Queries CloudWatch Logs for command stdout and stderr output, printing it to the terminal.
    Raises an exception if the command exits with a non-zero exit status.

    See https://docs.aws.amazon.com/systems-manager/latest/userguide/execute-remote-commands.html for details.
    """
    send_command_args = dict(DocumentName="AWS-RunShellScript",
                             CloudWatchOutputConfig=dict(CloudWatchOutputEnabled=True, CloudWatchLogGroupName=__name__),
                             Parameters=dict(commands=[command]),
                             TimeoutSeconds=timeout,
                             Comment="Started by {}".format(__name__))
    if instance_ids:
        send_command_args.update(InstanceIds=instance_ids)
    if targets:
        send_command_args.update(Targets=targets)
    log_readers, stdout, command_id = {}, [], None  # type: ignore
    try:
        command_id = clients.ssm.send_command(**send_command_args)["Command"]["CommandId"]
        while True:
            statuses = []
            for invocation in paginate(clients.ssm.get_paginator("list_command_invocations"), CommandId=command_id):
                if invocation["Status"] in {"TimedOut", "Cancelled", "Failed"}:
                    if invocation["Status"] in {"TimedOut", "Cancelled"}:
                        logger.error("SSM command failed: {}".format(invocation["StatusDetails"]))
                    raise AegeaException("SSM command failed: {}".format(invocation))
                statuses.append(invocation["Status"])
                if invocation["Status"] not in {"InProgress", "Success"}:
                    continue
                for stream in "stdout", "stderr":
                    log_stream_name = "{}/{}/aws-runShellScript/{}".format(command_id, invocation["InstanceId"], stream)
                    if log_stream_name not in log_readers:
                        log_readers[log_stream_name] = CloudwatchLogReader(log_group_name=__name__,
                                                                           log_stream_name=log_stream_name)
                    try:
                        for event in log_readers[log_stream_name]:
                            print(event["message"], file=getattr(sys, stream))
                    except clients.logs.exceptions.ResourceNotFoundException:
                        logger.debug("No logs for %s", log_stream_name)
                sys.stderr.write(".")
                sys.stderr.flush()
            if statuses and all(s == "Success" for s in statuses):
                break
            time.sleep(1)

        for invocation in paginate(clients.ssm.get_paginator("list_command_invocations"), CommandId=command_id):
            log_stream_name = "{}/{}/aws-runShellScript/stdout".format(command_id, invocation["InstanceId"])
            try:
                for event in CloudwatchLogReader(log_group_name=__name__, log_stream_name=log_stream_name):
                    stdout.append(event["message"])
            except clients.logs.exceptions.ResourceNotFoundException:
                logger.debug("No logs for %s", log_stream_name)
    except KeyboardInterrupt:
        if command_id:
            logger.error("Cancelling SSM command")
            clients.ssm.cancel_command(CommandId=command_id)
            logger.error("SSM command cancelled")
        raise
    logger.info("SSM command completed")
    return stdout
