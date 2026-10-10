# 日志 → 测试案例 SOP

> 来源：`3B04-0020`（卡端静默 SELECT → Try Again → 二次拍卡联机）、`3C05-9100-06`（Kernel 2 CDA 卡在离线终端脱机批准）。
> 一句话：**日志是唯一事实源——先抄全、再定断言、最后让工具可重跑并自检。**

## 1 取证（不许猜）

| 要拿到 | 日志里找 |
|---|---|
| 自动 / 手动交易 | 有 `TEST_INFO/CONFIG/ACT` = TEI 自动；`InputAmount` = 读写器手动 |
| 卡端应答 | `<apdu send>` / `<rapdu>`：SELECT PPSE、SELECT ADF、GPO、READ RECORD、GENERATE AC |
| 交易结论 | `OPS =`（DF8129 8B）、`PresentFlag`、`data record:`、`disc data:`、`UIRD`、`kernel id:`、错误注记 |
| 用哪个配置 | `r-terpara#` + `get aid param` → **逐字节**比对配置库反查（不按案例号猜；term 有差异要记录） |
| 实现缺陷证据 | `app:[模块][函数 - 行号]`（直接定位代码，如 `emv_application_selection.c:593`、`emv_initiate_application.c:864`） |

## 2 定断言（只写能复现的）

- **pass** = 交易结论 + OPS 关键位：`outcome` / `online` / `resp_code` / `out.ops.status` / `out.ops.cvm` / 关键 DataRecord tag；金额、币种、`9C` 照日志写进 ACT。
- **warn** = 依赖 POS 白名单或需读写器侧核对：DiscData（host 不断言）、`KernelId`（POS 不上报）、证据类（CDA 的 `9F4B`/`9F4C`）。
- **一个 ACT 可有多条 OUT**：中间 OUT（TRY AGAIN / SELECT NEXT）不结算，只认最终 OUT；逐条用 `outs.N.*` 断言。
- **必有反面用例**：拿一条必然失败的 OUT 证明规则非恒真。

## 3 做档案（能抄就抄）

- AIP / AFL / PDOL / CDOL1 / CDOL2 / 8E / IAC / AUC / 货币 / 日期 / PAN / PSN / track2 逐字节照抄日志。
- 记录按日志**实际读到的 SFI+记录号**放；注意内核会丢弃 AFL 首项 `08 01 01 00`（C-2 §3.3.2，`emv_initiate_application.c:864`），日志的读序列就是证据。
- 档案表达不了的 tag（`9F4D` Log Entry、`9F51` DRDOL…）→ 写进案例 `notes`，不臆造、不静默丢弃。
- 编码要复刻卡端算法（track2→BCD `'='→'D'`、CVM list 第三字节…），并用日志记录字节**逐字节校验**生成器输出。

## 4 需要密钥时（CDA/ODA）

参考卡私钥不可得 → 照 `tools/make_amex_cda_case.py` 先例：自造 CA/发卡行/ICC 链 → CA 公钥以**新索引**写进案例用配置 → 证书按 EMV Book 2 §5/§6.5 构造 → 生成器内置 selfcheck（公钥回推 + CDA 往返）。
配置改哪个文件、用哪个索引、是否在 `index.json` 注明，以及"导入器会重写配置、生成器必须可重跑"的顺序说明——都要落进产物。

## 5 交付与回归（每案例四层测试）

1. 库能加载 + 校验 0 error（warn 必须在 `notes` 里有解释）；
2. 档案/记录与日志**逐字节一致**（把日志字面量写进测试）；
3. 链一致性：用配置里的 CA 公钥回推 `90` → `9F46`，结果 == 案例下发的 ICC 公钥（纯公开运算）；
4. 规则在日志形状 OUT 上 PASS、在反面 OUT 上 FAIL。

最后跑全套 `pytest`，并在交付说明里列出**待真机确认项**（卡端签名、POS 验签/白名单）。

## 6 踩坑清单

- 按案例号猜配置 → 一律用 `r-terpara` / `aid param` 反查。
- 假密文（`ac_crypto=fake`）会把 CDA/ODA 失败伪装成 APPROVED → 必须加证据类断言。
- 日志打印会截断（DataRecord、记录）→ 用其它日志/字段交叉补全。
- 把中间 OUT 当终局 → 误判 FAIL（host 已改为只认最终 OUT）。
- 裁决点（案例 ID、配置、密钥来源、断言级别）先问清再动文件。
