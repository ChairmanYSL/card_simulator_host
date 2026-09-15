# EMV Test Host — 统一设备协议 v1.2（唯一权威文档）

> 本文档为**唯一权威协议**，由上位机（host）与安卓卡模拟 app 双方协商定稿
> （协商结论 H1–H10 / O1–O7 / N1–N12 全部吸收，v1.2 见 §12）。
> 变更索引与审核入口：`docs/CHANGELOG.md`。
> 安卓侧旧文档（docs/host-protocol.md）已降级为指向本页的说明。
> POS 侧单列通知：`docs/protocol-change-notice-v1.2.md`。
>
> **验证哲学（定案）**：判定以 POS `transaction_result` 为准；安卓 APDU 流
> 是**诊断 trace**（Phase A），永不参与判定。
> 协议层字段全量 snake_case；`profile` / `keys` 为负载，使用各自的
> 规范化 schema（见 §8、§9），原样透传。
>
> **⚠️ v1.2 是硬切换**：客户端握手必须上报 `protocol_version:"1.2"`，
> 缺失或 `<1.2` 一律被拒（§12.1）。下文 §0–§11 描述与 v1.1 相同的部分，
> **凡与 §12 冲突处以 §12 为准**。

## 0. 角色与端口

| 角色 | 连接方向 | 默认端口 |
|---|---|---|
| host（Windows 上位机） | TCP 服务端 | 设备协议 **9100**（单端口）；Web/API/WS **8000** |
| android（NFC 卡模拟 app） | TCP 客户端 | 接入方式：`adb forward tcp:9100 tcp:9100` 或局域网 IP |
| pos（POS 交易 app） | TCP 客户端 | 同上 9100（POS 全人工操作，交易结束后自动回传结果） |

无 TLS；token 仅握手使用，之后凭连接会话。仅限隔离测试网络。

## 1. 帧格式（锁定）

```
+---------------------+-------------------------+
| 4 字节大端无符号长度 | UTF-8 JSON payload      |
+---------------------+-------------------------+
```

* 长度按 payload **UTF-8 字节数**计；最大帧 **1 MiB**，超出断开；
* payload 必须是 JSON 对象；单个连接累计 **5 个坏帧**即断开；
* 所有帧带信封：`{"v": 1, "type": "<消息类型>", "msg_id": "<uuid-hex>", "ts": <epoch 秒>}`。

## 2. 握手与身份验证

1. 客户端连接后 **10s 内**发 `handshake`；
2. 校验 `client_type` + `token`（对照 `tokens.json`）与 `protocol_version`；
3. 每类型**一个连接**；同 `device_id` 重连**替换旧会话**（H10/O6），不同
   device_id → `already_connected`。

```json
// client -> host
{"v":1,"type":"handshake","msg_id":"…","ts":…,
 "client_type":"android"|"pos","token":"…",
 "device_id":"…","app_version":"…","device_model":"…",
 "protocol_version":"1.1"}
// host -> client
{"v":1,"type":"handshake_ack","msg_id":"…","ts":…,
 "session_id":"android-<ts>-<port>","protocol_version":"1.1","server_time":…}
```

错误码：`auth_failed` / `already_connected` / `handshake_timeout` /
`protocol_mismatch`（版本 < 1.1 的**确定性失败**——客户端收到后不重连、
明确报错停止，与可重试的断线区分）。

安卓握手成功后 host 立即下发一次 `config_push{config_type:"device_config"}`。

## 3. ack 双向表与幂等（O5 定稿）

| 消息 | ack 方 | 语义 |
|---|---|---|
| host→app：config_push / start_emulation / stop_emulation | app 必 ack | host 15s 超时按用例流程处理 |
| host→pos：config_push(pos_config) / case_started | pos 必 ack | pos_config=交易参数已应用；case_started=信息帧 |
| app→host：status | **host 必 ack** | app 15s 未收 ack → 指数退避重发（1/2/4/8/16/30s 封顶） |
| pos→host：transaction_result | **host 必 ack** | ack = 已持久化；按 msg_id 幂等去重 |
| app→host：log / trace | 不 ack | 单向 |

## 4. 会话归属与迟到结果

* 每个用例的每次执行有唯一 `attempt_id`（host 生成，随 config/start 下发）；
* **用例上下文内**的 app 上报（status/log/trace）必须回显 `case_id + attempt_id`；
* **用例外**的上报（断线、无用例时）**不带** case_id/attempt_id——host 只记
  通用日志，绝不归入任何用例；
* POS `transaction_result` 按 `attempt_id` 归属；过期/缺失/无运行 → 写入
  quarantine（`runs/<日期>/<run_id>/quarantine.jsonl`），**不参与判定**。

