# 变更日志（CHANGELOG）

> **用途**：集中审核每次功能/协议修改。详细规范以 `docs/protocol.md`（协议权威）
> 与 `README.md`（前端/工具/用例库）为准，本文件只做索引与摘要。
> 日期采用仓库内 runs/日志可回溯的日期；早期无提交历史，日期为大致区间。
> 编号说明：N/H/O = 与安卓侧协商项；V = 上位机功能轮次。

## 2026-09-18（用例交易数据迁移到 ACT；POS 通知刷新；联调启动计划）

### 数据：`cases/**` 交易数据 `pos_params` → v2 `act`（解除联调阻断）
- **类型**：用例库数据迁移（+ 新增迁移工具）
- **背景**：v2 的 host 组帧只读 `case.transaction.act`（`executor.py:798-800`，无回退），
  而 v1.2 的 host 是把 `pos_params` **原样**（人读值 `"100.00"`/`"156"`）JSON 下发、由 POS 解读；
  仓库里**从来没有** `pos_params`→hex 的编码器。迁移前 **31/31** 个案例 `act` 为空 →
  **wire 上 `ACT.signalData` 是空数组**，POS 收不到金额/币种/交易类型（全仓 192 个单测覆盖不到）。
- **改动**：
  - 新增 `tools/pos_params_to_act.py`：`--dry-run`（默认）/ `--apply`，
    `--extras {drop,keep}`、`--exponent {yes,no}` 把两处口径显式化；
    每个编码规则在模块 docstring 里注明依据（`9F02` 6B BCD 且小数位由货币指数决定、
    `5F2A` n3→`0156`、`9F1A` n3、`9F33` 原样 hex、`5F36` 可选下发）；
    未知币种**告警**而不是静默按 2 处理。
  - 以**设计稿为准**执行 `--extras drop --exponent yes`：31 个案例迁移完成，
    `act` 全部非空、`pos_params` 全部移除。
- **验证**：`--dry-run` 4 种口径组合均 `31 cases converted, 0 failed`；
  真实用例库 `CaseLibrary.reload()` 后 `VISA-CL-001.act = [9F02=000000010000, 5F2A=0156, 9C=00, 5F36=02]`、
  `pos_params=[]`；全量单测 **199 passed / 0 failed**（迁移未破坏任何用例）。
- **⚠️ 需三方确认的行为变化**：按设计 §5「`9F1A` 不在 ACT（属 CONFIG.TERM）」，5 个 Mastercard 案例的 `9F1A`
  与 13 个 UnionPay 案例的 `9F33` **不再出现在 POS wire 上**（此前经 `pos_params` 下发）；
  且这两个 tag **当前不在任何 TERM 里**（已核实）→ 需 POS 以自身终端参数承担。
  若要求保内容不变，用 `python tools/pos_params_to_act.py --apply --extras keep` 重跑即可（一条命令回滚口径）。
  另 `5F36=02` 是**新增**的 wire 内容（此前不发）。

## 2026-09-18（联调准备：POS 通知刷新 + POS 手册勘误 + 启动计划）

### 文档：`change-notice-v2-pos.md` 刷新为最终定稿口径
- **改动**：新增「当前生效」状态与带日期的修订记录（r1 保留并标注作废）；
  CONFIG 由数组形态改为**同名 id 逐条展开**、`CONF_NAME` 更正为 `kernel_params_ref`（缺省=case_id）；
  新增 D1 空组语义（`DRL`/`EXCEPTION_FILE` 每笔恒发、空 → `value:""` = 清空、`null`/缺失 = 不管理）；
  DataRecord 改为 **host 白名单 + 取到才报（32 = 31 + `9F66`）**（其余 31 条明确「不复述、不臆造」）；
  新增 §4.1 `resp_code` 派生与 `out_no_outcome`；新增入站信封 `v` 校验；
  明确 v1.2 POS 帧全删、**`session_abort` 不再存在**（中止无任何通知帧）。
- **验证**：`value:[` 与 `CreateEMVDataRecord` 命中数归 0；残留 v1.x 字样均为修订记录/否定语境。

### 文档：`docs/POS机应用开发手册.md` 勘误（最小改动）
- 横幅重写为 v2 状态 + 权威指引 + v1.x→v2 替换表，并明确**正文 v1.x 部分已作废**；
  就地更正 7 处零判断的 wire 常量（信封 `v`、`protocol_version`、`protocol_mismatch` 阈值）。
- **未做（超出最小改动授权，已列清单上报）**：§6/§7/§8/§10/§11 的 v1.1 结构（`transaction_result`、
  `config_push{pos_config}`、`pos_params`、Java 代码样例）需整节重写。**v2 POS 端目前没有可照抄的参考实现文档**
  （只有 `tools/mock_clients/mock_pos.py`）。

### 文档：新增 `docs/联调-v2-启动计划.md`
- 三方（host/卡端/POS）联调启动计划：v2 变更要点速览、preflight（两端 connected + `app_version` 与
  `--expect-versions` 一致、dist 为本次构建、无残留 host/锁）、轮次门控与 `[PAUSE-REQ]/[PAUSE-ACK]`、
  统一测试修改流程（双侧证据交叉 + 卡端 logcat 时间戳校准 + 不用事件顺序推断因果）、
  已知限制（ACT 行为变化、`card.*` P2 未接线、DataRecord 31 条清单待 POS 提供）、
  证据与 token 保密口径、三方确认表。

