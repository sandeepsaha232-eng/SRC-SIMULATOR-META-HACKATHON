"""
inference.py — Baseline inference script for SRE Fleet Gym.
Grader-Compliant Version.
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

def heuristic_action(obs: dict, task_name: str) -> dict:
    fleet = obs.get("fleet", [])
    dependencies = obs.get("dependencies", {})
    tier_order = {"db": 0, "cache": 1, "app": 2, "edge": 3, "mon": 4}

    if task_name == "cascade_failure":
        fleet = sorted(
            fleet,
            key=lambda m: tier_order.get(m["id"].split("-")[0], 5)
        )

    for machine in fleet:
        if machine["status"] in ("critical", "degraded"):
            for proc in machine["processes"]:
                if proc.get("is_anomaly"):
                    return {
                        "machine_id": machine["id"],
                        "command": "kill_pid",
                        "target": str(proc["pid"]),
                        "reasoning": f"Anomaly flag detected on process {proc['pid']}."
                    }
                if proc.get("cpu_pct", 0) > 80.0:
                    return {
                        "machine_id": machine["id"],
                        "command": "kill_pid",
                        "target": str(proc["pid"]),
                        "reasoning": f"High CPU usage ({proc['cpu_pct']}%) detected on process {proc['pid']}."
                    }
                if proc.get("state") in ["zombie", "defunct"]:
                    return {
                        "machine_id": machine["id"],
                        "command": "kill_pid",
                        "target": str(proc["pid"]),
                        "reasoning": f"Zombie/defunct process state detected on PID {proc['pid']}."
                    }
            
            if machine["processes"]:
                highest_cpu_proc = max(machine["processes"], key=lambda p: p.get("cpu_pct", 0))
                return {
                    "machine_id": machine["id"],
                    "command": "kill_pid",
                    "target": str(highest_cpu_proc["pid"]),
                    "reasoning": f"Emergency triage: Killing highest CPU process (PID {highest_cpu_proc['pid']}) on critical node."
                }

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
                "status": m["status"],
                "anomaly_processes": anomalies,
                "dependencies": m.get("dependencies", []),
            })

    prompt = f"""You are an SRE agent managing a fleet of machines.
Task: {task_name}
Step: {obs['step_count']}

Unhealthy machines:
{json.dumps(fleet_summary, indent=2)}

Dependency map:
{json.dumps(obs.get('dependencies', {}), indent=2)}

Return ONLY valid JSON with these exact keys:
{{"machine_id": "string", "command": "kill_pid|restart_service|reboot|noop", "target": "pid_or_service_name_or_null", "reasoning": "short explanation of reasoning"}}"""

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=150,
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
                    action = heuristic_action(obs, task_name)
            except Exception:
                action = heuristic_action(obs, task_name)

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
