# host-POS 协议 v2（Mastercard CTEL2 TEI 兼容）设计冻结稿

> 状态：**v2 设计冻结稿（P1/P1c-1 已实施；本文已与 2026-09-18 的实现对齐）**。实施从 P1 起，**替换 v1.2、不并存**。
> 依据：`refernce/m_ctel2_en-us-2025-06-17.pdf`（CTEL2 v4.1，TEI 接口 p78-93，PCD-bypass p94-99）
> + `refernce/OPS_description_1.png` / `OPS_description_2.png`（OPS 位表）+ 用户 R1-R7 裁决。

## 0. 目标与范围

- POS 侧报文重构为 CTEL2 TEI 消息集：`TEST_INFO / CONFIG / ACT / MSG / OUT` + 对应 ACK。
  R1=b：**只换消息格式，TCP 角色保持现状**。
- 判定源：由「POS `transaction_result`」改为「**卡端全量 APDU** + **OUT(OPS/DataRecord/DiscData)**」。
- 卡端链路（`card_config` / `start_emulation` / `stop_emulation` / `status`）**保留不动**（R3），
  但卡端必须**按案例**上送**完整 APDU**（R3/R4=a）。
- 卡端 token 白名单扩展（见 `docs/change-notice-v2-card.md`）。
- **不与 v1.2 并存**（R7）：硬切换，删除 v1.2 的 POS 报文。

## 1. 传输层（沿用现有，仅版本号升级）

- host = TCP 服务端；`android` / `pos` = 客户端；端口 `9100`（Web/API/WS `8000`）。
- 帧：`4B 大端长度 + UTF-8 JSON`，最大 1MiB；坏帧累计 5 断连。
- 信封：`{"v":2,"type":"<小写消息类型>","msg_id":"<uuid>","ts":<epoch>}`（v 由 1 升 2；Q2 已定稿，见 §11）。
- 握手：`handshake{client_type, token, device_id, app_version, device_model, protocol_version:"2.0"}`；
  缺失或 `<2.0` -> `error{code:"protocol_mismatch"}` 并断开（确定性失败）。
- 心跳沿用（host 10s / 告警 30s / 断 60s）；CTEL2 未定义心跳，保留为传输层机制（待评审 Q5）。

## 2. POS 消息集（CTEL2 TEI）

### 2.1 host -> POS

| 帧 | 结构 | 说明 |
|---|---|---|
| `test_info` | `{"signalData":[{"id":"TestId","value":"CPO-PROC94-XP2"},{"id":"InterfaceVersion","value":"20250617"},{"id":"SessionId","value":"<run_id>"},{"id":"DekDetId","value":null}]}` | TestId 必填；其余可 null |
| `config` | `{"signalData":[...]}` | R2=b：内嵌完整配置，见 4 |
| `act` | `{"signalData":[{"id":"9F02","value":"000000001500"}, ...]}` | 交易数据，见 5 |

### 2.2 POS -> host

| 帧 | 结构 |
|---|---|
| `test_info_ack` | `{"signalData":[{"id":"ResponseCode","value":"YES"}]}`，或 `"NO"` + 可选 `{"id":"Description",...}` |
| `config_ack` | 同上 |
| `act_ack` | `{"signalData":[{"id":"ResponseCode","value":"YES"\|"NO"\|"NOTSUPPORTED"}]}` |
| `msg` | `{"signalData":[{"id":"UIRD","value":"<13B hex>"}]}`；一次 ACT 可 0..n 条，按发生顺序 |
| `out` | `{"signalData":[{"id":"OPS","value":"<8B hex>"},{"id":"DataRecord","value":"<TLV>"\|null},{"id":"DiscData","value":"<TLV>"\|null},{"id":"UIRD","value":null},{"id":"KernelId","value":"..."}]}`；一次 ACT 可 1..n 条 |

> wire `type` 用小写 snake（`test_info`/`config`/`act`/`msg`/`out` 与 `*_ack`），与现有信封风格一致；
> CTEL2 的大写 signalType 仅作语义对照（待评审 Q1）。

### 2.3 时序

```
host: test_info  ->  pos: test_info_ack
host: config     ->  pos: config_ack
host: act        ->  pos: act_ack
pos:  msg*（可选，按发生顺序）
pos:  out（>=1；dual tap 可多条）
```

- host 等每个 ACK 超时（默认 15s，可配）-> 案例 FAIL；
- `act_ack=NOTSUPPORTED` -> 案例 FAIL（本测试要求支持 ACT 交易数据）；
- host 收到第 1 条 `out` 即结束该 ACT 等待；迟到 `out` -> quarantine；
- 一次 `test_info` 可对应多个 `act`（同测试多笔交易），`config` 可被多个 ACT 复用。

