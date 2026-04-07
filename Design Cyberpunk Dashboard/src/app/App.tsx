import React, { useState, useEffect } from 'react';
import Masonry, { ResponsiveMasonry } from 'react-responsive-masonry';
import { motion } from 'motion/react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';

export type StatusState = 'OK' | 'WARN' | 'FAIL';

export interface Machine {
  id: string;
  state: StatusState;
  host: string;
  mem: string;
  disk: string;
  procs: number;
  deps: string;
  syslog: string;
}

const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.1 } }
};

const itemVariants = {
  hidden: { opacity: 0, y: 20, rotateX: 30, scale: 0.9 },
  visible: { 
    opacity: 1, y: 0, rotateX: 0, scale: 1,
    transition: { type: "spring", stiffness: 100, damping: 15 } 
  }
};

const Header = ({ sysState }: { sysState: any }) => (
  <motion.header 
    initial={{ opacity: 0, y: -20, rotateX: -20 }}
    animate={{ opacity: 1, y: 0, rotateX: 0 }}
    transition={{ duration: 0.8, ease: "easeOut" }}
    className="flex flex-col md:flex-row justify-between items-start md:items-center py-6 mb-8 z-10 relative border-b border-[#008f11]/50 pb-6"
    style={{ perspective: 1000 }}
  >
    <div className="flex flex-col gap-1">
      <h1 className="text-3xl md:text-5xl font-bold text-[#00ff41] drop-shadow-[0_0_10px_#00ff41] tracking-tighter">
        SRE FLEET GYM
      </h1>
      <p className="text-[#008f11] text-xs md:text-sm tracking-widest mt-1">
        // AUTONOMOUS INCIDENT RESPONSE SIMULATOR
      </p>
    </div>
    
    <div className="mt-4 md:mt-0 px-4 py-2 border border-[#00ff41] text-[#00ff41] text-xs md:text-sm tracking-widest bg-[rgba(0,255,65,0.1)] backdrop-blur-md shadow-[0_0_8px_rgba(0,255,65,0.4)] flex items-center gap-3 rounded-sm">
      <div className="w-2 h-2 bg-[#00ff41] animate-pulse shadow-[0_0_5px_#00ff41]" />
      TASK: <span className="text-white">{sysState.task}</span> | STEP: <span className="text-white">{sysState.step}</span> | DONE: <span className="text-white">{sysState.done ? 'true' : 'false'}</span>
    </div>
  </motion.header>
);

const ScoreCard = ({ title, score, isWarning = false }: { title: string; score: string; isWarning?: boolean }) => {
  return (
    <motion.div 
      whileHover={{ scale: 1.02, rotateY: 5, rotateX: -5 }}
      className={`flex-1 backdrop-blur-md p-4 flex flex-col justify-center items-center relative rounded-md transition-all duration-300
        ${isWarning 
          ? 'bg-[rgba(252,232,58,0.05)] border border-[#fce83a]/70 shadow-[0_0_15px_rgba(252,232,58,0.2)]' 
          : 'bg-[#0a0a0c]/60 border border-[#008f11]/50 shadow-[0_4px_10px_rgba(0,0,0,0.5)]'}`}
      style={{ transformStyle: "preserve-3d" }}
    >
      <div className="text-[#008f11] text-[10px] md:text-xs mb-2 text-center w-full transform-gpu translate-z-[20px]">{title}</div>
      <div className={`text-3xl md:text-4xl font-bold transform-gpu translate-z-[30px] ${isWarning ? 'text-[#fce83a] drop-shadow-[0_0_8px_#fce83a]' : 'text-[#00ff41] drop-shadow-[0_0_8px_#00ff41]'}`}>
        {score}
      </div>
    </motion.div>
  );
};

