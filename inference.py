"""
inference.py — Baseline inference script for SRE Fleet Gym.
Grader-Compliant Version with milestone-aware heuristic.
"""

from __future__ import annotations

import os
import json
import httpx

# ── Config ──────────────────────────────────────────────────────────────────

BASE_URL = os.environ.get("OPENENV_BASE_URL", "http://localhost:7860")
# 🚨 Strictly using the exact variable names requested by the grader specs
API_BASE_URL = os.environ.get("API_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-4o-mini")
HF_TOKEN = os.environ.get("HF_TOKEN")
MAX_STEPS = 25


# ── Heuristic Agent (fallback, no LLM needed) ───────────────────────────────

def heuristic_action(obs: dict, task_name: str, step: int = 0) -> dict:
    """
    Smart heuristic agent that uses realistic SRE commands.
    
    Strategy:
    - Task 1 (single_machine): CHECK_LOGS → CLEAR_DISK or KILL_PID the log_rotator
    - Task 2 (multi_machine): Iterate machines, KILL_PID each anomaly
    - Task 3 (cascade_failure): Fix in tier order (db → cache → app → edge)
    """
    fleet = obs.get("fleet", [])
    dependencies = obs.get("dependencies", {})
    tier_order = {"db": 0, "cache": 1, "app": 2, "edge": 3, "mon": 4, "m": 5}

    # ── Task 1: Disk-full scenario ───────────────────────────────────
    if task_name == "single_machine":
        machine = fleet[0] if fleet else None
        if not machine:
            return _noop(fleet)

        # Step 0: Check logs first (triggers log_read milestone)
        if step == 0:
            return {
                "machine_id": machine["id"],
                "command": "check_logs",
                "target": None,
                "reasoning": "Inspecting system logs to diagnose the disk-full alert on prod-web-01."
            }

        # Step 1: Check if disk is full → clear_disk or kill the filler
        if machine.get("disk_pct", 0) > 75.0:
            # Look for the disk-filling process first
            for proc in machine.get("processes", []):
                if proc.get("is_anomaly") and proc.get("name") in ("log_rotator", "disk_filler"):
                    return {
                        "machine_id": machine["id"],
                        "command": "kill_pid",
                        "target": str(proc["pid"]),
                        "reasoning": f"Identified disk-filling process {proc['name']} (PID {proc['pid']}). "
                                     f"Killing to stop /var/log growth. Disk at {machine.get('disk_pct', 0):.0f}%."
                    }
            # Fallback: use clear_disk command
            return {
                "machine_id": machine["id"],
                "command": "clear_disk",
                "target": None,
                "reasoning": f"Disk at {machine.get('disk_pct', 0):.0f}%. Running: find /var/log -name '*.gz' -delete && truncate -s 0 /var/log/syslog"
            }

        # Otherwise kill any remaining anomaly
        for proc in machine.get("processes", []):
            if proc.get("is_anomaly"):
                return {
                    "machine_id": machine["id"],
                    "command": "kill_pid",
                    "target": str(proc["pid"]),
                    "reasoning": f"Killing anomalous process PID {proc['pid']} ({proc.get('name', 'unknown')})."
                }

        return _noop(fleet)

    # ── Task 3: Cascade failure — fix in tier order ──────────────────
    if task_name == "cascade_failure":
        fleet = sorted(
            fleet,
            key=lambda m: tier_order.get(m["id"].split("-")[0], 5)
        )

    # ── General strategy for multi_machine and cascade_failure ───────
    for machine in fleet:
        if machine["status"] in ("critical", "degraded"):
            # Priority 1: Kill anomaly-flagged processes (these are the root causes)
            for proc in machine["processes"]:
                if proc.get("is_anomaly"):
                    # Handle disk-filler specifically
                    if proc.get("name") in ("log_rotator", "disk_filler") and machine.get("disk_pct", 0) > 75.0:
                        return {
                            "machine_id": machine["id"],
                            "command": "clear_disk",
                            "target": None,
                            "reasoning": f"Disk at {machine.get('disk_pct', 0):.0f}% on {machine['id']}. "
                                         f"Clearing logs and killing {proc['name']} (PID {proc['pid']})."
                        }
                    return {
                        "machine_id": machine["id"],
                        "command": "kill_pid",
                        "target": str(proc["pid"]),
                        "reasoning": f"Anomaly detected: {proc.get('name', 'unknown')} (PID {proc['pid']}) on {machine['id']}. "
                                     f"CPU: {proc.get('cpu_pct', 0):.1f}%, Mem: {proc.get('mem_pct', 0):.1f}%. Executing kill -9 {proc['pid']}."
                    }

            # Priority 2: Kill zombie/defunct processes (always bad)
            for proc in machine["processes"]:
                if proc.get("state") in ["zombie", "defunct"]:
                    return {
                        "machine_id": machine["id"],
                        "command": "kill_pid",
                        "target": str(proc["pid"]),
                        "reasoning": f"Zombie/defunct process state detected on PID {proc['pid']} ({proc.get('name', 'unknown')}) — {machine['id']}."
                    }
            
            # Priority 3: Clear disk if critically full
            if machine.get("disk_pct", 0) > 80.0:
                return {
                    "machine_id": machine["id"],
                    "command": "clear_disk",
                    "target": None,
                    "reasoning": f"Disk at {machine.get('disk_pct', 0):.0f}% on {machine['id']}. Clearing old logs."
                }

            # Priority 4: If machine is critical but has no anomalies, it's cascade pressure.
            # DON'T kill healthy processes — the CPU will drop once upstream deps are fixed.
            # Just skip this machine (noop is implicit by continuing the loop).
            has_any_anomaly = any(p.get("is_anomaly") for p in machine["processes"])
            if not has_any_anomaly:
                # This machine is suffering from cascade pressure, not a local fault.
                # It will self-heal once we fix the upstream dependency.
                continue

    return _noop(fleet)


