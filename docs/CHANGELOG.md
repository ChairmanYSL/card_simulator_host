# 变更日志（CHANGELOG）

> **用途**：集中审核每次功能/协议修改。详细规范以 `docs/protocol.md`（协议权威）
> 与 `README.md`（前端/工具/用例库）为准，本文件只做索引与摘要。
> 日期采用仓库内 runs/日志可回溯的日期；早期无提交历史，日期为大致区间。
> 编号说明：N/H/O = 与安卓侧协商项；V = 上位机功能轮次。

## 2026-09-10

### 🎉 round #15 全量联调：28 / 28 全部 PASS（收敛达成）
- **类型**：联调结果 + 报告
- **版本**：POS `com.zt.pos` **1.0.13**（`5a9ef2b`）+ 卡端 `com.szzt.cardsimulator`
  **1.0.31**（`83589d5`，构建产物刷新 `ebaa811`）；host `--trace-enabled`，用例库 28 例。
- **结果**：visa 8/8、unionpay 12/12、mastercard 4/4、jcb/amex/discover/pure 各 1/1 =
  **28 PASS / 0 FAIL / 0 INCONCLUSIVE**；`--expect-versions pos=1.0.13,card=1.0.31`
  预检通过；双端 logcat 已抓取。
- **关键修复（本轮生效）**：POS 1.0.13 的 *102 分支按 CID 兜底*（`VISA-CL-001/004`、
  `OPBD-001-01`、`DISC-CL-001` 转 PASS）与 *无 `DF8101` 时在线失败→declined*
  （`QPB-I-010-01` 转 PASS）；卡端 1.0.31 的诊断日志/服务加固；host 的 N12 超时 300s、
  `--settle`、用例排队尾、开跑前预检、"贴卡 ≥1s"提示。
- **回归红线**：`VISA-CL-002`、`UPI-CL-002`（AAC）仍 `05/declined`；无 `-1304`/`-1220`。
- **报告**：`logs/joint_debug/full_run_15_report.md`（逐用例 + 诊断列）、
  `logs/joint_debug/convergence_status.md`（收敛状态与轮次走势）。

### 报告逐用例表新增「诊断」列（retcode / stage / 在场 ms）
- **类型**：工具（报告）
- **改动**：`tools/joint_debug/report.py` §3 逐用例表新增 `诊断(retcode/stage/在场ms)` 列，
  取自 POS 上报的 `kernel_retcode` / `kernel_stage` / `card_present_ms`（协议 §7）；
  缺失显示 `-`。
- **验证**：round #15 报告可见每例的内核路径（如 VISA-CL-001 = `102 / 102 分支 CID 兜底(TC) / 1024`）
  与在场时长（669–1059ms），可直接佐证"贴卡 ≥1s"操作规范的生效。

### 协议协商 N13：VISA GPO 携带 `9F27`(CID) —— **已实现、未部署**（卡端 1.0.32 `ea90149`）
- **类型**：协议（协商项 N13）+ 文档
- **背景**：VISA 的非接离线分支在卡端已给 TC 时仍 `END(102)`，POS 侧按用户裁决
  增加"完成码 102 分支按 CID 兜底"。N13 让内核在 **GPO 阶段**即拿到 CID：卡端
  **1.0.32 (vc34)** 的 VISA GPO = `82/94/9F36` + `9F27`（AAC→`00`、TC→`40`、ARQC→`80`），
  **不带 `9F26`**（1.0.24 曾因 VISA GPO 带 AC 触发 `-1304`），`9F10` 仍只在 READ RECORD。
- **-1304 依据（双方核对）**：1.0.24 三次对照实验（GPO 无 AC→过；GPO 有 AC→`-1304`；
  GPO+记录都有→仍 `-1304`）+ trace `runs/2026-09-09/20260909T025029_ffea704a`
  （该 run `VISA-CL-001` = 80/declined）。host 侧独立复核一致。