const TelemetryPanel = ({ chartData, sysState }: { chartData: any[], sysState: any }) => {
  return (
    <motion.div 
      variants={containerVariants}
      initial="hidden"
      animate="visible"
      className="flex flex-col lg:flex-row gap-6 mb-10 w-full z-10 relative"
      style={{ perspective: 1200 }}
    >
      <motion.div 
        variants={itemVariants}
        className="flex-grow lg:w-[70%] bg-[#0a0a0c]/60 backdrop-blur-lg border border-[#008f11]/50 p-4 flex flex-col shadow-[0_8px_32px_rgba(0,255,65,0.05)] min-h-[300px] rounded-lg"
      >
        <h2 className="text-[#008f11] mb-4 text-xs md:text-sm tracking-widest font-bold">LIVE TELEMETRY // FLEET HEALTH RATIO</h2>
        <div className="flex-grow w-full h-[250px] relative">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 5, right: 0, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="colorHealth" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00ff41" stopOpacity={0.6}/>
                  <stop offset="95%" stopColor="#00ff41" stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#008f11" opacity={0.3} vertical={false} />
              <XAxis dataKey="time" stroke="#008f11" tick={{ fill: '#008f11', fontSize: 12, fontFamily: '"Space Mono", monospace' }} tickLine={false} axisLine={{ stroke: '#008f11', opacity: 0.5 }} />
              <YAxis stroke="#008f11" domain={[0, 1.2]} tick={{ fill: '#008f11', fontSize: 12, fontFamily: '"Space Mono", monospace' }} tickLine={false} axisLine={{ stroke: '#008f11', opacity: 0.5 }} />
              <Tooltip 
                contentStyle={{ backgroundColor: 'rgba(10,10,12,0.8)', backdropFilter: 'blur(8px)', border: '1px solid #00ff41', color: '#00ff41', fontFamily: '"Space Mono", monospace', borderRadius: '4px', boxShadow: '0 0 15px rgba(0,255,65,0.3)' }}
                itemStyle={{ color: '#00ff41', textTransform: 'uppercase', fontWeight: 'bold' }}
                labelStyle={{ color: '#008f11' }}
              />
              <Area type="monotone" dataKey="health" stroke="#00ff41" strokeWidth={3} fillOpacity={1} fill="url(#colorHealth)" animationDuration={300} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </motion.div>

      <motion.div 
        variants={containerVariants}
        className="w-full lg:w-[30%] flex flex-col gap-4"
      >
        <motion.div variants={itemVariants} className="h-full"><ScoreCard title="SINGLE MACHINE RISK" score={sysState.single} /></motion.div>
        <motion.div variants={itemVariants} className="h-full"><ScoreCard title="MULTI MACHINE RISK" score={sysState.multi} /></motion.div>
        <motion.div variants={itemVariants} className="h-full"><ScoreCard title="CASCADE FAILURE RISK" score={sysState.cascade} isWarning={true} /></motion.div>
      </motion.div>
    </motion.div>
  );
};

const MachineCard = ({ machine }: { machine: Machine }) => {
  let containerClasses = "flex flex-col p-5 mb-4 relative z-10 transition-all duration-300 backdrop-blur-xl rounded-lg ";
  let statusText = '';
  let statusColor = '';
  let idColor = '#00ff41';

  if (machine.state === 'OK') {
    containerClasses += "bg-[rgba(10,10,12,0.65)] border border-[#008f11]/60 shadow-[0_8px_32px_rgba(0,143,17,0.15)] hover:shadow-[0_12px_40px_rgba(0,143,17,0.3)]";
    statusText = '[ OK ]';
    statusColor = 'text-[#00d4aa]';
  } else if (machine.state === 'WARN') {
    containerClasses += "bg-[rgba(252,232,58,0.05)] border border-[#fce83a]/80 shadow-[0_8px_32px_rgba(252,232,58,0.2)] hover:shadow-[0_12px_40px_rgba(252,232,58,0.4)]";
    statusText = '[ WARN ]';
    statusColor = 'text-[#fce83a] drop-shadow-[0_0_8px_#fce83a]';
    idColor = '#fce83a';
  } else if (machine.state === 'FAIL') {
    containerClasses += "bg-[rgba(255,0,60,0.1)] border border-[#ff003c] shadow-[0_8px_32px_rgba(255,0,60,0.4)] hover:shadow-[0_12px_40px_rgba(255,0,60,0.6)] animate-pulse-red";
    statusText = '[ FAIL ]';
    statusColor = 'text-[#ff003c] drop-shadow-[0_0_12px_#ff003c]';
    idColor = '#ff003c';
  }

  // Adding blinking cursor logic to syslog
  return (
    <motion.div 
      variants={itemVariants}
      whileHover={{ 
        scale: 1.03, rotateY: 8, rotateX: -8, z: 50,
        transition: { type: "spring", stiffness: 300, damping: 20 }
      }}
      className={containerClasses}
      style={{ transformStyle: "preserve-3d" }}
    >
      <div className="flex justify-between items-center mb-3 transform-gpu translate-z-[30px]">
        <span className="font-bold text-lg" style={{ color: idColor, textShadow: machine.state === 'FAIL' ? '0 0 12px #ff003c' : '0 0 8px currentColor' }}>
          {machine.id}
        </span>
        <span className={`font-bold ${statusColor} tracking-widest`}>{statusText}</span>
      </div>
      <div className="border-t border-dashed border-[#008f11] opacity-50 mb-4 transform-gpu translate-z-[20px]" />
      
      <div className="grid grid-cols-2 gap-3 text-xs md:text-sm mb-5 transform-gpu translate-z-[20px]">
        <div className="flex flex-col gap-1"><span className="text-[#008f11]/80">HOST</span><span className="text-[#00ff41] font-medium">{machine.host}</span></div>
        <div className="flex flex-col gap-1"><span className="text-[#008f11]/80">MEM_USED</span><span className="text-[#00ff41] font-medium">{machine.mem}</span></div>
        <div className="flex flex-col gap-1"><span className="text-[#008f11]/80">DISK_PCT</span><span className="text-[#00ff41] font-medium">{machine.disk}</span></div>
        <div className="flex flex-col gap-1"><span className="text-[#008f11]/80">PROCS</span><span className="text-[#00ff41] font-medium">{machine.procs}</span></div>
      </div>
      
      <div className="text-xs flex items-center gap-2 transform-gpu translate-z-[20px]">
        <span className="text-[#008f11]/70">DEPS:</span>
        <span className="text-[#008f11] truncate">{machine.deps}</span>
      </div>

      <div className="mt-4 bg-[rgba(0,0,0,0.7)] backdrop-blur-md p-3 text-[10px] md:text-xs text-[#00ff41] border border-[#111111]/80 overflow-hidden whitespace-pre-wrap text-ellipsis font-bold rounded shadow-inner transform-gpu translate-z-[10px]">
        {machine.syslog}<span className="inline-block w-[6px] h-[12px] bg-[#00ff41] ml-1 animate-[blink_1s_step-end_infinite] align-middle" />
      </div>
    </motion.div>
  );
};

