import type { LibraryIssue, SchemeNode } from "../types";

export function SchemesPage({
  schemes,
  issues,
  onEnter,
}: {
  schemes: SchemeNode[];
  issues: LibraryIssue[];
  onEnter: (scheme: string, mode: string) => void;
}) {
  return (
    <div>
      <h3 className="section-title">
        <b>01</b> / test library · 卡组入口
      </h3>
      {schemes.length === 0 && (
        <div className="panel muted">library empty — check the --cases directory</div>
      )}
      <div className="card-grid">
        {schemes.map((scheme) => (
          <div key={scheme.scheme} className="scheme-card"
            onClick={() => onEnter(scheme.scheme, scheme.entry_modes[0]?.mode ?? "contactless")}>
            <div className="name">{scheme.display_name.toUpperCase()}</div>
            <div className="desc">{scheme.description || "no description"}</div>
            <div className="modes">
              <span className="chip">TOTAL {scheme.case_count}</span>
              {scheme.entry_modes.map((m) => (
                <span className="chip" key={m.mode}>
                  {m.mode.toUpperCase()} {m.case_count}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
      {issues.length > 0 && (
        <div className="panel gap">
          <div className="section-title">library issues</div>
          {issues.map((issue, i) => (
            <div key={i} className="rule-line"
              style={{ color: issue.severity === "error" ? "var(--red)" : "var(--amber)" }}>
              [{issue.severity.toUpperCase()}] {issue.path} — {issue.message}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
