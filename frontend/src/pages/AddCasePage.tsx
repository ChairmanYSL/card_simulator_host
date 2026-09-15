import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { PosParam, SchemeNode } from "../types";

type PlanRow = { action: string; expect: string };
type RuleRow = { path: string; op: string; expect: string; severity: string };
type AidRow = { aid: string; label: string };
type PosRow = { tag: string; name: string; value: string };
type NoRespRow = { command: string; times: string }; // times "" = 每次静默

type Kind = "case" | "group";

export type CaseDraft = {
  case_id: string;
  title: string;
  summary: string;
  scheme: string;
  // optional 所属总案例: when set the case is a 子案例 (written into the group dir)
  groupId: string;
  timeout_seconds: number;
  retry_on_fail: number;
  preconditions: string[];
  // card profile (cloned from the scheme card_template, bound to the case)
  profileName: string;
  network: string;
  pan: string;
  holder: string;
  expiry: string;
  track2: string;
  acBehavior: string;
  ppseMode: string;
  ppseSw: string;
  ppseData: string;
  noRespRows: NoRespRow[];
  aidRows: AidRow[];
  templateExtra: Record<string, unknown>;
  jsonProfileMode: boolean;
  jsonProfileText: string;
  keysJson: string;
  // POS transaction parameters (scheme template auto-added, tags editable)
  posParams: PosRow[];
  // v1.2: per-case POS kernel parameters (AID/CAPK/TERM) as a JSON document.
  // Deep validation is the server's job; this stays a textarea by design (no
  // AID/CAPK table UI in this iteration).
  kernelParamsJson: string;
  // v1.2.x: named POS config id resolved from <kernel-params-dir>/<scheme>/.
  kernelParamsRef: string;
  // operator guidance + machine expectations
  amount: string;
  currency: string;
  pin_hint: string;
  notes: string;
  exp_amount: string;
  exp_currency: string;
  cvm_required: string; // "" | "true" | "false"
  plan: PlanRow[];
  rules: RuleRow[];
};

/** 总案例: description + test plan ONLY — no profile / keys / transaction
 * params / expected results / timeout (mirrors GroupDef). */
export type GroupDraft = {
  group_id: string;
  title: string;
  summary: string;
  purpose: string;
  test_conditions: string;
  scheme: string;
  plan: PlanRow[];
  pass_criteria: string[];
};

type Errors = Record<string, string>;

const CASE_TABS = [
  { id: "basic", label: "基本信息" },
  { id: "card", label: "卡片档案" },
  { id: "transaction", label: "交易参数" },
  { id: "kernel", label: "内核参数" },
  { id: "plan", label: "测试计划" },
  { id: "rules", label: "预期结果" },
  { id: "json", label: "JSON 导入" },
] as const;
type CaseTabId = (typeof CASE_TABS)[number]["id"];

const GROUP_TABS = [
  { id: "basic", label: "总案例说明 · test plan" },
  { id: "json", label: "JSON 导入" },
] as const;
type GroupTabId = (typeof GROUP_TABS)[number]["id"];

const OPS = ["eq", "neq", "contains", "prefix", "regex", "any_of", "present", "absent", "range"];
const NO_EXPECT_OPS = new Set(["present", "absent"]);
const AC_BEHAVIORS = ["", "offline_approved", "offline_declined", "online_request"];
const PPSE_MODES = ["", "auto", "disabled", "empty", "custom"];
// v1.1 N12: command-name registry for noResponseCommands (silence behavior)
const NO_RESPONSE_COMMANDS = [
  "PPSE", "SELECT", "GPO", "READ_RECORD", "GET_DATA", "GENERATE_AC", "VERIFY",
];

// managed profile fields are edited through the form; templateExtra holds the
// rest (aip/afl/records/cvmList/...) untouched
const MANAGED_KEYS = new Set([
  "id", "name", "network", "pan", "cardholderName", "applicationExpirationDate",
  "track2EquivalentData", "acBehavior", "ppseMode", "ppseSw", "ppseData",
  "noResponseCommands", "aids",
]);

function stripManaged(template: Record<string, unknown> | null): Record<string, unknown> {
  if (!template) return {};
  const extra: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(template)) {
    if (!MANAGED_KEYS.has(key)) extra[key] = value;
  }
  return extra;
}

function templateToDraft(template: Record<string, unknown> | null): Pick<CaseDraft,
  "profileName" | "network" | "pan" | "holder" | "expiry" | "track2" |
  "acBehavior" | "ppseMode" | "ppseSw" | "ppseData" | "noRespRows" |
  "aidRows" | "templateExtra"> {
  const t = template ?? {};
  const aids = Array.isArray(t.aids) ? t.aids as { aid?: string; label?: string }[] : [];
  return {
    profileName: String(t.name ?? ""),
    network: String(t.network ?? ""),
    pan: String(t.pan ?? ""),
    holder: String(t.cardholderName ?? ""),
    expiry: String(t.applicationExpirationDate ?? "281231"),
    track2: String(t.track2EquivalentData ?? ""),
    acBehavior: String(t.acBehavior ?? ""),
    ppseMode: String(t.ppseMode ?? ""),
    ppseSw: String(t.ppseSw ?? ""),
    ppseData: String(t.ppseData ?? ""),
    noRespRows: noRespToRows(t.noResponseCommands),
    aidRows: aids.length > 0
      ? aids.map((a) => ({ aid: String(a.aid ?? ""), label: String(a.label ?? "") }))
      : [{ aid: "", label: "" }],
    templateExtra: stripManaged(template),
  };
}

function noRespToRows(value: unknown): NoRespRow[] {
  if (!Array.isArray(value)) return [];
  const rows: NoRespRow[] = [];
  for (const item of value) {
    if (typeof item === "string") {
      if (item.trim()) rows.push({ command: item.trim().toUpperCase(), times: "" });
    } else if (item && typeof item === "object") {
      const obj = item as { command?: unknown; times?: unknown };
      const command = typeof obj.command === "string" ? obj.command.trim().toUpperCase() : "";
      if (command) rows.push({
        command,
        times: typeof obj.times === "number" ? String(obj.times) : "",
      });
    }
  }
  return rows;
}

function posToRows(params: PosParam[] | null | undefined): PosRow[] {
  return (params ?? []).map((p) => ({
    tag: p.tag ?? "",
    name: p.name ?? "",
    value: p.value ?? "",
  }));
}

export function emptyDraft(scheme: string): CaseDraft {
  return {
    case_id: "",
    title: "",
    summary: "",
    scheme,
    groupId: "",
    timeout_seconds: 180,
    retry_on_fail: 0,
    preconditions: [""],
    ...templateToDraft(null),
    jsonProfileMode: false,
    jsonProfileText: "",
    keysJson: "",
    posParams: [],
    kernelParamsJson: "",
    kernelParamsRef: "",
    amount: "100.00",
    currency: "CNY",
    pin_hint: "none",
    notes: "",
    exp_amount: "",
    exp_currency: "",
    cvm_required: "",
    plan: [{ action: "", expect: "" }],
    rules: [{ path: "resp_code", op: "eq", expect: "00", severity: "pass" }],
  };
}

export function emptyGroupDraft(scheme: string): GroupDraft {
  return {
    group_id: "",
    title: "",
    summary: "",
    purpose: "",
    test_conditions: "",
    scheme,
    plan: [{ action: "", expect: "" }],
    pass_criteria: [""],
  };
}

function luhnOk(pan: string): boolean {
  const digits = pan.replace(/\D/g, "");
  if (digits.length < 12) return false;
  let total = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    total += d;
  }
  return total % 10 === 0;
}

function isHexEven(value: string): boolean {
  return /^(?:[0-9A-Fa-f]{2})+$/.test(value.trim());
}

