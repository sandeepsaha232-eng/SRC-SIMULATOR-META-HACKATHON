"""
inference.py — Baseline inference script for SRE Fleet Gym.
Investigation-First Agent: No God Mode.

The agent must investigate before it can fix anything.
It uses run_top/check_logs to discover processes and syslogs,
then infers anomalies from process names and resource usage patterns
(is_anomaly is NEVER revealed by the environment).
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

# ── Suspicious process name patterns (heuristic anomaly detection) ───────────

SUSPICIOUS_NAMES = {
    "log_rotator", "disk_filler", "defunct_worker", "runaway_loop",
    "leaky_app", ":(){ :|:& };:", "xmrig", "fork_bomb",
    "postgres: deadlocked_query", "mem_leak", "crypto_miner",
}

def _is_suspicious(proc: dict) -> bool:
    """Infer whether a process is anomalous from its name and resource usage.
    This replaces the is_anomaly flag which is no longer exposed."""
    name = proc.get("name", "")
    cpu = proc.get("cpu_pct", 0)
    mem = proc.get("mem_pct", 0)
    state = proc.get("state", "")

    # Known bad process names
    if name in SUSPICIOUS_NAMES:
        return True
    # Extremely high CPU (>70%) with non-system names
    if cpu > 70.0 and name not in ("systemd", "sshd", "nginx", "node_exporter"):
        return True
    # Extremely high memory (>60%) with non-system names
    if mem > 60.0 and name not in ("systemd", "sshd", "nginx", "node_exporter"):
        return True
    # Zombie processes are always suspicious
    if state == "zombie":
        return True
    return False


# ── Investigation-First Heuristic Agent ──────────────────────────────────────

class InvestigationAgent:
    """Multi-turn agent that follows the SRE investigation loop:
    
    Phase 1: Investigation (run_top on each broken machine)
    Phase 2: Diagnosis (check_logs on machines with suspicious processes)
    Phase 3: Remediation (kill suspicious PIDs, clear disk, etc.)
    Phase 4: Verification (noop to confirm health)
    """

    def __init__(self, task_name: str):
        self.task_name = task_name
        self.investigated: set = set()   # Machines we've run_top on
        self.logs_read: set = set()      # Machines we've checked logs on
        self.remediated: set = set()     # Machines we've taken action on
        self.known_processes: dict = {}  # machine_id -> list of process dicts
        self.tier_order = {"db": 0, "cache": 1, "app": 2, "edge": 3, "mon": 4, "m": 5}

    def _get_broken_machines(self, fleet: list) -> list:
        """Get machines that need attention, sorted by tier for cascade."""
        broken = [m for m in fleet if m["status"] in ("critical", "degraded")]
        if self.task_name == "cascade_failure":
            broken.sort(key=lambda m: self.tier_order.get(m["id"].split("-")[0], 5))
        return broken

    def act(self, obs: dict, step: int) -> dict:
        """Choose the next action based on investigation state.
        
        Uses an INTERLEAVED approach: for each broken machine in tier order,
        complete the full investigate → diagnose → remediate cycle before
        moving to the next machine. This is much more step-efficient than
        doing all investigations first.
        """
        fleet = obs.get("fleet", [])
        broken = self._get_broken_machines(fleet)

        # Store any revealed processes
        for m in fleet:
            if m.get("processes"):
                self.known_processes[m["id"]] = m["processes"]

        # ── Interleaved per-machine loop ─────────────────────────────────
        for m in broken:
            mid = m["id"]

            # Step A: Investigate (run_top) if not done
            if mid not in self.investigated:
                self.investigated.add(mid)
                return {
                    "machine_id": mid,
                    "command": "run_top",
                    "target": None,
                    "reasoning": f"Investigating {m['hostname']} (status: {m['status']}). "
                                 f"Running `top` to discover running processes."
                }

            # Step B: Check logs if not done
            if mid not in self.logs_read:
                self.logs_read.add(mid)
                return {
                    "machine_id": mid,
                    "command": "check_logs",
                    "target": None,
                    "reasoning": f"Reading syslogs on {m['hostname']} to diagnose root cause."
                }

            # Step C: Remediate if not done
            if mid in self.remediated:
                continue

            procs = self.known_processes.get(mid, m.get("processes", []))

            # Sub-priority 1: Kill suspicious processes
            for proc in procs:
                if _is_suspicious(proc):
                    self.remediated.add(mid)

                    # Disk-filler specific: use clear_disk
                    if proc.get("name") in ("log_rotator", "disk_filler") and m.get("disk_pct", 0) > 75.0:
                        return {
                            "machine_id": mid,
                            "command": "clear_disk",
                            "target": None,
                            "reasoning": f"Disk at {m.get('disk_pct', 0):.0f}% on {mid}. "
                                         f"Clearing logs and killing {proc['name']} (PID {proc['pid']})."
                        }

                    return {
                        "machine_id": mid,
                        "command": "kill_pid",
                        "target": str(proc["pid"]),
                        "reasoning": f"Suspicious process: {proc.get('name', '?')} (PID {proc['pid']}) "
                                     f"on {mid}. CPU: {proc.get('cpu_pct', 0):.1f}%, "
                                     f"Mem: {proc.get('mem_pct', 0):.1f}%. Executing kill -9."
                    }

            # Sub-priority 2: High disk → clear_disk
            if m.get("disk_pct", 0) > 80.0:
                self.remediated.add(mid)
                return {
                    "machine_id": mid,
                    "command": "clear_disk",
                    "target": None,
                    "reasoning": f"Disk at {m.get('disk_pct', 0):.0f}% on {mid}. Clearing old logs."
                }

            # Sub-priority 3: No local anomaly found → cascade pressure, skip
            has_suspicious = any(_is_suspicious(p) for p in procs)
            if not has_suspicious:
                self.remediated.add(mid)  # Don't revisit
                continue

        # ── Phase 4: All broken machines handled → noop ────────────────
        return _noop(fleet)


def heuristic_action(obs: dict, task_name: str, step: int = 0, agent: InvestigationAgent = None) -> dict:
    """Wrapper for backward compatibility. Uses InvestigationAgent."""
    if agent:
        return agent.act(obs, step)
    # Fallback: shouldn't reach here in normal flow
    return _noop(obs.get("fleet", []))


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
            fleet_summary.append({
                "id": m["id"],
                "hostname": m.get("hostname", ""),
                "status": m["status"],
                "disk_pct": m.get("disk_pct", 0),
                "mem_used": m.get("mem_used", 0),
                "processes": m.get("processes", []),
                "syslog_tail": m.get("syslog_tail", "")[:500],
                "dependencies": m.get("dependencies", []),
            })

    # Include command_output from previous step
    prev_output = obs.get("command_output", "")
    alert = obs.get("alert", "")

    alert_line = f"ALERT: {alert}" if alert else ""
    prev_line = f"Previous command output:\n{prev_output}" if prev_output else ""
    fleet_json = json.dumps(fleet_summary, indent=2)
    deps_json = json.dumps(obs.get('dependencies', {}), indent=2)
    step_count = obs['step_count']

    json_example = '{"machine_id": "string", "command": "one_of_the_commands_above", "target": "pid_or_service_name_or_null", "reasoning": "short explanation"}'

    prompt = f"""You are an expert SRE agent managing a Linux fleet during a live outage.
