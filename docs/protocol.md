# EMV Test Host — 统一设备协议 v2（唯一权威文档）

> 本文档为**唯一权威协议**。**v2 是硬切换**：v1.2 的 POS 报文已**全部删除**，不提供兼容开关、不并存
> （`backend/emv_host/protocol/messages.py:9-10`）。
> 关联文档（只读引用，不在此重复定义）：
> * 设计冻结稿：`docs/protocol-v2-design.md`（host↔POS CTEL2 TEI）；
> * POS 答复（**已定稿**）：`docs/reply-to-pos-v2-confirmation.md`（D1/D2/D3 + §A/§B/§C）；
> * 对端变更通知：`docs/change-notice-v2-pos.md`、`docs/change-notice-v2-card.md`、`docs/protocol-change-notice-brand.md`；
> * 变更索引与审核入口：`docs/CHANGELOG.md`。
>
> **wire 真值（以代码为准）**：
> * 信封/消息常量：`backend/emv_host/protocol/messages.py`；
> * 握手/身份/入站信封校验：`backend/emv_host/protocol/connection.py`；
> * 运行流与失败原因：`backend/emv_host/runner/executor.py`；
> * OUT 解码与派生字段：`backend/emv_host/judge/outcome.py`。
> 本文档与代码不符时以**代码行为**为准，并同步修订本文档（差异不得静默择一）。
>
> **判定哲学（v2 定案）**：判定源是 POS 的 **OUT**（OPS + DataRecord）。
> 卡端 APDU 是**诊断 trace** 通道；v2 的目标是让 APDU 成为可判定源，但本版（P1）**尚未接线**，
> `card.*` 断言路径当前恒为 missing（见 §12.8）。
> 协议层字段全量 snake_case；`signalData` 为 `[{id, value}]`；`profile` / `keys` 为负载，使用各自的
> camelCase 规范化 schema（见 §8、§9），原样透传。

## 0. 角色与端口

| 角色 | 连接方向 | 默认端口 |
|---|---|---|
| host（Windows 上位机） | TCP 服务端 | 设备协议 **9100**（单端口）；Web/API/WS **8000** |
| android（NFC 卡模拟 app） | TCP 客户端 | 接入方式：`adb forward tcp:9100 tcp:9100` 或局域网 IP |
| pos（POS 交易 app） | TCP 客户端 | 同上 9100（POS 全人工操作，交易结束后自动回传 OUT） |

无 TLS；token 仅握手使用，之后凭连接会话。仅限隔离测试网络。

* TCP 角色与 v1.2 一致：**host 仍是服务端**，两端都是客户端（设计 R1=b）；
* host↔POS 的报文格式改为 **Mastercard CTEL2 TEI 信号集**（§6.3 / §6.5）；host↔卡端链路沿用（§6.2 / §6.4）；
* 连接建立后 host **不再推送任何 `device_config`**（v1.2 帧已删，§12.2）；心跳与 ack 预算按 §3 / §5 的硬编码缺省。

## 1. 帧格式（锁定）

```
+---------------------+-------------------------+
| 4 字节大端无符号长度 | UTF-8 JSON payload      |
+---------------------+-------------------------+
```

* 长度按 payload **UTF-8 字节数**计；最大帧 **1 MiB**（`frame.py:23` `MAX_FRAME_BYTES`），
  声明长度超限 → **立即断开**（`FrameTooLargeError`，`frame.py:51-52`），**不计入**坏帧累计；
* payload 必须是 **JSON 对象**（非对象 → `FrameDecodeError`，`frame.py:58-59`）；
* 所有帧带信封：`{"v": 2, "type": "<消息类型>", "msg_id": "<uuid-hex>", "ts": <epoch 秒, 浮点>}`
  （`messages.py:22` `PROTOCOL_VERSION = 2`；`messages.py:101-110` `make_frame`）；
* 坏帧规则：**JSON 非法但长度前缀合法** → 计入累计计数、跳过继续读；**累计满 5**（`frame.py:24`
  `MAX_BAD_FRAMES`）才断连（`connection.py:216-221`、`connection.py:241-249`）；
  计数**不因成功帧重置**，按连接累计；
* **入站信封 `v` 校验（D0，收发都校验）**：

| 阶段 | 条件 | 行为 |
|---|---|---|
| 握手 | 首帧 `v != 2` | `HandshakeError("bad_frame")` → 回 `error{code:"bad_frame"}` 并断开（`connection.py:151-155`） |
| 握手之后 | 任意帧 `v != 2` | 记 warning 并**断开连接**（`connection.py:253-258`） |

## 2. 握手与身份验证

1. 客户端连接后 **10s 内**发 `handshake`（`connection.py:30` `HANDSHAKE_TIMEOUT`）；超时 → 直接断开
   （不发 `error` 帧，`connection.py:142-148` + `server.py:82-83`）；
2. 首帧必须是 `handshake`，否则 `bad_frame`（`connection.py:149-150`）；
3. 信封 `v` 必须为 **2**（§1）；`client_type` + `token` 对照 `tokens.json`，不匹配 → `auth_failed`
   （`connection.py:156-161`）；
4. **`protocol_version` 必须上报 `"2.0"`**（`messages.py:27` `PROTOCOL_VERSION_STRING = "2.0"`）：
   * 缺失 → `error{code:"protocol_mismatch"}` 并断开（`connection.py:166-170`）；
   * `< 2.0` → 同上（`connection.py:171-174`；版本按点分段转元组比较，不可解析视为 `(0,)`，
     `messages.py:90-94`）；
   * `>= 2.0` → 接受，`handshake_ack.protocol_version = "2.0"`（host 始终回自己的版本，
     客户端更高的小版本不被回显，`connection.py:195-200`）；
   * **无任何 1.x 兼容模式**；
5. 每 `client_type` **只允许一个**连接；同 `device_id` 重连**替换旧会话**（旧连接被 host 关闭，
   且其 `disconnected` 不会影响新会话）；不同 `device_id` → `already_connected`
   （`connection.py:176-188`、`server.py:112-119`）。

```json
// client -> host
{"v":2,"type":"handshake","msg_id":"…","ts":…,
 "client_type":"android"|"pos","token":"…",
 "device_id":"…","app_version":"…","device_model":"…",
 "protocol_version":"2.0"}
// host -> client
{"v":2,"type":"handshake_ack","msg_id":"…","ts":…,
 "session_id":"android-<ts>-<port>","protocol_version":"2.0","server_time":…}
```

握手期可发出的错误码（`connection.py` 实际抛出路径）：`bad_frame`（首帧类型或信封 `v`）/
`auth_failed` / `already_connected` / `protocol_mismatch`。
`messages.py:84-86` 另定义了 `handshake_timeout` / `unknown_message` / `server_busy` 三个常量，
当前**没有发出路径**（握手超时是直接断开，不发 error 帧）。

连接建立后 host 不再向任何端推送配置帧：卡端配置随每案例的 `config_push{card_config}` 下发（§6.2），
POS 配置随每案例的 `config` 下发（§6.3.1）。

## 3. ack 双向表与幂等