## 5. 心跳与失联检测（H6/N5 定稿）

* host 每 **10s** 发 `heartbeat`，客户端回 `heartbeat_ack`（任何帧都算存活）；
* 静默 ≥ **30s** → host 告警；≥ **60s** → host 断开；
* **客户端 watchdog**：3 × `device_config.heartbeat_interval`（缺省 10s×3）
  未收到 host heartbeat → 判死 → 断线 → 按 1/2/4/8/16/30s 退避自动重连
  （同 device_id 重连会被 host 接受并替换旧会话）；
* 未收到 device_config 前 app 用缺省（10s 心跳 / 30s 判死 / 15s ack 超时）。

## 6. 消息目录

### 6.1 通用（双向）
| type | 字段 | 说明 |
|---|---|---|
| heartbeat / heartbeat_ack | `ref_msg_id` | 保活 |
| ack | `ack_msg_id, ok, detail` | 见 §3 |
| error | `code, message, ref_msg_id?` | host→client 错误 |
| log | `level, message, case_id?, attempt_id?` | 客户端日志；`level:"trace"` 为诊断 trace（§6.4） |

### 6.2 host → android
| type | 字段 | 说明 |
|---|---|---|
| config_push（device_config） | `config_type, config:{heartbeat_interval, heartbeat_warn, log_level, trace_enabled, ack_timeout_seconds, extensions}` | 握手后一次 |
| config_push（card_config） | `config_type, case_id, attempt_id, profile, keys|null, timeout_seconds` | 每用例开始前（§8/§9） |
| start_emulation | `case_id, attempt_id` | 激活 + AID 注册 |
| stop_emulation | `case_id, attempt_id` | 内核 reset；**保留 AID 注册与档案**；重复 start 沿用当前档案（H9） |

### 6.3 host → pos
| type | 字段 | 说明 |
|---|---|---|
| config_push（pos_config） | `config_type, case_id, attempt_id, pos_params[{tag,name,value}]` | **每用例、安卓 emulating 之后、case_started 之前**下发；POS 应用交易参数后必 ack；POS 离线仅 warn（操作员可在 POS 上手动输入） |
| case_started | `case_id, attempt_id, scheme, entry_mode, operator_instructions` | 信息帧，人工操作提示 |

### 6.4 status / trace（安卓状态机，N9 定稿时序）
```
card_config ─ack→ app 处理完成 ──status{state:"ready", detail:{profile_id,
    default_payment_app, registered_aids[], ac_crypto}}──→ host 告警提示(可选)
start_emulation ─ack→ 激活+AID 注册 ──status{state:"emulating", detail:{profile_id}}──→ host 进入 WAIT_RESULT（15s 门槛）
交易结束/收到 stop_emulation ──status{state:"stopped", detail:{...}}──
异常 ──status{state:"error", detail:{code, message}}──
```
* error 码表（N7）：`import_failed / aid_registration_failed / atc_exhausted / internal`；
  用例内 error → 当前用例 ERROR、整轮中止（reason `android_error`，code 记入结果）；
  用例外 error → 仅日志与状态灯；
* `ready.detail.default_payment_app=false` → host 告警（不阻断，操作员处理）；
* `ac_crypto`：`"real"`（有密钥真实 Retail MAC）或 `"fake"`（8×0xFA 假密文，见 §9）；
* trace（Phase A）：`log{level:"trace", message:{event:"apdu"|"field",
  direction:"COMMAND"|"RESPONSE", cla, ins, p1, p2, data_hex, sw?, elapsed_ms,
  seq?, profile_id?}, case_id, attempt_id}`；
  * `message` 为 JSON **对象**；`sw` 为 hex 字符串（如 `"9000"`），COMMAND 省略；
  * `data_hex` 无数据为 `""`；`elapsed_ms`：COMMAND=0，RESPONSE=自配对 COMMAND 起耗时；
  * **静默指令（N12）**：命中 `noResponseCommands` 的 COMMAND 条目增加
    `no_response: true`，且**不产生对应 RESPONSE 条目**（app 本地日志同步标注该条无响应）；
  * 受 `device_config.trace_enabled` 控制（默认关）；host 按 attempt 落盘
    `runs/<run_id>/trace/<case_id>.jsonl` 并在历史页展示，**永不判定**；
  * Phase B（`card_trace` 批量、可断言）后续演进，本期不实现。

## 7. POS 结果与判定（不变，POS 权威）

