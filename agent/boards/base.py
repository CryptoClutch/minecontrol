"""
Board control adapter interface.

Separate from miner adapters (miners/) because fan/board control is a
hardware-platform concern, not a mining-software concern - the same
PeakMiner or SRBMiner binary can run on a plain Linux box (no board
controller, fan via nvidia-settings) or an Octominer chassis (dedicated
USB fan controller, independent of the GPU driver entirely).
"""

from abc import ABC, abstractmethod


class BoardAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def is_present(self) -> bool:
        """Return True if this board's control hardware/binary is actually
        available on this machine. Used so the agent can safely no-op on
        rigs that don't have this hardware rather than erroring."""
        raise NotImplementedError

    @abstractmethod
    def apply_fan_config(self, board_fan_cfg: dict) -> None:
        """
        Apply a BoardFanConfig dict: {mode, min_fan_pct, max_fan_pct,
        target_core_temp_c, target_mem_temp_c, manual_fan_pct}.
        Should be safe to call every sync cycle (idempotent).
        """
        raise NotImplementedError

    @abstractmethod
    def read_board_telemetry(self) -> dict:
        """
        Return whatever extra board-level telemetry is available (intake/
        exhaust temps, PSU power/voltage, fan RPMs) as a flat dict. Optional
        data - return {} if nothing is available. Not GPU-indexed; this is
        chassis-level info layered on top of the normal per-GPU report.
        """
        raise NotImplementedError
