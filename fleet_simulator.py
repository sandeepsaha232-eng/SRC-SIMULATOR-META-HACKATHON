"""
fleet_simulator.py — SRE Fleet Gym Simulator

Pure-Python simulation of a fleet of machines with injected failures.
No real hosts — every machine is a Pydantic object.
"""

from __future__ import annotations

import random
import uuid
from copy import deepcopy
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


# ── Enums ───────────────────────────────────────────────────────────────────

class ProcessState(str, Enum):
    RUNNING = "running"
    ZOMBIE = "zombie"
    DEAD = "dead"


class MachineStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"


class Command(str, Enum):
    KILL_PID = "kill_pid"
    RESTART_SERVICE = "restart_service"
    REBOOT = "reboot"
    NOOP = "noop"


# ── Pydantic Models ────────────────────────────────────────────────────────

class Process(BaseModel):
    pid: int = Field(..., description="Process identifier")
    name: str = Field(..., description="Process name")
    cpu_pct: float = Field(0.0, ge=0.0, description="CPU usage %")
    mem_pct: float = Field(0.0, ge=0.0, description="Memory usage %")
    state: ProcessState = Field(ProcessState.RUNNING, description="Process state")
    is_anomaly: bool = Field(False, description="Whether this process is injected fault")


class Machine(BaseModel):
    id: str = Field(..., description="Unique machine identifier")
    hostname: str = Field(..., description="Machine hostname")
    processes: List[Process] = Field(default_factory=list)
    cpu_total: float = Field(100.0, description="Total CPU capacity %")
    mem_total: float = Field(100.0, description="Total memory capacity %")
    mem_used: float = Field(0.0, description="Memory used %")
    disk_pct: float = Field(0.0, description="Disk usage %")
    syslog_tail: str = Field("systemd: Clean. Service running normally.", description="Last line of system log")
    status: MachineStatus = Field(MachineStatus.HEALTHY)
    dependencies: List[str] = Field(default_factory=list, description="IDs of machines this depends on")


class Action(BaseModel):
    machine_id: str = Field(..., description="Target machine ID")
    command: Command = Field(..., description="Command to execute")
    target: Optional[str] = Field(None, description="Target PID or service name")


class Observation(BaseModel):
    fleet: List[Machine] = Field(..., description="Current fleet state")
    dependencies: Dict[str, List[str]] = Field(default_factory=dict, description="Map of machine dependencies")
    step_count: int = Field(0, description="Steps taken in this episode")
    done: bool = Field(False, description="Whether episode is complete")
    reward: float = Field(0.0, description="Cumulative reward")
    info: Dict[str, Any] = Field(default_factory=dict, description="Extra info")


class TaskInfo(BaseModel):
    name: str
    description: str
    difficulty: str
    num_machines: int
    action_schema: Dict[str, Any]


class EpisodeRecord(BaseModel):
    """Full record of a completed episode for grading."""
    task_name: str
    initial_fleet: List[Machine]
    actions: List[Action] = Field(default_factory=list)
    observations: List[Observation] = Field(default_factory=list)
    final_fleet: List[Machine] = Field(default_factory=list)
    total_steps: int = 0
    total_reward: float = 0.0


# ── Fleet Simulator ────────────────────────────────────────────────────────

TASK_DEFINITIONS: Dict[str, TaskInfo] = {
    "single_machine": TaskInfo(
        name="single_machine",
        description="1 machine, 1 zombie process consuming excessive CPU. Kill the right PID.",
        difficulty="easy",
        num_machines=1,
        action_schema={
            "machine_id": "string",
            "command": "kill_pid | restart_service | reboot | noop",
            "target": "string (PID or service name)",
        },
    ),
    "multi_machine": TaskInfo(
        name="multi_machine",
        description="5 machines with mixed CPU spikes and memory pressure. Restore fleet health.",
        difficulty="medium",
        num_machines=5,
        action_schema={
            "machine_id": "string",
            "command": "kill_pid | restart_service | reboot | noop",
            "target": "string (PID or service name)",
        },
    ),
    "cascade_failure": TaskInfo(
        name="cascade_failure",
        description="20 machines with cascading failures and dependency chains. Partial credit for resolution order.",
        difficulty="hard",
        num_machines=20,
        action_schema={
            "machine_id": "string",
            "command": "kill_pid | restart_service | reboot | noop",
            "target": "string (PID or service name)",
        },
    ),
}