`transaction_result{case_id, attempt_id, result}`；`result` 含 resp_code /
outcome / online / approval_code / cvm / amount / currency / entry_mode /
tags（按 gpo / read_record / gen_ac 分组）。判定规则、真值表与匹配操作符
沿用既有语义：所有 `pass` 级规则满足才 PASS，`warn` 不满足判 WARN；
`cvm:"unknown"` 不算失败；CVM/ATC/UN 等易变标签用 present/prefix/absent。

**诊断字段（2026-09-09 联调约定，可选、不参与判定）**：为让归因不必翻 logcat，
POS 在 `result` 内附以下字段，`pos_meta` 内附 `app_version` / `device_model`：

| 字段 | 含义 |
|---|---|
| `result.app_version` | POS 应用版本（= `BuildConfig.VERSION_NAME`） |
| `result.kernel_retcode` | 内核完成码/负返回码（如 101/102/103/104/111/-1133） |
| `result.kernel_stage` | 失败/结束阶段（`app_select` / `gpo` / `read_record` / `gac` / `online` …） |
| `result.card_present_ms` | 卡端在场时长（用于判定是否属"HCE 会话窗口不足"） |
| `pos_meta.app_version` / `device_model` | 帧级设备与应用版本 |

host 侧：缺失这些字段不影响判定（仅影响报告与归因质量）；报告生成器
（`tools/joint_debug/report.py`）在字段存在时优先采用真实值。

## 8. 案例库与 CardProfile JSON（camelCase）

**v2 案例库布局**（profiles 与案例绑定，无卡组级共享档案库）：:

    cases/
      <scheme>/
        scheme.json                      scheme metadata + suite policy
                                         + card_template + pos_params 模板
        contactless/cases/<case_id>.json              独立可执行案例（自包含）
        contactless/cases/<group>/group.json          总案例（仅说明，无 profile）
        contactless/cases/<group>/<group>-NN.json     子案例（自包含，可执行）

* 每个**可执行案例**（独立案例或子案例）JSON 内嵌 `profile`（CardProfile）
  与可选 `keys`——**profile 与案例绑定**：`profile.id` 缺省自动 = `case_id`，
  显式不一致仅 warning；总案例（group.json）不配置 profile；
* **总案例可经 UI/JSON 创建**（`POST /api/groups`，仅说明 + test plan，
  **无 profile/keys/交易参数/预期结果**）；携带 `group_id` 的可执行案例
  写入 `<group>/<case_id>.json` 目录（loader 按目录归属，文件内不落 group_id）；
* `scheme.json.card_template`：该卡组默认卡片模板（新建案例时克隆）；
* `scheme.json.pos_params`：该卡组默认 **POS 交易参数模板**（新建案例自动带出，
  可增删改，tag 可自行设定）；
* 案例内 `transaction.pos_params`：本次实际下发的 POS 参数（tag 列表）。

**CardProfile 字段表**（与安卓 SAF 导入 schema 同构，camelCase）：
协议层字段 snake_case，profile 内部为 camelCase（N1 定稿）。
host 仅消费以下子集，其余字段原样透传（extra 透传）：

| 字段 | 说明 | host 消费 |
|---|---|---|
| `id` | 绑定主键（案例内嵌时 = case_id，可省略自动补全） | 标识 |
| `name` / `description` | 显示名 / 备注 | 显示名 |
| `network` | VISA/MASTERCARD/AMEX/JCB/UNIONPAY | 透传 |
| `aids[]` | `{aid, label, priority}` PPSE+SELECT | AID |
| `pan` | 主账号（数字，非数字忽略；Luhn 对象） | PAN |
| `panSequenceNumber` | 默认 1 | 透传 |
| `applicationExpirationDate` | YYMMDD（tag 5F24） | 有效期 |
| `track2EquivalentData` | hex（tag 57，Track2 校验对象） | Track2 校验 |
| `cardholderName` / `applicationLabel` / `applicationVersionNumber` | tag 5F20/50/9F09 | 透传 |
| `aip` / `afl` | tag 82/94（afl 4N、first≤last） | 断言/AFL 校验 |
| `issuerApplicationData` / `issuerCountryCode` | tag 9F10/5F28 | 透传 |
| `cvmList[]` | `{cvmCode, cvmCondition, applySucceedingCvm}` tag 8E | 透传 |
| `pdol`/`cdol1`/`cdol2`/`tdol`/`ddol` | hex TLV 模板 | 透传 |
| `symmetricKeyId` | 指向 keys.symmetric_keys[].id；无 → 假密文 | 真假判定 |
| `rsaKeyId` / `certificateProfileId` | ODA / 证书引用 | 透传 |
| `records[]` | `{sfi(1..31), record(≥1), fields[token]}`；空数组=旧行为；未配置 (sfi,record) → 0x6A83 | 记录配置 |
| `acBehavior` | `online_request/offline_approved/offline_declined`；非法→缺省（跟随终端 P1）warn | ac 行为 |
| `ppseMode` | `auto/disabled/empty/custom`（缺省 auto）；非法→auto（warn） | PPSE 行为 |
| `ppseSw` | 大写 4 位 hex；**任意 mode 的全局 SW 覆盖**（缺省为各 mode 默认 SW） | PPSE 行为 |
| `ppseData` | 偶长 hex 完整 PPSE FCI 模板（仅 custom 使用）；host 加载期 TLV 长度一致性检查（warning），app 侧非法 → 6700 | PPSE 行为 |
| `noResponseCommands` | **指令不响应（静默）列表（N12）**，见 §8.4；缺省 `[]` | 静默行为 |
| `createdAt` / `updatedAt` | app 管理 | 透传 |
| 其它未知字段 | Gson 默认忽略 | 透传 |

