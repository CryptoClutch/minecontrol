"""
Hashrate watchdog evaluation.

Runs after every check-in (alongside the temp/fan alert checks in alerts.py).
For any rig with a WatchdogPolicy assigned:
  1. Update the rolling baseline (global + per-GPU) with this check-in's data
  2. If still within startup_grace_s of the last restart/reboot, skip checks
  3. Compare current hashrate against the rolling baseline; if below the
     configured percentage threshold, escalate:
       - 1st consecutive failure -> set pending_action="restart"
       - 2nd+ consecutive failure -> set pending_action="reboot" + Discord alert
  4. On a healthy check-in, reset the failure counter (recovery)

The actual restart/reboot is performed by the rig's own agent, which reads
pending_action off its next sync call and clears it once handled - the head
node only ever decides and signals, it never touches the rig directly.
"""

import json
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from app.models.db import Rig, GPU, RigAssignment, WatchdogState, AlertLog
from app.alerts import send_discord


def _get_or_create_state(db: Session, rig_id: int, gpu_index) -> WatchdogState:
    state = (
        db.query(WatchdogState)
        .filter(WatchdogState.rig_id == rig_id, WatchdogState.gpu_index == gpu_index)
        .first()
    )
    if not state:
        state = WatchdogState(rig_id=rig_id, gpu_index=gpu_index)
        db.add(state)
        db.flush()
    return state


def _update_baseline(state: WatchdogState, current_hashrate: float, window: int):
    samples = json.loads(state.baseline_samples_json or "[]")
    samples.append(current_hashrate)
    samples = samples[-window:]
    state.baseline_samples_json = json.dumps(samples)
    state.baseline_avg = sum(samples) / len(samples) if samples else 0.0


def _in_grace_period(state: WatchdogState) -> bool:
    if not state.grace_until:
        return False
    now = datetime.now(timezone.utc)
    grace_until = state.grace_until
    if grace_until.tzinfo is None:
        grace_until = grace_until.replace(tzinfo=timezone.utc)
    return now < grace_until


def _escalate(db: Session, rig: Rig, state: WatchdogState, scope_label: str, ra: RigAssignment, startup_grace_s: int):
    """A failure was detected for this scope (global or a specific GPU) - bump the ladder."""
    state.consecutive_failures += 1

    if state.consecutive_failures == 1:
        action = "restart"
    else:
        action = "reboot"

    state.last_action = action
    state.last_action_at = datetime.now(timezone.utc)
    state.grace_until = datetime.now(timezone.utc) + timedelta(seconds=startup_grace_s)

    # Escalate the rig's pending_action - reboot takes priority over restart
    # if multiple scopes (e.g. two GPUs) fail in the same check-in.
    if action == "reboot" or ra.pending_action != "reboot":
        ra.pending_action = action

    message = (
        f"Watchdog: {scope_label} hashrate below threshold "
        f"(failure #{state.consecutive_failures}) -> action: {action}"
    )

    if action == "reboot":
        # Always alert on reboot - this is the "fails again" escalation step
        sent = send_discord(f"**[{rig.name}]** {message}")
        db.add(AlertLog(
            rig_id=rig.id,
            gpu_index=state.gpu_index,
            alert_type="watchdog_reboot",
            message=message,
            discord_sent=sent,
        ))
    else:
        # First failure -> just log it, no Discord noise yet (matches "restart
        # once, and if it fails again reboot + notify" - the notify is on reboot)
        db.add(AlertLog(
            rig_id=rig.id,
            gpu_index=state.gpu_index,
            alert_type="watchdog_restart",
            message=message,
            discord_sent=False,
        ))


def _recover(state: WatchdogState):
    if state.consecutive_failures > 0:
        state.consecutive_failures = 0
        state.last_action = None


def check_watchdog(db: Session, rig: Rig):
    """Call after every check-in, once GPU telemetry has been committed."""
    ra = db.query(RigAssignment).filter(RigAssignment.rig_id == rig.id).first()
    if not ra or not ra.watchdog_policy:
        return  # no watchdog assigned to this rig

    policy = ra.watchdog_policy
    window = policy.baseline_window_samples

    # ---- Global (rig-level) check ----
    global_state = _get_or_create_state(db, rig.id, None)
    total_hashrate = sum(g.hashrate for g in rig.gpus)

    in_grace = _in_grace_period(global_state)
    _update_baseline(global_state, total_hashrate, window)

    if not in_grace and policy.global_hashrate_min_pct and global_state.baseline_avg > 0:
        threshold = global_state.baseline_avg * (policy.global_hashrate_min_pct / 100.0)
        if total_hashrate < threshold:
            _escalate(db, rig, global_state, "Total rig", ra, policy.startup_grace_s)
        else:
            _recover(global_state)
    elif not in_grace:
        _recover(global_state)

    # ---- Per-GPU check ----
    if policy.per_gpu_hashrate_min_pct:
        for gpu in rig.gpus:
            gpu_state = _get_or_create_state(db, rig.id, gpu.gpu_index)
            in_grace_gpu = _in_grace_period(gpu_state)
            _update_baseline(gpu_state, gpu.hashrate, window)

            if in_grace_gpu:
                continue
            if gpu_state.baseline_avg <= 0:
                continue

            threshold = gpu_state.baseline_avg * (policy.per_gpu_hashrate_min_pct / 100.0)
            if gpu.hashrate < threshold:
                _escalate(db, rig, gpu_state, f"GPU {gpu.gpu_index} ({gpu.name})", ra, policy.startup_grace_s)
            else:
                _recover(gpu_state)

    db.commit()
