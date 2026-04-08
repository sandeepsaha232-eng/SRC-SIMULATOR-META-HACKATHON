---
title: SRC Simulator Hackathon
emoji: 🚀
colorFrom: purple
colorTo: blue
sdk: docker
app_file: app.py
pinned: false
---
<p align="center">
  <img src="https://raw.githubusercontent.com/sandeepsaha232-eng/SRC-SIMULATOR-META-HACKATHON/main/assets/hero.png" alt="SRE Fleet Gym Cyberpunk Dashboard" width="100%">
</p>

# 🚀 SRE Fleet Gym: The Autonomous Incident Response Training Ground

**SRE Fleet Gym** is a high-fidelity "flight simulator" for AI agents, designed to train autonomous SREs (System Reliability Engineers) in diagnosing and remediating complex infrastructure failures. Built on the OpenEnv framework, it provides a rigorous, penalized sandbox where agents navigate real-world outage scenarios—from zombie processes to catastrophic cache stampedes—in a battle against downtime.

## 🌟 Live Interactive Demo
[https://sandeep8327-src-simulator-hackathon.hf.space](https://sandeep8327-src-simulator-hackathon.hf.space)

*(The live dashboard is strictly read-only to prevent state mutation during agent evaluation. It will automatically poll and display the live state of the fleet once the agent initiates the /reset sequence.)*

---

## 🛠️ The Tech Stack
*   **Engine:** FastAPI, Python 3.10+ (OpenEnv Compliant)
*   **Simulation:** Pydantic-enforced state machines & deterministic anomaly generation
*   **Frontend:** React, TailwindCSS, Framer Motion (Cyberpunk Glassmorphism UI)
*   **Intelligence:** Groq LLM API (Llama 3 / Mixtral integration)
*   **Infrastructure:** Docker, Hugging Face Spaces

---

## 🏗️ How It Works (Architecture)

```mermaid
graph TD
    A[SRE Agent / LLM] <-->|Polls /state & POSTs /step Actions| B[FastAPI Engine]
    B -->|Generates Anomalies & State| C[Simulated Fleet Array]
    C -->|Telemetry & Syslogs| B
    B <-->|Real-time Data Polling| D[React Glassmorphism Dashboard]
    
    style A fill:#8b5cf6,stroke:#c4b5fd,stroke-width:2px,color:#fff
    style B fill:#10b981,stroke:#34d399,stroke-width:2px,color:#fff
    style C fill:#3b82f6,stroke:#93c5fd,stroke-width:2px,color:#fff
    style D fill:#f43f5e,stroke:#fda4af,stroke-width:2px,color:#fff
```

* **Simulated Server**: Dynamic Pydantic-enforced node states (CPU, Memory, Syslogs)
* **FastAPI**: The core OpenEnv Gym simulator logic and API router
* **LLM**: The autonomous SRE Copilot reading structured & unstructured data
* **React Dashboard**: The beautiful mission control UI visualizing it all

---

## ⚡ The "Why": Real-World Utility
SRE downtime costs massive enterprises millions of dollars per minute. Restoring complex dependencies (database layer -> cache layer -> app layer) during a live outage traditionally requires a "war room" of stressed, sleep-deprived engineers deciphering cryptic unstructured logs. 

**SRE Fleet Gym** changes the paradigm: it provides a rigorous, highly-penalized sandbox that trains autonomous coding agents to diagnose critical bottlenecks and execute surgical remediations in *milliseconds*.

---

## 📋 The 3 Tasks & The Difficulty Curve
Our environment dynamically spawns fleets with Pydantic-enforced typing. Agents must survive three distinct difficulty tiers:

1. **`single_machine` (Easy):** 1 machine suffering from a run-away `zombie` process. Tests basic agent observation targeting & `kill_pid` effectiveness.
2. **`multi_machine` (Medium):** 5 machines experiencing randomized CPU spikes and memory exhaustion. Tests the agent's ability to efficiently prioritize triage across a noisy fleet.
3. **`cascade_failure` (Hard):** 20 machines with deep dependency chains. The agent must diagnose root-cause via a dependency map.
    * **🔥 The "Cache Stampede" Trap:** If an agent blindly restarts a broken Cache layer before manually restoring the underlying Database layer, it triggers a catastrophic Cache Stampede. The database CPU hits 100%, and the agent is struck with a massive scalar penalty. This tests context-awareness and sequencing!

---

## 👁️ Action & Observation Space
Our environment challenges agents beyond structured integers. It requires reading *both* structured telemetry and unstructured string outputs.

### Observation Space
* **Fleet Telemetry**: Live CPU, memory percent, and disk pressure.
* **`syslog_tail`**: Unstructured console logs dynamically generated per machine (e.g., `kernel: CPU temp warning...` or `kernel: VFS: No space left on device`). The LLM must parse these strings to detect spoofed resource anomalies!
* **Dependency Map**: Graph of machine routing dependencies.

### Action Space
* `kill_pid`: Surgically remove an offending process integer.
* `restart_service`: Power cycle an anomaly target string.
* `reboot`: The nuclear option. Cures the machine but incurs severe downtime penalties.
* `noop`: Wait out the storm.

---

## ⚖️ Reward Shaping: The "SLO Burn Rate"
We didn't just use a basic `0/1` reward. Instead, SRE Fleet Gym utilizes a continuous **SLO Burn Rate** modifier inside the simulator step-logic:
* The base reward tracks the overall healthy-to-broken ratio minus a step-penalty to encourage speed.
* **Tier Weighting**: Broken databases (`db-*`) incur a `-0.10` penalty per step, while edge nodes (`edge-*`) only cost `-0.01`. To score well, the agent learns the concept of the Service Level Objective: triage the most financially critical infrastructure first.

---

## ⚡ Quick Start: Run It Locally

### 1. Using Docker (Recommended)
The fastest way to get the full environment (including the dashboard) running exactly as it does on Hugging Face:
```bash
docker build -t sre-fleet-gym .
docker run -p 7860:7860 sre-fleet-gym
```
Access the dashboard at: `http://localhost:7860`

### 2. Manual Installation
If you prefer running without Docker:
```bash
# Install dependencies
pip install -r requirements.txt

# Start the simulator & dashboard
uvicorn app:app --port 7860 --reload

# In a new terminal, run the agent (Heuristic/Deterministic)
python inference.py

# OR: Run with LLM Intelligence (Requires API Key)
export HF_TOKEN="your_key"
export API_BASE_URL="https://api.groq.com/openai/v1"
export MODEL_NAME="llama3-70b-8192"
python inference.py
```
