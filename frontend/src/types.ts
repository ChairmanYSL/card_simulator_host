export type DeviceInfo = {
  connected: boolean;
  device_id: string | null;
  app_version: string | null;
  connected_at: number | null;
  warn: boolean;
};

export type CurrentAttempt = {
  case_id: string | null;
  attempt_id: string | null;
  attempt: number;
  state: string;
};

export type CaseRunResult = {
  status: string;
  attempt?: number;
  attempts?: number;
  verdict?: string;
  finished_at?: number;
  // v1.2: why a case failed (result_timeout / pos_not_connected /
  // assertion_failed / ...); present on FAIL and ERROR records
  failure_reason?: string | null;
  // protocol v2 (design section 11 Q8): the live OUT outcome, streamed in the
  // run-level summary only when the case record carried a raw_result
  ops_status?: string;       // APPROVED / DECLINED / ONLINE REQUEST / RFU(0000)
  ops_status_bits?: string;  // the 4 status bits as text, e.g. "0001"
  resp_code?: string;        // DataRecord 8A (or the derived code), e.g. "3030"
};

export type SessionInfo = {
  run_id: string;
  brand: string;
  mode: string;
  run_status: string;
  stop_requested: boolean;
  fatal_reason: string | null;
  failure_reason?: string | null;
  queue: string[];
  current: CurrentAttempt;
  results: Record<string, CaseRunResult>;
} | null;

export type Snapshot = {
  devices: Record<string, DeviceInfo>;
  session: SessionInfo;
};

export type BrandCase = { case_id: string; title: string };

export type GroupChild = { case_id: string; title: string };

export type GroupNode = {
  group_id: string;
  title: string;
  summary: string;
  purpose: string;
  test_conditions: string;
  test_plan: { step: number; action: string; expect: string }[];
  pass_criteria: string[];
  case_count: number;
  children: GroupChild[];
};

export type BrandMode = {
  mode: string;
  case_count: number;
  cases: BrandCase[];       // all leaf cases (standalone + 子案例)
  standalone: BrandCase[];  // leaf cases that belong to no group
  groups: GroupNode[];       // 总案例 groups (with 子案例 children)
};

export type BrandNode = {
  brand: string;
  display_name: string;
  description: string;
  case_count: number;
  entry_modes: BrandMode[];
};

export type LibraryIssue = { path: string; severity: string; message: string };

export type PosParam = {
  tag: string;
  name?: string;
  value?: string;
};

//: v2 CTEL2 ACT transaction data item (docs/protocol.md §8.1): `id` is an EMV
//: tag (e.g. 9F02) or a proprietary id, `value` is an UPPERCASE hex string,
//: `""` = zero length, `null`/absent = the tag is not sent.  Only `act` reaches
//: the POS (backend/emv_host/runner/executor.py reads only transaction.act).
export type ActParam = {
  id: string;
  value?: string | null;
};

// ---------------------------------------------------------------------------
// v1.2 kernel parameters (POS AID/CAPK/TERM)
// ---------------------------------------------------------------------------

export type TlvField = {
  tag: string;   // 1-2 byte uppercase hex, e.g. "DF01"
  value: string; // even-length uppercase hex
};

export type AidKernelParam = {
  aid: string;             // 9F06 value, 5-8 bytes hex (no 9F06 tag)
  fields: TlvField[];      // remaining elements, in order; never 9F06
};

//: DF05 encoding (v1.2 C2).  ascii8 is the default and matches all current
//: POS assets; bcd4/bcd3 are packed BCD; raw passes expiry_raw verbatim.
export type ExpiryEncoding = "ascii8" | "bcd4" | "bcd3" | "raw";

export type CapkParam = {
  rid: string;             // 9F06 value: exactly 5 bytes hex
  key_index: string;       // 9F22 value: 1 byte hex
  expiry?: string;         // canonical YYYYMMDD (required unless encoding=raw)
  expiry_encoding?: ExpiryEncoding;
  expiry_raw?: string;     // hex verbatim; only with expiry_encoding="raw"
  hash_alg?: number;       // DF06, default 1
  key_alg?: number;        // DF07, default 1
  modulus: string;         // DF02 raw modulus hex (128/144/176/248 bytes)
  exponent?: string;       // DF04, default "03"
  checksum?: string;       // DF03 hex; omit to let the host compute SHA1
};

export type KernelParams = {
  aids: AidKernelParam[];
  caps: CapkParam[];
  term: string;            // single TERM TLV hex
};

