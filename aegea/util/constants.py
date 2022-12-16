import json
import os
from typing import Any, Dict

_constants_filename = os.path.join(os.path.dirname(__file__), "..", "constants.json")
_constants = {}  # type: Dict[str, Any]

def write():
    from . import aws
    raise NotImplementedError()
    """
    constants = {"instance_types": {}}
    instance_attrs = ["vcpu", "memory", "storage", "gpu", "clockSpeed", "networkPerformance"]
    spot_instance_families = {"m3", "m4", "c3", "c4", "r3", "r4", "i2", "i3", "d2", "g2"}
    for product in aws.get_ec2_products():
        if not any(product["attributes"]["instanceType"].startswith(fam + ".") for fam in spot_instance_families):
            continue
        traits = {field: product["attributes"].get(field) for field in instance_attrs}
        constants["instance_types"][product["attributes"]["instanceType"]] = traits
    with open(_constants_filename, "w") as fh:
        json.dump(constants, fh)
    """

def get(field):
    if not _constants:
        with open(_constants_filename) as fh:
            _constants.update(json.load(fh))
    return _constants.get(field)