| 帧 | 方向 | ack 形式 | host 超时 | 超时 / 失败后果 |
|---|---|---|---|---|
| `test_info` | host→POS | `test_info_ack`（**按帧 type 关联**） | `--pos-ack-timeout`（缺省 15s） | 案例 FAIL `test_info_ack_timeout` |
| `config` | host→POS | `config_ack`（按 type） | 15s | 案例 FAIL `config_ack_timeout` |
| `act` | host→POS | `act_ack`（按 type） | 15s | 案例 FAIL `act_ack_timeout` |
| `out` | POS→host | 通用 `ack`（**判定已落盘后**才发） | — | POS **不必**依赖 host 的 ack（§A/5.3）；host 不会因缺 ack 失败 |
| `msg` | POS→host | 通用 `ack` | — | best-effort，仅记录 UIRD |
| `status` | 卡→host | 通用 `ack`（**host 必 ack**） | 卡端 15s 未收到可重发 | — |
| `log` | 卡→host | 不 ack | — | 单向 |
| `config_push(card_config)` | host→卡 | 通用 `ack` | `--card-ack-timeout`（缺省 15s） | 案例 FAIL `card_config_ack_timeout`（超时/断线）/ `card_config_rejected`（`ok:false`） |
| `start_emulation` | host→卡 | 通用 `ack` | 15s | 案例 FAIL `card_emulation_ack_timeout` / `card_emulation_rejected` |
| `stop_emulation` | host→卡 | 通用 `ack` | `stop_ack_timeout` 3s（best-effort） | 仅日志 |
| `heartbeat` | ↔ | `heartbeat_ack` | 10s | 30s 警告 / 60s 断开（§5） |

* **通用 `ack`**：`{"ack_msg_id": <被 ack 帧的 msg_id>, "ok": bool, "detail": str}`
  （`messages.py:113-114`）；关联靠 `ack_msg_id`（`connection.py:106-109`）。
  `out` 的 ack `detail` 取值见 §7.2；
* **CTEL2 `*_ack`**：**不带 `ack_msg_id`**，关联**只按帧 type**（同一连接同一时刻只有一个信号在途，
  `connection.py:33-36` + `connection.py:111-129`）。`ref_msg_id` 仅作诊断，**host 不依赖**
  （POS 现做法「type + 附带 `ref_msg_id`」正确，§A/6.1）。
  `*_ack.signalData` 的 `ResponseCode` 解析对 id **大小写不敏感**（`executor.py:884-890`）；
* `ResponseCode != "YES"` 的后果：`act` + `NOTSUPPORTED` → `act_not_supported`；
  其余 → `{test_info|config|act}_rejected`（`executor.py:875-881`）；
* POS 未连接时发信号 → `pos_not_connected`（`executor.py:870-874`）；
* **幂等**：`out` 的 `msg_id` 在当前 attempt 内去重（`session.judged_msg_ids`，attempt 开始/结束时重置
  `runner/session.py:65-85`）。重复 `msg_id` → ack `{"ok":true,"detail":"duplicate"}`，
  **不再判定、不覆盖落盘、不写 quarantine**（`executor.py:461-464`）；
* 收到 `ok:true` 后**不要重发**；只有 `ok:false`（如 `detail:"persist_failed"`）才需换新 `msg_id` 重试；
* ack 预算可用 `serve --card-ack-timeout` / `--pos-ack-timeout` 调整（缺省 15s，`appconfig.py:65-66`）。

## 4. 会话归属与迟到结果

* 每个用例的每次执行有唯一 `attempt_id`（host 生成），随 `card_config` / `start_emulation` /
  `stop_emulation` 下发（`executor.py:710-719`、`executor.py:736-737`、`executor.py:955-962`）；
* **用例上下文内**的卡端上报（`status` / `log`）必须回显 `case_id + attempt_id`；attempt 不匹配的上报
  只记通用日志（`executor.py:369-414`）；
* **CTEL2 的 POS 帧不携带 `case_id` / `attempt_id`**（POS 只是在同一连接上应答本笔 `act`）：
  `out` 归属「当前在途 attempt」——即 host 正处于 `WAIT_OUT` 且 waiter 未完成
  （`executor.py:465-471`、`session.is_waiting_result`）；
* **无法归属的 `out`**（无运行 / 无在途等待 / 已结束的 attempt）→ **隔离**：
  追加到 `runs/<日期>/<run_id>/quarantine.jsonl`，并 ack `{"ok":true,"detail":"quarantined"}`，
  **不参与判定**（`executor.py:468-471`、`executor.py:492-504`）；
  * quarantine 记录：`{"at":…, "reason":…, "frame":{…}}`（`run_store.py:141-144`）；
  * `reason` 取值台阶（按代码顺序）：`no active run` → `stale attempt_id` → `missing attempt_id`
    → `case_id mismatch`（CTEL2 `out` 不带这两个 id，因此在实际链路上会落到
    `missing attempt_id` / `case_id mismatch` 分支，`executor.py:492-499`）；
* 迟到的 `out`（用例已结束、attempt 已换）不参与任何判定，只进 quarantine。

## 5. 心跳与失联检测

* host 每 **10s** 发 `heartbeat`（`server.py:31` + `server.py:157-166`），客户端回
  `heartbeat_ack`（`ref_msg_id` 回显原 `msg_id`，`connection.py:260-262`）；
  **任何入站帧都算存活**（刷新 `last_recv`，`connection.py:251`）；
* 静默 ≥ **30s** → host 告警；≥ **60s** → host 断开（`appconfig.py:58-60`；`connection.py:231-240`）；
* **`device_config` 已删除**（§12.2），两端心跳/判死/ack 超时**全部硬编码缺省**：
  心跳 10s / 告警 30s / 断开 60s / ack 15s；
* **卡端 watchdog 与重连退避（卡端实现，语义沿用）**：3 × 10s 未收到 host `heartbeat` → 判死 → 断线
  → 按 1/2/4/8/16/30s 指数退避自动重连；同 `device_id` 重连会被 host 接受并替换旧会话（§2 第 5 条）；
* run 期间任一端断线 → **当前案例 FAIL** 并停止整轮：卡端 `android_disconnected`，POS
  `pos_disconnected`（`executor.py:300-322`；v1.2 的「POS 断开仅告警」已废除）。

## 6. 消息目录

### 6.1 通用（双向）

| type | 常量（`messages.py`） | 字段 | 说明 |
|---|---|---|---|
| `heartbeat` / `heartbeat_ack` | `MSG_HEARTBEAT` / `MSG_HEARTBEAT_ACK` | `ref_msg_id` | 保活（§5） |
| `ack` | `MSG_ACK` | `ack_msg_id, ok, detail` | 通用 ack（§3） |
| `error` | `MSG_ERROR` | `code, message, ref_msg_id?` | host→client 错误 |
| `log` | `MSG_LOG` | `level, message, case_id?, attempt_id?` | 客户端日志；`level:"trace"` 为 APDU 诊断通道（§6.4） |

### 6.2 host → android（卡端链路，v2 保留）

| type | 常量 | 字段 | 说明 |
|---|---|---|---|
| `config_push`（`config_type:"card_config"`） | `MSG_CONFIG_PUSH` + `CONFIG_TYPE_CARD` | `config_type, case_id, attempt_id, profile, keys|null, timeout_seconds, trace_enabled` | 每案例开始前；`profile`/`keys` 为 camelCase 负载（§8/§9） |
| `start_emulation` | `MSG_START_EMULATION` | `case_id, attempt_id` | 激活 + AID 注册 |
| `stop_emulation` | `MSG_STOP_EMULATION` | `case_id, attempt_id` | 内核 reset；**保留 AID 注册与档案**；每个 attempt 至多一次（§10） |

* `config_push` 在 v2 只承载 `card_config` 一种 `config_type`（`CONFIG_TYPE_CARD = "card_config"`，
  `messages.py:47`）；v1.2 的 `device_config` / `pos_config` / `pos_kernel_config` 已删除（§12.2）；
* `trace_enabled` 是 host 侧开关值（`--trace-enabled` 映射，缺省 false），卡端从**此处**读取；
  host 侧落盘仍受该开关约束（§6.4）。

### 6.3 host → pos（CTEL2 TEI，v2 新报文集）

| type | 常量 | 结构 |
|---|---|---|
| `test_info` | `MSG_TEST_INFO` | `signalData:[{id:"TestId",value:"<case_id>"},{id:"InterfaceVersion",value:"20250617"},{id:"SessionId",value:"<run_id>"},{id:"DekDetId",value:null}]` |
| `config` | `MSG_CONFIG` | `signalData:[{id,value}…]`，见 §6.3.1 |
| `act` | `MSG_ACT` | `signalData:[{id,value}…]`，交易数据（§8.1） |

