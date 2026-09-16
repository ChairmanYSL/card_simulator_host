# POS 端 v1.2 协议变更通知

> **术语更正（与 v1.2 同窗口）**：帧内的卡组字段由 `scheme` 更名为 **`brand`**（取值不变）。详见 `docs/protocol-change-notice-brand.md`。

> **对象**：POS 应用（`com.zt.pos`，E:\Work\ZT\KS8226\L2-SELFTEST-APP）
> **来源**：host 仓库 `E:\Work\ZT\card_simulator_host`
> **日期**：2026-09-14
> **性质**：**只生成，不发送**——由用户转交 POS 端。
> **前置**：POS 端当前为 v1.1（`pos_config` 已实现）。v1.2 是**硬切换**，见第 1 节。

---

## 1. 硬切换与握手（先读这一节）

* 客户端握手必须上报 `protocol_version: "1.2"`。
* host 现在**拒绝**缺失 `protocol_version` 的握手，也拒绝任何 `< 1.2` 的值：
  回 `error{code:"protocol_mismatch"}` 后**断开**。
* 信封 `v` 仍为 `1`（未变）。
* 因此**两端必须同窗口升级**。旧 POS + 新 host 只会得到 `protocol_mismatch`，
  不会降级兼容。
* host 的 `handshake_ack.protocol_version` 回 `"1.2"`。

POS 端握手帧（示例）：

```json
{"v":1,"type":"handshake","msg_id":"...","ts":0,"client_type":"pos",
 "token":"<tokens.json 里的 pos token>","device_id":"<设备号>",
 "app_version":"<版本>","protocol_version":"1.2"}
```

---

## 2. 冻结项（不得改名/改编码）

| 项 | 冻结值 |
|---|---|
| 内核参数帧的 `config_type` | **`pos_kernel_config`**（**不是** `kernel_params`） |
| 内核参数负载字段名 | `kernel_params: {aid:[...], capk:[...], drl:[...], exception_file:[...], term:"..."}`（`drl`/`exception_file` 为 v1.2.x 新增，`version` 仅 host 侧） |
| 交易参数帧 | `config_type="pos_config"`，`pos_params:[{tag,name,value}]`，字段名与编码不变 |
| 结果帧 | `type="transaction_result"` + `case_id` + `attempt_id` + `result` |
| 信封 | `v=1` |

---

## 3. 帧时序（host → POS）

一个案例内，POS 会按固定顺序收到（**每一步都必须 ack**）：

```
（卡端就绪后）
1. config_push{config_type:"pos_kernel_config", case_id, attempt_id, kernel_params}
2. config_push{config_type:"pos_config",         case_id, attempt_id, pos_params}
3. case_started{case_id, attempt_id, brand, entry_mode, operator_instructions}
   ← 之后由 POS 操作员完成交易
4. POS → host: transaction_result{case_id, attempt_id, result, device_id?}
```

要点：

* 第 1 步**可选**：host 只有在该 brand 配了内核参数集时才发；没配就完全不发这一帧，
  POS 应继续用自带表。负载里的 `aid`/`capk` 为空数组时同样视为「无可下发」。
* 第 1 步始终在 `pos_config` **之前**。
* 第 2 步在案例的 `pos_params` 非空时才发。
* 第 3 步的 ack 现在是**强制**的（v1.1 时代 host 不等待）。
* host 不再发送 `device_config`（v1.2 已删除）；POS 端也不依赖它，心跳/判死/ack 超时用缺省
  10s / 30s / 15s。

---

## 4. `pos_kernel_config` 规格

### 4.1 帧格式

```json
{"v":1,"type":"config_push","msg_id":"...","ts":0,
 "config_type":"pos_kernel_config","case_id":"VISA-CL-001","attempt_id":"...",
 "kernel_params":{
   "aid":  ["9F0607A0000000031010DF0101009F08020002...", "..."],
   "capk": ["9F0605A0000000039F2201...", "..."],
   "drl":  ["9F0605A0000000258F0101DF810503001000", "..."],
   "exception_file": ["5A08373737345678904F5F340100"],
   "term": "9F6604360000809F1A0201569F7A0101"
 }}
```

