# CAPK DF03 全量审计与修正（2026-10-10）

> 口径（用户确认）：**DF03 = SHA1(9F06 ‖ 9F22 ‖ DF02 ‖ DF04)**，四个值均为裸字节
> （无 tag / 无长度；模数按原始字节参与）。
> 实现：`backend/emv_host/protocol/kernel_tlv.py:191-202`（`compute_capk_checksum`），
> 加载期校验见 `backend/emv_host/cases/validation.py:556-566`（`trust_checksum=True`
> → 不符仅 warning，不阻断）。
> 审计脚本：`tmp/audit_all_capk.py`（一次性扫描；如需长期保留可提升为 `tools/`）。
>
> **状态：已修正** —— 唯一不符的公钥 `A000000025/07` 已按公式更新（见「根因」「处置」）。

## 扫描范围

`cases/**/*.json` 中所有 CAPK 列表：命名配置库（`*/pos_configs/*.json` 的 `caps`）与
案例内联配置（`kernel_params.caps`）。`index.json` / `.gen_cases_manifest.json` 排除。

| 项 | 数量 |
|---|---|
| 扫描文件 | **85** |
| CAPK 条目 | **897** |
| 缺 DF03 的条目 | **0** |
| 含不符条目的文件 | **32** |
| **不符的公钥（去重后）** | **1** |

## 结论：只有一条公钥不符

| 项 | 值 |
|---|---|
| RID / 索引 | `A000000025` / `07` |
| 模数长度 / 指数 | 126 字节 / `03` |
| 文件里的 DF03（实际） | `4C3681CD1D207B2BDC6E4DD5E33657F356496994` |
| 计算值（期望） | `5DC21CFD0B4348E8F374ECD71406580DA62A4068` |
| 出现处 | **32 处**（同一份 vendor 样例，字节完全一致） |

其余 896 条全部「实际 == 期望」，包括：Amex 同文件内的 `/01 /02 /03 /04 /05 /06 /09 /0A`、
Mastercard 全部 53 个配置（含 `PPS_perf_MC` 里为本机 CDA 案例生成的 CA 公钥）、
以及 visa / jcb / discover / pure / unionpay 各组。

## 受影响的 32 个配置文件（全部为 Amex 命名配置库）

| 文件 | 该条目位置（1-based） | 该文件 CAPK 总数 |
|---|---|---|
| `CONF_01.json` | `capk[6]` | 9 |
| `CONF_01_IOP.json` | `capk[7]` | 11 |
| `CONF_01_RemoveCRL.json` | `capk[7]` | 8 |
| `CONF_03.json` | `capk[7]` | 8 |
| `CONF_06.json` | `capk[7]` | 8 |
| `CONF_08.json` | `capk[7]` | 8 |
| `CONF_09.json` | `capk[7]` | 8 |
| `CONF_10.json` | `capk[7]` | 8 |
| `CONF_11.json` | `capk[7]` | 8 |
| `CONF_12.json` | `capk[7]` | 8 |
| `CONF_13.json` | `capk[7]` | 8 |
| `CONF_15.json` | `capk[7]` | 8 |
| `CONF_16.json` | `capk[7]` | 8 |
| `CONF_17.json` | `capk[7]` | 8 |
| `CONF_18.json` | `capk[7]` | 8 |
| `CONF_19.json` | `capk[7]` | 8 |
| `CONF_20.json` | `capk[7]` | 8 |
| `CONF_22.json` | `capk[7]` | 8 |
| `CONF_23.json` | `capk[7]` | 8 |
| `CONF_24.json` | `capk[7]` | 8 |
| `CONF_25.json` | `capk[7]` | 8 |
| `CONF_26.json` | `capk[7]` | 8 |
| `CONF_27.json` | `capk[7]` | 8 |
| `CONF_28.json` | `capk[7]` | 8 |
| `CONF_29.json` | `capk[7]` | 8 |
| `CONF_30.json` | `capk[7]` | 8 |
| `CONF_31.json` | `capk[7]` | 8 |
| `CONF_32.json` | `capk[7]` | 8 |
| `CONF_33.json` | `capk[7]` | 8 |
| `CONF_34.json` | `capk[7]` | 8 |
| `CONF_35.json` | `capk[7]` | 8 |
| `CONF_99.json` | `capk[7]` | 8 |

即 Amex 配置库 **32/32 个文件全部命中**（该样例被复制进每个文件），位置差异只是各文件
CAPK 条目数不同（9 / 11 / 8）导致。

## 根因（已定位）：厂商把密钥 `/01` 的 DF03 复制到了 `/07`

`A000000025/01`（128B、指数 `03`）在厂商样例里的 DF03 **就是** `4C3681CD…` 且校验通过；
`A000000025/07`（126B、指数 `03`）**带着同一串值** —— 即 Expresspay 4.2.0 XML 把 `/01` 的
DF03 误抄到了 `/07`（CONF_01 里 `/01` 已被本机 CDA 案例的本地 CA 密钥替换，所以只剩 `/07`
残留这串值）。这也解释了为什么它在 32 个文件里逐字节相同：同一份样例被复制。

## 处置（2026-10-10 用户裁定：以 `SHA1(9F06‖9F22‖DF02‖DF04)` 为准）

| 项 | 结果 |
|---|---|
| 修正范围 | **32 个 amex 配置文件**的 `A000000025/07` DF03：`4C3681CD…` → **`5DC21CFD0B4348E8F374ECD71406580DA62A4068`** |
| 改动方式 | JSON 感知改写（`json.dumps(indent=2, ensure_ascii=False)` 与原文逐字节一致 → 只有该 checksum 串变化）；`/01` 上的同值**未被触碰** |
| 导入器同步 | `tools/import_expresspay_xml.py` 增加 `CAPK_DF03_CORRECTIONS`，重新导入时写计算值并在 `index.json` 的 `capk_df03_notes` 留痕（未列出的不符条目仍按原样保留并报告） |
| 溯源注记 | `cases/amex/pos_configs/index.json` 新增 `capk_df03_notes`（32 条，逐配置说明"corrected … vendor XML value does not verify"） |
| 复核 | 全量重算：**85 个文件 / 897 条 CAPK，0 条不符、0 条缺 DF03**；案例库加载 amex 告警 **0**；`pytest` **400 passed** |
| vendor 原件 | `refernce/Expresspay 4.2.0/TerminalConfig/CONF_*.xml` **保持原样**（只读参考；修正由导入器承担） |

> 注：本报告只记录 RID/索引、模数长度、指数与 DF03（哈希），**不含模数字节**。