* `config` / `act` 的 `value` 一律为**大写 hex 字符串**（由 host 归一化，见 §6.3.1、§8.1）；
  `test_info` 的 value 是普通字符串（案例 id / 版本号 / run id，`DekDetId` 恒为 `null`）；
  三个信号统一：`null` = 不发送该 id，`""` = 长度 00；
* **发送顺序固定**：`test_info` → `config`（仅当本案例有内核参数，见 §6.3.3）→ `act`
  （`executor.py:775-801`）；一次 `test_info` 可对应多笔 `act`（例如 dual tap），`config` 可被多笔 `act` 复用；
* `TestId` = 案例 id（`case.case_id`，`executor.py:784`；对端通知写为 `<test名>`）；
  `SessionId` = run id；`DekDetId` 恒为 `null`。

#### 6.3.1 CONFIG 形态与 id 表（R2=b + D1）

**形态（权威）：同名 id 逐条展开**——`AID` / `CAPK` / `DRL` / `EXCEPTION_FILE` **每条一个 item**，
**不使用**「单 item 的值 = 数组」的形态（`executor.py:893-921`；Q6 已定稿、§A/6.2 以逐条为权威）。

```jsonc
// config 示例（Amex）
{"v":2,"type":"config","msg_id":"…","ts":…,
 "signalData":[
   {"id":"CONF_NAME","value":"CONF_16"},
   {"id":"AID","value":"9F06…"},
   {"id":"CAPK","value":"9F06…"},
   {"id":"DRL","value":"9F06…8F…DF8105…"},
   {"id":"EXCEPTION_FILE","value":"5A…5F34…"},
   {"id":"TERM","value":"…"},
   {"id":"VERSION","value":"4.2.0"}
 ]}
```

| id | 常量（`messages.py`） | 值 | 语义 / 对应 v1.2 |
|---|---|---|---|
| `CONF_NAME` | `CONFIG_ID_NAME` | 字符串：`kernel_params_ref`，缺省 = `case_id` | 信息用（POS 记录/日志）。**代码取 ref 本身**（如 `CONF_16`），不是 `"<brand>/<ref>"`（`executor.py:897-898`） |
| `AID` | `CONFIG_ID_AID` | 每条一个：一条拼接 BER-TLV 大写 hex（`9F06` 打头） | 对应 v1.2 `kernel_params.aid[]` |
| `CAPK` | `CONFIG_ID_CAPK` | 每条一个：`9F06‖9F22‖DF05‖DF06‖DF07‖DF02‖DF04‖DF03` | 对应 `capk[]` |
| `DRL` | `CONFIG_ID_DRL` | 每条一个：`9F06+8F+DF8105` 拼接 TLV | 对应 `drl[]`（Amex 文件内名为 CRL） |
| `EXCEPTION_FILE` | `CONFIG_ID_EXCEPTION_FILE` | 每条一个：`5A+5F34` 拼接 TLV | 对应 `exception_file[]` |
| `TERM` | `CONFIG_ID_TERM` | 一条拼接 TLV hex（= `CONF.term + PROFILE.term`，loader 合成） | 对应 `term` |
| `VERSION` | `CONFIG_ID_VERSION` | 字符串（如 `"4.2.0"`） | host 侧元数据；非空时随 CONFIG 下发（`executor.py:919-920`） |

* 编码规则（`kernel_tlv.py`）：AID `9F06` 5–8 字节、首元素固定；CAPK 元素顺序固定，
  `DF03 = SHA1(9F06‖9F22‖DF02‖DF04)`；`DF05` 支持 `ascii8`（默认）/`bcd4`/`bcd3`/`raw`；
  DRL 每条 `9F06+8F+DF8105`；exception file 每条 `5A+5F34`；
* `TERM` 是**多条 TLV 直接拼接**的 hex（不是单条）。

#### 6.3.2 空组语义（D1）

| 情形 | host 行为（`executor.py:904-916`） | POS 语义 |
|---|---|---|
| `DRL` 非空 | 逐条 `{"id":"DRL","value":"<tlv>"}` | 整表替换（R2=b） |
| `DRL` 空 | **恒发一条** `{"id":"DRL","value":""}` | **清空该表且不加**（`CONF_01_RemoveCRL` 依赖此语义） |
| `EXCEPTION_FILE` 非空 / 空 | 同上（逐条 / 一条 `value:""`） | 同上 |
| id 缺失 | host **不产生**该形态（DRL/EXCEPTION_FILE 每笔恒发） | 「本笔不管理该表」（POS 侧语义，host 不用） |
| `AID` / `CAPK` 空 | **不发**该 id（无「清空」语义） | 本笔不出现空 AID/CAPK |

* 即：`DRL` / `EXCEPTION_FILE` **在每一笔下发 CONFIG 的案例中恒存在**（至少一条 `value:""`）；
  「清空」与「不管理」的区别完全由 `value:""` vs id 缺失表达，**不新增** `CLEAR_*` 之类显式 id。

#### 6.3.3 CONFIG 发送时机

* 每案例、`test_info` 之后、`act` 之前下发；
* **仅当本案例解析出的内核参数非空**才发（`executor.py:791-796`）；「空」的定义是
  AID/CAPK/DRL/EXCEPTION_FILE/TERM 全空（`models.py:247-250`，`VERSION` 不计入）；
  此时**不发 `config` 帧**，`test_info` 后直接 `act`，POS 使用自带参数表；
* 内核参数来源：案例级 `kernel_params` 覆盖 brand 默认（`executor.py:688-693`）；
  命名库 / 外部目录加载见 `cases/loader.py`（`CONF_*` 与 `PROFILE_*` 自由配对，`term = CONF.term + PROFILE.term`）。

### 6.4 status / trace（安卓状态机）

```
card_config ─ack→ app 处理完成 ──status{state:"ready", detail:{profile_id,
    default_payment_app, registered_aids[], ac_crypto}}──→ host 告警提示(可选)
start_emulation ─ack→ 激活+AID 注册 ──status{state:"emulating", detail:{profile_id, registered_aids}}──→ host 进入 WAIT_OUT
交易结束/收到 stop_emulation ──status{state:"stopped", detail:{...}}──
异常 ──status{state:"error", detail:{code, message}}──
```

* `status` 由 host **必 ack**（`executor.py:335-337`）；
* error 码表：`import_failed / aid_registration_failed / atc_exhausted / internal`；
  **用例内** error → 当前用例 **ERROR**（保留结构化 `error{code,message}`）、整轮停止
  （`android_error`，`executor.py:375-387`）；**用例外** error → 仅日志；
* `ready.detail.default_payment_app=false` → host 告警（不阻断，操作员处理）；
* `ready.detail.registered_aids` 是**计划列表**，不是判据；权威注册结果随 `emulating.detail.registered_aids`
  在日志中给出（`executor.py:389-405`）；
* `ac_crypto`：`"real"`（有密钥真实 Retail MAC）或 `"fake"`（8×0xFA 假密文，见 §9）；
* **trace（APDU 诊断通道）**：`log{level:"trace", message:{event:"apdu"|"field",
  direction:"COMMAND"|"RESPONSE", cla, ins, p1, p2, data_hex, sw?, elapsed_ms, seq?, profile_id?},
  case_id, attempt_id}`；
  * `message` 为 JSON **对象**；`sw` 为 hex 字符串（如 `"9000"`），COMMAND 省略；
  * `data_hex` 无数据为 `""`；`elapsed_ms`：COMMAND=0，RESPONSE=自配对 COMMAND 起耗时；
  * **静默指令**：命中 `noResponseCommands` 的 COMMAND 条目增加 `no_response: true`，
    且**不产生**对应 RESPONSE 条目（§8.4）；
  * 卡端通知要求该通道**恒开、按案例分段**（`docs/change-notice-v2-card.md` §3）；
    host 侧落盘仍受 `--trace-enabled` 控制（缺省关），且**当前只落盘不判定**：
    `executor.py:416-440`（`_on_trace`），按 attempt 写
    `runs/<run_id>/trace/<case_id>.jsonl`（`run_store.py:146-153`），历史页展示；
  * `card.*` 断言（APDU 反解）为 P2，**尚未实现**（§12.8）。

