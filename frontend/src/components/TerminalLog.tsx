import { useEffect, useRef, useState } from "react";
import type { LogLine } from "../types";
import { fmtTime } from "./Tag";

const LEVELS = ["all", "info", "warn", "error"] as const;
const WARN_LEVELS = new Set(["warn", "warning"]);

export function TerminalLog({ lines }: { lines: LogLine[] }) {
  const [filter, setFilter] = useState<(typeof LEVELS)[number]>("all");
  const boxRef = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true);

  const visible = lines.filter((l) => filter === "all" || l.level === filter
    || (filter === "warn" && WARN_LEVELS.has(l.level)));

  useEffect(() => {
    if (stick && boxRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight;
    }
  }, [visible.length, stick]);

  const onScroll = () => {
    const el = boxRef.current;
    if (!el) return;
    setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
  };

  return (
    <div>
      <div className="log-tools">
        {LEVELS.map((level) => (
          <button key={level} className={filter === level ? "on" : ""}
            onClick={() => setFilter(level)}>
            {level.toUpperCase()}
          </button>
        ))}
        <span className="muted" style={{ marginLeft: "auto", fontSize: 11 }}>
          {visible.length} lines
        </span>
      </div>
      <div className="terminal" ref={boxRef} onScroll={onScroll}>
        {visible.map((line, i) => (
          <div key={i} className={`line ${line.level}`}>
            <span className="t">{fmtTime(line.ts)}</span>
            {line.message}
          </div>
        ))}
        {visible.length === 0 && <div className="line muted">-- no log lines --</div>}
      </div>
    </div>
  );
}
