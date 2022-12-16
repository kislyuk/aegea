"""
List, read, and write Aegea configuration parameters.
"""

import json
from typing import List

from .ls import register_listing_parser, register_parser
from .util.printing import format_table, page_output


def configure(args):
    configure_parser.print_help()

configure_parser = register_parser(configure)

def ls(args):
    from . import config, tweak

    def collect_kv(d, path, collector):
        for k, v in d.items():
            if isinstance(v, (dict, tweak.Config)):
                collect_kv(d[k], path + "." + k, collector)
            else:
                collector.append([path.lstrip(".") + "." + k, repr(v)])
    collector = []  # type: List[List]
    collect_kv(config, "", collector)
    page_output(format_table(collector))

ls_parser = register_listing_parser(ls, parent=configure_parser)

def get(args):
    """Get an Aegea configuration parameter by name"""
    from . import config
    for key in args.key.split("."):
        config = getattr(config, key)
    print(json.dumps(config))

get_parser = register_parser(get, parent=configure_parser)
get_parser.add_argument("key")

def set(args):
    """Set an Aegea configuration parameter to a given value"""
    from . import config, tweak

    class ConfigSaver(tweak.Config):
        @property
        def config_files(self):
            return [config.config_files[2]]

    config_saver = ConfigSaver(use_yaml=True, save_on_exit=False)
    c = config_saver
    for key in args.key.split(".")[:-1]:
        try:
            c = c[key]
        except KeyError:
            c[key] = {}
            c = c[key]
    c[args.key.split(".")[-1]] = json.loads(args.value) if args.json else args.value
    config_saver.save()

set_parser = register_parser(set, parent=configure_parser)
set_parser.add_argument("key")
set_parser.add_argument("value")

def sync(args):
    """Save Aegea configuration to your AWS IAM account, or retrieve a previously saved configuration"""
    raise NotImplementedError()

sync_parser = register_listing_parser(sync, parent=configure_parser)