### 6.5 pos → host（CTEL2 TEI）

| type | 常量 | 结构 | 说明 |
|---|---|---|---|
| `test_info_ack` | `MSG_TEST_INFO_ACK` | `signalData:[{id:"ResponseCode",value:"YES"}]`，或 `"NO"` + 可选 `{id:"Description",value:"…"}` | 缺 `TestId` 时 POS 回 `NO`（§A/7.5） |
| `config_ack` | `MSG_CONFIG_ACK` | 同上 | |
| `act_ack` | `MSG_ACT_ACK` | `signalData:[{id:"ResponseCode",value:"YES"\|"NO"\|"NOTSUPPORTED"}]` | `NOTSUPPORTED` → 案例 FAIL `act_not_supported` |
| `msg` | `MSG_MSG` | `signalData:[{id:"UIRD",value:"<13B hex>"}]` | 用户界面请求；一次 `act` 可 0..n 条，按发生顺序 |
| `out` | `MSG_OUT` | `signalData:[{id:"OPS",…},{id:"DataRecord",…},{id:"DiscData",…},{id:"UIRD",…},{id:"KernelId",…}]` | 结果；一次 `act` 可 ≥1 条（dual tap）；见 §7 |

* `ResponseCode` 取值常量：`YES` / `NO` / `NOTSUPPORTED`（`messages.py:72-74`）；
* host 收到 `msg` → 通用 `ack` + 记录 UIRD（`executor.py:348-351`、`executor.py:442-451`）；
* host 收到 `out` → 判定 + 落盘 + ack（§7.2）。

### 6.6 v2 一次案例的完整时序

```
host: card_config        → android: ack
host: start_emulation    → android: ack        android: status{emulating}
host: test_info          → pos: test_info_ack
host: config             → pos: config_ack     （仅当本案内核参数非空，§6.3.3）
host: act                → pos: act_ack
pos:  msg*（0..n）        → host: ack
pos:  out（≥1）           → host: 判定+落盘 → ack{detail:"persisted"}
host: stop_emulation     → android: ack        （任何退出路径，§10）
```

host 状态机：`HANDSHAKE → READY →〔每案例〕CONFIGURING → WAITING_EMULATION → POS_CONFIG
→ WAIT_OUT → JUDGING/DONE`（`runner/session.py`、`executor.py:695-852`）。
任一 ACK 超时 / `NO` / 断线 → 案例 FAIL（原因码见 §12.3）；收到 `out` 后由断言引擎判
PASS / WARN / FAIL（§7.7）。

### 6.7 v1.2 已删除帧（REMOVED，硬切换，无兼容开关）

| 已删除帧 | v2 替代 / 处置 |
|---|---|
| `config_push{config_type:"device_config"}` | **删除**；心跳/判死/ack 超时改为两端硬编码缺省（§5），连接后不再推送任何配置 |
| `config_push{config_type:"pos_config"}` | **删除**，由 `act` 取代（交易数据见 §8.1） |
| `config_push{config_type:"pos_kernel_config"}` | **删除**，由 `config` 取代（§6.3.1） |
| `case_started` | **删除**；操作员提示由 host Web UI（`operator_instructions`）与 `test_info` 承载 |
| `transaction_result` | **删除**，由 `out` 取代（§7） |
| `session_abort` | **删除**；CTEL2 无对应信号，异常收尾**不再**向两端发通知帧（`executor.py:559`） |

依据：`messages.py:9-10`（v1.2 POS 帧 REMOVED 清单）、`executor.py:775-777`（POS 链路不再使用
`pos_config`/`pos_kernel_config`/`case_started`/`transaction_result`）、`executor.py:295-298`（连接后不下发
`device_config`）。`session_abort` 的常量在 `messages.py` 中已不存在。

## 7. POS 结果（OUT）与判定

### 7.1 OUT signalData

| id | 必填 | 说明 |
|---|---|---|
| `OPS` | **是** | Outcome Parameter Set（DF8129），**8 字节** hex；POS 优先透传内核 `DF8129`，取不到或长度非 8B → 全 0（§A/3） |
| `DataRecord` | 否 | 该笔交易的 TLV（hex）；白名单 + 「取到才报」（§7.5）；`null` / 缺失 = 空 |
| `DiscData` | 否 | POS 私有 TLV → `out.discdata.<tag>`；**本轮 host 不断言**，POS 可不报（§7.6） |
| `UIRD` | 否 | 取 `DF8116`（13B hex），取不到则全 0；与 `msg` 同构（§A/5.2） |
| `KernelId` | 否 | Kernel Identifier-Terminal → `out.kernelid`；**本轮 POS 不上报** |

* id 匹配对大小写不敏感（`outcome.py:106-112`）；
* **`OPS` 缺失或为 `null`** → OUT 非法：`build_result` 抛 `ValueError`，host 记 quarantine 并使案例失败
  （ack `detail:"out_invalid"`，§7.2；`outcome.py:118-121`、`executor.py:972-978`）；
* OPS 长度非 8 字节或非法 hex 同样按非法 OUT 处理（`outcome.py:54-60`）；
* `DataRecord` / `DiscData` 为非法 TLV（截断、长度形式不支持、尾随垃圾）→ 同样按非法 OUT 处理
  （`outcome.py:96-103`、`kernel_tlv.py:245-265`）。

### 7.2 判定顺序与 ack 语义（D2）

**host 收到 `out` 的顺序固定为：判定 → 落盘 → ack**（`executor.py:453-490`）。
因此 POS 收到的 ack 含义是「**判定已落盘**」，而不是「报文已到达」。

| ack | ok | 触发条件 | 附加行为 |
|---|---|---|---|
| `detail:"persisted"` | `true` | 判定并成功写入 `results/<case_id>.json` | 结果投递给运行循环；`msg_id` 记入本 attempt 去重集 |
| `detail:"duplicate"` | `true` | `msg_id` 已在**本 attempt** 判定过 | **不再判定**、不覆盖落盘、不写 quarantine（`executor.py:461-464`） |
| `detail:"quarantined"` | `true` | OUT 无法归属当前在途 attempt / 案例不存在 | 写 `quarantine.jsonl`，不参与判定（§4） |
| `detail:"out_invalid"` | `true` | OPS 缺失/非法、TLV 非法（`build_result` 抛 `ValueError`） | 写 quarantine（`reason` = 异常文本）；结果投递为 `fail`，该案例以 `assertion_failed` 落盘 FAIL |
| `detail:"persist_failed"` | `false` | 落盘抛异常（磁盘满/权限等） | 运行循环**继续等待**可用的 OUT（waiter 不 resolve），`msg_id` 不记入去重集（`executor.py:478-485`） |

* 收到 `ok:true` **不要重发**；只有 `ok:false` 才换新 `msg_id` 重试；
* 去重窗口 = **当前 attempt**（`begin_attempt` 重置 `judged_msg_ids`）；跨案例迟到 → quarantine；
* host 先建 waiter 再等 OUT（`WAIT_OUT`），等待上限 = 案例的 `timeout_seconds`
  （`executor.py:805-844`），超时 → 案例 FAIL `out_timeout`。

### 7.3 OPS 位表（DF8129，8B）