- **轨迹（2026-09-10）**：round #15 以**不带 N13**的卡端 **1.0.31** 跑出 **28/28 全绿**
  （证明 POS 的 102 兜底不依赖本项）；其后双方一度批准补入、卡端实现为 **1.0.32 (vc34,
  `ea90149`，单测 133/133)**，最终裁决**不上机部署**——全绿基线在 1.0.31、`DISC-CL-001`
  已由 POS 兜底解决、且"GPO 只带 CID 不带 AC"从未实测，不值得在全绿基线上引入未实测变更。
- **最终裁决（2026-09-10，卡端用户）：不引入 N13**，保持在役 **1.0.31** 的 28/28 全绿基线；
  N13 以"已实现、未部署"留分支（`ea90149` / 产物 `1a71169`）。将来若某终端要求 GPO 阶段带 CID：
  一行启用 + 装机 + **VISA/OPBD 专项验证**（红线：无 `-1304`、`VISA-CL-002` 仍 declined），
  按 `[PAUSE-REQ]` 流程走。
- **文档**：host 侧 `docs/protocol.md` §8.5 记为「**已实现、未部署**」；卡端
  `docs/change-log.md` 1.0.32 条目与 `docs/host-protocol.md`（N13）同步标注同状态。

## 2026-09-10

### 报告新增「迟到/未归属结果（quarantine）」一节
- **类型**：工具（报告）
- **背景**：run #14 的 `UPI-CL-007` 结果在 host 判超时后 6s 才回传，被归档为
  `stale attempt_id`（不参与判定），并连带让下一例 `MC-CL-001` 被 POS ack 门控
  饿死。报告里原先看不出"到底跑没跑"。
- **改动**：`tools/joint_debug/report.py` 在每个输入 run 的目录下读取
  `quarantine.jsonl`（形态 `{at, reason, frame:{case_id, attempt_id, result}}`），
  在 §4 末尾输出「迟到/未归属结果」小节：条数 + 表格（run_id / case_id /
  attempt_id / resp-outcome / 原因）；无记录时明确写"无"。
- **验证**：对 run #14 数据重跑生成器，正确列出 1 条
  （`UPI-CL-007` / `3e72c64e…` / 05/declined / `stale attempt_id`）。

## 2026-09-10

### run_cases.py 开跑前预检（双端在线 + 版本核对）
- **类型**：工具（防呆）
- **背景**：run #14 曾出现"卡端未连/POS 不在线/装着旧版本就开跑"导致整轮空跑或
  INCONCLUSIVE（如 `MC-CL-001`）。用户要求严格按"两端 READY 才开跑"。
- **改动**：`tools/joint_debug/run_cases.py`
  - 新增 `preflight()`：开跑前读 `/api/health`，逐端打印 `connected` 与
    `app_version`；任一端未连接、或（给了 `--expect-versions` 时）版本不符、
    或 host `busy=true` → **拒绝开跑**（退出码 2），不发任何 run。
  - 新增 `--expect-versions pos=1.0.13,card=1.0.31`（可省略）与
    `--no-preflight`（显式跳过）。
- **验证**：当前双端未连、版本为 1.0.12/1.0.30 时，命令以退出码 **2** 拒绝开跑
  并逐端打印不符原因；`--help` 列出两个新参数。

## 2026-09-10

### 报告工具支持 POS 诊断字段（配合协议 §7）
- **类型**：工具（报告）
- **背景**：POS 1.0.12 起在 `transaction_result` 上报
  `app_version` / `kernel_retcode` / `kernel_stage` / `card_present_ms`
  （协议见 `docs/protocol.md` §7）。
- **改动**：`tools/joint_debug/report.py`
  - `load_case_result()` 采集上述字段（`result.*` 或 `pos_meta.app_version`）；
  - §1「设备版本」改为**优先显示真实上报值**（无值才提示未上报）；
  - 逐用例归因明细新增一行 `诊断字段：...`（缺失则不输出）。
- **验证**：对 run #14 数据重跑生成器，正常产出（该轮 POS 未上报，故显示"为空"）；
  语法与 CLI 校验通过。

## 2026-09-09

