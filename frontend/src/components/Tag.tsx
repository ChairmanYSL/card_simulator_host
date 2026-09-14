export function Tag({ status }: { status: string }) {
  return <span className={`tag ${status}`}>{status}</span>;
}

export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function fmtDuration(secs: number | null | undefined): string {
  if (!secs) return "—";
  const s = Math.round(secs);
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
}