const MachineGrid = ({ machines }: { machines: Machine[] }) => {
  return (
    <motion.div 
      variants={containerVariants}
      initial="hidden"
      animate="visible"
      className="w-full relative z-10 mb-20"
      style={{ perspective: 1500 }}
    >
      <ResponsiveMasonry columnsCountBreakPoints={{ 350: 1, 750: 2, 1024: 3, 1440: 4 }}>
        <Masonry gutter="20px">
          {machines.map((machine) => (
            <MachineCard key={machine.id} machine={machine} />
          ))}
        </Masonry>
      </ResponsiveMasonry>
        {machines.length === 0 && (
          <div className="text-center p-20 text-[#008f11] text-xl uppercase tracking-widest border border-dashed border-[#008f11]/50 bg-[#0a0a0c]/30">
              &gt; AWAITING /RESET COMMAND VIA API...
          </div>
        )}
    </motion.div>
  );
};

const MissionControl = () => {
  const [loading, setLoading] = useState(false);

  const triggerReset = async (task: string) => {
    setLoading(true);
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), 10000);

    try {
      const res = await fetch('/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_name: task }),
        signal: controller.signal
      });
      clearTimeout(id);
      if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
      console.log(`Reset triggered for ${task}`);
    } catch (e: any) {
      console.error('Reset failed:', e.message);
      alert(`RESET FAILED: ${e.name === 'AbortError' ? 'Request timed out (10s)' : e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const triggerBaseline = async () => {
    setLoading(true);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 120000); // 🚀 120 seconds (2 minutes)

    try {
      const res = await fetch('/baseline', { 
        method: 'POST',
        signal: controller.signal 
      });
      clearTimeout(timeoutId);
      if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
    } catch (e: any) {
      console.error('Baseline failed (120s):', e.message);
      alert(`BASELINE FAILED: ${e.name === 'AbortError' ? 'Request timed out (120s)' : e.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <motion.div 
      variants={itemVariants}
      initial="hidden"
      animate="visible"
      className="mb-10 p-6 bg-[#0a0a0c]/80 backdrop-blur-xl border border-[#00ff41]/30 rounded-lg shadow-[0_0_20px_rgba(0,255,65,0.1)] relative z-10"
    >
      <div className="flex flex-col md:flex-row justify-between items-center gap-6">
        <div className="flex flex-col gap-1">
          <h2 className="text-[#00ff41] text-lg font-bold tracking-tighter">MISSION CONTROL</h2>
          <p className="text-[#008f11] text-[10px] tracking-widest">// INITIALIZE FLEET SCENARIOS</p>
        </div>
        <div className="flex flex-wrap gap-3 justify-center">
          <button 
            onClick={() => triggerReset('single_machine')}
            disabled={loading}
            className="px-4 py-2 bg-transparent border border-[#00ff41]/50 text-[#00ff41] text-xs hover:bg-[#00ff41]/10 transition-all font-bold tracking-widest disabled:opacity-50"
          >
            [ EASY: SINGLE ]
          </button>
          <button 
            onClick={() => triggerReset('multi_machine')}
            disabled={loading}
            className="px-4 py-2 bg-transparent border border-[#00ff41]/50 text-[#00ff41] text-xs hover:bg-[#00ff41]/10 transition-all font-bold tracking-widest disabled:opacity-50"
          >
            [ MEDIUM: MULTI ]
          </button>
          <button 
            onClick={() => triggerReset('cascade_failure')}
            disabled={loading}
            className="px-4 py-2 bg-transparent border border-[#fce83a]/50 text-[#fce83a] text-xs hover:bg-[#fce83a]/10 transition-all font-bold tracking-widest disabled:opacity-50"
          >
            [ HARD: CASCADE ]
          </button>
          <button 
            onClick={triggerBaseline}
            disabled={loading}
            className="ml-4 px-6 py-2 bg-[#00ff41] text-[#050505] text-xs hover:bg-[#00ff41]/80 transition-all font-bold tracking-widest shadow-[0_0_15px_#00ff41] disabled:opacity-50"
          >
            {loading ? 'EXECUTING...' : 'RUN BASELINE AGENT'}
          </button>
        </div>
      </div>
    </motion.div>
  );
};

export default function App() {
  const [machines, setMachines] = useState<Machine[]>([]);
  const [chartData, setChartData] = useState<any[]>([]);
  const [sysState, setSysState] = useState({ task: 'AWAITING_AGENT', step: 0, done: false, single: '1.00', multi: '1.00', cascade: '0.74' });

  useEffect(() => {
    let tickCount = 0;
    const fetchState = async () => {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 10000);

      try {
        const res = await fetch('/state', { signal: controller.signal });
        clearTimeout(timeoutId);
        
        if (!res.ok) return;
        const data = await res.json();
        
        // Don't clear machines if fleet is empty after a reset was just triggered
        setSysState({ 
          task: data.task_name || 'AWAITING_AGENT', 
          step: data.step_count || 0, 
          done: data.done || false,
          single: '1.00', multi: '1.00', cascade: '0.74'
        });

        if (!data.fleet || data.fleet.length === 0) {
          setMachines([]);
          return;
        }

        const newMachines: Machine[] = data.fleet.map((m: any) => {
          let state: StatusState = 'WARN';
          if (m.status === 'healthy') state = 'OK';
          else if (m.status === 'critical') state = 'FAIL';
          let sysLines = Array.isArray(m.syslog_tail) ? m.syslog_tail.join('\n') : m.syslog_tail;
          return {
            id: m.id, state, host: m.hostname,
            mem: (m.mem_used || 0).toFixed(1) + '%',
            disk: (m.disk_pct || 0).toFixed(1) + '%',
            procs: m.processes ? m.processes.length : 0,
            deps: m.dependencies && m.dependencies.length > 0 ? m.dependencies.join(', ') : 'none',
            syslog: '> root@' + m.hostname + ':~# tail -f /var/log/syslog\n' + sysLines
          };
        });
        setMachines(newMachines);
        
        const hCount = data.fleet.filter((m: any) => m.status === 'healthy').length;
        const ratio = data.fleet.length > 0 ? hCount / data.fleet.length : 0;
        
        setChartData(prev => {
          const lastStep = prev.length > 0 ? prev[prev.length - 1].time : -1;
          if (data.step_count !== lastStep) {
             const next = data.step_count === 0 ? [] : [...prev];
             next.push({ time: data.step_count, health: ratio });
             if (next.length > 40) next.shift();
             return next;
          }
          return prev;
        });
      } catch (e: any) {
        if (e.name === 'AbortError') {
          console.error('State fetch timed out (10s)');
        } else {
          console.error('Telemetry fetch failed:', e);
        }
      }
    };
    fetchState();
    const timer = setInterval(fetchState, 1500);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="min-h-screen bg-[#050505] text-[#00ff41] font-['Space_Mono',monospace] uppercase relative overflow-x-hidden selection:bg-[#00ff41] selection:text-[#050505]">
      {/* Background Grids */}
      <motion.div 
        initial={{ opacity: 0 }} animate={{ opacity: 0.1 }} transition={{ duration: 2 }}
        className="fixed inset-0 pointer-events-none z-0"
        style={{ backgroundImage: `linear-gradient(#008f11 1px, transparent 1px), linear-gradient(90deg, #008f11 1px, transparent 1px)`, backgroundSize: '40px 40px' }}
      />
      <div 
        className="fixed inset-0 pointer-events-none z-[9999] opacity-20 mix-blend-overlay"
        style={{ background: 'linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.25) 50%), linear-gradient(90deg, rgba(255, 0, 0, 0.06), rgba(0, 255, 0, 0.02), rgba(0, 0, 255, 0.06))', backgroundSize: '100% 4px, 6px 100%' }}
      />
      <div className="fixed top-[-20%] left-[-10%] w-[50%] h-[50%] bg-[#00ff41] blur-[150px] opacity-[0.03] rounded-full pointer-events-none" />
      <div className="fixed bottom-[-20%] right-[-10%] w-[50%] h-[50%] bg-[#ff003c] blur-[150px] opacity-[0.03] rounded-full pointer-events-none" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10 py-4">
        <Header sysState={sysState} />
        <MissionControl />
        <TelemetryPanel chartData={chartData} sysState={sysState} />
        <MachineGrid machines={machines} />
      </div>
    </div>
  );
}

