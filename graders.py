"""
graders.py — Task graders for the SRE Fleet Gym.

Each grader receives an EpisodeRecord and returns a float score in (0.0, 1.0).
Scores are clamped to [0.01, 0.99] to satisfy the OpenEnv validator.

Milestone-based scoring with partial progress signals:
  - log_read:         Agent inspected the machine's logs          (+0.10)
  - pid_identified:   Agent targeted the correct anomaly PID      (+0.25)
  - node_isolated:    Agent drained/isolated the node (cascade)   (+0.15)
  - service_restored: Machine reached HEALTHY status              (+0.25)
"""

from __future__ import annotations

from typing import Dict

from fleet_simulator import (
    EpisodeRecord,
    MachineStatus,
    Command,
    ProcessState,
)


# ── Milestone Scoring Weights ──────────────────────────────────────────────

MILESTONE_WEIGHTS = {
    "log_read": 0.10,
    "pid_identified": 0.25,
    "node_isolated": 0.15,
    "service_restored": 0.25,
}


def _calc_milestone_score(milestones: Dict[str, Dict[str, bool]], relevant_keys: list | None = None) -> tuple[float, list[str]]:
    """
    Calculate milestone completion score across all tracked machines.
    Returns (score_0_to_1, feedback_lines).
    """
    if not milestones:
        return 0.0, ["No milestones tracked."]

    feedback = []
    total_weighted = 0.0
    achieved_weighted = 0.0
    
    keys = relevant_keys or list(MILESTONE_WEIGHTS.keys())

    for machine_id, ms in milestones.items():
        for name, achieved in ms.items():
            if name not in keys:
                continue
            weight = MILESTONE_WEIGHTS.get(name, 0.10)
            total_weighted += weight
            if achieved:
                achieved_weighted += weight
                feedback.append(f"  ✅ {machine_id}: {name}")
            else:
                feedback.append(f"  ❌ {machine_id}: {name}")

    ratio = achieved_weighted / total_weighted if total_weighted > 0 else 0.0
    return ratio, feedback


def grade_single_machine(episode: EpisodeRecord) -> Dict:
    """
    Easy grader: 1 machine, disk full from log_rotator.
    
    Milestone scoring:
    - log_read:          Agent inspected machine logs        (+0.15)
    - pid_identified:    Agent killed log_rotator or used clear_disk  (+0.30)
    - service_restored:  Machine is HEALTHY                  (+0.40)
    - Step bonus: +0.10 for ≤ 3 steps
    - Step penalty: -0.05 per extra step beyond 5
    """
    score = 0.01
    feedback = []

    initial_machine = episode.initial_fleet[0] if episode.initial_fleet else None
    if not initial_machine:
        return {"score": 0.01, "feedback": ["No initial fleet found."]}

    # Find the anomaly PID
    anomaly_pids = [p.pid for p in initial_machine.processes if p.is_anomaly]

    # ── Milestone: log_read ──────────────────────────────────────────
    log_read = False
    if episode.milestones:
        for mid, ms in episode.milestones.items():
            if ms.get("log_read"):
                log_read = True
    
    if log_read:
        score += 0.15
        feedback.append("✅ Inspected machine logs (+0.15)")

    # ── Milestone: pid_identified ────────────────────────────────────
    killed_correct = False
    used_clear_disk = False
    used_reboot = False

    for action in episode.actions:
        if action.command == Command.KILL_PID:
            target_pid = int(action.target) if action.target else -1
            if target_pid in anomaly_pids:
                killed_correct = True
                feedback.append(f"✅ Correctly identified and killed anomaly PID {target_pid} (+0.30)")
        elif action.command == Command.CLEAR_DISK:
            used_clear_disk = True
            feedback.append("✅ Used clear_disk command to free disk space (+0.30)")
        elif action.command == Command.REBOOT:
            used_reboot = True
            feedback.append("⚠️ Used reboot — brute force approach (+0.15)")

    if killed_correct or used_clear_disk:
        score += 0.30
    elif used_reboot:
        score += 0.15

    # ── Milestone: service_restored ──────────────────────────────────
    if episode.final_fleet:
        final_machine = episode.final_fleet[0]
        if final_machine.status == MachineStatus.HEALTHY:
            score += 0.40
            feedback.append("✅ Machine restored to HEALTHY status (+0.40)")
        elif final_machine.disk_pct < 75.0:
            score += 0.20
            feedback.append("⚠️ Disk pressure relieved but machine not fully healthy (+0.20)")
        else:
            feedback.append("❌ Machine still in degraded/critical state")

    # ── Step bonus/penalty ───────────────────────────────────────────
    steps = episode.total_steps
    if steps <= 3 and score > 0.20:
        score = min(0.99, score + 0.10)
        feedback.append(f"🏆 Efficient: solved in {steps} steps (+0.10)")
    elif steps > 5:
        penalty = 0.05 * (steps - 5)
        score = max(0.01, score - penalty)
        feedback.append(f"⏱️ Took {steps} steps (penalty: -{penalty:.2f})")

    score = max(0.01, min(0.99, score))
    return {"score": round(score, 4), "max_score": 1.0, "feedback": feedback}


