from __future__ import absolute_import, division, print_function, unicode_literals

import os, sys, datetime, errno

USING_PYTHON2 = True if sys.version_info < (3, 0) else False

if USING_PYTHON2:
    from StringIO import StringIO
    from repr import Repr
    from multiprocessing import cpu_count
    str = unicode # noqa
    from ..packages.backports.functools_lru_cache import lru_cache
    from ..packages.backports.shutil_get_terminal_size import get_terminal_size
    from ..packages.backports.tempfile import TemporaryDirectory
    import subprocess32 as subprocess
else:
    from io import StringIO
    from reprlib import Repr
    str = str
    from functools import lru_cache
    from shutil import get_terminal_size
    from tempfile import TemporaryDirectory
    import subprocess
    from os import cpu_count
