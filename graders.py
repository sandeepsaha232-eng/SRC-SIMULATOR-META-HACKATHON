"""
graders.py — Task graders for the SRE Fleet Gym.

Each grader receives an EpisodeRecord and returns a float score in (0.0, 1.0).
Scores are clamped to [0.01, 0.99] to satisfy the OpenEnv validator.
"""

from __future__ import annotations

from typing import Dict

from fleet_simulator import (
    EpisodeRecord,
    MachineStatus,
    Command,
    ProcessState,
)


def grade_single_machine(episode: EpisodeRecord) -> Dict:
    """
    Easy grader: 1 machine, 1 zombie process.
    
    Scoring:
    - 1.0 if agent killed the correct anomaly PID
    - 0.5 if agent used reboot (brute force)
    - 0.0 if anomaly is still alive or wrong PID killed
    - Bonus: +0.1 for doing it in ≤ 3 steps
    - Penalty: -0.05 per extra step beyond 3
    """
    score = 0.01
    feedback = []

    initial_machine = episode.initial_fleet[0] if episode.initial_fleet else None
    if not initial_machine:
        return {"score": 0.01, "feedback": ["No initial fleet found."]}

    # Find the anomaly PID
    anomaly_pids = [p.pid for p in initial_machine.processes if p.is_anomaly]

    # Check what agent did
    killed_correct = False
    used_reboot = False

    for action in episode.actions:
        if action.command == Command.KILL_PID:
            target_pid = int(action.target) if action.target else -1
            if target_pid in anomaly_pids:
                killed_correct = True
                feedback.append(f"✅ Correctly identified and killed anomaly PID {target_pid}")
        elif action.command == Command.REBOOT:
            used_reboot = True
            feedback.append("⚠️ Used reboot — brute force approach")

    if killed_correct:
        score = 0.99
    elif used_reboot:
        score = 0.5
    else:
        # Check if anomaly is gone from final fleet
        if episode.final_fleet:
            final_machine = episode.final_fleet[0]
            anomaly_alive = any(p.is_anomaly for p in final_machine.processes)
            if not anomaly_alive:
                score = 0.7
                feedback.append("Anomaly removed but method unclear")
            else:
                score = 0.01
                feedback.append("❌ Anomaly still alive in final state")

    # Step bonus/penalty
    steps = episode.total_steps
    if steps <= 3 and score > 0:
        score = min(0.99, score + 0.1)
        feedback.append(f"🏆 Efficient: solved in {steps} steps")
    elif steps > 5:
        penalty = 0.05 * (steps - 5)
        score = max(0.01, score - penalty)
        feedback.append(f"⏱️ Took {steps} steps (penalty applied)")

    score = max(0.01, min(0.99, score))
    return {"score": round(score, 4), "max_score": 1.0, "feedback": feedback}


def grade_multi_machine(episode: EpisodeRecord) -> Dict:
    """
    Medium grader: 5 machines with mixed issues.
    
    Scoring: % of machines restored to healthy status.
    """
    feedback = []

    if not episode.final_fleet:
        return {"score": 0.01, "feedback": ["No final fleet state"]}

    total = len(episode.final_fleet)
    healthy = sum(1 for m in episode.final_fleet if m.status == MachineStatus.HEALTHY)

    base_score = healthy / total if total > 0 else 0.0
    feedback.append(f"Fleet health: {healthy}/{total} machines healthy")

    # Check for wrong kills (killing non-anomaly processes)
    wrong_kills = 0
    for action in episode.actions:
        if action.command == Command.KILL_PID:
            # Find the machine in initial fleet
            init_machine = next((m for m in episode.initial_fleet if m.id == action.machine_id), None)
            if init_machine and action.target:
                target_pid = int(action.target)
                proc = next((p for p in init_machine.processes if p.pid == target_pid), None)
                if proc and not proc.is_anomaly:
                    wrong_kills += 1

    if wrong_kills > 0:
        penalty = 0.1 * wrong_kills
        base_score = max(0.01, base_score - penalty)
        feedback.append(f"⚠️ {wrong_kills} non-anomaly process(es) killed (penalty: -{penalty:.2f})")

    # Efficiency bonus
    if episode.total_steps <= 10 and base_score >= 0.8:
        base_score = min(0.99, base_score + 0.05)
        feedback.append("🏆 Efficient resolution")

    base_score = max(0.01, min(0.99, base_score))
    return {"score": round(base_score, 4), "max_score": 1.0, "feedback": feedback}


def grade_cascade_failure(episode: EpisodeRecord) -> Dict:
    """
    Hard grader: 20 machines with dependency chains.
    
    Scoring:
    - Base: % of machines restored to healthy
    - Bonus: correct resolution order (fix dependencies first)
    - Penalty: wrong kills, killing healthy processes
    """
    feedback = []

    if not episode.final_fleet:
        return {"score": 0.01, "feedback": ["No final fleet state"]}

    total = len(episode.final_fleet)
    healthy = sum(1 for m in episode.final_fleet if m.status == MachineStatus.HEALTHY)

    # Healing score = machines_healed / total_machines * 0.5
    healing_score = (healthy / total * 0.5) if total > 0 else 0.0
    feedback.append(f"Fleet health: {healthy}/{total} machines healthy (+{healing_score:.3f})")

    # Check resolution order — reward fixing lower tiers first
    tier_order = {"db": 0, "cache": 1, "app": 2, "edge": 3, "mon": 4}
    action_tiers = []
    for action in episode.actions:
        if action.command in (Command.KILL_PID, Command.RESTART_SERVICE, Command.REBOOT):
            prefix = action.machine_id.split("-")[0]
            tier = tier_order.get(prefix, 5)
            action_tiers.append(tier)

    # Calculate order ratio
    order_bonus = 0.0
    if len(action_tiers) >= 2:
        ordered_pairs = sum(1 for i in range(len(action_tiers) - 1) if action_tiers[i] <= action_tiers[i + 1])
        order_ratio = ordered_pairs / (len(action_tiers) - 1)
        order_bonus = order_ratio * 0.3
        feedback.append(f"Resolution order: {order_ratio:.0%} correct (+{order_bonus:.3f})")
    elif len(action_tiers) == 1:
        order_bonus = 0.3
        feedback.append(f"Resolution order: 100% correct (+{order_bonus:.3f})")

    # Speed bonus: max 0.2
    steps = episode.total_steps
    if steps <= 15:
        speed_bonus = 0.2
    elif steps <= 30:
        speed_bonus = 0.2 * (30 - steps) / 15.0
    else:
        speed_bonus = 0.0
    feedback.append(f"Speed bonus: {steps} steps (+{speed_bonus:.3f})")

    # Wrong kill penalty
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

    final_score = healing_score + order_bonus + speed_bonus - wrong_kill_penalty
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
