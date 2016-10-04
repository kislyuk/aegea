#!/usr/bin/env python
# coding: utf-8

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, unittest, collections, itertools, copy, re, subprocess, importlib, pkgutil, json, datetime

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, pkg_root)
import aegea
from aegea.util import Timestamp
from aegea.util.aws import (resolve_ami, SpotFleetBuilder, IAMPolicyBuilder, locate_ubuntu_ami, get_ondemand_price_usd,
                            ARN, DNSZone, get_user_data)
from aegea.util.exceptions import AegeaException
from aegea.util.compat import USING_PYTHON2, str
from aegea.util.git import private_submodules

for importer, modname, is_pkg in pkgutil.iter_modules(aegea.__path__):
    importlib.import_module((aegea.__package__ or "aegea") + "." + modname)

class TestAegea(unittest.TestCase):
    SubprocessResult = collections.namedtuple("SubprocessResult", "stdout stderr returncode")
    def setUp(self):
        pass

    def call(self, cmd, **kwargs):
        print('Running "{}"'.format(cmd), file=sys.stderr)
        expect = kwargs.pop("expect", [dict(return_codes=[os.EX_OK], stdout=None, stderr=None)])
        if isinstance(cmd, list) and cmd[0] == "aegea":
            cmd[0] = subprocess.check_output(["which", cmd[0]]).decode().strip()
            cmd = ["coverage", "run"] + cmd
        elif cmd.startswith("aegea"):
            cmd = cmd.split(" ", 1)
            cmd[0] = subprocess.check_output(["which", cmd[0]]).decode().strip()
            cmd = "coverage run " + " ".join(cmd)
        process = subprocess.Popen(cmd, stdin=kwargs.get("stdin", subprocess.PIPE), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, **kwargs)
        out, err = process.communicate()
        return_code = process.poll()
        out = out.decode(sys.stdin.encoding)
        err = err.decode(sys.stdin.encoding)
        def match(return_code, out, err, expected):
            exit_ok = return_code in expected["return_codes"]
            stdout_ok = re.search(expected.get("stdout") or "", out)
            stderr_ok = re.search(expected.get("stderr") or "", err)
            return exit_ok and stdout_ok and stderr_ok
        if not any(match(return_code, out, err, exp) for exp in expect):
            print(err)
            e = subprocess.CalledProcessError(return_code, cmd, output=out)
            e.stdout, e.stderr = out, err
            raise e
        return self.SubprocessResult(out, err, return_code)

    def test_basic_aegea_commands(self):
        self.call(["aegea"], expect=[dict(return_codes=[1])])
        self.call(["aegea", "--help"])
        self.call(["aegea", "--version"])
        self.call(["aegea", "pricing"])
        self.call(["aegea", "ls", "-w9"])
        self.call(["aegea", "ssh", "nonexistent_instance"],
                  expect=[dict(return_codes=[1, os.EX_SOFTWARE], stderr="AegeaException: Could not resolve")])
        instance_id = json.loads(self.call(["aegea", "ls", "--json"]).stdout)[0]["id"]
        for subcommand in aegea.parser._actions[-1].choices:
            expect = [dict(return_codes=[os.EX_OK]),
                      dict(return_codes=[1, os.EX_SOFTWARE],
                           stderr="(UnauthorizedOperation|AccessDenied|DryRunOperation)")]
            args = []
            if subcommand in ("ssh", "put_alarm"):
                args += ["--help"]
            elif subcommand == "console":
                args += [instance_id]
            elif subcommand in ("start", "stop", "reboot", "terminate", "rename"):
                args += [instance_id, instance_id, "--dry-run"]
            elif subcommand == "grep":
                args += ["--help"] if USING_PYTHON2 else ["error", "syslog", "--start-time=-2h", "--end-time=-5m"]
                expect.append(dict(return_codes=[os.EX_DATAERR]))
            elif subcommand in ("launch", "build_ami"):
                args += ["--no-verify-ssh-key-pem-file", "--dry-run", "test"]
            elif subcommand == "rm":
                args += [resolve_ami()]
            elif subcommand in ("secrets", "rds", "elb", "flow_logs", "deploy"):
                args += ["ls"]
            elif subcommand == "pricing":
                args += ["AmazonEC2", "--json"]
            elif subcommand == "billing":
                args += ["--min-cost", "0.1"]
                if "AWS_DETAILED_BILLING_REPORTS_BUCKET" in os.environ:
                    args += ["--detailed-billing-reports-bucket", os.environ["AWS_DETAILED_BILLING_REPORTS_BUCKET"]]
            elif subcommand == "ls":
                args += ["--filter", "state=running"]
            elif subcommand == "tag":
                args += [instance_id, "test=test test2=test"]
            elif subcommand == "untag":
                args += [instance_id, "test test2"]
            self.call(["aegea", subcommand] + args, expect=expect)

    def test_dry_run_commands(self):
        unauthorized_ok = [dict(return_codes=[os.EX_OK]),
                           dict(return_codes=[1, os.EX_SOFTWARE], stderr="UnauthorizedOperation")]
        self.call("aegea launch unittest --dry-run --no-verify-ssh-key-pem-file",
                  shell=True, expect=unauthorized_ok)
        self.call("aegea launch unittest --dry-run --spot --no-verify-ssh-key-pem-file",
                  shell=True, expect=unauthorized_ok)
        self.call("aegea launch unittest --dry-run --duration-hours 1 --no-verify-ssh-key-pem-file",
                  shell=True, expect=unauthorized_ok)
        self.call("aegea launch unittest --duration 0.5 --min-mem 6 --cores 2 --dry-run --no-verify --client-token t",
                  shell=True, expect=unauthorized_ok)
        self.call("aegea build_ami i --dry-run --no-verify-ssh-key-pem-file",
                  shell=True, expect=unauthorized_ok)

    def test_spot_fleet_builder(self):
        builder = SpotFleetBuilder(launch_spec={})
        self.assertEqual(set(spec["InstanceType"] for spec in builder.launch_specs()),
                         {"c3.large", "c4.large", "m3.large", "m4.large", "m3.medium"})
        self.assertEqual(set(spec["InstanceType"] for spec in builder.launch_specs(max_overprovision=4)),
                         {"c3.large", "c4.large", "m3.large", "m4.large", "m3.medium", "m4.xlarge", "c3.xlarge",
                          "c4.xlarge", "m3.xlarge"})
        with self.assertRaises(AegeaException):
            builder = SpotFleetBuilder(launch_spec={}, min_cores_per_instance=16)
        builder = SpotFleetBuilder(launch_spec={}, cores=16, min_cores_per_instance=16)
        self.assertEqual(set(spec["InstanceType"] for spec in builder.launch_specs()),
                         {'c3.4xlarge', 'c4.8xlarge', 'c4.4xlarge', 'm4.10xlarge', 'c3.8xlarge', 'm4.4xlarge'})
        builder = SpotFleetBuilder(launch_spec={}, cores=16, min_cores_per_instance=16, min_mem_per_core_gb=6)
        self.assertEqual(set(spec["InstanceType"] for spec in builder.launch_specs()),
                         {'r3.4xlarge', 'r3.8xlarge', 'd2.4xlarge', 'i2.8xlarge', 'd2.8xlarge', 'i2.4xlarge'})
        builder = SpotFleetBuilder(launch_spec={}, cores=32, min_cores_per_instance=32, min_mem_per_core_gb=6)
        self.assertEqual(set(spec["InstanceType"] for spec in builder.launch_specs()),
                         {'r3.8xlarge', 'i2.8xlarge', 'd2.8xlarge'})
        # TODO: This will need updating when X1s come out
        builder = SpotFleetBuilder(launch_spec={}, cores=32, min_cores_per_instance=16, min_mem_per_core_gb=8)
        self.assertFalse(set(spec["InstanceType"] for spec in builder.launch_specs()))
        builder = SpotFleetBuilder(launch_spec={}, cores=4, gpus_per_instance=1)
        self.assertEqual(set(spec["InstanceType"] for spec in builder.launch_specs()), {"g2.2xlarge"})
        builder = SpotFleetBuilder(launch_spec={}, cores=16, gpus_per_instance=4, client_token="t")
        self.assertEqual(set(spec["InstanceType"] for spec in builder.launch_specs()), {"g2.8xlarge"})
        builder = SpotFleetBuilder(launch_spec={}, min_ephemeral_storage_gb=1)
        self.assertEqual(set(spec["InstanceType"] for spec in builder.launch_specs()),
                         {'m3.large', 'c3.large', 'm3.medium'})

    def test_iam_policy_builder(self):
        policy = IAMPolicyBuilder(principal="arn:aws:iam::account-id:user/foo", action="s3:GetObject")
        policy.add_action("s3:PutObject")
        policy.add_resource("arn:aws:s3:::examplebucket")
        policy.add_statement(effect="Deny")
        expected = {"Version": "2012-10-17",
                    "Statement": [{"Action": ["s3:GetObject", "s3:PutObject"],
                                   "Resource": ["arn:aws:s3:::examplebucket"],
                                   "Effect": "Allow",
                                   "Principal": {"AWS": "arn:aws:iam::account-id:user/foo"}},
                                  {"Action": [], "Effect": "Deny"}]}
        self.assertEqual(json.loads(str(policy)), expected)

    def test_aws_utils(self):
        self.assertTrue(isinstance(get_ondemand_price_usd("us-east-1", "t2.micro"), str))
        self.assertEquals(str(ARN(region="", account_id="")), "arn:aws::::")
        self.assertTrue(str(ARN()).startswith("arn:aws:"))
        self.assertEquals(str(ARN("arn:aws:foo:bar:xyz:zzt")), "arn:aws:foo:bar:xyz:zzt")
        self.assertEquals(str(ARN("arn:aws:a:b:c:d", service="x", region="us-west-1", account_id="1", resource="2")),
                          "arn:aws:x:us-west-1:1:2")
        with self.assertRaises(AegeaException):
            DNSZone(use_unique_private_zone=False)
        get_user_data(commands=["ls"], packages=["foo"], files=["bar"])

    def test_locate_ubuntu_ami(self):
        self.assertTrue(locate_ubuntu_ami("com.ubuntu.cloud:server:16.04:amd64", "us-east-1").startswith("ami-"))
        ami = locate_ubuntu_ami(product="com.ubuntu.cloud:server:16.04:amd64", channel="releases", stream="released",
                                region="us-west-2")
        self.assertTrue(ami.startswith("ami-"))

    def test_date_utils(self):
        with self.assertRaises(TypeError):
            Timestamp()
        self.assertEqual(str(Timestamp(12345)), "1970-01-01 00:00:12")
        self.assertEqual(str(Timestamp(1466533609099)), "2016-06-21 18:26:49")
        for valid_input in "5s", "-5s", "5m", "-5m", "5h", "-5h", "5d", "-5d", "5w", "-5w", "2016-06-21 18:26:49":
            self.assertTrue(isinstance(Timestamp(valid_input), datetime.datetime))
        for invalid_input in None, "", {}, []:
            with self.assertRaises(Exception):
                print(Timestamp(invalid_input))

    @unittest.skipIf(USING_PYTHON2, "requires Python 3 dependencies")
    def test_deploy_utils(self):
        self.call(os.path.join(pkg_root, "aegea", "rootfs.skel", "usr", "bin", "aegea-deploy-pilot"),
                  expect=[dict(return_codes=[2], stderr="argument --repo is required")])
        self.call(os.path.join(pkg_root, "aegea", "rootfs.skel", "usr", "bin", "aegea-get-secret"),
                  expect=[dict(return_codes=[2])])
        self.call(os.path.join(pkg_root, "aegea", "rootfs.skel", "usr", "bin", "aegea-git-ssh-helper"),
                  expect=[dict(return_codes=[2])])

    def test_secrets(self):
        unauthorized_ok = [dict(return_codes=[os.EX_OK]),
                           dict(return_codes=[1, os.EX_SOFTWARE], stderr="(AccessDenied|NoSuchKey)")]
        self.call("test_secret=test aegea secrets put test_secret --iam-role aegea.launch",
                  shell=True, expect=unauthorized_ok)
        self.call("aegea secrets put test_secret --generate-ssh-key --iam-role aegea.launch",
                  shell=True, expect=unauthorized_ok)
        self.call("aegea secrets ls", shell=True, expect=unauthorized_ok)
        self.call("aegea secrets ls --json", shell=True, expect=unauthorized_ok)
        self.call("aegea secrets get test_secret --iam-role aegea.launch", shell=True, expect=unauthorized_ok)
        self.call("aegea secrets delete test_secret --iam-role aegea.launch", shell=True, expect=unauthorized_ok)

    @unittest.skipUnless("GH_AUTH" in os.environ, "requires GitHub credentials")
    def test_git_utils(self):
        for submodule in private_submodules("git@github.com:ansible/ansible.git"):
            print(submodule)

if __name__ == '__main__':
    unittest.main()
