import type { Snapshot, WsEvent } from "./types";

type Handlers = {
  onSnapshot: (snapshot: Snapshot) => void;
  onEvent: (event: WsEvent) => void;
  onStatus: (connected: boolean) => void;
};

/** WebSocket with sequence-based reconnect: resubscribes with the last seq
 * we saw, so missed events are replayed by the server's ring buffer. */
export function connectWs(handlers: Handlers): () => void {
  let socket: WebSocket | null = null;
  let lastSeq = 0;
  let closedByUs = false;
  let retryDelay = 500;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    // T18: a pending retry must never resurrect the socket after cleanup
    // (React StrictMode double-mounts and page switches both call the disposer)
    if (closedByUs) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${location.host}/ws?since=${lastSeq}`);
    socket.onopen = () => {
      retryDelay = 500;
      handlers.onStatus(true);
    };
    socket.onmessage = (msg) => {
      const payload = JSON.parse(msg.data as string);
      if (payload.kind === "snapshot") {
        lastSeq = Math.max(lastSeq, payload.seq ?? 0);
        handlers.onSnapshot(payload.snapshot as Snapshot);
        return;
      }
      lastSeq = Math.max(lastSeq, payload.seq ?? 0);
      handlers.onEvent(payload as WsEvent);
    };
    socket.onclose = () => {
      handlers.onStatus(false);
      if (closedByUs) return;
      retryTimer = setTimeout(open, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 10000);
    };
    socket.onerror = () => socket?.close();
  };

  open();
  return () => {
    closedByUs = true;
    if (retryTimer !== null) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    socket?.close();
    socket = null;
  };
}
