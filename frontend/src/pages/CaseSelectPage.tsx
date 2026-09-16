import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { CaseDetail, GroupNode, BrandCase, BrandNode } from "../types";

type RunOptions = {
  retry_count: number;
};

type DetailState =
  | { kind: "case"; detail: CaseDetail }
  | { kind: "group"; group: GroupNode };

export function CaseSelectPage({
  brandNode,
  defaultMode,
  onBack,
  onStart,
}: {
  brandNode: BrandNode;
  defaultMode: string;
  onBack: () => void;
  onStart: (kind: string, caseIds: string[], options: RunOptions,
            groupId?: string) => void;
}) {
  const [mode, setMode] = useState(defaultMode);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<DetailState | null>(null);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  // v1.2: on_failure was removed — the host stops the run on the first FAIL, so
  // offering "失败继续" would be a lie.
  const [options, setOptions] = useState<RunOptions>({
    retry_count: 1,
  });
  const [caseError, setCaseError] = useState<string | null>(null);

  const modeNode = brandNode.entry_modes.find((m) => m.mode === mode);
  const caseIds = useMemo(() => (modeNode?.cases ?? []).map((c) => c.case_id), [modeNode]);

  useEffect(() => {
    setSelected(new Set());
    setDetail(null);
    setCaseError(null);
  }, [mode]);

  const toggle = (caseId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(caseId)) next.delete(caseId);
      else next.add(caseId);
      return next;
    });
  };

  const allSelected = caseIds.length > 0 && caseIds.every((c) => selected.has(c));

  const openCaseDetail = async (caseId: string) => {
    setCaseError(null);
    try {
      setDetail({ kind: "case", detail: await api.caseDetail(brandNode.brand, mode, caseId) });
    } catch (e) {
      setCaseError(e instanceof Error ? e.message : String(e));
      setDetail(null);
    }
  };

  const openGroupDetail = (group: GroupNode) => {
    setDetail({ kind: "group", group });
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(group.group_id)) next.delete(group.group_id);
      else next.add(group.group_id);
      return next;
    });
  };

  const runGroup = (group: GroupNode) => {
    if (group.children.length === 0) return;
    // v1.2 T17: let the backend resolve the group's 子案例 (kind=group); the
    // server then also reports the group as a unit and keeps the queue stable.
    onStart("group", group.children.map((c) => c.case_id), options,
            group.group_id);
  };

  const renderLeafRow = (c: BrandCase) => (
    <tr key={c.case_id} className={`case-row ${selected.has(c.case_id) ? "selected" : ""}`}>
      <td>
        <input type="checkbox" style={{ accentColor: "var(--accent)" }}
          checked={selected.has(c.case_id)} onChange={() => toggle(c.case_id)} />
      </td>
      <td className="mono clickable" onClick={() => openCaseDetail(c.case_id)}>{c.case_id}</td>
      <td className="clickable" onClick={() => openCaseDetail(c.case_id)}>{c.title}</td>
    </tr>
  );

  return (
    <div>
      <div className="toolbar">
        <button className="btn" onClick={onBack}>← 返回卡组</button>
        <h3 className="section-title" style={{ margin: 0 }}>
          <b>01 ▸</b> {brandNode.display_name.toUpperCase()} / 选择用例
        </h3>
      </div>

      <div className="toolbar">
        {brandNode.entry_modes.map((m) => (
          <button key={m.mode}
            className={`btn ${m.mode === mode ? "primary" : ""}`}
            onClick={() => setMode(m.mode)}>
            {m.mode.toUpperCase()} ({m.case_count})
          </button>
        ))}
        <span className="muted" style={{ marginLeft: "auto" }}>
          策略 失败即停（v1.2 固定）
          重试
          <input type="number" min={0} max={5} value={options.retry_count}
            style={{ width: 52 }}
            onChange={(e) => setOptions({ ...options, retry_count: Number(e.target.value) || 0 })} />
        </span>
      </div>

      <div className="toolbar">
        <button className="btn primary"
          onClick={() => onStart("full", caseIds, options)}>
          ▶ 整轮测试（全部 {caseIds.length} 个）
        </button>
        <button className="btn"
          disabled={selected.size === 0}
          onClick={() => onStart("selected", [...selected], options)}>
          ▶ 测试选中（{selected.size}）
        </button>
        <button className="btn"
          onClick={() => onStart("resume", [], options)}>
          ⟲ 断点续跑（未通过用例）
        </button>
        <label style={{ color: "var(--dim)", fontSize: 15 }}>
          <input type="checkbox" style={{ accentColor: "var(--accent)", marginRight: 6 }}
            checked={allSelected}
            onChange={() => setSelected(allSelected ? new Set() : new Set(caseIds))} />
          全选
        </label>
      </div>

      {caseError && (
        <div className="banner error" role="alert">{caseError}</div>
      )}

      <div className="detail-grid">
        <div className="panel">
          {modeNode && modeNode.groups.length > 0 && (
            <>
              <div className="section-title">总案例分组（点击查看说明，子案例可勾选执行）</div>
              <table className="grid">
                <tbody>
                  {modeNode.groups.map((group) => {
                    const open = expandedGroups.has(group.group_id);
                    return (
                      <FragmentRow key={group.group_id}
                        group={group} open={open}
                        selected={selected}
                        onToggle={toggle}
                        onHeaderClick={() => openGroupDetail(group)}
                        onRun={() => runGroup(group)}
                        onChildClick={openCaseDetail} />
                    );
                  })}
                </tbody>
              </table>
            </>
          )}

          {modeNode && modeNode.standalone.length > 0 && (
            <>
              <div className="section-title" style={{ marginTop: modeNode.groups.length ? 22 : 0 }}>
                独立案例（可直接执行）
              </div>
              <table className="grid">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}></th>
                    <th>case id</th>
                    <th>title</th>
                  </tr>
                </thead>
                <tbody>
                  {modeNode.standalone.map(renderLeafRow)}
                </tbody>
              </table>
            </>
          )}

          {(!modeNode || (modeNode.groups.length === 0 && modeNode.standalone.length === 0)) && (
            <div className="muted">该支付方式下暂无用例</div>
          )}
        </div>

        <div className="panel">
          {detail ? (
            detail.kind === "group" ? <GroupDetailPanel group={detail.group} onChildClick={openCaseDetail} />
              : <CaseDetailPanel detail={detail.detail} />
          ) : (
            <div className="muted">
              点击总案例查看其 test plan 说明；点击左侧用例（独立或子案例）查看
              操作指引与预期结果。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FragmentRow({
  group, open, selected, onToggle, onHeaderClick, onRun, onChildClick,
}: {
  group: GroupNode;
  open: boolean;
  selected: Set<string>;
  onToggle: (caseId: string) => void;
  onHeaderClick: () => void;
  onRun: () => void;
  onChildClick: (caseId: string) => void;
}) {
  return (
    <>
      <tr className="case-row" onClick={onHeaderClick}>
        <td className="group-expander">{open ? "▾" : "▸"}</td>
        <td className="mono">{group.group_id}</td>
        <td>
          {group.title}
          <span className="chip" style={{ marginLeft: 10 }}>总案例 · {group.case_count} 子案例</span>
        </td>
        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
          <button className="btn small" onClick={(e) => { e.stopPropagation(); onRun(); }}>
            ▶ 运行本组
          </button>
        </td>
      </tr>
      {open && group.children.map((c) => (
        <tr key={c.case_id}
          className={`case-row sub ${selected.has(c.case_id) ? "selected" : ""}`}>
          <td></td>
          <td>
            <input type="checkbox" style={{ accentColor: "var(--accent)" }}
              checked={selected.has(c.case_id)}
              onChange={(e) => { e.stopPropagation(); onToggle(c.case_id); }} />
            <span className="mono clickable" style={{ marginLeft: 8 }}
              onClick={() => onChildClick(c.case_id)}>{c.case_id}</span>
          </td>
          <td className="clickable" onClick={() => onChildClick(c.case_id)}>{c.title}</td>
          <td></td>
        </tr>
      ))}
    </>
  );
}

function GroupDetailPanel({ group, onChildClick }: {
  group: GroupNode;
  onChildClick: (caseId: string) => void;
}) {
  return (
    <div className="kv">
      <div className="panel-tab">总案例说明 · test plan</div>
      <div className="section-title" style={{ marginTop: 14 }}>{group.group_id}</div>
      <p className="v" style={{ fontSize: 20, fontWeight: 600 }}>{group.title}</p>
      {group.summary && <p className="muted">{group.summary}</p>}
      <p><span className="k">测试目的</span></p>
      <p className="v">{group.purpose || "—"}</p>
      <p><span className="k">测试条件</span></p>
      <p className="v">{group.test_conditions || "—"}</p>
      <div className="gap">
        <div className="section-title">test plan</div>
        {group.test_plan.map((s) => (
          <div key={s.step} className="rule-line">
            <span className="k">#{s.step}</span> {s.action}
            <span className="muted"> → {s.expect}</span>
          </div>
        ))}
      </div>
      {group.pass_criteria.length > 0 && (
        <div className="gap">
          <div className="section-title">通过标准</div>
          {group.pass_criteria.map((c, i) => (
            <div key={i} className="rule-line">• {c}</div>
          ))}
        </div>
      )}
      <div className="gap">
        <div className="section-title">子案例（点击查看详情）</div>
        {group.children.map((c) => (
          <div key={c.case_id} className="rule-line clickable"
            onClick={() => onChildClick(c.case_id)}>
            <span className="mono">{c.case_id}</span> — {c.title}
          </div>
        ))}
      </div>
    </div>
  );
}

function CaseDetailPanel({ detail }: { detail: CaseDetail }) {
  const p = detail.profile;
  return (
    <div className="kv">
      <div className="section-title">{detail.case_id}</div>
      <p className="v">{detail.title}</p>
      <p className="muted">{detail.summary}</p>
      <div className="gap">
        <div className="section-title">卡片档案（随案例内嵌）</div>
        <p><span className="k">profile</span> <span className="mono v">{p.id}</span>
          {p.name && <span className="muted"> · {p.name}</span>}</p>
        <p>
          <span className="k">acBehavior</span> <span className="mono v">{p.acBehavior || "default"}</span>
          <span className="k"> PPSE</span> <span className="mono v">{p.ppseMode || "auto"}</span>
          {p.ppseSw && <span className="mono v"> · SW {p.ppseSw}</span>}
          {p.hasKeys && <span className="chip">keys</span>}
        </p>
        {p.noResponseCommands.length > 0 && (
          <p><span className="k">静默指令</span>{" "}
            {p.noResponseCommands.map((c) => <span key={c} className="chip">{c}</span>)}
            <span className="muted">（不响应）</span>
          </p>
        )}
        {p.pan && (
          <p><span className="k">PAN</span> <span className="mono v">{p.pan}</span> ·
            <span className="k"> expiry</span> <span className="mono v">{p.applicationExpirationDate}</span></p>
        )}
        {p.aids.length > 0 && (
          <p><span className="k">AIDs</span>{" "}
            {p.aids.map((aid) => <span key={aid} className="mono v" style={{ marginRight: 8 }}>{aid}</span>)}
          </p>
        )}
      </div>
      {detail.pos_params.length > 0 && (
        <div className="gap">
          <div className="section-title">POS 交易参数（下发 POS）</div>
          {detail.pos_params.map((pp, i) => (
            <p key={i} className="rule-line">
              <span className="mono">{pp.tag}</span>
              {pp.name && <span className="k"> {pp.name}</span>}
              <span className="v"> = {pp.value}</span>
            </p>
          ))}
        </div>
      )}
      <p><span className="k">timeout</span> <span className="v">{detail.timeout_seconds}s</span> ·
        <span className="k"> retry</span> <span className="v">{detail.retry_on_fail}</span></p>
      <p><span className="k">operator</span>{" "}
        <span className="v">
          {detail.operator_instructions.amount} {detail.operator_instructions.currency} /
          {detail.operator_instructions.entry_mode} / pin:{detail.operator_instructions.pin_hint}
        </span>
      </p>
      <p className="muted">{detail.operator_instructions.notes}</p>
      <div className="gap">
        <div className="section-title">test plan</div>
        {detail.test_plan.map((s) => (
          <div key={s.step} className="rule-line">
            <span className="k">#{s.step}</span> {s.action}
            <span className="muted"> → {s.expect}</span>
          </div>
        ))}
      </div>
      <div className="gap">
        <div className="section-title">expected results</div>
        {detail.expected_results.map((r, i) => (
          <div key={i} className="rule-line">
            <span className="mono">{r.path}</span>{" "}
            <span className="k">{r.op}</span>{" "}
            <span className="v">{JSON.stringify(r.expect)}</span>{" "}
            <span className="muted">[{r.severity}]</span>
          </div>
        ))}
      </div>
    </div>
  );
}
