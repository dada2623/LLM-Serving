import os

import torch
import vllm.logger as _vllm_logger

# The ROCm module logs its AMDSMI->HIP fallback during platform discovery.
# Its default rank-scoped logger imports distributed state, which recursively
# imports current_platform before discovery has finished. Log this import's
# warnings per process, then restore the original logger method.
_scope_check = _vllm_logger._should_log_with_scope
try:
    _vllm_logger._should_log_with_scope = lambda scope: True
    from vllm.platforms.rocm import RocmPlatform
finally:
    _vllm_logger._should_log_with_scope = _scope_check


class WslRocmPlatform(RocmPlatform):
    """Use HIP properties where AMDSMI is unavailable; single GPU only."""

    @classmethod
    def get_device_name(cls, device_id=0):
        return torch.cuda.get_device_name(device_id)

    @classmethod
    def get_device_uuid(cls, device_id=0):
        if device_id != 0 or torch.cuda.device_count() != 1:
            raise RuntimeError("Only one visible GPU is supported by this adapter")
        value = getattr(torch.cuda.get_device_properties(device_id), "uuid", None)
        if value:
            return str(value)
        visible = os.environ.get("ROCR_VISIBLE_DEVICES", "")
        if visible.startswith("GPU-") and "," not in visible:
            return visible
        raise RuntimeError("GPU UUID is unavailable; set ROCR_VISIBLE_DEVICES to one UUID")

    @classmethod
    def device_count(cls):
        return torch.cuda.device_count()

    @classmethod
    def is_fully_connected(cls, physical_device_ids):
        return len(physical_device_ids) <= 1

    @classmethod
    def get_all_device_numa_nodes(cls):
        # WSL does not expose Linux AMDSMI GPU NUMA topology.
        return None