**分工边界（v2 定稿）**：profile 是**卡片侧**数据——AIP（tag 82，字段 `aip`）、
CVM（tag 8E，字段 `cvmList[]`）、AID、PAN、记录等全部随 `card_config{profile,
keys}` 下发安卓；**交易参数（pos_params，按 tag）只下发 POS**（pos_config），
安卓侧永远收不到 pos_params。

### 8.1 POS 交易参数（pos_params）

```jsonc
// scheme.json 模板（每卡组要求不同）与案例内 transaction.pos_params 同构
"pos_params": [
  {"tag": "9F02", "name": "amount", "value": "100.00"},   // 金额
  {"tag": "5F2A", "name": "currency", "value": "156"},    // 币种
  {"tag": "9C",   "name": "transaction_type", "value": "00"}
]
```

* `tag`：EMV tag，1–3 字节**大写 hex**；多字节 tag 首字节低 5 位须为 `0x1F`
  （如 9F02/5F2A/BF0C）；格式错误在加载期即拦截；
* `name`/`value`：POS 端解释使用的辅助名与取值（字符串）；
* 每案例在「添加用例」页按卡组模板**自动带出**，可增删行、改 tag；
* POS 收到后 ack；POS 侧的 tag 语义由 POS 实现约定（本表仅为模板示例）。

**host 约定 tag（联调裁决 2026-09-09）**：

| tag | 名称 | 取值 | 语义 |
|---|---|---|---|
| `DF8101` | `test_host_online_approval` | `01` / 缺省 | 仅当 host 显式下发 `01` 时，POS 在收到 `EMV_REQ_GO_ONLINE` 后返回**测试主机在线授权**（`setOnlineResult(EMV_ONLINE_SUCC_ACCEPT, 占位 ARPC, len)` 后继续流程）；未下发时 POS 保持真实行为（无收单主机 → 在线失败） |
| `9F66` | `ttq` | 如 `36804000` | 终端交易属性覆盖：POS 收到后覆盖内核 TTQ（用于验证在线能力位） |
| `9F1B` | `terminal_floor_limit` | 如 `FFFFFFFF` | 终端 floor limit 覆盖：POS 收到后覆盖内核终端限额（用于让确定性离线用例**不因超过限额被 TRM 判为强制联机**，如 `VISA-CL-001`） |

用途：`UPI-CL-003` / `MC-CL-003` / `QPB-B-002-01` 属"期望在线批准"用例，
现场无收单主机；按用户裁决，**只有 host 显式开启时才模拟授权**，避免默认伪造。
`VISA-CL-001` 的 `9F1B=FFFFFFFF` 用于消除终端风险管理（TRM）造成的随机联机
（实测内核 `EMV_REQ_CONFIRM_FORCE_ONLINE` 不触发、`preprocess` 后
`setForceOnline(0)` 返回 -124 无效，floor limit 才是有效杠杆）。

### 8.2 keys（随案例内嵌）

`keys` 不再是 `<profile_id>.keys.json` 兄弟文件，而是**内嵌在案例 JSON 中**
（可选字段；无 keys 的案例走假密文，见 §9）。结构见 §9，`card_config.keys`
与案例内嵌 keys 同构。

### 8.3 PPSE mode 语义（N11 定稿，用例↔mode 映射）

`auto`=按 aids 生成目录（9000）；`disabled`=直接回 6A82；`empty`=9000 + BF0C
内无 61 条目；`custom`=原样返回 ppseData（默认 9000，可用 ppseSw 覆盖）。
典型用例：正常 FCI→auto；FCI 缺 AID→custom（61 内仅 50/87、无 4F）；无 61
模板→empty；错误响应码→disabled（可加 ppseSw 表达任意错误码）。custom 模板
示例（label 不填充）：
`6F20 840E 325041592E5359532E4444463031 A50E BF0C0B 6109 5004 56495341 870101`
（generator 编程构造，长度由 loader 的 TLV 校验兜底）。

