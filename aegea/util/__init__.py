from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, re, socket, time, io, gzip, logging
from functools import partial
from datetime import datetime
from dateutil.parser import parse as dateutil_parse
from dateutil.relativedelta import relativedelta

from .printing import GREEN
from .compat import Repr, str

logger = logging.getLogger(__name__)

def wait_for_port(host, port, timeout=600, print_progress=True):
    if print_progress:
        sys.stderr.write("Waiting for {}:{}...".format(host, port))
        sys.stderr.flush()
    start_time = time.time()
    while True:
        try:
            socket.socket().connect((host, port))
            if print_progress:
                sys.stderr.write(GREEN("OK") + "\n")
            return
        except Exception:
            time.sleep(1)
            if print_progress:
                sys.stderr.write(".")
                sys.stderr.flush()
            if time.time() - start_time > timeout:
                raise

def validate_hostname(hostname):
    if len(hostname) > 255:
        raise Exception("Hostname {} is longer than 255 characters".format(hostname))
    if hostname[-1] == ".":
        hostname = hostname[:-1]
    allowed = re.compile(r"(?!-)[A-Z\d-]{1,63}(?<!-)$", re.IGNORECASE)
    if not all(allowed.match(x) for x in hostname.split(".")):
        raise Exception("Hostname {} is not RFC 1123 compliant".format(hostname))

class VerboseRepr:
    def __repr__(self):
        return "<{module}.{classname} object at 0x{mem_loc:x}: {dict}>".format(
            module=self.__module__,
            classname=self.__class__.__name__,
            mem_loc=id(self),
            dict=Repr().repr(self.__dict__)
        )

def natural_sort(i):
    return sorted(i, key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)])

def paginate(boto3_paginator, *args, **kwargs):
    for page in boto3_paginator.paginate(*args, **kwargs):
        for result_key in boto3_paginator.result_keys:
            for value in page.get(result_key.parsed.get("value"), []):
                yield value

class Timestamp(datetime):
    """
    Integer inputs are interpreted as milliseconds since the epoch. Sub-second precision is discarded. Suffixes (s, m,
    h, d, w) are supported. Negative inputs (e.g. -5m) are interpreted as relative to the current date. Other inputs
    (e.g. 2020-01-01, 15:20) are parsed using the dateutil parser.
    """
    _precision = {}

    def __new__(cls, t, snap=0):
        if isinstance(t, (str, bytes)) and t.isdigit():
            t = int(t)
        if not isinstance(t, (str, bytes)):
            from dateutil.tz import tzutc
            return datetime.fromtimestamp(t // 1000, tz=tzutc())
        try:
            units = ["weeks", "days", "hours", "minutes", "seconds"]
            diffs = {u: float(t[:-1]) for u in units if u.startswith(t[-1])}
            if len(diffs) == 1:
                # Snap > 0 governs the rounding of units (hours, minutes and seconds) to 0 to improve cache performance
                snap_units = {u.rstrip("s"): 0 for u in units[units.index(list(diffs)[0]) + snap:]} if snap else {}
                snap_units.pop("day", None)
                snap_units.update(microsecond=0)
                ts = datetime.now().replace(**snap_units) + relativedelta(**diffs)
                cls._precision[ts] = snap_units
                return ts
            return dateutil_parse(t)
        except (ValueError, OverflowError, AssertionError):
            raise ValueError('Could not parse "{}" as a timestamp or time delta'.format(t))

    @classmethod
    def match_precision(cls, timestamp, precision_source):
        if precision_source in cls._precision:
            logger.debug("Discarding timestamp %s %s precision", timestamp, ", ".join(cls._precision[precision_source]))
        return timestamp.replace(**cls._precision.get(precision_source, dict(microsecond=0)))

def add_time_bound_args(p, snap=0):
    t = partial(Timestamp, snap=snap)
    p.add_argument("--start-time", type=t, default=Timestamp("-7d", snap=snap), help=Timestamp.__doc__, metavar="START")
    p.add_argument("--end-time", type=t, help=Timestamp.__doc__, metavar="END")

class hashabledict(dict):
    def __hash__(self):
        return hash(tuple(sorted(self.items())))

def describe_cidr(cidr):
    import ipwhois, ipaddress, socket
    address = ipaddress.ip_network(str(cidr)).network_address
    try:
        whois = ipwhois.IPWhois(address).lookup_rdap()
        whois_names = [whois["asn_country_code"]] if "asn_country_code" in whois else []
        whois_names += [o.get("contact", {}).get("name", "") for o in whois.get("objects", {}).values()]
    except Exception:
        try:
            whois_names = [socket.gethostbyaddr(address)]
        except Exception:
            whois_names = [cidr]
    return ", ".join(str(n) for n in whois_names)

def gzip_compress_bytes(payload):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="w", mtime=0) as gzfh:
        gzfh.write(payload)
    return buf.getvalue()

def get_mkfs_command(fs_type="xfs", label="aegveph"):
    if fs_type == "xfs":
        return "mkfs.xfs -L {} -f ".format(label)
    elif fs_type == "ext4":
        return "mkfs.ext4 -L {} -F -E lazy_itable_init,lazy_journal_init ".format(label)