### run #14 复盘后的 host 侧改动（贴卡提示 / 用例排序 / VISA-CL-003 授权开关 / 归因表）
- **类型**：后端（操作员提示）+ 工具（排序、归因表）+ 用例库
- **背景**：run #14（POS 1.0.10 + 卡端 1.0.30）28 例 **18 PASS / 8 FAIL / 2 INCONCLUSIVE**。
  复盘发现三例"回归"（`MC-CL-003`/`AMEX-CL-001`/`PURE-CL-001`）的主因是
  **卡端 HCE 会话窗口 <350ms 被拆链**（run #13 窗口 759–990ms 时同用例全 PASS），
  而两例 INCONCLUSIVE 分别是 N12 收尾超时与 POS ack 门控级联。
- **改动**：
  1) `runner/executor.py`：`case_started.operator_instructions.notes` 统一追加
     "贴卡后保持手机贴在读卡器上 ≥1 秒"（全局生效，不改逐用例文案）；
  2) `tools/joint_debug/run_cases.py`：长耗时用例（`UPI-CL-006/007`）自动排到
     每个 scheme 队尾（`order_cases()`）；
  3) 用例 `VISA-CL-003` 补 `pos_params DF8101=01`（配合用户裁决：POS 在
     "在线无法完成" 且无该 tag 时回 declined）；出参 `transaction.pos_params`
     由 3 项变 4 项。
  4) `tools/joint_debug/report.py`：`ATTRIBUTION_RULES` 按本轮结论重写
     （VISA/DISC 的 102 分支、卡端会话窗口、N12 超时、ack 门控、
     `QPB-I-010-01` 在线失败语义）。
- **测试**：`backend/tests` 80 passed；`POST /api/library/reload` 后 28 例 0 error；
  `/api/cases/visa/contactless/VISA-CL-003` 出参含 `DF8101`。
- **报告**：`logs/joint_debug/full_run_14_report.md`（含 §6 人工核对）、
  `logs/joint_debug/round14_actions.md`（按端行动清单）。

### 修复 N12 用例超时与跨 scheme 串扰（run #14 复盘）
- **类型**：用例库（超时）+ 工具（驱动）
- **现象**：run #14 中 `UPI-CL-007` 与 `MC-CL-001` 判 INCONCLUSIVE。
- **根因**：
  1) N12 静默让 POS 内核等满自身超时（实测 `UPI-CL-007` 结果在 case_started 后
     **约 185s** 才回传），而用例 `timeout_seconds=180` → 差 5s 判超时；
  2) 该超时用例在 POS 侧仍在收尾，host 已切到下一个 scheme，POS 对新的
     `case_started` 回"上一笔交易尚未结束，仅 ack 本用例并忽略" → 下一个用例
     （`MC-CL-001`）整轮空等到超时；迟到的 `UPI-CL-007` 结果被 host 归档
     （quarantined，不参与判定）。
- **修复**：
  - `UPI-CL-006` / `UPI-CL-007` 的 `timeout_seconds` 180 → **300**（覆盖内核自身
    N12 超时），已 `POST /api/library/reload` 生效；
  - `tools/joint_debug/run_cases.py` 新增 `--settle`（默认 **15s**）：每个 scheme
    结束后静默等待，等 POS 收尾上一笔再开下一个 scheme（原为固定 2s）。
- **验证**：`--help` 含 `--settle`；`/api/cases/unionpay/contactless/UPI-CL-007`
  返回 `timeout_seconds=300`；库重载 28 例 0 error。

### 新增 `POST /api/library/reload`（改用例免重启）+ VISA-CL-001 加 floor limit
- **类型**：后端（工具/接口）+ 用例库 + 协议约定
- **背景**：联调中手工改用例后必须重启后端才能生效，重启会断开双端连接
  （卡端上次重连耗时约 8 分钟）。新增 `POST /api/library/reload`：重读用例库，
  返回各 scheme/mode 用例数与 error 列表，失败返回 400。