## 2026-09-18（前端 v2 展示 + README v2 同步 + 修复前端构建阻断）

### 修复（**既有缺陷，非本轮引入**）：前端在 HEAD 上无法构建
- **类型**：前端缺陷修复（阻断级）
- **背景**：`npm run build`（`tsc -b && vite build`）在本轮之前即失败，且 `frontend/dist` 长期停留在
  2026-09-15 的陈旧构建——即此前所有前端改动都未能真正发布。
- **改动**（`frontend/src/pages/AddCasePage.tsx`，2 处独立缺陷）：
  - JSX **文本**中的裸 `<=`（`模数长度 <= 256 字节`）→ `&lt;=`（`TS1003`，JSX 文本里的 `<` 必须转义）；
  - 引用未定义标识符 `sch`（`emptyDraft/emptyGroupDraft/applyTemplate`）→ `brand`
    （`366f95c` 的 `scheme`→`brand` 改名遗漏，`TS2304`）。
- **验证**：`npm run build` 成功（41 modules，`dist/assets/index-DIcIZby1.js` 207.54 kB）；
  `dist` 时间戳更新为 2026-09-18 16:52。
- **未改的同源命名残留**：`AddCasePage.tsx:583/585/587`（形参 `sch`）、`:635`（`const sch = brand`）。

### 前端：v2 展示（设计 §11 Q8 的"先最小可用"：`out.ops.status` + APDU）
- **改动**：
  - `frontend/src/types.ts`：新增 `OutOps` / `CaseRawResult` / `TraceMessage` / `TraceEntry`；
    `RunDetail` 补 `trace`（后端早已返回，前端类型缺失）。
  - `frontend/src/pages/HistoryPage.tsx`：运行详情新增 `out.ops`（`status [status_bits]`）与 `resp_code`
    两列；新增 APDU trace 折叠区（按 `COMMAND`/`RESPONSE` + `SW` + `elapsed_ms` 渲染）；页脚标签
    `v1.2：` → `v2：`。
- **落点**：`out.ops` 与 trace 仅存在于已落盘记录（`results/<case_id>.json` + `runs/<run_id>/trace/`），
  经 `/api/history/{run_id}` 暴露；实时 `SessionInfo.results` 不含 `raw_result`，故 **RunPage 实时 OPS 显示
  与 AddCasePage 的 `pos_params`→`act` 表单改造属"一步到位"，未做（待裁决）**。
- **验证**：`npm run build` 通过；起真实 host 后 `GET /api/history/{run_id}` 实测
  `raw_result.out.ops.status="RFU(0000)"`/`status_bits="0000"`/`resp_code=""`、`trace` 2 条，
  与 UI 渲染字段逐个对应；`GET /` → 200（新 dist 已被服务）。
  **未做视觉/交互验证**（仓库无前端测试框架）。

### 文档：README 同步为 v2
- **改动**：横幅、拓扑图（`result 回传`→`out 回传`）、编排模型（→ `test_info → config → act → out`，
  判定落盘后 ack）、用例 JSON 示例（`pos_params`→`act`；`tags.gen_ac.9F26`→`out.datarecord.9F26`）、
  运行语义（失败原因码改 v2；**删除"`session_abort` 双方必 ack"这一 v2 已不成立的承诺**）、mock 说明。
- **验证**：逐条审计残余 `v1.2` 字样，均为"已删除/legacy"等历史或否定语境，无一处作为现行行为。
- **未做**：`docs/POS机应用开发手册.md` 未检查，可能仍是 v1.2。

## 2026-09-18（协议 v2 P1c-2：D1/D2/D3 + 入站 v 校验落地；设计文档与 POS 答复定稿）

### 协议：空组清空语义 / `resp_code` 口径 / 启动日志降噪 / 入站 `v` 校验
- **类型**：协议行为 + 安全校验 + 日志治理（host 侧）；**仅工作区改动，尚未提交**
- **依据**：`docs/test-report-v2-p1/REPORT.md`（Round 1，F1–F3 与 §5.3）+ `docs/reply-to-pos-v2-confirmation.md`；
  负责人 2026-09-18 对 D1/D2/D3 及「入站 v 校验」的裁决。
- **改动**：
  - **D1 空组语义**（`runner/executor.py::_config_frame`）：`DRL`/`EXCEPTION_FILE` **每笔恒发**；
    空表发一条 `{id:"DRL", value:""}` = 「清空该表且不加」；id 缺失 = 本笔不管理该表。
  - **D2(a) `resp_code` 口径**（`runner/executor.py`）：OPS 全 0 + 空 `DataRecord`（内核未跑）→
    用例 FAIL / `out_no_outcome`，`resp_code` 为空串，不伪造 `05/91/80`。
  - **D3 日志降噪**（`cases/loader.py`）：named POS config 装载日志 `logger.info` → `logger.debug`
    （原先 87 行 / 约 29 KB 启动噪音）。
  - **入站 `v` 校验**（`protocol/connection.py`）：握手期 `v != 2` → `HandshakeError("bad_frame")`；
    运行期 `v != 2` → warning + 断开。信封 `v` 由「只发不收」改为收发都校验。
  - **文档**：`protocol-v2-design.md` §4（CONFIG 逐条 + 空组语义）、§6.1（`resp_code` 口径）、
    §6.2（DataRecord 32-tag 白名单 + 取到才报）、§6.3（DiscData 本轮不报）、§8（失败原因码新增
    `out_no_outcome`/`*_error`/`pos_not_connected`）；`reply-to-pos-v2-confirmation.md` 由「待拍板」**定稿**。
