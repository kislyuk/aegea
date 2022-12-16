import sys

from ._boto3_loader import Loader

sys.modules[__name__] = Loader("resource")  # type: ignore

__getattr__ = Loader("resource").__getattr__  # required for linting