- **用例**：`VISA-CL-001` 增加 `pos_params` `9F1B=FFFFFFFF`（终端 floor limit
  覆盖）——实测内核因 floor limit 判 `EMV_REQ_GO_ONLINE` 且
  `EMV_REQ_CONFIRM_FORCE_ONLINE` 不触发、`preprocess` 后 `setForceOnline(0)`
  返回 -124 无效；抬高限额让 100.00 低于限额，使确定性离线用例真正离线。
- **协议**：`docs/protocol.md` §8.1 约定 tag 表新增 `9F1B`
  （`terminal_floor_limit`）。
- **测试**：`backend/tests` 80 passed。

### 用例库按用户裁决收敛为 28 例（负例删除 / N12 改判 / 在线授权开关）
- **类型**：用例库（用户裁决）+ 协议约定 + 文档
- **删除设计负例**：`VISA-CL-005`、`UPI-CL-005`、`MC-CL-005`（标题即
  "DEMO expected-failure"——卡端 declined 而用例期望 approve，故意不匹配），
  同时从三个卡组的 `.gen_cases_manifest.json` 移除条目；用例库 31 → **28**。
- **`UPI-CL-007`（N12）改判**：真实 POS 内核首次 GPO 静默后不再重发
  GPO（`状态27 → -112`），故判定改为「终端超时/拒绝即 PASS」——
  `resp_code any_of ['80','91','05']` + `outcome eq declined`；卡端
  `noResponseCommands` 语义不变（见 `docs/protocol.md` §8.4）。
- **新增 host 约定 pos_params tag**：`DF8101=01`
  （`test_host_online_approval`）加入 `UPI-CL-003`/`MC-CL-003`/`QPB-B-002-01`；
  仅当 host 显式下发时才允许 POS 返回测试主机在线授权（占位 ARPC），
  未下发保持真实行为（在线失败 → 91/declined）。同时给 `QPB-B-002-01`
  补 `9F66=36804000`（终端 TTQ 覆盖）。
- **影响**：需重启 host 后端重载用例库（本次已重启，`/api/tree` = 28 例）。

### run_cases.py 增加双端 logcat 自动抓取（联调日志建设）
- **类型**：工具（日志建设）
- **背景**：run #8 复盘时 POS 端 logcat 环形缓冲已滚动，AMEX/DISC/MC 的内核
  retcode 只能靠旁证推断。为让每个 attempt 都留下内核证据，把抓取做进驱动脚本。
- **实现**：`tools/joint_debug/run_cases.py` 新增 `--adb`（默认
  `E:\Android\Sdk\platform-tools\adb.exe`）、`--serials`（`role=serial`，默认
  `pos=256068b7,card=463169e6`）、`--no-logcat`；运行期间对在线设备并行
  `adb logcat -v threadtime`，落盘 `logs/joint_debug/logcat/<stamp>-{pos,card}.log`，
  结束在 `finally` 中停止，设备离线自动跳过。
- **验证**：`--help` 正常；独立 3 秒抓取写出 195 KB 可读日志。

### 双端自动化联调（第一轮）与用例预期对齐
- **类型**：用例库（预期调整，用户确认）+ 工具 + 联调记录
- **用例预期**：`QPB-D-001-02/03/04`、`OPBD-001-02/03/04`、`UPI-CL-006` 的
  `resp_code` `any_of` 增加 `80`——POS 端对终端/超时/内核错误统一回 `80/declined`，
  与用例"拒绝"语义一致（用户确认按 POS 映射放宽）。
- **新增工具**：`tools/joint_debug/run_cases.py`（全量/单用例 REST 驱动、409 自动
  等待重试、逐条断言明细）、`tools/joint_debug/patch_pdol.py`（给缺省 pdol 的
  用例显式补 24B 标准模板）。
- **联调结果**：真实双端（卡端 `com.szzt.cardsimulator`、POS 端 `com.zt.pos`）
  首轮 31 用例；UnionPay 8 个 PASS（`QPB-D-001-01..04`、`QPB-I-010-01`、
  `UPI-CL-001/002/004`），其余失败逐条归因。