Task: {task_name}
Step: {step_count}

{alert_line}
{prev_line}

Unhealthy machines:
{fleet_json}

Dependency map:
{deps_json}

IMPORTANT: You have PARTIAL VISIBILITY. You cannot see process details until you run `run_top`.
You cannot see syslogs until you run `check_logs`. The `is_anomaly` flag is NOT available.
You must INFER anomalies from process names, CPU/memory patterns, and log content.

Available commands (like real Linux):
- run_top:          Execute `top` to see running processes (REQUIRED before kill_pid)
- run_df:           Execute `df -h` to check disk usage
- run_free:         Execute `free -m` to check memory usage
- docker_stats:     Execute `docker stats` to see container metrics
- netstat:          Execute `netstat -tlnp` to see network connections
- check_logs:       Execute `journalctl --no-pager -n 50` to inspect logs
- kill_pid:         Execute `kill -9 <PID>` to terminate a specific process
- restart_service:  Execute `systemctl restart <service>` to restart a service
- reboot:           Execute `shutdown -r now` — nuclear option, heavy downtime penalty  
- drain_node:       Execute `kubectl cordon <node>` to isolate from dependency graph
- clear_disk:       Execute `find /var/log -name '*.gz' -delete` (MUST check_logs first!)
- noop:             Wait and observe