export type ProfileSummary = {
  id: string;
  name: string;
  network: string;
  acBehavior: string;
  ppseMode: string;
  ppseSw: string;
  ppseData: string;
  noResponseCommands: string[];
  aids: string[];
  pan: string;
  applicationExpirationDate: string;
  hasKeys: boolean;
};

export type BrandTemplate = {
  card_template: Record<string, unknown> | null;
  //: legacy brand.json pos_params — v2 replaced it with a case's `act`
  //: (docs/protocol.md §8.1), so the add-case page only displays it read-only
  pos_params: PosParam[];
};

export type OperatorInstructions = {
  amount: string;
  currency: string;
  entry_mode: string;
  pin_hint: string;
  notes: string;
};

export type MatchRule = {
  path: string;
  op: string;
  expect: unknown;
  severity: string;
};

export type CaseDetail = {
  case_id: string;
  title: string;
  summary: string;
  brand: string;
  entry_mode: string;
  profile: ProfileSummary;
  has_keys: boolean;
  preconditions: string[];
  test_plan: { step: number; action: string; expect: string }[];
  timeout_seconds: number;
  retry_on_fail: number;
  operator_instructions: OperatorInstructions;
  //: v2: CTEL2 ACT transaction data — the only POS transaction parameters
  act: ActParam[];
  //: legacy v1.2 tag list, never sent to the POS any more
  pos_params: PosParam[];
  expected_results: MatchRule[];
  // v1.2: per-case POS kernel parameter override (null = use brand default)
  kernel_params?: KernelParams | null;
};

export type HistorySummary = {
  run_id: string;
  kind: string;
  brand: string;
  mode: string;
  status: string;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  abort_reason: string | null;
  // v1.2: the run index carries the case-level failure reason too
  failure_reason?: string | null;
  total: number;
  passed: number;
  failed: number;
  warned: number;
  aborted: number;
  skipped: number;
  inconclusive: number;
  errored: number;
};

export type RunDetail = {
  run: Record<string, unknown> & HistorySummary;
  results: Record<string, {
    case_id: string;
    status: string;
    attempt?: number;
    attempts: number;
    failure_reason?: string | null;
    note?: string;
    verdict?: { status: string; rules: { path: string; op: string; expect: unknown; severity: string; ok: boolean; actual: unknown; message: string }[] };
    raw_result?: CaseRawResult;
  }>;
  quarantine: { at: number; reason: string; frame: Record<string, unknown> }[];
  //: protocol v2: per-case APDU diagnostic trace, capped server-side (last 500)
  trace?: Record<string, TraceEntry[]>;
};

export type LogLine = { level: string; message: string; ts: number };

export type WsEvent = { seq: number; event: string; data: unknown };

// ---------------------------------------------------------------------------
// protocol v2: CTEL2 OUT (OPS + DataRecord) and the APDU diagnostic trace
// ---------------------------------------------------------------------------

//: OPS = Outcome Parameter Set (DF8129, 8 bytes), decoded by the host
//: (backend/emv_host/judge/outcome.py).  An all-zero OPS decodes to
//: status "RFU(0000)" / outcome "rfu(0000)" -> case FAIL out_no_outcome.
export type OutOps = {
  raw: string;
  status: string;       // APPROVED / DECLINED / ONLINE REQUEST / END APPLICATION / RFU(0000) / ...
  status_bits: string;  // the 4 status bits as text, e.g. "0001" (APPROVED) / "0000"
  outcome: string;      // approved / declined / rfu(0000) / ...
  start?: string;
  cvm?: string;
  data_record_present?: number;
  discretionary_data_present?: number;
};

//: the judgeable result persisted inside results/<case_id>.json (raw_result)
export type CaseRawResult = {
  outcome?: string;
  online?: string;
  resp_code?: string;   // "" when the kernel produced no outcome (v2 D2)
  out?: {
    ops?: OutOps;
    datarecord?: Record<string, string>;
    discdata?: Record<string, string>;
    uird?: string | null;
    kernelid?: string | null;
  };
};

//: one runs/<run_id>/trace/<case_id>.jsonl entry (APDU diagnostic channel,
//: log{level:"trace"}); never judged (docs/protocol.md section 6.4)
export type TraceMessage =
  | string
  | {
      event?: string;      // "apdu" | "field"
      direction?: string;  // "COMMAND" | "RESPONSE"
      seq?: number;
      cla?: number; ins?: number; p1?: number; p2?: number;
      data_hex?: string;
      sw?: string;
      elapsed_ms?: number;
      no_response?: boolean;
      tag?: string;
      value?: string;
      [k: string]: unknown;
    };

export type TraceEntry = {
  at: number;
  attempt_id?: string;
  message: TraceMessage;
};