host 加载校验（针对案例内嵌 profile / scheme.json card_template）：Luhn；
有效期 YYMMDD；hex 偶长；afl 4N+first≤last；records sfi∈[1,31]、无重复；
aids hex；acBehavior 非法仅 warning；内嵌 keys 校验 base64 与 id 模式
`^[A-Za-z0-9_-]{1,64}$`；pos_params tag 格式在模型层拦截、重复 tag 仅 warning。
profile.id 与 case_id 绑定：缺省自动补全 = case_id，不一致仅 warning。

### 8.4 指令不响应（noResponseCommands，N12 定稿）

**用途**：让卡片对某条 APDU 指令**不发任何响应**（静默）——区别于
`ppseMode:disabled`（回错误 SW 6A82）。用于构造“终端等不到响应”的异常用例
（如 GPO 无响应 → 终端超时/不再发起交易）。

```jsonc
// 案例内嵌 profile 中
"noResponseCommands": [
  "GPO",                                    // 简写：每次都静默
  {"command": "READ_RECORD", "times": 1}    // 仅前 1 次静默，之后正常应答
]
```

* 元素两种形态：字符串 = 该指令**每次都静默**；对象 `{command, times:N≥1}`
  = **仅前 N 次静默**，第 N+1 次起恢复正常应答；
* 指令名（**命令名注册表**，第一期，host 校验未知项为 warning、app 侧忽略）：
  `PPSE`（SELECT PPSE）、`SELECT`（SELECT AID）、`GPO`（GET PROCESSING
  OPTIONS）、`READ_RECORD`、`GET_DATA`、`GENERATE_AC`、`VERIFY`（PIN）；
* 静默期间 app **不发送任何响应 APDU**（无 TLV、无 SW）；终端行为由 POS 实现
  决定（通常重发或超时中止），host 不判定安卓静默本身，判定仍以 POS 结果为准；
* 空列表 / 字段缺省 = 全部指令正常响应；同一指令重复配置 → host warning、
  app 侧按数组顺序后项覆盖前项（双方 warning 策略一致）。

**N12 验收裁决（2026-09-09，用户定稿）**：真实 POS 内核在首次 GPO 静默后
**不再重发 GPO**（`状态 27 → -112`），因此 `UPI-CL-007` 的判定改为
「终端超时/拒绝即 PASS」：`resp_code any_of ['80','91','05']` + `outcome eq
declined`。卡端 `noResponseCommands` 语义维持本节不变（静默 = 不发任何响应，
`times` 按单次 emulation 会话累计）；如需验证"静默后恢复"，改用 APDU 级探针
（同一会话连发两次 GPO），不要走真实终端。

**app 侧映射表与匹配策略（N12 安卓确认稿，定稿）**：

| 名称 | CLA | INS | P1/P2 | 附加条件 |
|---|---|---|---|---|
| PPSE | 00 | A4 | 04/00 | data = `2PAY.SYS.DDF01` 或 `1PAY.SYS.DDF01` |
| SELECT | 00 | A4 | 04/00 | data ≠ PPSE DF 名（即应用 AID） |
| GPO | 80 | A8 | 不限定 | — |
| READ_RECORD | 00 | B2 | 不限定 | — |
| GET_DATA | 80 | CA | 不限定 | — |
| GENERATE_AC | 80 | AE | 不限定 | — |
| VERIFY | 00 | 20 | 不限定 | — |

* 匹配策略：按 CLA+INS（PPSE/SELECT 再按数据域是否等于 PPSE DF 名区分）匹配，
  P1/P2 不作筛选，防终端异形 P1/P2 漏匹配；
* `times` 计数会话边界：`start_emulation` 起、`stop_emulation` 止；start 时清零，
  跨用例不延续；计数在 HCE 路由层的会话级过滤器上，独立于内核交易状态
  （断场/0x88 内核 reset 不影响计数，同一会话多次贴卡累计）；
* 实现落点：HCE 路由层在进入 EMV 内核之前拦截，命中静默 →
  `processCommandApdu` 返回 `null`（Android 官方语义：不发任何响应字节，终端
  等待超时）；NFC 场保持存活；不影响 `ready/emulating` 状态上报，status 帧不加字段；
* 真机风险（双方知悉）：个别厂商 ROM 对 `return null` 行为可能有差异，联调需在
  真机+真实 POS 上验证终端确实超时；若异常再议备选方案；
* 第一期边界（host 定稿）：GET_DATA/VERIFY 仅需“静默本身”，**不要求**静默后
  恢复正常数据应答（app 内核现回 6D00）；后续需要（如 GET_DATA 返回 9F17）时
  host 另立协商项再实现内核 handler。