* `aid[]` / `capk[]` / `drl[]` / `exception_file[]` 每项是**拼接 BER-TLV 的大写 hex，无外层模板**；
* `term` 是**多个 TLV 直接拼接**的 hex（不是单条）；
* 长度编码：`<= 0x7F` 短形式、`0x80-0xFF` 用 `81 xx`、`0x0100-0xFFFF` 用 `82 xx xx`（CAPK 模数上限 256 字节）；
* `version` 是 host 侧元数据（Amex Expresspay 为 `"4.2.0"`），**不在 wire 上**。

### 4.2 逐条调用（顺序即数组顺序）

```
for x in kernel_params.aid:            updateAidParam(1, bytes.fromhex(x))
for x in kernel_params.capk:           updateCAPKParam(1, bytes.fromhex(x))
for x in kernel_params.drl:            <POS SDK DRL API>(bytes.fromhex(x))
for x in kernel_params.exception_file: <POS SDK ExceptionFile API>(bytes.fromhex(x))
if kernel_params.term:                 setTerminalParam(bytes.fromhex(term))
```

全部成功 → `ack{ok:true}`；任一条失败 → `ack{ok:false, detail:"<原因>"}`。

### 4.3 AID 条目

* 首元素固定 `9F06`，值为 **5–8 字节**的 AID/RID；
* 其后元素**按 host 给出的顺序原样写入**（host 保留资产里的 DFxx 顺序，内核接受）；
* host 不会在 `aid` 条目里给出第二个 `9F06`。

真实样例（VISA `A0000000031010`，取自 POS 资产）：

```
9F0607A0000000031010DF0101009F08020140DF1105D84000A800DF1205D84004F800
DF130500100000009F1B04000186A0DF150400000000DF160199DF170199DF14039F3704
DF180100
```

### 4.4 CAPK 条目

元素顺序**固定**如下（与 `app/src/main/assets/emv/params` 的 `CAPK=` 段一致）：

| 顺序 | tag | 长度 | 含义 |
|---|---|---|---|
| 1 | `9F06` | 5B | RID |
| 2 | `9F22` | 1B | 密钥索引 |
| 3 | `DF05` | 见 4.5 | 失效期 |
| 4 | `DF06` | 1B | 哈希算法（POS 资产恒 `01`；Expresspay 为 `01`） |
| 5 | `DF07` | 1B | 密钥算法（POS 资产恒 `01`；**Expresspay 为 `00`**） |
| 6 | `DF02` | <=256B | 模数（裸字节；Expresspay 用到 126/128/160/176/192/224/248B） |
| 7 | `DF04` | 1-8B | 指数（如 `03` / `010001`） |
| 8 | `DF03` | 20B | 校验和 |

**`DF03` 算法（POS 可自行复核）**：

```
DF03 = SHA1( 9F06值 ‖ 9F22值 ‖ DF02值(裸字节) ‖ DF04值 )
```

注意：**不含 `DF05`**。因此 DF05 的编码方式**不影响** DF03。

### 4.5 `DF05` 的四种编码（v1.2 新增支持）

host 现在支持四种 DF05 写法，POS 端 `updateCAPKParam` **只接收字节、不解释 DF05**，
所以 POS 无需为编码改代码——但**内核**必须能接受下发的形状。
默认（且与现有资产完全一致）是 `ascii8`：

| 编码 | 线上 DF05 字节数 | 示例：`20441231` | 说明 |
|---|---|---|---|
| `ascii8`（默认） | 8 | `3230343431323331` | 8 位 ASCII 数字，**现资产口径** |
| `bcd4` | 4 | `20441231` | 压缩 BCD：年 2B + 月 1B + 日 1B |
| `bcd3` | 3 | `441231` | 压缩 BCD：年(YY) 1B + 月 1B + 日 1B |
| `raw` | 任意偶长 | 原样透传 | 非日期的厂商样例（host 侧只报 warning） |

* `bcd3`/`bcd4` 只能表达 **2000–2099**；
* host 默认**不下发** `RID=A000000333 / 9F22=08`（其资产 DF05 为 `PHWWIPQI`，不是日期），
  因此默认 CAPK 条数是 **28**（资产 29 减去这 1 条）。只有在确认必须原样下发时，
  host 才会以 `raw` 形式给出该条；