> 位表来源：设计 §6.1（`refernce/OPS_description_1.png` / `_2.png`）；host 解码实现 `outcome.py:52-85`。
> 未定义取值一律派生为 `RFU(bbbb)`（`bbbb` 为 4 位二进制）。

| 字节 | 位 | 字段 | 取值 / host 输出（`out.ops.*`） |
|---|---|---|---|
| Byte1 | b8–b5 | Status | `0001`→`APPROVED`；`0010`→`DECLINED`；`0011`→`ONLINE REQUEST`；`0100`→`END APPLICATION`；`0101`→`SELECT NEXT`；`0110`→`TRY ANOTHER INTERFACE`；`0111`→`TRY AGAIN`；`1111`→`N/A`；其它 `RFU(…)` → `status`；同时输出 `status_bits`（如 `"0001"`） |
| Byte1 | b4–b1 | RFU | — |
| Byte2 | b8–b5 | Start | `0000`→`A`；`0001`→`B`；`0010`→`C`；`0011`→`D`；`1111`→`N/A`；其它 RFU |
| Byte3 | b8–b5 | Online Response Data | `1111`→`N/A`；其它 `RFU(…)` |
| Byte4 | b8–b5 | CVM | `0000`→`NO_CVM`；`0001`→`OBTAIN_SIGNATURE`；`0010`→`ONLINE_PIN`；`0011`→`CONFIRMATION_CODE_VERIFIED`；`1111`→`N/A`；其它 RFU |
| Byte5 | b8 | UI Request on Outcome Present | 0/1 → `ui_request_on_outcome` |
| Byte5 | b7 | UI Request on Restart Present | 0/1 → `ui_request_on_restart` |
| Byte5 | b6 | Data Record Present | 0/1 → `data_record_present`（POS 侧约定：该位为 0 时**不应**上报 `DataRecord`；host 解码**不**依该位清空 DataRecord） |
| Byte5 | b5 | Discretionary Data Present | 0/1 → `discretionary_data_present` |
| Byte5 | b4 | Receipt | 0/1 → `receipt` |
| Byte6 | b8–b5 | Alternate Interface Preference | `1111`→`N/A`；其它 RFU |
| Byte7 | b8–b1 | Field Off Request | 原始字节十进制 → `field_off_request`（`FF` 表示 N/A） |
| Byte8 | b8–b1 | Removal Timeout | ×100ms → `removal_timeout` |

* 另输出 `raw`：上游原样 8B hex；
* 示例（CTEL2 文档样例）：`30F0F01038F0FF00` = Status `ONLINE REQUEST` / Start `N/A` /
  OnlineResp `N/A` / CVM `OBTAIN_SIGNATURE` / DataRecord+DiscData+Receipt / AIP `N/A` /
  FieldOff `0` / RemovalTimeout `0`。

### 7.4 派生字段（`outcome` / `resp_code` / `online`）

| 字段 | 派生规则（`outcome.py:88-93`、`outcome.py:129-141`） |
|---|---|
| `outcome` | `APPROVED`→`approved`；`DECLINED`→`declined`；其它 → Status 小写、空格换下划线（如 `ONLINE REQUEST`→`online_request`，`N/A`→`n/a`，`RFU(0000)`→`rfu(0000)`） |
| `resp_code` | **优先 `DataRecord.8A`**；无 `8A` 再按 OPS Status：`APPROVED`→`00`、`DECLINED`→`05`、`ONLINE REQUEST`→`00`；**其余（含内核未跑）→ 空串 `""`** |
| `online` | 有 `DataRecord.9F27`（CID）时：首字节高 2 位 `== 0b10` → `online`，否则 `offline`；无 `9F27` 且 Status=`ONLINE REQUEST` → `online`；其余 `offline` |

**内核未跑（D2(a)）**：OPS Status 位 = `0000` **且** `DataRecord` 为空 → 不伪造 `05`/`91`/`80`：
`resp_code` 为空串；该次判定若 verdict 非 PASS，案例记录的 `failure_reason` 记 `out_no_outcome`
（verdict 为 FAIL 时同时作为整轮停止原因，`executor.py:991-1014`）。
实测口径：内核正常跑完时 POS **一律上报 `8A`**（`91`/`16`/`80` 都走 `8A`，`§D2`）。

### 7.5 DataRecord：白名单 + 「取到才报」

* POS 侧 `CreateEMVDataRecord()` 只导出 **host 约定的白名单 tag**（KS8226 无全量导出 API），
  共 **32 条 = 31 条 + `9F66`**；`9F66` 为 `QPB-B-002-01` 断言 `out.datarecord.9F66` = 本笔下发的 TTQ 所需，
  且 POS 保留「`CONFIG.TERM` 含 `9F66` 时用下发值覆盖内核回读值」的行为（设计 §6.2、§B.1）；
* **取不到的 tag 不出现在 `DataRecord` 中**（「取到才报」）；`9F02` / `9F03` / `9F53` 允许出现（在白名单内）；
* `DataRecord` 的 31 条具体 tag 清单**不在本仓库**：设计稿只点名了 `9F66`
  （`docs/protocol-v2-design.md:152`），host 侧代码不做白名单校验/裁剪——收到什么就解码什么
  （`outcome.py:96-103`、`outcome.py:122-123`）。因此本清单的权威副本在 host/POS 约定件中，
  本文档不臆造（**待补**：把 31 条 tag 落到文档或代码常量）；
* DataRecord 解码为 `out.datarecord.<TAG>`（tag 大写 hex，值为大写 hex，`kernel_tlv.py:245-265`）；
  未取到的 tag 在断言层表现为 **missing**（`present` 失败 / `absent` 通过，`judge/asserter.py:78-89`）。

### 7.6 DiscData / UIRD / KernelId

* `DiscData`：POS 私有 TLV，解码为 `out.discdata.<tag>`；**本轮 host 不断言 `out.discdata.*`**
  （§A/7.3、设计 §6.3）；host 仍会解码并落盘，将来若要用另给 tag 清单；
* `UIRD`：取 `DF8116`（13B hex），取不到则全 0（与 `msg` 同构）；host 记入 `session.pos_msgs`
  并落盘，**本轮不判定**；
* `KernelId`：本轮 POS 不上报 → `out.kernelid` 为 null。

### 7.7 断言路径与判定规则

| 路径 | 内容 |
|---|---|
| `out.ops.<field>` | §7.3 的 OPS 字段（`status` / `status_bits` / `cvm` / `start` / `data_record_present` / …） |
| `out.datarecord.<TAG>` | DataRecord 中的 tag 值（大写 hex） |
| `out.discdata.<TAG>` | DiscData 中的 tag 值（**本轮不断言**） |
| `out.uird` / `out.kernelid` | UIRD / KernelId 原值 |
| `outcome` / `online` / `resp_code` | §7.4 派生字段（顶层，**不是** `out.` 前缀） |
| `card.*` | 设计 §7.1（`card.apdu.<seq>.*` / `card.gpo.pdol.<tag>` / `card.gac.cdol1.<tag>` / `card.gac.cdol2.<tag>`）——**P2 未接线**，当前恒为 missing（§12.8） |

* 匹配算子：`eq / neq / contains / prefix / regex / any_of / present / absent / range`
  （`judge/asserter.py:74-120`）；
* 判定规则：所有 `pass` 级规则满足才 **PASS**；全部 `pass` 满足但至少一条 `warn` 不满足 → **WARN**；
  否则 **FAIL**（`judge/asserter.py:123-138`）；`cvm:"unknown"` 不算失败，CVM/ATC/UN 等易变标签
  用 `present` / `prefix` / `absent`；
* 示例（`QPB-B-002-01`）：`resp_code eq "00"`、`outcome eq "approved"`、`online eq "online"`、
  `out.datarecord.9F66 eq "36804000"`。