- **验证**：Round 2 实测 ① 聚焦子集 **155 passed / 0 failed（20.56s）**；
  ② 全量当时 **170 passed / 14 failed**，14 个全为 F4（v1.2 残留）。
  **F4 迁移完成后同日复测：`python -m pytest -q` → 184 passed, 0 failed（27.14s）**，
  证据 `docs/test-report-v2-p1-round2/raw/full-pytest-after-f4.txt`。
  **注意**：D1/D2/入站 v 校验三条新行为**均 0 单测覆盖**（见 Round-2 报告 §5.1），
  「未引入新失败」≠「已验证」；D1 的 `CONF_01_RemoveCRL` 场景当前无 case 引用。
- **口径**：以上为 host 仓库内改动 + 本机 pytest 实测，**未与 POS/卡端联调**。

### 测试：`test_runner_timeout.py` / `test_v12_fixes.py` 迁 v2（F4）
- **改动**：两文件的 `StubProtocol` 补 v2 `send_to_and_ack_signal`（镜像 `protocol/server.py:148`）与
  `send_and_wait_signal`；断言的 v1.2 帧/原因码改 v2（`case_started`→`act`、`transaction_result`→`out`、
  `result_timeout`→`out_timeout`、`pos_*`→`config_*`/`act_*`）；`_on_transaction_result`→`_on_out`。
- **`session_abort` 语义变化**：v2 §10 已删除该帧，原「通知两端会话已中止」的正断言改为
  「不发任何 v1.2 legacy 帧」的负断言；即 **v2 下无任何帧通告整轮中止**，卡端仅靠 `stop_emulation` 收尾。
- **验证**：29/29 通过；全量 184 passed。改动审查：19 处 assert 替换全部有 v2 对应，新增 21 处含更强的
  顺序/状态断言（`pos.seen == [test_info, config, act]`、`record["status"]`），非弱化。

### 测试：补三条新行为的回归覆盖（`tests/test_v2_rulings.py`，8 例）
- **背景**：D1 空表语义、D2 `out_no_outcome`、入站 `v` 校验三条新行为此前 **0 单测覆盖**
  （见 `docs/test-report-v2-p1-round2/REPORT.md` §5.1），"未引入新失败"不等于"已验证"。
- **改动**：新增 `backend/tests/test_v2_rulings.py`（本轮唯一新增文件，未改任何既有受跟踪文件）：
  D1 三例（空表清空项 / 非空逐条 / 两表独立）、D2 三例（主路径 + "有 DataRecord 则不算 no_outcome"边界 +
  正常批准仍 PASS 的对照）、入站 `v` 两例（握手 `v != 2` → `bad_frame`；握手后 `v != 2` → 告警并断开）。
- **验证**：8 passed；全量 **192 passed / 0 failed**（起点 14 failed / 170 passed）。
  非空转验证：临时破坏三处行为 → 对应用例各失败 → 哈希校验还原（`executor.py`/`connection.py` SHA256
  与破坏前逐字节一致，`git diff --stat` 与基线相同）。

## 2026-09-18（协议 v2 P1c-1r：Round-1 测试报告 F1/F2/F3 修复）

### 修复：`gen_cases` 撞名（阻断级）/ quarantine 断言 / 真实子进程 stdout 管道
- **类型**：工具缺陷修复 + 测试修复 + 新增断言（提交 `0fb2c40`）
- **依据**：`docs/test-report-v2-p1/REPORT.md`（Round 1）。
- **改动**：
  - **F1**：`tools/gen_cases.py` 因 `scheme -> brand` 重命名产生 **9 个函数**重复形参（`ast.parse` 不检重复形参，
    故此前核验漏检），导致全文件 `SyntaxError`；品牌定义 dict 形参统一改名 **`brand_conf`**（`: dict` 注解与
    9 处调用点语义不变）。复核：`compileall` 全树 exit=0 + AST 结构化扫描 0 处重复形参。
  - **F2**：`tests/test_runner_result.py` 的 quarantine 落盘行数断言 `2 -> 1`
    （无 session 不落盘是 v1.2 起既有设计，`_quarantine` 与 v1.2 逐字相同）。
  - **F3**：`tests/test_launch.py` / `tests/test_shutdown.py` 真实子进程 stdout 由 `PIPE`（未排空）改为
    **文件重定向**。根因：启动期日志约 **29 KB** > Windows 匿名管道 4 KB 缓冲，子进程阻塞在 bind 之前；
    对照实验：stdout→文件/排空管道 1.8s 起服务，未排空管道 145s 从未监听（服务端本身无缺陷）。
  - **附加**：`tests/test_frames.py` 新增**出站信封 `v=2`** 断言（补 Round-1 报告的覆盖度缺口）；
    `executor._pos_signal` 拆分 `*_ack_timeout`（`ConnectionClosed`）与 `*_error`（本地/编程/传输异常）。