## 3. host 状态机

`HANDSHAKE -> READY ->〔每案例〕TEST_INFO_SENT -> CONFIG_SENT -> ACT_SENT -> WAIT_MSG_OUT -> DONE`
任一 ACK 超时 / `NO` -> FAIL；收到 `out` 后由断言引擎判 PASS/WARN/FAIL。

## 4. CONFIG 内容（R2=b）

`config.signalData`（id 由 host 定义，value 为 hex）：

**形态（Q6 已定稿，POS「v2 变更确认回复」6.2 对齐）**：**同名 id 逐条展开为权威**；
不使用「单 item 值 = 数组」的形态。host 已按此实现（`executor._config_frame`）。

| id | 值 | 对应 v1.2 |
|---|---|---|
| `CONF_NAME` | `"<brand>/<ref>"`（信息用，如 `amex/CONF_16`） | - |
| `AID` | 每条一个 item：一条拼接 BER-TLV 大写 hex | `kernel_params.aid[]` |
| `CAPK` | 每条一个 item：一条拼接 BER-TLV | `capk[]` |
| `DRL` | 每条一个 item：`9F06+8F+DF8105` 拼接 TLV | `drl[]` |
| `EXCEPTION_FILE` | 每条一个 item：`5A+5F34` 拼接 TLV | `exception_file[]` |
| `TERM` | 一条拼接 TLV hex（= CONF.term + PROFILE.term） | `term` |
| `VERSION` | `"4.2.0"` 等（host 侧元数据） | `version` |

**空组语义（D1 已定稿）**：`DRL` / `EXCEPTION_FILE` **每笔恒发**（host 已实现）：

- 非空 → 逐条 `{id:"DRL", value:"<tlv>"}`，语义 = 整表替换（R2=b）；
- **空 → 发一条 `{id:"DRL", value:""}`，语义 = 清空该表且不加**（`CONF_01_RemoveCRL` 依赖此语义，
  否则按 POS「缺失 = 不管理」会残留上一笔的 DRL/黑名单）；
- `value:null` 或 id 缺失 = 本笔不管理该表；
- `AID` / `CAPK` 非空时逐条发；本轮不出现「空 AID/CAPK」。

## 5. ACT 交易数据

| id | 名称 | 编码 | 示例 |
|---|---|---|---|
| `9F02` | Amount Authorized (Numeric) | 6B BCD | `000000001500` |
| `9F03` | Amount Other (Numeric) | 6B BCD | `000000000000` |
| `5F2A` | Transaction Currency Code | 2B（n3，BCD） | `0978` |
| `5F36` | Transaction Currency Exponent | 1B | `02` |
| `9A` | Transaction Date | 3B BCD YYMMDD | `240412` |
| `9F21` | Transaction Time | 3B BCD HHMMSS | `133333` |
| `9C` | Transaction Type | 1B | `00` |
| `9F53` | Transaction Category Code | 1B | 可 null |
| `5F57` | Account Type | 1B | 可 null |
| `9F7C` | Merchant Custom Data | 20B | 可 null |
| `DF8112` | Tags To Read (DEDS) | Var | 可 null |
| 私有 tag | - | - | 允许 |

- value 一律 hex 字符串；null = 不发该 tag；`""` = 长度 00；
- 金额小数点位由 `5F36` 货币指数决定（R6）；
- `9F1A`（Terminal Country）不在 ACT，属于终端参数（CONFIG.TERM），由 CONFIG 下发（R6）。

## 6. OUT 解析

### 6.1 OPS（Outcome Parameter Set，DF8129，8B）

> 位表来源：`refernce/OPS_description_1.png` / `_2.png`。host 解码为 `out.ops.*` 供断言。