# Common healthy baseline processes
_BASELINE_PROCS = [
    ("systemd", 1.0, 2.0),
    ("sshd", 0.5, 1.0),
    ("nginx", 3.0, 4.0),
    ("node_exporter", 1.5, 1.5),
]

# Anomaly templates per fault type
_FAULT_TEMPLATES = {
    "zombie": lambda pid: Process(pid=pid, name="defunct_worker", cpu_pct=85.0, mem_pct=5.0, state=ProcessState.ZOMBIE, is_anomaly=True),
    "cpu_hog": lambda pid: Process(pid=pid, name="runaway_loop", cpu_pct=92.0, mem_pct=8.0, state=ProcessState.RUNNING, is_anomaly=True),
    "mem_leak": lambda pid: Process(pid=pid, name="leaky_app", cpu_pct=15.0, mem_pct=78.0, state=ProcessState.RUNNING, is_anomaly=True),
    "disk_filler": lambda pid: Process(pid=pid, name="log_rotator", cpu_pct=25.0, mem_pct=12.0, state=ProcessState.RUNNING, is_anomaly=True),
    "fork_bomb": lambda pid: Process(pid=pid, name=":(){ :|:& };:", cpu_pct=95.0, mem_pct=45.0, state=ProcessState.RUNNING, is_anomaly=True),
    "crypto_miner": lambda pid: Process(pid=pid, name="xmrig", cpu_pct=98.0, mem_pct=15.0, state=ProcessState.RUNNING, is_anomaly=True),
}


def _make_baseline_machine(machine_id: str, hostname: str, deps: List[str] | None = None) -> Machine:
    """Create a healthy machine with standard baseline processes."""
    procs = []
    for i, (name, cpu, mem) in enumerate(_BASELINE_PROCS):
        procs.append(Process(
            pid=100 + i,
            name=name,
            cpu_pct=cpu + random.uniform(-0.5, 0.5),
            mem_pct=mem + random.uniform(-0.3, 0.3),
            state=ProcessState.RUNNING,
        ))
    mem_used = sum(p.mem_pct for p in procs)
    return Machine(
        id=machine_id,
        hostname=hostname,
        processes=procs,
        mem_used=mem_used,
        disk_pct=random.uniform(20.0, 45.0),
        status=MachineStatus.HEALTHY,
        dependencies=deps or [],
    )


