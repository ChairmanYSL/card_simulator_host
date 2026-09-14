import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { connectWs } from "./ws";
import type { LogLine, OperatorInstructions, SchemeNode, SessionInfo, Snapshot, WsEvent } from "./types";
import { StatusBar } from "./components/StatusBar";
import { SchemesPage } from "./pages/SchemesPage";
import { CaseSelectPage } from "./pages/CaseSelectPage";
import { AddCasePage } from "./pages/AddCasePage";
import { RunPage } from "./pages/RunPage";
import { HistoryPage } from "./pages/HistoryPage";

type View =
  | { name: "schemes" }
  | { name: "select"; scheme: string; mode: string }
  | { name: "add" }
  | { name: "run" }
  | { name: "history" };

const EMPTY_SNAPSHOT: Snapshot = { devices: {}, session: null };

export default function App() {
  const [view, setView] = useState<View>({ name: "schemes" });
  const [snapshot, setSnapshot] = useState<Snapshot>(EMPTY_SNAPSHOT);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [instructions, setInstructions] = useState<Record<string, OperatorInstructions>>({});
  const [wsConnected, setWsConnected] = useState(false);
  const [tree, setTree] = useState<{ schemes: SchemeNode[]; issues: { path: string; severity: string; message: string }[] }>(
    { schemes: [], issues: [] },
  );
  const [startError, setStartError] = useState<string | null>(null);
  const snapshotRef = useRef(snapshot);
  snapshotRef.current = snapshot;

  const applyEvent = useCallback((event: WsEvent) => {
    const { event: name, data } = event;
    if (name === "log") {
      const line = data as unknown as LogLine;
      setLogs((prev) => [...prev.slice(-499), line]);
      return;
    }
    if (name === "device_status") {
      const devices = (data as { devices: Record<string, never> }).devices;
      setSnapshot((prev) => ({ ...prev, devices }));
      return;
    }
    if (name === "run_status") {
      const session = data as unknown as SessionInfo;
      setSnapshot((prev) => ({ ...prev, session }));
      return;
    }
    if (name === "case_status") {
      const caseStatus = data as {
        case_id: string; status: string; attempt?: number; attempt_id?: string;
        state?: string; operator_instructions?: OperatorInstructions;
      };
      setSnapshot((prev) => {
        if (!prev.session) return prev;
        const results = { ...prev.session.results };
        results[caseStatus.case_id] = {
          ...(results[caseStatus.case_id] ?? {}),
          status: caseStatus.status,
          attempts: caseStatus.attempt !== undefined ? caseStatus.attempt + 1 : results[caseStatus.case_id]?.attempts,
        };
        const session: SessionInfo = {
          ...prev.session,
          results,
          current:
            caseStatus.case_id === prev.session.current.case_id
              ? { ...prev.session.current, state: caseStatus.state ?? prev.session.current.state }
              : prev.session.current,
        };
        return { ...prev, session };
      });
      if (caseStatus.operator_instructions) {
        setInstructions((prev) => ({ ...prev, [caseStatus.case_id]: caseStatus.operator_instructions! }));
      }
      return;
    }
    if (name === "quarantine") {
      const q = data as { reason: string; frame?: { case_id?: string } };
      setLogs((prev) => [
        ...prev.slice(-499),
        { level: "warn", message: `quarantined result (${q.reason}) case=${q.frame?.case_id ?? "?"}`, ts: Date.now() / 1000 },
      ]);
      return;
    }
  }, []);

  useEffect(() => {
    const close = connectWs({
      onSnapshot: (snap) => setSnapshot(snap),
      onEvent: applyEvent,
      onStatus: setWsConnected,
    });
    // REST is authoritative: refresh the snapshot periodically as a fallback
    const poll = setInterval(() => {
      api.snapshot().then(setSnapshot).catch(() => undefined);
    }, 5000);
    return () => {
      close();
      clearInterval(poll);
    };
  }, [applyEvent]);

  useEffect(() => {
    api.tree().then(setTree).catch(() => undefined);
  }, []);

  const onStart = async (kind: string, caseIds: string[], options: { on_failure: string; retry_count: number }) => {
    if (view.name !== "select") return;
    setStartError(null);
    try {
      await api.startRun({
        scheme: view.scheme,
        mode: view.mode,
        kind,
        case_ids: caseIds,
        on_failure: options.on_failure,
        retry_count: options.retry_count,
      });
      setView({ name: "run" });
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e));
    }
  };

  const refreshTree = () => {
    api.tree().then(setTree).catch(() => undefined);
  };

  const nav = (name: View["name"]) => setView({ name } as View);

  const schemeNode = view.name === "select"
    ? tree.schemes.find((s) => s.scheme === view.scheme)
    : undefined;

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="brand">
          EMV<span style={{ color: "var(--dim)" }}>//</span>HOST
          <small>auto test rig v0.1</small>
        </div>
        <button className={`nav-item ${view.name === "schemes" ? "active" : ""}`}
          onClick={() => nav("schemes")}>01 · 卡组入口</button>
        <button className={`nav-item ${view.name === "add" ? "active" : ""}`}
          onClick={() => nav("add")}>02 · 添加用例</button>
        <button className={`nav-item ${view.name === "run" ? "active" : ""}`}
          onClick={() => nav("run")}>03 · 运行视图</button>
        <button className={`nav-item ${view.name === "history" ? "active" : ""}`}
          onClick={() => nav("history")}>04 · 历史记录</button>
        <div className={`ws-state ${wsConnected ? "on" : ""}`}>
          <span className="dot" />{wsConnected ? "LINK UP" : "LINK DOWN"}
        </div>
      </nav>

      <div className="main">
        <StatusBar devices={snapshot.devices} session={snapshot.session} wsConnected={wsConnected} />
        <div className="content">
          {startError && (
            <div className="panel" style={{ borderColor: "var(--red)", color: "var(--red)" }}>
              start failed: {startError}
            </div>
          )}
          {view.name === "schemes" && (
            <SchemesPage
              schemes={tree.schemes}
              issues={tree.issues}
              onEnter={(scheme, mode) => setView({ name: "select", scheme, mode })}
            />
          )}
          {view.name === "select" && schemeNode && (
            <CaseSelectPage
              schemeNode={schemeNode}
              defaultMode={view.mode}
              onBack={() => setView({ name: "schemes" })}
              onStart={onStart}
            />
          )}
          {view.name === "add" && (
            <AddCasePage
              schemes={tree.schemes}
              onAdded={refreshTree}
              onGoto={(scheme, mode) => setView({ name: "select", scheme, mode })}
            />
          )}
          {view.name === "run" && (
            <RunPage session={snapshot.session} instructions={instructions} logs={logs} />
          )}
          {view.name === "history" && <HistoryPage />}
        </div>
      </div>
    </div>
  );
}
