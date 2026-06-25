import os
import re
import subprocess
from .base import BoardAdapter

# Default install path matches HiveOS's own layout - if you copied the
# controller binary somewhere else, override via OCTOFAN_BIN_PATH env var.
DEFAULT_BIN_PATH = "/hive/opt/octofan/fan_controller_cli"

# Octominer has 3 physical case fans, each one cooling a bank of 3 GPU slots
# (fan 0 -> slots 0-2, fan 1 -> slots 3-5, fan 2 -> slots 6-8, per HiveOS's
# octofan script). Fan IDs themselves are typically 0/6/9 on the controller
# (auto-detected by HiveOS at runtime) - we read them the same way below
# rather than hardcoding, since they can vary by unit.
NUM_FANS = 3


class OctominerAdapter(BoardAdapter):
    name = "octominer"

    def __init__(self):
        self.bin_path = os.environ.get("OCTOFAN_BIN_PATH", DEFAULT_BIN_PATH)

    def is_present(self) -> bool:
        return os.path.isfile(self.bin_path) and os.access(self.bin_path, os.X_OK)

    def _run(self, args: list[str]) -> str:
        try:
            result = subprocess.run(
                [self.bin_path] + args,
                capture_output=True, text=True, timeout=10
            )
            return result.stdout
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"[octominer] WARN: fan_controller_cli call failed: {e}")
            return ""

    def _percent_to_pwm(self, percent: float) -> int:
        pwm = round(255 * percent / 100)
        return max(0, min(255, pwm))

    def _read_status(self) -> str:
        """Raw -r output, parsed by the helpers below."""
        return self._run(["-r"])

    def _detect_fan_ids(self, status_text: str) -> list[int]:
        """
        Fan IDs aren't always 0/1/2 - HiveOS auto-detects them from the -r
        output ('FAN No. X RPM:' lines). Falls back to 0/1/2 if detection
        fails (e.g. on first run before the controller has reported).
        """
        ids = []
        for line in status_text.splitlines():
            m = re.match(r"FAN No\. (\d+) RPM:\s*(\d+)", line)
            if m and "max" not in line:
                fan_id, rpm = int(m.group(1)), int(m.group(2))
                if rpm > 10:  # filters out disconnected/non-existent fan slots
                    ids.append(fan_id)
        return ids[:NUM_FANS] if ids else [0, 1, 2]

    def apply_fan_config(self, board_fan_cfg: dict) -> None:
        if not board_fan_cfg:
            return

        mode = board_fan_cfg.get("mode", "auto")
        status_text = self._read_status()
        fan_ids = self._detect_fan_ids(status_text)

        if mode == "manual":
            pct = board_fan_cfg.get("manual_fan_pct")
            if pct is None:
                return
            pwm = self._percent_to_pwm(pct)
            for fan_id in fan_ids:
                self._run(["-f", str(fan_id), "-v", str(pwm)])
            return

        # auto mode: approximate the min/max + target-temp curve.
        # NOTE: this is a simplified version of HiveOS's own algorithm,
        # which also factors in each GPU's individual fan% to compensate
        # for cards already maxed out - that level of nuance needs GPU fan
        # data threaded through from the agent's main loop. This version
        # ramps fan speed linearly between min_fan_pct and max_fan_pct based
        # on how far the hottest reported temp is above target_core_temp_c.
        min_fan = board_fan_cfg.get("min_fan_pct", 30)
        max_fan = board_fan_cfg.get("max_fan_pct", 100)
        target_temp = board_fan_cfg.get("target_core_temp_c", 65)

        hottest_temp = self._get_hottest_gpu_temp()
        if hottest_temp is None:
            fan_pct = max_fan  # can't read temps - fail safe to max cooling
        else:
            over = hottest_temp - target_temp
            if over <= 0:
                fan_pct = min_fan
            else:
                # +5%/degree over target, clamped to the configured range
                fan_pct = min(max_fan, min_fan + over * 5)

        pwm = self._percent_to_pwm(fan_pct)
        for fan_id in fan_ids:
            self._run(["-f", str(fan_id), "-v", str(pwm)])

    def _get_hottest_gpu_temp(self):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                text=True, timeout=5
            )
            temps = [float(x) for x in out.strip().splitlines() if x.strip()]
            return max(temps) if temps else None
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            return None

    def read_board_telemetry(self) -> dict:
        status_text = self._read_status()
        telemetry = {}

        def grab(pattern, cast=float):
            m = re.search(pattern, status_text)
            if m:
                try:
                    return cast(m.group(1))
                except ValueError:
                    return None
            return None

        telemetry["intake_temp_c"] = grab(r"Temperature No\. 0\s*:?\s*(-?\d+\.?\d*)")
        telemetry["exhaust_temp_c"] = grab(r"Temperature No\. 1\s*:?\s*(-?\d+\.?\d*)")
        telemetry["psu_power_w"] = grab(r"PSU Pac:\s*(-?\d+\.?\d*)")
        telemetry["psu_voltage_ac"] = grab(r"PSU Vac:\s*(-?\d+\.?\d*)")

        return {k: v for k, v in telemetry.items() if v is not None}
