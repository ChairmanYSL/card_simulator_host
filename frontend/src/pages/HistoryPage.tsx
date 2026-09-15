import { useEffect, useState } from "react";
import { api } from "../api";
import type { HistorySummary, RunDetail } from "../types";
import { fmtDuration, fmtTime, Tag } from "../components/Tag";

function Count({ n, label }: { n: number; label: string }) {
  if (!n) return null;
  return <span className="muted"> {label}={n}</span>;
}

export function HistoryPage() {
  const [runs, setRuns] = useState<HistorySummary[]>([]);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setRuns((await api.history()).runs);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const open = async (runId: string) => {
    try {
      setDetail(await api.historyDetail(runId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const maskPan = (value: unknown): string => {
    const s = String(value ?? "");
    if (/^\d{13,19}$/.test(s)) {
      return s.slice(0, 6) + "******" + s.slice(-4);
    }
    return s;
  };

  return (
    <div>
      <div className="toolbar">
        <h3 className="section-title" style={{ margin: 0 }}><b>04</b> / history · 历史记录</h3>
        <button className="btn" style={{ marginLeft: "auto" }} onClick={() => void refresh()}>⟳ 刷新</button>
      </div>
      {error && <div className="panel" style={{ borderColor: "var(--red)", color: "var(--red)" }}>{error}</div>}

      <div className="panel">
        <table className="grid">
          <thead>
            <tr>
              <th>run id</th>
              <th>scheme / mode</th>
              <th>kind</th>
              <th>status</th>
              <th>result</th>
              <th>started</th>
              <th>duration</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id} className="clickable" onClick={() => void open(r.run_id)}>
                <td className="mono">{r.run_id}</td>
                <td className="mono">{r.scheme}/{r.mode}</td>
                <td className="muted">{r.kind}</td>
                <td><Tag status={r.status} /></td>
                <td>
                  <Count n={r.passed} label="PASS" />
                  <Count n={r.failed} label="FAIL" />
                  <Count n={r.warned} label="WARN" />
                  <Count n={r.skipped} label="SKIP" />
                  <Count n={r.inconclusive} label="INC" />
                  <Count n={r.aborted} label="ABORT" />
                  <Count n={r.errored} label="ERR" />
                </td>
                <td className="muted">{fmtTime(r.started_at)}</td>
                <td className="muted">
                  {r.finished_at ? fmtDuration(r.finished_at - (r.started_at ?? r.created_at)) : "—"}
                </td>
              </tr>
            ))}
            {runs.length === 0 && (
              <tr><td colSpan={7} className="muted">no runs recorded yet</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {detail && (
        <div className="panel gap">
          <div className="section-title">
            run <span className="mono">{detail.run.run_id}</span> detail
            {detail.run.abort_reason && (
              <span style={{ color: "var(--amber)" }}> · aborted: {String(detail.run.abort_reason)}</span>
            )}
          </div>
          <table className="grid">
            <thead>
              <tr>
                <th>case id</th>
                <th>status</th>
                <th>attempts</th>
                <th>failure reason</th>
                <th>assertion detail</th>
              </tr>
            </thead>
            <tbody>
              {Object.values(detail.results).map((r) => (
                <tr key={r.case_id}>
                  <td className="mono">{r.case_id}</td>
                  <td><Tag status={r.status} /></td>
                  <td className="muted">{r.attempts}</td>
                  <td className="mono" style={{
                    color: r.failure_reason ? "var(--red)" : undefined,
                    fontSize: 12,
                  }}>
                    {r.failure_reason || "—"}
                  </td>
                  <td>
                    {r.verdict?.rules.map((rule, i) => (
                      <div key={i} className={`rule-line ${rule.ok ? "ok" : "bad"}`}>
                        <span className="mono">{rule.path}</span> {rule.op}{" "}
                        {rule.ok ? "" : `expected=${maskPan(rule.expect)} actual=${maskPan(rule.actual)}`}
                        {rule.ok ? ` = ${maskPan(rule.actual)}` : ""}
                        <span className="muted"> [{rule.severity}]</span>
                      </div>
                    ))}
                    {r.note && <div className="rule-line muted">{r.note}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="muted" style={{ fontSize: 12 }}>
            v1.2：任一案例 FAIL 后整轮立即停止，其余案例记为 <span className="mono">SKIPPED</span>
            （note=<span className="mono">run_stopped_after_failure:&lt;case_id&gt;</span>），不是漏跑；
            <span className="mono"> failure_reason</span> 说明 FAIL 的具体原因。
          </div>
          {detail.quarantine.length > 0 && (
            <div className="gap">
              <div className="section-title">quarantine · 迟到/无主结果</div>
              {detail.quarantine.map((q, i) => (
                <div key={i} className="rule-line" style={{ color: "var(--amber)" }}>
                  {fmtTime(q.at)} — {q.reason} — {JSON.stringify(q.frame).slice(0, 160)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
