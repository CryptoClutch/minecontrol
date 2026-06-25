#!/usr/bin/env python3
"""
MineControl rig agent.

Runs locally on a mining rig (e.g. superserver). Every CHECKIN_INTERVAL seconds:
  1. Polls the configured miner's local stats API for per-GPU telemetry
     (via the matching miner adapter - see miners/)
  2. Reports that telemetry to the MineControl head node (on TrueNAS)
  3. Pulls the rig's current desired sync config (flight sheet + per-GPU OC profiles)
  4. If the config differs from what's currently running, regenerates the
     miner's launch script (using the adapter for that miner's CLI syntax)
     and restarts it via systemd

The miner in use is NOT hardcoded here - it comes from the sync config's
"miner" field (set per flight sheet on the head node), so the same agent
works whether the rig runs PeakMiner, SRBMiner, or any future adapter.

Run as a systemd service (see minecontrol-agent.service.template) under the
same user that manages the miner service, since restarting it requires
sudo systemctl access.
"""

import json
import os
import subprocess
import sys
import time
import requests

from miners import get_adapter
from boards import get_board_adapter

# ---------- Config (env-overridable - set these in minecontrol-agent.service) ----------
HEAD_NODE_URL = os.environ.get("MINECONTROL_HEAD_URL")           # e.g. http://<truenas-lan-ip>:8000
RIG_NAME = os.environ.get("MINECONTROL_RIG_NAME")                # must match the name used at registration
RIG_ID = os.environ.get("MINECONTROL_RIG_ID")                     # numeric id returned when you registered the rig
MINER_API_URL_OVERRIDE = os.environ.get("MINER_API_URL")          # optional - overrides the adapter's default
CHECKIN_INTERVAL = int(os.environ.get("MINECONTROL_INTERVAL", "30"))
MINER_DIR = os.environ.get("MINECONTROL_MINER_DIR", os.path.expanduser("~/miner"))
START_SCRIPT_PATH = os.path.join(MINER_DIR, "start.sh")
SYSTEMD_SERVICE = os.environ.get("MINECONTROL_SYSTEMD_SERVICE", "pearl-miner.service")
SCREEN_SESSION_NAME = os.environ.get("MINECONTROL_SCREEN_NAME", "superserver")
BOARD_TYPE_OVERRIDE = os.environ.get("MINECONTROL_BOARD_TYPE")     # "octominer" | "none" - auto-detected if unset
ALLOW_REBOOT = os.environ.get("MINECONTROL_ALLOW_REBOOT", "true").lower() == "true"

if not HEAD_NODE_URL or not RIG_NAME:
    print("[agent] ERROR: MINECONTROL_HEAD_URL and MINECONTROL_RIG_NAME must be set (see .env.example)")
    sys.exit(1)

try:
    BOARD = get_board_adapter(BOARD_TYPE_OVERRIDE)
    print(f"[agent] Board adapter: {BOARD.name}")
except ValueError as e:
    print(f"[agent] ERROR: {e}")
    sys.exit(1)

# track the last-applied config so we don't restart the miner every cycle for no reason
LAST_APPLIED_HASH = None


