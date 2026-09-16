import type { LibraryIssue, BrandNode } from "../types";

export function BrandsPage({
  brands,
  issues,
  onEnter,
}: {
  brands: BrandNode[];
  issues: LibraryIssue[];
  onEnter: (brand: string, mode: string) => void;
}) {
  return (
    <div>
      <h3 className="section-title">
        <b>01</b> / test library · 卡组入口
      </h3>
      {brands.length === 0 && (
        <div className="panel muted">library empty — check the --cases directory</div>
      )}
      <div className="card-grid">
        {brands.map((brand) => (
          <div key={brand.brand} className="brand-card"
            onClick={() => onEnter(brand.brand, brand.entry_modes[0]?.mode ?? "contactless")}>
            <div className="name">{brand.display_name.toUpperCase()}</div>
            <div className="desc">{brand.description || "no description"}</div>
            <div className="modes">
              <span className="chip">TOTAL {brand.case_count}</span>
              {brand.entry_modes.map((m) => (
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
