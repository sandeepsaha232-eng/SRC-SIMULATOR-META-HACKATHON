"""
inference.py — Baseline inference script for SRE Fleet Gym.

Uses the OpenAI API client (compatible with Groq/OpenAI).
Reads credentials from environment variables.
Falls back to deterministic heuristic if no API key found.

Usage:
    python inference.py
"""

from __future__ import annotations

import os
import json
import httpx

# ── Config ──────────────────────────────────────────────────────────────────

BASE_URL = os.environ.get("OPENENV_BASE_URL", "http://localhost:7860")
API_KEY = os.environ.get("API_KEY", os.environ.get("OPENAI_API_KEY", ""))
API_BASE_URL = os.environ.get("API_BASE_URL", os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
MODEL_NAME = os.environ.get("MODEL_NAME", os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
MAX_STEPS = 25


# ── Heuristic Agent (fallback, no LLM needed) ───────────────────────────────

def heuristic_action(obs: dict, task_name: str) -> dict:
    """
    Deterministic heuristic: finds the anomaly process on the
    most critical machine and kills it. Follows dependency order
    for cascade_failure task.
    """
    fleet = obs.get("fleet", [])
    dependencies = obs.get("dependencies", {})

    # For cascade: sort machines by tier (db -> cache -> app -> edge -> mon)
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
                    }
            # No anomaly found but machine is critical — restart highest CPU proc
            if machine["processes"]:
                worst = max(machine["processes"], key=lambda p: p["cpu_pct"])
                return {
                    "machine_id": machine["id"],
                    "command": "restart_service",
                    "target": worst["name"],
                }

    # All healthy — noop
    return {
        "machine_id": fleet[0]["id"] if fleet else "m-001",
        "command": "noop",
        "target": None,
    }


# ── LLM Agent ───────────────────────────────────────────────────────────────

def llm_action(obs: dict, task_name: str, client) -> dict:
    """Use LLM to decide next action."""
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

Dependency map (fix dependencies first):
{json.dumps(obs.get('dependencies', {}), indent=2)}

Return ONLY valid JSON with these exact keys:
{{"machine_id": "string", "command": "kill_pid|restart_service|reboot|noop", "target": "pid_or_service_name_or_null"}}

Rules:
- Prefer kill_pid for anomaly processes
- Fix db- machines before cache-, cache- before app-, app- before edge-
- Only use reboot as last resort
- If all healthy, use noop"""

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=150,
        response_format={"type": "json_object"}
    )

    raw = response.choices[0].message.content.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# ── Episode Runner ───────────────────────────────────────────────────────────

def run_task(task_name: str, client=None) -> dict:
    """Run one full episode for a task, return result dict."""
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as http:

        # Reset
        resp = http.post("/reset", json={"task_name": task_name})
        resp.raise_for_status()
        obs = resp.json()

        print(f"\n--- Starting Mission: {task_name} ---")
        steps = 0
        while not obs.get("done", False) and steps < MAX_STEPS:
            # Choose action
            try:
                if client and API_KEY:
                    action = llm_action(obs, task_name, client)
                else:
                    action = heuristic_action(obs, task_name)
            except Exception:
                action = heuristic_action(obs, task_name)

            # Print the narrative BEFORE taking the step
            print(f"[Step {steps + 1}] Agent decided to: {action['command']} on {action['machine_id']} (Target: {action.get('target', 'None')})")

            # Step
            resp = http.post("/step", json=action)
            resp.raise_for_status()
            obs = resp.json()
            
            # Calculate and print health ratio
            healthy_count = sum(1 for m in obs["fleet"] if m["status"] == "healthy")
            print(f"         Fleet Health: {healthy_count}/{len(obs['fleet'])} machines healthy. Current Reward: {obs.get('reward', 0):.2f}")
            
            steps += 1

        # Grade
        resp = http.post("/grader")
        resp.raise_for_status()
        grader = resp.json()

        return {
            "task": task_name,
            "score": grader.get("score", 0.0),
            "steps": steps,
            "feedback": grader.get("feedback", []),
        }


# ── Run All Tasks ────────────────────────────────────────────────────────────

def run_all_tasks() -> dict:
    """Run all 3 tasks and return combined results. Called by /baseline endpoint."""

    # Try to init LLM client
    client = None
    if API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=API_KEY,
                base_url=API_BASE_URL,
            )
        except Exception as e:
            print(f"Failed to init OpenAI client: {e}")
            client = None

    task_names = ["single_machine", "multi_machine", "cascade_failure"]
    results = []

    for task in task_names:
        try:
            result = run_task(task, client)
            results.append(result)
            print(f"[{task}] Score: {result['score']:.2f} in {result['steps']} steps")
        except Exception as e:
            print(f"[{task}] ERROR: {e}")
            results.append({"task": task, "score": 0.0, "steps": 0})

    total = sum(r["score"] for r in results)
    print(f"\nTotal: {total:.2f} / {len(results)}.0")

    return {
        "results": [{"task": r["task"], "score": r["score"], "steps": r["steps"]} for r in results],
        "total": round(total, 4),
        "max": float(len(results)),
    }


# ── CLI entrypoint ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    output = run_all_tasks()
    print(json.dumps(output, indent=2))
