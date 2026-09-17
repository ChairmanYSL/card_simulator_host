# POS 端变更通知：协议 v2（Mastercard CTEL2 TEI）

> 影响端：**POS 交易应用**（KS8226）。窗口：与 host **同窗口升级**，**不兼容 v1.2**（硬切换）。
> 依据：`docs/protocol-v2-design.md`；Mastercard CTEL2 v4.1 TEI
> （`refernce/m_ctel2_en-us-2025-06-17.pdf` p78-93）。
> 交付：本通知由 host 侧生成，只读交付；host **不修改 POS 仓库**。

## 1. 硬切换

- 握手 `protocol_version` 必须为 **`"2.0"`**；缺失或 `<2.0` -> `error{protocol_mismatch}` 并断开。
- 删除 v1.2 的 POS 报文：`config_push{pos_config}`、`config_push{pos_kernel_config}`、
  `case_started`、`transaction_result`、`session_abort`、`device_config`。
- TCP 角色不变：POS 仍是客户端，连 host `9100`（R1=b）。

## 2. 帧格式（不变）

`4B 大端长度 + UTF-8 JSON`；信封 `{"v":2,"type":"<小写>","msg_id":"<uuid>","ts":<epoch>}`。

## 3. host -> POS 信号

| `type` | 结构 |
|---|---|
| `test_info` | `signalData:[{id:"TestId",value:"<test名>"},{id:"InterfaceVersion",value:"20250617"},{id:"SessionId",value:"<run_id>"},{id:"DekDetId",value:null}]` |
| `config` | `signalData:[{id:"CONF_NAME",value:"<brand>/<ref>"},{id:"AID",value:[...]},{id:"CAPK",value:[...]},{id:"DRL",value:[...]},{id:"EXCEPTION_FILE",value:[...]},{id:"TERM",value:"<TLV hex>"},{id:"VERSION",value:"..."}]` |
| `act` | `signalData:[{id:"9F02",value:"000000001500"},{id:"9F03",value:"000000000000"},{id:"5F2A",value:"0978"},{id:"5F36",value:"02"},{id:"9A",value:"240412"},{id:"9F21",value:"133333"},{id:"9C",value:"00"}, ...]` |

- `value` 一律 hex；`null` = 该 tag 不发送；`""` = 长度 00。
- `CONFIG` **内嵌完整配置**（R2=b），不是 CTEL2 原生的「仅 CONF_NAME」：
  POS 收到后按 AID/CAPK/DRL/EXCEPTION_FILE/TERM 各自应用（沿用 v1.2 的
  `updateAidParam/updateCAPKParam/setTerminalParam` 语义与去重/覆盖规则）。
- `ACT` 交易数据 tag/编码见设计 5；金额固定 6B BCD，币种指数在 `5F36`；
  `9F1A`（终端国家码）由 `CONFIG.TERM` 提供，不在 ACT 里。

## 4. POS -> host 信号

| `type` | 结构 |
|---|---|
| `test_info_ack` | `signalData:[{id:"ResponseCode",value:"YES"}]`，或 `"NO"` + 可选 `{id:"Description",value:"..."}` |
| `config_ack` | 同上 |
| `act_ack` | `signalData:[{id:"ResponseCode",value:"YES"\|"NO"\|"NOTSUPPORTED"}]` |
| `msg` | `signalData:[{id:"UIRD",value:"<13B hex>"}]`（0..n，按发生顺序） |
| `out` | `signalData:[{id:"OPS",value:"<8B hex>"},{id:"DataRecord",value:"<TLV hex>"\|null},{id:"DiscData",value:"<TLV hex>"\|null},{id:"UIRD",value:"<13B hex>"\|null},{id:"KernelId",value:"..."}]`（>=1） |

- `OPS`（DF8129，8B）位表见设计 6.1（来源 `refernce/OPS_description_*.png`）；
- `DataRecord` = 该笔交易 TLV DB 的**全量 tag**（`CreateEMVDataRecord()`），host 从中取断言 tag；
- `DiscData` = 私有数据（含 Error Indication）；UIRD/KernelId 可 null；
- 一次 ACT 可有多条 `msg` / `out`（dual tap）；`out` 按发生顺序即时发送。

## 5. 时序与失败

```
host: test_info  ->  pos: test_info_ack
host: config     ->  pos: config_ack
host: act        ->  pos: act_ack
pos:  msg*  ->  pos: out（>=1）
```

- 每个 ACK 默认 15s 内回；超时 -> host 判该案例 FAIL；
- `act_ack=NOTSUPPORTED` -> 案例 FAIL（本测试要求支持 ACT 交易数据）；
- ACK 的 `msg_id` 用对应请求的 `msg_id` 作 `ack_msg_id`（沿用现有 ack 机制）；
- 断线 -> 该案例 FAIL。

## 6. 回报要求

1. 确认已实现 `TEST_INFO/CONFIG/ACT` + 3 个 ACK、`MSG`、`OUT`；
2. 给出 CONFIG 应用的返回语义（部分失败怎么回 NO）；
3. 给出 OPS 生成口径（是否直接透传内核的 DF8129）；
4. 给出 DataRecord 的 tag 范围（是否 TLV DB 全量）；
5. 升级版本号 + commit。