- **归因摘要**：卡端记录 `57/5A` 为空（Visa `-1304`）、GPO fast path 网络范围
  （Mastercard 完成码 117 / PURE 111）、POS 完成码 117/111 未映射、JCB/AMEX
  发现阶段失败、联机/PIN 类环境阻塞、两端运行中重装/重启导致 409/断连。
- **报告**：`logs/joint_debug/full_run_1_classification.md`、
  `logs/joint_debug/status_run3.md`、`logs/joint_debug/classification.md`。

## 2026-09-08（最近）

### 设备断线中断 ACK 等待导致运行任务静默死亡 + 409 诊断日志（稳定性修复）
- **类型**：后端（修复 + 日志建设）
- **现象**：模拟卡端/POS 端均连接后运行单个用例，测试未完成；终端出现两条
  `POST /api/run/abort 409 Conflict`；落盘 run.json 停在 RUNNING、checkpoint 停在
  upcoming（logs/host.log 14:33 现场可复现）
- **根因**：`ClientConnection.close()` 用 `fut.cancel()` 取消在途 ACK 等待；
  等待方（run loop 里 `send_and_wait_ack`）只捕获 `Exception`，而 future 被取消
  抛 `asyncio.CancelledError`（BaseException）→ 整个 run task 静默死亡（无日志、
  无落盘终态），`busy` 变 False → 之后 abort/stop/skip 全部 409 "no run in progress"
- **修复**：close() 改为对在途 ACK future `set_exception(ConnectionClosed)`（调用方
  现有的 `except Exception` 可处理，保持“POS 断线仅告警”语义）
- **兜底**：run task 入口 `_run_loop` 加 try/except 包装——异常/取消一律
  `logger.exception` 大声记录并把 run 落盘为 ABORTED（当前用例 ERROR），不再允许
  “RUNNING 僵尸”；stop/abort/skip 被拒时记录并返回含 run_id/status/task 状态的诊断
  信息；`/api/*` 请求新增 HTTP 中间件日志（uvicorn.access 不写 host.log，409 之前
  无法在文件日志里关联）
- **文档落点**：本条目（代码注释同步说明）
- **验证**：新增回归测试（close 时在途 ACK 抛 ConnectionClosed 而非
  CancelledError；run loop 崩溃自动落盘 ABORTED）；80 tests 全绿

### 新增演示卡组 JCB / AMEX(ExpressPay) / Discover / PURE
- **类型**：用例库 + 工具（tools/gen_cases.py）
- **内容**：`SCHEMES` 增加 jcb/amex/discover/pure；每卡组生成 scheme.json（card_template + pos_params）+ 1 冒烟用例 `<PREFIX>-CL-001`（offline approved）；visa/mastercard/unionpay 原有完整演示不变；生成器保持清单式非破坏
  - JCB：AID `A0000000651010`、PAN 前缀 352800（占位）；AMEX：AID `A00000002501`（占位）；Discover：AID `A0000003241010`（占位）——占位数据可在网页 AddCase 修改为真实值
  - **PURE**（Gemalto，规范《PURE Contactless Reader v2.1.8》，PPSE=`2PAY.SYS.DDF01`）：AID 列表 `D9999999991010 / D9999999992020 / D666666666 / A0000002282010 / A0000002802010 / A0000007231010`；示例 PAN `6280706258024090`（Luhn 通过，tag57 track2 与之同源）；`kernelId:"2D"`（PPSE 目录项 9F2A，透传字段供安卓侧）
- **文档落点**：README（卡组入口列表、演示库结构注记：新增卡组与 PURE 数据）
- **验证**：7 卡组加载 0 error / 0 warning；后端 78 tests 全绿

## 2026-09-07（最近）