- **验证**：Round 2 ① 155 passed / 0 failed；② 仅 F4 的 14 个失败。耗时由 592s/597s 降至
  **20.56s / 21.55s**（F3 消除了 3×约 24s 的无效重试）。

## 2026-09-18（协议 v2 P1c-1：用例断言迁移 + CTEL2 OUT 测试）

### 测试/工具：断言路径迁 `out.datarecord.*`，runner 测试改 v2（提交 `ffa28f2`）
- **改动**：11 个用例文件把 `tags.gen_ac.*` / `tags.gpo.*` / `tags.read_record.*` 共 17 处断言迁移为
  `out.datarecord.*`；`test_launch`/`test_protocol_server` 版本断言升 2.0；
  `test_runner_result.py` 整体重写为 CTEL2 OUT（判定+落盘后 ack、msg_id 去重、quarantine）；
  `executor._on_out` 补 msg_id 去重守卫；新增 `docs/test-plan-v2-p1.md`。
- **验证**：Round 1 聚焦子集 5 failed / 149 passed（5 个失败即后续 P1c-1r 的 F1/F2/F3），全量 19 failed / 164 passed。

## 2026-09-18（协议 v2 P1：host CTEL2 TEI 核心）

### 协议：host-POS 报文切换为 CTEL2 TEI（提交 `13195f6`）
- **改动**：`test_info`/`config`/`act` 下发 + POS `out`（OPS + DataRecord）判定与落盘；
  CONFIG 内嵌完整配置（R2=b）；v1.2 POS 报文（`config_push*`/`case_started`/`transaction_result`/`session_abort`）
  按 R7 **硬切换删除**，不保留兼容开关；`_on_transaction_result` → `_on_out`。
- **验证**：见 P1c-1 轮次（同一批用例）。

## 2026-09-15（v1.2.x 修正：Amex POS 配置改为模块化自由配对）

### 重构：CONF / Profile 拆成独立模块，case 自由选择
- **类型**：架构调整（host 侧）+ 工具/前端/文档
- **背景**：上一版把 `CONF_NN` 与同号 `Profile_NN` 绑定进 term；用户要求 case 与配置分离、
  自由配对，且 `Profile_25` 的 `9F6E` 录入多了一个尾字符。
- **改动**：
  - 命名库分两类模块：`CONF_*`（AID/CAPK/DRL/ExceptionFile + TerminalParameter term）
    与 `PROFILE_*`（Profile tags term）；工具 `--profile-dir` 下的每个 Profile 都单独成模块。
  - case 新增 `terminal_profile_ref`（与 `kernel_params_ref` 并列，均可选）；loader/API
    合成 `term = CONF.term + PROFILE.term`，AID/CAPK/DRL/ExceptionFile 只取 CONF。
  - `kernel_params` / `kernel_params_ref` / `terminal_profile_ref` 三者互斥（内联不可与 ref 同用）。
  - 前端 AddCasePage 增 `terminal_profile_ref` 输入。
  - `Profile_25.xml` 的 `9F6E` 由 `188000003` 修正为 `18800000`（本地参考数据，`refernce/` 不入 git）。
  - 文档同步 `protocol.md`/变更通知/`README.md`。
- **验证**：按要求未跑 pytest；工具离线解析 32/32 CONF 通过、54/54 Profile 通过（合成后校验 0 error）。

## 2026-09-15（amex POS 配置入库 + 外部覆盖）

### 调整：命名 POS 配置库默认放仓库内并 git 追踪
- **背景**：用户裁决 cos 测试 CAPK 可入库；amex 的 86 个模块（32 CONF + 54 PROFILE + index）
  从 `_secure/.../kernel_params/amex/` 迁入 `cases/amex/pos_configs/`（7 卡组的按 brand 结构）。
- **改动**：
  - loader 命名库优先级：外部 `<kernel-params-dir>/<brand>/` > 仓库内
    `cases/<brand>/pos_configs/`（二者取第一个存在的，整体生效）。
  - brand 级默认集优先级：外部 `<dir>/<brand>.json` > 仓库内
    `cases/<brand>/pos_configs/default.json` > 旧 `kernel_params.json`（opt-in）。
  - `tools/import_expresspay_xml.py` 增 `--flat`（直接写入 `--out-dir`），并取消"拒绝写入仓库"
    限制（现在是仓库内生成为主）。
  - `.gitignore` 仅保留旧单文件 `cases/*/kernel_params.json` 忽略；`pos_configs/` 正常追踪。
  - README/protocol.md 同步；`refernce/` 仍本地保留。
- **验证**：无外部目录时 loader 从仓库内加载 amex **86 模块 / 0 error**；带外部目录时外部优先，
  visa 默认集正常加载。按要求未跑 pytest/tsc。

## 2026-09-15（协议 v2 P0：Mastercard CTEL2 设计冻结）

