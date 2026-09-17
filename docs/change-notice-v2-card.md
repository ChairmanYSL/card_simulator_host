# 卡端变更通知：协议 v2（全量 APDU 上送 + token 白名单）

> 影响端：**卡端 Android app**（HCE 卡模拟）。窗口：与 host **同窗口升级**，
> protocol_version = **`"2.0"`**（硬切换）。
> 交付：本通知由 host 侧生成，只读交付；host **不修改卡端仓库**。

## 1. 握手

- `handshake.protocol_version` 必须为 `"2.0"`；缺失或 `<2.0` -> host `protocol_mismatch` 断开。

## 2. 保留不动的部分

`card_config{profile, keys|null, timeout_seconds}`、`start_emulation`、`stop_emulation`、
`status{state:ready|emulating|stopped|error, detail}` 全部保留（R3），语义不变。

## 3. 全量 APDU 上送（R3/R4=a）

**卡端必须把每个案例内的所有 APDU 逐条上送给 host**，host 据此判定通过条件。

- 通道：沿用现有 `log{level:"trace", message:{...}}` 帧（R4=a），**不再受 `trace_enabled` 控制，恒开**；
- `message` 字段沿用现有格式：
  `{event:"apdu"|"field", direction:"COMMAND"|"RESPONSE", cla, ins, p1, p2, data_hex, sw?, elapsed_ms, seq?, profile_id?}`
  （`sw` 为 hex 字符串，COMMAND 省略；`data_hex` 无数据为 `""`）；
- **按案例分段、不得合并**（R3）：
  - 每帧必须带 `case_id` + `attempt_id`（= 最近一次 `card_config`/`start_emulation` 的值）；
  - 卡端在收到 `card_config` / `start_emulation` 后**重置 APDU `seq`**；
  - 案例结束（收到 `stop_emulation` 或上报 `status:stopped`）**封口**；
  - 严禁把上一个案例的 APDU 混入本案例（host 会判 `card_apdu_bleed`）；
- 静默指令（`noResponseCommands`）仍只发 COMMAND 条目（`no_response:true`，不发 RESPONSE）；
- 字段缺失/串案 -> host 判该案例 FAIL（`card_apdu_missing`）。

> 说明：该通道此前只作诊断（「永不判定」）；v2 起它是**判定源**，因此完整性与案例归属是硬要求。

## 4. token 白名单扩展（用户裁定）

以下 tag 需加入卡端「记录/FCI 构造」的 token 白名单，并支持按 profile 配置发出：

| token | tag | 名称 | 格式/长度 | 出现位置 |
|---|---|---|---|---|
| `cdol1` | `8C` | CDOL1 | TLV 模板（原样透传） | READ RECORD |
| `cdol2` | `8D` | CDOL2 | TLV 模板 | READ RECORD |
| （新增） | `9F07` | Application Usage Control | b，2B | READ RECORD |
| （新增） | `9F08` | Application Version Number | b，2B | READ RECORD |
| （新增） | `9F42` | Application Currency Code | n3->BCD，2B | READ RECORD |
| （新增） | `5F25` | Application Effective Date | n6->BCD，3B（YYMMDD） | READ RECORD |
| （新增） | `9F0D` | Issuer Action Code - Default | b，5B | READ RECORD |
| （新增） | `9F0E` | Issuer Action Code - Denial | b，5B | READ RECORD |
| （新增） | `9F0F` | Issuer Action Code - Online | b，5B | READ RECORD |
| （新增） | `9F70` | Card Interface and Payment Capabilities | b，**2B（Format b 16）**，Amex 独有 | READ RECORD |
| （新增） | `5F2D` | Language Preference | an，2-8B，[ISO639] alpha | **FCI（A5/BF0C）** |

- token 名沿用现有 6 个（`pan/track2/cardholderName/expiryDate/issuerCountryCode/cvmList`）的风格；
- **待双方确认**：上述「新增」行的 profile 字段名（camelCase），建议：
  `applicationUsageControl / applicationCurrencyCode / applicationEffectiveDate /
  iacDefault / iacDenial / iacOnline / cardInterfaceAndPaymentCapabilities / languagePreference`；
- 目标是能复现参考日志的卡：AID `A000000025010403`、PDOL `9F35019F370495059F34039F02069F0306`、
  CDOL1 `9F02069F03069F1A0295055F2A029A039C019F37049F6E049F34039F3501`、记录 1 含上述 tag。

## 5. 回报要求

1. 确认 APDU trace 恒开、按案例分段（带 `case_id`/`attempt_id`、seq 重置与封口规则）；
2. 确认 token 白名单扩展落地 + 最终 profile 字段名；
3. 升级版本号 + commit。
