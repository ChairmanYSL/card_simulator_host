import { useState } from "react";
import { api } from "../api";
import type { LogLine, OperatorInstructions, SessionInfo } from "../types";
import { fmtTime, Tag } from "../components/Tag";
import { TerminalLog } from "../components/TerminalLog";

export function RunPage({
  session,
  instructions,
  logs,
}: {
  session: SessionInfo;
  instructions: Record<string, OperatorInstructions>;
  logs: LogLine[];
}) {
  const [error, setError] = useState<string | null>(null);
  const [busyCmd, setBusyCmd] = useState(false);

  const run = async (fn: () => Promise<unknown>) => {
    setBusyCmd(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyCmd(false);
    }
  };

  if (!session) {
    return (
      <div>
        <h3 className="section-title"><b>03</b> / run view · 运行视图</h3>
        <div className="panel muted">当前没有运行。到「卡组入口」选择用例开始测试。</div>
      </div>
    );
  }

  const current = session.current;
  const waiting = current.state === "WAIT_RESULT";
  const instr = current.case_id ? instructions[current.case_id] : null;
  const started = session.run_status !== "PENDING";
  // H1: ERROR is terminal too — an android-side error ends the run, so the live
  // view must stop and the controls must disable like every other终态.
  const finished = ["FINISHED", "STOPPED", "ABORTED", "INTERRUPTED", "ERROR"].includes(session.run_status);

  return (
    <div>
      <h3 className="section-title">
        <b>03</b> / run view · {session.brand.toUpperCase()} / {session.mode.toUpperCase()}
      </h3>

      {error && <div className="panel" style={{ borderColor: "var(--red)", color: "var(--red)" }}>{error}</div>}

      <div className="panel">
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <span className="muted">run</span>
          <span className="mono">{session.run_id}</span>
          <Tag status={session.run_status} />
          <span className="muted">current</span>
          <span className="mono">{current.case_id ?? "—"}</span>
          <span className="muted">attempt</span>
          <span className="mono">{current.case_id ? current.attempt : "—"}</span>
          <span className="muted">state</span>
          <span className="mono">{current.state}</span>
          {session.fatal_reason && (
            <span style={{ color: "var(--red)" }}>[{session.fatal_reason}]</span>
          )}
          <span style={{ marginLeft: "auto", display: "flex", gap: 10 }}>
            <button className="btn danger" disabled={finished || busyCmd}
              onClick={() => run(() => api.abort())}>
              ⏹ 立即中止（当前用例 ABORTED）
            </button>
            <button className="btn" disabled={finished || busyCmd}
              onClick={() => run(() => api.stop())}>
              ⏸ 停止（本用例后停）
            </button>
            <button className="btn" disabled={!waiting || busyCmd}
              onClick={() => run(() => api.skip())}>
              ⏭ 跳过当前用例
            </button>
          </span>
        </div>
        {started && !finished && (
          <div className="muted" style={{ marginTop: 8 }}>
            live — 结果随 POS 回传实时更新
          </div>
        )}
      </div>

      {waiting && instr && (
        <div className="operator-box">
          <div className="title">operator instructions · 操作员指引</div>
          <div className="big">
            {instr.amount} {instr.currency}
            <span className="muted" style={{ fontSize: 13, marginLeft: 12 }}>
              entry={instr.entry_mode.toUpperCase()} pin={instr.pin_hint.toUpperCase()}
            </span>
          </div>
          <div className="note">{instr.notes}</div>
        </div>
      )}

      <div className="panel">
        <div className="section-title">queue · 用例队列</div>
        <table className="grid">
          <thead>
            <tr>
              <th>case id</th>
              <th>status</th>
              <th>ops / resp (v2)</th>
              <th>attempts</th>
              <th>failure reason</th>
              <th>finished</th>
            </tr>
          </thead>
          <tbody>
            {session.queue.map((caseId) => {
              const r = session.results[caseId];
              const isCurrent = current.case_id === caseId;
              return (
                <tr key={caseId} className={`case-row ${isCurrent ? "current" : ""}`}>
                  <td className="mono">
                    {caseId} {isCurrent && <span className="muted">◂ RUNNING</span>}
                  </td>
                  <td>{r ? <Tag status={r.status} /> : <Tag status={isCurrent ? "RUNNING" : "PENDING"} />}</td>
                  <td className="mono" style={{ fontSize: 12 }}>
                    {r?.ops_status ? (
                      <>
                        {r.ops_status}
                        {r.resp_code && <span className="muted"> · {r.resp_code}</span>}
                      </>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="muted">{r?.attempts ?? 0}</td>
                  <td className="mono" style={{
                    color: r?.failure_reason ? "var(--red)" : undefined,
                    fontSize: 12,
                  }}>
                    {r?.failure_reason || "—"}
                  </td>
                  <td className="muted">{r?.finished_at ? fmtTime(r.finished_at) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="muted" style={{ fontSize: 12 }}>
          v1.2：首个 FAIL 即停止整轮，其后案例显示 <span className="mono">SKIPPED</span>；
          <span className="mono"> failure_reason</span> 为本次失败的直接原因。
          <br />
          v2：POS 回传 OUT 后，<span className="mono">ops / resp</span> 列显示 OPS status
          与 <span className="mono">resp_code</span>（运行期间即时更新，无需等整轮结束）。
        </div>
      </div>

      <div className="panel">
        <div className="section-title">console · 实时日志</div>
        <TerminalLog lines={logs} />
      </div>
    </div>
  );
}