function buildProfile(d: CaseDraft): unknown {
  if (d.jsonProfileMode) {
    return JSON.parse(d.jsonProfileText); // validation runs before submit
  }
  const profile: Record<string, unknown> = { ...d.templateExtra };
  profile.id = d.case_id.trim(); // bound: profile id == case id
  profile.name = d.profileName.trim();
  profile.network = d.network.trim();
  profile.pan = d.pan.trim();
  profile.cardholderName = d.holder.trim();
  profile.applicationExpirationDate = d.expiry.trim();
  profile.track2EquivalentData = d.track2.trim().toUpperCase();
  profile.acBehavior = d.acBehavior;
  profile.ppseMode = d.ppseMode;
  profile.ppseSw = d.ppseSw.trim().toUpperCase();
  profile.ppseData = d.ppseData.trim().toUpperCase();
  profile.noResponseCommands = d.noRespRows
    .filter((r) => r.command.trim())
    .map((r) => {
      const command = r.command.trim().toUpperCase();
      const times = r.times.trim();
      return times === "" ? command : { command, times: Number(times) };
    });
  profile.aids = d.aidRows
    .map((a) => ({ aid: a.aid.trim().toUpperCase(), label: a.label.trim(), priority: 0 }))
    .filter((a) => a.aid);
  return profile;
}

function buildKeys(d: CaseDraft): unknown {
  if (!d.keysJson.trim()) return null;
  return JSON.parse(d.keysJson); // validation runs before submit
}

export function buildPayload(d: CaseDraft): unknown {
  const payload: Record<string, unknown> = {
    case_id: d.case_id.trim(),
    title: d.title.trim(),
    summary: d.summary.trim(),
    scheme: d.scheme,
    entry_mode: "contactless",
    profile: buildProfile(d),
    keys: buildKeys(d),
    preconditions: d.preconditions.map((p) => p.trim()).filter(Boolean),
    transaction: {
      operator_instructions: {
        amount: d.amount.trim(),
        currency: d.currency.trim().toUpperCase(),
        entry_mode: "contactless",
        pin_hint: d.pin_hint,
        notes: d.notes.trim(),
      },
      expected_values: {
        amount: d.exp_amount.trim() || null,
        currency: d.exp_currency.trim() || null,
        entry_mode: "contactless",
        cvm_required: d.cvm_required === "" ? null : d.cvm_required === "true",
      },
      pos_params: d.posParams
        .filter((p) => p.tag.trim())
        .map((p) => ({
          tag: p.tag.trim().toUpperCase(),
          name: p.name.trim(),
          value: p.value.trim(),
        })),
    },
    test_plan: d.plan.map((row, i) => ({ step: i + 1, action: row.action.trim(), expect: row.expect.trim() })),
    timeout_seconds: d.timeout_seconds,
    retry_on_fail: d.retry_on_fail,
    expected_results: d.rules.map((r) => {
      const expect = NO_EXPECT_OPS.has(r.op)
        ? null
        : r.op === "any_of"
          ? r.expect.split(",").map((s) => s.trim()).filter(Boolean)
          : r.op === "range"
            ? r.expect.split(",").map((s) => s.trim()).filter(Boolean)
            : r.expect.trim();
      return { path: r.path.trim(), op: r.op, expect, severity: r.severity };
    }),
  };
  if (d.groupId.trim()) payload.group_id = d.groupId.trim();
  if (d.kernelParamsRef.trim())
    payload.kernel_params_ref = d.kernelParamsRef.trim();
  const kernel = buildKernelParams(d);
  if (kernel !== null) payload.kernel_params = kernel;
  return payload;
}

/** v1.2: parse the kernel-params textarea.  Empty = no case-level override. */
function buildKernelParams(d: CaseDraft): unknown | null {
  const text = d.kernelParamsJson.trim();
  if (!text) return null;
  return JSON.parse(text); // validation runs before submit
}

/** 总案例 payload: NO profile / keys / transaction / expected_results. */
export function buildGroupPayload(g: GroupDraft): unknown {
  return {
    group_id: g.group_id.trim(),
    title: g.title.trim(),
    summary: g.summary.trim(),
    purpose: g.purpose.trim(),
    test_conditions: g.test_conditions.trim(),
    scheme: g.scheme,
    entry_mode: "contactless",
    test_plan: g.plan.map((row, i) => ({ step: i + 1, action: row.action.trim(), expect: row.expect.trim() })),
    pass_criteria: g.pass_criteria.map((c) => c.trim()).filter(Boolean),
  };
}