| 字节 | 位 | 字段 | 取值 |
|---|---|---|---|
| Byte1 | b8-5 | Status | `0001` APPROVED；`0010` DECLINED；`0011` ONLINE REQUEST；`0100` END APPLICATION；`0101` SELECT NEXT；`0110` TRY ANOTHER INTERFACE；`0111` TRY AGAIN；`1111` N/A；其它 RFU |
| Byte1 | b4-1 | RFU | - |
| Byte2 | b8-5 | Start | `0000` A；`0001` B；`0010` C；`0011` D；`1111` N/A；其它 RFU |
| Byte3 | b8-5 | Online Response Data | `1111` N/A；其它 RFU |
| Byte4 | b8-5 | CVM | `0000` NO CVM；`0001` OBTAIN SIGNATURE；`0010` ONLINE PIN；`0011` CONFIRMATION CODE VERIFIED；`1111` N/A；其它 RFU |
| Byte5 | b8 | UI Request on Outcome Present | 0/1 |
| Byte5 | b7 | UI Request on Restart Present | 0/1 |
| Byte5 | b6 | Data Record Present | 0/1 |
| Byte5 | b5 | Discretionary Data Present | 0/1 |
| Byte5 | b4 | Receipt | 0 N/A；1 YES |
| Byte6 | b8-5 | Alternate Interface Preference | `1111` N/A；其它 RFU |
| Byte7 | b8-1 | Field Off Request | `FF` N/A；否则 hold time（x100ms） |
| Byte8 | b8-1 | Removal Timeout | x100ms |

示例：CTEL2 的 `30F0F01038F0FF00` = Status ONLINE REQUEST / Start N/A / OnlineResp N/A /
CVM OBTAIN SIGNATURE / DataRecord+DiscData+Receipt / AIP N/A / FieldOff 0 / RemovalTimeout 0。

host 派生：`outcome` = Status（APPROVED 记 approved / DECLINED 记 declined / ...）；
`online` 由 Status 与 Online Response Data 推；`resp_code` 派生口径（Q9 已定稿，见 D2(a)）：
优先 `DataRecord.8A`；无 `8A` 再按 OPS Status（`APPROVED`→`00`、`DECLINED`→`05`、`ONLINE REQUEST`→`00`）；
**内核未跑**（OPS 全 0 + 空 DataRecord）→ `resp_code` 为空串、判该用例 FAIL（`out_no_outcome`），不伪造 `05/91/80`。

### 6.2 DataRecord

**host 约定白名单 + 「取到才报」（已定稿，替代原「TLV DB 全量」）**：
POS 侧 `CreateEMVDataRecord()` 只导出 host 约定的白名单 tag（KS8226 无全量导出 API）；
取不到的 tag **不出现**在 `DataRecord` 中。host 解成 `out.datarecord.<tag>`；
OPS Byte5 b6=0 或值为 null 时该源为空。

白名单 **32 条**（31 条 + `9F66`，后者为 `QPB-B-002-01` 断言 `out.datarecord.9F66` = 本笔下发的 TTQ 所需；
POS 保留「`CONFIG.TERM` 含 `9F66` 时用下发值覆盖内核回读值」的行为）。

> `9F02` / `9F03` / `9F53` 允许出现在 `DataRecord`（在白名单内，已确认）。

### 6.3 DiscData / UIRD / KernelId

- `DiscData`：POS 私有 TLV -> `out.discdata.<tag>`。**本轮 host 不断言 `out.discdata.*`**，
  POS 可不报；将来若要用，另给 tag 清单；
- `UIRD`：取 `DF8116`（13B hex），取不到则全 0（与 `msg` 同构）；
- `KernelId`：Kernel Identifier-Terminal -> `out.kernelid`；**本轮 POS 不上报**。

## 7. 卡端链路（保留 + 全量 APDU）

- `card_config{profile, keys\|null, timeout_seconds}` / `start_emulation` / `stop_emulation` / `status` 不变（R3）。
- **APDU trace（R4=a）**：沿用 `log{level:"trace", message:{...}}`，语义升级：
  - **恒开**：不再依赖 `trace_enabled`（或 host 恒置 true）；
  - **按案例、不合并**（R3）：每帧带 `case_id` + `attempt_id`；卡端在 `card_config`/`start_emulation`
    后重置 APDU `seq`，案例结束（`stop_emulation` 或 `status:stopped`）封口；任何 APDU 不得跨案例合并；
  - host 按 `attempt_id` 落盘 `runs/<run_id>/trace/<case_id>.jsonl`，并**作为判定源**（废除「永不判定」）；
  - trace 缺失/串案 -> 案例 FAIL（`card_apdu_missing` / `card_apdu_bleed`）。

### 7.1 APDU 断言路径（R5）

| 路径 | 含义 |
|---|---|
| `card.apdu.<seq>.command` / `.response` | 原始 CAPDU / RAPDU hex（含 SW） |
| `card.gpo.pdol.<tag>` | GPO（`80 A8`）命令数据按卡 profile `pdol` 反解出的 tag 值 |
| `card.gac.cdol1.<tag>` | GAC（`80 AE`，P1=0x40）命令数据按卡 profile `cdol1` 反解 |
| `card.gac.cdol2.<tag>` | P1=0x00 的 GAC 按 `cdol2` 反解 |