### POS 机应用开发手册（文档新增）
- **类型**：文档（POS 端交付物）
- **内容**：面向安卓 7.1.2（API 25）的 POS 端完整开发手册——连接/握手/心跳/ack 退避重发、pos_config 与 case_started 处理、transaction_result 报文、pos_params 映射、卡片静默（N12）时的 POS 超时策略、状态机、Java 8 + org.json 参考骨架、联调检查清单与常见问题
- **文档落点**：`docs/POS机应用开发手册.md`（依据 docs/protocol.md；README 已加入口）

### N12 档案级「指令不响应（静默）」
- **类型**：协议字段 + 前端 + 工具 + 文档
- **内容**：`CardProfile.noResponseCommands`（字符串=每次静默；`{"command":"GPO","times":N}`=仅前 N 次）；命令名注册表 PPSE/SELECT/GPO/READ_RECORD/GET_DATA/GENERATE_AC/VERIFY；未知/重复 warning；app 侧映射表、会话边界、`return null` 实现落点、trace `no_response:true` 定稿
- **文档落点**：`docs/protocol.md` §6.4 / §8.4；`docs/N12_协商说明_指令不响应.md`（定稿）；`docs/N12_上位机确认回复.md`（回执：GET_DATA/VERIFY 本期不做正常应答、真机风险知悉）；README §用例库/工具节
- **前端**：「卡片档案」新增「静默指令」编辑；详情面板展示
- **Demo**：`UPI-CL-006`（GPO 每次静默）、`UPI-CL-007`（GPO 仅首次静默）
- **验证**：78 tests 全绿；tsc+vite 通过；独立端口端到端（UPI-CL-007 PASS、UPI-CL-006 FAIL 符合预期）；mock android 日志确认下发
- **备注**：安卓侧实现 + 真机联调待办（见 N12 回执）

### gen_cases 改为清单式非破坏重生成（事故修复）
- **类型**：工具（安全修复）
- **内容**：不再 `rmtree` 整卡组目录；每卡组 `.gen_cases_manifest.json` 记录生成文件，重跑仅覆盖/清理清单内文件，网页自建用例不再被误删
- **文档落点**：README §用例库结构（新增 manifest 说明）
- **验证**：临时目录模拟“用户自建文件”重跑两次存活；仓库三卡组 manifest 已生成；78 tests 全绿

### acBehavior「无」选项
- **类型**：前端（纯 UI 文案，语义不变）
- **内容**：卡片档案 acBehavior 首项改「无（不设置，按终端 P1 应答）」+ 说明 hint
- **文档落点**：README「02 添加用例」；协议语义本身已存在于 protocol.md §9（空值=跟随终端 P1）

### UnionPay QPB.D.001.00 使用 PPSE 目录选择方式（分组用例）
- **类型**：案例库（UnionPay 分组 Demo）
- **内容**：新增总案例 `QPB-D-001`（group.json）与 4 个可执行子案例
  `QPB-D-001-01…04`（仅属 UnionPay；Visa 下历史同名 OPBD-001 保留不动）：
  -01 PPSE 正常 FCI（auto）→ 交易批准；-02 FCI 缺 AID（custom，61 内无 4F，
  label=UNIONPAY）→ declined；-03 FCI 无 61 模板（empty）→ declined；
  -04 错误响应码（disabled/6A82）→ declined；断言 resp_code/outcome 与
  OPBD-001 同构
- **文档落点**：`cases/unionpay/contactless/cases/QPB-D-001/`（group.json +
  QPB-D-001-01…04.json）；生成器 `tools/gen_cases.py` 新增
  `_gen_qpb_d_001_group`；UnionPay manifest 已收录新文件
- **验证**：loader 校验 0 error / 0 warning（含 ppseData TLV 长度自洽）；
  78 tests 全绿

### UnionPay QPB.B.002.00 状态检查请求（分组用例）
- **类型**：案例库（UnionPay 分组 Demo）
- **内容**：新增总案例 `QPB-B-002`（group.json）与可执行子案例 `QPB-B-002-01`：
  授权金额 0.01（一个货币单位、低于各限额）；档案 `profile.pdol` 配置
  9F38 PDOL 内容（请求 9F37/9F02/5F2A/9F66），FCI 由安卓侧包装；期望 POS 在
  GPO 命令中回传 TTQ 9F66=36804000（字节2位8='1' 请求联机密文）；断言
  resp_code=00 / outcome=approved / online=online / tags.gpo.9F66=36804000
  （需测试收单主机）
