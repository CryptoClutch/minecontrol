"""
Miner adapter interface.

Each supported miner (PeakMiner, SRBMiner-MULTI, T-Rex, lolMiner, etc.)
implements this interface so the rest of the agent - and the head node's
data model (flight sheets, OC profiles) - stays miner-agnostic. Flight
sheets/OC profiles describe *what* to mine and *how* to tune each GPU;
the adapter is the only thing that knows the specific binary's CLI syntax
and stats API shape.

To add a new miner: create a new file in this package, subclass MinerAdapter,
implement build_launch_command() and parse_stats(), then register it in
miners/__init__.py's ADAPTERS dict.
"""

from abc import ABC, abstractmethod


class MinerAdapter(ABC):
    """One adapter instance per agent process, selected by the rig's configured miner."""

    # Subclasses set this - used for logging/debugging and the screen session name
    name: str = "base"

    @abstractmethod
    def build_launch_command(self, cfg: dict) -> str:
        """
        Given a RigSyncConfig dict (coin, pool_url, wallet, worker_name,
        extra_args, gpus: list of per-GPU OC settings), return the full
        command line (as a single string, ready to embed in a launch script)
        needed to start this miner with the resolved config.

        Does NOT include screen/X/fan setup - just the miner binary + flags.
        """
        raise NotImplementedError

    @abstractmethod
    def fan_setup_lines(self, cfg: dict) -> list[str]:
        """
        Return any shell commands needed to set fan speeds BEFORE launching
        the miner. Some miners (SRBMiner) control fan natively via a CLI flag
        and need nothing here; others (PeakMiner) have no fan flag and need
        nvidia-settings calls. Return [] if nothing extra is needed.
        """
        raise NotImplementedError

    @abstractmethod
    def parse_stats(self, raw_json: dict) -> list[dict]:
        """
        Given this miner's raw stats API JSON response, return a list of
        per-GPU dicts normalized to our schema:
          {gpu_index, name, hashrate, temp_c, fan_pct, power_draw_w,
           core_clock_mhz, mem_clock_mhz, shares_ok, shares_invalid}
        Missing fields should default sensibly (0.0 / None) rather than
        raising - a partially-populated report is better than a crash.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def default_api_url(self) -> str:
        """Default local stats API URL for this miner, used if the agent's
        env var override isn't set."""
        raise NotImplementedError
