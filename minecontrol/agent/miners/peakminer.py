from .base import MinerAdapter


class PeakMinerAdapter(MinerAdapter):
    name = "peakminer"

    @property
    def default_api_url(self) -> str:
        return "http://127.0.0.1:4068/summary"

    def build_launch_command(self, cfg: dict) -> str:
        gpu_flags = []
        for g in cfg["gpus"]:
            idx = g["gpu_index"]
            if g.get("core_lock_mhz") is not None:
                gpu_flags.append(f"--gpu-lcore{idx} {g['core_lock_mhz']}")
            if g.get("mem_lock_mhz") is not None:
                gpu_flags.append(f"--gpu-lmem{idx} {g['mem_lock_mhz']}")
            if g.get("core_offset_mhz") is not None:
                gpu_flags.append(f"--gpu-core{idx} {g['core_offset_mhz']}")
            if g.get("power_limit_w") is not None:
                gpu_flags.append(f"--gpu-power{idx} {g['power_limit_w']}")

        extra = cfg.get("extra_args") or ""
        base_flags = [
            f"--coin {cfg['coin']}",
            f"-o {cfg['pool_url']}",
            f"-u {cfg['wallet']}",
            f"--worker {cfg['worker_name']}",
        ]
        all_flag_lines = base_flags + gpu_flags + ([extra] if extra else []) + ["--api-port 4068"]
        flags_str = " \\\n  ".join(all_flag_lines)
        return f"./peakminer \\\n  {flags_str}"

    def fan_setup_lines(self, cfg: dict) -> list[str]:
        """PeakMiner has no built-in fan flag - fan control stays on nvidia-settings."""
        lines = []
        for g in cfg["gpus"]:
            if g.get("fan_target_pct") is not None:
                idx = g["gpu_index"]
                lines.append(
                    f'nvidia-settings -a "[gpu:{idx}]/GPUFanControlState=1" '
                    f'-a "[fan:{idx}]/GPUTargetFanSpeed={g["fan_target_pct"]}"'
                )
        return lines

    def parse_stats(self, raw_json: dict) -> list[dict]:
        # NOTE: field names below are a best guess pending confirmation against
        # PeakMiner's real :4068/summary output - verify with:
        #   curl http://127.0.0.1:4068/summary | python3 -m json.tool
        gpus = []
        for dev in raw_json.get("devices", []):
            gpus.append({
                "gpu_index": dev.get("index"),
                "name": dev.get("name"),
                "hashrate": dev.get("hashrate", 0.0),
                "temp_c": dev.get("temperature", 0.0),
                "fan_pct": dev.get("fan_speed", 0.0),
                "power_draw_w": dev.get("power_draw", 0.0),
                "core_clock_mhz": dev.get("core_clock", 0.0),
                "mem_clock_mhz": dev.get("mem_clock", 0.0),
                "shares_ok": dev.get("shares_accepted", 0),
                "shares_invalid": dev.get("shares_rejected", 0),
            })
        return gpus