## 8. 案例库与 CardProfile JSON（camelCase）

**案例库布局**（profiles 与案例绑定，无卡组级共享档案库）：

    cases/
      <brand>/
        brand.json                      brand metadata + suite policy
                                         + card_template + kernel_params
                                         + pos_params（legacy，不下发）
        contactless/cases/<case_id>.json              独立可执行案例（自包含）
        contactless/cases/<group>/group.json          总案例（仅说明，无 profile）
        contactless/cases/<group>/<group>-NN.json     子案例（自包含，可执行）
        pos_configs/                    命名 POS 内核配置库（CONF_* / PROFILE_*，§6.3.1）

* 每个**可执行案例**（独立案例或子案例）JSON 内嵌 `profile`（CardProfile）与可选 `keys`——
  **profile 与案例绑定**：`profile.id` 缺省自动 = `case_id`，显式不一致仅 warning；
  总案例（group.json）不配置 profile；
* **总案例可经 UI/JSON 创建**（`POST /api/groups`，仅说明 + test plan，**无 profile/keys/交易参数/预期结果**）；
  携带 `group_id` 的可执行案例写入 `<group>/<case_id>.json` 目录（loader 按目录归属，文件内不落 group_id）；
* `brand.json.card_template`：该卡组默认卡片模板（新建案例时克隆）；
* `brand.json.pos_params`：**legacy** 卡组默认 POS 参数模板（v1.2 遗留，**不下发**；POS 交易数据现由 ACT 承担，§8.1）；
* 案例内 `kernel_params` / `kernel_params_ref` / `terminal_profile_ref`：喂 CONFIG（§6.3.1），
  三者互斥（内联不可与 ref 同用）。

**CardProfile 字段表**（与安卓 SAF 导入 schema 同构，camelCase）：
协议层字段 snake_case，profile 内部为 camelCase。
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
| `pdol`/`cdol1`/`cdol2`/`tdol`/`ddol` | hex TLV 模板 | 透传（也是 P2 APDU 反解依据） |
| `symmetricKeyId` | 指向 keys.symmetric_keys[].id；无 → 假密文 | 真假判定 |
| `rsaKeyId` / `certificateProfileId` | ODA / 证书引用 | 透传 |
| `records[]` | `{sfi(1..31), record(≥1), fields[token]}`；空数组=旧行为；未配置 (sfi,record) → 0x6A83 | 记录配置 |
| `acBehavior` | `online_request/offline_approved/offline_declined`；非法→缺省（跟随终端 P1）warn | ac 行为 |
| `ppseMode` | `auto/disabled/empty/custom`（缺省 auto）；非法→auto（warn） | PPSE 行为 |
| `ppseSw` | 大写 4 位 hex；**任意 mode 的全局 SW 覆盖**（缺省为各 mode 默认 SW） | PPSE 行为 |
| `ppseData` | 偶长 hex 完整 PPSE FCI 模板（仅 custom 使用）；host 加载期 TLV 长度一致性检查（warning），app 侧非法 → 6700 | PPSE 行为 |
| `noResponseCommands` | **指令不响应（静默）列表**，见 §8.4；缺省 `[]` | 静默行为 |
| `createdAt` / `updatedAt` | app 管理 | 透传 |
| 其它未知字段 | Gson 默认忽略 | 透传 |

**分工边界（v2 定稿）**：profile 是**卡片侧**数据——AIP（tag 82，字段 `aip`）、CVM（tag 8E，
字段 `cvmList[]`）、AID、PAN、记录等全部随 `config_push{card_config}` 下发安卓；
**交易数据（ACT，§8.1）与内核参数（CONFIG，§6.3.1）只下发 POS**，安卓侧永远收不到。

host 加载校验（针对案例内嵌 profile / brand.json card_template）：Luhn；有效期 YYMMDD；
hex 偶长；afl 4N+first≤last；records sfi∈[1,31]、无重复；aids hex；acBehavior 非法仅 warning；
内嵌 keys 校验 base64 与 id 模式 `^[A-Za-z0-9_-]{1,64}$`；`act` 的 id 归一化为大写、value 去空白并大写；
profile.id 与 case_id 绑定：缺省自动补全 = case_id，不一致仅 warning。

### 8.1 ACT 交易数据（取代 v1.2 的 `pos_params`）

**wire 结构**：`act.signalData = [{id, value}, …]`，逐条来自案例 JSON 的 `transaction.act[]`
（`executor.py:798-799`；模型 `ActParam`，`models.py:442-476`）。

| id | 名称 | 编码（约定） | 示例 |
|---|---|---|---|
| `9F02` | Amount Authorized (Numeric) | 6B BCD | `000000001500` |
| `9F03` | Amount Other (Numeric) | 6B BCD | `000000000000` |
| `5F2A` | Transaction Currency Code | 2B BCD | `0978` |
| `5F36` | Transaction Currency Exponent | 1B | `02` |
| `9A` | Transaction Date | 3B BCD YYMMDD | `240412` |
| `9F21` | Transaction Time | 3B BCD HHMMSS | `133333` |
| `9C` | Transaction Type | 1B | `00` |
| `9F53` | Transaction Category Code | 1B | 可 null |
| `5F57` | Account Type | 1B | 可 null |
| `9F7C` | Merchant Custom Data | 20B | 可 null |
| `DF8112` | Tags To Read (DEDS) | Var | 可 null |
| 私有 tag | — | — | 允许 |

* `value` 一律 **hex 字符串**；`null` = 不发该 tag；`""` = 长度 00；host 写入前统一
  `strip().upper()`、去空格（`models.py:460-465`）；
* 金额小数点位由 `5F36` 货币指数决定；`9F1A`（Terminal Country）不在 ACT，属于终端参数（`CONFIG.TERM`）；
* **终端级 tag 一律走 `CONFIG.TERM`**：`9F66`（TTQ）、`9F1B`（floor limit）、`9F1A` 等不下发 ACT
  （POS 侧语义：`CONFIG.TERM` 含 `9F66` 时用下发值覆盖内核回读值，§B.1）；
* host 约定 tag `DF8101 = "01"`：沿用「模拟测试主机在线批准」，**现在通过 ACT 下发**
  （§A/5.1）；未下发时 POS 保持真实行为（无收单主机 → 在线失败）；
* **legacy `transaction.pos_params`**：为兼容旧案例数据仍保留在模型里，但**不再下发 POS**
  （`models.py:471-476`）；解析与下发完全由 `act` 承担。

> **实现状态提示（数据面）**：当前 `cases/**` 下的可执行案例仍普遍只带 `pos_params`（旧字段），
> 未迁移到 `act`；因此**当前 wire 上的 `ACT.signalData` 为空数组**。案例数据迁移属于 P1c-2/P3
> 待办（§12.8）。本条是数据现状，不是协议语义。

### 8.2 keys（随案例内嵌）

`keys` 不是 `<profile_id>.keys.json` 兄弟文件，而是**内嵌在案例 JSON 中**（可选字段；无 keys 的案例走假密文，见 §9）。
结构见 §9，`config_push{card_config}.keys` 与案例内嵌 keys 同构。

### 8.3 PPSE mode 语义

`auto`=按 aids 生成目录（9000）；`disabled`=直接回 6A82；`empty`=9000 + BF0C 内无 61 条目；
`custom`=原样返回 ppseData（默认 9000，可用 ppseSw 覆盖）。
典型用例：正常 FCI→auto；FCI 缺 AID→custom（61 内仅 50/87、无 4F）；无 61 模板→empty；
错误响应码→disabled（可加 ppseSw 表达任意错误码）。custom 模板示例（label 不填充）：
`6F20 840E 325041592E5359532E4444463031 A50E BF0C0B 6109 5004 56495341 870101`
（generator 编程构造，长度由 loader 的 TLV 校验兜底）。

### 8.4 指令不响应（noResponseCommands）