def fetch_miner_stats(adapter):
    """Poll the active miner's local stats API and normalize via its adapter."""
    api_url = MINER_API_URL_OVERRIDE or adapter.default_api_url
    try:
        resp = requests.get(api_url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[agent] WARN: could not reach {adapter.name} API at {api_url}: {e}")
        return []
    except ValueError as e:
        print(f"[agent] WARN: {adapter.name} API returned non-JSON response: {e}")
        return []

    try:
        return adapter.parse_stats(data)
    except Exception as e:
        print(f"[agent] WARN: failed to parse {adapter.name} stats: {e}")
        return []


def fetch_nvidia_smi_fallback():
    """
    Fallback/cross-check source: nvidia-smi directly, in case a miner's API
    doesn't expose everything we need (e.g. it may not report fan% reliably).
    Miner-agnostic - works the same regardless of which adapter is active.
    """
    try:
        out = subprocess.check_output([
            "nvidia-smi",
            "--query-gpu=index,name,temperature.gpu,fan.speed,power.draw,clocks.sm,clocks.mem",
            "--format=csv,noheader,nounits"
        ], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"[agent] WARN: nvidia-smi fallback failed: {e}")
        return {}

    result = {}
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        idx = int(parts[0])
        result[idx] = {
            "name": parts[1],
            "temp_c": float(parts[2]),
            "fan_pct": float(parts[3]),
            "power_draw_w": float(parts[4]),
            "core_clock_mhz": float(parts[5]),
            "mem_clock_mhz": float(parts[6]),
        }
    return result


def report_checkin(gpus):
    payload = {"rig_name": RIG_NAME, "gpus": gpus}
    try:
        resp = requests.post(f"{HEAD_NODE_URL}/api/rigs/checkin", json=payload, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[agent] WARN: check-in to head node failed: {e}")


def fetch_sync_config():
    if not RIG_ID:
        print("[agent] WARN: MINECONTROL_RIG_ID not set, skipping config sync (telemetry-only mode)")
        return None
    try:
        resp = requests.get(f"{HEAD_NODE_URL}/api/rigs/{RIG_ID}/sync", timeout=10)
        if resp.status_code == 409:
            print("[agent] No flight sheet assigned yet - skipping sync")
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"[agent] WARN: could not fetch sync config: {e}")
        return None


def build_start_script(cfg: dict, adapter) -> str:
    """
    Render the full launch script: X server + nvidia-smi persistence mode +
    any adapter-specific fan setup + the adapter's miner launch command,
    wrapped in a detached screen session.
    """
    fan_lines = adapter.fan_setup_lines(cfg)
    fan_block = "\n".join(fan_lines)
    launch_cmd = adapter.build_launch_command(cfg)

    return f"""#!/bin/bash
export DISPLAY=:0
cd {MINER_DIR}

# Start X if not running
X :0 &
sleep 3

nvidia-smi -pm 1
{fan_block}

screen -dmS {SCREEN_SESSION_NAME} {launch_cmd}
"""


def config_hash(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True)


def handle_pending_action(pending_action: str):
    """
    Act on a watchdog-triggered restart/reboot, then clear it on the head
    node so it doesn't get re-applied on the next sync. Reboot is gated by
    ALLOW_REBOOT so a misconfigured policy can't take down a rig you're
    not expecting to be unattended-reboot-safe.
    """
    if pending_action == "restart":
        print("[agent] Watchdog requested a miner restart")
        try:
            subprocess.run(["sudo", "systemctl", "restart", SYSTEMD_SERVICE], check=True)
        except subprocess.CalledProcessError as e:
            print(f"[agent] ERROR: watchdog restart failed: {e}")
            return  # don't clear pending_action - let it retry/escalate next cycle
        _clear_pending_action()

    elif pending_action == "reboot":
        if not ALLOW_REBOOT:
            print("[agent] Watchdog requested a REBOOT but MINECONTROL_ALLOW_REBOOT=false - skipping, restarting miner instead")
            try:
                subprocess.run(["sudo", "systemctl", "restart", SYSTEMD_SERVICE], check=True)
            except subprocess.CalledProcessError as e:
                print(f"[agent] ERROR: fallback restart failed: {e}")
            _clear_pending_action()
            return

        print("[agent] Watchdog requested a REBOOT - rebooting now")
        _clear_pending_action()  # clear before rebooting, since we won't get a chance after
        try:
            subprocess.run(["sudo", "reboot"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"[agent] ERROR: reboot command failed: {e}")


def _clear_pending_action():
    try:
        requests.post(f"{HEAD_NODE_URL}/api/rigs/{RIG_ID}/clear-pending-action", timeout=10)
    except requests.RequestException as e:
        print(f"[agent] WARN: failed to clear pending_action on head node: {e}")


def apply_sync_config(cfg: dict, adapter):
    """Regenerate and apply the launch script only if the resolved config actually changed."""
    global LAST_APPLIED_HASH

    h = config_hash(cfg)
    if h == LAST_APPLIED_HASH:
        return  # nothing changed, don't restart the miner needlessly

    print(f"[agent] New config detected (miner={adapter.name}) - regenerating start script and restarting miner")
    script_contents = build_start_script(cfg, adapter)

    try:
        os.makedirs(MINER_DIR, exist_ok=True)
        with open(START_SCRIPT_PATH, "w") as f:
            f.write(script_contents)
        os.chmod(START_SCRIPT_PATH, 0o755)
    except OSError as e:
        print(f"[agent] ERROR: failed to write start script: {e}")
        return

    try:
        subprocess.run(["sudo", "systemctl", "restart", SYSTEMD_SERVICE], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[agent] ERROR: failed to restart {SYSTEMD_SERVICE}: {e}")
        return

    LAST_APPLIED_HASH = h
    try:
        requests.post(f"{HEAD_NODE_URL}/api/assignments/rig/{RIG_ID}/confirm", timeout=10)
    except requests.RequestException:
        pass
    print("[agent] Config applied successfully")


def main_loop():
    print(f"[agent] MineControl agent starting for rig '{RIG_NAME}' -> {HEAD_NODE_URL}")
    while True:
        # Pull the desired config FIRST, every cycle - this tells us which
        # adapter should be active even before any restart happens, so a
        # freshly-started agent immediately knows how to read stats from a
        # miner that's already running from a previous apply.
        cfg = fetch_sync_config()
        adapter = None
        if cfg:
            try:
                adapter = get_adapter(cfg["miner"])
            except ValueError as e:
                print(f"[agent] ERROR: {e}")

        gpus = []
        if adapter:
            gpus = fetch_miner_stats(adapter)

        # cross-check / fill gaps with nvidia-smi - this also catches fan%
        # specifically, which is the field most likely to be missing or
        # unreliable from any given miner's own API
        smi_data = fetch_nvidia_smi_fallback()
        if not gpus and smi_data:
            # miner API unreachable (or no flight sheet assigned yet) - report
            # from nvidia-smi alone (no hashrate data in this case)
            gpus = [{"gpu_index": idx, **vals, "hashrate": 0.0, "shares_ok": 0, "shares_invalid": 0}
                    for idx, vals in smi_data.items()]
        else:
            for g in gpus:
                smi = smi_data.get(g["gpu_index"])
                if smi:
                    g["fan_pct"] = smi["fan_pct"] or g["fan_pct"]

        if gpus:
            report_checkin(gpus)
        else:
            print("[agent] WARN: no GPU data available this cycle (miner API and nvidia-smi both failed)")

        if cfg:
            if cfg.get("board_fan"):
                try:
                    BOARD.apply_fan_config(cfg["board_fan"])
                except Exception as e:
                    print(f"[agent] WARN: board fan config apply failed: {e}")

            if cfg.get("pending_action"):
                handle_pending_action(cfg["pending_action"])
            elif adapter:
                apply_sync_config(cfg, adapter)

        time.sleep(CHECKIN_INTERVAL)


if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        sys.exit(0)
