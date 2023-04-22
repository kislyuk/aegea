Changes for v4.4.0 (2023-04-21)
===============================

-  aegea ssh: download correct SSM plugin

-  aegea launch: use t3a.micro instance type by default

-  Use Amazon Linux 2023 when specifying Amazon Linux

-  Update paramiko dependency range

-  Remove aegea deploy and git utils

Changes for v4.3.4 (2023-04-02)
===============================

-  Bump dependency versions

-  Test and documentation infrastructure improvements

Changes for v4.3.3 (2022-12-16)
===============================

-  Bump dependency versions

-  Test infrastructure improvements

Changes for v4.3.2 (2022-07-01)
===============================

launch: Add logic to set and discover Amazon Linux 2022 AMI Update
base_config.yml

Changes for v4.3.1 (2022-03-09)
===============================

-  Add default config value for build_ami.tags

Changes for v4.3.0 (2022-03-09)
===============================

-  build-ami: correctly ingest tag value from config chain

   Note that this change necessitates the use of a new syntax for the
   build_ami.tags section of the YAML config file. Instead of a list of
   strings, use a mapping.

Changes for v4.2.2 (2022-03-09)
===============================

-  build-ami: correctly ingest tag value from config chain (part 2)

Changes for v4.2.1 (2022-03-09)
===============================

-  build-ami: correctly ingest tag value from config chain

Changes for v4.2.0 (2022-01-14)
===============================

-  Bump dependency versions

-  Implement full IMDSv2 support

Changes for v4.1.2 (2021-12-23)
===============================

Add version file to gitignore

Changes for v4.1.1 (2021-12-23)
===============================

-  Use setuptools-scm to manage version

Changes for v4.1.0 (2021-12-23)
===============================

-  Begin CloudTrail log reader (aegea cloudtrail)

-  Documentation and test improvements

Changes for v4.0.3 (2021-10-11)
===============================

-  Fix loading of default value for encryption config

Changes for v4.0.2 (2021-10-11)
===============================

-  ensure_s3_bucket: Set bucket default encryption

Changes for v4.0.1 (2021-09-15)
===============================

-  ssh: Avoid using DescribeSubnets to get instance AZ

Changes for v4.0.0 (2021-09-08)
===============================

-  aegea ssh: Derive SSH user name and ID consistently. Please note:
   this change alters the algorithm that derives auto-provisioned Linux
   user IDs from IAM principal names, which can cause changes to EFS
   permissions and in other situations where UID mappings are used.

Changes for v3.9.1 (2021-08-06)
===============================

-  launch: add manage_iam option, –no-manage-iam flag to access it

Changes for v3.9.0 (2021-07-19)
===============================

-  Namespace rootfs.skel by command

