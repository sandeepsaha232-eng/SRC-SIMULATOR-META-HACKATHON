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
    CHECK_LOGS = "check_logs"
    DRAIN_NODE = "drain_node"
    CLEAR_DISK = "clear_disk"


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
    syslog_tail: str = Field("systemd: Clean. Service running normally.", description="System log block")
    status: MachineStatus = Field(MachineStatus.HEALTHY)
    dependencies: List[str] = Field(default_factory=list, description="IDs of machines this depends on")
    drained: bool = Field(False, description="Whether this node has been cordoned/drained")


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
    milestones: Dict[str, Dict[str, bool]] = Field(default_factory=dict)


# ── Fleet Simulator ────────────────────────────────────────────────────────

TASK_DEFINITIONS: Dict[str, TaskInfo] = {
    "single_machine": TaskInfo(
        name="single_machine",
        description="Clear a full disk. A log_rotator process is filling /var/log on a single machine. "
                    "Identify the offending PID from the syslogs, kill it or clear the disk.",
        difficulty="easy",
        num_machines=1,
        action_schema={
            "machine_id": "string",
            "command": "kill_pid | restart_service | reboot | noop | check_logs | drain_node | clear_disk",
            "target": "string (PID or service name)",
        },
    ),
    "multi_machine": TaskInfo(
        name="multi_machine",
        description="Memory leak in a service fleet. 5 machines with mixed CPU spikes, zombie processes, "
                    "and memory exhaustion. Triage and restore fleet health.",
        difficulty="medium",
        num_machines=5,
        action_schema={
            "machine_id": "string",
            "command": "kill_pid | restart_service | reboot | noop | check_logs | drain_node | clear_disk",
            "target": "string (PID or service name)",
        },
    ),
    "cascade_failure": TaskInfo(
        name="cascade_failure",
        description="Cascading database deadlock. 20 machines across 5 tiers. A deadlocked query on db-01 "
                    "is causing cache timeouts, app server 502s, and edge alerts. Trace the root cause "
                    "through hundreds of log lines and fix in dependency order.",
        difficulty="hard",
        num_machines=20,
        action_schema={
            "machine_id": "string",
            "command": "kill_pid | restart_service | reboot | noop | check_logs | drain_node | clear_disk",
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
    "deadlocked_query": lambda pid: Process(pid=pid, name="postgres: deadlocked_query", cpu_pct=5.0, mem_pct=82.0, state=ProcessState.RUNNING, is_anomaly=True),
}

# ── Realistic syslog noise lines ─────────────────────────────────────────

_SYSLOG_NOISE = [
    "{ts} {host} CRON[{rnd}]: (root) CMD (/usr/sbin/logrotate /etc/logrotate.conf)",
    "{ts} {host} sshd[{rnd}]: Accepted publickey for deploy from 10.0.1.{ip} port 22 ssh2",
    "{ts} {host} systemd[1]: Started Session {rnd} of user deploy.",
    "{ts} {host} kernel: [{epoch}] audit: type=1400 audit(1712808847.{rnd}:42): apparmor=\"STATUS\"",
    "{ts} {host} node_exporter[{rnd}]: msg=\"Scrape complete\" duration_seconds=0.003",
    "{ts} {host} systemd[1]: Reloading NTP client/server.",
    "{ts} {host} kernel: [{epoch}] TCP: request_sock_TCP: Possible SYN flooding on port 443.",
]

_SYSLOG_ANOMALY_TEMPLATES = {
    "disk_filler": [
        "{ts} {host} kernel: [{epoch}] EXT4-fs warning (device sda1): ext4_dx_add_entry:2074: Directory (ino 131073) index full, reach max htree level :2",
        "{ts} {host} kernel: [{epoch}] VFS: file-max limit {rnd} reached — cannot allocate new fd",
        "{ts} {host} systemd[1]: logrotate.service: Failed with result 'exit-code'. /var/log 97% full.",
        "{ts} {host} kernel: [{epoch}] EXT4-fs error (device sda1): ext4_find_entry:1455: inode #131073: comm log_rotator: No space left on device",
        "{ts} {host} kernel: [{epoch}] ALERT: disk usage on /var/log has exceeded 95%. PID {pid} (log_rotator) is the primary writer.",
    ],
    "zombie": [
        "{ts} {host} kernel: [{epoch}] INFO: task defunct_worker:{pid} blocked for more than 120 seconds.",
        "{ts} {host} kernel: [{epoch}]       Not tainted 5.15.0-86-generic #96-Ubuntu",
        "{ts} {host} systemd[1]: [WARN] defunct_worker.service: Process {pid} unresponsive (state: D disk-sleep)",
        "{ts} {host} kernel: [{epoch}] defunct_worker  D    0  {pid}      1 0x00000000",
    ],
    "cpu_hog": [
        "{ts} {host} kernel: [{epoch}] watchdog: BUG: soft lockup - CPU#{rnd} stuck for 22s! [runaway_loop:{pid}]",
        "{ts} {host} systemd[1]: [WARN] runaway_loop has held CPU lock for 30s — consider kill -9 {pid}",
        "{ts} {host} kernel: [{epoch}] perf: interrupt took too long ({rnd} > {rnd2} ns), lowering kernel.perf_event_max_sample_rate",
    ],
    "mem_leak": [
        "{ts} {host} kernel: [{epoch}] leaky_app invoked oom-killer: gfp_mask=0x100cca(GFP_HIGHUSER_MOVABLE), order=0",
        "{ts} {host} kernel: [{epoch}] Out of memory: Killed process {pid} (leaky_app) total-vm:8234512kB, anon-rss:6412840kB",
        "{ts} {host} kernel: [{epoch}] Memory cgroup out of memory: Killed process {pid}",
        "{ts} {host} systemd[1]: leaky_app.service: Main process exited, code=killed, status=9/KILL",
    ],
    "fork_bomb": [
        "{ts} {host} kernel: [{epoch}] cgroup: fork rejected by pids controller in /system.slice/fork_bomb.service",
        "{ts} {host} kernel: [{epoch}] CRIT: process table full — {rnd} active tasks (max 32768)",
        "{ts} {host} systemd[1]: [EMERG] Process {pid} spawning children uncontrollably — fork bomb detected",
    ],
    "crypto_miner": [
        "{ts} {host} kernel: [{epoch}] CPU temp warning: package temp {rnd}°C above threshold, cpu clock throttled",
        "{ts} {host} kernel: [{epoch}] perf: process {pid} (xmrig) consuming 98% CPU across all cores",
        "{ts} {host} auditd[{rnd}]: ALERT: Unrecognized binary 'xmrig' launched from /tmp/.cache/ (UID 1000)",
        "{ts} {host} systemd[1]: [WARN] Suspicious outbound connection from PID {pid} to stratum+tcp://pool.minexmr.com:4444",
    ],
    "deadlocked_query": [
        "{ts} {host} postgresql[{pid}]: LOG:  process {pid} still waiting for ShareLock on transaction 847291 after 30000.123 ms",
        "{ts} {host} postgresql[{pid}]: DETAIL:  Process holding the lock: {rnd}. Wait queue: {pid}.",
        "{ts} {host} postgresql[{pid}]: LOG:  deadlock detected — Process {pid} waits for ShareLock on transaction 847291; blocked by process {rnd}",
        "{ts} {host} postgresql[{pid}]: HINT:  See server log for query details.",
        "{ts} {host} kernel: [{epoch}] postgres: connection pool exhausted (max_connections=200, active=200, waiting=847)",
        "{ts} {host} systemd[1]: postgresql.service: Watchdog timeout — connection backlog critical",
    ],
}

# Cascade-specific downstream log templates
_CASCADE_DOWNSTREAM_LOGS = {
    "cache": [
        "{ts} {host} redis[{rnd}]: WARN: Connection to upstream db-01:5432 timed out after 30s",
        "{ts} {host} redis[{rnd}]: ERR: GET key 'session:847291' failed — upstream database unreachable",
        "{ts} {host} systemd[1]: redis.service: Cache miss rate exceeded 95% — falling through to origin",
    ],
    "app": [
        "{ts} {host} nginx[{rnd}]: upstream timed out (110: Connection timed out) while connecting to cache-{idx}:6379",
        "{ts} {host} gunicorn[{rnd}]: [ERROR] Worker timeout — request to /api/v1/users took 45s (max 30s)",
        "{ts} {host} systemd[1]: gunicorn.service: 502 Bad Gateway responses: {rnd}/min (threshold: 10/min)",
    ],
    "edge": [
        "{ts} {host} haproxy[{rnd}]: WARNING: backend app-servers has no available members!",
        "{ts} {host} haproxy[{rnd}]: Server app-{idx}/prod-app-{idx} is DOWN. {rnd} active connections, 0 requeued.",
        "{ts} {host} systemd[1]: haproxy.service: Health check failures on {rnd}/{rnd2} backend servers",
    ],
}


def _fmt_syslog(template: str, hostname: str, pid: int = 0) -> str:
    """Format a syslog template with realistic values."""
    import random as _r
    return template.format(
        ts=f"Apr 11 03:{_r.randint(10,59)}:{_r.randint(10,59)}",
        host=hostname,
        pid=pid,
        rnd=_r.randint(1000, 9999),
        rnd2=_r.randint(100, 999),
        ip=_r.randint(2, 254),
        epoch=f"{_r.randint(40000,49999)}.{_r.randint(100,999)}",
        idx=f"{_r.randint(1,8):02d}",
    )


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
        self._destructive_penalties: float = 0.0
        # Milestone tracker: machine_id -> {milestone_name: bool}
        self._milestones: Dict[str, Dict[str, bool]] = {}

    # ── Helpers ─────────────────────────────────────────────────────────

    def _generate_syslog(self, machine: Machine) -> str:
        """Generate multi-line, realistic Linux syslog output for a machine."""
        lines: List[str] = []
        hostname = machine.hostname

        # Always start with 1-2 noise lines for realism
        noise_count = random.randint(1, 3)
        for _ in range(noise_count):
            tmpl = random.choice(_SYSLOG_NOISE)
            lines.append(_fmt_syslog(tmpl, hostname))

        # Add anomaly-specific log lines
        has_anomaly = False
        for p in machine.processes:
            if p.is_anomaly:
                has_anomaly = True
                # Determine fault type from process name
                fault_type = self._classify_fault(p)
                if fault_type in _SYSLOG_ANOMALY_TEMPLATES:
                    templates = _SYSLOG_ANOMALY_TEMPLATES[fault_type]
                    # Include 2-4 anomaly log lines for richness
                    sample_count = min(len(templates), random.randint(2, 4))
                    for tmpl in random.sample(templates, sample_count):
                        lines.append(_fmt_syslog(tmpl, hostname, p.pid))

        # For cascade_failure task, add downstream impact logs
        if self._task_name == "cascade_failure" and not has_anomaly:
            tier = machine.id.split("-")[0]
            if tier in _CASCADE_DOWNSTREAM_LOGS and machine.status != MachineStatus.HEALTHY:
                templates = _CASCADE_DOWNSTREAM_LOGS[tier]
                for tmpl in templates[:2]:
                    lines.append(_fmt_syslog(tmpl, hostname))

        if not has_anomaly and machine.status == MachineStatus.HEALTHY:
            lines.append(_fmt_syslog("{ts} {host} systemd[1]: All services operational. Load avg: 0.{rnd} 0.{rnd2} 0.{rnd}", hostname))

        # Add one more noise line at end
        lines.append(_fmt_syslog(random.choice(_SYSLOG_NOISE), hostname))

        return "\n".join(lines)

    def _classify_fault(self, proc: Process) -> str:
        """Classify a process into a fault type for syslog template lookup."""
        name = proc.name
        if "log_rotator" in name or "disk_filler" in name:
            return "disk_filler"
        elif "defunct_worker" in name or proc.state == ProcessState.ZOMBIE:
            return "zombie"
        elif "runaway_loop" in name:
            return "cpu_hog"
        elif "leaky_app" in name or "mem_leak" in name:
            return "mem_leak"
        elif "fork_bomb" in name or ":()" in name:
            return "fork_bomb"
        elif "xmrig" in name or "crypto" in name:
            return "crypto_miner"
        elif "deadlocked_query" in name or "deadlock" in name:
            return "deadlocked_query"
        return "cpu_hog"  # Fallback

    def _init_milestones(self) -> None:
        """Initialize milestone tracking for all machines with anomalies."""
        self._milestones = {}
        for m in self._fleet:
            has_anomaly = any(p.is_anomaly for p in m.processes)
            if has_anomaly or m.status != MachineStatus.HEALTHY:
                self._milestones[m.id] = {
                    "log_read": False,       # Agent inspected logs on this machine
                    "pid_identified": False,  # Agent targeted the correct anomaly PID
                    "node_isolated": False,   # Agent drained/isolated this node (cascade)
                    "service_restored": False, # Machine reached HEALTHY status
                }

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
        self._destructive_penalties = 0.0
        self._done = False

        if task_name == "single_machine":
            self._fleet = self._spawn_single_machine()
        elif task_name == "multi_machine":
            self._fleet = self._spawn_multi_machine()
        elif task_name == "cascade_failure":
            self._fleet = self._spawn_cascade_failure()

        # Initialize milestones for all broken machines
        self._init_milestones()

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

        # Track milestone: log_read — agent targeted this machine
        if action.machine_id in self._milestones:
            self._milestones[action.machine_id]["log_read"] = True

        # Execute the action
        self._execute_action(action)

        # Tick dynamics (drift, cascades, etc.)
        self._tick_dynamics()

        # Update service_restored milestones
        for m in self._fleet:
            if m.id in self._milestones and m.status == MachineStatus.HEALTHY:
                self._milestones[m.id]["service_restored"] = True

        # Check completion
        self._check_done()

        obs = self._make_observation()

        if self._episode:
            self._episode.observations.append(deepcopy(obs))
            self._episode.total_steps = self._step_count
            self._episode.total_reward = obs.reward
            self._episode.final_fleet = deepcopy(self._fleet)
            self._episode.milestones = deepcopy(self._milestones)

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
        """Easy: 1 machine, disk full from log_rotator process."""
        m = _make_baseline_machine("m-001", "prod-web-01")
        filler = _FAULT_TEMPLATES["disk_filler"](999)
        m.processes.append(filler)
        m.mem_used = sum(p.mem_pct for p in m.processes)
        m.disk_pct = 95.0  # Disk is critically full
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
            m.disk_pct = min(95.0, m.disk_pct + (50.0 if faults[i] == "disk_filler" else 0.0))
            m.status = MachineStatus.CRITICAL
            fleet.append(m)
        return fleet

    def _spawn_cascade_failure(self) -> List[Machine]:
        """Hard: 20 machines with dependency chains and cascading DB deadlock."""
        fleet = []

        # Tier 1: Database layer (3 machines) — no deps
        for i in range(3):
            mid = f"db-{i+1:02d}"
            m = _make_baseline_machine(mid, f"prod-db-{i+1:02d}")
            # Root cause: deadlocked query on db-01
            if i == 0:
                m.processes.append(_FAULT_TEMPLATES["deadlocked_query"](950))
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

        # Update mem_used and generate initial syslogs
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
                # Track milestone: pid_identified if killing an anomaly
                if proc.is_anomaly and machine.id in self._milestones:
                    self._milestones[machine.id]["pid_identified"] = True

                # Penalty for killing non-anomaly processes
                if not proc.is_anomaly:
                    self._destructive_penalties += 0.15
                    print(f"PENALTY: Killed healthy process PID {target_pid} on {machine.id}")

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
            # Heavy penalty for brute-force reboot
            self._destructive_penalties += 0.10

        elif action.command == Command.CHECK_LOGS:
            # Diagnostic action: doesn't fix anything, but triggers log_read milestone
            # and enriches the syslog output for next observation
            if machine.id in self._milestones:
                self._milestones[machine.id]["log_read"] = True
            # Generate extra-verbose syslog for this machine on next tick
            machine.syslog_tail = self._generate_syslog(machine)

        elif action.command == Command.DRAIN_NODE:
            # Isolate the machine from the dependency graph
            machine.drained = True
            if machine.id in self._milestones:
                self._milestones[machine.id]["node_isolated"] = True
            # Draining prevents cascade propagation from this node
            print(f"NODE DRAINED: {machine.id} isolated from dependency graph")

        elif action.command == Command.CLEAR_DISK:
            # Clear disk space — specifically handles disk-full scenarios
            if machine.disk_pct > 75.0:
                machine.disk_pct = max(15.0, machine.disk_pct - 60.0)
                # Also kill the disk-filling process if present
                disk_procs = [p for p in machine.processes if p.is_anomaly and p.name in ("log_rotator", "disk_filler")]
                for dp in disk_procs:
                    dp.state = ProcessState.DEAD
                    if machine.id in self._milestones:
                        self._milestones[machine.id]["pid_identified"] = True
                machine.processes = [p for p in machine.processes if p.state != ProcessState.DEAD]
                machine.mem_used = sum(p.mem_pct for p in machine.processes)
            else:
                # Clearing disk on a non-full machine is wasteful but not destructive
                pass

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

            # Cascade: if a dependency is CRITICAL and NOT drained, degrade this machine
            if self._task_name == "cascade_failure":
                for dep_id in m.dependencies:
                    dep = self._find_machine(dep_id)
                    if dep and dep.status == MachineStatus.CRITICAL and not dep.drained:
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
                
            m.syslog_tail = self._generate_syslog(m)

    # ── Reward + Done ───────────────────────────────────────────────────

    def _check_done(self) -> None:
        if self._step_count >= self._max_steps:
            self._done = True

        # Done if all machines are healthy
        if all(m.status == MachineStatus.HEALTHY for m in self._fleet):
            self._done = True

    def _calc_reward(self) -> float:
        """
        Milestone-based reward with partial progress signals.

        Reward = milestone_progress (0.0–0.60) + fleet_health_bonus (0.0–0.25)
                 - step_penalty - trap_penalties - destructive_penalties
        """
        total = len(self._fleet)
        if total == 0:
            return 0.0

        # ── Milestone progress (0.0 – 0.60) ─────────────────────────────
        milestone_score = 0.0
        if self._milestones:
            total_milestones = 0
            achieved_milestones = 0
            for machine_id, ms in self._milestones.items():
                for name, achieved in ms.items():
                    total_milestones += 1
                    if achieved:
                        achieved_milestones += 1
            if total_milestones > 0:
                milestone_score = (achieved_milestones / total_milestones) * 0.60

        # ── Fleet health bonus (0.0 – 0.25) ─────────────────────────────
        healthy = sum(1 for m in self._fleet if m.status == MachineStatus.HEALTHY)
        health_bonus = (healthy / total) * 0.25

        # ── SLO Burn Rate Penalty ────────────────────────────────────────
        slo_penalty = 0.0
        tier_weights = {"db": 0.04, "cache": 0.02, "app": 0.01, "edge": 0.005, "m": 0.01, "mon": 0.005}
        for m in self._fleet:
            if m.status != MachineStatus.HEALTHY:
                tier = m.id.split("-")[0]
                slo_penalty += tier_weights.get(tier, 0.01)

        # ── Step penalty ─────────────────────────────────────────────────
        step_penalty = 0.005 * self._step_count

        # ── Combine ──────────────────────────────────────────────────────
        reward = milestone_score + health_bonus - step_penalty - slo_penalty - self._trap_penalties - self._destructive_penalties

        return round(max(0.0, min(1.0, reward)), 4)

    # ── Observation Builder ─────────────────────────────────────────────

    def _make_observation(self) -> Observation:
        reward = self._calc_reward()
        info: Dict[str, Any] = {
            "milestones": deepcopy(self._milestones),
        }

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
