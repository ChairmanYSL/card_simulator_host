# POS 端变更通知：协议 v2（Mastercard CTEL2 TEI）

> **状态：当前生效（final）** —— 本文档已按 **2026-09-18 定稿**的 D0/D1/D2/D3 与确认项
> （§A/§B/§C）修订，是 POS 端的**唯一对接口径**；与本文档旧版本冲突时，**以本版为准**。
> 影响端：**POS 交易应用**（KS8226）。窗口：与 host **同窗口升级**，**不兼容 v1.2**（硬切换）。
> 权威依据（只读引用，不在本通知重复定义）：`docs/protocol.md`（v2 唯一权威协议，
> §1 / §2 / §6.3 / §6.5 / §7）、`docs/reply-to-pos-v2-confirmation.md`（host 答复，**已定稿**）；
> 设计冻结稿 `docs/protocol-v2-design.md`；
> Mastercard CTEL2 v4.1 TEI（`refernce/m_ctel2_en-us-2025-06-17.pdf` p78-93）。
> 交付：本通知由 host 侧生成，只读交付；host **不修改 POS 仓库**。
>
> **修订记录（不删除历史）**
>
> * **2026-09-20（r2，当前）**：按定稿结论重写以下口径 —— CONFIG 改为**同名 id 逐条展开**并补**空组
>   `value:""` = 清空**（D1）；`DataRecord` 由「TLV DB 全量」改为 **host 白名单 + 取到才报**
>   （32 条 = 31 条 + 新增 `9F66`，§B/1）；补 **`resp_code` 派生**与**内核未跑的 `out_no_outcome`**（D2(a)）；
>   补**入站信封 `v` 校验**（D0）；明确 v1.2 POS 报文**全部删除、无兼容模式**，且 **`session_abort` 已不存在**。
> * 2026-09-15（r1，**已被 r2 取代**）：首版。其 CONFIG「单 item 的 value = 数组」形态与
>   「`DataRecord` = TLV DB 全量」表述**均作废**，不得再作为实现依据。

## 1. 硬切换

- 握手 `protocol_version` 必须为 **`"2.0"`**；缺失或 `<2.0` -> `error{protocol_mismatch}` 并断开
  （确定性失败，重连无用）。
- 信封 `v` 必须为 **`2`**（**每一帧**；收发都校验，入站校验见 §2）。
- v1.2 的 POS 报文**全部删除，无兼容模式、不与 v2 并存**：
  `config_push{pos_config}`、`config_push{pos_kernel_config}`、`case_started`、`transaction_result`、
  `session_abort`、`device_config`。
- **`session_abort` 不再存在**：CTEL2 无对应信号。异常收尾（abort / FAIL / 断线 / host 退出）
  **没有任何帧**通知 POS「本轮终止」；POS 不要等待、不要实现该帧，按自身交易流程自然结束即可。
- TCP 角色不变：POS 仍是客户端，连 host `9100`（R1=b）。

## 2. 帧格式（长度前缀不变，信封 `v` 收发都校验）

`4B 大端长度 + UTF-8 JSON`；信封 `{"v":2,"type":"<小写>","msg_id":"<uuid>","ts":<epoch>}`。

- **入站信封 `v` 校验（D0，新增）**：

  | 阶段 | 条件 | host 行为 |
  |---|---|---|
  | 握手 | 首帧 `v != 2` | 回 `error{code:"bad_frame"}` 并**断开** |
  | 握手之后 | 任一帧 `v != 2` | 记 warning 并**断开连接** |

  即 POS 发出的**每一帧**都必须带 `"v":2`；否则连接被断 -> 当前案例 FAIL（§5）。
- 其余传输层规则不变：最大帧 **1 MiB**（超出立即断开）；非法 JSON 但长度前缀合法按**坏帧累计**，
  累计满 5 才断开。

## 3. host -> POS 信号

| `type` | 结构 |
|---|---|
| `test_info` | `signalData:[{id:"TestId",value:"<case_id>"},{id:"InterfaceVersion",value:"20250617"},{id:"SessionId",value:"<run_id>"},{id:"DekDetId",value:null}]` |
| `config` | `signalData:[{id:"CONF_NAME",value:"<ref>"},{id:"AID",value:"<TLV hex>"}…,{id:"CAPK",value:"<TLV hex>"}…,{id:"DRL",value:"<TLV hex>"}…,{id:"EXCEPTION_FILE",value:"<TLV hex>"}…,{id:"TERM",value:"<TLV hex>"},{id:"VERSION",value:"4.2.0"}]` —— **同名 id 逐条展开**（`…` = 该 id 可重复多条），见下 |
| `act` | `signalData:[{id:"9F02",value:"000000001500"},{id:"9F03",value:"000000000000"},{id:"5F2A",value:"0978"},{id:"5F36",value:"02"},{id:"9A",value:"240412"},{id:"9F21",value:"133333"},{id:"9C",value:"00"}, ...]` |