-  Scan all config file dirs for rootfs.skel (#63)

-  Accommodate vpc eventual consistency

Changes for v3.8.6 (2021-07-08)
===============================

-  Allow storage and iam_role to be passed via config files

-  Only mount accessible efs shares (#61)

Changes for v3.8.5 (2021-06-28)
===============================

-  launch: fix logic for detection of existing EFS home

-  launch: correctly handle EFS home in multiple VPCs

-  launch: cancel SIR on waiter error

-  batch: deploy Lambda helper from a writable location

-  logs: set default log horizon to 24h

Changes for v3.8.4 (2021-06-17)
===============================

-  aegea ssh: Restore python 3.6 compatibility

-  Documentation improvements

Changes for v3.8.3 (2021-06-08)
===============================

-  ssh: print legible help when instance is not running

-  Add autoprovisioned user to docker group

-  Add aegea-ssh shim for vscode

-  Fix logic error in set_aws_profile

Changes for v3.8.2 (2021-06-06)
===============================

launch: select subnet for spot instances

Changes for v3.8.1 (2021-05-27)
===============================

-  Use correct ARN resource element for principal name

Changes for v3.8.0 (2021-05-27)
===============================

-  launch: use cloudinit to provision current user by default

-  ssh: use ec2-instance-connect, unbundle keymaker

-  Avoid using non-aegea AMIs by default

Changes for v3.7.3 (2021-05-17)
===============================

-  Support IPv6 in aegea VPCs

-  Use only aegea-managed or default VPCs

-  rm: support deleting subnets and vpcs

-  Sort subnets by vpc_id

-  aegea build-ami: use default image name if not supplied

Changes for v3.7.2 (2021-05-12)
===============================

-  batch: use custom log group if set

-  Drop direct dependency on awscli to facilitate awscli v2
   compatibility

Changes for v3.7.1 (2021-05-01)
===============================

-  Use SSM for locating Ubuntu AMI

Changes for v3.7.0 (2021-03-30)
===============================

-  aegea now supports arm64 instances

Changes for v3.6.57 (2021-03-19)
================================

-  Recover bdm aliases

Changes for v3.6.56 (2021-03-19)
================================

-  Enable resizing of root volume

Changes for v3.6.55 (2021-03-17)
================================

-  Fix incomplete release

Changes for v3.6.54 (2021-03-17)
================================

-  Update release script

Changes for v3.6.53 (2021-03-17)
================================

-  Add ripgrep and fd to base AMI

-  batch watch: Sleep on exit to capture more logs

Changes for v3.6.52 (2021-03-08)
================================

-  Avoid assumptions about source profile IAM usernames

Changes for v3.6.51 (2021-03-02)
================================

-  Sort ECR images by date

-  build-ami: adjust timeouts and error message

Changes for v3.6.50 (2021-02-25)
================================

-  Replace deprecated btrfs-tools with btrfs-progs

Changes for v3.6.49 (2021-02-25)
================================

-  Use c5.xlarge to build AMIs; clarify Batch error message

Changes for v3.6.48 (2021-02-25)
================================

-  aegea sfn ls: add status filter

-  Add zstd to base config

Changes for v3.6.47 (2020-12-24)
================================

-  Bump dependency versions

Changes for v3.6.46 (2020-12-24)
================================

-  aegea batch: begin fargate support

Changes for v3.6.45 (2020-12-19)
================================

-  Use subprocess instead of distro to check for Ubuntu presence (#56)

Changes for v3.6.44 (2020-10-23)
================================

-  Begin managed policy concatenator; use concatenated policy in aegea
   launch

-  Change default ecs run container image to Ubuntu 20.04 LTS

Changes for v3.6.43 (2020-09-19)
================================

-  ecs stop: fix argument reference

Changes for v3.6.42 (2020-09-15)
================================

-  Add ecs stop

-  ecs run: add tag support

Changes for v3.6.41 (2020-09-14)
================================

Fixup for 294931ecd22f90c1086f0d9994a003dc816da1ff

Changes for v3.6.40 (2020-09-14)
================================

-  Fixup for 294931ecd22f90c1086f0d9994a003dc816da1ff

Changes for v3.6.39 (2020-09-14)
================================

-  ecs run: accept raw numeric cpu, mem values

Changes for v3.6.38 (2020-09-14)
================================

-  Fix broken release

Changes for v3.6.37 (2020-09-14)
================================

-  ecs run: Align default container memory reservation to Fargate task

Changes for v3.6.36 (2020-09-14)
================================

-  Specify default user for batch submit

Changes for v3.6.35 (2020-09-13)
================================

-  Pass through user setting in ecs and batch

-  Test improvements

Changes for v3.6.34 (2020-09-08)
================================

-  Use instance id in lieu of public_dns_name when adding host key

Changes for v3.6.33 (2020-09-08)
================================

-  launch: poll instance state; do not assume public DNS name. Fixes #55

-  Support ECS EFS volumes

-  Enable ssh agent forwarding in sudo shells by default

-  Update CRAN mirror for Ubuntu 20.04

Changes for v3.6.32 (2020-07-24)
================================

-  Use bless-provided username when connecting to containers

Changes for v3.6.31 (2020-07-24)
================================

-  aegea ssh: support bless with oidc

Changes for v3.6.30 (2020-07-18)
================================

-  Add ecs ssh

Changes for v3.6.29 (2020-07-17)
================================

-  ecs: give parsers unique names

Changes for v3.6.28 (2020-07-16)
================================

-  batch: give parsers unique names

-  ensure_vpc: set tags at create time

-  rm: support EIGWs

Changes for v3.6.27 (2020-07-10)
================================

Bump dependencies

Changes for v3.6.26 (2020-07-10)
================================

-  launch: assign tags at launch time where possible

-  version: Print versions of boto3, botocore

-  IAM: avoid trying to write policy every time

-  Do not assume a private AMI is present

Changes for v3.6.25 (2020-06-14)
================================

-  Add aegea s3 versions, aegea s3 restore

-  build-ami: Disable apt-daily-upgrade.service

-  Restrict default batch job IAM policies

-  build-ami: Make Linux shell profile env file sh compatible

Changes for v3.6.24 (2020-05-30)
================================

-  Fix hostname config when using SSM with Bless

-  Save instance public key under correct hostname

Changes for v3.6.23 (2020-05-30)
================================

Fixup for build-ami

Changes for v3.6.22 (2020-05-29)
================================

Fixup 2

Changes for v3.6.21 (2020-05-29)
================================

Revert “Fixup for build_ami”

Changes for v3.6.20 (2020-05-29)
================================

-  Fixup for build_ami

Changes for v3.6.19 (2020-05-28)
================================

batch submit –wdl –watch: Return WDL output

Changes for v3.6.18 (2020-05-28)
================================

-  ecs watch: make compatible with new ECS task IDs

-  Fix s3 buckets options config

-  batch submit: Begin WDL support

Changes for v3.6.17 (2020-05-24)
================================

-  Fix batch watch, begin customizable job log printing

-  ecr retag: Add repo ID sanity check

Changes for v3.6.16 (2020-05-16)
================================

-  Use ssm for build-ami

-  Begin aegea run

Changes for v3.6.15 (2020-05-15)
================================

-  Disable apt-daily.service

-  Begin aegea s3 select

Changes for v3.6.14 (2020-05-06)
================================

-  Add aegea ecr retag

-  batch submit: set DEBIAN_FRONTEND=noninteractive

-  Accelerate aegea buckets ls

-  Cap ThreadPoolExecutor workers at 8

Changes for v3.6.13 (2020-04-30)
================================

Revert “Use setuptools_scm”

Changes for v3.6.12 (2020-04-30)
================================

-  Fixup for build-docker-image

-  Use setuptools_scm

Changes for v3.6.11 (2020-04-25)
================================

Fixup for build-docker-image

Changes for v3.6.10 (2020-04-23)
================================

-  Batch: allow container to be unset

Changes for v3.6.9 (2020-04-23)
===============================

-  Add support for client endpoint config

-  Speed up APT install for docker builder instance

-  Add new regions to VPC base config

-  Add aegea sfn stop

-  Cosmetic improvements to sfn history

Changes for v3.6.8 (2020-04-13)
===============================

-  Add sfn history

Changes for v3.6.7 (2020-04-10)
===============================

-  batch: add SSM policy and name tags for CE instances

-  Add info logging for batch ssh

Changes for v3.6.6 (2020-04-08)
===============================

-  batch ssh: Fix default ssh_args

Changes for v3.6.5 (2020-04-08)
===============================

-  Fix aegea ssh

Changes for v3.6.4 (2020-04-07)
===============================

-  batch: ask to terminate job on Ctrl-C

-  Manage Batch memory quota

-  Update boto3 and awscli dependencies to match Ubuntu LTS

-  Add AmazonSSMManagedInstanceCore to aegea.launch role

-  batch describe: use common helper to pull in description cache

Changes for v3.6.3 (2020-03-23)
===============================

Fix typo in deb package URL

Changes for v3.6.2 (2020-03-23)
===============================

-  Use HTTPS to download SM plugin

Changes for v3.6.1 (2020-03-23)
===============================

Use unauthenticated S3 session to download public S3 URL Update readme
to mention SSH SSM integration

Changes for v3.6.0 (2020-03-22)
===============================

-  ssh: use SSM Session Manager by default

Changes for v3.5.2 (2020-03-22)
===============================

-  sfn watch: Return deserialized output for pretty-printing

Changes for v3.5.1 (2020-03-20)
===============================

-  Resolve SSH port lazily and without mutable kwarg

Changes for v3.5.0 (2020-03-20)
===============================

-  batch: add job description helper lambda

-  Print the command that would be run with aegea batch –dry-run (#53)

-  sfn describe: allow state machines to be described

Changes for v3.4.3 (2020-03-10)
===============================

-  sfn watch: Print Lambda name if available

Changes for v3.4.2 (2020-03-05)
===============================

Add aegea sfn watch

Changes for v3.4.1 (2020-03-03)
===============================

-  batch submit: don’t require command override if job definition is set

Changes for v3.4.0 (2020-03-03)
===============================

-  Initial release of the aegea sfn family of functions

Changes for v3.3.12 (2020-03-02)
================================

Fixup for 3b43abdf558cc700dc35218190c54a477783a275



Changes for v3.3.11 (2020-03-02)
================================

Ignore empty sfn input/output

Changes for v3.3.10 (2020-03-02)
================================

Add aegea sfn describe

Changes for v3.3.9 (2020-03-02)
===============================

Begin aegea sfn

Changes for v3.3.8 (2020-02-18)
===============================

-  build-docker-image: Parameterize Docker image tag

Changes for v3.3.7 (2020-02-10)
===============================

-  Update policies in preparation for SSM support

Changes for v3.3.6 (2020-01-30)
===============================

Fixup for 2ef2186e0749e205153374aa6a106379d4e62090

Changes for v3.3.5 (2020-01-30)
===============================

-  ebs attach: Fix mkfs defaults

-  build-docker-image: add –no-cache option

Changes for v3.3.4 (2020-01-17)
===============================

-  logs: add –print-s3-urls

Changes for v3.3.3 (2020-01-14)
===============================

-  Parallelize aegea batch ls

-  aegea ecs tasks: List all tasks

Changes for v3.3.2 (2019-12-31)
===============================

Stop Ubuntu MOTD spam, part 2

Changes for v3.3.1 (2019-12-31)
===============================

-  Stop Ubuntu MOTD spam

Changes for v3.3.0 (2019-12-31)
===============================

-  build-docker-image: use Docker cache

Changes for v3.2.7 (2019-12-13)
===============================

-  batch submit –execute: Follow s3 redirects for staging bucket url
   (#51)

Changes for v3.2.6 (2019-12-13)
===============================

-  aegea batch terminate: allow multiple job IDs, custom reason

Changes for v3.2.5 (2019-12-12)
===============================

Fix typo

Changes for v3.2.4 (2019-12-12)
===============================

-  Batch: configurable staging bucket; use HEAD Bucket

Changes for v3.2.3 (2019-12-09)
===============================

-  Fixup for 2600524a76ac1a0373d619ba245955eb40661e92

Changes for v3.2.2 (2019-12-09)
===============================

-  Make nvme discovery more defensive

-  aegea rm: support removing EC2 launch templates by id

Changes for v3.2.1 (2019-11-26)
===============================

-  aegea ecs: do not require ecs:CreateCluster if cluster is present

Changes for v3.2.0 (2019-11-26)
===============================

-  Log performance improvements (#50)

-  Use CloudWatch log export for aegea logs

-  Use CloudWatch Logs Insights for aegea grep

-  Add log group name completer

Changes for v3.1.3 (2019-11-20)
===============================

-  aegea launch: add Bless support

Changes for v3.1.2 (2019-11-18)
===============================

-  Fix bugs in default ephemeral device handling logic

-  aegea ssh: Support configurable use_kms_auth

Changes for v3.1.1 (2019-11-15)
===============================

-  SpotFleetBuilder: Use AmazonEC2SpotFleetTaggingRole

-  aegea scp: Do not crash if no colon is found

Changes for v3.1.0 (2019-11-15)
===============================

-  aegea ssh, aegea scp: Add bless support

-  aegea ecs: reuse task definitions

-  aegea cost: add group by tag support

-  aegea batch: format ephemeral storage on host

-  Always encrypt EBS volumes

Changes for v3.0.2 (2019-10-29)
===============================

-  Fix dockerd configuration for default AMI

Changes for v3.0.1 (2019-10-29)
===============================

-  Add aegea cost-forecast

Changes for v3.0.0 (2019-10-28)
===============================

-  Begin aegea cost

-  Update pricing code to use pricing API

-  Add instance type and service name completers

-  aegea rm: Fix IAM policy deletion logic

-  aegea security-groups: render port ranges correctly

-  aegea rds ls: add ARN

-  Use getservbyname for SSH port

-  Recognize only dash-separated commands

-  Test improvements

Changes for v2.9.0 (2019-10-22)
===============================

-  aegea launch: add –efs-home and update EFS infra code

Changes for v2.8.3 (2019-10-17)
===============================

-  aegea ecs run: set mount_instance_storage to None

-  aegea batch update-compute-environment: support zero values

Changes for v2.8.2 (2019-10-16)
===============================

-  Fix job definition reuse regression introduced in b00296b

-  Centralize sort_by handling

Changes for v2.8.1 (2019-10-15)
===============================

-  Fix release of v2.8.0

Changes for v2.8.0 (2019-10-15)
===============================

-  aegea batch: ebs cleanup: make resilient to open WD handles

-  aegea rds ls lists clusters; add aegea rds instances

-  Use AWS_PROFILE, unset AWS_DEFAULT_PROFILE

-  aegea iam: do not crash if access is denied to list attached policies

Changes for v2.7.9 (2019-10-05)
===============================

aegea batch: allow ebs shellcode to deal with incorrect usage

Changes for v2.7.8 (2019-10-05)
===============================

-  aegea batch: fix install issues in ebs shellcode

Changes for v2.7.7 (2019-10-04)
===============================

-  Workaround for Python bug 33666: os.errno was removed in Python 3.7

-  Update AMI builder settings

Changes for v2.7.6 (2019-10-03)
===============================

-  aegea batch: tab completers, paginators for queues, CEs

Changes for v2.7.5 (2019-10-03)
===============================

-  aegea batch: avoid setting resourceRequirements unless needed

Changes for v2.7.4 (2019-10-03)
===============================

-  aegea batch: manually construct job definition paginator

-  Add helper to get ECS container metadata

Changes for v2.7.3 (2019-09-30)
===============================

Support –profile and –region CLI options

Changes for v2.7.2 (2019-09-27)
===============================

-  aegea ssh: fix command building

Changes for v2.7.1 (2019-09-20)
===============================

-  Fix zone detection, try 2

Changes for v2.7.0 (2019-09-20)
===============================

-  Add aegea lambda update_config

-  Tag EBS volumes with managedBy and batch job ID tags

-  Refactor DNS default zone management

-  Set dev tree version back to placeholder value (0.0.0)

Changes for v2.6.11 (2019-09-18)
================================

-  aegea ebs detach: continue on unmount failure

Changes for v2.6.10 (2019-09-18)
================================

-  aegea ebs: Always print create response even if attach fails

Changes for v2.6.9 (2019-09-16)
===============================

-  aegea ebs attach: Fall back to Xen device name

Changes for v2.6.8 (2019-09-16)
===============================

-  aegea ebs create: make return value invariant on options

Changes for v2.6.7 (2019-09-16)
===============================

-  aegea ebs: Use FS labels to track EBS volumes on non-NVMe instances

Changes for v2.6.6 (2019-09-12)
===============================

-  Correctly process ebs_vol_mgr_shellcode string

-  aegea iam users: print access keys

-  aegea ecs run: Allow Fargate executor to fetch ECR images

Changes for v2.6.5 (2019-09-09)
===============================

-  Add aegea batch update-compute-environment

Changes for v2.6.4 (2019-09-09)
===============================

-  aegea batch watch: Forward exit code from job

Changes for v2.6.3 (2019-09-09)
===============================

-  aegea.util.aws.ensure_iam_role: Fix trust policy handling bug for new
   roles

Changes for v2.6.2 (2019-09-08)
===============================

-  aegea batch: Use ephemeral storage

Changes for v2.6.1 (2019-09-06)
===============================

-  aegea batch submit: EBS shellcode fixes

Changes for v2.6.0 (2019-09-06)
===============================

-  Updates to aegea ebs and aegea batch submit to better support EBS
   volume management

Changes for v2.5.8 (2019-09-06)
===============================

-  Expand aegea ebs functionality

Changes for v2.5.7 (2019-09-02)
===============================

-  aegea ecs run: utilize 4G scratch space

-  aegea ecs run: forward exit code from container

Changes for v2.5.6 (2019-08-30)
===============================

-  aegea ecs run: set trust policy; allow IAM policies to be updated

Changes for v2.5.5 (2019-08-30)
===============================

-  aegea ecs run: fix –execute env var expectations

Changes for v2.5.4 (2019-08-29)
===============================

-  aegea ecs watch: Fix for breaking change in ECS API

-  aegea logs: fix bug where log_stream was ignored

Changes for v2.5.3 (2019-08-29)
===============================

-  aegea launch: Improve help in DNS error message

Changes for v2.5.2 (2019-08-28)
===============================

-  aegea ssh: turn on ServerAliveInterval by default

Changes for v2.5.1 (2019-08-07)
===============================

-  aegea launch: prefer AMIs built by current user or by Aegea

Changes for v2.5.0 (2019-07-30)
===============================

-  aegea ecs run improvements

-  Print simple defaults in help messages; consolidate help formatting

Changes for v2.4.0 (2019-07-29)
===============================

-  Add aegea ecs

-  aegea top: don’t crash on access deny; parallelize query

Changes for v2.3.6 (2019-05-22)
===============================

-  aegea batch: include parameter hash in job definiton

Changes for v2.3.5 (2019-05-21)
===============================

Reset job definition namespace

Changes for v2.3.4 (2019-05-21)
===============================

-  Fix for v2.3.3 (release only committed changes)

Changes for v2.3.3 (2019-05-21)
===============================

-  aegea batch: Look for a matching job definition before creating one

-  Avoid crashing when no access is given to MFA status

Changes for v2.3.2 (2019-03-08)
===============================

-  aegea launch: Match subnet if AZ is specified

Changes for v2.3.1 (2019-03-04)
===============================

-  Allow empty principal in aegea secrets put

Changes for v2.3.0 (2019-02-11)
===============================

-  Implement aegea lambda update

-  Implement aegea configure set

Changes for v2.2.9 (2019-01-22)
===============================

-  Expand aegea –version to print platform details

-  Test fixes

Changes for v2.2.8 (2019-01-22)
===============================

-  Fix logic error in selecting private DNS zone in aegea launch

Changes for v2.2.7 (2019-01-21)
===============================

-  Debug and optimize EC2 pricing API client

-  Allow passing of options to scp

-  Fix linter errors

-  Avoid CVE-2018-1000805

Changes for v2.2.6 (2018-10-05)
===============================

-  Move chalice dependency to extras

Changes for v2.2.5 (2018-10-05)
===============================

-  Update version range for tweak dependency

Changes for v2.2.4 (2018-09-07)
===============================

-  aegea logs: use get_log_events instead of filter_log_events for speed

-  Begin aegea config

Changes for v2.2.3 (2018-07-17)
===============================

-  Bump keymaker dependency

Changes for v2.2.2 (2018-07-17)
===============================

-  Add volume type to batch submit command (#41)

Changes for v2.2.1 (2018-05-07)
===============================

-  Fix logic bug in aegea ssh username discovery

-  aegea build-ami: Ubuntu 18.04 compatibility

Changes for v2.2.0 (2018-05-03)
===============================

-  Get correct IAM username for cross-account SSH

-  Bump dependencies

Changes for v2.1.9 (2018-04-13)
===============================

-  Bump deps

Changes for v2.1.8 (2018-04-12)
===============================

-  Fixups for aegea deploy

Changes for v2.1.7 (2018-04-12)
===============================

-  Buildbox usability updates

Changes for v2.1.6 (2018-04-11)
===============================

-  Fix Python compat issue in key_fingerprint

Changes for v2.1.5 (2018-04-11)
===============================

-  Fix queue naming in aegea-deploy-pilot

Changes for v2.1.4 (2018-04-10)
===============================

-  Continue secrets migration

-  Fix splitting of deploy systemd unit names

Changes for v2.1.3 (2018-04-10)
===============================

-  Begin switching aegea secrets to secretsmanager

-  Add Lambda listing parsers

-  Bump deps and add common deps per @cschin request

-  Fix permissions in cloudinit rootfs.skel input

-  Accommodate IAM eventual consistency in instance profiles

Changes for v2.1.2 (2018-04-05)
===============================

-  Bump dependencies

Changes for v2.1.1 (2018-03-26)
===============================

-  Bump pip ami build dependencies

-  Add aegea scp

Changes for v2.1.0 (2017-12-20)
===============================

-  Beautify batch shellcode

-  aegea launch: add support for EBS volumes via --storage

-  aegea --log-level: Remove unneeded NOTSET level

-  Expand documentation

Changes for v2.0.9 (2017-11-21)
===============================

-  Fix version generation

Changes for v2.0.8 (2017-11-21)
===============================

-  aegea batch submit: Use S3 to stage execute payload

-  Enable newline formatting and excise comments in ebs shellcode

-  kill processes using the filesystem before unmounting (#34)

Changes for v2.0.7 (2017-11-20)
===============================

-  aegea batch watch: fix logic error when job fails before starting

Changes for v2.0.6 (2017-11-20)
===============================

-  Disable custom Batch AMIs by default

Changes for v2.0.5 (2017-11-20)
===============================

-  Make sure version is updated when rolling release

Changes for v2.0.4 (2017-11-20)
===============================

-  Fix broken release

Changes for v2.0.3 (2017-11-19)
===============================

-  Bump tweak dependency with upstream fix

Changes for v2.0.2 (2017-11-17)
===============================

-  Undo changes that had to do with tweak breakage

-  fix another typo that was breaking job launch (#33)

Changes for v2.0.1 (2017-11-16)
===============================

-  fix batch: newlines and percent characters have special meaning (#32)

Changes for v2.0.0 (2017-11-15)
===============================

-  Further ameliorate the volume attach/detach polling issues (#31)

-  Limit time we wait for aws detach to succeed before deleting volume
   (#30)

-  Make exception catching more specific

Changes for v1.0.1 (2017-09-15)
===============================

Fix for batch API breaking changes (#25)

Changes for v1.10.0 (2017-09-11)
================================

-  Set default nofile to 100000; lint fixes

-  aegea batch submit: Add ability to specify ulimits nofile to
   conatiner and also adding sensible default (#24)

-  Change aegea-deploy service to serve as template, add custom make
   targets, using one queue per (org, name, branch, instanceid)

-  Add iam-role argument to build

Changes for v1.9.18 (2017-08-16)
================================

-  aegea batch watch: Do not crash if log stream does not exist yet

Changes for v1.9.17 (2017-06-15)
================================

Merge pull request #22 from wholebiome/build-timeout Extend timeout for
AMI builds Added timeout to loop, default much longer Fix tests

Changes for v1.9.16 (2017-06-01)
================================

-  Add file missed in 0c99863

Changes for v1.9.15 (2017-06-01)
================================

-  Fix logic error in parameter naming

Changes for v1.9.14 (2017-05-29)
================================

-  Temporarily disable batch custom AMI

Changes for v1.9.13 (2017-05-29)
================================

-  Minor refactor in batch

-  Ensure default selection of batch instances has instance storage

-  Begin aegea lambda ls, aegea rm --lambda

-  Tab complete log levels

-  Avoid using pkgutil for introspection

Changes for v1.9.12 (2017-05-14)
================================

-  Batch bug fixes and begin support for custom Batch ECI AMIs

Changes for v1.8.4 (2017-02-02)
===============================

-  Install process robustness improvements

-  Install documentation improvements

Changes for v1.8.3 (2017-02-01)
===============================

-  Don't symlink aegea in bin to avoid pip uninstall bugs

Changes for v1.8.2 (2017-02-01)
===============================

-  Resume interrupted release

Changes for v1.8.1 (2017-02-01)
===============================

-  Resume interrupted release

Changes for v1.8.0 (2017-02-01)
===============================

-  Installation documentation and robustness improvements

-  Batch API and mission-specific improvements

Changes for v1.7.4 (2017-01-26)
===============================

-  aegea batch: automatic setup of builder IAM policies

-  aegea batch submit --job-role: automatic setup of job IAM roles

-  aegea batch submit --storage: EBS volume manager

-  Autocomplete column titles in listing subcommands where a resource is
   available

-  Autoconfigure a VPC if all VPCs including the default VPC were
   deleted

-  Asset loader: offload rootfs.skel to S3 when user-data exceeds 16K
   limit

-  Arvados updates

-  Make missions dir doc link relative (#9)

-  Display statusReason in aegea batch ls and aegea batch watch

Changes for v1.7.3 (2017-01-18)
===============================

-  Add automatic configuration for route53 private DNS

-  Various improvements to aegea batch

-  Work around autoloader import issue seen on some Python 2.7 versions

-  aegea build\_ami: improve progress and error messages

Changes for v1.7.2 (2017-01-13)
===============================

-  Fix makefile shell assumption

-  Batch WIP

Changes for v1.7.1 (2017-01-13)
===============================

-  Test and release infra improvements

-  Batch docs

Changes for v1.7.0 (2017-01-10)
===============================

-  aegea-build-image-for-mission now builds ECR images by default

-  Integration work for Batch

Changes for v1.6.3 (2017-01-08)
===============================

-  Add ELB SG configurator, aegea-rebuild-public-elb-sg

-  Add awscli to deps

Changes for v1.6.2 (2017-01-06)
===============================

-  ELB deploy: set default target group name properly

-  Make sure wheel is installed before attempting setup

-  Aegea batch submit: Begin CWL support

-  Aegea batch watch: amend log line dup fix

Changes for v1.6.1 (2017-01-03)
===============================

-  Improvements to aegea batch

Changes for v1.6.0 (2016-12-30)
===============================

-  Aegea EFS refactor

-  Aegea batch

-  Add IP Ranges API

-  Add aegea buckets cors placeholder

-  Aegea bucket lifecycle

-  Test and release infrastructure improvements

Changes for v1.5.1 (2016-11-14)
===============================

-  Fogdog mission: add environment placeholder

-  Begin timestamp backport

-  Propagate base AMI metadata in build\_image

Changes for v1.5.0 (2016-11-10)
===============================

-  Implement aegea rds snapshot

-  Only use pager with pretty-printed tables

-  Add Amazon Linux AMI locator

-  Use -w0 for auto col width table formatter

-  aegea zones update: support multiple updates

-  Cosmetic and documentation fixes

Changes for v1.4.0 (2016-11-02)
===============================

-  aegea-build-ami-for-mission: skip make if no Makefile
-  Begin FogDog mission
-  Arvados config support; improve config file handling
-  Don't fail cloud-init on account of expected ssh failure
-  Run ssh-add from aegea launch
-  aegea elb create bugfix
-  Fix ELB behavior when TG is present
-  Simplify arg forwarding in build\_ami

Changes for v1.3.0 (2016-10-20)
===============================

-  Support running core aegea on Ubuntu 14.04 vendored Python

-  Improve freeform cloud-config-data passing

-  Fix pager; introduce --auto-col-width table formatter

-  List security groups in elb listing

-  Break out and begin buildout of aegea ebs subcommand

-  Begin improving rds listings

-  Improve DNS zone repr

-  New protocol to check out local tracking branch in aegea deploy

-  aegea elb create: configurable health check path

-  Key cloud-init files manifest by file path to avoid duplicates

Changes for v1.2.2 (2016-10-08)
===============================

-  ELB provisioning and listing improvements

Changes for v1.2.1 (2016-10-07)
===============================

-  Aegea deploy fixups

Changes for v1.2.0 (2016-10-05)
===============================

-  Online documentation improvements

-  aegea zones: begin ability to edit records from command line

-  Begin support for recursive git clone deploy keys (#4)

-  Pretty-print dicts and lists as json in tables

-  Logic fixes in elb create command

Changes for v1.1.1 (2016-09-27)
===============================

-  Initial support for arvados mission

Changes for v1.1.0 (2016-09-27)
===============================

-  Begin work on missions

-  aegea-deploy-pilot: admit dashes in branch name via service name

-  Fix bug where tweak overwrote config file supplied via environment

-  Online documentation improvements

Changes for v1.0.0 (2016-09-22)
===============================

-  Aegea build\_image renamed to build\_ami
-  Aegea tag, untag
-  Doc improvements
-  Ubuntu 14.04 compatibility and role improvements
-  docker-event-relay reliability improvements
-  Remove snapd from default loadout
-  aegea volumes: display attachment instance names
-  aegea-deploy-pilot: Deploy on SIGUSR1

-  Initial support for flow logs
-  Pretty-print and perform whois lookups for aegea security\_groups
-  aegea ls security\_groups: break out protocol into its own column
-  Print security group rules in aegea ls security\_groups
-  List security groups in aegea ls
-  Print zone ID in aegea zones
-  Aegea deploy reliability improvements: use per-pid queues
-  Aegea launch reliability improvements: Back off on polling the EC2
   API

Changes for v0.9.8 (2016-08-23)
===============================

-  Update release script
-  Config updates
-  Sort properly while formatting datetimes
-  Continue ALB support

Changes for v0.9.7 (2016-08-17)
===============================

-  Add babel and format relative dates
-  Add aegea elb create
-  Changes in support of app deploy infrastructure
-  Add R default mirror config
-  IAM principal lists now report attached policies

Changes for v0.9.6 (2016-08-14)
===============================

Continue release script

Changes for v0.9.5 (2016-08-14)
===============================

Continue release script

Version 0.7.0 (2016-05-29)
--------------------------
- Introduce rds subcommand

Version 0.6.0 (2016-05-29)
--------------------------
- Rollup: many changes

Version 0.5.0 (2016-05-05)
--------------------------
- Rollup: many changes

Version 0.4.0 (2016-04-19)
--------------------------
- aegea audit implementation (except section 4)
- numerous image improvements

Version 0.3.0 (2016-04-12)
--------------------------
- Rollup: many changes

Version 0.2.3 (2016-03-30)
--------------------------
- Rollup: many changes

Version 0.2.1 (2016-03-12)
--------------------------
- Begin tracking version history
- Expand test suite
