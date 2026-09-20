# 联机（Online）流程实现方案草案 — 待裁定

> **状态：草案，未实施**（2026-09-20）。依据用户 2026-09-20 对 online 处理机制的说明整理。
> 涉及 wire 格式的三处决策点**尚未裁定**，故不改任何代码。

## 1. 需求（用户原话要点）

1. 后台通过案例把卡片配置成**走联机交易**
2. POS 在处理 `process()` 返回值为 **22（联机处理）** 时，**向后台发送一个联机报文**
3. 若后台案例设置为**默认联机成功** → **不下发**联机数据，在接收到 online 的 outcome 之后**直接判定案例成功**
4. 若有**联机失败**的测试需求 → 后台向 POS 返回联机相关数据，例如
   `8A023030`，另可含
   `91`（Issuer Authentication Data，b，8–16）、
   `71`（Issuer Script Template 1，b，var ≤256）、
   `72`（Issuer Script Template 2，b，var ≤256）
5. POS 通过接口 **`emv_set_online_result`**（Java 侧 `EmvInterface.setOnlineResult`）把联机数据设置进内核
6. 内核继续处理，返回**联机处理之后的返回值**（103/104 等）
7. **通过两次上送 outcome** 比对交易结果是**联机批准**还是**联机拒绝**

## 2. 已核实的现状（证据）

| 项 | 现状 | 证据 |
| --- | --- | --- |
| `process()` 返回 22 | `EMV_STATUS.EMV_REQ_GO_ONLINE = 22`（SDK 常量表） | `javap -constants` |
| **POS 当前行为** | **本地决定，不与 host 交互**：`spec.testOnlineApproval`（`DF8101=01`）→ `setOnlineResult(SUCC_ACCEPT, arpc8)`；否则 `setOnlineResult(FAIL)` | `KernelDriver.java:447-457` |
| 卡端 `acBehavior` | 已支持 `online_request` / `offline_approved` / `offline_declined` | `cases/models.py:375`、`validation.py:49` |
| **host 无联机处理** | 全仓无"联机报文/联机应答"实现；`online_request` 仅出现在卡端配置 | `grep online_request backend/` |
| **host 的 `out` 语义** | 每个 `out` **都判定并 `deliver_result()`**；第二次 `out` 时 `is_waiting_result()` 已 False → **进 quarantine** | `executor.py:453-490` |
| 协议对多次 `out` 的规定 | "一次 `act` 可 **≥1 条（dual tap）**" —— 允许，但**语义是多次点击**，非"联机两阶段" | `protocol.md:296` |
| **协议缺口** | **没有"联机报文 / 联机应答"消息**；`msg` 帧现有用途是 UIRD | `protocol.md:295`、`Signals.java:29,49` |

## 3. 目标流程（我的理解，待确认）

```
卡端 acBehavior=online_request → 卡返回 ARQC
  → POS 内核 process() 返回 22 (EMV_REQ_GO_ONLINE)
  → POS 向 host 发「联机报文」
  → host 按案例配置：
        ├─ 默认联机成功：不回数据，直接判案例成功
        └─ 联机失败测试：回联机数据（8A / 91 / 71 / 72）
  → POS 收到应答 → setOnlineResult(数据) → 内核继续 → 返回 103/104
  → POS 发第二次 out（最终 outcome）
  → host 比对两次 outcome → 判联机批准 / 联机拒绝
```

## 4. 三处待裁定（wire 格式）

### Q1 「联机报文」如何承载？
- (a) **复用 `out`**：第一次 `out` 的 OPS status = `ONLINE REQUEST` 即作为联机请求
  - 优点：不动协议；`out` 本就允许多次
  - 风险：host 需区分"联机阶段的 out"与"最终 out"（靠 OPS status）
- (b) **复用 `msg`**：新增信号 `{id:"OnlineRequest", value:"1"}`（`msg` 现语义为 UIRD，需扩义）
- (c) **新增消息类型**（如 `online_request`）

### Q2 「联机数据」如何回给 POS？
- (a) **新增 host→POS 消息**（如 `online_result`，`signalData:[{id:"8A",value:"3030"},{id:"91",…},{id:"71",…},{id:"72",…}]`）
- (b) 复用现有 `ack` 的 `detail` 字段承载（`detail` 现为字符串，需约定编码）
- (c) 其他

### Q3 host 判定时机（**必须改 host**）
现状是"收到 `out` 即判定并结束 attempt"，联机场景下第二次 `out` 会被 quarantine。
需确认：
- (a) **默认联机成功 → 只看第一次 `out`（online_request）即判成功**（不再等第二次）
- (b) **一律等第二次 `out` 才判定**（第一次仅记录）
- (c) 按案例配置区分

## 5. 实施切分（裁定后执行）

| 端 | 改动 |
| --- | --- |
| **卡端** | **无**（`acBehavior=online_request` 已支持） |
| **host** | ① 案例配置新增"联机成功/联机数据"字段；② 接收联机报文；③ 按 Q3 调整 attempt 结束时机；④ 联机数据回传 |
| **POS** | ① `KernelDriver` 的 `EMV_REQ_GO_ONLINE` 分支改为"发联机报文 + 等应答"；② 组 TLV（8A/91/71/72）→ `setOnlineResult`；③ 第二次 `out` |

## 6. 前置阻塞（与本需求无关）

**POS 读卡当前仍失效**（2026-09-20 17:12 实测）：设备重启（uptime 798s）后依旧
`reader.open rc=0` → `search_target_begin` → 20s → `waitForCard rc=-108`；
卡端 `registered 3 AIDs`、AID 在路由表，但**零 APDU**。
需人工确认手机与 KS8226 读卡区的物理贴合（本次与 15:11 那次"重启即恢复"不同）。