- `value` 一律 hex（host 已归一化为**大写**）；`null` = 该 tag 不发送；`""` = 长度 00
  —— **例外**：`DRL` / `EXCEPTION_FILE` 的 `""` = 清空该表（见下）。
- `test_info`：`TestId` = 案例 id（必填；缺 `TestId` 时 POS 回 `NO`）；`SessionId` = run id；
  `DekDetId` 恒为 `null`。
- **CONFIG 形态（权威 = 同名 id 逐条展开，R2=b + D1）**，**不使用**「单个 item 的 value = 数组」的形态：

  * `AID` / `CAPK` / `DRL` / `EXCEPTION_FILE`：**每条一个 item**（有几个就发几条同 id 项）；
  * `DRL` / `EXCEPTION_FILE` **在每一笔下发 CONFIG 的案例中恒存在**（至少一条）：
    - 非空 -> 逐条 `{id:"DRL", value:"<tlv>"}`（整表替换）；
    - **空 -> 发一条 `{id:"DRL", value:""}`，语义 = 清空该表且不加**（`CONF_01_RemoveCRL` 依赖此语义，
      否则上一笔的 DRL/黑名单会残留）；
    - `value:null` 或 **id 缺失** = 本笔不管理该表；
    - **不新增** `CLEAR_DRL` / `CLEAR_EXCEPTION_FILE` 之类显式 id。
  * `CONF_NAME` = `kernel_params_ref`（缺省 = case_id），**信息用**（记录/日志），
    值是 ref 本身（如 `CONF_16`），**不是** `"<brand>/<ref>"`；
  * `TERM` = 多条 TLV **直接拼接**的 hex（= CONF.term + PROFILE.term）；
  * `VERSION` 非空时随 CONFIG 下发；
  * 应用语义沿用 v1.2 的 `updateAidParam/updateCAPKParam/setTerminalParam` 与去重/覆盖规则（整表替换）。
- `config` **仅当本案例内核参数非空**（AID/CAPK/DRL/EXCEPTION_FILE/TERM 全空则不发）才下发；
  此时 `test_info` 之后**直接** `act`，POS 使用自带参数表。不要因「没收到 config」报错。
- `ACT` 交易数据 tag/编码见设计 §5；金额固定 6B BCD，币种指数在 `5F36`；
  `9F1A`（终端国家码）由 `CONFIG.TERM` 提供，不在 ACT 里；
  `9F66`（TTQ）等**终端级 tag 一律走 `CONFIG.TERM`**；
  host 通过 ACT 下发 `DF8101="01"`，沿用「模拟测试主机在线批准」语义（未下发时 POS 保持真实行为）。
- 一次 `test_info` 可对应多笔 `act`（双拍），`config` 可被多笔 `act` 复用。

## 4. POS -> host 信号

| `type` | 结构 |
|---|---|
| `test_info_ack` | `signalData:[{id:"ResponseCode",value:"YES"}]`，或 `"NO"` + 可选 `{id:"Description",value:"..."}` |
| `config_ack` | 同上 |
| `act_ack` | `signalData:[{id:"ResponseCode",value:"YES"\|"NO"\|"NOTSUPPORTED"}]` |
| `msg` | `signalData:[{id:"UIRD",value:"<13B hex>"}]`（0..n，按发生顺序） |
| `out` | `signalData:[{id:"OPS",value:"<8B hex>"},{id:"DataRecord",value:"<TLV hex>"\|null},{id:"DiscData",value:"<TLV hex>"\|null},{id:"UIRD",value:"<13B hex>"\|null},{id:"KernelId",value:"..."}]`（>=1） |

- **ACK 关联**：三个 `*_ack` **按帧 `type` 关联**为权威（同一连接同一时刻只有一个信号在途）；
  `ref_msg_id` 仅作诊断，host **不依赖**。`ResponseCode` 的 id 解析对**大小写不敏感**。
- `OPS`（DF8129，**8B**）：**优先透传内核 `DF8129`**；取不到或长度非 8B -> **全 0**；
  内核未运行 -> 全 0 且**不读残留值**。位表见设计 §6.1（来源 `refernce/OPS_description_*.png`）。