function validate(d: CaseDraft): Errors {
  const e: Errors = {};
  if (!d.case_id.trim()) e.case_id = "案例号必填";
  else if (!/^[A-Za-z0-9_-]{1,64}$/.test(d.case_id.trim()))
    e.case_id = "仅限字母/数字/下划线/连字符，最长 64 位（同时作为文件名）";
  if (!d.title.trim()) e.title = "标题必填";

  // card profile quick checks (server does the full validation)
  const panDigits = d.pan.replace(/\D/g, "");
  if (!d.pan.trim()) e.pan = "PAN 必填（从卡组模板克隆，可修改）";
  else if (panDigits.length < 12) e.pan = "PAN 至少 12 位数字";
  else if (!luhnOk(panDigits)) e.pan = "PAN 未通过 Luhn 校验";
  if (d.expiry.trim() && !/^\d{6}$/.test(d.expiry.trim()))
    e.expiry = "有效期须为 YYMMDD 六位数字";
  if (d.track2.trim() && !isHexEven(d.track2)) e.track2 = "track2 须为偶数长度 hex";
  if (d.acBehavior && !AC_BEHAVIORS.includes(d.acBehavior)) e.acBehavior = "非法的 acBehavior";
  if (d.ppseMode && !PPSE_MODES.includes(d.ppseMode)) e.ppseMode = "非法的 ppseMode";
  if (d.ppseSw.trim() && !/^[0-9A-F]{4}$/.test(d.ppseSw.trim().toUpperCase()))
    e.ppseSw = "ppseSw 须为大写 4 位 hex";
  if (d.ppseData.trim() && !isHexEven(d.ppseData)) e.ppseData = "ppseData 须为偶数长度 hex";
  const seenNoResp = new Set<string>();
  d.noRespRows.forEach((r, i) => {
    const command = r.command.trim().toUpperCase();
    if (!command) return;
    if (!NO_RESPONSE_COMMANDS.includes(command)) {
      e[`noResp.${i}`] = `指令 ${command} 不在注册表（${NO_RESPONSE_COMMANDS.join("/")}）`;
      return;
    }
    if (seenNoResp.has(command)) {
      e[`noResp.${i}`] = `指令 ${command} 重复`;
      return;
    }
    seenNoResp.add(command);
    const times = r.times.trim();
    if (times !== "" && (!/^\d+$/.test(times) || Number(times) < 1)) {
      e[`noResp.${i}`] = "次数须为空（每次静默）或 ≥1 的整数（前 N 次静默）";
    }
  });
  d.aidRows.forEach((a, i) => {
    if (a.aid.trim() && !/^(?:[0-9A-Fa-f]{2})+$/.test(a.aid.trim()))
      e[`aid.${i}`] = "AID 须为偶数长度 hex";
  });
  if (d.jsonProfileMode) {
    try {
      const parsed = JSON.parse(d.jsonProfileText);
      if (!parsed || typeof parsed !== "object") throw new Error("not object");
    } catch {
      e.jsonProfile = "完整 profile JSON 不合法";
    }
  }
  if (d.keysJson.trim()) {
    try {
      const parsed = JSON.parse(d.keysJson);
      if (!parsed || typeof parsed !== "object") throw new Error("not object");
    } catch {
      e.keysJson = "keys JSON 不合法（留空表示无密钥）";
    }
  }
  // v1.2 kernel parameters: shape only.  The server validates hex lengths,
  // DF03 checksums, DF05 encodings and dedup keys (see validate_kernel_params);
  // duplicating that here would drift, so we only catch malformed JSON and a
  // wrong top-level shape and let the server give the authoritative message.
  if (d.kernelParamsJson.trim() && d.kernelParamsRef.trim()) {
    e.kernelParams = "kernel_params 与 kernel_params_ref 不能同时填写";
  }
  if (d.kernelParamsJson.trim()) {
    try {
      const parsed = JSON.parse(d.kernelParamsJson);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        throw new Error("not object");
      const obj = parsed as Record<string, unknown>;
      for (const key of ["aids", "caps", "drl", "exception_file"]) {
        const v = obj[key];
        if (v !== undefined && !Array.isArray(v))
          e.kernelParams = `kernel_params.${key} 须为数组`;
      }
      if (obj.term !== undefined && typeof obj.term !== "string")
        e.kernelParams = "kernel_params.term 须为字符串（hex）";
      if (obj.version !== undefined && typeof obj.version !== "string")
        e.kernelParams = "kernel_params.version 须为字符串";
      const empty =
        (!Array.isArray(obj.aids) || obj.aids.length === 0) &&
        (!Array.isArray(obj.caps) || obj.caps.length === 0) &&
        (!Array.isArray(obj.drl) || obj.drl.length === 0) &&
        (!Array.isArray(obj.exception_file) || obj.exception_file.length === 0) &&
        !(typeof obj.term === "string" && obj.term.trim());
      if (empty && !e.kernelParams)
        e.kernelParams =
          "kernel_params 为空：aids/caps/drl/exception_file/term 至少给一项，或清空此页以使用卡组默认";
    } catch {
      e.kernelParams = "内核参数 JSON 不合法（留空表示不下发，沿用卡组默认）";
    }
  }

  // POS transaction parameters: tag format + duplicates (server warn-level,
  // surfaced here as errors so the user fixes them before submit)
  const seenTags = new Set<string>();
  d.posParams.forEach((p, i) => {
    const tag = p.tag.trim().toUpperCase();
    if (!tag) return;
    if (!/^[0-9A-F]{2}(?:[0-9A-F]{2}){0,2}$/.test(tag)) {
      e[`pos.${i}.tag`] = "tag 须为 1-3 字节大写 hex（如 9F02）";
      return;
    }
    if (tag.length > 2 && (parseInt(tag.slice(0, 2), 16) & 0x1f) !== 0x1f) {
      e[`pos.${i}.tag`] = `多字节 tag 首字节低 5 位须为 0x1F：${tag}`;
      return;
    }
    if (seenTags.has(tag)) e[`pos.${i}.tag`] = `tag ${tag} 重复`;
    seenTags.add(tag);
  });

  if (!/^\d{1,8}(\.\d{1,2})?$/.test(d.amount.trim()))
    e.amount = "金额格式如 100.00（最多 8 位整数、2 位小数）";
  if (!/^[A-Z]{3}$/.test(d.currency.trim()))
    e.currency = "币种为 3 位大写字母，如 CNY";
  if (d.exp_amount.trim() && !/^\d{1,8}(\.\d{1,2})?$/.test(d.exp_amount.trim()))
    e.exp_amount = "金额格式如 100.00";
  if (d.exp_currency.trim() && !/^\d{3}$/.test(d.exp_currency.trim()))
    e.exp_currency = "ISO 数字币种码为 3 位数字，如 156";
  if (!Number.isInteger(d.timeout_seconds) || d.timeout_seconds < 10 || d.timeout_seconds > 3600)
    e.timeout = "超时范围 10–3600 秒";
  if (!Number.isInteger(d.retry_on_fail) || d.retry_on_fail < 0 || d.retry_on_fail > 5)
    e.retry = "重试次数 0–5";
  d.preconditions.forEach((p, i) => {
    if (p.trim().length > 200) e[`pre.${i}`] = "前置条件最长 200 字符";
  });
  if (d.plan.length === 0) e.plan = "至少 1 个测试步骤";
  d.plan.forEach((row, i) => {
    if (!row.action.trim()) e[`plan.${i}.action`] = "操作说明必填";
    if (!row.expect.trim()) e[`plan.${i}.expect`] = "预期结果必填";
  });
  if (d.rules.length === 0) e.rules = "至少 1 条断言规则";
  d.rules.forEach((r, i) => {
    if (!r.path.trim()) e[`rules.${i}.path`] = "字段路径必填，如 resp_code 或 tags.gen_ac.9F26";
    if (!NO_EXPECT_OPS.has(r.op) && !r.expect.trim()) e[`rules.${i}.expect`] = "期望值必填";
    if (r.op === "any_of" && r.expect.split(",").map((s) => s.trim()).filter(Boolean).length === 0)
      e[`rules.${i}.expect`] = "any_of 需用逗号分隔多个期望值";
    if (r.op === "range") {
      const parts = r.expect.split(",").map((s) => s.trim());
      if (parts.length !== 2 || parts.some((p) => p === "" || isNaN(Number(p))))
        e[`rules.${i}.expect`] = "range 需两个数字，用逗号分隔，如 0,1000";
    }
    if (r.op === "regex") {
      try {
        new RegExp(r.expect);
      } catch {
        e[`rules.${i}.expect`] = "正则表达式不合法";
      }
    }
  });
  return e;
}

function validateGroup(g: GroupDraft): Errors {
  const e: Errors = {};
  if (!g.group_id.trim()) e.group_id = "总案例号必填";
  else if (!/^[A-Za-z0-9_-]{1,64}$/.test(g.group_id.trim()))
    e.group_id = "仅限字母/数字/下划线/连字符，最长 64 位（同时作为目录名）";
  if (!g.title.trim()) e.title = "标题必填";
  if (g.plan.length === 0) e.plan = "至少 1 个测试步骤";
  g.plan.forEach((row, i) => {
    if (!row.action.trim()) e[`plan.${i}.action`] = "操作说明必填";
    if (!row.expect.trim()) e[`plan.${i}.expect`] = "预期结果必填";
  });
  g.pass_criteria.forEach((c, i) => {
    if (c.trim().length > 500) e[`criteria.${i}`] = "通过标准最长 500 字符";
  });
  return e;
}

const CASE_TAB_KEYS: Record<CaseTabId, (key: string) => boolean> = {
  basic: (k) => ["case_id", "title", "timeout", "retry"].includes(k) || k.startsWith("pre."),
  card: (k) => ["profileName", "network", "pan", "holder", "expiry", "track2",
    "acBehavior", "ppseMode", "ppseSw", "ppseData", "jsonProfile", "keysJson"]
    .includes(k) || k.startsWith("aid.") || k.startsWith("noResp."),
  transaction: (k) => ["amount", "currency", "exp_amount", "exp_currency"]
    .includes(k) || k.startsWith("pos."),
  kernel: (k) => k === "kernelParams" || k === "kernelParamsRef",
  plan: (k) => k === "plan" || k.startsWith("plan."),
  rules: (k) => k === "rules" || k.startsWith("rules."),
  json: () => false,
};

const GROUP_TAB_KEYS: Record<GroupTabId, (key: string) => boolean> = {
  basic: () => true,
  json: () => false,
};

function Field({
  label, error, children, hint,
}: { label: string; error?: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint && !error && <div className="hint">{hint}</div>}
      {error && <div className="err">{error}</div>}
    </div>
  );
}