**用途**：让卡片对某条 APDU 指令**不发任何响应**（静默）——区别于 `ppseMode:disabled`（回错误 SW 6A82）。
用于构造“终端等不到响应”的异常用例（如 GPO 无响应 → 终端超时/不再发起交易）。

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
  `PPSE`（SELECT PPSE）、`SELECT`（SELECT AID）、`GPO`（GET PROCESSING OPTIONS）、
  `READ_RECORD`、`GET_DATA`、`GENERATE_AC`、`VERIFY`（PIN）；
* 静默期间 app **不发送任何响应 APDU**（无 TLV、无 SW）；终端行为由 POS 实现决定
  （通常重发或超时中止），host 不判定安卓静默本身，判定仍以 POS 结果（OUT）为准；
* 空列表 / 字段缺省 = 全部指令正常响应；同一指令重复配置 → host warning、
  app 侧按数组顺序后项覆盖前项（双方 warning 策略一致）。

**app 侧映射表与匹配策略（定稿）**：

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
  `processCommandApdu` 返回 `null`（Android 官方语义：不发任何响应字节，终端等待超时）；
  NFC 场保持存活；不影响 `ready/emulating` 状态上报，`status` 帧不加字段；
* 真机风险（双方知悉）：个别厂商 ROM 对 `return null` 行为可能有差异，联调需在
  真机 + 真实 POS 上验证终端确实超时；若异常再议备选方案；
* 第一期边界：GET_DATA/VERIFY 仅需“静默本身”，**不要求**静默后恢复正常数据应答
  （app 内核现回 6D00）；后续需要（如 GET_DATA 返回 9F17）时另立协商项再实现内核 handler。

### 8.5 VISA GPO 携带 CID（N13 · **已实现、未部署**，卡端 1.0.32 `ea90149`，2026-09-10）

**状态：已实现未部署。** 在役（round #15 全绿）版本为卡端 **1.0.31**，**不含**本项；
N13 代码留在卡端分支（commit `ea90149`，标题 "implemented, not deployed"），**未装机**。
缘由：VISA 非接离线分支在卡端已给 TC 时仍 `END(102)`，POS 侧因此增加
"完成码 102 分支按 CID 兜底"；N13 让内核在 GPO 阶段即拿到 CID。
实现形态：VISA 的 GPO = `82/94/9F36` + **`9F27`**（按 `acBehavior`：AAC→`00`、TC→`40`、ARQC→`80`）。

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

## 9. keys 包与 ac_behavior 规则

`config_push{card_config}` 中 **keys 与 profile 并列**（keys 不在 profile 内）：

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

密文规则：**有对称密钥 → 真实 Retail MAC（任意 AC 类型，`ac_crypto:"real"`）；
无密钥 → 假密文（任意 AC 类型，8×0xFA，`ac_crypto:"fake"`）**。
环境假设：即使真实 ARQC，在线用例仍需测试收单主机/卡 BIN 白名单，否则主机侧仍会拒绝。

## 10. 中断与会话收尾

* **`stop_emulation` 的发送时机**：一旦卡端上报 `emulating`，其后**任何**退出路径都必须发一次
  `stop_emulation`（含 POS 信号失败、结果超时、断言 FAIL、手动 abort、异常）；
  未到 `emulating` 的失败（卡端未连接、`card_config` 被拒、就绪超时）**不发**；
  每个 attempt **至多一次**（`executor.py:923-936`，由 `_run_case` 的 `finally` 保证，`executor.py:664-672`）；
  ack 预算 3s、best-effort，仅日志；
* `stop_emulation` 语义：app ack + `status{state:"stopped"}`，内核 reset，**保留 AID 注册与档案**；
  重复 start 无需重发 `card_config`；
* **status error（用例内）**：当前用例 ERROR、整轮中止（`android_error`，保留结构化
  `error{code,message}`）；
* **卡端 TCP 断开**：当前用例 FAIL `android_disconnected`、整轮停止；
  **POS 断开**：当前用例 FAIL `pos_disconnected`（v1.2 的「仅告警」已废除）；
  浏览器断线不影响运行；
* **命令语义（三个动词）**：`stop` = 当前用例跑完即停（剩余不动）；`abort` = 立即中断当前用例
  （状态 ABORTED）；`skip` = 仅 `WAIT_OUT` 状态可把当前用例置 SKIPPED 并继续
  （`executor.py:240-264`）；
* 迟到 / 无主 OUT → quarantine（§4）；
* **`session_abort` 已删除**：异常收尾不再向卡端或 POS 发通知帧（`executor.py:559`，§12.2）。

## 11. 参考实现

* host：`backend/emv_host/`（protocol/runner/store/web/judge），启动
  `card_simulator_host` 或 `python -m emv_host serve`；
* mock 客户端（协议参照实现）：
  * `tools/mock_clients/mock_pos.py` — **v2 CTEL2 参照**：应答 `test_info` / `config` / `act`
    （`_ack` 带 `ResponseCode=YES`），随后发 `msg`（可选 UIRD）+ `out`
    （预置 `approved_offline` / `declined_offline` / `approved_online` 三种 OPS+DataRecord 组合）；
  * `tools/mock_clients/mock_android.py` — 卡端链路参照：`card_config`（含 `trace_enabled`）→
    `status ready`、`start_emulation` → `status emulating`、`stop_emulation` → `status stopped`，
    `--trace` 时发结构化 `log{level:"trace"}`；握手版本取 `PROTOCOL_VERSION_STRING`（= `"2.0"`）；
* POS 侧通知文档：`docs/change-notice-v2-pos.md`；卡端通知：`docs/change-notice-v2-card.md`；
  术语改名通知：`docs/protocol-change-notice-brand.md`；
* 内核参数导出：`tools/export_kernel_params.py`；Expresspay XML 导入：
  `tools/import_expresspay_xml.py`（生成仓库内命名 POS 配置库 `cases/<brand>/pos_configs/`）。

## 12. v2 硬切换：删除清单、失败原因与运行策略

### 12.1 版本协商（硬切换）

* `PROTOCOL_VERSION = 2`（信封 `v`），`PROTOCOL_VERSION_STRING = "2.0"`（握手协商值）；
* 握手**必须**带 `protocol_version`：缺失或 `< 2.0` → `error{code:"protocol_mismatch"}` 并断开；
* **不提供任何 1.x 兼容模式**；两端与 host 必须**同窗口升级**；
* 运行期入站帧信封 `v != 2` → warning + 断开（D0，§1）。

### 12.2 已删除的 v1.2 帧（REMOVED）

`config_push{device_config}`、`config_push{pos_config}`、`config_push{pos_kernel_config}`、
`case_started`、`transaction_result`、`session_abort` —— 明细见 §6.7。

### 12.3 `failure_reason` 全表（v2 实现）

下表**逐条对应 `runner/executor.py` 中实际会写入案例记录的字符串**（`_fail()` 与判定路径）。

