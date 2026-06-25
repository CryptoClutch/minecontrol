import json
import os
from .base import MinerAdapter


class SRBMinerAdapter(MinerAdapter):
    """
    SRBMiner-MULTI adapter. Unlike PeakMiner, SRBMiner takes comma-separated
    per-flag-type values (one flag, all GPU values together) rather than a
    flag-per-GPU-index, and supports fan control natively via --gpu-fan0.
    """
    name = "srbminer"

    @property
    def default_api_url(self) -> str:
        # SRBMiner's HTTP API, when enabled with --api-enable (default port 21550)
        return "http://127.0.0.1:21550"

    def build_launch_command(self, cfg: dict) -> str:
        gpus_sorted = sorted(cfg["gpus"], key=lambda g: g["gpu_index"])
        gpu_ids = ",".join(str(g["gpu_index"]) for g in gpus_sorted)

        def csv_or_none(field):
            vals = [g.get(field) for g in gpus_sorted]
            if any(v is None for v in vals):
                return None
            return ",".join(str(v) for v in vals)

        cclock = csv_or_none("core_lock_mhz")
        mclock = csv_or_none("mem_lock_mhz")
        plimit = csv_or_none("power_limit_w")
        fan = csv_or_none("fan_target_pct")

        flags = [
            "--algorithm pearlhash" if cfg["coin"] == "pearl" else f"--algorithm {cfg['coin']}",
            f"--pool {cfg['pool_url']}",
            f"--wallet {cfg['wallet']}",
            f"--worker {cfg['worker_name']}",
            "--disable-cpu",
            f"--gpu-id {gpu_ids}",
        ]
        if cclock:
            flags.append(f"--gpu-cclock0 {cclock}")
        if mclock:
            flags.append(f"--gpu-mclock0 {mclock}")
        if plimit:
            flags.append(f"--gpu-plimit0 {plimit}")
        if fan:
            flags.append(f"--gpu-fan0 {fan}")

        extra = cfg.get("extra_args") or ""
        if extra:
            flags.append(extra)
        flags.append("--api-enable")
        flags.append(f"--api-rig-name {cfg['worker_name']}")
        flags.append("--log-file ./srbminer.log")

        flags_str = " \\\n  ".join(flags)
        return f"./srbminer_custom_bin \\\n  {flags_str}"

    def fan_setup_lines(self, cfg: dict) -> list[str]:
        # SRBMiner handles fan natively via --gpu-fan0, no nvidia-settings needed
        return []

    def parse_stats(self, raw_json: dict) -> list[dict]:
        # SRBMiner's API returns a dict keyed by GPU index under "algorithms"/"devices"
        # depending on version - this targets the common "gpus" list shape.
        # NOTE: verify against a real running instance and adjust field names.
        gpus = []
        for dev in raw_json.get("gpus", []):
            gpus.append({
                "gpu_index": dev.get("device_id"),
                "name": dev.get("name"),
                "hashrate": dev.get("hashrate_total_now", 0.0),
                "temp_c": dev.get("temperature", 0.0),
                "fan_pct": dev.get("fan_speed", 0.0),
                "power_draw_w": dev.get("power_usage", 0.0),
                "core_clock_mhz": dev.get("core_clock", 0.0),
                "mem_clock_mhz": dev.get("memory_clock", 0.0),
                "shares_ok": dev.get("accepted_shares", 0),
                "shares_invalid": dev.get("rejected_shares", 0),
            })
        return gpus