### 设计：host-POS 报文重构为 CTEL2 TEI（P0，仅文档）
- **新增** docs/protocol-v2-design.md：TEST_INFO/CONFIG/ACT + ACK、MSG、OUT(OPS/DataRecord/DiscData)；
  CONFIG 内嵌完整配置（R2=b）；卡端全量 APDU 作为判定源（R3/R4）；断言路径 card.* / out.*（R5）；
  OPS（DF8129，8B）位表（R6）；删除 v1.2 POS 报文、不与旧版并存（R7）。
- **新增** docs/change-notice-v2-pos.md（POS 端实现通知）；
  **新增** docs/change-notice-v2-card.md（卡端通知：APDU 恒开/按案例/可判定 + token 白名单扩展）。
- **裁决记录**：R1=b 保持 TCP 角色；R2=b CONFIG 内嵌完整配置；R3 卡端链路保留不动；
  R4=a 复用 trace 且恒开可判定；R5 接受 card.*/out.*；R6 OPS 两张图；R7 先 P0、不并存。
- **代码未改**：P0 仅文档，P1 起实施（host CTEL2 层、卡端 APDU、用例、Mastercard 配置/CAPK）。

## 2026-09-15（术语统一：scheme -> brand，全量改名）

### 重构：卡组字段全面更名为 brand
- **背景**：`scheme` 不是卡组的规范术语，统一改为 **brand**（card brand）；取值保持小写 id 不变。
- **改动**：
  - **wire**：`case_started` / `session_abort` 的 `scheme` -> `brand`（硬切换，无兼容别名）。
  - **数据**：case/group JSON 的 `"scheme"` -> `"brand"`；`cases/<b>/scheme.json` -> `brand.json`；
    `.gen_cases_manifest.json` 同步。
  - **代码**：`SchemeDef`->`BrandDef`、`scheme`->`brand`、`schemes`->`brands`、
    `scheme_cases`->`brand_cases`、`SCHEMES`->`BRANDS`、API 路径参数 `{scheme}`->`{brand}`、
    CLI `--scheme`->`--brand`（`import_expresspay_xml.py`/`export_kernel_params.py`）。
  - **前端**：`SchemesPage.tsx`->`BrandsPage.tsx`，类型/字段/文案同步。
  - **文档**：全部 living docs 同步；`docs/protocol.md` 增 13；新增
    `docs/protocol-change-notice-brand.md`（对端收到 brand 字段）。
  - 历史测试报告证据（`docs/test-report-v1.2/**`）也一并改为新术语以保持一致。
- **验证**：代码/数据中无残留 `scheme`（仅改名说明文档保留旧词）；全部 .py AST、cases JSON 解析通过；`load_library('cases')` 7 卡组 0 error。按要求未跑 pytest/tsc。
  7 卡组 0 error。按要求未跑 pytest/tsc。

## 2026-09-15（v1.2.x：kernel params dir 缺文件策略放宽为 (c)）

### 调整：目录只覆盖部分卡组不再报 error
- **背景**：amex 命名库落地后，外部目录只有部分卡组有配置，原 C3「每 brand 缺文件即 error」
  会对 discover/jcb/mastercard/pure/unionpay 各报一条红色 error。
- **改动**：`load_library` 改为仅当整个 `<kernel-params-dir>` 内**一个配置都没有**
  （既无 `<brand>.json` 也无 `<brand>/<ref>.json`）时报 **一条** error；
  单个卡组缺配置是正常情况，静默使用自带表。
- **验证**：空目录/不存在目录 = 1 error；仅有 `visa.json` = 0 error；仅有 `amex/` = 0 error。
  既有 `test_c3_missing_external_file_is_reported_not_silent`（空目录）仍成立。

## 2026-09-15（v1.2.x：Amex Expresspay 4.2.0 POS 配置）

### 新增：DRL / ExceptionFile + 命名 POS 配置库（仅 amex）
- **类型**：协议扩展（host 侧）+ 数据导入工具 + 文档
- **背景**：用户提供 `refernce/Expresspay 4.2.0/{TerminalConfig,TerminalProfile}`
  参考 XML，要求给 amex 卡组补 POS 端配置，版本 `"4.2.0"`，不影响其他卡组。
- **改动**：
  - `KernelParams` 增 `drl`（`9F06+8F+DF8105`）、`exception_file`（`5A+5F34`）与
    host 侧 `version`（不上 wire）；`payload()` 增 `drl`/`exception_file` 两个 wire 键。
  - `kernel_tlv`：新增 `encode_drl`/`encode_exception_file`；模数上限由白名单
    (128/144/176/248) 放宽为 **<=256 字节**；指数放宽为 1-8 字节；多字节 tag 解析支持
    3 字节（`DF810x`）；BER 长度支持 `82 xx xx`。
  - `validation`：DRL/ExceptionFile 校验；`DF06/DF07` 允许 0；命名库配置以
    `trust_checksum=True` 校验（vendor 样例 DF03 不符只 warning，不阻断）。
  - `loader`：新增仓库外命名 POS 配置库 `<kernel-params-dir>/<brand>/<ref>.json` +
    `CaseLibrary.named_kernel_params`；case 增 `kernel_params_ref`（与内联
    `kernel_params` 互斥，仅内存解析，模数不写回案例文件）。
  - 工具 `tools/import_expresspay_xml.py`：把 CONF/Profile XML 转成命名配置
    （AID/CAPK/DRL/ExceptionFile/term）；term = CONF 的 TerminalParameter + 同号
    Profile，多 TLV 拼接。
  - 前端 AddCasePage 内核参数页签支持 `drl`/`exception_file`/`version` 与
    `kernel_params_ref`。
  - `.gitignore` 增 `refernce/`（含 CAPK 模数，仅本地保留，不进 git）。
  - 文档：`docs/protocol.md` 12.2/12.4/11、
    `docs/protocol-change-notice-v1.2.md` 2/4.1/4.2/4.4/4.8、`README.md`。