### 8.5 VISA GPO 携带 CID（N13 · **已实现、未部署**，卡端 1.0.32 `ea90149`，2026-09-10）

**状态：已实现未部署。** 在役（round #15 全绿）版本为卡端 **1.0.31**，**不含**本项；
N13 代码留在卡端分支（commit `ea90149`，标题 "implemented, not deployed"），
**未装机**。缘由：VISA 非接离线分支在卡端已给 TC 时仍 `END(102)`，POS 侧因此增加
"完成码 102 分支按 CID 兜底"；N13 让内核在 GPO 阶段即拿到 CID。
实现形态：VISA 的 GPO = `82/94/9F36` + **`9F27`**（按 `acBehavior`：AAC→`00`、TC→`40`、
ARQC→`80`）。

* **只加 CID、不带 `9F26`**：VISA GPO 携带 AC 曾在 1.0.24 触发内核 `-1304`；`9F10`
  仍只在 READ RECORD；
* **`VISA-CL-002`（AAC）必须仍返回 `9F27=00`**（回归红线：该用例须 declined）；
* 这是**联调约定、不是规范惯例**（VISA 规范通常不在 GPO 返回 CID）；
* **实施轨迹（2026-09-10）**：round #15 以卡端 **1.0.31（无 N13）** 跑出 **28/28 全绿**
  （POS 的 102 兜底走自身 `CONTINUE 7/33/26` 暂存 + `readTag(0x9F27)`，**不依赖本项**）；
  其后双方一度批准补入并实现为 **1.0.32 (vc34, `ea90149`)**，最终裁决**不上机部署** ——
  全绿基线在 1.0.31，`DISC-CL-001` 已由 POS 侧兜底解决，N13 非必需，且"GPO 只带 CID、
  不带 AC"的组合从未实测，不值得在全绿基线上引入未实测变更。
* **若将来启用**：一行启用 + 装机 + **VISA/OPBD 专项验证**（`VISA-CL-002` 必须仍 declined、
  不得出现 `-1304`），按 `[PAUSE-REQ]` 流程走。
* **-1304 依据（历史，双方核对）**：1.0.24 三次对照实验（GPO 无 AC→过；GPO 有 AC→`-1304`；
  GPO+记录都有→仍 `-1304`）+ trace `runs/2026-09-09/20260909T025029_ffea704a`
  （该 run `VISA-CL-001` = 80/declined）。故**只加 CID、绝不加 `9F26`**。
* 若将来启用后实测出现 `-1304`：撤销该行、重建回退（约 2 分钟），恢复 1.0.31 行为。

## 9. keys 包与 ac_behavior 规则（N2/O4/N8 定稿）

`card_config` 中 **keys 与 profile 并列**（keys 不在 profile 内）：

```json
"keys": {
  "symmetric_keys": [{"id": "mk1", "key_data": "<base64>"}],
  "rsa_keys": [{"id": "icc-key", "public_key": "<base64 DER>", "private_key": "<base64 PKCS#8>"}],
  "certificates": {"ca": "<base64>", "issuer": "<base64>", "icc": "<base64>",
                   "icc_private_key": "<base64>", "icc_exponent": "<base64>", "icc_remainder": "<base64>"}
}
```

首 GAC 行为（按 profile.acBehavior；缺省跟随终端 P1）：

| acBehavior | 首 GAC 应答 |
|---|---|
| `online_request` | ARQC |
| `offline_approved` | TC |
| `offline_declined` | AAC |

密文规则（N8）：**有对称密钥 → 真实 Retail MAC（任意 AC 类型，
`ac_crypto:"real"`）；无密钥 → 假密文（任意 AC 类型，8×0xFA，
`ac_crypto:"fake"`）**。环境假设：即使真实 ARQC，在线用例仍需测试收单
主机/卡 BIN 白名单，否则主机侧仍会拒绝。

## 10. 中断

* `stop_emulation`：app ack + `status{state:"stopped"}`，内核 reset，保留
  AID 注册与档案（H9）；重复 start 无需重发 card_config；
* status error（用例内）：当前用例 ERROR、整轮中止（reason `android_error`）；
* 安卓 TCP 断开：当前用例 ABORTED、整轮停止；POS 断开只告警，超时后当前
  用例 INCONCLUSIVE；浏览器断线不影响运行；
* 迟到/无主结果 → quarantine。

## 11. 参考实现

* host：`backend/emv_host/`（protocol/runner/store/web），启动
  `card_simulator_host` 或 `python -m emv_host serve`；