* **请确认目标内核实际接受哪种形状**，并把结论回报 host；默认按 `ascii8` 执行。

### 4.6 幂等与去重（重要）

* AID 去重键 = `9F06` 全值；CAPK 去重键 = `9F06 ‖ 9F22`。
* 重复收到**相同**条目不得报错（`ok:true` 即可）。
* **同一 RID 但内容不同**时必须真正生效（见 4.7），不能静默跳过。
* 不要因为本次下发就递增 `AID_PARAM_VERSION` 或触发「清空重灌」——那会清掉出厂 AID/CAPK
  （POS 现有实现已对此有注释说明）。

### 4.7 跨案例覆盖语义（**本轮重点**）

host 按**每个案例**下发 AID/CAPK 全量。同一 RID 在两个案例里可能带不同的 AID 元素
（如 `DF11`/`DF12`）。若 POS 沿用 v1.1 的「同 RID 已存在就跳过（add-only）」：
**案例 2 会静默使用案例 1 的 AID 体，而 host 收到 `ok:true` → 误判**。

因此 v1.2 要求：

> 对 host 下发的**受管 AID**，若内核中同 `9F06` 已存在但**内容不同**，
> 必须先 `DELETE` 再 `ADD`（或等价的原地更新），并把结果如实回报。

已知约束（POS 现有实现里有记录）：`updateAidParam(DELETE)` 会在 `emvaids0.par` 留下空洞、
导致下次启动只加载部分条目。若走 DELETE+ADD，请确保恢复该文件的一致性（或采用内核支持的
原地更新路径），并在回报中说明采用的做法。CAPK 同理（同 RID+索引、内容不同时需覆盖）。

### 4.8 `drl[]` 与 `exception_file[]`（v1.2.x 新增，Amex Expresspay 4.2.0）

Expresspay 的 `TerminalConfig/*.xml` 每份还含 CRL（host 侧字段名 `drl`）与 ExceptionFile：

| 数组 | 元素 TLV 顺序 | 长度 |
|---|---|---|
| `drl[]` | `9F06`(RID) + `8F`(index) + `DF8105`(SN) | 5B / 1B / 样例 3B |
| `exception_file[]` | `5A`(PAN) + `5F34`(PSN) | 样例 8B / 1B |

* 两者与 `aid[]`/`capk[]` 一样是**拼接 BER-TLV、无外层模板**，逐条调用 POS SDK 的 DRL /
  ExceptionFile API（具体 API 名以 POS SDK 为准）；
* DRL 去重键建议 `9F06 + 8F + DF8105`，ExceptionFile 去重键建议 `5A`；
* `version:"4.2.0"` 只在 host 侧保留，不随帧下发。
* CONF/PROFILE 的模块拆分只发生在 host 侧；POS 收到的仍是合成后的完整 `kernel_params`。

---

## 5. `pos_config`（不变，但 ack 变成强制）

```json
{"v":1,"type":"config_push","msg_id":"...","ts":0,
 "config_type":"pos_config","case_id":"VISA-CL-001","attempt_id":"...",
 "pos_params":[{"tag":"9F02","name":"amount","value":"100.00"},
               {"tag":"5F2A","name":"currency","value":"156"}]}
```

* 字段名/编码**完全不变**：`tag` 为 1–3 字节大写 hex，`value` 为字符串，`name` 仅展示；
* 未知 tag：**忽略并记日志**（host 不强制白名单，只校验 tag 格式与重复）；
* 未收到 ack / ack 失败 → 该案例直接判 **FAIL**（v1.1 只是 warning）。

---

## 6. `case_started`（现在必须 ack）

```json
{"v":1,"type":"case_started","msg_id":"...","ts":0,
 "case_id":"VISA-CL-001","attempt_id":"...","brand":"visa",
 "entry_mode":"contactless","operator_instructions":{...}}
```

* 收到后请**立即 ack**（此时 host 已在等，超时 15s）；
* 之后按 `operator_instructions` 提示操作员完成交易；
* ack 失败/超时 → 案例判 **FAIL**（`pos_case_started_ack_timeout`）。

---

## 7. `transaction_result`（ack 的含义变了）