function suggestSubId(groupId: string, childCount: number): string {
  return `${groupId}-${String(childCount + 1).padStart(2, "0")}`;
}

export function AddCasePage({
  schemes,
  onAdded,
  onGoto,
}: {
  schemes: SchemeNode[];
  onAdded: () => void;
  onGoto: (scheme: string, mode: string) => void;
}) {
  const [kind, setKind] = useState<Kind>("case");
  const [tab, setTab] = useState<string>("basic");
  const [draft, setDraft] = useState<CaseDraft>(() => emptyDraft(schemes[0]?.scheme ?? ""));
  const [gdraft, setGdraft] = useState<GroupDraft>(() => emptyGroupDraft(schemes[0]?.scheme ?? ""));
  const [errors, setErrors] = useState<Errors>({});
  const [gerrors, setGerrors] = useState<Errors>({});
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState("");

  const scheme = kind === "case" ? draft.scheme : gdraft.scheme;
  const schemeNode = schemes.find((s) => s.scheme === scheme);
  const groups = schemeNode?.entry_modes.find((m) => m.mode === "contactless")?.groups ?? [];

  const set = <K extends keyof CaseDraft>(key: K, value: CaseDraft[K]) =>
    setDraft((prev) => ({ ...prev, [key]: value }));
  const gset = <K extends keyof GroupDraft>(key: K, value: GroupDraft[K]) =>
    setGdraft((prev) => ({ ...prev, [key]: value }));

  const applyTemplate = (sch: string) => {
    setTemplateError(null);
    api.schemeTemplate(sch).then((res) => {
      setDraft((prev) => {
        if (prev.scheme !== sch) return prev; // user switched away meanwhile
        const card = templateToDraft(res.card_template);
        return { ...prev, ...card, posParams: posToRows(res.pos_params) };
      });
    }).catch((e) =>
      setTemplateError(e instanceof Error ? e.message : String(e)));
  };

  useEffect(() => {
    if (!draft.scheme) return;
    applyTemplate(draft.scheme);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.scheme]);

  const changeScheme = (sch: string) => {
    setDraft((prev) => ({ ...emptyDraft(sch), title: prev.title, summary: prev.summary,
      timeout_seconds: prev.timeout_seconds, retry_on_fail: prev.retry_on_fail }));
    setGdraft((prev) => ({ ...emptyGroupDraft(sch), title: prev.title, summary: prev.summary }));
    setErrors({});
    setGerrors({});
    setServerError(null);
    applyTemplate(sch);
  };

  const activePayload = () =>
    kind === "group" ? buildGroupPayload(gdraft) : buildPayload(draft);

  const tabErrors = useMemo(() => {
    const keys = Object.keys(kind === "case" ? errors : gerrors);
    const map: Record<string, number> = {};
    const tabs = kind === "case" ? CASE_TABS : GROUP_TABS;
    const keyMap = kind === "case" ? CASE_TAB_KEYS : GROUP_TAB_KEYS;
    for (const t of tabs) {
      const count = keys.filter((k) => (keyMap as Record<string, (k: string) => boolean>)[t.id](k)).length;
      if (count > 0) map[t.id] = count;
    }
    return map;
  }, [kind, errors, gerrors]);

  const syncJsonFromForm = () => {
    try {
      setJsonText(JSON.stringify(activePayload(), null, 2));
    } catch {
      setJsonText("// profile/keys JSON 不合法，无法生成预览");
    }
  };

  const reset = () => {
    const sch = scheme;
    setDraft(emptyDraft(sch));
    setGdraft(emptyGroupDraft(sch));
    setErrors({});
    setGerrors({});
    setServerError(null);
    setSuccess(null);
    setJsonMode(false);
    setJsonText("");
    setTab("basic");
    applyTemplate(sch);
  };

  const switchKind = (k: Kind) => {
    if (k === kind) return;
    setKind(k);
    setTab("basic");
    setErrors({});
    setGerrors({});
    setServerError(null);
    setSuccess(null);
    setJsonMode(false);
    setJsonText("");
    if (k === "group") {
      setGdraft((prev) =>
        prev.scheme === draft.scheme ? prev : { ...emptyGroupDraft(draft.scheme), title: prev.title });
    }
  };

  const onSubmit = async () => {
    setServerError(null);
    setSuccess(null);
    if (jsonMode) {
      let obj: unknown;
      try {
        obj = JSON.parse(jsonText);
      } catch (e) {
        setServerError(`JSON 解析失败：${e instanceof Error ? e.message : String(e)}`);
        return;
      }
      try {
        if (kind === "group") {
          const check = await api.validateGroup(obj);
          if (!check.ok) { setServerError(check.errors.join("；")); return; }
          const res = await api.addGroup(obj);
          finish(res.group_id);
        } else {
          const check = await api.validateCase(obj);
          if (!check.ok) { setServerError(check.errors.join("；")); return; }
          const res = await api.addCase(obj);
          finish(res.case_id);
        }
      } catch (e) {
        setServerError(e instanceof Error ? e.message : String(e));
      }
      return;
    }
    if (kind === "group") {
      const found = validateGroup(gdraft);
      setGerrors(found);
      if (Object.keys(found).length > 0) {
        setServerError(`总案例表单有 ${Object.keys(found).length} 处不合法，请修正后重试`);
        return;
      }
      setSubmitting(true);
      try {
        const res = await api.addGroup(buildGroupPayload(gdraft));
        finish(res.group_id);
      } catch (e) {
        setServerError(e instanceof Error ? e.message : String(e));
      } finally {
        setSubmitting(false);
      }
      return;
    }
    const found = validate(draft);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      const errKeys = Object.keys(found);
      const firstTab = CASE_TABS.find((t) => errKeys.some((k) => CASE_TAB_KEYS[t.id](k))) ?? CASE_TABS[0];
      setTab(firstTab.id);
      setServerError(`表单有 ${errKeys.length} 处输入不合法，已定位到「${firstTab.label}」选项卡，请修正后重试`);
      return;
    }
    setSubmitting(true);
    try {
      const res = await api.addCase(buildPayload(draft));
      finish(res.case_id);
    } catch (e) {
      setServerError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const finish = (id: string) => {
    setSuccess(id);
    onAdded();
  };

  const input = (key: keyof CaseDraft, value: string | number | string[]) => set(key, value as never);
  const ginput = (key: keyof GroupDraft, value: string | string[]) => gset(key, value as never);

  const enterJsonProfile = () => {
    const profile = buildProfile(draft);
    set("jsonProfileMode", true);
    set("jsonProfileText", JSON.stringify(profile, null, 2));
  };

  const exitJsonProfile = () => {
    // commit the JSON text back into the quick fields when valid
    try {
      const parsed = JSON.parse(draft.jsonProfileText) as Record<string, unknown>;
      const mapped = templateToDraft({ ...parsed, name: parsed.name ?? "", network: parsed.network ?? "" });
      setDraft((prev) => ({
        ...prev,
        ...mapped,
        jsonProfileMode: false,
        templateExtra: { ...prev.templateExtra, ...stripManaged(parsed) },
      }));
    } catch {
      set("jsonProfileMode", false); // invalid JSON: drop it (server would reject anyway)
    }
  };

  const onGroupSelect = (gid: string) => {
    setDraft((prev) => {
      const prevGroup = prev.groupId;
      const prevGroupObj = groups.find((g) => g.group_id === prevGroup);
      const nextGroupObj = groups.find((g) => g.group_id === gid);
      const prevSuggested = prevGroupObj ? suggestSubId(prevGroupObj.group_id, prevGroupObj.children.length) : "";
      const nextSuggested = nextGroupObj ? suggestSubId(nextGroupObj.group_id, nextGroupObj.children.length) : "";
      let caseId = prev.case_id;
      if (!caseId || (prevGroup && caseId === prevSuggested)) caseId = nextSuggested;
      return { ...prev, groupId: gid, case_id: caseId };
    });
  };

  const tabs = kind === "case" ? CASE_TABS : GROUP_TABS;
  const activeTabErrors = tabErrors;

  return (
    <div>
      <div className="toolbar">
        <h3 className="section-title" style={{ margin: 0 }}>
          <b>02</b> / 添加测试案例 · ADD CASE
        </h3>
      </div>

      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8 }}>
        <button className={`btn ${kind === "case" ? "primary" : ""}`}
          onClick={() => switchKind("case")}>
          可执行案例（独立 / 子案例）
        </button>
        <button className={`btn ${kind === "group" ? "primary" : ""}`}
          onClick={() => switchKind("group")}>
          总案例（含子案例 · 仅 test plan）
        </button>
        <span className="muted" style={{ marginLeft: "auto" }}>
          总案例只配置说明与 test plan，不配置 profile / 交易参数 / 预期结果
        </span>
      </div>

      {serverError && (
        <div className="banner error" role="alert">
          <b>✕ 无法添加：</b>{serverError}
        </div>
      )}
      {templateError && kind === "case" && (
        <div className="banner error" role="alert">
          <b>✕ 模板加载失败：</b>{templateError}
        </div>
      )}
      {success && (
        <div className="banner ok" role="status">
          <b>✓ 已添加 {success}</b>
          {kind === "group"
            ? <button className="btn" onClick={() => {
              const gid = success;
              const grp = groups.find((g) => g.group_id === gid);
              setKind("case");
              setTab("basic");
              setDraft((prev) => ({
                ...prev,
                groupId: gid,
                case_id: grp
                  ? suggestSubId(grp.group_id, grp.children.length)
                  : `${gid}-01`,
              }));
              setSuccess(null);
            }}>＋ 继续添加该组的子案例</button>
            : <button className="btn" onClick={() => onGoto(draft.scheme, "contactless")}>查看用例</button>}
          <button className="btn" onClick={reset}>继续添加</button>
        </div>
      )}

      <div className="panel">
        <div className="tabs" role="tablist">
          {tabs.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id}
              className={`tab-btn ${tab === t.id ? "active" : ""}`}
              onClick={() => setTab(t.id)}>
              {t.label}
              {activeTabErrors[t.id] && <span className="tab-badge">{activeTabErrors[t.id]}</span>}
            </button>
          ))}
        </div>

        {/* ======================= 可执行案例 ======================= */}
        {kind === "case" && tab === "basic" && (
          <div className="form-grid">
            <Field label="案例号 case_id *" error={errors.case_id}
              hint={draft.groupId
                ? `子案例：将保存到 ${draft.groupId}/ 目录`
                : "独立案例；档案 id 自动 = 案例号"}>
              <input type="text" value={draft.case_id} placeholder="如 VISA-CL-101 或 OPBD-001-01"
                onChange={(e) => input("case_id", e.target.value)} />
            </Field>
            <Field label="所属总案例（可选）" hint="选组则为本组的子案例；不选则为独立案例">
              <select value={draft.groupId} onChange={(e) => onGroupSelect(e.target.value)}>
                <option value="">（无 · 独立案例）</option>
                {groups.map((g) => (
                  <option key={g.group_id} value={g.group_id}>
                    {g.group_id} · {g.title}（{g.children.length} 子案例）
                  </option>
                ))}
              </select>
            </Field>
            <Field label="标题 *" error={errors.title}>
              <input type="text" value={draft.title} placeholder="如 Visa 非接离线批准（TC）"
                onChange={(e) => input("title", e.target.value)} />
            </Field>
            <Field label="卡组 *">
              <select value={draft.scheme} onChange={(e) => changeScheme(e.target.value)}>
                {schemes.map((s) => (
                  <option key={s.scheme} value={s.scheme}>{s.display_name}</option>
                ))}
              </select>
            </Field>
            <Field label="摘要 summary">
              <textarea rows={3} value={draft.summary}
                placeholder="可选，一句话描述该用例"
                onChange={(e) => input("summary", e.target.value)} />
            </Field>
            <div className="field-row">
              <Field label="超时（秒）*" error={errors.timeout}>
                <input type="number" min={10} max={3600} value={draft.timeout_seconds}
                  onChange={(e) => input("timeout_seconds", Number(e.target.value) || 0)} />
              </Field>
              <Field label="失败重试次数" error={errors.retry}>
                <input type="number" min={0} max={5} value={draft.retry_on_fail}
                  onChange={(e) => input("retry_on_fail", Number(e.target.value) || 0)} />
              </Field>
            </div>
            <div className="field full">
              <label>前置条件 preconditions</label>
              {draft.preconditions.map((p, i) => (
                <div key={i} className="row-line">
                  <input type="text" value={p} placeholder={`前置条件 ${i + 1}`}
                    onChange={(e) => {
                      const list = [...draft.preconditions];
                      list[i] = e.target.value;
                      input("preconditions", list);
                    }} />
                  <button className="btn small" onClick={() =>
                    set("preconditions", draft.preconditions.filter((_, j) => j !== i))}>移除</button>
                </div>
              ))}
              <button className="btn small" onClick={() =>
                set("preconditions", [...draft.preconditions, ""])}>＋ 添加前置条件</button>
            </div>
          </div>
        )}

        {kind === "case" && tab === "card" && (
          <div>
            <p className="hint" style={{ marginTop: 0 }}>
              档案从卡组默认模板克隆并<b>随案例内嵌绑定</b>（profile.id 自动 = 案例号）。
              这里只放<b>卡片侧</b>配置（下发安卓）：AIP(82)、CVM(8E)、PPSE、PAN 等随
              card_config 下发；交易参数（tag）不在此页，在「交易参数」页签且只下发 POS。
            </p>
            <div className="form-grid">
              <Field label="档案显示名" error={errors.profileName}>
                <input type="text" value={draft.profileName} placeholder="如 Visa 离线批准卡"
                  onChange={(e) => input("profileName", e.target.value)} />
              </Field>
              <Field label="network" error={errors.network}>
                <input type="text" value={draft.network} placeholder="如 VISA"
                  onChange={(e) => input("network", e.target.value)} />
              </Field>
              <Field label="PAN *" error={errors.pan}
                hint="Luhn 校验；改 PAN 后请在完整 JSON 中同步 track2（或保留模板 PAN）">
                <input type="text" value={draft.pan} onChange={(e) => input("pan", e.target.value)} />
              </Field>
              <Field label="有效期 YYMMDD（tag 5F24）" error={errors.expiry}>
                <input type="text" value={draft.expiry} maxLength={6}
                  onChange={(e) => input("expiry", e.target.value)} />
              </Field>
              <Field label="持卡人（tag 5F20）" error={errors.holder}>
                <input type="text" value={draft.holder}
                  onChange={(e) => input("holder", e.target.value)} />
              </Field>
              <Field label="track2（tag 57，hex）" error={errors.track2}
                hint="hex；改 PAN 后需同步，如 &lt;PAN&gt;=2812201 的 ascii hex">
                <input type="text" value={draft.track2} className="mono"
                  onChange={(e) => input("track2", e.target.value)} />
              </Field>
            </div>

            <div className="field full" style={{ marginTop: 14 }}>
              <label>AID 列表（PPSE/SELECT）</label>
              {draft.aidRows.map((a, i) => (
                <div key={i} className="row-line">
                  <input type="text" value={a.aid} placeholder="如 A0000000031010"
                    className="mono"
                    onChange={(e) => {
                      const list = [...draft.aidRows];
                      list[i] = { ...list[i], aid: e.target.value };
                      set("aidRows", list);
                    }} />
                  <input type="text" value={a.label} placeholder="label"
                    onChange={(e) => {
                      const list = [...draft.aidRows];
                      list[i] = { ...list[i], label: e.target.value };
                      set("aidRows", list);
                    }} />
                  <button className="btn small" disabled={draft.aidRows.length <= 1}
                    onClick={() => set("aidRows", draft.aidRows.filter((_, j) => j !== i))}>移除</button>
                  {errors[`aid.${i}`] && <span className="err">{errors[`aid.${i}`]}</span>}
                </div>
              ))}
              <button className="btn small" onClick={() =>
                set("aidRows", [...draft.aidRows, { aid: "", label: "" }])}>＋ 添加 AID</button>
            </div>

            <div className="form-grid" style={{ marginTop: 14 }}>
              <Field label="acBehavior（首 GAC 行为）" error={errors.acBehavior}
                hint="无：不设置首 GAC 行为（部分卡组/卡片不支持 GAC 决策，按终端 P1 应答）">
                <select value={draft.acBehavior} onChange={(e) => input("acBehavior", e.target.value)}>
                  {AC_BEHAVIORS.map((v) => (
                    <option key={v} value={v}>
                      {v === "" ? "无（不设置，按终端 P1 应答）" : v}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="PPSE mode" error={errors.ppseMode}>
                <select value={draft.ppseMode} onChange={(e) => input("ppseMode", e.target.value)}>
                  {PPSE_MODES.map((v) => (
                    <option key={v} value={v}>{v || "auto（缺省）"}</option>
                  ))}
                </select>
              </Field>
              <Field label="ppseSw（大写 4 位 hex SW 覆盖）" error={errors.ppseSw}>
                <input type="text" value={draft.ppseSw} maxLength={4} placeholder="如 6A82"
                  onChange={(e) => input("ppseSw", e.target.value.toUpperCase())} />
              </Field>
              <Field label="ppseData（custom 完整 FCI hex）" error={errors.ppseData}
                hint="偶长 hex；加载期做 TLV 长度一致性校验">
                <textarea rows={3} value={draft.ppseData} spellCheck={false} className="mono"
                  onChange={(e) => input("ppseData", e.target.value)} />
              </Field>
            </div>

            <div className="field full" style={{ marginTop: 16 }}>
              <label>静默指令（不响应，N12）</label>
              <p className="hint" style={{ marginTop: 0 }}>
                卡片收到该指令时<b>不发任何响应 APDU</b>（区别于 ppseMode=disabled 的回错误 SW）；
                次数留空 = 每次都静默；填 N ≥1 = 仅前 N 次静默，之后正常应答。
              </p>
              {draft.noRespRows.length === 0 && (
                <p className="hint">默认不配置：卡片对所有指令正常响应。</p>
              )}
              {draft.noRespRows.map((r, i) => (
                <div key={i} className="row-line">
                  <select value={r.command}
                    onChange={(e) => {
                      const list = [...draft.noRespRows];
                      list[i] = { ...list[i], command: e.target.value };
                      set("noRespRows", list);
                    }}>
                    <option value="">— 选择指令 —</option>
                    {NO_RESPONSE_COMMANDS.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                  <input type="text" value={r.times} placeholder="次数（空=每次）"
                    style={{ width: 140 }}
                    onChange={(e) => {
                      const list = [...draft.noRespRows];
                      list[i] = { ...list[i], times: e.target.value };
                      set("noRespRows", list);
                    }} />
                  <button className="btn small"
                    onClick={() => set("noRespRows", draft.noRespRows.filter((_, j) => j !== i))}>移除</button>
                  {errors[`noResp.${i}`] && <span className="err">{errors[`noResp.${i}`]}</span>}
                </div>
              ))}
              <button className="btn small" onClick={() =>
                set("noRespRows", [...draft.noRespRows, { command: "", times: "" }])}>
                ＋ 添加静默指令
              </button>
            </div>

            <div className="gap" style={{ marginTop: 16 }}>
              <div className="section-title">完整 profile JSON（兜底；记录/afl/cvmList 等在此编辑）</div>
              {!draft.jsonProfileMode ? (
                <button className="btn small" onClick={enterJsonProfile}>✎ 打开完整 JSON 编辑</button>
              ) : (
                <>
                  <textarea rows={14} spellCheck={false} className="json-area mono"
                    value={draft.jsonProfileText}
                    onChange={(e) => set("jsonProfileText", e.target.value)} />
                  {errors.jsonProfile && <div className="err">{errors.jsonProfile}</div>}
                  <div className="row-line" style={{ marginTop: 8 }}>
                    <button className="btn small primary" onClick={exitJsonProfile}>
                      ✓ 应用 JSON（回到快捷编辑）
                    </button>
                    <button className="btn small" onClick={enterJsonProfile}>⟲ 重新生成</button>
                  </div>
                </>
              )}
            </div>

            <div className="gap">
              <div className="section-title">keys（可选，随案例内嵌）</div>
              <p className="hint">留空 = 无密钥（假密文）；填写 KeysBundle JSON 后启用真实密钥。</p>
              <textarea rows={5} spellCheck={false} className="json-area mono"
                placeholder={'{"symmetric_keys":[{"id":"mk1","key_data":"<base64>"}]}'}
                value={draft.keysJson}
                onChange={(e) => set("keysJson", e.target.value)} />
              {errors.keysJson && <div className="err">{errors.keysJson}</div>}
            </div>
          </div>
        )}

        {kind === "case" && tab === "transaction" && (
          <div>
            <div className="section-title">POS 交易参数（按 tag 下发 POS，安卓不收）</div>
            <p className="hint" style={{ marginTop: 0 }}>
              切换卡组时按卡组模板自动带出参数项；tag/名称/值均可自行修改增删。
            </p>
            {draft.posParams.length === 0 && (
              <p className="hint">该卡组未配置默认参数模板，可手动添加。</p>
            )}
            {draft.posParams.map((p, i) => (
              <div key={i} className="plan-row">
                <div className="field grow">
                  <label>tag *</label>
                  <input type="text" value={p.tag} placeholder="如 9F02" className="mono"
                    onChange={(e) => {
                      const list = [...draft.posParams];
                      list[i] = { ...list[i], tag: e.target.value.toUpperCase() };
                      set("posParams", list);
                    }} />
                  {errors[`pos.${i}.tag`] && <div className="err">{errors[`pos.${i}.tag`]}</div>}
                </div>
                <div className="field grow">
                  <label>名称</label>
                  <input type="text" value={p.name} placeholder="如 amount"
                    onChange={(e) => {
                      const list = [...draft.posParams];
                      list[i] = { ...list[i], name: e.target.value };
                      set("posParams", list);
                    }} />
                </div>
                <div className="field grow">
                  <label>值</label>
                  <input type="text" value={p.value} placeholder="如 100.00"
                    onChange={(e) => {
                      const list = [...draft.posParams];
                      list[i] = { ...list[i], value: e.target.value };
                      set("posParams", list);
                    }} />
                </div>
                <button className="btn small"
                  onClick={() => set("posParams", draft.posParams.filter((_, j) => j !== i))}>移除</button>
              </div>
            ))}
            <button className="btn small" onClick={() =>
              set("posParams", [...draft.posParams, { tag: "", name: "", value: "" }])}>
              ＋ 添加参数项
            </button>

            <div className="section-title" style={{ marginTop: 22 }}>操作员指引与机器期望</div>
            <div className="form-grid">
              <Field label="交易金额（操作员输入）*" error={errors.amount}
                hint="如 100.00，POS 人工输入；也可与 9F02 tag 一致">
                <input type="text" value={draft.amount} onChange={(e) => input("amount", e.target.value)} />
              </Field>
              <Field label="币种（操作员输入）*" error={errors.currency}>
                <input type="text" value={draft.currency} maxLength={3} style={{ textTransform: "uppercase" }}
                  onChange={(e) => input("currency", e.target.value.toUpperCase())} />
              </Field>
              <Field label="支付方式" hint="本后台仅支持非接 contactless">
                <input type="text" value="contactless" disabled />
              </Field>
              <Field label="PIN 提示" hint="操作员是否被要求输 PIN">
                <select value={draft.pin_hint} onChange={(e) => input("pin_hint", e.target.value)}>
                  <option value="none">none（无 PIN）</option>
                  <option value="bypass">bypass（跳过）</option>
                </select>
              </Field>
              <Field label="操作员备注 notes">
                <textarea rows={3} value={draft.notes} placeholder="给操作员的提示，如：将手机贴到读卡器"
                  onChange={(e) => input("notes", e.target.value)} />
              </Field>
              <Field label="期望金额（机器断言用，可空）" error={errors.exp_amount}
                hint="与 operator 金额分离，避免手输金额导致的假失败">
                <input type="text" value={draft.exp_amount} placeholder="如 100.00"
                  onChange={(e) => input("exp_amount", e.target.value)} />
              </Field>
              <Field label="期望币种 ISO 数字码（可空）" error={errors.exp_currency}>
                <input type="text" value={draft.exp_currency} maxLength={3} placeholder="如 156"
                  onChange={(e) => input("exp_currency", e.target.value)} />
              </Field>
              <Field label="期望 CVM 必填（可空）">
                <select value={draft.cvm_required} onChange={(e) => input("cvm_required", e.target.value)}>
                  <option value="">不校验</option>
                  <option value="true">是</option>
                  <option value="false">否</option>
                </select>
              </Field>
            </div>
          </div>
        )}

        {kind === "case" && tab === "kernel" && (
          <div>
            <div className="section-title">POS 内核参数（AID / CAPK / TERM）</div>
            <p className="hint" style={{ marginTop: 0 }}>
              每案例下发到 POS 内核（<span className="mono">pos_kernel_config</span>），在
              <span className="mono"> pos_config</span> 之前发送。留空表示<b>不下发</b>，POS 使用自带表
              （或卡组在后台配置的默认集合）。
            </p>
            <p className="hint">
              本版本用 JSON 文本域编辑（不做 AID/CAPK 表格 UI）。服务端会做深度校验：
              <span className="mono">9F06</span> 5–8 字节、CAPK 元素顺序、
              <span className="mono"> DF02</span> 模数长度 <= 256 字节、
              <span className="mono"> DF03 = SHA1(9F06‖9F22‖DF02‖DF04)</span>、
              <span className="mono"> DF05</span> 编码（ascii8/bcd4/bcd3/raw）与去重。
            </p>
            <details style={{ marginBottom: 12 }}>
              <summary className="hint">结构示例（可点开复制）</summary>
              <pre className="mono" style={{ fontSize: 12, overflowX: "auto" }}>{`{
  "aids": [
    {"aid": "A0000000031010",
     "fields": [{"tag": "DF01", "value": "01"},
                {"tag": "9F08", "value": "0002"}]}
  ],
  "caps": [
    {"rid": "A000000003", "key_index": "01",
     "expiry": "20441231", "expiry_encoding": "ascii8",
     "hash_alg": 1, "key_alg": 1,
     "modulus": "<DF02 裸模数 hex>", "exponent": "03",
     "checksum": "<DF03 20 字节 hex，可留空由 host 计算>"}
  ],
  "drl": [
    {"rid": "A000000025", "index": "01", "serial": "001000"}
  ],
  "exception_file": [
    {"pan": "373737345678904F", "pan_sequence": "00"}
  ],
  "term": "9F6604360000809F1A0201569F7A0101",
  "version": "4.2.0"
}`}</pre>
            </details>
            <Field label="kernel_params_ref（命名 POS 配置）"
              error={errors.kernelParams}
              hint="外部 <kernel-params-dir>/<scheme>/<ref>.json 的 id；与下方 JSON 二选一。留空=不引用">
              <input type="text" value={draft.kernelParamsRef}
                placeholder="如 CONF_01"
                onChange={(e) => input("kernelParamsRef", e.target.value)} />
            </Field>
            <Field label="kernel_params（JSON）" error={errors.kernelParams}
              hint="留空=不下发；aids/caps 可为空数组；term 可省略（host 会告警）">
              <textarea rows={14} className="mono" spellCheck={false}
                placeholder='留空表示不下发内核参数'
                value={draft.kernelParamsJson}
                onChange={(e) => input("kernelParamsJson", e.target.value)} />
            </Field>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button className="btn small" onClick={() =>
                input("kernelParamsJson", JSON.stringify(
                  { aids: [], caps: [], drl: [], exception_file: [], term: "" }, null, 2))}>
                插入空模板
              </button>
              <button className="btn small" onClick={() =>
                input("kernelParamsJson", JSON.stringify(
                  { aids: [], caps: [] }, null, 2))}>
                清空为本案例不下发
              </button>
            </div>
          </div>
        )}

        {kind === "case" && tab === "plan" && (
          <div>
            {draft.plan.map((row, i) => (
              <div key={i} className="plan-row">
                <span className="step-no">#{i + 1}</span>
                <div className="field grow">
                  <label>操作说明 *</label>
                  <input type="text" value={row.action} placeholder="如：主机下发卡片档案并启动模拟"
                    onChange={(e) => {
                      const list = [...draft.plan];
                      list[i] = { ...list[i], action: e.target.value };
                      set("plan", list);
                    }} />
                  {errors[`plan.${i}.action`] && <div className="err">{errors[`plan.${i}.action`]}</div>}
                </div>
                <div className="field grow">
                  <label>预期结果 *</label>
                  <input type="text" value={row.expect} placeholder="如：安卓上报 emulating"
                    onChange={(e) => {
                      const list = [...draft.plan];
                      list[i] = { ...list[i], expect: e.target.value };
                      set("plan", list);
                    }} />
                  {errors[`plan.${i}.expect`] && <div className="err">{errors[`plan.${i}.expect`]}</div>}
                </div>
                <button className="btn small" disabled={draft.plan.length <= 1}
                  onClick={() => set("plan", draft.plan.filter((_, j) => j !== i))}>移除</button>
              </div>
            ))}
            <button className="btn small" onClick={() =>
              set("plan", [...draft.plan, { action: "", expect: "" }])}>＋ 添加步骤</button>
          </div>
        )}

        {kind === "case" && tab === "rules" && (
          <div>
            <p className="hint" style={{ marginTop: 0 }}>
              断言作用于 POS 交易结果报文，路径如 <span className="mono">resp_code</span>、
              <span className="mono">outcome</span>、<span className="mono">tags.gen_ac.9F26</span>。
              所有 pass 级规则满足才判 PASS；warn 级不满足判 WARN。
            </p>
            {draft.rules.map((r, i) => (
              <div key={i} className="rule-row">
                <div className="field grow">
                  <label>字段路径 *</label>
                  <input type="text" value={r.path} placeholder="resp_code"
                    onChange={(e) => {
                      const list = [...draft.rules];
                      list[i] = { ...list[i], path: e.target.value };
                      set("rules", list);
                    }} />
                  {errors[`rules.${i}.path`] && <div className="err">{errors[`rules.${i}.path`]}</div>}
                </div>
                <div className="field grow">
                  <label>操作符 *</label>
                  <select value={r.op}
                    onChange={(e) => {
                      const list = [...draft.rules];
                      list[i] = { ...list[i], op: e.target.value };
                      set("rules", list);
                    }}>
                    {OPS.map((op) => <option key={op} value={op}>{op}</option>)}
                  </select>
                </div>
                <div className="field grow">
                  <label>期望值 {NO_EXPECT_OPS.has(r.op) ? "（present/absent 无需填写）" : "*"}</label>
                  <input type="text" value={r.expect}
                    disabled={NO_EXPECT_OPS.has(r.op)}
                    placeholder={r.op === "any_of" ? "00, 01（逗号分隔）" : r.op === "range" ? "0, 1000" : "00"}
                    onChange={(e) => {
                      const list = [...draft.rules];
                      list[i] = { ...list[i], expect: e.target.value };
                      set("rules", list);
                    }} />
                  {errors[`rules.${i}.expect`] && <div className="err">{errors[`rules.${i}.expect`]}</div>}
                </div>
                <div className="field">
                  <label>严重度</label>
                  <select value={r.severity}
                    onChange={(e) => {
                      const list = [...draft.rules];
                      list[i] = { ...list[i], severity: e.target.value };
                      set("rules", list);
                    }}>
                    <option value="pass">pass</option>
                    <option value="warn">warn</option>
                  </select>
                </div>
                <button className="btn small" disabled={draft.rules.length <= 1}
                  onClick={() => set("rules", draft.rules.filter((_, j) => j !== i))}>移除</button>
              </div>
            ))}
            <button className="btn small" onClick={() =>
              set("rules", [...draft.rules, { path: "", op: "eq", expect: "", severity: "pass" }])}>
              ＋ 添加断言规则
            </button>
          </div>
        )}

        {/* ======================= 总案例 ======================= */}
        {kind === "group" && tab === "basic" && (
          <div>
            <p className="hint" style={{ marginTop: 0 }}>
              总案例（分组）仅配置<b>说明与 test plan</b>，不配置 profile / 交易参数 /
              预期结果——子案例各自内嵌档案与参数。创建后可「继续添加该组的子案例」。
            </p>
            <div className="form-grid">
              <Field label="总案例号 group_id *" error={gerrors.group_id}
                hint="命名习惯如 OPBD-001；同时作为目录名；不可直接运行">
                <input type="text" value={gdraft.group_id} placeholder="如 OPBD-001"
                  onChange={(e) => ginput("group_id", e.target.value)} />
              </Field>
              <Field label="标题 *" error={gerrors.title}>
                <input type="text" value={gdraft.title} placeholder="如 OPB.D 001.00 使用PPSE目录选择方式"
                  onChange={(e) => ginput("title", e.target.value)} />
              </Field>
              <Field label="卡组 *">
                <select value={gdraft.scheme} onChange={(e) => changeScheme(e.target.value)}>
                  {schemes.map((s) => (
                    <option key={s.scheme} value={s.scheme}>{s.display_name}</option>
                  ))}
                </select>
              </Field>
              <Field label="摘要 summary">
                <textarea rows={2} value={gdraft.summary}
                  placeholder="可选，分组一句话说明"
                  onChange={(e) => ginput("summary", e.target.value)} />
              </Field>
              <Field label="测试目的 purpose">
                <textarea rows={3} value={gdraft.purpose}
                  onChange={(e) => ginput("purpose", e.target.value)} />
              </Field>
              <Field label="测试条件 test_conditions">
                <textarea rows={3} value={gdraft.test_conditions}
                  onChange={(e) => ginput("test_conditions", e.target.value)} />
              </Field>
            </div>

            <div className="gap">
              <div className="section-title">test plan（总案例级步骤）</div>
              {gdraft.plan.map((row, i) => (
                <div key={i} className="plan-row">
                  <span className="step-no">#{i + 1}</span>
                  <div className="field grow">
                    <label>操作说明 *</label>
                    <input type="text" value={row.action} placeholder="如：交易预处理成功"
                      onChange={(e) => {
                        const list = [...gdraft.plan];
                        list[i] = { ...list[i], action: e.target.value };
                        gset("plan", list);
                      }} />
                    {gerrors[`plan.${i}.action`] && <div className="err">{gerrors[`plan.${i}.action`]}</div>}
                  </div>
                  <div className="field grow">
                    <label>预期结果 *</label>
                    <input type="text" value={row.expect} placeholder="如：进入 PPSE 选择流程"
                      onChange={(e) => {
                        const list = [...gdraft.plan];
                        list[i] = { ...list[i], expect: e.target.value };
                        gset("plan", list);
                      }} />
                    {gerrors[`plan.${i}.expect`] && <div className="err">{gerrors[`plan.${i}.expect`]}</div>}
                  </div>
                  <button className="btn small" disabled={gdraft.plan.length <= 1}
                    onClick={() => gset("plan", gdraft.plan.filter((_, j) => j !== i))}>移除</button>
                </div>
              ))}
              <button className="btn small" onClick={() =>
                gset("plan", [...gdraft.plan, { action: "", expect: "" }])}>＋ 添加步骤</button>
            </div>

            <div className="gap">
              <div className="section-title">通过标准 pass_criteria（逐条）</div>
              {gdraft.pass_criteria.map((c, i) => (
                <div key={i} className="row-line">
                  <input type="text" value={c} placeholder={`通过标准 ${i + 1}`}
                    onChange={(e) => {
                      const list = [...gdraft.pass_criteria];
                      list[i] = e.target.value;
                      gset("pass_criteria", list);
                    }} />
                  <button className="btn small" disabled={gdraft.pass_criteria.length <= 1}
                    onClick={() => gset("pass_criteria", gdraft.pass_criteria.filter((_, j) => j !== i))}>移除</button>
                  {gerrors[`criteria.${i}`] && <span className="err">{gerrors[`criteria.${i}`]}</span>}
                </div>
              ))}
              <button className="btn small" onClick={() =>
                gset("pass_criteria", [...gdraft.pass_criteria, ""])}>＋ 添加通过标准</button>
            </div>
          </div>
        )}

        {/* ======================= JSON 导入（两种形态共用） ======================= */}
        {tab === "json" && (
          <div>
            <p className="hint" style={{ marginTop: 0 }}>
              {kind === "group"
                ? "总案例 JSON（GroupDef：group_id/title/purpose/test_conditions/test_plan/pass_criteria，无 profile）。"
                : "可执行案例 JSON（含内嵌 profile 与 transaction.pos_params）。"}
              表单自动生成预览；勾选「编辑 JSON」后可粘贴直接导入，提交前先调用服务端校验。
            </p>
            <label className="check-line">
              <input type="checkbox" style={{ accentColor: "var(--accent)" }}
                checked={jsonMode}
                onChange={(e) => {
                  setJsonMode(e.target.checked);
                  if (e.target.checked) syncJsonFromForm();
                }} />
              编辑 JSON 模式（从表单生成或手动粘贴）
            </label>
            <textarea className="json-area" rows={18} spellCheck={false}
              readOnly={!jsonMode} value={jsonMode ? jsonText : JSON.stringify(activePayload(), null, 2)}
              onChange={(e) => setJsonText(e.target.value)} />
            {jsonMode && (
              <button className="btn small" style={{ marginTop: 8 }} onClick={syncJsonFromForm}>
                ⟲ 从当前表单重新生成 JSON
              </button>
            )}
          </div>
        )}

        <div className="form-actions">
          <button className="btn primary" disabled={submitting} onClick={() => void onSubmit()}>
            {submitting ? "提交中…" : kind === "group" ? "＋ 添加总案例（分组）" : "＋ 添加测试案例"}
          </button>
          <button className="btn" onClick={reset}>清空重填</button>
        </div>
      </div>
    </div>
  );
}