- **已知数据异常**：`TerminalProfile/Profile_25.xml` 的 `9F6E=188000003` 为 9 位奇长 hex，
  工具按源数据原样报错并跳过 `CONF_25`（其余 31 份正常），待用户裁决。
- **验证**：按要求本轮**未运行 pytest**（仅做模块导入与 31/32 参考配置离线校验）。
- **不影响**：其他卡组；`pos_config`/`card_config`/信封字段不变。

## 2026-09-14（v1.2 测试审核修复轮，host 侧 P0–P4）

### 修复：测试审核的 5 个在库缺陷（F1/F2/F3 + H1/H2）
- **类型**：代码修复（host 运行期 + 测试工具链）
- **依据**：`docs/test-report-v1.2/DEFECTS.md`、`docs/v1.2-测试审核与修复计划.md` TS-1..TS-5
- **改动**：
  - **F1**（P2）`tools/mock_clients/mock_common.py` 收发分离：单 reader task + `asyncio.Queue`，
    `on_message` 内等待 ack 不再阻塞 ack 投递。修复前 POS mock 必然打印
    `ack timeout (15.0s) for transaction_result`，掩盖「host 已落盘并 ack」的事实。
  - **F2**（P2）`run.abort_reason` 统一为 `case_failed:<case_id>:<reason>`：断言 FAIL 路径
    （不经 `_fail()`）也补 `fatal_reason`；`_fail()` 不再覆盖已记录的 `failure_reason`
    （修复前会把 `assertion_failed` 改写成 `result_timeout`）。
  - **F3**（P3）`results/<case>.json` 三条写入路径统一输出 `attempt`(0 基) 与 `attempts`(计数)。
  - **H1**（P2）`TERMINAL_RUN_STATUS` 与 `RunPage.finished` 增 `ERROR`；`_finalize_crash`
    对已终态运行不再降级为 ABORTED。
  - **H2**（P1 条件/P2）`RunSession.card_emulating` + `_run_case` try/finally →
    `_stop_card_if_started`：`emulating` 之后任何失败路径都补发 `stop_emulation`（原先只有
    第 5 步 finally 会发，跨案例残留风险）；仍保证每案例只发一次。
  - **H-LOG**：记录 `emulating.registered_aids`（卡端裁决 3.1c，ready 阶段保持 `[]` 不作判据）。
- **验证**：`cd backend && python -m pytest` = **173 passed**（新增 `tests/test_v12_fixes.py`、
  `tests/test_df05_encoding.py`）；L3 实跑确认 POS 侧可见 `detail=persisted` 与 `detail=duplicate`。
- **不影响**：`pos_config` 字段/编码冻结不变；`config_type=pos_kernel_config` 冻结不变。

