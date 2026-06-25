# MineControl

A self-hosted, flight-sheet-style mining fleet manager. Head node runs on TrueNAS
(via Dockge), with lightweight agents on each rig reporting telemetry and pulling
config (coin/pool/wallet + per-GPU overclock profiles).

> **Before you push this to GitHub:** this repo intentionally ships with no
> wallet addresses, IPs, or hostnames baked in — everything real lives in your
> own `.service` file (gitignored) or shell env, not in tracked files. Keep it
> that way: don't commit a filled-in `minecontrol-agent.service`, only the
> `.template` version.

## Deploy the head node (TrueNAS via Dockge)

1. On your TrueNAS box: `git clone <YOUR_REPO_URL> minecontrol`
2. Edit `docker/docker-compose.yml`:
   - Replace `<YOUR_TRUENAS_DATASET_PATH>` with a real dataset path for persistent SQLite storage
   - Set `DISCORD_WEBHOOK_URL` (or set it via Dockge's environment UI instead)
3. In Dockge, add this as a new stack pointing at `docker/docker-compose.yml`
4. Start the stack — it'll be reachable at `http://<truenas-ip>:8000`

## Add the Cloudflare Tunnel + Access policy

1. In your existing Cloudflare Tunnel config, add a new public hostname
   (e.g. `miners.yourdomain.com`) pointing at `http://<truenas-lan-ip>:8000`
2. In Zero Trust → Access → Applications, create a new app for that hostname,
   using your existing identity provider, scoped to your email
3. Confirm unauthenticated requests to the hostname get blocked by Access
   before reaching the app

## Set up the agent on a rig (e.g. superserver)

**Option A — install script:**
```bash
git clone <YOUR_REPO_URL> minecontrol
cd minecontrol
sudo bash agent/install.sh
```
It'll prompt for the head node URL, rig name, and rig ID (leave ID blank if
you haven't registered the rig yet — see below), then writes and enables the
systemd service for you. Read the script before running it; it installs a
root-level service that can restart your miner.

**Option B — manual:**
1. `git clone <YOUR_REPO_URL> minecontrol` on the rig
2. `pip3 install requests`
3. Copy `agent/minecontrol-agent.service.template` to
   `/etc/systemd/system/minecontrol-agent.service` and fill in every
   `<PLACEHOLDER>`
4. `sudo systemctl daemon-reload && sudo systemctl enable --now minecontrol-agent`

**Register the rig with the head node** (either before or after installing the agent):
```bash
curl -X POST http://<truenas-ip>:8000/api/rigs \
  -H "Content-Type: application/json" \
  -d '{"name":"superserver","lan_ip":"<rig-lan-ip>","agent_type":"native","miner_api_port":4068}'
```
Note the returned `id` — set it as `MINECONTROL_RIG_ID` in the service file,
then `sudo systemctl daemon-reload && sudo systemctl restart minecontrol-agent`.

**Important — verify PeakMiner's actual API schema before relying on it.**
`agent.py`'s `fetch_peakminer_stats()` assumes a `devices` list with fields like
`index`, `hashrate`, `temperature`, `fan_speed`, etc. This was not independently
confirmed against PeakMiner's real `:4068/summary` JSON output — check it with:
```bash
curl http://127.0.0.1:4068/summary | python3 -m json.tool
```
and adjust the field names in `fetch_peakminer_stats()` to match. The agent
already falls back to `nvidia-smi` for fan/temp/clocks if the miner API is
unreachable or fields are missing, but hashrate/shares only come from PeakMiner.

## Multi-miner support

Flight sheets specify which miner to use via the `miner` field (defaults to
`peakminer`). Currently supported: `peakminer`, `srbminer`. Each miner has
its own adapter (`agent/miners/`) that translates the same flight-sheet/OC-
profile data into that miner's actual CLI syntax - SRBMiner's
comma-separated per-flag-type values vs. PeakMiner's per-GPU-indexed flags,
for example. Adding a new miner means writing one adapter file and
registering it in `agent/miners/__init__.py` - the head node's data model
doesn't change.

## Board-level fan control (Octominer)

For rigs with an Octominer chassis, board fan profiles control the chassis
fans via the real `fan_controller_cli` binary HiveOS itself uses (confirmed
against HiveOS's public source) - either a fixed manual speed or an auto
curve bounded by min/max fan% and a target core temperature. This is
separate from per-GPU fan control (which still goes through OCProfile for
plain Linux rigs using nvidia-settings) since Octominer's fan controller is
whole-board, independent of the GPU driver.

```bash
curl -X POST http://<truenas-ip>:8000/api/board-fan-profiles -H "Content-Type: application/json" -d '{
  "name": "Octominer-Auto",
  "mode": "auto",
  "min_fan_pct": 40, "max_fan_pct": 100,
  "target_core_temp_c": 65, "target_mem_temp_c": 80
}'
```

Assign it alongside a flight sheet via `/api/assignments/rig` using
`board_fan_profile_id`. On rigs without Octominer hardware, the agent
auto-detects this (checks for the controller binary) and simply no-ops -
safe to leave a board fan profile assigned even on a plain Linux box.

**Note:** the auto-curve here is a simplified linear ramp (+5% fan per °C
over target, clamped to min/max) - HiveOS's own algorithm also factors in
each GPU's individual fan% to compensate for cards already maxed out. Good
enough as a starting point; refine `agent/boards/octominer.py` if you want
closer parity.

## Hashrate watchdog

Configurable per rig, HiveOS-style. "Normal" hashrate is auto-baselined
from a rolling average of recent check-ins (per rig and optionally per-GPU)
rather than a number you maintain by hand. On a drop below the configured
percentage of baseline:
- 1st consecutive failure -> agent restarts the miner
- 2nd consecutive failure -> agent reboots the rig + Discord alert fires

A `startup_grace_s` window (default 300s / 5 min) suppresses checks after
any restart/reboot so a still-warming-up miner doesn't immediately
re-trigger - matches the grace period John uses on his existing rigs.

```bash
curl -X POST http://<truenas-ip>:8000/api/watchdog-policies -H "Content-Type: application/json" -d '{
  "name": "Standard-Watchdog",
  "check_interval_s": 120,
  "startup_grace_s": 300,
  "baseline_window_samples": 20,
  "global_hashrate_min_pct": 80,
  "per_gpu_hashrate_min_pct": 70
}'
```

Assign via `/api/assignments/rig` using `watchdog_policy_id`. Rebooting can
be disabled per-rig by setting `MINECONTROL_ALLOW_REBOOT=false` in the
agent's systemd unit - in that case the agent restarts the miner again
instead of rebooting, but the Discord alert still fires on the 2nd failure
so you're not silently stuck.

## Create your first wallet, flight sheet, and OC profiles

Wallets are now their own object (like HiveOS's Wallets page) — create one
per coin/address, then reference it by `wallet_id` from any flight sheet.
`pool_url` and `worker_name_template` support template variables resolved
at sync time: `{wallet}`, `{worker}`, `{rig_name}`.

```bash
# Wallet (reusable across multiple flight sheets)
curl -X POST http://<truenas-ip>:8000/api/wallets -H "Content-Type: application/json" -d '{
  "name": "Main Pearl Wallet",
  "coin": "pearl",
  "address": "YOUR_WALLET_ADDRESS"
}'
# note the returned id - that's your wallet_id below

# OC profile for healthy GPUs
curl -X POST http://<truenas-ip>:8000/api/oc-profiles -H "Content-Type: application/json" -d '{
  "name": "Ampere-150W-Standard",
  "core_lock_mhz": 1450, "mem_lock_mhz": 5001,
  "core_offset_mhz": 200, "power_limit_w": 150, "fan_target_pct": 100
}'

# Reduced profile for a GPU with a known issue (e.g. fan fault)
curl -X POST http://<truenas-ip>:8000/api/oc-profiles -H "Content-Type: application/json" -d '{
  "name": "Throttled-100W",
  "core_lock_mhz": 1300, "mem_lock_mhz": 5001,
  "core_offset_mhz": 0, "power_limit_w": 100, "fan_target_pct": 100,
  "notes": "Reduced power - suspected fan fault"
}'

# Flight sheet, referencing the wallet by id, with {wallet}/{worker} template vars
curl -X POST http://<truenas-ip>:8000/api/flight-sheets -H "Content-Type: application/json" -d '{
  "name": "Pearl-Solo-Herominers",
  "coin": "pearl",
  "pool_url": "stratum+tcp://us2.pearl.herominers.com:1200/{wallet}.{worker}",
  "wallet_id": 1,
  "worker_name_template": "{rig_name}"
}'

# Assign flight sheet to the rig
curl -X POST http://<truenas-ip>:8000/api/assignments/rig -H "Content-Type: application/json" \
  -d '{"rig_id": 1, "flight_sheet_id": 1}'

# Assign OC profile to a specific GPU (find gpu_id via GET /api/rigs/1 after first check-in)
curl -X POST http://<truenas-ip>:8000/api/assignments/gpu -H "Content-Type: application/json" \
  -d '{"gpu_id": 1, "oc_profile_id": 1}'
```

Reusing the same wallet across multiple flight sheets (e.g. mining the same
coin on two different pools) just means reusing the same `wallet_id` — the
address itself is never duplicated or retyped.

The agent picks up new assignments on its next sync cycle (every
`MINECONTROL_INTERVAL` seconds, default 30) and restarts the miner with the
new config automatically.

## Updating an agent after a `git pull`

```bash
cd minecontrol && git pull
sudo systemctl restart minecontrol-agent
```

## What's tested vs. not yet verified

**Tested end-to-end (verified working in this build):**
- Rig registration, check-in, telemetry storage
- Flight sheet + OC profile CRUD
- Per-GPU OC profile merge logic via `/api/rigs/{id}/sync`
- Agent's launch-script generation (valid bash, correct PeakMiner flags)
- Alert firing on temp/fan-fault conditions
- Agent fails fast with a clear error if required env vars are missing,
  rather than silently using placeholder/fake defaults

**Not yet verified — confirm on your actual hardware before relying on it:**
- PeakMiner's real `:4068/summary` JSON schema (agent's field mapping is a
  best guess pending your confirmation)
- Octominer/HiveOS agent variant (not yet built — this version covers the
  native Linux/superserver case only)
- The dashboard's WebSocket live view, under real multi-rig load
