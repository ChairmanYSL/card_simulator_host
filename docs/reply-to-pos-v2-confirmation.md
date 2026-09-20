# host 对 POS「v2 变更确认回复」的答复

> 对应 POS 文档：`v2_变更确认回复.md`（POS 2.0.0 / versionCode 15）
> 关联：`docs/protocol-v2-design.md`、`docs/change-notice-v2-pos.md`
> 状态：**已定稿**（2026-09-18 负责人对 D1/D2/D3 拍板完毕，见 §D；可直接回传 POS）。

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

## C. host 侧修改（已落地，工作区待提交）

1. **空组语义（D1 已定，已实现）**：`CONF_01_RemoveCRL` 的 DRL 为空时，host 原先**不发 `DRL` 项**；
   按 POS「缺失 = 不管理」会导致上一笔 DRL 残留。现已改为：
   - 非空：逐条 `{id:"DRL", value:"<tlv>"}`；
   - **空：发一条 `{id:"DRL", value:""}`，语义 = 清空该表且不加**；`EXCEPTION_FILE` 同理；
   - `AID`/`CAPK` 非空时逐条发（本轮不出现「空 AID/CAPK」）。
   - 代码：`backend/emv_host/runner/executor.py::_config_frame`。
2. **设计文档修订（已完成）**：§4 CONFIG 形态（逐条 + 空组语义）、§6.2 DataRecord（32-tag 白名单 + 取到才报）、
   §6.3 DiscData（本轮不报）、§6.1 `resp_code` 口径、§8 失败原因码（新增 `out_no_outcome` / `*_error` / `pos_not_connected`）。
3. **`resp_code` 派生（D2(a) 已定，已实现）**：`DataRecord.8A` 优先；无 8A 再按 OPS Status
   （APPROVED→`00` / DECLINED→`05` / ONLINE REQUEST→`00`）；内核未跑 → `resp_code=""` + 用例 FAIL（`out_no_outcome`）。
   代码：`backend/emv_host/judge/outcome.py`、`backend/emv_host/runner/executor.py`。
4. **入站 `v` 校验（D0，已实现）**：`backend/emv_host/protocol/connection.py`。
5. **启动期日志降噪（D3，已实现）**：`backend/emv_host/cases/loader.py`。

> 状态口径：以上为 **host 仓库内改动 + 本机 pytest 实测**（Round-2：① 155 passed / ② 仅 F4 的 14 个），
> **尚未与 POS 设备联调验证**。

## D. 负责人拍板结果（2026-09-18）

### D1. 「空组 = 清空」的 wire 约定 —— **采纳建议方案**

`{id:"DRL", value:""}` / `{id:"EXCEPTION_FILE", value:""}` = **清空该表且不加**；
`value:null` 或 id 缺失 = 本笔不管理该表。**不新增显式 id**（如 `CLEAR_DRL`）。

- host 侧已按此实现（`executor._config_frame`：`DRL`/`EXCEPTION_FILE` **每笔恒发**，空表发一条 `value:""`）。
- 设计文档 §4 已同步为「同名 id 逐条展开 + 空组 = `value:""` 清空」。
- **请 POS 按此语义实现**：收到 `value:""` 必须清空对应表；收到 id 缺失时不动该表。

### D2. 「无内核 outcome」时 `resp_code` 怎么表达 —— **采纳 (a)**

- 内核正常跑完 → POS **一律上报 `8A`**（`91`/`16`/`80` 都走 `8A`，host 不需要私有 tag）；
- 内核未跑（卡侧失败 / 未挥卡 / POS 终止）→ POS 按 §3 发 **OPS 全 0 + 空 DataRecord + UIRD 全 0**；
  host 判该用例 **FAIL / `out_no_outcome`**，视为「本笔没有有效结果」，**不伪造 `05`/`91`/`80`**。
- 实测口径（`resp_code`）：优先 `DataRecord.8A`；无 `8A` 再按 OPS Status（`APPROVED`→`00`、`DECLINED`→`05`、
  `ONLINE REQUEST`→`00`）；内核未跑时 `resp_code` 为**空串**。
- 设计文档 §6.1、§8 已同步（新增 `out_no_outcome`）。
- 注：`any_of ['05','91',...]` 类用例本就要求真实内核结果，故 (a) 不会削弱其判定力。

### D3. 启动期 per-config INFO 降噪 —— **同意降级**

`cases/amex/pos_configs` 的 87 行 / 约 29 KB 启动日志已由 `logger.info` 降为 **`logger.debug`**
（`cases/loader.py`）。依据：Round-1 报告 §4（F3）——该日志量超过 Windows 匿名管道 4 KB 缓冲，
会让「真实子进程 + `stdout=PIPE` 不读取」的进程阻塞在 bind 之前。

### D0（附加，本轮一并裁决）

- **入站帧 `v` 校验**：采纳「拒绝」——握手期 `v != 2` 回 `HandshakeError("bad_frame")`；
  运行期 `v != 2` 记 warning 并断开（`protocol/connection.py`）。即信封 `v` 由「只发不收」改为**收发都校验**。

## E. 下一步

- 本答复自本日起为**定稿**，可直接回传 POS；
- host 侧 P1c-2 剩余：迁移 `test_runner_timeout.py` / `test_v12_fixes.py` 到 v2、`docs/protocol.md` 重写为 v2、
  前端 v2 展示（`out.ops.status` + APDU）。
