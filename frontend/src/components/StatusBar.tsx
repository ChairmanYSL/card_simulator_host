import type { DeviceInfo, SessionInfo } from "../types";
import { Tag } from "./Tag";

const NAMES: Record<string, string> = {
  android: "Android Card Simulator",
  pos: "POS Transaction App",
};

export function DeviceLight({ type, info }: { type: string; info: DeviceInfo | undefined }) {
  const cls = !info || !info.connected ? "device" : info.warn ? "device warn" : "device on";
  const id = info?.device_id ? ` :: ${info.device_id}` : "";
  return (
    <div className={cls} title={info?.app_version ?? ""}>
      <span className="lamp" />
      <span>
        <span className="name">{NAMES[type] ?? type}</span>
        <span className="id">{id}</span>
      </span>
    </div>
  );
}

export function StatusBar({
  devices,
  session,
  wsConnected,
}: {
  devices: Record<string, DeviceInfo>;
  session: SessionInfo;
  wsConnected: boolean;
}) {
  return (
    <div className="statusbar">
      <DeviceLight type="android" info={devices.android} />
      <DeviceLight type="pos" info={devices.pos} />
      <span className={`ws-state ${wsConnected ? "on" : ""}`} style={{ marginTop: 0 }}>
        <span className="dot" />WS
      </span>
      {session ? (
        <span className="run-chip">
          RUN <span className="mono">{session.run_id}</span>{" "}
          <Tag status={session.run_status} />
        </span>
      ) : (
        <span className="run-chip muted">IDLE / NO RUN</span>
      )}
    </div>
  );
}