def _noop(fleet: list) -> dict:
    """Return a noop action."""
    return {
        "machine_id": fleet[0]["id"] if fleet else "m-001",
        "command": "noop",
        "target": None,
        "reasoning": "Fleet status healthy. Monitoring for anomalies."
    }


# ── LLM Agent ───────────────────────────────────────────────────────────────

def llm_action(obs: dict, task_name: str, client) -> dict:
    fleet_summary = []
    for m in obs["fleet"]:
        if m["status"] != "healthy":
            anomalies = [p for p in m["processes"] if p.get("is_anomaly")]
            fleet_summary.append({
                "id": m["id"],
                "hostname": m.get("hostname", ""),
                "status": m["status"],
                "disk_pct": m.get("disk_pct", 0),
                "mem_used": m.get("mem_used", 0),
                "syslog_tail": m.get("syslog_tail", "")[:500],  # Truncate long syslogs
                "anomaly_processes": anomalies,
                "dependencies": m.get("dependencies", []),
            })

    prompt = f"""You are an expert SRE agent managing a Linux fleet during a live outage.
Task: {task_name}
Step: {obs['step_count']}

Unhealthy machines:
{json.dumps(fleet_summary, indent=2)}

Dependency map:
{json.dumps(obs.get('dependencies', {}), indent=2)}

Available commands (like real Linux):
- kill_pid: Execute `kill -9 <PID>` to terminate a specific process
- restart_service: Execute `systemctl restart <service>` to restart a service
- reboot: Execute `shutdown -r now` — nuclear option, heavy downtime penalty  
- check_logs: Execute `journalctl -u <service> --no-pager -n 50` to inspect logs
- drain_node: Execute `kubectl cordon <node>` to isolate from dependency graph
- clear_disk: Execute `find /var/log -name '*.gz' -delete && truncate -s 0 /var/log/syslog`
- noop: Wait and observe

Strategy tips:
- Fix root causes first (databases before caches before apps)
- Use check_logs to gather more intel before acting
- Kill specific PIDs instead of rebooting (reboot = penalty)
- drain_node prevents cascade propagation from a broken machine

Return ONLY valid JSON with these exact keys:
{{"machine_id": "string", "command": "kill_pid|restart_service|reboot|noop|check_logs|drain_node|clear_disk", "target": "pid_or_service_name_or_null", "reasoning": "short explanation"}}"""

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=200,
        response_format={"type": "json_object"}
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# ── Episode Runner ───────────────────────────────────────────────────────────