- `DataRecord` = **host 约定的 tag 白名单 + 「取到才报」**（**不是** TLV DB 全量，
  KS8226 无全量导出 API）：
  * 白名单共 **32 条 = 31 条 + `9F66`**；`9F66` 是本轮**新增**项：`QPB-B-002-01` 断言
    `out.datarecord.9F66` 等于本笔下发的 TTQ，并保留 POS 已实现的
    「`CONFIG.TERM` 含 `9F66` 时用下发值覆盖内核回读值」；
  * 其余 31 条的清单由 host/POS 约定件承载（**本通知不复述、不臆造**）；
  * **取不到的 tag 不出现在 `DataRecord` 中**（不要补 0、不要编造）；
    `9F02` / `9F03` / `9F53` 允许出现（在白名单内）；`OPS` 的 Data Record Present 位为 0 时不应上报；
  * host 侧**不做白名单裁剪/校验**：收到什么就解码什么（`out.datarecord.<TAG>`）。
- `DiscData` = POS 私有数据 TLV；**本轮 host 不断言 `out.discdata.*`，POS 可不报**
  （将来若要用，host 另给 tag 清单）。
- `UIRD` 取 `DF8116`（**13B hex**），取不到则全 **0**；host 记入 `session.pos_msgs`，本轮不判定。
- `KernelId`：**本轮 POS 不上报**。
- **`msg` / `out` 不等 ACK**：host 收到后会回通用 `ack`（POS 可完全不依赖）；`out` 按发生顺序**即时**发送，
  一次 ACT 可有多条（双拍）。
- 幂等：同一 `msg_id` 的 `out` 在**当前 attempt 内**去重 -> ack `{"ok":true,"detail":"duplicate"}`，
  host 不再判定；收到 `ok:true` 后**不要重发**，只有 ack `ok:false`（如 `detail:"persist_failed"`）
  才换新 `msg_id` 重试。

### 4.1 `resp_code` 派生与「内核未跑」（D2(a)）

- 内核**正常跑完** -> POS **一律上报 `DataRecord.8A`**（`91`/`16`/`80` 都走 `8A`，host 不需要私有 tag）。
- host 派生 `resp_code`：**优先 `DataRecord.8A`**；无 `8A` 再按 OPS Status
  （`APPROVED`->`00`、`DECLINED`->`05`、`ONLINE REQUEST`->`00`）；其余（含内核未跑）为**空串**。
- **内核未跑**（卡侧失败 / 未挥卡 / POS 终止）-> POS 按 §4 发 **OPS 全 0 + 空 DataRecord + UIRD 全 0**；
  host 判该用例 **FAIL**（verdict 非 PASS 时案例记 `failure_reason = out_no_outcome`，并作为整轮停止原因），
  视为「本笔没有有效结果」；**host 不伪造 `05`/`91`/`80`，也不要求 POS 伪造**。
- OPS **缺失 / 非 8B / 非法 hex**，或 `DataRecord`/`DiscData` 为非法 TLV -> 该 OUT **非法**：
  host ack `detail:"out_invalid"`，该 OUT 进 quarantine，案例 FAIL。

## 5. 时序与失败

```
host: test_info  ->  pos: test_info_ack
host: config     ->  pos: config_ack     （仅当本案内核参数非空，见 §3）
host: act        ->  pos: act_ack
pos:  msg*（0..n）->  host: ack          （POS 可不依赖）
pos:  out（>=1）  ->  host: 判定+落盘 -> ack
```

- 每个 ACK 默认 **15s** 内回；超时 -> host 判该案例 FAIL
  （`test_info_ack_timeout` / `config_ack_timeout` / `act_ack_timeout`）；
- `ResponseCode = NO` -> 案例 FAIL（`*_rejected`）；`act_ack = NOTSUPPORTED` -> 案例 FAIL
  （`act_not_supported`，本测试要求支持 ACT 交易数据）；
- POS 未连接时发信号 -> 案例 FAIL（`pos_not_connected`）；
- **断线** -> 当前案例 FAIL 并**停止整轮**（POS 断开 = `pos_disconnected`；host 不再只告警）；
- 案例 `WAIT_OUT` 超时 -> 案例 FAIL（`out_timeout`）；
- 任一 FAIL -> 立即停止整轮，剩余队列逐条落 SKIPPED；
- **没有任何「运行中止」通知帧**（`session_abort` 已删除）：POS 无需实现、也收不到。

## 6. 回报要求

1. 确认已实现 `test_info` / `config` / `act` + 3 个 `*_ack`、`msg`、`out`（v2 CTEL2 报文集）；
2. 升级版本号 + commit。

> 历史提问（**已关闭，无需再答**）：r1 的第 2/3/4 项（CONFIG 部分失败怎么回 `NO`、OPS 生成口径、
> `DataRecord` 的 tag 范围）已由 POS《v2 变更确认回复》+ host 答复
> `docs/reply-to-pos-v2-confirmation.md`（§A/3、§A/6.2、§B/1、§D1）定稿：
> CONFIG = 逐条展开 + 空组 `value:""` 清空；OPS = 优先透传 `DF8129`；
> `DataRecord` = 32-tag 白名单「取到才报」。本通知已按此更新（见 §3、§4）。
