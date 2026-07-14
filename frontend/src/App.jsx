import { useState, useEffect, useCallback } from "react";
import {
  AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";

/* ═══════════════════════════════════════════════════════════════
   BRAND & CONFIG
═══════════════════════════════════════════════════════════════ */
const C = {
  red:      "#E31837",
  redDk:    "#B8001E",
  white:    "#FFFFFF",
  bg:       "#F4F4F4",
  border:   "#E8E8E8",
  text:     "#111111",
  muted:    "#888888",
  running:  "#16A34A",
  stopped:  "#9CA3AF",
  amber:    "#D97706",   // stale tile footer and fleet banner
};
const POLL_MS  = 10_000;
// In dev the Vite proxy forwards /api → http://165.22.247.235:8001 (strips /api prefix).
// In production the built bundle talks directly to the API server.
const API_BASE = "/api";

/* ═══════════════════════════════════════════════════════════════
   MACHINE MASTER DATA
   (matches DB: machine.id, machine_component_instance.id, slave)
═══════════════════════════════════════════════════════════════ */
const MACHINES = [
  { id:3,  name:"Jet 33", cid:4,  model:"Yaskawa F7",     slave:1  },
  { id:4,  name:"Jet 32", cid:5,  model:"INVT CHF100A",   slave:2  },
  { id:5,  name:"Jet 16", cid:6,  model:"Yaskawa A1000",  slave:3  },
  { id:6,  name:"Jet 01", cid:7,  model:"INVT CHF100A",   slave:4  },
  { id:7,  name:"Jet 02", cid:8,  model:"INVT CHF100A",   slave:5  },
  { id:8,  name:"Jet 03", cid:9,  model:"INVT CHF100A",   slave:6  },
  { id:9,  name:"Jet 04", cid:10, model:"INVT CHF100A",   slave:7  },
  { id:10, name:"Jet 20", cid:11, model:"INVT CHF100A",   slave:8  },
  { id:11, name:"Jet 19", cid:12, model:"Yaskawa V1000",  slave:9  },
  { id:12, name:"Jet 21", cid:13, model:"Yaskawa A1000",  slave:10 },
  { id:13, name:"Jet 26", cid:14, model:"INVT CHF100A",   slave:11 },
  { id:14, name:"Jet 27", cid:15, model:"INVT CHF100A",   slave:12 },
  { id:15, name:"Jet 28", cid:16, model:"INVT CHF100A",   slave:13 },
  { id:16, name:"Jet 29", cid:17, model:"INVT CHF100A",   slave:14 },
];

// Keyed by machine.id — used to recover model/slave_id after live API merges.
const MACHINES_MAP = Object.fromEntries(MACHINES.map(m => [m.id, m]));

/* ═══════════════════════════════════════════════════════════════
   UTILITIES
═══════════════════════════════════════════════════════════════ */
const rnd = (lo, hi, d=2) => parseFloat((Math.random()*(hi-lo)+lo).toFixed(d));

// Format a sensor value safely.
// Returns "—" for null/undefined (tag missing from response).
// Returns the formatted number string for 0 and above.
// Zero is a valid reading (idle VFD) — do not conflate with missing.
const fmt = (val, dec = 1) => {
  if (val === null || val === undefined) return "—";
  return dec === 0 ? Math.round(val).toLocaleString() : parseFloat(val).toFixed(dec);
};

const toIST = (utcStr) => {
  if (!utcStr) return "—";
  try {
    const d = new Date(utcStr.endsWith("Z") ? utcStr : utcStr + "Z");
    return d.toLocaleTimeString("en-IN", {
      timeZone:"Asia/Kolkata", hour:"2-digit",
      minute:"2-digit", second:"2-digit", hour12:true,
    });
  } catch { return "—"; }
};

const nowIST = () =>
  new Date().toLocaleString("en-IN", {
    timeZone:"Asia/Kolkata", day:"2-digit", month:"short",
    hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:true,
  });

const STALE_THRESHOLD_MS = 2 * 60 * 1000;  // 2 min = 12 missed 10 s poll cycles

// A reading is stale if last_updated is older than STALE_THRESHOLD_MS.
// Normalise to UTC before comparison — API timestamps have no Z suffix
// (same pattern as toIST above; without it new Date() parses as local time
// and isStale always returns false for IST users).
function isStale(lastUpdated) {
  if (!lastUpdated) return true;
  const ts = lastUpdated.endsWith("Z") ? lastUpdated : lastUpdated + "Z";
  return (Date.now() - new Date(ts).getTime()) > STALE_THRESHOLD_MS;
}

// Human-readable relative time: "42s ago", "5m ago", "1h 12m ago", "Never".
function lastSeenText(lastUpdated) {
  if (!lastUpdated) return "Never";
  const ts      = lastUpdated.endsWith("Z") ? lastUpdated : lastUpdated + "Z";
  const diffMs  = Date.now() - new Date(ts).getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr  = Math.floor(diffMin / 60);
  if (diffSec < 60)  return diffSec + "s ago";
  if (diffMin < 60)  return diffMin + "m ago";
  return diffHr + "h " + (diffMin % 60) + "m ago";
}

// Four machine states, priority: STALE > RUNNING > STOPPED > NO_DATA.
// STOPPED VFDs still report real Modbus values (0 Hz, 0 A, ~565 V DC bus)
// so STOPPED must show real fmt() values, not "—".
function getMachineState(machine) {
  if (!machine.last_updated) return 'NO_DATA';
  if (isStale(machine.last_updated)) return 'STALE';
  const freq = machine.tags?.frequency ?? null;
  if (freq === null) return 'NO_DATA';
  if (freq > 0) return 'RUNNING';
  return 'STOPPED';
}

/* ═══════════════════════════════════════════════════════════════
   OFFLINE DEVELOPMENT ONLY
   buildFleet() and buildHistory() generate synthetic data for UI
   work without a backend connection.  They are no longer called in
   production.  Do not remove — useful when iterating on layout
   without a VPN or network.
═══════════════════════════════════════════════════════════════ */
const STOPPED = new Set([7, 12]); // Jet 02 and Jet 21 stopped at night

const buildFleet = () => MACHINES.map(m => {
  const on  = !STOPPED.has(m.id);
  const hz  = on ? rnd(27, 34) : 0;
  return {
    machine_id:            m.id,
    machine_name:          m.name,
    component_instance_id: m.cid,
    model:                 m.model,
    slave_id:              m.slave,
    last_updated: new Date(Date.now() - rnd(0,14000,0)).toISOString().slice(0,19),
    tags: {
      frequency:      on ? hz               : 0,
      current:        on ? rnd(4.5,8.5)     : 0,
      power:          on ? rnd(18,35)       : 0,
      rpm:            on ? Math.round(hz*57.5) : 0,
      torque:         on ? rnd(15,45)       : 0,
      output_voltage: on ? rnd(240,295,1)   : 0,
      dc_voltage:     rnd(555,582,1),
    },
  };
});

const buildHistory = (machineId) => {
  const now = Date.now();
  const on  = !STOPPED.has(machineId);
  return Array.from({length:61}, (_,i) => {
    const t = new Date(now - (60-i)*60_000);
    const label = t.toLocaleTimeString("en-IN",{
      timeZone:"Asia/Kolkata", hour:"2-digit", minute:"2-digit", hour12:false,
    });
    const hz = on ? 27 + Math.sin(i/8)*3 + rnd(-.4,.4) : 0;
    return {
      label,
      frequency:      on ? parseFloat(hz.toFixed(2))                                    : 0,
      current:        on ? parseFloat((6+Math.sin(i/10)*1.5+rnd(-.3,.3)).toFixed(2))   : 0,
      power:          on ? parseFloat((25+Math.sin(i/12)*5+rnd(-1,1)).toFixed(2))      : 0,
      rpm:            on ? Math.round(hz*57.5)                                          : 0,
      output_voltage: on ? parseFloat((265+Math.sin(i/15)*10+rnd(-2,2)).toFixed(1))    : 0,
      dc_voltage:     parseFloat((568+Math.sin(i/20)*8+rnd(-1,1)).toFixed(1)),
      torque:         on ? parseFloat((30+Math.sin(i/9)*8+rnd(-2,2)).toFixed(1))       : 0,
    };
  });
};

/* ═══════════════════════════════════════════════════════════════
   AUTH & API HELPERS
═══════════════════════════════════════════════════════════════ */
const tryLogin = async (email, password) => {
  // Offline dev shortcut — demo@mevion.com does not exist in the production DB:
  // if (email === "demo@mevion.com" && password === "demo") return "DEMO_TOKEN";
  const res = await fetch(`${API_BASE}/login`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ username: email, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Invalid credentials");
  }
  return (await res.json()).access_token;
};

// Authenticated fetch wrapper.
// On 401, throws an error with .status=401 so callers can trigger logout.
// On other non-OK responses, throws a descriptive error.
const apiFetch = async (path, token) => {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    const err = new Error("Session expired. Please sign in again.");
    err.status = 401;
    throw err;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Server error ${res.status}`);
  }
  return res.json();
};

// Flatten { bucket, tags: { frequency: 30.1, ... } } → { label: "HH:MM:SS", frequency: 30.1, ... }
// so Recharts dataKey={active} continues to work unchanged after the history shape change.
const flattenHistory = (buckets) =>
  (buckets || []).map(d => ({ label: toIST(d.bucket), ...(d.tags || {}) }));

/* ═══════════════════════════════════════════════════════════════
   STATUS DOT — pulse animation for running machines
═══════════════════════════════════════════════════════════════ */
const Dot = ({on, stale}) => (
  <span style={{position:"relative",display:"inline-flex",width:10,height:10,flexShrink:0}}>
    <span style={{
      position:"absolute",inset:0,borderRadius:"50%",
      background: (!stale && on) ? C.running : C.stopped,
      animation: (!stale && on) ? "dotPulse 2.5s ease-in-out infinite" : "none",
    }}/>
    {(!stale && on) && (
      <span style={{
        position:"absolute",inset:-3,borderRadius:"50%",
        background:C.running, opacity:0,
        animation:"dotRing 2.5s ease-out infinite",
      }}/>
    )}
  </span>
);

/* ═══════════════════════════════════════════════════════════════
   LOGIN PAGE
═══════════════════════════════════════════════════════════════ */
const LoginPage = ({onLogin}) => {
  const [email, setEmail]       = useState("");
  const [pass, setPass]         = useState("");
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState("");

  const submit = async () => {
    if (!email || !pass) { setError("Enter your email and password"); return; }
    setError(""); setLoading(true);
    try {
      const tok = await tryLogin(email, pass);
      onLogin(tok);
    } catch(e) {
      setError(e.message || "Login failed");
    } finally { setLoading(false); }
  };

  const INP = {
    width:"100%", padding:"10px 14px",
    border:`1.5px solid ${C.border}`, borderRadius:8,
    fontSize:15, color:C.text, background:C.white,
    fontFamily:"inherit", outline:"none",
    transition:"border-color .15s",
    boxSizing:"border-box",
  };

  return (
    <div style={{
      minHeight:"100vh", background:C.bg,
      display:"flex", flexDirection:"column",
      alignItems:"center", justifyContent:"center",
      fontFamily:"-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
      padding:24,
    }}>
      <style>{`
        @keyframes dotPulse{0%,100%{opacity:1}50%{opacity:.5}}
        @keyframes dotRing{0%{transform:scale(1);opacity:.4}100%{transform:scale(2.5);opacity:0}}
        *{box-sizing:border-box}
        .li:focus{border-color:${C.red}!important;box-shadow:0 0 0 3px rgba(227,24,55,.1)!important}
        .lb:hover:not(:disabled){opacity:.88;transform:translateY(-1px)}
        .lb{transition:all .15s ease;cursor:pointer}
        .so:hover{background:${C.bg}!important}
      `}</style>

      {/* Brand mark */}
      <div style={{textAlign:"center",marginBottom:44}}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"center",gap:10,marginBottom:6}}>
          <img src="/mevion-logo.png" style={{height:120, width:"auto"}} alt="mevion"/>
        </div>
        <div style={{fontSize:11,color:C.muted,letterSpacing:4,textTransform:"uppercase"}}>
          Data to Decisions
        </div>
      </div>

      {/* Card */}
      <div style={{
        background:C.white, borderRadius:16,
        padding:"36px 40px", width:"100%", maxWidth:400,
        border:`1px solid ${C.border}`,
        boxShadow:"0 4px 28px rgba(0,0,0,.06)",
      }}>
        <h1 style={{fontSize:20,fontWeight:700,color:C.text,margin:"0 0 4px"}}>Sign in</h1>
        <p style={{fontSize:14,color:C.muted,margin:"0 0 24px"}}>SSPPL Factory Monitor</p>

        {error && (
          <div style={{
            background:"#FFF5F5", border:"1px solid #FECACA",
            borderRadius:8, padding:"10px 14px", marginBottom:18,
            color:C.red, fontSize:13, lineHeight:1.5,
          }}>{error}</div>
        )}

        <div style={{marginBottom:14}}>
          <label style={{fontSize:13,fontWeight:600,color:C.text,display:"block",marginBottom:5}}>Email</label>
          <input className="li" type="email" value={email}
            onChange={e=>setEmail(e.target.value)}
            onKeyDown={e=>e.key==="Enter"&&submit()}
            placeholder="you@ssppl.com" style={INP}/>
        </div>

        <div style={{marginBottom:28}}>
          <label style={{fontSize:13,fontWeight:600,color:C.text,display:"block",marginBottom:5}}>Password</label>
          <input className="li" type="password" value={pass}
            onChange={e=>setPass(e.target.value)}
            onKeyDown={e=>e.key==="Enter"&&submit()}
            placeholder="••••••••" style={INP}/>
        </div>

        <button className="lb" onClick={submit} disabled={loading} style={{
          width:"100%", padding:12, background:C.red,
          color:C.white, border:"none", borderRadius:8,
          fontSize:15, fontWeight:700, letterSpacing:.3,
          opacity: loading ? .7 : 1,
        }}>
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </div>

      <p style={{marginTop:28,fontSize:12,color:C.muted}}>
        Shiv Shakti Prints Pvt Ltd · Surat, Gujarat
      </p>
    </div>
  );
};

/* ═══════════════════════════════════════════════════════════════
   MACHINE TILE
═══════════════════════════════════════════════════════════════ */
const Tile = ({machine, onClick}) => {
  // getMachineState() encodes four states: STALE > RUNNING > STOPPED > NO_DATA.
  // STOPPED VFDs report real 0 Hz / 0 A / ~565 V — show those values, not dashes.
  const state = getMachineState(machine);
  const [hov, setHov] = useState(false);

  const isRunning    = state === 'RUNNING';
  const isStaleState = state === 'STALE';

  // RUNNING and STOPPED get coloured pill chips; STALE/NO_DATA are plain text.
  const badge = (() => {
    if (state === 'RUNNING') return (
      <span style={{fontSize:10,fontWeight:700,letterSpacing:.5,
        background:"#22c55e",color:"#fff",padding:"1px 6px",borderRadius:4}}>RUNNING</span>
    );
    if (state === 'STOPPED') return (
      <span style={{fontSize:10,fontWeight:700,letterSpacing:.5,
        background:"#6b7280",color:"#fff",padding:"1px 6px",borderRadius:4}}>STOPPED</span>
    );
    if (state === 'STALE') return (
      <span style={{fontSize:10,fontWeight:700,letterSpacing:.5,color:"#9ca3af"}}>STALE</span>
    );
    return (
      <span style={{fontSize:10,fontWeight:700,letterSpacing:.5,color:"#374151"}}>NO DATA</span>
    );
  })();

  return (
    <div
      onClick={onClick}
      onMouseEnter={()=>setHov(true)}
      onMouseLeave={()=>setHov(false)}
      style={{
        background:C.white, borderRadius:12,
        border:`1.5px solid ${hov ? (isRunning ? "rgba(22,163,74,.35)" : C.border) : C.border}`,
        borderLeft:`4px solid ${isRunning ? C.running : C.stopped}`,
        padding:"18px 16px 13px",
        cursor:"pointer",
        transition:"all .14s ease",
        transform: hov ? "translateY(-2px)" : "none",
        boxShadow: hov ? "0 6px 22px rgba(0,0,0,.08)" : "none",
      }}
    >
      {/* Header row */}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:13}}>
        <div>
          <div style={{fontSize:15,fontWeight:800,color:C.text,letterSpacing:-.3}}>{machine.machine_name}</div>
          <div style={{fontSize:10,color:C.muted,marginTop:2}}>{machine.model}</div>
        </div>
        <div style={{display:"flex",alignItems:"center",gap:5,paddingTop:1}}>
          <Dot on={isRunning} stale={isStaleState}/>
          {badge}
        </div>
      </div>

      {/* Metrics 2×2
          RUNNING: accent red / C.text values.
          STOPPED: grey values — real zeros, not dashes (VFD is powered, motor idle).
          STALE:   muted last-known values — not dashes (operator needs last reading).
          NO_DATA: dashes. */}
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:"8px 14px",marginBottom:11}}>
        {[
          {label:"Frequency", val:fmt(machine.tags?.frequency),  unit:"Hz", big:true,  accent:true  },
          {label:"Power",     val:fmt(machine.tags?.power),      unit:"kW", big:true,  accent:true  },
          {label:"Current",   val:fmt(machine.tags?.current),    unit:"A",  big:false, accent:false },
          {label:"RPM",       val:fmt(machine.tags?.rpm, 0),     unit:"",   big:false, accent:false },
        ].map(m=>{
          const valueColor =
            state === 'RUNNING' ? (m.accent ? C.red : C.text) :
            state === 'STOPPED' ? '#9ca3af' :
            C.muted;
          const displayVal = state === 'NO_DATA' ? '—' : m.val;
          return (
            <div key={m.label}>
              <div style={{fontSize:10,color:C.muted,fontWeight:500,marginBottom:1}}>{m.label}</div>
              <div style={{
                fontSize: m.big ? 20 : 14,
                fontWeight: m.big ? 800 : 700,
                color: valueColor,
                fontVariantNumeric:"tabular-nums",
                letterSpacing: m.big ? -.3 : -.1,
                lineHeight:1.1,
              }}>
                {displayVal}
                {displayVal !== "—" && m.unit && (
                  <span style={{fontSize:10,fontWeight:400,color:C.muted,marginLeft:2}}>{m.unit}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer — amber relative time when stale; IST clock time when fresh */}
      <div style={{
        borderTop:`1px solid ${C.border}`,paddingTop:9,
        fontSize:10, color: isStaleState ? C.amber : C.muted,
      }}>
        {isStaleState
          ? "Last seen " + lastSeenText(machine.last_updated)
          : "Updated " + toIST(machine.last_updated) + " IST"}
      </div>
    </div>
  );
};

/* ═══════════════════════════════════════════════════════════════
   FLEET DASHBOARD
═══════════════════════════════════════════════════════════════ */
const FleetDashboard = ({token, onLogout, onSelect}) => {
  const [fleet, setFleet]     = useState([]);
  const [summary, setSummary] = useState({
    total_machines: 0, running: 0, stopped: 0, total_power_kw: 0, last_updated: null,
  });
  const [time, setTime]             = useState(nowIST());
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState(null);
  const [gatewayStatus, setGatewayStatus] = useState(null);

  // Poll gateway status every 30 s — independent of 10 s machine polling.
  // On failure, setGatewayStatus(null) so the badge simply disappears.
  useEffect(() => {
    const fetchGatewayStatus = () => {
      apiFetch('/gateway/status', token)
        .then(data => setGatewayStatus(data))
        .catch(() => setGatewayStatus(null));
    };
    fetchGatewayStatus();
    const iv = setInterval(fetchGatewayStatus, 30_000);
    return () => clearInterval(iv);
  }, [token]);

  const refresh = useCallback(async () => {
    try {
      const [liveData, summaryData] = await Promise.all([
        apiFetch("/machines/live", token),
        apiFetch("/fleet/summary", token),
      ]);
      // Merge API rows with MACHINES master list.
      // Machines absent from the API (no telemetry yet, e.g. loose RS485 ferrule)
      // appear in the grid with empty tags — visible as STOPPED with "—" readings.
      const byId = Object.fromEntries(liveData.map(m => [m.machine_id, m]));
      const merged = MACHINES.map(m => {
        const api = byId[m.id];
        return api
          ? { ...api, model: m.model, slave_id: m.slave }
          : { machine_id: m.id, machine_name: m.name,
              component_instance_id: m.cid, model: m.model,
              slave_id: m.slave, last_updated: null, tags: {} };
      });
      setFleet(merged);
      setSummary(summaryData);
      setError(null);
    } catch (e) {
      if (e.status === 401) { onLogout(); return; }
      setError(e.message || "API unreachable");
    } finally {
      setLoading(false);
      setTime(nowIST());
    }
  }, [token, onLogout]);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, POLL_MS);
    return () => clearInterval(iv);
  }, [refresh]);

  return (
    <div style={{
      minHeight:"100vh", background:C.bg,
      fontFamily:"-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
    }}>
      <style>{`
        @keyframes dotPulse{0%,100%{opacity:1}50%{opacity:.5}}
        @keyframes dotRing{0%{transform:scale(1);opacity:.4}100%{transform:scale(2.5);opacity:0}}
        *{box-sizing:border-box}
        .so{transition:background .12s}
        .so:hover{background:${C.bg}!important}
        @media(max-width:960px){.fg{grid-template-columns:repeat(3,1fr)!important}}
        @media(max-width:650px){.fg{grid-template-columns:repeat(2,1fr)!important}.ks{grid-template-columns:repeat(2,1fr)!important}}
        @media(max-width:400px){.fg{grid-template-columns:1fr!important}}
      `}</style>

      {/* Header */}
      <header style={{
        background:C.white, borderBottom:`1px solid ${C.border}`,
        padding:"0 24px", height:56,
        display:"flex", alignItems:"center", justifyContent:"space-between",
        position:"sticky", top:0, zIndex:100,
      }}>
        <div style={{display:"flex",alignItems:"center",gap:14}}>
          <div style={{display:"flex",alignItems:"center",gap:8}}>
            <img src="/mevion-logo.png" style={{height:48, width:"auto"}} alt="mevion"/>
          </div>
          <span style={{width:1,height:18,background:C.border}}/>
          <span style={{fontSize:13,color:C.muted,fontWeight:500}}>Factory Monitor</span>
        </div>
        <div style={{display:"flex",alignItems:"center",gap:14}}>
          {/* Gateway status badge — shown once the first /gateway/status fetch returns.
              Hidden on null (failed fetch or first load) so the header stays clean. */}
          {gatewayStatus !== null && (
            <div style={{display:"flex",alignItems:"center",gap:6,fontSize:13}}>
              {/* Coloured dot: green = online, amber = offline */}
              <span style={{
                width:8, height:8, borderRadius:"50%", display:"inline-block", flexShrink:0,
                background: gatewayStatus.is_online ? "#22c55e" : "#f59e0b",
              }}/>
              {/* Text: dark colours — the header background is white */}
              <span style={{color: gatewayStatus.is_online ? C.running : C.amber}}>
                {gatewayStatus.is_online
                  ? `Gateway · ${gatewayStatus.seconds_ago}s ago`
                  : `Gateway offline · ${lastSeenText(gatewayStatus.last_seen)}`
                }
              </span>
              {/* Modbus-error chip: only when online but some slaves failed */}
              {gatewayStatus.is_online && gatewayStatus.machines_failed > 0 && (
                <span style={{
                  background:"#7c2d12", color:"#fca5a5",
                  borderRadius:4, padding:"1px 5px", fontSize:11,
                }}>
                  {gatewayStatus.machines_failed} Modbus error{gatewayStatus.machines_failed > 1 ? "s" : ""}
                </span>
              )}
            </div>
          )}
          <span style={{fontSize:12,color:C.muted}}>{time} IST</span>
          <button className="so" onClick={onLogout} style={{
            padding:"6px 14px", border:`1.5px solid ${C.border}`,
            borderRadius:8, background:C.white, cursor:"pointer",
            fontSize:13, color:C.text, fontWeight:500,
          }}>Sign out</button>
        </div>
      </header>

      <main style={{padding:"20px 24px", maxWidth:1440, margin:"0 auto"}}>
        {/* KPI bar — values from /fleet/summary; total shows server count / master count */}
        <div className="ks" style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:14,marginBottom:20}}>
          {[
            {label:"Total machines", val:`${summary.total_machines} / ${MACHINES.length}`, unit:"",   color:C.text   },
            {label:"Running",        val:summary.running,                                   unit:"",   color:C.running},
            {label:"Stopped",        val:summary.stopped,                                   unit:"",   color:C.stopped},
            {label:"Total power",    val:(summary.total_power_kw ?? 0).toFixed(1),          unit:"kW", color:C.red    },
          ].map(k=>(
            <div key={k.label} style={{
              background:C.white, borderRadius:12, padding:"18px 20px",
              border:`1.5px solid ${C.border}`,
            }}>
              <div style={{fontSize:11,color:C.muted,marginBottom:5,fontWeight:500}}>{k.label}</div>
              <div style={{fontSize:30,fontWeight:900,color:k.color,fontVariantNumeric:"tabular-nums",letterSpacing:-1,lineHeight:1}}>
                {k.val}
                {k.unit && <span style={{fontSize:13,fontWeight:400,color:C.muted,marginLeft:4}}>{k.unit}</span>}
              </div>
            </div>
          ))}
        </div>

        {/* Staleness banner — shown when any machine has stale data.
            Not dismissible: disappears automatically when fresh data arrives.
            Amber = warning, not fault (the API itself is working fine). */}
        {fleet.some(m => isStale(m.last_updated)) && (() => {
          const validTs = fleet
            .filter(m => m.last_updated)
            .map(m => new Date(
              m.last_updated.endsWith("Z") ? m.last_updated : m.last_updated + "Z"
            ).getTime());
          const oldestMs = validTs.length ? Math.max(...validTs) : null;
          const agoText  = oldestMs
            ? lastSeenText(new Date(oldestMs).toISOString())
            : "unknown";
          return (
            <div style={{
              background:"#FFFBEB", border:"1px solid #FDE68A", borderRadius:8,
              padding:"10px 16px", marginBottom:14,
              color:"#92400E", fontSize:13, lineHeight:1.6,
            }}>
              <div style={{fontWeight:700,marginBottom:2}}>
                ⚠ Gateway data is stale — last reading received {agoText}.
              </div>
              <div style={{fontSize:12,opacity:.85}}>
                The gateway may be offline or the factory internet connection may be down.
              </div>
            </div>
          );
        })()}

        {/* Error banner — shown when a poll fails; keeps last known grid visible */}
        {error && (
          <div style={{
            background:"#FFF5F5", border:"1px solid #FECACA", borderRadius:8,
            padding:"10px 16px", marginBottom:14,
            color:C.red, fontSize:13,
            display:"flex", justifyContent:"space-between", alignItems:"center",
          }}>
            <span>⚠ {error}</span>
            <span style={{color:C.muted, fontSize:11}}>Retrying in 10 s</span>
          </div>
        )}

        {/* Section label */}
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:13}}>
          <h2 style={{fontSize:13,fontWeight:700,color:C.text,margin:0,textTransform:"uppercase",letterSpacing:.5}}>
            Jet dyeing machines
          </h2>
          <span style={{fontSize:11,color:C.muted}}>Auto-refreshes every 10 s</span>
        </div>

        {/* Grid */}
        {loading ? (
          <div style={{textAlign:"center",padding:60,color:C.muted}}>Loading fleet data…</div>
        ) : fleet.length === 0 && error ? (
          <div style={{textAlign:"center",padding:60,color:C.red}}>{error}</div>
        ) : (
          <div className="fg" style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:14}}>
            {fleet.map(m=>(
              <Tile key={m.machine_id} machine={m} onClick={()=>onSelect(m)}/>
            ))}
          </div>
        )}
      </main>
    </div>
  );
};

/* ═══════════════════════════════════════════════════════════════
   JET DETAIL PAGE
═══════════════════════════════════════════════════════════════ */
const METRICS_DEF = [
  {key:"frequency",      label:"Frequency",       unit:"Hz", color:C.red,     dec:2 },
  {key:"current",        label:"Current",          unit:"A",  color:"#2563EB", dec:2 },
  {key:"power",          label:"Power",            unit:"kW", color:"#D97706", dec:2 },
  {key:"rpm",            label:"RPM",              unit:"",   color:"#7C3AED", dec:0, int:true },
  {key:"output_voltage", label:"Output voltage",   unit:"V",  color:"#059669", dec:1 },
  {key:"dc_voltage",     label:"DC bus voltage",   unit:"V",  color:C.muted,   dec:1 },
  {key:"torque",         label:"Torque",           unit:"%",  color:"#DB2777", dec:1 },
];

const TIME_WINDOWS = [1, 3, 6, 12, 24];  // hours; drives the history fetch and chart title

const JetDetail = ({machine, token, onBack, onLogout}) => {
  const [live, setLive]               = useState(machine);
  const [hist, setHist]               = useState([]);
  const [active, setActive]           = useState("frequency");
  const [historyHours, setHistoryHours] = useState(1);
  const [time, setTime]               = useState(nowIST());
  const [error, setError]             = useState(null);

  const on  = (live.tags?.frequency ?? 0) > 0;
  const met = METRICS_DEF.find(m=>m.key===active) || METRICS_DEF[0];

  useEffect(() => {
    let cancelled = false;

    const fetchAll = async () => {
      try {
        const [liveData, histData] = await Promise.all([
          apiFetch(`/machines/${machine.machine_id}/live`, token),
          apiFetch(`/machines/${machine.machine_id}/history?hours=${historyHours}`, token),
        ]);
        if (cancelled) return;
        // Recover model/slave_id from master list — not present in the live API response.
        const master = MACHINES_MAP[machine.machine_id] || {};
        setLive({ ...liveData, model: master.model, slave_id: master.slave });
        setHist(flattenHistory(histData.data));
        setError(null);
      } catch (e) {
        if (cancelled) return;
        if (e.status === 401) { onLogout(); return; }
        setError(e.message || "API unreachable");
      }
      if (!cancelled) setTime(nowIST());
    };

    fetchAll();
    const iv = setInterval(fetchAll, POLL_MS);
    return () => { cancelled = true; clearInterval(iv); };
  }, [machine.machine_id, token, onLogout, historyHours]);

  // fmt here is scoped to JetDetail to handle the "alive" check:
  // dc_voltage is shown even when frequency=0 (machine stopped but powered).
  // It intentionally shadows the module-level fmt — JetDetail needs metric context.
  const fmt = (m, val) => {
    const alive = on || m.key === "dc_voltage";
    if (!alive || val === undefined || val === null) return "—";
    if (m.int) return Math.round(val).toLocaleString();
    return parseFloat(val).toFixed(m.dec);
  };

  return (
    <div style={{
      minHeight:"100vh", background:C.bg,
      fontFamily:"-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
    }}>
      <style>{`
        @keyframes dotPulse{0%,100%{opacity:1}50%{opacity:.5}}
        @keyframes dotRing{0%{transform:scale(1);opacity:.4}100%{transform:scale(2.5);opacity:0}}
        *{box-sizing:border-box}
        .mc{transition:all .14s;cursor:pointer}
        .mc:hover{box-shadow:0 0 0 3px rgba(227,24,55,.1)!important;border-color:${C.red}!important}
        .so{transition:background .12s}.so:hover{background:${C.bg}!important}
        @media(max-width:800px){.mr{grid-template-columns:repeat(4,1fr)!important}}
        @media(max-width:500px){.mr{grid-template-columns:repeat(2,1fr)!important}}
      `}</style>

      {/* Header */}
      <header style={{
        background:C.white, borderBottom:`1px solid ${C.border}`,
        padding:"0 24px", height:56,
        display:"flex", alignItems:"center", justifyContent:"space-between",
        position:"sticky", top:0, zIndex:100,
      }}>
        <div style={{display:"flex",alignItems:"center",gap:12}}>
          <button className="so" onClick={onBack} style={{
            padding:"6px 12px", border:`1.5px solid ${C.border}`,
            borderRadius:8, background:C.white, cursor:"pointer",
            fontSize:13, color:C.text, fontWeight:600,
            display:"flex", alignItems:"center", gap:5,
          }}>← Fleet</button>
          <span style={{width:1,height:18,background:C.border}}/>
          <span style={{fontSize:15,fontWeight:800,color:C.text}}>{machine.machine_name}</span>
          <div style={{display:"flex",alignItems:"center",gap:6}}>
            <Dot on={on}/>
            <span style={{fontSize:10,fontWeight:700,color:on?C.running:C.stopped,letterSpacing:.5}}>
              {on?"RUNNING":"STOPPED"}
            </span>
          </div>
        </div>
        <div style={{display:"flex",alignItems:"center",gap:12}}>
          <span style={{fontSize:11,color:C.muted}}>{time} IST</span>
          <div style={{display:"flex",alignItems:"center",gap:6}}>
            <img src="/mevion-logo.png" style={{height:44, width:"auto"}} alt="mevion"/>
          </div>
        </div>
      </header>

      <main style={{padding:"20px 24px", maxWidth:1440, margin:"0 auto"}}>
        {/* Badge row */}
        <div style={{marginBottom:16,display:"flex",gap:10,alignItems:"center"}}>
          <span style={{
            fontSize:12, background:"#F3F4F6", color:C.muted,
            padding:"3px 10px", borderRadius:6, fontWeight:500,
          }}>{machine.model}</span>
          <span style={{fontSize:12,color:C.muted}}>
            Slave {machine.slave_id} · Last seen {toIST(live.last_updated)} IST
          </span>
        </div>

        {/* Error banner — keeps last known data visible with a clear warning */}
        {error && (
          <div style={{
            background:"#FFF5F5", border:"1px solid #FECACA", borderRadius:8,
            padding:"10px 16px", marginBottom:14, color:C.red, fontSize:13,
          }}>⚠ {error} — showing last known data</div>
        )}

        {/* Metrics row */}
        <div className="mr" style={{display:"grid",gridTemplateColumns:"repeat(7,1fr)",gap:12,marginBottom:20}}>
          {METRICS_DEF.map(m=>{
            const isAct = active===m.key;
            const val   = live.tags?.[m.key];
            const alive = on || m.key==="dc_voltage";
            return (
              <div
                key={m.key} className="mc"
                onClick={()=>setActive(m.key)}
                style={{
                  background:C.white, borderRadius:10, padding:"14px 14px",
                  border:`1.5px solid ${isAct ? m.color : C.border}`,
                  boxShadow: isAct ? `0 0 0 3px ${m.color}22` : "none",
                }}
              >
                <div style={{fontSize:10,color:C.muted,marginBottom:3,fontWeight:500}}>{m.label}</div>
                <div style={{
                  fontSize:17, fontWeight:800,
                  color: alive ? m.color : C.stopped,
                  fontVariantNumeric:"tabular-nums", letterSpacing:-.2,
                }}>
                  {fmt(m,val)}
                  {alive && val !== undefined && val !== null && m.unit && (
                    <span style={{fontSize:10,fontWeight:400,color:C.muted,marginLeft:2}}>{m.unit}</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* Chart panel */}
        <div style={{background:C.white,borderRadius:12,padding:"22px 24px",border:`1.5px solid ${C.border}`}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:16}}>
            <div>
              <div style={{fontSize:14,fontWeight:700,color:C.text}}>
                {met.label} — last {historyHours === 1 ? "60 minutes" : `${historyHours} hours`}
              </div>
              <div style={{fontSize:11,color:C.muted,marginTop:3}}>
                Select a metric above to switch view · auto-refreshes every 10 s
              </div>
            </div>
            <div style={{
              fontSize:28, fontWeight:900, color:met.color,
              fontVariantNumeric:"tabular-nums", letterSpacing:-1,
            }}>
              {fmt(met, live.tags?.[met.key])}
              <span style={{fontSize:13,fontWeight:400,color:C.muted,marginLeft:4}}>{met.unit}</span>
            </div>
          </div>

          {/* Time window selector — changes history fetch depth and chart title */}
          <div style={{display:"flex",gap:6,marginBottom:16}}>
            {TIME_WINDOWS.map(h => (
              <button
                key={h}
                onClick={()=>setHistoryHours(h)}
                style={{
                  padding:"4px 12px", borderRadius:6, fontFamily:"inherit",
                  border: historyHours===h ? "none" : "1px solid #374151",
                  background: historyHours===h ? C.red : "transparent",
                  color: historyHours===h ? "#fff" : "#9ca3af",
                  fontWeight: historyHours===h ? 700 : 400,
                  fontSize:12, cursor:"pointer",
                }}
              >{h}h</button>
            ))}
          </div>

          {/* flattenHistory maps { bucket, tags:{...} } → { label, frequency, power, ... }
              so dataKey={active} reads the right field without any chart code changes. */}
          <ResponsiveContainer width="100%" height={250}>
            <AreaChart data={hist} margin={{top:4,right:8,left:-8,bottom:0}}>
              <defs>
                <linearGradient id={`g_${active}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={met.color} stopOpacity={0.13}/>
                  <stop offset="95%" stopColor={met.color} stopOpacity={0}/>
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false}/>
              <XAxis dataKey="label" tick={{fontSize:10,fill:C.muted}} tickLine={false} axisLine={false} interval={9}/>
              <YAxis tick={{fontSize:10,fill:C.muted}} tickLine={false} axisLine={false} width={38}/>
              <Tooltip
                contentStyle={{background:C.white,border:`1px solid ${C.border}`,borderRadius:8,fontSize:12}}
                formatter={v=>[`${typeof v==="number"?v.toFixed(met.dec):v} ${met.unit}`,met.label]}
                labelStyle={{color:C.text,fontWeight:600}}
              />
              <Area
                type="monotone" dataKey={active}
                stroke={met.color} strokeWidth={2}
                fill={`url(#g_${active})`}
                dot={false} activeDot={{r:4,strokeWidth:0,fill:met.color}}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </main>
    </div>
  );
};

/* ═══════════════════════════════════════════════════════════════
   APP ROOT
═══════════════════════════════════════════════════════════════ */
export default function App() {
  const [page, setPage]   = useState("login");
  const [token, setToken] = useState(null);
  const [sel, setSel]     = useState(null);

  const logout = () => { setToken(null); setPage("login"); setSel(null); };

  if (page === "login")
    return <LoginPage onLogin={t => { setToken(t); setPage("fleet"); }}/>;

  if (page === "fleet")
    return (
      <FleetDashboard
        token={token}
        onLogout={logout}
        onSelect={m => { setSel(m); setPage("detail"); }}
      />
    );

  if (page === "detail" && sel)
    return (
      <JetDetail
        machine={sel} token={token}
        onBack={() => { setSel(null); setPage("fleet"); }}
        onLogout={logout}
      />
    );

  return <LoginPage onLogin={t => { setToken(t); setPage("fleet"); }}/>;
}