def run_task(task_name: str, client=None) -> dict:
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as http:
        action_log = []
        rewards_log = [] # Added to track rewards for the [END] log

        resp = http.post("/reset", json={"task_name": task_name})
        resp.raise_for_status()
        obs = resp.json()

        # 🚨 STRICT GRADER FORMAT: [START]
        print(f"[START] task={task_name} env=sre_fleet_gym model={MODEL_NAME}", flush=True)
        
        steps = 0
        while not obs.get("done", False) and steps < MAX_STEPS:
            try:
                if client and HF_TOKEN:
                    action = llm_action(obs, task_name, client)
                else:
                    action = heuristic_action(obs, task_name, step=steps)
            except Exception:
                action = heuristic_action(obs, task_name, step=steps)

            # Dashboard logging
            target_str = action.get('target', 'None')
            command_str = action['command']
            reasoning = action.get("reasoning", "Targeted anomalous process.")
            action_log.append({
                "machine": action["machine_id"],
                "command": f"{command_str} on {target_str}",
                "reasoning": reasoning
            })

            # Execute Step
            resp = http.post("/step", json=action)
            resp.raise_for_status()
            obs = resp.json()
            steps += 1
            
            reward = obs.get("reward", 0.0)
            done = obs.get("done", False)
            rewards_log.append(reward)

            # 🚨 STRICT GRADER FORMAT: [STEP]
            # Compress action dict to a string without spaces to avoid regex issues
            action_str = json.dumps(action).replace(" ", "")
            done_str = str(done).lower()
            print(f"[STEP] step={steps} action={action_str} reward={reward:.2f} done={done_str} error=null", flush=True)

        # Grade
        resp = http.post("/grader")
        resp.raise_for_status()
        grader = resp.json()

        score = grader.get("score", 0.0)
        success_str = str(score > 0.0).lower() # Assuming any positive score is partial success
        rewards_str = ",".join(f"{r:.2f}" for r in rewards_log) if rewards_log else "0.00"
        
        # 🚨 STRICT GRADER FORMAT: [END]
        print(f"[END] success={success_str} steps={steps} score={score:.3f} rewards={rewards_str}", flush=True)

        return {
            "task": task_name,
            "score": score,
            "steps": steps,
            "feedback": grader.get("feedback", []),
            "history": action_log
        }


# ── Run All Tasks ────────────────────────────────────────────────────────────

def run_all_tasks() -> dict:
    client = None
    if HF_TOKEN:
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=HF_TOKEN,
                base_url=API_BASE_URL
            )
        except Exception as e:
            client = None

    task_names = ["single_machine", "multi_machine", "cascade_failure"]
    results = []

    for task in task_names:
        try:
            result = run_task(task, client)
            results.append(result)
        except Exception as e:
            results.append({"task": task, "score": 0.0, "steps": 0})

    total = sum(r["score"] for r in results)

    return {
        "results": [{"task": r["task"], "score": r["score"], "steps": r["steps"], "history": r.get("history", [])} for r in results],
        "total": round(total, 4),
        "max": float(len(results)),
    }


if __name__ == "__main__":
    output = run_all_tasks()
    # The final JSON block is kept for your dashboard to read
    print(json.dumps(output, indent=2))