```json
{"v":1,"type":"transaction_result","msg_id":"...","ts":0,
 "case_id":"VISA-CL-001","attempt_id":"<必须回填 host 给的值>",
 "device_id":"...","result":{"resp_code":"00","outcome":"approved",
   "online":"offline","tags":{...}}}
```

* **`case_id` 与 `attempt_id` 必须与 `case_started`/`pos_config` 里收到的一致**。
  只回填 `case_id` 不够：`attempt_id` 是归属判据，不匹配会被隔离（quarantine）。
* host 的 ack 现在表示 **「判定结果已落盘」**，不是「已收到」：

  | ack detail | 含义 | POS 应做什么 |
  |---|---|---|
  | `persisted` | 已落盘，判定生效 | 正常，不要重发 |
  | `duplicate` | 该 `msg_id` 已判定过 | 视为成功，**不要**重发 |
  | `quarantined` | 归属校验失败（晚到/串案例） | 视为失败上报，排查 `attempt_id` |
  | `ok:false, detail:"persist_failed"` | host 落盘失败 | **可以重发**（换新 `msg_id`） |

* 因此：**收到 `ok:true` 之后不要重发**；`ok:false` 才是需要重试的情形。

---

## 8. `session_abort`（v1.2 新增，必须 ack）

host 在**异常收尾**时向 POS 与卡端各发一次：

```json
{"v":1,"type":"session_abort","msg_id":"...","ts":0,
 "run_id":"...","brand":"visa","mode":"contactless",
 "case_id":"VISA-CL-001","attempt_id":"...",
 "reason":"case_failed|result_timeout|pos_disconnected|android_disconnected|manual_abort|...",
 "action":"stop"}
```

* 收到后请**立即 ack**，并把本次联调视为结束（停止本案例的收尾动作）；
* host 只等 3s，**不 ack 也只记日志**，不会阻塞收尾——但请务必 ack，便于日志判定；
* 该帧**只在异常时**出现；正常跑完不会发。

---

## 9. ack 矩阵（host 侧强制）

| 帧 | 方向 | 是否必须 ack | host 超时 | 超时/失败后果 |
|---|---|---|---|---|
| `config_push`(pos_kernel_config) | host→POS | **必须** | 15s | 案例 FAIL `pos_kernel_config_ack_timeout` / `..._apply_failed` |
| `config_push`(pos_config) | host→POS | **必须** | 15s | 案例 FAIL `pos_config_ack_timeout` / `pos_config_rejected` |
| `case_started` | host→POS | **必须** | 15s | 案例 FAIL `pos_case_started_ack_timeout` / `..._rejected` |
| `transaction_result` | POS→host | host 落盘后 ack | `case.timeout_seconds` | 无结果 → 案例 FAIL `result_timeout` |
| `session_abort` | host→POS | **必须** | 3s | 仅日志（best effort） |
| `heartbeat` | host→POS | `heartbeat_ack` | 10s 周期 | 静默 30s 警告，60s 断开 |

**POS 未连接**时的任何一步 → 该案例直接 FAIL（`pos_not_connected`），
整轮立即停止，剩余案例记为 `SKIPPED`。

---

## 10. 断线 / 超时一律按案例 FAIL

* 运行期间 POS 断线 → 该案例 FAIL（`pos_disconnected`），本轮停止；
* 回连后**不会**自动续跑，需要重新发起。

---

## 11. Table 35 未消费 tag（需 POS 明确处理）

host 下发的 `pos_params` 允许自由 tag（Q4 裁决：Table 35 为最小集）。
以下 tag 目前 POS **未消费**，请二选一并在回报中说明：

| tag | 含义 |
|---|---|
| `5F57` | 发卡行国家代码 |
| `9F7C` | 商户自定义数据 |
| `DF8112` | 内核能力/配置（PayPass 系） |
| `9F53` | 交易类别码（MCC）相关 |
| `5F36` | 交易货币指数 |
| `9A` | 交易日期 |
| `9F21` | 交易时间 |

处理方式：(a) 扩展消费；或 (b) 明确忽略并在日志中记录。host 对未消费 tag 只记 warning。

---

## 12. 测试向量

### 12.1 AID（真实，VISA）

