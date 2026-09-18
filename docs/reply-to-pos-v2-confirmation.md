# host 对 POS「v2 变更确认回复」的答复

> 对应 POS 文档：`v2_变更确认回复.md`（POS 2.0.0 / versionCode 15）
> 关联：`docs/protocol-v2-design.md`、`docs/change-notice-v2-pos.md`
> 状态：**待我方负责人对 D 两项拍板后定稿回传**；其余为 host 已确认项。

## A. 直接确认（无需双方改动）

| POS 项 | host 答复 |
| --- | --- |
| 1 实现确认（信封 v2 / 握手 2.0 / v1.x 报文全删，7 个帧） | **接受**，与 host 实现一致。 |
| 6.1 ACK 关联 | **type 匹配为权威**；`ref_msg_id` 仅诊断、host 不依赖。POS 现做法（type + 附带 ref_msg_id）**正确**。 |
| 6.2 CONFIG 数组形态 | **以「同名 id 逐条展开」为权威**；数组形态不采用。host 会同步修订设计文档 4（从 `value:[...]` 改为逐条）。 |
| 3 OPS 生成口径 | **接受**：优先透传 `DF8129`；取不到/长度非 8B  全 0；内核未运行  全 0 且不读残留。 |
| 5.1 ACT -> 内核映射 | **接受**（含 `DF8101=01` 沿用模拟在线批准）。 |
| 5.2 UIRD / KernelId | **接受**：UIRD 取 `DF8116`（13B），否则全 0；KernelId 本轮不上报。 |
| 5.3 可靠性/时序 | **接受**：`out`/`msg` 不等 ACK（host 收到 OUT 后会回通用 `ack`，POS 可不依赖）；断线即该用例 FAIL。 |
| 7.2 黑名单每笔先清后加 | **确认**：host 每笔下发完整配置（R2=b），语义就是「整表替换」。 |
| 7.4 `9F02/9F03/9F53` 可出现在 DataRecord | **允许**（在白名单内）。 |
| 7.5 `test_info` 缺 `TestId` 回 NO | **符合预期**（CTEL2 标注 TestId 必填）。 |
| 7.3 `DiscData` | **本轮不需要**：host 不断言 `out.discdata.*`；将来若要用，另给 tag 清单。 |

## B. host 将接受的 POS 方案（含一处需 POS 配合）

1. **DataRecord = 31-tag 白名单**（替代 host 设计文档原写的「TLV DB 全量」）：**接受**（KS8226 无全量导出 API）。
   - host 将把设计文档 6.2 改为「host 约定白名单 + 取到才报」。
   - **请把 `9F66` 加入白名单**（`QPB-B-002-01` 需要 `out.datarecord.9F66` 等于本笔下发的 TTQ），并保留
     POS 已实现的「`CONFIG.TERM` 有 9F66 时用下发值覆盖内核回读值」。即白名单变 **32 条**。
2. **OPS 全 0 的语义**：接受「内核未运行 = 全 0 + 空 DataRecord + UIRD 全 0」。

## C. host 侧将修改（P1c-2 一并落地）

1. **空组语义（重要，需 POS 确认 D1）**：`CONF_01_RemoveCRL` 的 DRL 为空时，host 当前**不发 `DRL` 项**；
   按 POS「缺失 = 不管理」会导致上一笔 DRL 残留。host 将改为：
   - 非空：逐条 `{id:"DRL", value:"<tlv>"}`；
   - **空：发一条 `{id:"DRL", value:""}`，语义 = 清空该表且不加**；`EXCEPTION_FILE` 同理；
   - `AID`/`CAPK` 非空时逐条发（本轮不出现「空 AID/CAPK」）。
2. **设计文档修订**：4 CONFIG 形态（逐条）、6.2 DataRecord（白名单）、6.3 DiscData（本轮不报）。
3. **`resp_code` 派生（见 D2）**：`DataRecord.8A` 优先；无 8A 再按 OPS Status（APPROVED00 / DECLINED05 / ONLINE REQUEST00）。

## D. 待我方负责人拍板（拍板后即回传 POS）

### D1. 「空组 = 清空」的 wire 约定

建议：`{id:"DRL", value:""}` / `{id:"EXCEPTION_FILE", value:""}` 表示「清空该表且不加」；
`value:null` 或 id 缺失 = 本笔不管理该表。
- 备选：新增显式 id（如 `CLEAR_DRL`）。
- **请负责人确认用哪一种**（我倾向 `value:""`，改动最小）。

### D2. 「无内核 outcome」时 `resp_code` 怎么表达（91/80/16）

现状：`resp_code = DataRecord.8A ?? OPS.status`。POS 指出「卡侧失败 / 未挥卡 / POS 终止」通常没有 8A。
host 建议二选一：

- **(a) 推荐**：内核正常跑完  POS **一律上报 `8A`**（91/16/80 都走 8A，host 不需要私有 tag）；
  内核未跑（卡侧失败/未挥卡/POS 终止） POS 按 3 发 OPS 全 0，**host 判该用例 FAIL（`out_no_outcome`）**，
  视为「本笔没有有效结果」，不伪造 05/91/80。
- **(b)**：内核未跑时由 POS 用 OPS Status 表达：`END APPLICATION(0100)` -> host 映射 `resp_code="80"`、
  `TRY AGAIN(0111)` -> `"91"`（需负责人认可该映射与用例 `any_of` 集合匹配）。

**请负责人选 (a) 或 (b)**（我倾向 (a)：不伪造结果；`any_of ['05','91',...]` 的用例本来就要求真实内核结果）。

### D3. 附带（低风险，默认按此执行，除非反对）

- 启动期 per-config INFO（87 行 / 约 29 KB）**是否降为 DEBUG**（Round-1 报告 F3 附带项）：默认**降**，减少启动噪音与管道风险。

## E. 下一步

- 负责人确认 D1/D2/D3 后，host 回传本答复定稿给 POS；
- 同时进 P1c-2：迁移 `test_runner_timeout.py` / `test_v12_fixes.py`、`docs/protocol.md` v2、前端 v2 展示、
  以及本答复 C 的 host 改动。
