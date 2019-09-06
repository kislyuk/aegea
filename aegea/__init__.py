"""
Amazon Web Services Operator Interface

For general help, run ``aegea help`` or visit https://github.com/kislyuk/aegea/wiki.
For help with individual commands, run ``aegea <command> --help``.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, argparse, logging, shutil, json, datetime, traceback, errno, warnings, platform
from textwrap import fill
import tweak
from botocore.exceptions import NoRegionError
from io import open
from .util.compat import USING_PYTHON2
from .version import __version__

if sys.version_info < (2, 7, 9):  # See https://urllib3.readthedocs.io/en/latest/advanced-usage.html#sni-warning
    try:
        import botocore.vendored.requests.packages.urllib3.contrib.pyopenssl as p
        p.inject_into_urllib3()
        import requests.packages.urllib3.contrib.pyopenssl as p
        p.inject_into_urllib3()
    except ImportError:
        msg = ('Your Python version is too old to support TLS SNI. '
               'Run "pip install pyopenssl ndg-httpsclient" to avoid connection issues.')
        warnings.warn(msg)

logger = logging.getLogger(__name__)

config, parser = None, None
_subparsers, _hidden_subparsers = {}, {}

class AegeaConfig(tweak.Config):
    base_config_file = os.path.join(os.path.dirname(__file__), "base_config.yml")

    @property
    def config_files(self):
        return [self.base_config_file] + tweak.Config.config_files.fget(self)

    @property
    def user_config_dir(self):
        return os.path.join(self._user_config_home, self._name)

class AegeaHelpFormatter(argparse.RawTextHelpFormatter):
    def _get_help_string(self, action):
        default = get_config_for_prog(self._prog).get(action.dest)
        if default is not None and not isinstance(default, list):
            return action.help + " (default: {})".format(default)
        return action.help

def initialize():
    global config, parser
    from .util.printing import BOLD, RED, ENDC
    config = AegeaConfig(__name__, use_yaml=True, save_on_exit=False)
    if not os.path.exists(config.config_files[2]):
        config_dir = os.path.dirname(os.path.abspath(config.config_files[2]))
        try:
            os.makedirs(config_dir)
        except OSError as e:
            if not (e.errno == errno.EEXIST and os.path.isdir(config_dir)):
                raise
        shutil.copy(os.path.join(os.path.dirname(__file__), "user_config.yml"), config.config_files[2])
        logger.info("Wrote new config file %s with default values", config.config_files[2])
        config = AegeaConfig(__name__, use_yaml=True, save_on_exit=False)

    parser = argparse.ArgumentParser(
        description="{}: {}".format(BOLD() + RED() + __name__.capitalize() + ENDC(), fill(__doc__.strip())),
        formatter_class=AegeaHelpFormatter
    )
    parser.add_argument("--version", action="version", version="%(prog)s {}\n{} {}\n{}".format(
        __version__,
        platform.python_implementation(),
        platform.python_version(),
        platform.platform()
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
                    print(datetime.datetime.now().isoformat(), file=fh)
                    print(err_msg, file=fh)
                exit("{}: {}. See {} for error details.".format(e.__class__.__name__, e, err_log_filename))
            except Exception:
                print(err_msg, file=sys.stderr)
                exit(os.EX_SOFTWARE)
    if isinstance(result, SystemExit):
        raise result
    elif result is not None:
        if isinstance(result, dict) and "ResponseMetadata" in result:
            del result["ResponseMetadata"]
        print(json.dumps(result, indent=2, default=lambda x: str(x)))

def get_config_for_prog(prog):
    command = prog.split(" ", 1)[-1].replace("-", "_").replace(" ", "_")
    return config.get(command, {})

def register_parser(function, parent=None, name=None, **add_parser_args):
    if config is None:
        initialize()
    if parent is None:
        parent = parser
    parser_name = name or function.__name__
    if parent.prog not in _subparsers:
        _subparsers[parent.prog] = parent.add_subparsers()
    if "_" in parser_name and not USING_PYTHON2:
        add_parser_args["aliases"] = [parser_name]
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
    subparser.add_argument("--log-level", default=config.get("log_level"),
                           help=str([logging.getLevelName(i) for i in range(10, 60, 10)]),
                           choices={logging.getLevelName(i) for i in range(10, 60, 10)})
    subparser.set_defaults(entry_point=function)
    if parent and sys.version_info < (2, 7, 9):  # See https://bugs.python.org/issue9351
        parent._defaults.pop("entry_point", None)
    subparser.set_defaults(**get_config_for_prog(subparser.prog))
    return subparser
