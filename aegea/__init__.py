"""
Amazon Web Services Operator Interface

For general help, run ``aegea help`` or visit https://github.com/kislyuk/aegea/wiki.
For help with individual commands, run ``aegea <command> --help``.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, argparse, logging, shutil, json, datetime, traceback, errno, warnings, platform
from textwrap import fill
from typing import Dict, Any

import tweak, boto3, botocore
from botocore.exceptions import NoRegionError
from io import open
from .util.compat import USING_PYTHON2
from .version import __version__

logger = logging.getLogger(__name__)

config, parser = None, None  # type: AegeaConfig, argparse.ArgumentParser
_subparsers = {}  # type: Dict[Any, Any]

class AegeaConfig(tweak.Config):
    base_config_file = os.path.join(os.path.dirname(__file__), "base_config.yml")

    @property
    def config_files(self):
        return [self.base_config_file] + tweak.Config.config_files.fget(self)

    @property
    def user_config_dir(self):
        return os.path.join(self._user_config_home, self._name)

    @property
    def user_config_file(self):
        return os.path.join(self.user_config_dir, "config.yml")

class AegeaHelpFormatter(argparse.RawTextHelpFormatter):
    def _get_help_string(self, action):
        default = _get_config_for_prog(self._prog).get(action.dest)
        # Avoid printing defaults for list and store_false actions, since they are confusing.
        if default is not None and not isinstance(default, list) and "StoreFalse" not in action.__class__.__name__:
            return action.help + " (default: {})".format(default)
        return action.help

def initialize():
    global config, parser
    from .util.printing import BOLD, RED, ENDC
    config = AegeaConfig(__name__, use_yaml=True, save_on_exit=False)
    if not os.path.exists(config.user_config_file):
        config_dir = os.path.dirname(os.path.abspath(config.user_config_file))
        try:
            os.makedirs(config_dir, exist_ok=True)
            shutil.copy(os.path.join(os.path.dirname(__file__), "user_config.yml"), config.user_config_file)
            logger.info("Wrote new config file %s with default values", config.user_config_file)
            config = AegeaConfig(__name__, use_yaml=True, save_on_exit=False)
        except OSError as e:
            logger.error("Error writing user config file %s: %s", config.user_config_file, e)

    parser = argparse.ArgumentParser(
        description="{}: {}".format(BOLD() + RED() + __name__.capitalize() + ENDC(), fill(__doc__.strip())),
        formatter_class=AegeaHelpFormatter
    )
    parser.add_argument("--version", action="version", version="%(prog)s {}\n{}\n{}\n{} {}\n{}".format(
        __version__,
        "boto3 " + boto3.__version__,
        "botocore " + botocore.__version__,
        platform.python_implementation(),
        platform.python_version(),
        platform.platform(),
    ))

    def help(args):
        parser.print_help()
    register_parser(help)

def main(args=None):
    parsed_args = parser.parse_args(args=args)
    logger.setLevel(parsed_args.log_level)
    has_attrs = (getattr(parsed_args, "sort_by", None) and getattr(parsed_args, "columns", None))
    if has_attrs and parsed_args.sort_by not in parsed_args.columns:
        parsed_args.columns.append(parsed_args.sort_by)
    try:
        result = parsed_args.entry_point(parsed_args)
    except Exception as e:
        if isinstance(e, NoRegionError):
            msg = "The AWS CLI is not configured."
            msg += " Please configure it using instructions at"
            msg += " http://docs.aws.amazon.com/cli/latest/userguide/cli-chap-getting-started.html"
            exit(msg)
        elif logger.level < logging.ERROR:
            raise
        else:
            err_msg = traceback.format_exc()
            try:
                err_log_filename = os.path.join(config.user_config_dir, "error.log")
                with open(err_log_filename, "ab") as fh:
                    print(datetime.datetime.now().isoformat(), file=fh)  # type: ignore
                    print(err_msg, file=fh)  # type: ignore
                exit("{}: {}. See {} for error details.".format(e.__class__.__name__, e, err_log_filename))
            except Exception:
                print(err_msg, file=sys.stderr)
                exit(os.EX_SOFTWARE)
    if isinstance(result, SystemExit):
        raise result
    elif result is not None:
        if isinstance(result, dict) and "ResponseMetadata" in result:
            del result["ResponseMetadata"]
        print(json.dumps(result, indent=2, default=str))

def _get_config_for_prog(prog):
    command = prog.split(" ", 1)[-1].replace("-", "_").replace(" ", "_")
    return config.get(command, {})

def register_parser(function, parent=None, name=None, **add_parser_args):
    def get_aws_profiles(**kwargs):
        from botocore.session import Session
        return list(Session().full_config["profiles"])

    def set_aws_profile(profile_name):
        os.environ["AWS_PROFILE"] = profile_name
        os.environ.pop("AWS_DEFAULT_PROFILE", None)

    def get_region_names(**kwargs):
        from botocore.loaders import create_loader
        for partition_data in create_loader().load_data("endpoints")["partitions"]:
            if partition_data["partition"] == config.partition:
                return partition_data["regions"].keys()

    def set_aws_region(region_name):
        os.environ["AWS_DEFAULT_REGION"] = region_name

    def set_endpoint_url(endpoint_url):
        from .util.aws._boto3_loader import Loader
        Loader.client_kwargs["default"].update(endpoint_url=endpoint_url)

    def set_client_kwargs(client_kwargs):
        from .util.aws._boto3_loader import Loader
        Loader.client_kwargs.update(json.loads(client_kwargs))

    if config is None:
        initialize()
    if parent is None:
        parent = parser
    parser_name = name or function.__name__
    if parent.prog not in _subparsers:
        _subparsers[parent.prog] = parent.add_subparsers()
    if "description" not in add_parser_args:
        func_module = sys.modules[function.__module__]
        add_parser_args["description"] = add_parser_args.get("help", function.__doc__ or func_module.__doc__)
    if add_parser_args["description"] and "help" not in add_parser_args:
        add_parser_args["help"] = add_parser_args["description"].strip().splitlines()[0].rstrip(".")
    add_parser_args.setdefault("formatter_class", AegeaHelpFormatter)
    subparser = _subparsers[parent.prog].add_parser(parser_name.replace("_", "-"), **add_parser_args)
    if "_" in parser_name and USING_PYTHON2:
        _subparsers[parent.prog]._name_parser_map[parser_name] = subparser
    subparser.add_argument("--max-col-width", "-w", type=int, default=32,
                           help="When printing tables, truncate column contents to this width. Set to 0 for auto fit.")
    subparser.add_argument("--json", action="store_true",
                           help="Output tabular data as a JSON-formatted list of objects")
    subparser.add_argument("--log-level", default=config.get("log_level"), type=str.upper,
                           help=str([logging.getLevelName(i) for i in range(10, 60, 10)]),
                           choices={logging.getLevelName(i) for i in range(10, 60, 10)})
    subparser.add_argument("--profile", help="Profile to use from the AWS CLI configuration file",
                           type=set_aws_profile).completer = get_aws_profiles
    subparser.add_argument("--region", help="Region to use (overrides environment variable)",
                           type=set_aws_region).completer = get_region_names
    subparser.add_argument("--endpoint-url", metavar="URL", help="Service endpoint URL to use", type=set_endpoint_url)
    subparser.add_argument("--client-kwargs", help=argparse.SUPPRESS, type=set_client_kwargs)
    subparser.set_defaults(entry_point=function)
    if parent and sys.version_info < (2, 7, 9):  # See https://bugs.python.org/issue9351
        parent._defaults.pop("entry_point", None)
    subparser.set_defaults(**_get_config_for_prog(subparser.prog))
    return subparser