def grade_multi_machine(episode: EpisodeRecord) -> Dict:
    """
    Medium grader: 5 machines with mixed issues.
    
    Per-machine milestone scoring, averaged across fleet:
    - log_read:         +0.10 per machine
    - pid_identified:   +0.25 per machine
    - service_restored: +0.25 per machine
    Plus: wrong-kill penalty, efficiency bonus
    """
    feedback = []

    if not episode.final_fleet:
        return {"score": 0.01, "feedback": ["No final fleet state"]}

    total = len(episode.final_fleet)
    healthy = sum(1 for m in episode.final_fleet if m.status == MachineStatus.HEALTHY)

    # ── Milestone scoring ────────────────────────────────────────────
    milestone_keys = ["log_read", "pid_identified", "service_restored"]
    ms_score, ms_feedback = _calc_milestone_score(episode.milestones, milestone_keys)
    
    # Milestone component: 60% of total
    milestone_component = ms_score * 0.60
    feedback.append(f"Milestone progress: {ms_score:.0%} (+{milestone_component:.3f})")

    # ── Fleet health component: 25% of total ─────────────────────────
    health_component = (healthy / total * 0.25) if total > 0 else 0.0
    feedback.append(f"Fleet health: {healthy}/{total} machines healthy (+{health_component:.3f})")

    base_score = milestone_component + health_component

    # ── Wrong kills penalty ──────────────────────────────────────────
    wrong_kills = 0
    for action in episode.actions:
        if action.command == Command.KILL_PID:
            init_machine = next((m for m in episode.initial_fleet if m.id == action.machine_id), None)
            if init_machine and action.target:
                try:
                    target_pid = int(action.target)
                    proc = next((p for p in init_machine.processes if p.pid == target_pid), None)
                    if proc and not proc.is_anomaly:
                        wrong_kills += 1
                except ValueError:
                    wrong_kills += 1

    if wrong_kills > 0:
        penalty = 0.10 * wrong_kills
        base_score = max(0.01, base_score - penalty)
        feedback.append(f"⚠️ {wrong_kills} non-anomaly process(es) killed (penalty: -{penalty:.2f})")

    # ── Efficiency bonus ─────────────────────────────────────────────
    if episode.total_steps <= 10 and base_score >= 0.5:
        base_score = min(0.99, base_score + 0.10)
        feedback.append("🏆 Efficient resolution (+0.10)")

    base_score = max(0.01, min(0.99, base_score))
    return {"score": round(base_score, 4), "max_score": 1.0, "feedback": feedback}