| `failure_reason` | 触发条件 | 代码位置 |
|---|---|---|
| `android_not_connected` | 案例开始前卡端未连接 | `executor.py:699-703` |
| `card_config_ack_timeout` | `card_config` 未在 15s 内被 ack（或卡端断线） | `executor.py:720-726` |
| `card_config_rejected` | `card_config` 被 ack `ok:false` | `executor.py:727-731` |
| `card_emulation_ack_timeout` | `start_emulation` 未在 15s 内被 ack | `executor.py:738-745` |
| `card_emulation_rejected` | `start_emulation` 被 ack `ok:false` | `executor.py:746-751` |
| `card_ready_timeout` | 卡端 15s 内未上报 `status{emulating}` | `executor.py:752-762` |
| `test_info_ack_timeout` | `test_info` 无 `test_info_ack`（超时/断线） | `executor.py:857-864`（name=`test_info`，frame 见 `executor.py:781-788`） |
| `test_info_error` | 发送 `test_info` 时本地/传输异常 | `executor.py:866-868` |
| `test_info_rejected` | `ResponseCode != YES`（含 `NOTSUPPORTED`） | `executor.py:875-881` |
| `config_ack_timeout` | `config` 无 `config_ack` | `executor.py:857-864`（name=`config`，frame 见 `executor.py:791-796`） |
| `config_error` | 发送 `config` 时本地/传输异常 | `executor.py:866-868` |
| `config_rejected` | `ResponseCode != YES` | `executor.py:875-881` |
| `act_ack_timeout` | `act` 无 `act_ack` | `executor.py:857-864`（name=`act`，frame 见 `executor.py:798-800`） |
| `act_error` | 发送 `act` 时本地/传输异常 | `executor.py:866-868` |
| `act_not_supported` | `act_ack` 的 `ResponseCode = NOTSUPPORTED` | `executor.py:877-880` |
| `act_rejected` | `act_ack` 的 `ResponseCode = NO` | `executor.py:875-881` |
| `pos_not_connected` | 发任一 CTEL2 信号时 POS 未连接 | `executor.py:870-874` |
| `out_timeout` | `WAIT_OUT` 超过案例 `timeout_seconds` 未收到 `out` | `executor.py:840-844` |
| `out_no_outcome` | OPS Status 位 `0000` 且 DataRecord 空（内核未跑出结果） | `executor.py:994-1001`、`executor.py:1010-1014` |
| `assertion_failed` | 断言不满足（含 `out_invalid` 后无其他原因时的兜底） | `executor.py:831-832`、`executor.py:1002-1003`、`executor.py:1010-1012` |
| `android_disconnected` | run 期间卡端断线 | `executor.py:318-319` |
| `pos_disconnected` | run 期间 POS 断线 | `executor.py:320-321` |
| `android_error` | 用例内卡端 `status{state:"error"}`（案例状态 = ERROR） | `executor.py:375-386` |

* **首个原因生效**：`_fail()` 只记录第一条（后续原因不覆盖已落盘的判定结果，
  `executor.py:187-205`）；
* `out_invalid`（OPS 缺失/非法、TLV 非法）是 **ack `detail`** 而非 `failure_reason`：
  该 OUT 被 quarantine、结果投递为 `fail`，随后由 `_attempt` 兜底记为 `assertion_failed`
  （`executor.py:972-978`、`executor.py:831-832`）；
* 设计稿 §8 中列的 `card_apdu_missing` / `card_apdu_mismatch:<path>` / `card_apdu_bleed` /
  `out_ops_mismatch:<path>` / `out_datarecord_missing:<tag>` **在代码中不存在**（P2 待实现，§12.8）；
  这些语义目前由通用断言 `assertion_failed` 覆盖。

### 12.4 run 级停止策略与 `abort_reason`

* 任一案例 FAIL → **立即停止整轮**；剩余队列逐条落 `SKIPPED`
  （`note = run_stopped_after_failure:<失败的 case_id>`，`executor.py:621-637`）；
* run 级 `abort_reason`：
  * `case_failed:<case_id>:<failure_reason>`（`_fail` 组装，`executor.py:202-204`；
    断言 FAIL 路径 `executor.py:594-604`）；
  * `manual abort` / `manual stop`（`executor.py:605-610`）；
  * `server_shutdown`（host 退出时中断在途 run，`executor.py:266-283`）；
  * `run task cancelled` / `internal error: <ExcType>: <msg>`（崩溃兜底，`executor.py:511-522`）；
* run 状态：`ERROR`（`android_error`）、`ABORTED`（FAIL/abort/崩溃）、`STOPPED`（手动 stop）、
  `FINISHED`（正常跑完）；崩溃恢复的 run 为 `INTERRUPTED`（`run_store.py:217-243`）；
* 案例状态集合：`PASS / WARN / FAIL / SKIPPED / ABORTED / INCONCLUSIVE / ERROR`
  （`executor.py:85`）。

### 12.5 坏帧规则

* 见 §1：非法 JSON（长度前缀合法）→ 计入累计、跳过；累计满 5 → 断连；
  长度非法（> 1 MiB）→ 立即断连、不计入累计；
* 计数**不因成功帧重置**（`connection.py:216-221`），按连接累计（重连后重新计数）。

### 12.6 落盘契约

* `runs/<日期>/<run_id>/`：`run.json`（run 摘要 + 每案例状态）、`checkpoint.json`（写前日志，resume 依据）、
  `results/<case_id>.json`（单案例完整记录）、`quarantine.jsonl`、`trace/<case_id>.jsonl`、`host.log`；
* `results/<case_id>.json` 同时含 `attempt`（**0 基**：第几次尝试）与 `attempts`（**1 基计数**）；
* 所有 run 产物用「同目录 `.tmp` + `os.replace`」**原子写入**，不留 `.tmp`（`run_store.py:30-51`）；
* 案例记录含 `failure_reason`；run 索引（`run.json.results[case_id]`）同步该字段
  （`run_store.py:123-139`）；
* OUT 判定结果结构：`{out:{ops,datarecord,discdata,uird,kernelid}, outcome, online, resp_code, card}`
  （`judge/outcome.py:143-155`）。

### 12.7 运行配置

| 参数 | 默认 | 说明 |
|---|---|---|
| `--device-port` | 9100 | 设备协议端口（单端口，两端共用） |
| `--port` | 8000 | Web/API/WS 端口 |
| `--card-ack-timeout` | 15 | 卡端通用 ack 预算（秒） |
| `--pos-ack-timeout` | 15 | CTEL2 信号 ack 预算（秒） |
| `--trace-enabled` | 关 | 落盘卡端 APDU trace（`log{level:"trace"}`） |
| `--kernel-params-dir` | 无 | 仓库外内核参数目录（`<dir>/<brand>.json` 或 `<dir>/<brand>/<ref>.json`，外部优先于仓库内） |

### 12.8 实施与测试状态（待办）

* host 侧 v2（CTEL2 报文集 + OUT 判定/落盘/ack + 卡端链路）**已实现**；
* **`card.*` APDU 判定（P2）尚未接线**：`session.card_apdus` 只有初始化/清空、没有任何写入者，
  `_judge` 传入的 `card` 恒为空 dict（`runner/session.py:46-48`、`executor.py:972-974`），
  因此 `card.gpo.pdol.*` / `card.gac.cdol*.*` 断言当前恒为 missing；
  设计稿 §7/§8 的 `card_apdu_*` 失败原因也尚未实现；
* trace 通道当前仍受 `--trace-enabled` 控制且**只落盘不判定**（设计 R4=a 的目标是恒开 + 可判定）；
* 案例库 `act` 数据尚未迁移（现有案例只有 legacy `pos_params`，见 §8.1 提示）；
* 前端 v2 展示（`out.ops.status` + APDU）待做；
* L5/L6 真机联调**阻塞**：需 POS 与卡端都升级到 `protocol_version="2.0"` 并各自发 `[READY]`。

## 13. 术语：卡组字段统一为 `brand`

* EMV/支付领域规范术语是 **card brand / payment brand**，故全面使用 `brand`；
  取值不变（小写卡组 id：`visa`/`mastercard`/`unionpay`/`jcb`/`amex`/`discover`/`pure`）；
* 仓库内 case JSON、`brand.json`、API 路径、前端与文档统一用 `brand`；**不提供 `scheme` 兼容别名**（硬切换）；
* v1.2 中携带该字段的 `case_started` / `session_abort` 两帧已随 v2 **删除**（§12.2）；
  当前 wire 上没有任何帧携带 `brand` 字段（POS/卡端帧都不带），`brand` 仅存在于案例库与 API 层；
* 对端改动详见 `docs/protocol-change-notice-brand.md`。
