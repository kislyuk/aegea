import sys

from ._boto3_loader import Loader

sys.modules[__name__] = Loader("client")  # type: ignore

__getattr__ = Loader("client").__getattr__  # required for linting