```
9F0607A0000000031010DF0101009F08020140DF1105D84000A800DF1205D84004F800
DF130500100000009F1B04000186A0DF150400000000DF160199DF170199DF14039F3704
DF180100
```

期望：`updateAidParam` 返回成功；内核里 `A0000000031010` 的属性为 `DF01=01`、`9F08=0002`（其余见上）。

### 12.2 CAPK（DF03 复核用例）

用一个小模数演示算法（真实模数见资产，勿外传）：

```
rid=A000000003 1B? 否：9F06 值 = A000000003 (5B)
9F22 = 01
DF02 = ABAB...(128 字节)
DF04 = 03
DF03 = 47E658CEAEE1FF313B755ADB4EBC98A5DC288937
```

即：`SHA1(A000000003 ‖ 01 ‖ DF02裸字节 ‖ 03)` 应等于该 `DF03`。
请用同样的方法复核实际下发的 CAPK 条目。

### 12.3 DF05 三种日期编码（同一日期 `20441231`）

| 编码 | DF05 元素（tag+len+value） |
|---|---|
| ascii8 | `DF05083230343431323331` |
| bcd4 | `DF050420441231` |
| bcd3 | `DF0503441231` |

### 12.4 `session_abort` 示例

```json
{"v":1,"type":"session_abort","msg_id":"ab12","ts":0,"run_id":"20260914T204420_982800bd",
 "brand":"visa","mode":"contactless","case_id":"VISA-CL-001","attempt_id":"15d6dc56...",
 "reason":"result_timeout","action":"stop"}
```

期望：立刻回 `ack{ack_msg_id:"ab12", ok:true}`，并停止本案例收尾。

### 12.5 `pos_kernel_config` 最小示例

```json
{"v":1,"type":"config_push","msg_id":"k1","ts":0,
 "config_type":"pos_kernel_config","case_id":"VISA-CL-001","attempt_id":"a1",
 "kernel_params":{
   "aid":["9F0607A0000000031010DF010100"],
   "capk":[],
   "term":"9F6604360000809F1A0201569F7A0101"}}
```

期望：`updateAidParam(1, 9F0607A0000000031010DF010100)` 成功、
`setTerminalParam(9F6604360000809F1A0201569F7A0101)` 成功 → `ack{ok:true}`。

---

## 13. 升级窗口与回报要求

1. **同窗口升级**：POS 与卡端必须与 host 同窗口升级到 1.2；混跑只会 `protocol_mismatch`。
2. 升级后请在联调前回报：
   * 新版本号（`app_version`）与 commit；
   * 第 4.7 节「同 RID 内容不同时的覆盖做法」；
   * 第 4.5 节「内核实际接受的 DF05 形状」；
   * 第 11 节 7 个 tag 的处理选择（消费 or 忽略）；
   * 回 `[READY]`，host 据此更新 `--expect-versions`。
3. host 侧 `--expect-versions` 将在收到回报后更新（卡端 1.0.33，POS 待定）。

---

## 14. 附：host 侧失败原因一览（便于对照排查）

| `failure_reason` | 触发点 |
|---|---|
| `android_not_connected` | 卡端未连接 |
| `card_config_ack_timeout` / `card_config_rejected` | 卡端配置阶段 |
| `card_emulation_ack_timeout` / `card_emulation_rejected` | 启动模拟阶段 |
| `card_ready_timeout` | 卡端未在 15s 内报 `emulating` |
| `pos_not_connected` | POS 未连接（任何一步） |
| `pos_kernel_config_ack_timeout` / `pos_kernel_config_apply_failed` | 内核参数下发 |
| `pos_config_ack_timeout` / `pos_config_rejected` | 交易参数下发 |
| `pos_case_started_ack_timeout` / `pos_case_started_rejected` | 案例启动通知 |
| `result_timeout` | 未在 `timeout_seconds` 内收到结果 |
| `assertion_failed` | 结果与 `expected_results` 不符 |
| `android_disconnected` / `pos_disconnected` | 运行期断线 |
| `android_error` | 卡端上报 `status:error`（案例状态为 ERROR） |

run 级 `abort_reason` 统一为 `case_failed:<case_id>:<failure_reason>`。