def grade_cascade_failure(episode: EpisodeRecord) -> Dict:
    """
    Hard grader: 20 machines with dependency chains and DB deadlock.
    
    Scoring:
    - Milestone progress:     40% (log_read, pid_identified, node_isolated, service_restored)
    - Resolution order:       30% (fixing dependencies first: db → cache → app → edge)
    - Speed bonus:            15%
    - Wrong kill penalty:     -0.05 per offense
    - Trap penalty:           -0.20 for cache stampede
    """
    feedback = []

    if not episode.final_fleet:
        return {"score": 0.01, "feedback": ["No final fleet state"]}

    total = len(episode.final_fleet)
    healthy = sum(1 for m in episode.final_fleet if m.status == MachineStatus.HEALTHY)

    # ── Milestone progress (40%) ─────────────────────────────────────
    ms_score, ms_feedback = _calc_milestone_score(episode.milestones)
    milestone_component = ms_score * 0.40
    feedback.append(f"Milestone progress: {ms_score:.0%} (+{milestone_component:.3f})")

    # ── Resolution order (30%) ───────────────────────────────────────
    tier_order = {"db": 0, "cache": 1, "app": 2, "edge": 3, "mon": 4}
    action_tiers = []
    for action in episode.actions:
        if action.command in (Command.KILL_PID, Command.RESTART_SERVICE, Command.REBOOT, Command.CLEAR_DISK, Command.DRAIN_NODE):
            prefix = action.machine_id.split("-")[0]
            tier = tier_order.get(prefix, 5)
            action_tiers.append(tier)

    order_bonus = 0.0
    if len(action_tiers) >= 2:
        ordered_pairs = sum(1 for i in range(len(action_tiers) - 1) if action_tiers[i] <= action_tiers[i + 1])
        order_ratio = ordered_pairs / (len(action_tiers) - 1)
        order_bonus = order_ratio * 0.30
        feedback.append(f"Resolution order: {order_ratio:.0%} correct (+{order_bonus:.3f})")
    elif len(action_tiers) == 1:
        order_bonus = 0.30
        feedback.append(f"Resolution order: 100% correct (+{order_bonus:.3f})")

    # ── Speed bonus (15%) ────────────────────────────────────────────
    steps = episode.total_steps
    if steps <= 15:
        speed_bonus = 0.15
    elif steps <= 30:
        speed_bonus = 0.15 * (30 - steps) / 15.0
    else:
        speed_bonus = 0.0
    feedback.append(f"Speed bonus: {steps} steps (+{speed_bonus:.3f})")

    # ── Fleet health component (15%) ─────────────────────────────────
    health_component = (healthy / total * 0.15) if total > 0 else 0.0
    feedback.append(f"Fleet health: {healthy}/{total} machines healthy (+{health_component:.3f})")

    # ── Wrong kill penalty ───────────────────────────────────────────
    wrong_kills = 0
    for action in episode.actions:
        if action.command == Command.KILL_PID:
            init_machine = next((m for m in episode.initial_fleet if m.id == action.machine_id), None)
            if init_machine and action.target:
                try:
                    target_pid = int(action.target)
                    proc = next((p for p in init_machine.processes if p.pid == target_pid), None)
                    if proc and not proc.is_anomaly:
                        wrong_kills += 1
                except ValueError:
                    wrong_kills += 1

    wrong_kill_penalty = 0.05 * wrong_kills
    if wrong_kills > 0:
        feedback.append(f"⚠️ {wrong_kills} wrong kill(s) (penalty: -{wrong_kill_penalty:.2f})")

    final_score = milestone_component + order_bonus + speed_bonus + health_component - wrong_kill_penalty
    final_score = max(0.01, min(0.99, final_score))

    return {"score": round(final_score, 4), "max_score": 1.0, "feedback": feedback}


# ── Grader dispatcher ──────────────────────────────────────────────────────

GRADERS = {
    "single_machine": grade_single_machine,
    "multi_machine": grade_multi_machine,
    "cascade_failure": grade_cascade_failure,
}


def grade_episode(episode: EpisodeRecord) -> Dict:
    """Grade an episode using the appropriate task grader."""
    grader_fn = GRADERS.get(episode.task_name)
    if not grader_fn:
        return {"score": 0.01, "feedback": [f"No grader for task: {episode.task_name}"]}
    result = grader_fn(episode)
    result["task_name"] = episode.task_name
    result["total_steps"] = episode.total_steps
    return result