* mock 客户端（协议参照实现）：`tools/mock_clients/mock_android.py`
  （v1.2：ready/emulating/stopped、`card_config.trace_enabled`、`session_abort`、
  trace 演示、keys 感知）、`mock_pos.py`（`pos_kernel_config` + `session_abort`）；
* POS 侧通知文档：`docs/protocol-change-notice-v1.2.md`（只生成不发送）；
* 内核参数导出：`tools/export_kernel_params.py`（读 POS 资产，写到**仓库外**目录）。
* Expresspay XML 导入：`tools/import_expresspay_xml.py`（写**仓库外**命名 POS 配置库）。

---

## 12. v1.2 变更（本节优先于上文任何冲突表述）

> 实施状态：host 侧**已实现并已测**（`cd backend && python -m pytest` 全绿）；
> 卡端 1.0.33 (vc35) 待实施；POS 端待按通知文档实施。
> 两端与 host 必须**同窗口升级**。

### 12.1 版本协商（硬切换）

* `PROTOCOL_VERSION_STRING = "1.2"`；信封 `v` 仍为 `1`。
* 握手**必须**带 `protocol_version`：
  * 缺失 → `error{code:"protocol_mismatch"}` 并断开；
  * `< 1.2` → 同上；
  * `>= 1.2` → 接受，`handshake_ack.protocol_version="1.2"`。
* 不提供任何 1.1 兼容模式。

### 12.2 删除与新增帧

| 变化 | 内容 |
|---|---|
| **删除** | `config_push{config_type:"device_config"}` 不再发往任何端。心跳/判死/ack 超时改用硬编码缺省 **10s / 30s / 15s** |
| **新增** | `config_push{config_type:"pos_kernel_config"}`（host→POS，AID/CAPK/TERM/DRL/ExceptionFile） |
| **新增** | `session_abort`（host→双方，异常收尾通知） |
| 不变 | `card_config`（卡端）、`pos_config`（POS 端，字段/编码冻结） |

### 12.3 `card_config` 增 `trace_enabled`

```json
{"type":"config_push","config_type":"card_config","case_id":"...","attempt_id":"...",
 "profile":{...},"keys":null,"timeout_seconds":180,"trace_enabled":false}
```

* 卡端从**此处**读取 trace 开关（v1.1 是从 `device_config` 读）；
* 缺省 `false`；非布尔按 `false` 处理。

### 12.4 `pos_kernel_config`（详见 POS 通知文档 §4）

```json
{"type":"config_push","config_type":"pos_kernel_config","case_id":"...","attempt_id":"...",
 "kernel_params":{"aid":["<拼接BER-TLV hex>"],"capk":["..."],"drl":["<9F06+8F+DF8105 hex>"],"exception_file":["<5A+5F34 hex>"],"term":"<多个TLV拼接 hex>"}}
```

* 每案例下发，且**先于** `pos_config`；
* `pos_params` 整体缺失/为空 → 不下发，POS 用自带表；
* `term` 是**多个 TLV 直接拼接**的 hex（不是单条）；
* `drl[]`（Amex 文件内名为 CRL）每条 `9F06`+`8F`+`DF8105`，`exception_file[]` 每条 `5A`+`5F34`，均为拼接 BER-TLV；
* `version` 是 host 侧元数据（Amex 为 `"4.2.0"`），**不上 wire**；
* AID `9F06` 5–8 字节、首元素固定；CAPK 元素顺序固定
  `9F06‖9F22‖DF05‖DF06‖DF07‖DF02‖DF04‖DF03`；
  `DF03 = SHA1(9F06‖9F22‖DF02‖DF04)`；
* DF05 支持 `ascii8`（默认）|`bcd4`|`bcd3`|`raw`；
* 同一 RID 内容不同时必须**真正覆盖**（DELETE-then-ADD），不得静默跳过；
* 数据来自**仓库外**目录（`serve --kernel-params-dir`）：`<scheme>.json` 为卡组默认，
  `<scheme>/<ref>.json` 为命名 POS 配置库（case 通过 `kernel_params_ref` 引用）；
  `tools/import_expresspay_xml.py` 从 Expresspay XML 生成（Amex `version:"4.2.0"`）。仓库内不含密钥材料。

### 12.5 `session_abort`

```json
{"type":"session_abort","run_id":"...","scheme":"visa","mode":"contactless",
 "case_id":"...","attempt_id":"...","reason":"case_failed|result_timeout|...","action":"stop"}
```

* host→卡端与 POS 端**各发一次**；双方**必 ack**（host 只等 3s，best-effort，不阻塞收尾）；
* 卡端须**视同停止**（停 HCE），但**不补** `status{stopped}`（卡端裁决 3.2b）；
* 触发：案例 FAIL（断言/超时/断线/内核参数被拒等）、manual abort/stop、server_shutdown；
* **正常跑完不发**。