反解依据 = case.profile 的 `pdol`/`cdol1`/`cdol2`（host 已知所配卡档案）。
本例：`card.gpo.pdol.9F35 = "E2"`、`card.gac.cdol1.9F35 = "22"`。

## 8. 判定与失败原因

- `expected_results` 支持三类路径：`card.*`（7.1）、`out.*`（6）、派生字段 `outcome`/`online`/`resp_code`；
- 匹配算子沿用：`eq/neq/contains/prefix/regex/any_of/present/absent/range`；
- 新失败原因码：
  - `test_info_ack_timeout` / `test_info_rejected` / `test_info_error`
  - `config_ack_timeout` / `config_rejected` / `config_error`
  - `act_ack_timeout` / `act_rejected` / `act_not_supported` / `act_error`
  - `out_timeout`
  - `pos_not_connected`（POS 未连接时任一 signal 未确认）
  - `out_no_outcome`（OPS 全 0 + 空 DataRecord = 内核未跑出结果，见 6.1 D2(a)）
  - `card_apdu_missing` / `card_apdu_mismatch:<path>` / `card_apdu_bleed`
  - `out_ops_mismatch:<path>` / `out_datarecord_missing:<tag>`
- 任一端断线 -> 案例 FAIL；整轮停止策略沿用（任一 FAIL 即停止）。

## 9. 案例 JSON 变化

- `transaction.pos_params` -> **ACT 交易数据**：每项 `{id, value}`（hex，见 5）；
  待评审 Q4：是否保留 `pos_params` 命名、只把 value 改成 hex；
- `expected_results` 路径扩展（8）；
- `kernel_params_ref` / `terminal_profile_ref` 保留，喂 CONFIG；
- `operator_instructions` 保留（人工提示，可随 `test_info` 一起给人看）。

## 10. 移除（v1.2 POS 报文）

`config_push{device_config}`、`config_push{pos_config}`、`config_push{pos_kernel_config}`、
`case_started`、`transaction_result`、`session_abort`。
待评审 Q5：`session_abort` 是否保留为传输层异常通知（CTEL2 无对应信号）。

## 11. 开放问题（评审状态，2026-09-18 更新）

> 状态口径：**已定稿**项以 `backend/emv_host/protocol/messages.py` 与 `runner/executor.py` 的实际实现为准
> （P1/P1c-1 已实施）。仍未决的项在末尾注明。

1. wire `type` 用大写 CTEL2 名还是小写 snake？—— **已定稿：小写 snake**
   （`messages.py:51-59`：`test_info`/`config`/`act`/`test_info_ack`/`config_ack`/`act_ack`/`msg`/`out`）。
2. `protocol_version` 是否 `"2.0"`？信封 `v` 是否升 2？—— **已定稿：均为是**
   （`messages.py:22,27`；且入站 `v` 已加校验，见 `protocol/connection.py`）。
3. CONFIG 的 id 命名 —— **已定稿**：`CONF_NAME/AID/CAPK/DRL/EXCEPTION_FILE/TERM/VERSION`（`messages.py:63-69`）。
4. ACT 是否沿用 `transaction.pos_params` 命名？—— **已定稿：不沿用，改用 `TransactionParams.act`**
   （`cases/models.py:474`；`pos_params` 仅作 legacy 保留且**不使用**，见 `models.py:472` 注释）。
5. `session_abort` 与 heartbeat 的去留？—— **已定稿**：`session_abort` **删除**（§10 硬切换）；
   heartbeat 保留为传输层机制（CTEL2 未定义，`messages.py:36-37`）。
6. CONFIG 的 AID/CAPK 形态？—— **已定稿：同名 id 逐条展开**（§4，POS 6.2 对齐）。
7. 卡端 trace 恒开后 `trace_enabled` / `device_config` 如何处置？—— **部分**：`device_config` 已删除
   （`messages.py:9-10`）；`trace_enabled` 由 `card_config` 承载（`executor.py:697-698` 注释）。
8. **前端改动范围（先最小可用 vs 一步到位）—— 仍未决**，需负责人裁决。
9. `resp_code` 派生口径 —— **已定稿**（见 §6.1 D2(a)）：`DataRecord.8A` 优先，其次 OPS Status。
10. `out.datarecord` 与 `card.*` 断言的优先级/组合语义 —— **部分**：`out.*` 已实施；
    `card.*`（APDU 判定）属 **P2，尚未实施**。