- **文档落点**：`cases/unionpay/contactless/cases/QPB-B-002/`（group.json +
  QPB-B-002-01.json）；生成器 `tools/gen_cases.py` 新增 `_gen_qpb_b_002_group`；
  UnionPay manifest 已收录新文件
- **验证**：loader 校验 0 error / 0 warning；78 tests 全绿
- **备注**：9F66 置位为终端行为，host 仅能断言 POS 回传值；若内核其它字节有
  差异，可将断言改为 regex `^..80`

### UnionPay QPB.I.010.00 交易不能联机完成（分组用例）
- **类型**：案例库（UnionPay 分组 Demo）
- **内容**：新增总案例 `QPB-I-010`（group.json，含测试目的/条件/流程/通过标准）
  与可执行子案例 `QPB-I-010-01`：档案内嵌 profile，`acBehavior=online_request`
  （卡片应答 ARQC 促使 POS 发起联机授权），GPO 响应含 `issuerApplicationData`
  （tag 9F10，26 字节样例值由用例提供方给定）；「联机主机无响应」由 POS 侧环境
  构造（host 无法模拟收单后台）；断言映射（用户约定）outcome=declined 且
  resp_code ∈ {05, 91, 16, 80}
- **文档落点**：`cases/unionpay/contactless/cases/QPB-I-010/`（group.json +
  QPB-I-010-01.json）；生成器 `tools/gen_cases.py` 新增 `_gen_qpb_i_010_group`；
  UnionPay manifest 已收录新文件
- **验证**：loader 校验 0 error / 0 warning；78 tests 全绿

## 前期（2026-09-01 ~ 09-06 区间，无精确日期记录）

### AddCase 支持「总案例 / 可执行案例」两种形态
- **类型**：前端 + 后端 API
- **内容**：`POST /api/groups(validate)` 建总案例（仅说明+test plan，无 profile/交易参数/预期结果）；`POST /api/cases` 可选 `group_id` 写入组目录（文件不带 group_id，归属由目录决定）；总案例/案例号互斥；AddCase 顶部形态切换 + 所属总案例下拉 + 子案例编号自动建议 `组号-NN`
- **文档落点**：protocol.md §8 布局；README「02 添加用例」
- **验证**：测试覆盖（groups validate/创建/冲突/子案例入组）

### v2：profile 与案例一对一绑定 + POS 交易参数
- **类型**：协议/数据模型 + 前端 + Demo
- **内容**：删除共享 profiles 库；`profile`/可选 `keys` 内嵌案例（profile.id 自动=case_id）；`SchemeDef.card_template`+`pos_params`（卡组模板）；`transaction.pos_params`（tag 列表，按卡组模板自动带出、可改 tag）；`config_push{pos_config}` 下发 POS（安卓不收）；AddCase「卡片档案」页签（克隆模板编辑）
- **文档落点**：protocol.md §8（布局/字段/pos_params/keys/分工边界）；README §用例库/02
- **验证**：端到端 pos_config 下发 + 判定

### 总案例分组 + PPSE 档案适配
- **类型**：案例库 + 前端 + Demo
- **内容**：GroupDef（总案例说明+test plan）/ 子案例；`ppseMode/ppseSw/ppseData`（auto/disabled/empty/custom）；demo `OPBD-001` + PPSE 档案变体
- **文档落点**：protocol.md §8.3 PPSE 语义；README
- **验证**：树/详情/运行冒烟（OPBD-001-01 FAIL、02–04 PASS 语义保持）

### v1.1 协议基线（更早）
- 握手/心跳/ack/重试/身份、单端口双客户端、`card_config{profile,keys}`、trace、POS 判定权威等 —— 均已吸收进 `docs/protocol.md`（H1–H10/O1–O7/N1–N10）。