class FleetSimulator:
    """Simulates a fleet of machines with injected failures."""

    def __init__(self):
        self._fleet: List[Machine] = []
        self._task_name: str = ""
        self._step_count: int = 0
        self._done: bool = False
        self._episode: EpisodeRecord | None = None
        self._max_steps: int = 30
        self._trap_penalties: float = 0.0

    # ── Helpers ─────────────────────────────────────────────────────────

    def _generate_syslog(self, processes: List[Process]) -> str:
        for p in processes:
            if p.is_anomaly:
                if p.name == "xmrig" or p.name == "crypto-miner":
                    return f"kernel: CPU temp warning. process {p.pid} (unrecognized) consuming 99% resources."
                elif p.name == "defunct_worker" or p.name == "zombie-proc":
                    return f"systemd: [WARN] Process {p.pid} unresponsive. Status: D (disk sleep)."
                elif p.name == "leaky_app" or p.name == "mem-leak":
                    return f"kernel: Out of memory: Killed process {p.pid}."
                elif p.name == "runaway_loop":
                    return f"systemd: [WARN] runaway_loop has held CPU lock for 30s."
                elif p.name == "log_rotator" or p.name == "disk_filler":
                    return f"kernel: VFS: No space left on device"
                elif p.name == ":(){ :|:& };:" or p.name == "fork_bomb":
                    return f"kernel: cgroup: fork rejected by pids controller"
        return "systemd: Clean. Service running normally."

    # ── Public API ──────────────────────────────────────────────────────

    def reset(self, task_name: str) -> Observation:
        """Spawn a fresh broken fleet for the given task."""
        import random
        # Ensure fully reproducible deterministic baselines for OpenEnv validation
        random.seed(42)
        
        if task_name not in TASK_DEFINITIONS:
            raise ValueError(f"Unknown task: {task_name}. Choose from: {list(TASK_DEFINITIONS.keys())}")

        self._task_name = task_name
        self._step_count = 0
        self._trap_penalties = 0.0
        self._done = False

        if task_name == "single_machine":
            self._fleet = self._spawn_single_machine()
        elif task_name == "multi_machine":
            self._fleet = self._spawn_multi_machine()
        elif task_name == "cascade_failure":
            self._fleet = self._spawn_cascade_failure()

        self._episode = EpisodeRecord(
            task_name=task_name,
            initial_fleet=deepcopy(self._fleet),
        )

        obs = self._make_observation()
        self._episode.observations.append(deepcopy(obs))
        return obs

    def step(self, action: Action) -> Observation:
        """Execute an action, tick dynamics, return new observation."""
        if self._done:
            return self._make_observation()

        self._step_count += 1

        # Record action
        if self._episode:
            self._episode.actions.append(deepcopy(action))

        # Execute the action
        self._execute_action(action)

        # Tick dynamics (drift, cascades, etc.)
        self._tick_dynamics()

        # Check completion
        self._check_done()

        obs = self._make_observation()

        if self._episode:
            self._episode.observations.append(deepcopy(obs))
            self._episode.total_steps = self._step_count
            self._episode.total_reward = obs.reward
            self._episode.final_fleet = deepcopy(self._fleet)

        return obs

    def get_state(self) -> Dict[str, Any]:
        """Return current fleet state snapshot."""
        return {
            "task_name": self._task_name,
            "step_count": self._step_count,
            "done": self._done,
            "fleet": [m.model_dump() for m in self._fleet],
            "dependencies": {m.id: m.dependencies for m in self._fleet if m.dependencies},
        }

    def get_episode(self) -> EpisodeRecord | None:
        """Return the episode record for grading."""
        return self._episode

    # ── Fleet Spawners ──────────────────────────────────────────────────

    def _spawn_single_machine(self) -> List[Machine]:
        """Easy: 1 machine, 1 zombie process."""
        m = _make_baseline_machine("m-001", "prod-web-01")
        zombie = _FAULT_TEMPLATES["zombie"](999)
        m.processes.append(zombie)
        m.mem_used = sum(p.mem_pct for p in m.processes)
        m.status = MachineStatus.CRITICAL
        return [m]

    def _spawn_multi_machine(self) -> List[Machine]:
        """Medium: 5 machines with mixed CPU + memory issues."""
        fleet = []
        faults = ["cpu_hog", "mem_leak", "zombie", "crypto_miner", "disk_filler"]
        for i in range(5):
            m = _make_baseline_machine(f"m-{i+1:03d}", f"prod-svc-{i+1:02d}")
            anomaly_pid = 900 + i * 10
            fault = _FAULT_TEMPLATES[faults[i]](anomaly_pid)
            m.processes.append(fault)
            m.mem_used = sum(p.mem_pct for p in m.processes)
            m.disk_pct = min(95.0, m.disk_pct + (40.0 if faults[i] == "disk_filler" else 0.0))
            m.status = MachineStatus.CRITICAL
            fleet.append(m)
        return fleet

    def _spawn_cascade_failure(self) -> List[Machine]:
        """Hard: 20 machines with dependency chains and cascading failures."""
        fleet = []

        # Tier 1: Database layer (3 machines) — no deps
        for i in range(3):
            mid = f"db-{i+1:02d}"
            m = _make_baseline_machine(mid, f"prod-db-{i+1:02d}")
            # Inject mem_leak on first DB
            if i == 0:
                m.processes.append(_FAULT_TEMPLATES["mem_leak"](950))
                m.status = MachineStatus.CRITICAL
            fleet.append(m)

        # Tier 2: Cache layer (3 machines) — depend on DBs
        db_ids = [f"db-{i+1:02d}" for i in range(3)]
        for i in range(3):
            mid = f"cache-{i+1:02d}"
            m = _make_baseline_machine(mid, f"prod-cache-{i+1:02d}", deps=[db_ids[i]])
            if i == 1:
                m.processes.append(_FAULT_TEMPLATES["cpu_hog"](960))
                m.status = MachineStatus.CRITICAL
            fleet.append(m)

        # Tier 3: App servers (8 machines) — depend on cache
        cache_ids = [f"cache-{i+1:02d}" for i in range(3)]
        fault_indices = random.sample(range(8), 4)  # 4 of 8 are broken
        faults_to_use = ["zombie", "fork_bomb", "crypto_miner", "cpu_hog"]
        for i in range(8):
            mid = f"app-{i+1:02d}"
            dep = cache_ids[i % 3]
            m = _make_baseline_machine(mid, f"prod-app-{i+1:02d}", deps=[dep])
            if i in fault_indices:
                fault_type = faults_to_use[fault_indices.index(i)]
                m.processes.append(_FAULT_TEMPLATES[fault_type](970 + i))
                m.status = MachineStatus.CRITICAL
            fleet.append(m)

        # Tier 4: Edge / LB (4 machines) — depend on app servers
        app_ids = [f"app-{i+1:02d}" for i in range(8)]
        for i in range(4):
            mid = f"edge-{i+1:02d}"
            deps = [app_ids[i * 2], app_ids[i * 2 + 1]]
            m = _make_baseline_machine(mid, f"prod-edge-{i+1:02d}", deps=deps)
            if i == 0:
                m.processes.append(_FAULT_TEMPLATES["disk_filler"](990))
                m.disk_pct = 92.0
                m.status = MachineStatus.DEGRADED
            fleet.append(m)

        # Tier 5: Monitoring (2 machines) — depend on everything
        for i in range(2):
            mid = f"mon-{i+1:02d}"
            all_ids = [m.id for m in fleet]
            m = _make_baseline_machine(mid, f"prod-mon-{i+1:02d}", deps=random.sample(all_ids, min(5, len(all_ids))))
            fleet.append(m)

        # Update mem_used
        for m in fleet:
            m.mem_used = sum(p.mem_pct for p in m.processes)

        return fleet

    # ── Action Execution ────────────────────────────────────────────────

    def _execute_action(self, action: Action) -> None:
        machine = self._find_machine(action.machine_id)
        if machine is None:
            return

        if action.command == Command.KILL_PID:
            try:
                target_pid = int(action.target) if action.target else -1
            except ValueError:
                target_pid = -1  # Protect against LLM hallucinating a string
                
            proc = next((p for p in machine.processes if p.pid == target_pid), None)
            if proc:
                # Explicitly free disk space if the anomaly is a disk filler
                if proc.is_anomaly and proc.name in ("log_rotator", "disk_filler"):
                    machine.disk_pct = max(20.0, machine.disk_pct - 50.0)

                proc.state = ProcessState.DEAD
                machine.processes = [p for p in machine.processes if p.state != ProcessState.DEAD]
                machine.mem_used = sum(p.mem_pct for p in machine.processes)

        elif action.command == Command.RESTART_SERVICE:
            if "cache" in machine.id:
                # The Cache Stampede Trap
                for dep_id in machine.dependencies:
                    db_machine = self._find_machine(dep_id)
                    if db_machine and db_machine.status != MachineStatus.HEALTHY:
                        # Trap is sprung
                        for p in db_machine.processes:
                            p.cpu_pct = 100.0
                        db_machine.status = MachineStatus.CRITICAL
                        self._trap_penalties += 0.2
                        print(f"TRAP TRIGGERED: Cache stampede on {db_machine.id}!")
                        
            svc_name = action.target or ""
            for p in machine.processes:
                if p.name == svc_name:
                    p.cpu_pct = max(1.0, p.cpu_pct * 0.1)
                    p.mem_pct = max(1.0, p.mem_pct * 0.2)
                    p.state = ProcessState.RUNNING

        elif action.command == Command.REBOOT:
            # Reboot kills all anomalies but also restarts healthy services (downtime penalty)
            machine.processes = [p for p in machine.processes if not p.is_anomaly]
            for p in machine.processes:
                p.cpu_pct = max(0.5, p.cpu_pct * 0.3)
                p.mem_pct = max(0.5, p.mem_pct * 0.3)
            machine.mem_used = sum(p.mem_pct for p in machine.processes)

        elif action.command == Command.NOOP:
            pass

    # ── Dynamics ────────────────────────────────────────────────────────

    def _tick_dynamics(self) -> None:
        """Tick simulation dynamics: CPU noise, memory drift, cascade propagation."""
        for m in self._fleet:
            for p in m.processes:
                if p.state == ProcessState.RUNNING:
                    # Small random drift
                    p.cpu_pct = max(0.1, min(100.0, p.cpu_pct + random.uniform(-1.5, 1.5)))
                    p.mem_pct = max(0.1, min(100.0, p.mem_pct + random.uniform(-0.5, 0.5)))
                    # Anomalies trend upward
                    if p.is_anomaly:
                        p.cpu_pct = min(100.0, p.cpu_pct + random.uniform(0.5, 3.0))
                        p.mem_pct = min(100.0, p.mem_pct + random.uniform(0.2, 1.5))

            m.mem_used = sum(p.mem_pct for p in m.processes)

            # Cascade: if a dependency is CRITICAL, degrade this machine
            if self._task_name == "cascade_failure":
                for dep_id in m.dependencies:
                    dep = self._find_machine(dep_id)
                    if dep and dep.status == MachineStatus.CRITICAL:
                        # Cascade: inject CPU pressure
                        for p in m.processes:
                            if not p.is_anomaly:
                                p.cpu_pct = min(100.0, p.cpu_pct + random.uniform(2.0, 8.0))

            # Recompute status
            total_cpu = sum(p.cpu_pct for p in m.processes)
            has_anomaly = any(p.is_anomaly for p in m.processes)

            if has_anomaly or total_cpu > 90.0 or m.mem_used > 85.0 or m.disk_pct > 90.0:
                m.status = MachineStatus.CRITICAL
            elif total_cpu > 60.0 or m.mem_used > 60.0 or m.disk_pct > 75.0:
                m.status = MachineStatus.DEGRADED
            else:
                m.status = MachineStatus.HEALTHY
                
            m.syslog_tail = self._generate_syslog(m.processes) 

    # ── Reward + Done ───────────────────────────────────────────────────

    def _check_done(self) -> None:
        if self._step_count >= self._max_steps:
            self._done = True

        # Done if all machines are healthy
        if all(m.status == MachineStatus.HEALTHY for m in self._fleet):
            self._done = True

    def _calc_reward(self) -> float:
        """Calculate reward based on fleet health."""
        total = len(self._fleet)
        if total == 0:
            return 0.0

        healthy = sum(1 for m in self._fleet if m.status == MachineStatus.HEALTHY)
        base_reward = healthy / total

        # Step penalty: small cost per step to encourage efficiency
        step_penalty = 0.01 * self._step_count
        
        # SLO Burn Rate Penalty: heavy penalty if critical infrastructure stays broken
        slo_penalty = 0.0
        tier_weights = {"db": 0.10, "cache": 0.05, "app": 0.02, "edge": 0.01, "m": 0.01}
        for m in self._fleet:
            if m.status != MachineStatus.HEALTHY:
                tier = m.id.split("-")[0]
                slo_penalty -= tier_weights.get(tier, 0.01)

        reward = max(0.0, base_reward - step_penalty + slo_penalty - self._trap_penalties)

        return round(reward, 4)

    # ── Observation Builder ─────────────────────────────────────────────

    def _make_observation(self) -> Observation:
        reward = self._calc_reward()
        info: Dict[str, Any] = {}

        if self._done:
            healthy = sum(1 for m in self._fleet if m.status == MachineStatus.HEALTHY)
            info["healthy_count"] = healthy
            info["total_count"] = len(self._fleet)
            info["success"] = all(m.status == MachineStatus.HEALTHY for m in self._fleet)

        deps = {m.id: m.dependencies for m in self._fleet if m.dependencies}

        return Observation(
            fleet=deepcopy(self._fleet),
            dependencies=deps,
            step_count=self._step_count,
            done=self._done,
            reward=reward,
            info=info,
        )

    # ── Helpers ─────────────────────────────────────────────────────────

    def _find_machine(self, machine_id: str) -> Machine | None:
        return next((m for m in self._fleet if m.id == machine_id), None)
