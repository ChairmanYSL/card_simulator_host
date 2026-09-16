# 协议通知：卡组字段由 `scheme` 更名为 `brand`

> 影响端：**POS 端 + 卡端（Android）**
> 窗口：与 v1.2 同窗口升级；协议版本号仍为 `1.2`（硬切换，无 `scheme` 兼容别名）。

## 1. 变更内容

| 帧 | 旧字段 | 新字段 | 取值 |
|---|---|---|---|
| `case_started` | `scheme` | **`brand`** | 小写卡组 id，如 `visa`/`amex`/... |
| `session_abort` | `scheme` | **`brand`** | 同上 |

示例：

```json
{"type":"case_started","case_id":"AMEX-CL-001","attempt_id":"...",
 "brand":"amex","entry_mode":"contactless","operator_instructions":{...}}
{"type":"session_abort","run_id":"...","brand":"amex","mode":"contactless",
 "case_id":"...","attempt_id":"...","reason":"case_failed","action":"stop"}
```

## 2. 两端需要做的

- **POS 端**：解析 `case_started` / `session_abort` 时读 `brand`；`case_id`/`attempt_id`
  的配对语义不变。
- **卡端**：`card_config`/`status` 等帧不含该字段；若日志/逻辑里用了 `scheme`，同步改名即可。
- host 侧其它 wire 字段、编码、时序、ack 矩阵全部不变。

## 3. 术语说明

Visa / Mastercard / Amex / JCB / UnionPay / Discover / PURE 在 EMV 与支付领域的规范叫法是
**card brand / payment brand**；`scheme` 容易与其它概念混淆，故统一为 `brand`。

## 4. 兼容策略

**硬切换**：host 只发 `brand`，不发 `scheme`。两端必须与 host 同窗口升级；
握手 `protocol_version` 仍要求 `"1.2"`。
