# 卡端变更通知：空记录（`70 00`）支持

> 影响端：**卡端 Android app**（HCE 卡模拟）。交付：本通知由 host 侧生成，只读交付；
> host **不修改卡端仓库**。
> 触发案例：`3M08-5132A`（参考日志
> `E:\Work\KSAPI\ksapi-entrypoint-arch-prj\Log\3M08-5132A(PPS_MC_ATM).txt`）。
> 现状：host 档案模型已放开 `records[].fields: []`（原先 `min_length=1`），
> 但**卡端需明确支持**该口径，否则该案例第二笔读取会失败。

## 1. 背景（日志事实）

该日志 AFL 第 2 项 = `10 01 02 01`（**SFI 2，records 1-2**，ODA 计数 1），终端按 AFL 依次读：

| 请求 | 日志里卡端的应答 |
|---|---|
| `00 B2 01 14 00`（SFI 2/1） | `70 81 BC {8F=F1, 9F32=03, 92(空), 90=176B}` + `9000` |
| `00 B2 02 14 00`（SFI 2/2） | **`70 00` + `9000`（空记录：模板存在、内容为空）** |

即：**"配置了但内容为空" 与 "没有这条记录" 是两种不同应答**，终端按 AFL 索取时必须能区分。

## 2. 需求

| 档案（`profile.records[]`） | 卡端应答 |
|---|---|
| 配置了 `(sfi, record)`，`fields` **非空** | “`70 <len> <TLVs>` + `9000`”（现状不变） |
| 配置了 `(sfi, record)`，`fields` = **空数组** | **“`70 00` + `9000`”**（存在但无内容） |
| **未配置** 的 `(sfi, record)` | `6A83`（现状不变） |

- 空记录只在 `records[]` **显式列出**时发生；`records` 缺省/空数组（旧行为）不受影响；
- 空记录不参与 ODA（本案例 `AIP=1880`，无 SDA/DDA/CDA，故与证书链无关）；
- wire 协议零变化：host 只是把 `fields: []` 原样放进 `card_config.profile`，
  卡端按上表解释即可（不需要新增字段名）。

## 3. 请一并确认的一个口径：**空字符串字段**

同一条记录里 `issuerPublicKeyRemainder` 为空串（日志 SFI 2/1 的 `92 00`：**tag 存在、长度 0**）。
请确认卡端口径：

- A（建议，与日志一致）：空串 → 发 **`<tag> 00`**（present-but-empty）；
- B：空串 → 该 tag **省略**。

> 本案例无 ODA，两种口径都不影响终端判定；但请定下口径并回执，后续 SDA/CDA 档案会用到
> （`92` 的长度直接影响 ICC/发卡行公钥恢复）。

## 4. 验收

1. 用 `cases/mastercard/contactless/cases/3M08-5132A.json`（配置 `PPS_MC_ATM`）跑：
   SFI 2/2 必须回 `70 00 9000`，终端能读满 AFL 的 4 条记录（2/1、2/2、3/1、3/2）；
2. 现有回归不变：非空记录照常、未配置记录仍 `6A83`；
3. 回报：确认空记录口径 + §3 的空字符串口径 + 版本号/commit。

## 5. host 侧已做的配套改动（供对照）

- `backend/emv_host/cases/models.py`：`RecordConfig.fields` 放开为可为空（附注释指向本通知）；
- `docs/protocol.md` §8 CardProfile 字段表：`records[]` 行注明 `fields: []` = 空记录；
- 案例 `3M08-5132A` 的 SFI 2/2 以 `"fields": []` 表达，案例库加载 0 error。
