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
  attempts?: number;
  verdict?: string;
  finished_at?: number;
};

export type SessionInfo = {
  run_id: string;
  scheme: string;
  mode: string;
  run_status: string;
  stop_requested: boolean;
  fatal_reason: string | null;
  queue: string[];
  current: CurrentAttempt;
  results: Record<string, CaseRunResult>;
} | null;

export type Snapshot = {
  devices: Record<string, DeviceInfo>;
  session: SessionInfo;
};

export type SchemeCase = { case_id: string; title: string };

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

export type SchemeMode = {
  mode: string;
  case_count: number;
  cases: SchemeCase[];       // all leaf cases (standalone + 子案例)
  standalone: SchemeCase[];  // leaf cases that belong to no group
  groups: GroupNode[];       // 总案例 groups (with 子案例 children)
};

export type SchemeNode = {
  scheme: string;
  display_name: string;
  description: string;
  case_count: number;
  entry_modes: SchemeMode[];
};

export type LibraryIssue = { path: string; severity: string; message: string };

export type PosParam = {
  tag: string;
  name?: string;
  value?: string;
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

export type SchemeTemplate = {
  card_template: Record<string, unknown> | null;
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
  scheme: string;
  entry_mode: string;
  profile: ProfileSummary;
  has_keys: boolean;
  preconditions: string[];
  test_plan: { step: number; action: string; expect: string }[];
  timeout_seconds: number;
  retry_on_fail: number;
  operator_instructions: OperatorInstructions;
  pos_params: PosParam[];
  expected_results: MatchRule[];
};

export type HistorySummary = {
  run_id: string;
  kind: string;
  scheme: string;
  mode: string;
  status: string;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  abort_reason: string | null;
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
    attempts: number;
    note?: string;
    verdict?: { status: string; rules: { path: string; op: string; expect: unknown; severity: string; ok: boolean; actual: unknown; message: string }[] };
    raw_result?: Record<string, unknown>;
  }>;
  quarantine: { at: number; reason: string; frame: Record<string, unknown> }[];
};

export type LogLine = { level: string; message: string; ts: number };

export type WsEvent = { seq: number; event: string; data: unknown };