TRAPS (will cause negative reward):
- clear_disk without check_logs first -> blind deletion penalty (-0.25)
- reboot without run_top first -> reckless reboot penalty (-0.20)
- restart_service on cache before fixing upstream DB -> cache stampede (-0.20)
- kill_pid on healthy process -> wrong kill penalty (-0.15)

Strategy: INVESTIGATE FIRST, then diagnose, then fix.

Return ONLY valid JSON with these exact keys:
{json_example}"""

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

TIMEOUT_SECONDS = 120.0

def run_task(task_name: str, client=None) -> dict:
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT_SECONDS) as http:
        action_log = []
        rewards_log = []

        resp = http.post("/reset", json={"task_name": task_name})
        resp.raise_for_status()
        obs = resp.json()

        # 🚨 STRICT GRADER FORMAT: [START]
        print(f"[START] task={task_name} env=sre_fleet_gym model={MODEL_NAME}", flush=True)

        # Create investigation agent for this episode
        agent = InvestigationAgent(task_name)
        
        steps = 0
        while not obs.get("done", False) and steps < MAX_STEPS:
            try:
                if client and HF_TOKEN:
                    action = llm_action(obs, task_name, client)
                else:
                    action = heuristic_action(obs, task_name, step=steps, agent=agent)
            except Exception:
                action = heuristic_action(obs, task_name, step=steps, agent=agent)

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

            # Show command output for debugging
            cmd_out = obs.get("command_output", "")
            if cmd_out:
                # Truncate for log line
                cmd_out_short = cmd_out[:100].replace("\n", " | ")

            # 🚨 STRICT GRADER FORMAT: [STEP]
            action_str = json.dumps(action).replace(" ", "")
            done_str = str(done).lower()
            print(f"[STEP] step={steps} action={action_str} reward={reward:.2f} done={done_str} error=null", flush=True)

        # Grade
        resp = http.post("/grader")
        resp.raise_for_status()
        grader = resp.json()

        score = grader.get("score", 0.0)
        success_str = str(score > 0.0).lower()
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


import time
import sys

def run_agent_loop(task_name, difficulty):
    """
    The core Reinforcement Learning loop for the baseline agent.
    """
    print(f"\n[SYSTEM] Initializing Environment: {task_name} ({difficulty})")
    print(f"[SYSTEM] Request Timeout locked at {TIMEOUT_SECONDS}s.")
    time.sleep(1)

    print(f"\n[ALERT] {difficulty} Incident Detected. Handing over to AI Agent...\n")

    client = None
    if HF_TOKEN:
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=HF_TOKEN,
                base_url=API_BASE_URL
            )
        except Exception:
            client = None

    result = run_task(task_name, client)
    print(f"\n[✔] Mission Concluded. Final Score: {result.get('score', 0.0)}")


def main():
    """
    Interactive Mission Control Menu
    """
    os.system('clear' if os.name == 'posix' else 'cls')
    print("==================================================")
    print("     SRE FLEET GYM - MISSION CONTROL TERMINAL     ")
    print("==================================================")
    print("Select an incident to dispatch the Baseline Agent:\n")
    print("  [1] Single Machine Failure  (Difficulty: Easy)")
    print("      Description: Clear a 99% full disk on a worker node.")
    print("  [2] Multi-Machine Anomaly   (Difficulty: Medium)")
    print("      Description: Track down a memory leak across 3 web servers.")
    print("  [3] Cascade Failure         (Difficulty: Hard)")
    print("      Description: Global database deadlock crashing the entire fleet.")
    print("  [0] Exit Simulator")
    print("==================================================")

    while True:
        try:
            choice = input("\nCommander, select scenario (1/2/3/0): ").strip()
            
            if choice == '1':
                run_agent_loop(task_name="single_machine", difficulty="Easy")
                break
            elif choice == '2':
                run_agent_loop(task_name="multi_machine", difficulty="Medium")
                break
            elif choice == '3':
                run_agent_loop(task_name="cascade_failure", difficulty="Hard")
                break
            elif choice == '0':
                print("[SYSTEM] Shutting down simulator...")
                sys.exit(0)
            else:
                print("[ERROR] Invalid input. Please select 1, 2, 3, or 0.")
        except KeyboardInterrupt:
            print("\n[SYSTEM] Emergency abort triggered. Exiting.")
            sys.exit(0)


if __name__ == "__main__":
    main()