### 12.6 ack 矩阵（host 侧强制）

| 帧 | 方向 | ack | host 超时 | 超时/失败后果 |
|---|---|---|---|---|
| `card_config` | →卡 | 必 | 15s | 案例 FAIL `card_config_ack_timeout` / `card_config_rejected` |
| `start_emulation` | →卡 | 必 | 15s | 案例 FAIL `card_emulation_ack_timeout` / `..._rejected` |
| `status` emulating | ←卡 | host 等 | 15s | 案例 FAIL `card_ready_timeout` |
| `pos_kernel_config` | →POS | 必 | 15s | 案例 FAIL `pos_kernel_config_ack_timeout` / `..._apply_failed` |
| `pos_config` | →POS | 必 | 15s | 案例 FAIL `pos_config_ack_timeout` / `pos_config_rejected` |
| `case_started` | →POS | 必 | 15s | 案例 FAIL `pos_case_started_ack_timeout` / `..._rejected` |
| `transaction_result` | ←POS | host **落盘后** ack | `case.timeout_seconds` | 案例 FAIL `result_timeout` |
| `stop_emulation` | →卡 | 必 | 3s | 仅日志 |
| `session_abort` | →双方 | 必 | 3s | 仅日志 |
| `heartbeat` | ↔ | `heartbeat_ack` | 10s | 30s 警告，60s 断开 |

* 任一端未连接 → 该案例 FAIL（`android_not_connected` / `pos_not_connected`）；
* ack 预算可用 `serve --card-ack-timeout` / `--pos-ack-timeout` 调整（默认 15s）。

### 12.7 `transaction_result` 的 ack 语义（D2）

* host **先落盘判定结果、再 ack**；因此 ack 表示「判定已落盘」：
  `persisted` / `duplicate` / `quarantined` / `ok:false{detail:"persist_failed"}`；
* 收到 `ok:true` 后**不要重发**；只有 `ok:false` 才需换新 `msg_id` 重试；
* 去重窗口 = **当前 attempt**（`begin_attempt` 重置 `judged_msg_ids`）；
  跨案例晚到 → `quarantined` 并写入 `quarantine.jsonl`。

### 12.8 失败即整轮停止（Q9）

* 任一案例 FAIL → **立即停止整轮**；剩余队列逐条落 `SKIPPED`
  （`note=run_stopped_after_failure:<case_id>`）；
* run 级 `abort_reason` 统一为 `case_failed:<case_id>:<failure_reason>`（F2）；
* POS 断线由 v1.1 的「仅告警」改为**案例 FAIL**（`pos_disconnected`）；
* 卡端错误 → 案例 **ERROR**（保留结构化 `error{code,message}`），run 状态 `ERROR`。

### 12.9 `stop_emulation` 的发送时机（H2）

* 一旦卡端上报 `emulating`，其后**任何**退出路径都必须发一次 `stop_emulation`
  （含 `pos_kernel_config`/`pos_config`/`case_started` 失败、结果超时、断言 FAIL、手动 abort）；
* 未到 `emulating` 的失败（卡端未连接、`card_config` 被拒、就绪超时）**不发**；
* 每个 attempt 至多一次。

### 12.10 坏帧规则

* **JSON 非法但长度前缀合法**：计入累计计数、跳过继续读；**累计满 5** 才断连；
* **长度非法**（声明长度 > 1 MiB）：**立即断开**，不计入累计 5；
* v1.1 的「成功帧重置计数」已取消（卡端 K6 同步改）。

### 12.11 落盘契约

* `results/<case_id>.json` 同时含 `attempt`（0 基）与 `attempts`（1 基计数）（F3）；
* 所有 run 产物用「同目录 `.tmp` + `os.replace`」原子写入，不留 `.tmp`（T11）；
* case 记录含 `failure_reason`；run 索引同步该字段。

### 12.12 运行配置新增

| 参数 | 默认 | 说明 |
|---|---|---|
| `--card-ack-timeout` | 15 | 卡端 ack 预算（秒） |
| `--pos-ack-timeout` | 15 | POS 端 ack 预算（秒） |
| `--kernel-params-dir` | 无 | 仓库外内核参数目录（每 scheme 一个 `<scheme>.json`） |

### 12.13 测试基线

* host 侧 `cd backend && python -m pytest`：**182 passed**；
* 测试方案与修订：`docs/test-plan-v1.2.md`（含 P-D1..P-D7 修订）；
* L5/L6 真机联调**阻塞**：需两端升级到 1.2 并各自发 `[READY]`。
