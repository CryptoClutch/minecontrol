"""
Database models for MineControl.

Schema:
  rigs                - physical mining machines (superserver, Octominer boards)
  gpus                - individual GPUs belonging to a rig
  wallets             - reusable wallet addresses, one per coin, referenced by flight sheets
  flight_sheets       - rig-level mining config: coin / miner / pool template / wallet reference
  oc_profiles         - reusable per-GPU overclock presets (core/mem lock, offset, power limit)
  gpu_assignments     - binds a GPU to a specific OC profile
  board_fan_profiles  - whole-board fan control (Octominer-style auto curve or manual %)
  watchdog_policies   - configurable hashrate watchdog thresholds + escalation timing
  watchdog_state      - runtime tracking for the watchdog's rolling baseline + escalation ladder
  rig_assignments     - binds a rig to a flight sheet, board fan profile, and watchdog policy
  alerts_log          - history of alerts fired (temp, offline, hashrate drop, etc.)
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


class Rig(Base):
    __tablename__ = "rigs"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)          # e.g. "superserver"
    lan_ip = Column(String, nullable=False)                      # e.g. "192.168.1.50"
    agent_type = Column(String, nullable=False, default="native")  # "native" | "octominer"
    miner_api_port = Column(Integer, default=4068)               # PeakMiner API port
    last_seen = Column(DateTime, nullable=True)
    status = Column(String, default="unknown")                   # online/offline/unknown
    created_at = Column(DateTime, default=utcnow)

    gpus = relationship("GPU", back_populates="rig", cascade="all, delete-orphan")
    rig_assignment = relationship(
        "RigAssignment", back_populates="rig", uselist=False, cascade="all, delete-orphan"
    )


class GPU(Base):
    __tablename__ = "gpus"

    id = Column(Integer, primary_key=True)
    rig_id = Column(Integer, ForeignKey("rigs.id"), nullable=False)
    gpu_index = Column(Integer, nullable=False)   # index within the rig (0-6, etc.)
    name = Column(String, nullable=True)          # e.g. "RTX 3070", filled in on first report

    # latest reported telemetry (updated on every agent check-in)
    hashrate = Column(Float, default=0.0)
    temp_c = Column(Float, default=0.0)
    fan_pct = Column(Float, default=0.0)
    power_draw_w = Column(Float, default=0.0)
    core_clock_mhz = Column(Float, default=0.0)
    mem_clock_mhz = Column(Float, default=0.0)
    shares_ok = Column(Integer, default=0)
    shares_invalid = Column(Integer, default=0)
    last_updated = Column(DateTime, nullable=True)

    rig = relationship("Rig", back_populates="gpus")
    assignment = relationship(
        "GPUAssignment", back_populates="gpu", uselist=False, cascade="all, delete-orphan"
    )


class Wallet(Base):
    """
    A reusable wallet address, independent of any single flight sheet -
    mirrors HiveOS's Wallets page. One wallet can be referenced by many
    flight sheets (e.g. the same Pearl address across several pool configs).
    """
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)   # e.g. "Main Pearl Wallet"
    coin = Column(String, nullable=False)                 # e.g. "pearl" - for filtering in the UI
    address = Column(String, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    flight_sheets = relationship("FlightSheet", back_populates="wallet")


class FlightSheet(Base):
    """
    Rig-level mining config: coin, pool URL template, and a reference to a
    Wallet (not a raw address). pool_url and worker_name_template may contain
    placeholders resolved at sync time:
      {wallet}     -> the referenced wallet's address
      {rig_name}   -> the rig's name
      {worker}     -> alias for {rig_name}, for HiveOS-style familiarity
    e.g. pool_url = "stratum+tcp://pool.example.com:3333"
         worker_name_template = "{rig_name}"
    or, for pools that encode wallet+worker directly in the URL:
         pool_url = "stratum+tcp://pool.example.com:3333/{wallet}.{worker}"
    """
    __tablename__ = "flight_sheets"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    coin = Column(String, nullable=False)              # e.g. "pearl"
    miner = Column(String, nullable=False, default="peakminer")  # "peakminer" | "srbminer" | ...
    pool_url = Column(String, nullable=False)           # may contain {wallet}/{worker}/{rig_name}
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    worker_name_template = Column(String, default="{rig_name}")
    extra_args = Column(Text, nullable=True)            # raw passthrough flags, optional
    created_at = Column(DateTime, default=utcnow)

    wallet = relationship("Wallet", back_populates="flight_sheets")
    rig_assignments = relationship("RigAssignment", back_populates="flight_sheet")


class OCProfile(Base):
    __tablename__ = "oc_profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)   # e.g. "Ampere-150W-Standard"
    core_lock_mhz = Column(Integer, nullable=True)        # --gpu-lcoreN
    mem_lock_mhz = Column(Integer, nullable=True)         # --gpu-lmemN
    core_offset_mhz = Column(Integer, nullable=True)      # --gpu-coreN (applied alongside lock)
    power_limit_w = Column(Integer, nullable=True)        # --gpu-powerN (watts)
    fan_target_pct = Column(Integer, nullable=True)       # applied via nvidia-settings, not a peakminer flag
    notes = Column(Text, nullable=True)

    gpu_assignments = relationship("GPUAssignment", back_populates="oc_profile")


class GPUAssignment(Base):
    """Binds a single GPU (by rig + index) to an OC profile."""
    __tablename__ = "gpu_assignments"

    id = Column(Integer, primary_key=True)
    gpu_id = Column(Integer, ForeignKey("gpus.id"), unique=True, nullable=False)
    oc_profile_id = Column(Integer, ForeignKey("oc_profiles.id"), nullable=False)
    applied_at = Column(DateTime, nullable=True)
    status = Column(String, default="pending")  # pending | applied | failed

    gpu = relationship("GPU", back_populates="assignment")
    oc_profile = relationship("OCProfile", back_populates="gpu_assignments")


class BoardFanProfile(Base):
    """
    Whole-board fan control, matching Octominer's auto-fan model: either a
    fixed manual speed, or an auto curve bounded by min/max fan% that ramps
    based on target core/mem temperatures. This is board-level, not per-GPU -
    unlike OCProfile, which is assigned per-GPU.
    """
    __tablename__ = "board_fan_profiles"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    mode = Column(String, nullable=False, default="auto")  # "auto" | "manual"

    # auto mode
    min_fan_pct = Column(Integer, nullable=True)
    max_fan_pct = Column(Integer, nullable=True)
    target_core_temp_c = Column(Integer, nullable=True)
    target_mem_temp_c = Column(Integer, nullable=True)

    # manual mode
    manual_fan_pct = Column(Integer, nullable=True)

    notes = Column(Text, nullable=True)


class WatchdogPolicy(Base):
    """
    Configurable hashrate watchdog, HiveOS-style. "Normal" hashrate is
    auto-baselined from a rolling average of recent check-ins (per rig and
    per GPU) rather than a fixed number you have to maintain by hand.

    Escalation ladder on trigger (fixed sequence, thresholds configurable):
      1st consecutive failure  -> restart the miner
      2nd consecutive failure  -> reboot the rig + send Discord alert
    startup_grace_s suppresses checks for a window after any restart/reboot
    so a still-warming-up miner doesn't immediately re-trigger.
    """
    __tablename__ = "watchdog_policies"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    check_interval_s = Column(Integer, nullable=False, default=120)
    startup_grace_s = Column(Integer, nullable=False, default=300)  # 5 min default, per John's usual setup
    baseline_window_samples = Column(Integer, nullable=False, default=20)
    global_hashrate_min_pct = Column(Integer, nullable=True)   # e.g. 80 -> alert if total < 80% of baseline
    per_gpu_hashrate_min_pct = Column(Integer, nullable=True)  # e.g. 70 -> alert if any single GPU < 70% of its baseline
    notes = Column(Text, nullable=True)


class WatchdogState(Base):
    """
    Runtime state for the watchdog - NOT user-editable. One row per rig
    (global) and one per rig+gpu_index (per-GPU), tracking the rolling
    baseline and the escalation ladder's current position.
    """
    __tablename__ = "watchdog_state"

    id = Column(Integer, primary_key=True)
    rig_id = Column(Integer, ForeignKey("rigs.id"), nullable=False)
    gpu_index = Column(Integer, nullable=True)  # NULL = rig-level/global row

    baseline_samples_json = Column(Text, default="[]")  # JSON list of recent hashrate samples
    baseline_avg = Column(Float, default=0.0)

    consecutive_failures = Column(Integer, default=0)
    last_action = Column(String, nullable=True)       # "restart" | "reboot" | None
    last_action_at = Column(DateTime, nullable=True)
    grace_until = Column(DateTime, nullable=True)      # suppress checks until this time


class RigAssignment(Base):
    """Binds a rig to a flight sheet (coin/pool/wallet), and optionally a
    board fan profile and watchdog policy."""
    __tablename__ = "rig_assignments"

    id = Column(Integer, primary_key=True)
    rig_id = Column(Integer, ForeignKey("rigs.id"), unique=True, nullable=False)
    flight_sheet_id = Column(Integer, ForeignKey("flight_sheets.id"), nullable=False)
    board_fan_profile_id = Column(Integer, ForeignKey("board_fan_profiles.id"), nullable=True)
    watchdog_policy_id = Column(Integer, ForeignKey("watchdog_policies.id"), nullable=True)
    applied_at = Column(DateTime, nullable=True)
    status = Column(String, default="pending")  # pending | applied | failed
    pending_action = Column(String, nullable=True)   # "restart" | "reboot" | None - set by watchdog, cleared once agent acts on it

    rig = relationship("Rig", back_populates="rig_assignment")
    flight_sheet = relationship("FlightSheet", back_populates="rig_assignments")
    board_fan_profile = relationship("BoardFanProfile")
    watchdog_policy = relationship("WatchdogPolicy")


class AlertLog(Base):
    __tablename__ = "alerts_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=utcnow)
    rig_id = Column(Integer, ForeignKey("rigs.id"), nullable=True)
    gpu_index = Column(Integer, nullable=True)
    alert_type = Column(String, nullable=False)   # "temp_high" | "offline" | "hashrate_drop" | "fan_fault" | etc.
    message = Column(Text, nullable=False)
    discord_sent = Column(Boolean, default=False)
