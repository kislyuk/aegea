Aegea: Amazon Web Services Operator Interface
=============================================

*Aegea* is a command line interface (CLI) that provides a set of essential commands and terminal dashboards for
operators of Amazon Web Services (AWS) accounts. Aegea lets you build AMIs and Docker images using the
`cloud-init <http://cloudinit.readthedocs.io/>`_ config management package, manage config roles, launch and monitor instances
and services, and manage AWS resources including ELB, RDS, and AWS Batch. It is intended to be used in conjunction with the
existing functionality of the `AWS CLI <https://aws.amazon.com/cli/>`_ and `boto3 <https://boto3.readthedocs.io/>`_.

Installation
~~~~~~~~~~~~
::

   pip install aegea

Before you do this, you will also need to install some system library dependencies:

+--------------+---------+-------------------------------------------------------------------------------------------------------+
| OS           | Python  | Command                                                                                               |
+==============+=========+=======================================================================================================+
| OS X         |         | ``sudo easy_install-2.7 pip; sudo pip install --ignore-installed six python-dateutil;``               |
|              |         | ``sudo pip install aegea`` (if prompted to install XCode Command Line Tools, do it and try again)     |
+--------------+---------+-------------------------------------------------------------------------------------------------------+
| Ubuntu       | Python 2| sudo apt-get update;                                                                                  |
|              |         | sudo apt-get install build-essential python-pip python-dev python-cffi libffi-dev libssl-dev moreutils|
+--------------+---------+-------------------------------------------------------------------------------------------------------+
| Ubuntu       | Python 3| sudo apt-get update;                                                                                  |
|              |         | sudo apt-get install build-essential python3-{pip,dev,cffi} libffi-dev libssl-dev moreutils           |
+--------------+---------+-------------------------------------------------------------------------------------------------------+
| Red Hat      | Python 2| sudo yum install python-devel python-cffi openssl-devel moreutils                                     |
+--------------+---------+-------------------------------------------------------------------------------------------------------+
| Red Hat      | Python 3| sudo yum install python3-devel python3-cffi openssl-devel moreutils                                   |
+--------------+---------+-------------------------------------------------------------------------------------------------------+

Run ``aws configure`` to configure `IAM <https://aws.amazon.com/iam/>`_ access credentials that will be used by the
``aws`` and ``aegea`` commands. You can create a new IAM key at https://console.aws.amazon.com/iam/home#/users.

**Ubuntu 12.04**: Use ``pip install cffi`` instead of ``apt-get install python-cffi``. Update your Python packaging utilities:
``for p in six setuptools packaging pip setuptools; do pip install --upgrade $p; hash -r; done``.

**Local install**: run ``make install`` in this directory.

**No root access; user-local install**: Use ``make install_venv`` to install aegea in its own virtualenv. The last line of the 
output shows how to activate the virtualenv. The version of virtualenv packaged in Ubuntu 12.04 is too old; use
``pip install --upgrade --user virtualenv`` to upgrade it.

**OS X with Homebrew Python**: Run ``brew link --force openssl``.

Aegea Batch
~~~~~~~~~~~
The `AWS Batch <https://aws.amazon.com/batch>`_ API lets you run non-interactive command line workflows in Docker
containers, managing AWS ECS, Spot Fleet, and EC2 in your account on your behalf. Use the ``aegea batch`` family of commands
to interact with AWS Batch. The key command is ``aegea batch submit`` to submit jobs.

`aegea/missions/docker-example/ <aegea/missions/docker-example/>`_ is a root directory of an **aegea mission** -
a configuration management role. It has a rootfs.skel and a config.yml, which has directives to install packages,
etc. The example just installs the bwa APT package.

Run ``aegea-build-image-for-mission docker-example dex`` to build an ECR image called dex from the "docker-example"
mission. You can list ECR images with ``aegea ecr ls``, and delete them with e.g. ``aws ecr delete-repository dex``.

Run ``aegea batch submit --ecs-image dex --command "bwa aln || true" "bwa mem || true" --memory 2048 --vcpus 4 --watch``
to run a Batch job that requires 2 GB RAM and 4 cores to be allocated to the Docker container, using the "dex" image,
and executes two commands as listed after --command, using "bash -euo pipefail -c".

You can also use ``aegea batch submit --execute FILE``. This will slurp up FILE (any type of shell script or ELF
executable) and execute it in the job's Docker container.

The concurrency and cost of your Batch jobs is governed by the "Max vCPUs" setting in your compute environment.
To change the capacity or other settings of your compute environment, go to
https://console.aws.amazon.com/batch/home?region=us-east-1#/compute-environments, select "aegea_batch", and click "Edit".

AWS Batch launches and manages `ECS <https://aws.amazon.com/ecs/>`_ host instances to execute your jobs. You can see the
host instances by running ``aegea ls``.

Configuration management
~~~~~~~~~~~~~~~~~~~~~~~~
Aegea supports ingesting configuration from a configurable array of sources. Each source is a JSON or YAML file.
Configuration sources that follow the first source update the configuration using recursive dictionary merging. Sources are
enumerated in the following order (i.e., in order of increasing priority):

- Site-wide configuration source, ``/etc/aegea/config.yml``
- User configuration source, ``~/.config/aegea/config.yml``
- Any sources listed in the colon-delimited variable ``AEGEA_CONFIG_FILE``
- Command line options

**Array merge operators**: When loading a chain of configuration sources, Aegea uses recursive dictionary merging to
combine the sources. Additionally, when the original config value is a list, Aegea supports array manipulation
operators, which let you extend and modify arrays defined in underlying configurations. See
https://github.com/kislyuk/tweak#array-merge-operators for a list of these operators.

Building AMIs and Docker images
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Aegea includes a lightweight configuration management system for building machine images based on
`cloud-init <http://cloudinit.readthedocs.io/>`_ (both Docker images and AMIs are supported).

TODO: ``build_image build_ami build_docker_image rootfs.skel``

.. image:: https://circleci.com/gh/kislyuk/aegea.svg?style=svg&circle-token=70d22b84025fad5d484ac5f3df1fc0a183c0f516
   :target: https://circleci.com/gh/kislyuk/aegea
.. image:: https://codecov.io/gh/kislyuk/aegea/branch/master/graph/badge.svg?token=a9suCNpECz
   :target: https://codecov.io/gh/kislyuk/aegea
