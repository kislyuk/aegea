#!/usr/bin/env python
# coding: utf-8

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, unittest, argparse, collections, copy, re, subprocess, importlib, pkgutil, json, datetime, glob, time

pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))  # noqa
sys.path.insert(0, pkg_root)  # noqa
import aegea, aegea.util.aws.dns
from aegea.util import Timestamp
from aegea.util.cloudinit import get_user_data
from aegea.util.aws import (resolve_ami, locate_ami, get_ondemand_price_usd, ARN,
                            get_public_ip_ranges, ensure_s3_bucket, encode_tags, decode_tags, filter_by_tags,
                            clients, resources, get_bdm, get_iam_role_for_instance, make_waiter)
from aegea.util.aws.iam import IAMPolicyBuilder
from aegea.util.aws.batch import ensure_job_definition
from aegea.util.aws.spot import SpotFleetBuilder
from aegea.util.compat import USING_PYTHON2, str
from aegea.util.exceptions import AegeaException
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

    @unittest.skipIf("GITHUB_ACTIONS" in os.environ, "suffers from rate limits in CI")
    def test_pricing_commands(self):
        self.call(["aegea", "pricing", "AmazonEC2"])
        self.call(["aegea", "pricing", "AmazonRDS"])

    def test_basic_aegea_commands(self):
        self.call(["aegea"], expect=[dict(return_codes=[1])])
        self.call(["aegea", "--help"])
        self.call(["aegea", "--version"])
        self.call(["aegea", "pricing"])
        self.call(["aegea", "ls", "-w9"])
        for ssh_cmd in "ssh", "scp":
            self.call(["aegea", ssh_cmd, "nonexistent_instance:"],
                      expect=[dict(return_codes=[1, os.EX_SOFTWARE], stderr="AegeaException: Could not resolve")])
        instance_id = json.loads(self.call(["aegea", "ls", "--json"]).stdout)[0]["id"]
        for subcommand in aegea.parser._actions[-1].choices:
            expect = [dict(return_codes=[os.EX_OK]),
                      dict(return_codes=[1, os.EX_SOFTWARE],
                           stderr="(UnauthorizedOperation|AccessDenied|DryRunOperation)")]
            args = []
            if subcommand in ("ssh", "scp", "run", "put-alarm", "batch", "rm"):
                args += ["--help"]
            elif subcommand == "top" and sys.version_info < (3, 5):
                continue  # concurrent.futures.ThreadPoolExecutor thread count autotune introduced in 3.5
            elif "_" in subcommand:
                continue
            elif subcommand == "build-docker-image":
                args += ["--dry-run", "docker-example"]
            elif subcommand == "console":
                args += [instance_id]
            elif subcommand == "iam":
                args += ["users"]
            elif subcommand in ("start", "stop", "reboot", "terminate", "rename"):
                args += [instance_id, instance_id, "--dry-run"]
            elif subcommand in ("grep", "filter"):
                args += ["--help"] if USING_PYTHON2 else ["error", "syslog", "--start-time=-2h", "--end-time=-5m"]
                expect.append(dict(return_codes=[os.EX_DATAERR]))
            elif subcommand == "launch":
                args += ["--no-verify-ssh-key-pem-file", "--dry-run", "test", "--ubuntu-linux-ami"]
            elif subcommand == "build-ami":
                args += ["--no-verify-ssh-key-pem-file", "--dry-run", "test"]
            elif subcommand == "s3":
                args += ["buckets"]
            elif subcommand in ("secrets", "rds", "elb", "flow-logs", "deploy", "zones", "ebs", "efs",
                                "ecr", "lambda", "configure", "sfn"):
                args += ["ls"]
            elif subcommand == "pricing":
                args += ["AmazonS3", "--json"]
            elif subcommand == "billing":
                continue  # FIXME
                args += ["ls", "--min-cost", "0.1"]
                if "AWS_BILLING_REPORTS_BUCKET" in os.environ:
                    args += ["--billing-reports-bucket", os.environ["AWS_BILLING_REPORTS_BUCKET"]]
            elif subcommand == "ls":
                args += ["--filter", "state=running"]
            elif subcommand == "tag":
                args += [instance_id, "test=test test2=test"]
            elif subcommand == "untag":
                args += [instance_id, "test test2"]
            elif subcommand == "ecs":
                args += ["clusters"]
            self.call(["aegea", subcommand] + args, expect=expect)

    def test_dry_run_commands(self):
        unauthorized_ok = [dict(return_codes=[os.EX_OK]),
                           dict(return_codes=[1, os.EX_SOFTWARE], stderr="UnauthorizedOperation")]
        self.call("aegea launch unittest --dry-run --storage /x=512 /y=1024 --no-verify --ubuntu-linux-ami",
                  shell=True, expect=unauthorized_ok)
        self.call("aegea launch unittest --dry-run --no-verify-ssh-key-pem-file --ubuntu-linux-ami",
                  shell=True, expect=unauthorized_ok)
        self.call("aegea launch unittest --dry-run --spot --no-verify-ssh-key-pem-file --amazon-linux-ami",
                  shell=True, expect=unauthorized_ok)
        self.call("aegea launch unittest --dry-run --duration-hours 1 --no-verify-ssh-key-pem-file --amazon-linux-ami",
                  shell=True, expect=unauthorized_ok)
        self.call(("aegea launch unittest --duration 0.5 --min-mem 6 --cores 2 --dry-run --no-verify --client-token t "
                   "--amazon-linux-ami"),
                  shell=True, expect=unauthorized_ok)
        self.call("aegea build-ami i --dry-run --no-verify-ssh-key-pem-file",
                  shell=True, expect=unauthorized_ok)

        self.call("aegea batch submit --command pwd --dry-run", shell=True)
        self.call("echo pwd > run.sh && aegea batch submit --execute run.sh --dry-run", shell=True)
        self.call("aegea batch submit --wdl '{}' --dry-run".format(__file__.replace(".py", ".wdl")), shell=True)

        self.call("aegea ecs run --command pwd --dry-run", shell=True)

    @unittest.skipIf(sys.version_info < (3, 8), "Skipping test which is prone to rate limiting")
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
                         {'r3.4xlarge', 'r3.8xlarge', 'd2.4xlarge', 'i2.8xlarge', 'd2.8xlarge', 'i2.4xlarge',
                          'i3.4xlarge', 'r4.4xlarge', 'i3.8xlarge', 'r4.8xlarge'})
        builder = SpotFleetBuilder(launch_spec={}, cores=32, min_cores_per_instance=32, min_mem_per_core_gb=6)
        self.assertEqual(set(spec["InstanceType"] for spec in builder.launch_specs()),
                         {'r3.8xlarge', 'i2.8xlarge', 'd2.8xlarge', 'i3.8xlarge', 'r4.16xlarge', 'i3.16xlarge',
                          'r4.8xlarge'})
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

        policy = IAMPolicyBuilder(expected)
        self.assertEqual(json.loads(str(policy)), expected)
        self.assertEqual(len(policy.policy["Statement"]), 2)
        policy.add_statement(principal="arn:aws:iam::account-id:user/foo",
                             action=["s3:GetObject", "s3:PutObject"],
                             resource=["arn:aws:s3:::examplebucket"])
        self.assertEqual(len(policy.policy["Statement"]), 2)
        policy.add_statement(principal="arn:aws:iam::account-id:user/foo",
                             action="s3:GetObject",
                             resource="arn:aws:s3:::examplebucket")
        self.assertEqual(len(policy.policy["Statement"]), 2)
        policy.add_statement(principal="arn:aws:iam::account-id:user/foo",
                             action=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                             resource=["arn:aws:s3:::examplebucket"])
        self.assertEqual(len(policy.policy["Statement"]), 3)
        policy.add_statement(principal="arn:aws:iam::account-id:user/foo",
                             action=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                             resource=["arn:aws:s3:::examplebucket"])
        self.assertEqual(len(policy.policy["Statement"]), 3)
        policy.add_statement(principal="arn:aws:iam::account-id:user/foo",
                             action=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                             resource=["arn:aws:s3:::examplebucket2"])
        self.assertEqual(len(policy.policy["Statement"]), 4)
        policy.add_statement(principal="arn:aws:iam::account-id:user/bar",
                             action=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                             resource=["arn:aws:s3:::examplebucket2"])
        self.assertEqual(len(policy.policy["Statement"]), 5)
        policy.add_statement(effect="Deny")
        self.assertEqual(len(policy.policy["Statement"]), 6)
        policy.add_statement()
        self.assertEqual(len(policy.policy["Statement"]), 7)

    def test_aws_utils(self):
        if not USING_PYTHON2:
            self.assertTrue(isinstance(get_ondemand_price_usd("us-east-1", "t2.micro"), str))
        self.assertEquals(str(ARN(region="", account_id="")), "arn:aws::::")
        self.assertTrue(str(ARN()).startswith("arn:aws:"))
        self.assertEquals(str(ARN("arn:aws:foo:bar:xyz:zzt")), "arn:aws:foo:bar:xyz:zzt")
        self.assertEquals(str(ARN("arn:aws:a:b:c:d", service="x", region="us-west-1", account_id="1", resource="2")),
                          "arn:aws:x:us-west-1:1:2")
        get_user_data(commands=["ls"], packages=["foo"], files=["bar"])

        # Test serialization of tweak.Config objects
        from tweak import Config
        d = dict(x={}, y=[1, 2])
        c = Config(save_on_exit=False, _parent=self, _data=d)

        aegea.util.aws.dns.config = c
        c.dns = {}
        c.dns.private_zone = "aegea.test"
        with self.assertRaises(AegeaException):
            aegea.util.aws.dns.DNSZone(create_default_private_zone=False)
        with self.assertRaises(AegeaException):
            aegea.util.aws.dns.DNSZone(zone_name="foobar", create_default_private_zone=False)

        self.assertEquals(get_user_data(foo=c, bar=2), get_user_data(bar=2, foo=c))
        # ensure_s3_bucket()
        self.assertEqual(encode_tags(["foo=bar"]), [{'Key': 'foo', 'Value': 'bar'}])
        self.assertEqual(encode_tags(dict(foo="bar")), [{'Key': 'foo', 'Value': 'bar'}])
        self.assertEqual(decode_tags([dict(Key="foo", Value="bar")]), {'foo': 'bar'})
        filter_by_tags(resources.ec2.instances, Name="")
        self.assertEqual(get_bdm(),
                         [dict(VirtualName="ephemeral" + str(i), DeviceName="xvd" + chr(ord("b") + i))
                          for i in range(12)])
        # for instance in resources.ec2.instances.all():
        #     get_iam_role_for_instance(instance.id)
        #     break
        make_waiter(clients.efs.describe_file_systems, "FileSystems[].LifeCycleState", "available", "pathAny")

    def test_locate_ami(self):
        self.assertTrue(locate_ami("Ubuntu", "20.04", "amd64").startswith("ami-"))
        self.assertTrue(locate_ami("Ubuntu", "20.04", "arm64").startswith("ami-"))
        self.assertTrue(locate_ami("Amazon Linux", "2", "x86_64").startswith("ami-"))
        self.assertTrue(locate_ami("Amazon Linux", "2", "arm64").startswith("ami-"))

    def test_ip_ranges(self):
        get_public_ip_ranges()
        get_public_ip_ranges(region="us-east-1", service="ROUTE53_HEALTHCHECKS")

    def test_date_utils(self):
        with self.assertRaises(TypeError):
            Timestamp()
        self.assertEqual(str(Timestamp(12345)), "1970-01-01 00:00:12+00:00")
        self.assertEqual(str(Timestamp(1466533609099)), "2016-06-21 18:26:49+00:00")
        for valid_input in "5s", "-5s", "5m", "-5m", "5h", "-5h", "5d", "-5d", "5w", "-5w", "2016-06-21 18:26:49":
            self.assertTrue(isinstance(Timestamp(valid_input), datetime.datetime))
        for invalid_input in None, "", {}, []:
            with self.assertRaises(Exception):
                print(Timestamp(invalid_input))

    @unittest.skipIf(USING_PYTHON2, "requires Python 3 dependencies")
    def test_deploy_utils(self):
        deploy_utils_bindir = os.path.join(pkg_root, "aegea", "rootfs.skel", "usr", "bin")
        for script in glob.glob(deploy_utils_bindir + "/aegea*"):
            self.call([script, "--help"], expect=[dict(return_codes=[0, 1])])
        for script in "aegea-deploy-pilot", "aegea-git-ssh-helper":
            self.call(os.path.join(deploy_utils_bindir, script),
                      expect=[dict(return_codes=[2], stderr="(required|too few)")])

    @unittest.skipIf(sys.version_info[:2] != (3, 9), "Skipping test which is prone to race conditions")
    def test_secrets(self):
        unauthorized_ok = [dict(return_codes=[os.EX_OK]),
                           dict(return_codes=[1, os.EX_SOFTWARE], stderr="(AccessDenied|NoSuchKey)")]
        secret_name = "test_secret_{}".format(int(time.time()))
        self.call("{s}=test aegea secrets put {s} --iam-role aegea.launch".format(s=secret_name),
                  shell=True, expect=unauthorized_ok)
        self.call("aegea secrets put {s} --generate-ssh-key --iam-role aegea.launch".format(s=secret_name),
                  shell=True, expect=unauthorized_ok)
        self.call("aegea secrets ls", shell=True, expect=unauthorized_ok)
        self.call("aegea secrets ls --json", shell=True, expect=unauthorized_ok)
        self.call("aegea secrets get {s} --iam-role aegea.launch".format(s=secret_name), shell=True,
                  expect=unauthorized_ok)
        self.call("aegea secrets delete {s} --iam-role aegea.launch".format(s=secret_name), shell=True,
                  expect=unauthorized_ok)

    @unittest.skipIf("GITHUB_ACTIONS" in os.environ, "does not correctly run in CI")
    def test_ensure_job_definition(self):
        from aegea.batch import submit_parser
        args = submit_parser.parse_args(["--command", ""])
        args.default_job_role_iam_policies = []
        args.user, args.job_role, args.default_job_role_iam_policies = "0", "aegea.batch.worker", []
        jd1 = ensure_job_definition(args)
        jd2 = ensure_job_definition(args)
        self.assertEqual(jd1["jobDefinitionArn"], jd2["jobDefinitionArn"])

    @unittest.skipUnless("GH_AUTH" in os.environ, "requires GitHub credentials")
    def test_git_utils(self):
        for submodule in private_submodules("git@github.com:ansible/ansible.git"):
            print(submodule)

if __name__ == '__main__':
    unittest.main()
