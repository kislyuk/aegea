from datetime import datetime, timedelta

from ... import logger
from .. import VerboseRepr, constants
from ..exceptions import AegeaException
from . import clients
from .iam import ensure_iam_role


class SpotFleetBuilder(VerboseRepr):
    # TODO: vivify from toolspec; vivify from SFR ID; update with incremental cores/memory requirements
    def __init__(self, launch_spec, cores=1, min_cores_per_instance=1, min_mem_per_core_gb=1.5, gpus_per_instance=0,
                 min_ephemeral_storage_gb=0, spot_price=None, duration_hours=None, client_token=None,
                 instance_type_prefixes=None, dry_run=False):
        if spot_price is None:
            spot_price = 1
        if "SecurityGroupIds" in launch_spec:
            launch_spec["SecurityGroups"] = [dict(GroupId=i) for i in launch_spec["SecurityGroupIds"]]
            del launch_spec["SecurityGroupIds"]
        self.launch_spec = launch_spec
        self.cores = cores
        self.min_cores_per_instance = min_cores_per_instance
        self.min_ephemeral_storage_gb = min_ephemeral_storage_gb
        if min_cores_per_instance > cores:
            raise AegeaException("SpotFleetBuilder: min_cores_per_instance cannot exceed cores")
        self.min_mem_per_core_gb = min_mem_per_core_gb
        self.gpus_per_instance = gpus_per_instance
        self.instance_type_prefixes = instance_type_prefixes
        self.dry_run = dry_run
        self.iam_fleet_role = self.get_iam_fleet_role()
        self.spot_fleet_request_config = dict(SpotPrice=str(spot_price),
                                              TargetCapacity=cores,
                                              IamFleetRole=self.iam_fleet_role.arn)
        if client_token:
            self.spot_fleet_request_config.update(ClientToken=client_token)
        if duration_hours:
            deadline = datetime.utcnow().replace(microsecond=0) + timedelta(hours=duration_hours)
            self.spot_fleet_request_config.update(ValidUntil=deadline,
                                                  TerminateInstancesWithExpiration=True)

    @classmethod
    def get_iam_fleet_role(cls):
        return ensure_iam_role("SpotFleet",
                               policies=["service-role/AmazonEC2SpotFleetTaggingRole"],
                               trust=["spotfleet"])

    def instance_types(self, max_overprovision=3):
        def compute_ephemeral_storage_gb(instance_data):
            if instance_data["storage"] == "EBS only":
                return 0
            count, size = [int(x) for x in instance_data["storage"].rstrip("SSD").rstrip("HDD").split("x")]
            return count * size

        max_cores = self.cores * max_overprovision
        max_mem_per_core = self.min_mem_per_core_gb * max_overprovision
        max_gpus = self.gpus_per_instance * max_overprovision
        for instance_type, instance_data in constants.get("instance_types").items():
            cores, gpus = int(instance_data["vcpu"]), int(instance_data["gpu"] or 0)
            mem_per_core = float(instance_data["memory"].rstrip(" GiB")) / cores
            if compute_ephemeral_storage_gb(instance_data) < self.min_ephemeral_storage_gb:
                continue
            if cores < self.min_cores_per_instance or cores > max_cores:
                continue
            if mem_per_core < self.min_mem_per_core_gb or mem_per_core > max_mem_per_core:
                continue
            if gpus < self.gpus_per_instance or gpus > max_gpus:
                continue
            if not any(instance_type.startswith(i) for i in self.instance_type_prefixes or [""]):
                continue
            yield instance_type, int(instance_data["vcpu"])

    def launch_specs(self, **kwargs):
        for instance_type, weighted_capacity in self.instance_types(**kwargs):
            yield dict(self.launch_spec,
                       InstanceType=instance_type,
                       WeightedCapacity=weighted_capacity)

    def __call__(self, **kwargs):
        self.spot_fleet_request_config["LaunchSpecifications"] = list(self.launch_specs())
        logger.debug(self.spot_fleet_request_config)
        res = clients.ec2.request_spot_fleet(DryRun=self.dry_run,
                                             SpotFleetRequestConfig=self.spot_fleet_request_config,
                                             **kwargs)
        return res["SpotFleetRequestId"]