### 变更：CAPK 密钥材料移出工作区（C3）
- **类型**：安全/配置（含 CLI 新参数）
- **改动**：`cases/visa/kernel_params.json`（含 CAPK 模数）移到仓库外
  `E:\Work\ZT\_secure\card_simulator_host\kernel_params\`；host 新增
  `AppConfig.kernel_params_dir` + `serve --kernel-params-dir`，按 `<brand>.json` 加载。
  仓库内旧路径仅在 `load_library(use_legacy_kernel_params=True)` 时读取，**默认关闭**。
  `logs/` 中三份含模数的会话 dump 已归档到 `_secure` 外置目录。
- **验证**：仓库内（含 logs/、证据、快照）模数残留扫描 = 0；`git status` 不含任何内核参数文件。
- **回退**：把外部目录去掉即可回到「不下发内核参数」；不会读到仓库内文件。

### 新增：DF05（CAPK 失效期）四种编码（C2）
- **类型**：协议/数据模型
- **改动**：`CapkParam` 增 `expiry_encoding`(`ascii8`|`bcd4`|`bcd3`|`raw`) 与 `expiry_raw`；
  `kernel_tlv.encode_expiry/decode_expiry` 实现四种编码；`validate_kernel_params` 按编码校验
  （合法公历日期、bcd3/bcd4 的 2000–2099 范围、往返一致、raw 非空偶长 hex 且只报 warning）。
  `ascii8` 为默认且与旧编码器逐字节一致；DF03 与 DF05 编码无关。
- **策略**：资产中 `A000000333/08`（`DF05=PHWWIPQI`）默认排除，CAPK = 28 条；
  确需下发时用 `expiry_encoding=raw` 显式放行。
- **验证**：`tests/test_df05_encoding.py`（往返/边界/闰日/DF03 不变性/raw 逃生口/校验规则）。

### 修订：`docs/test-plan-v1.2.md` 的 7 处方案偏差（P-D1..P-D7 / TS-7）
- P-D1 §6.5 失败验证换为真·奇数长度 term，并注明 `pos_kernel_config_apply_failed` 需 reject 型 POS
  （`9F660436000080` 是 14 字符合法值，不能当坏数据）。
- P-D2 §6.3 计数改为 AID 19 / CAPK 28 / TERM 1，并说明排除 PHWWIPQI 的原因。
- P-D3 §4.8 判据分档：`emulating` 之前失败 → 仅 `session_abort`；之后失败 → `stop_emulation` + `session_abort`。
- P-D4 §5.2 去重窗口限定为当前 attempt；跨案例晚到重传 → `quarantined` 且写 `quarantine.jsonl`。
- P-D5 §4.3 落盘核对同时读 `attempt` 与 `attempts`。
- P-D6 §0.3 环境限制改为「仅受限沙箱/无控制台时出现」的条件性免责。
- P-D7 §4.9 用 `--delay` 远大于 `timeout_seconds` 取代依赖控制台 stdin 的 `--interactive`。

### 协议文档升到 v1.2（TS-8，部分）
- **类型**：文档（协议权威）
- **改动**：`docs/protocol.md` 标题升为 v1.2，新增 **§12 v1.2 变更**（版本协商硬切换、
  删除 device_config、`pos_kernel_config`、`card_config.trace_enabled`、`session_abort`、
  ack 矩阵、`transaction_result` 落盘后 ack 语义、失败即整轮停止、`stop_emulation` 时机、
  坏帧累计规则、落盘契约、新增 CLI 参数、测试基线），并声明「与 §12 冲突处以 §12 为准」。
- **降级说明（在 CHANGELOG 备案）**：§0–§11 正文**仍是 v1.1 措辞**（如 §2 握手宽容、
  §3 ack 表、§5 心跳、§6.3 host→pos 帧目录、§10 中断语义），未逐节改写。
  §12 已覆盖全部行为差异且为最高优先级，但**逐节正文改写留待后续**；
  POS 侧完整口径见 `docs/protocol-change-notice-v1.2.md`。

### 补齐：T7 / T14–T15 / T20（第二轮）
- **类型**：工具 + 前端 + 联调脚本
- **改动**：
  - **T7** `tools/gen_cases.py`：生成的每个案例显式带 `kernel_params: null`（6 处案例字典，
    精确锚定保证落在 `transaction` 内与 `pos_params` 同级）；
    `_clean_previous_generated` 增加**清单路径逃逸防护**（解析后必须落在 brand 目录内，否则跳过并告警）；
    重新生成 7 卡组演示库 —— **自建用例 OPBD-001-* 与 OPBD-001 组全部保留**，`load_library` 0 error。
  - **T14** 前端类型/API：`KernelParams`/`CapkParam`/`AidKernelParam`/`TlvField`、
    `CaseDetail.kernel_params`、`failure_reason`（CaseRunResult/RunDetail/HistorySummary）、`attempt`；
    `startRun` 支持 `group_id` 与可选 `case_ids`；`loader.public_case` 暴露案例级 `kernel_params`；
    `api.history_detail` 透出 `attempt`/`failure_reason`。
  - **T15** 添加用例页新增**内核参数页签**：JSON 文本域 + 可复制结构示例 + 空模板/清空按钮；
    校验只做「JSON 合法 + 顶层形状（aids/caps 为数组、term 为字符串、不可全空）」，
    深度校验（hex 长度、DF03、DF05 编码、去重）仍由服务端负责，避免双份规则漂移。
    本版本**不做 AID/CAPK 表格 UI**（按方案降险）。
  - **T20** `tools/joint_debug/`：`run_cases.py` 报告打印/回传 `failure_reason`，
    preflight 说明 `/api/health` 不返回 `protocol_version`（须看握手日志 `protocol=1.2`），
    `--on-failure` 默认改 `stop` 并注明 `continue` 在 v1.2 已失效；`report.py` 逐用例表新增
    **失败原因**列并附 SKIPPED 说明。
  - `README.md`：v1.2 横幅 + 运行语义改写（失败即停、ack 纪律、session_abort、断线即 FAIL）、
    内核参数与 `export_kernel_params.py` 章节；测试数更新为 184。
  - **T16** HistoryPage 新增「失败原因」列 + SKIPPED 说明；RunPage 队列同列。
  - **T17** CaseSelectPage「运行本组」改用 `kind=group` + `group_id`（后端自行解析子案例）；
    移除「失败继续」选项（v1.2 恒定失败即停），仅留重试次数。
  - **T18** `ws.ts` 保存并清理重连定时器，`open()` 在 `closedByUs` 时直接返回，
    避免切页/StrictMode 双挂载后旧定时器复活 socket。
  - `docs/POS机应用开发手册.md` 顶部加 v1.2 差异对照表并指向变更通知（正文仍是 v1.1，
    待后续逐节升级）。
- **验证**：`cd backend && python -m pytest` = **184 passed**（新增 gen_cases 清单防护 2 例）；
  `npx tsc --noEmit` 通过、`npm run build` 成功；
  `report.py` 对真实 run 冒烟输出新列。
- **仍未做**：`docs/POS机应用开发手册.md` 正文逐节升级、`protocol.md` §0–§11 逐节改写、
  可选 TS-10 日志降噪。前端 T14–T18 已全部完成。

### 基线
- 仓库此前**无任何 commit**；本轮先建基线 `a3c3791 baseline-before-v1.2-fixes`。
  顺序偏差：CAPK 文件在基线 commit **之前**就已移出工作区（方案原顺序是先 commit），
  以免模数永久进入 git 历史。

## 2026-09-14

### 新增文档：dsh 跨会话双工通信插件操作手册
- **类型**：文档（工具/环境，非协议、非上位机功能）
- **改动**：新增 `docs/dsh-session-link.md`，记录 dsh 动态 Cordis Host 插件 `seslnk-1`
  （当前包 `pkg-6`）的操作手册：4 个工具（`session_link_list` / `_send` / `_reply` / `_status`）
  的用法、A→B→A 双工链路、账本验证、8 个分支用例、失败排查、5 层防风暴参数、两条能力边界。
- **不影响**：本仓库代码、`protocol.md`、用例库、前端行为，以及 host↔卡端联调基线（无版本变更）。
- **验证状态**：包激活、工具 schema、`inject` 生效为**直接观测**；
  实投、回投关联、离线补投、hop 累计为**未验证**（未实投），已在文档第 10 节逐项标注。
- **注意**：插件是**运行时对象**，不落盘；dsh 重启后需重新 `cordis_run` 到 `pkg-6`。

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
     每个 brand 队尾（`order_cases()`）；
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

### 修复 N12 用例超时与跨 brand 串扰（run #14 复盘）
- **类型**：用例库（超时）+ 工具（驱动）
- **现象**：run #14 中 `UPI-CL-007` 与 `MC-CL-001` 判 INCONCLUSIVE。
- **根因**：
  1) N12 静默让 POS 内核等满自身超时（实测 `UPI-CL-007` 结果在 case_started 后
     **约 185s** 才回传），而用例 `timeout_seconds=180` → 差 5s 判超时；
  2) 该超时用例在 POS 侧仍在收尾，host 已切到下一个 brand，POS 对新的
     `case_started` 回"上一笔交易尚未结束，仅 ack 本用例并忽略" → 下一个用例
     （`MC-CL-001`）整轮空等到超时；迟到的 `UPI-CL-007` 结果被 host 归档
     （quarantined，不参与判定）。
- **修复**：
  - `UPI-CL-006` / `UPI-CL-007` 的 `timeout_seconds` 180 → **300**（覆盖内核自身
    N12 超时），已 `POST /api/library/reload` 生效；
  - `tools/joint_debug/run_cases.py` 新增 `--settle`（默认 **15s**）：每个 brand
    结束后静默等待，等 POS 收尾上一笔再开下一个 brand（原为固定 2s）。
- **验证**：`--help` 含 `--settle`；`/api/cases/unionpay/contactless/UPI-CL-007`
  返回 `timeout_seconds=300`；库重载 28 例 0 error。

### 新增 `POST /api/library/reload`（改用例免重启）+ VISA-CL-001 加 floor limit
- **类型**：后端（工具/接口）+ 用例库 + 协议约定
- **背景**：联调中手工改用例后必须重启后端才能生效，重启会断开双端连接
  （卡端上次重连耗时约 8 分钟）。新增 `POST /api/library/reload`：重读用例库，
  返回各 brand/mode 用例数与 error 列表，失败返回 400。
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
- **内容**：`BRANDS` 增加 jcb/amex/discover/pure；每卡组生成 brand.json（card_template + pos_params）+ 1 冒烟用例 `<PREFIX>-CL-001`（offline approved）；visa/mastercard/unionpay 原有完整演示不变；生成器保持清单式非破坏
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
- **内容**：删除共享 profiles 库；`profile`/可选 `keys` 内嵌案例（profile.id 自动=case_id）；`BrandDef.card_template`+`pos_params`（卡组模板）；`transaction.pos_params`（tag 列表，按卡组模板自动带出、可改 tag）；`config_push{pos_config}` 下发 POS（安卓不收）；AddCase「卡片档案」页签（克隆模板编辑）
- **文档落点**：protocol.md §8（布局/字段/pos_params/keys/分工边界）；README §用例库/02
- **验证**：端到端 pos_config 下发 + 判定

### 总案例分组 + PPSE 档案适配
- **类型**：案例库 + 前端 + Demo
- **内容**：GroupDef（总案例说明+test plan）/ 子案例；`ppseMode/ppseSw/ppseData`（auto/disabled/empty/custom）；demo `OPBD-001` + PPSE 档案变体
- **文档落点**：protocol.md §8.3 PPSE 语义；README
- **验证**：树/详情/运行冒烟（OPBD-001-01 FAIL、02–04 PASS 语义保持）

### v1.1 协议基线（更早）
- 握手/心跳/ack/重试/身份、单端口双客户端、`card_config{profile,keys}`、trace、POS 判定权威等 —— 均已吸收进 `docs/protocol.md`（H1–H10/O1–O7/N1–N10）。
