# 测试情况报告：协议 v2 P1c-1 修复验证（Round 2）

> 依据文档：`docs/test-plan-v2-p1-round2.md`
> 被测对象：**`0fb2c40` + 工作区未提交的 P1c-2 host 改动**（见 §5，非仅 `0fb2c40`）
> 执行时间：2026-09-18（本地），普通终端（非 DSH 沙箱）
> 上一轮：`docs/test-report-v2-p1/REPORT.md`（Round 1）

## 0. 环境与性质（显式标注）

| 项 | 值 |
| --- | --- |
| OS / Python / pytest | Windows / 3.14.5 / 9.1.1（`asyncio_mode=auto`） |
| rootdir / configfile | `backend` / `pytest.ini` |
| 设备 | **无**。未启动 host、未连卡端、未连 POS |
| 性质 | **纯进程内单元/集成测试**，无真机、无 mock 联调 |

## 1. 结论：**与 Round-2 文档预期完全一致**

| 轮次 | collected | 结果 | 耗时 | 文档预期 | 判定 |
| --- | --- | --- | --- | --- | --- |
| ① 聚焦子集 | 155 | **155 passed / 0 failed / 0 error** | **20.56s** | 0 failed | ✅ 符合 |
| ② 全量 | 184 | **170 passed / 14 failed / 0 error** | **21.55s** | 仅 F4 的 14 个 | ✅ 符合 |

Round 1 的 5 个"应全绿"失败（F1 的 2 个 + F2 的 1 个 + F3 的 2 个）**全部消除**，且**未引入任何新失败**。

**附带重要改善（F3 修复的间接收益）**：两轮耗时从 592.06s → **20.56s**、597.36s → **21.55s**。原因是 F3 消除了 `test_shutdown` 用例内 3×约 24s 的无效重试（子进程不再因管道阻塞而"起不来"）。

用例数 154 → 155 的增量 = `0fb2c40` 在 `test_frames.py` 新增的**信封 `v=2` 断言**（Round 1 报告 §6 的覆盖度缺口），已核实被收集并 PASS。

## 2. F1 复核（`tools/gen_cases.py` 撞名，含"9 处"的说法）

其他会话指出 Round 1 报告只发现第 1 处、实际有 9 个函数撞名（`ast.parse` 不校验重复形参）。本轮**独立复核成立**：

| 复核手段 | 结果 |
| --- | --- |
| `python -m compileall -q tools backend\emv_host` | **exit=0**（全树编译通过） |
| AST 结构化扫描（比较 `n.args.args` 形参名集合大小） | `tools/**.py` 中重复形参函数 = **none** |
| 扫描器自测（对 `def f(a, a): pass` 跑同一逻辑） | 命中 `['f']` → 扫描器**有效**，故"none"是真阴性 |
| `embedded_profile` 形参 | `['brand_conf', 'brand']` —— 与裁定一致（`: dict` 注解保留） |
| 改动量 | `tools/gen_cases.py` 32 insertions / 32 deletions；`brand_conf` 全文 34 处 |

`test_export_kernel_params.py` 11 个用例全部通过（Round 1 为 2 failed / 9 passed），其中 2 个正是通过 `load_gen_cases()` 动态导入该文件的用例。

## 3. F2 / F3 复核

| 项 | 用例 | 结果 |
| --- | --- | --- |
| **F2** | `test_runner_result.py::test_unattributed_out_is_quarantined` | ✅ PASS（断言 `== 1`，保留 ack 断言） |
| **F3** | `test_launch.py::test_serve_from_backend_dir_uses_repo_defaults` | ✅ PASS |
| **F3** | `test_shutdown.py::test_real_process_ctrl_c_exit_clean` | ✅ PASS |

`test_runner_result.py` 6/6 通过；`test_launch.py` 7/7 通过；`test_shutdown.py` 9/9 通过。

## 4. F4：14 个 v1.2 残留（与文档预期一致，仍收敛于同一根因）

### 4.1 失败清单（`文件::用例名` → 断言行 / 末行错误）

`tests/test_runner_timeout.py`（14 个用例中 8 失败）：

| # | 用例 | 行 | 末行错误 |
| --- | --- | --- | --- |
| 1 | `test_result_timeout_fails_case_stops_run_and_skips_remainder` | :183 | `test_info_error` |
| 2 | `test_pos_offline_fails_case_instead_of_warning` | :247 | `test_info_error` |
| 3 | `test_pos_config_rejection_fails_case` | :261 | `test_info_error` |
| 4 | `test_case_started_rejection_fails_case` | :273 | `test_info_error` |
| 5 | `test_pos_case_started_ack_timeout_fails_case` | :290 | `test_info_error` |
| 6 | `test_kernel_config_is_pushed_before_pos_config` | :361 | `IndexError: list index out of range` |
| 7 | `test_kernel_config_rejection_fails_case` | :385 | `test_info_error` |
| 8 | `test_case_level_kernel_params_override_brand` | :412 | `RuntimeError: coroutine raised StopIteration` |

`tests/test_v12_fixes.py`（15 个用例中 6 失败）：

| # | 用例 | 行 | 末行错误 |
| --- | --- | --- | --- |
| 9 | `test_f2_assertion_failure_abort_reason_carries_case_id` | :244 | `test_info_error` |
| 10 | `test_f2_passing_case_is_not_aborted` | :264 | `'ABORTED'` |
| 11 | `test_f2_every_failure_path_names_the_case` | :290 | `case_failed:VISA-CL-001:test_info_error` |
| 12 | `test_f3_all_writers_emit_attempt_and_attempts` | :307 | `'FAIL'` |
| 13 | `test_h2_early_failure_still_parks_the_card` | :377 | `test_info_error` |
| 14 | `test_h2_kernel_rejection_still_parks_the_card` | :392 | `test_info_error` |

**确认：失败仍 100% 收敛在 `test_runner_timeout.py` + `test_v12_fixes.py` 两个文件（8+6=14），无第三个文件。**

### 4.2 根因（与 Round 1 报告 §5.2 一致）

`StubProtocol`（`test_runner_timeout.py:102`、`test_v12_fixes.py:92`）缺 v2 的 `send_to_and_ack_signal`（真实实现 `protocol/server.py:148`，`executor.py:858` 调用）→ v2 运行环第一步 `test_info` 抛 `AttributeError`：

```
ERROR emv_host.runner:executor.py:138 [VISA-CL-001] test_info signal error:
      'StubProtocol' object has no attribute 'send_to_and_ack_signal'
```

`IndexError`（:361）/`StopIteration`（:412）为同一根因的次生现象。

### 4.3 附带复核：reason 拆分（`0fb2c40`）已生效

同样的 stub 缺陷，Round 1 记为 `test_info_ack_timeout`（把本地异常误标为"对端未答超时"），本轮记为 **`test_info_error`** —— 即 `executor._pos_signal` 的 `*_ack_timeout`（`ConnectionClosed`）与 `*_error`（本地/编程/传输异常）拆分**已实测生效**，Round 1 报告 §5.3 的推断已被证实并修复。

## 5. 本次测试对象的偏差说明（**重要**）

Round-2 文档假设被测对象是 `0fb2c40`。实测时工作区**另有未提交改动**（`git status`：`M backend/emv_host/cases/loader.py`、`M backend/emv_host/protocol/connection.py`、`M backend/emv_host/runner/executor.py`），即 **P1c-2 的一部分已被落地但未提交、未测**。本报告测的是这个真实工作区状态。

逐条列出这些未提交改动及其裁决依据：

| 文件 | 改动 | 依据 |
| --- | --- | --- |
| `cases/loader.py:691` | named POS config 装载日志 `logger.info` → **`logger.debug`** | 裁决"1.允许降噪" / D3=是 |
| `protocol/connection.py:151,253` | 入站帧 `v != PROTOCOL_VERSION` → 握手期 `HandshakeError("bad_frame")`、运行期 `ConnectionClosed` | 裁决"2.拒绝"（拒绝 `v != 2` 入站帧） |
| `runner/executor.py:904-916` | DRL / EXCEPTION_FILE **恒发**；空表发 `{id:"DRL", value:""}` = 清空且不加 | D1=允许建议 |
| `runner/executor.py:988-1014` | D2 口径 (a)：`no_outcome = OPS.status_bits=="0000" and 空 DataRecord` → `failure_reason="out_no_outcome"` | D2=(a) |

**已核实：这些未提交改动未引入任何新失败**——② 的失败集合与仅 `0fb2c40` 时的 F4 完全一致（同 14 个用例、同一根因），① 155/155 全绿。

### 5.1 覆盖度核查：三条新行为**均无测试覆盖**

"未引入新失败"只说明它们**没有破坏**既有用例，不等于它们**被验证**。逐条核查后：

| 新行为 | 位置 | 测试覆盖 |
| --- | --- | --- |
| D1 空表 `value:""` = 清空 | `executor.py:904-916` | **0**（`tests/` 内无任何 `_config_frame` / `DRL` / `EXCEPTION_FILE` / `kernel_params_ref` 引用） |
| D2 `out_no_outcome` | `executor.py:994-1014` | **0**（全仓仅 `executor.py` 内 6 处引用） |
| 入站 `v != 2` 拒绝/断开 | `connection.py:151,253` | **0**（唯一信封相关用例是 `test_frames.py:71` 的**出站** `make_frame` 断言；无任何用例发送 `v != 2` 入站帧） |

即：这三条新行为目前是**未被验证的新代码**，② 全绿与它们无关。建议在 P1c-2 内补三条单测
（`_config_frame` 空/非空表、`out_no_outcome` 主路径、入站 `v != 2` 的握手拒绝与运行期断开）。

**D1 的触发场景目前也不可达（已核实）**：D1 的注释以 `CONF_01_RemoveCRL` 为由（该文件 `drl` 为空，
其余 `CONF_01*` 有 30 条），但**全仓没有任何 case 引用 `CONF_01_RemoveCRL`**（除代码注释/文档/`tmp/` 草稿外
0 处命中）——它是「已入库但未被用例选用的配置」。故 D1 属**前瞻性正确性修复**（为将来选用该配置时不残留
上一笔 DRL），当前既无用例触发、也无测试覆盖。

## 6. D2 `no_outcome` 复核（对未提交改动的实测）

**位置**：`backend/emv_host/runner/executor.py:1000-1014`（未提交改动的一部分）

```python
no_outcome = (result["out"]["ops"].get("status_bits") == "0000"
              and not result["out"]["datarecord"])
verdict = assert_result(result, case.expected_results)
record["status"] = verdict.status
if no_outcome and verdict.status != "PASS":       # ← PASS 时被跳过
    record["failure_reason"] = "out_no_outcome"
elif verdict.status == "FAIL":
    record["failure_reason"] = "assertion_failed"
...
if verdict.status == "FAIL":
    session.failure_reason = session.failure_reason or (...)
elif no_outcome:                                   # ← 但这里给整轮设了失败原因
    session.failure_reason = session.failure_reason or "out_no_outcome"
return {"PASS": "pass", "WARN": "warn", "FAIL": "fail"}[verdict.status], "persisted"
```

**更正说明（重要）**：本节初稿曾推断"OPS 全 0 会被解成 `APPROVED`，故期望批准的用例会**假 PASS**"——**该推断已被实测否定**，故整节按实测重写。原因：`_OPS_STATUS`（`judge/outcome.py:31-35`）**不含 `0b0000`**，OPS 全 0 → `status="RFU(0000)"`、`outcome="rfu(0000)"`、`resp_code=""`（不伪造 05/91/80）。

**实测（真实 `_on_out` + 真实 judge，证据 `raw/probe-d2-no-outcome.txt`）**：

| 场景 | 用例形态 | 落盘 `status` | `record.failure_reason` | `session.failure_reason` |
| --- | --- | --- | --- | --- |
| **A** | 期望 `outcome eq approved`（典型用例） | **FAIL** | `out_no_outcome` | `out_no_outcome` |
| **B** | `expected_results == []`（无断言） | PASS | `None` | `out_no_outcome` |
| **C** | 仅 `warn` 级规则 | WARN | `out_no_outcome` | `out_no_outcome` |

**结论**：

- **主路径正确**：常规用例（含 pass 级断言）收到"OPS 全 0 + 空 DataRecord"时判 **FAIL / `out_no_outcome`**，符合 D2(a)，且不伪造 decline 码（`resp_code` 为空串）。
- **边界不自洽（低风险）**：场景 B 出现"用例自身记 PASS，但整轮以 `out_no_outcome` 停止"；场景 C 出现 WARN 用例携带 `out_no_outcome`。根因是 `no_outcome` 与 `assert_result` 未解耦（`executor.py:1000-1014`）。
- **可达性**：当前案例库 **0/136** 个用例 `expected_results` 为空、**0** 个使用 `severity:"warn"` → 场景 B/C **目前不可达**，故非当前风险，属健壮性问题。
- **建议（低优先，P1c-2 可选）**：把 `no_outcome` 与断言解耦（`no_outcome` 时直接强制 `status="FAIL"` + `failure_reason="out_no_outcome"`），并补 B/C 两条单测；或明确以注释固化"v2 用例必有 pass 级断言"这一前提。
- **覆盖缺口**：全仓 **0 个测试**覆盖 `out_no_outcome`（`executor.py` 内 6 处引用，`tests/` 0 处）。

## 7. 证据清单

| 文件 | 内容 |
| --- | --- |
| `raw/focused-subset-pytest.txt` | ① 完整原始输出：`155 passed, 3 warnings in 20.56s` |
| `raw/full-pytest.txt` | ② 完整原始输出（含 14 个失败的全部 traceback）：`14 failed, 170 passed in 21.55s` |
| `raw/failure-classification.txt` | ② 的 14 个失败块逐条根因提取 |
| `raw/p1_classify.py` | 上述提取脚本（与人工分析分文件） |
| `raw/probe-d2-no-outcome.txt` | §6 的 D2 三场景实测输出（A/B/C 的 status 与 failure_reason） |
| `raw/d2_no_outcome_probe.py` | 上述实测脚本（复用 v2 测试替身，驱动真实 `_on_out`） |

## 8. 下一步

- **F4 / P1c-2 未完成部分**（Round-2 文档 §4 所列）：迁移 `test_runner_timeout.py` / `test_v12_fixes.py` 到 v2；`docs/protocol.md` 重写为 v2；前端 v2 展示；以及 `docs/reply-to-pos-v2-confirmation.md` §C 的设计文档修订（§4 CONFIG 形态、§6.2 DataRecord 白名单、§6.3 DiscData）。
- **待裁决（低优先）**：§6 的 `no_outcome` 边界不自洽（场景 B/C，当前不可达）是否在 P1c-2 一并加固 + 补单测。
- **提醒**：上述 P1c-2 改动目前**均未提交**；建议在进入下一阶段前先明确提交边界（是否把 D1/D2/D3 + 入站 v 校验单独提交），避免与后续 F4 迁移混在一个提交里。

## 9. 追加：F4 迁移完成后的复测（2026-09-18 同日）

F4（`test_runner_timeout.py` / `test_v12_fixes.py` 迁 v2）已由子代理完成，并由本会话**独立复测**（不以子代理报告为准）：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q tests/test_runner_timeout.py tests/test_v12_fixes.py` | 子代理：`29 passed`；**本会话复跑全量见下** |
| `python -m pytest -q`（本会话独立执行） | **184 passed, 5 warnings in 27.14s** |

即 **② 的 14 个 F4 残留全部消除，全仓单测首次 0 失败**。证据：`raw/full-pytest-after-f4.txt`。

**改动审查（防"为过而弱化断言"）**：`git diff` 统计该两文件 19 处 assert 被替换、21 处新增，逐条核对后：

- 全部 19 处被删断言均为 v1.2 语义（`result_timeout`、`pos_config_rejected`、`pos_case_started_*`、
  `pos_kernel_config_*`、`session_abort`、`case_started`），且**均有 v2 对应替换**；
- 新增断言反而更强：`pos.seen == [MSG_TEST_INFO, MSG_CONFIG, MSG_ACT]`（顺序全量固定）、
  `record["status"] == "FAIL"/"PASS"`（原先只断 failure_reason）、`abort_reason == case_failed:C-01:out_timeout`；
- `session_abort` 的 3 处正断言（"POS/卡被通知会话已中止"）改为**负断言**"不发任何 v1.2 legacy 帧"，
  依据 `LEGACY_POS_FRAMES`（`test_runner_timeout.py:52`，6 个已删帧的非空集合，非空断言不空转）。
  **这是设计层面的功能移除，不是测试弱化**：v2 §10 已定稿删除 `session_abort`，
  即 v2 下**没有任何帧向 POS/卡端通告"整轮已中止"**——卡端仅靠 `stop_emulation` 收尾。
  若后续认为需要该通告，属**新增协议能力**，应回到设计评审，而不是恢复 v1.2 帧。

**遗留（子代理已如实上报，未擅自处理）**：若测试函数名仍含 v1.2 术语（`test_case_started_rejection_fails_case` 等），
为保持与审计清单 1:1 对照而刻意保留；若要求改名，另开一次机械改名提交。

## 10. P1c-2 剩余清单（截至本次会话结束时的实际状态）

已完成（本会话）：Round-2 测试与报告、D1/D2/D3 + 入站 `v` 校验复核定性与覆盖度核查、
设计文档 §4/§6.1/§6.2/§6.3/§8/§11 修订、POS 答复定稿、F4 两个测试文件迁 v2（全量 184 passed）、CHANGELOG 落档。

未完成（需裁决或后续执行）：

| # | 项 | 状态 / 卡点 |
| --- | --- | --- |
| 1 | `docs/protocol.md` 重写为 v2 | **已完成**（810 行 / 56 KB，599 插入 / 341 删除）；本会话已抽检：标题 v2、wire `type` 与 `messages.py` 一致、失败原因码在 `executor.py` 逐条存在、`out_invalid` 正确标注为 ack detail 而非 failure_reason |
| 2 | 三条新行为补单测（D1 空表 / D2 `out_no_outcome` / 入站 `v`） | **已完成**（`backend/tests/test_v2_rulings.py`，8 个用例）；本会话独立复跑 8 passed，并做过"破坏即失败"的非空转验证 |
| 3 | **前端 v2 展示**（`out.ops.status` + APDU） | **待裁决 Q8**：先最小可用，还是一步到位（`protocol-v2-design.md` §11 Q8 仍未决） |
| 4 | **README.md 的 v2 同步** | **未做**。实测 README 仍为 v1.2 口径，至少 6 处已过时：协议横幅（`README.md:4-13`，仍写 `protocol_version:"1.2"`）、编排模型（`:27`，仍写 `config_push{pos_kernel_config}`/`config_push{pos_config}`/`transaction_result`）、交易参数（`:113,:160`，仍写 `transaction.pos_params` + `config_push{pos_config}`）、v1.2 语义（`:170-184`，含 `session_abort` 必 ack）、mock 说明（`:251-253`）。**其中 `session_abort` 在 v2 已删除（§10）**，README 仍在承诺该行为 |
| 5 | 提交边界确认 + `AGENTS.md` 未提交改动 | 工作区另有 **`AGENTS.md` 的未提交改动（非本会话所为）**，提交时需与本次改动分开 |
| 6 | 三条新行为的联调落地 | 以上均为本机单测，**未与 POS/卡端联调** |

## 11. 复核中发现的**功能性缺口**（非测试失败——全绿也不代表 v2 能跑通）

本节由本轮 P1c-2 复核（含 `docs/protocol.md` 重写时的设计↔代码对照）产生，**均不体现为测试失败**，
但会直接决定 v2 联调能否进行。按 AGENTS.md §7.14，仅记录实测事实，不含"已修复"表述。

### G1（**阻断联调**）ACT 交易数据未迁移 → wire 上 `ACT.signalData` 恒为空数组

- **实测**：`cases/**` 共 **35** 个带 `transaction` 的案例文件，**35/35 的 `act` 为空**，
  其中 **31** 个仍只带 legacy `pos_params`（例：`AMEX-CL-001`/`DISC-CL-001`/`JCB-CL-001`/`MC-CL-001`
  均为 `act=0, pos_params=3`）。
- **代码路径**：`executor.py:798-800` 直接以 `[p.model_dump() for p in case.transaction.act]` 组帧，
  **无 `pos_params` 回退**；`models.py:471-476` 明确 "`pos_params` is retained for legacy case data but is
  **NOT sent to the POS any more** (v2 refactor, no coexistence)"（即不是可选项，是设计取舍）。
- **影响**：v2 联调时 POS 在 `act` 帧收不到任何交易数据（金额/日期/交易类型…），
  交易无法按用例执行；而**单测 184 passed 完全覆盖不到**——测试替身不校验 `ACT` 载荷内容。
- **证据**：`docs/protocol.md` §8.1 末（L544-545）已按代码如实标注；本轮独立复算见上。
- **待裁决**：把 `cases/**` 的 `transaction.pos_params` 迁移为 `transaction.act`
  （含 `9F02`/`9F03` 6B BCD、`5F2A`/`5F36`/`9A`/`9F21`/`9C` 的 hex 编码），
  或明确该迁移归 P3 且**迁移完成前不做 v2 联调**。

### G2（P2 已知，但需前置）`card.*` APDU 判定未接线

`session.card_apdus` **无任何写入者**（`runner/session.py:48,74,83` 仅初始化/清空；
唯一读者 `executor.py:972-974` 恒得空 dict）→ `card.*` 断言恒 missing。
设计 R4=a 要求"trace 恒开且作为判定源"，代码仍受 `--trace-enabled` 门控（`executor.py:427`）
且只落盘不判定（`executor.py:416-440`）。`card_apdu_*` / `out_ops_mismatch` / `out_datarecord_missing`
等失败原因码**在代码中不存在**（全仓 grep 无命中）。属设计 §8 声明的 P2，但联调计划应显式列为前置。

### G3 已发出的 POS 通知文档已过期（对端改动须走通知文档）

`docs/change-notice-v2-pos.md:24` 仍写 CONFIG **数组形态** `{id:"AID", value:[...]}`、
`:45` 仍写 DataRecord = **TLV DB 全量**；与最终定稿（`reply-to-pos-v2-confirmation.md` §A/6.2、§B.1）
及代码（`executor.py:900-916` 逐条、32-tag 白名单）**冲突**。按 AGENTS.md §2 约定，
对端改动只能通过通知文档交付 → 需要**重发/勘误**该通知（本轮未改，属越界范围）。

### G4 数据缺口：DataRecord 白名单的 31 条具体 tag 清单在仓库内不存在

定稿只点名新增 `9F66`（白名单 32 条 = 31 + `9F66`），但**31 条的具体清单无据可查**，
无法核对 POS 侧实现。需补来源（POS 侧 `CreateEMVDataRecord()` 的既有列表或另行定义）后再落文档/常量。

## 12. 收尾：本会话结束时的最终实测状态

| 命令 | 结果（本会话独立执行） |
| --- | --- |
| `python -m pytest -q tests/test_v2_rulings.py` | **8 passed**（1.19s） |
| `python -m pytest -q`（全量） | **192 passed, 6 warnings in 25.70s** |

证据：`raw/full-pytest-final.txt`。**全仓单测 0 失败**（Round 2 起点为 14 failed / 170 passed）。

§5.1 指出的三条"未被验证的新代码"已由 `backend/tests/test_v2_rulings.py`（8 例）覆盖，
并经"临时破坏 → 对应用例失败 → 哈希校验还原"的非空转验证
（还原后 `executor.py` / `connection.py` 的 SHA256 与破坏前逐字节一致，`git diff --stat` 与基线相同）。
**该文件是本轮唯一新增文件，未修改任何既有受跟踪文件。**

产出清单（本会话）：

| 文件 | 状态 |
| --- | --- |
| `docs/test-report-v2-p1-round2/REPORT.md` + `raw/` + `scripts/` | 新增（本轮报告与全部原始证据） |
| `docs/protocol.md` | 重写为 v2（810 行） |
| `docs/protocol-v2-design.md` | §4/§6.1/§6.2/§6.3/§8/§11 修订 + 状态行 |
| `docs/reply-to-pos-v2-confirmation.md` | D1/D2/D3 + D0 定稿 |
| `docs/CHANGELOG.md` | 补 P1 / P1c-1 / P1c-1r / P1c-2 + F4 共 4 段 |
| `backend/tests/test_runner_timeout.py`、`test_v12_fixes.py` | 迁 v2 |
| `backend/tests/test_v2_rulings.py` | 新增 8 例（D1/D2/入站 v） |
| `docs/test-report-v2-p1/`（Round 1） | 前一轮产出，未动 |

**仍未提交**：以上全部改动（含本会话前后的未提交项）都在工作区，尚未 `git commit`；
`AGENTS.md` 的 `M` 状态非本会话所为，提交时需与本轮改动分开。

## 13. 追加（goal round）：前端 v2 展示 + README 同步 + 新发现 F5

### 13.1 前端 v2 展示（按设计 §11 Q8 的"先最小可用"：`out.ops.status` + APDU）

- `frontend/src/types.ts`：新增 `OutOps` / `CaseRawResult` / `TraceMessage` / `TraceEntry`；
  `RunDetail` 补 `trace`（此前**类型里没有** `trace`，尽管后端早已返回它）。
- `frontend/src/pages/HistoryPage.tsx`：运行详情表新增 **`out.ops`**（`status [status_bits]`）与
  **`resp_code`** 两列；新增 **APDU trace** 折叠区（按 `COMMAND`/`RESPONSE`、`SW`、`elapsed_ms` 渲染）；
  页脚 `v1.2：` 标签更正为 `v2：`（该停止策略在 v2 仍然成立）。
- **落点说明**：`out.ops.status` 与 trace 只存在于**已落盘的结果记录**（`results/<case_id>.json`）
  与 `runs/<run_id>/trace/`，经 `/api/history/{run_id}` 暴露；实时 `SessionInfo.results` 只有
  status/attempts/failure_reason，**不含 `raw_result`**。故最小可用落在 **HistoryPage**；
  RunPage 实时显示 OPS 需要改后端 session 载荷（属 Q8 的"一步到位"，**未做，待裁决**）。

**验证（无 React 测试框架，故用"构建 + 真实服务契约"两项）**：

| 手段 | 结果 |
| --- | --- |
| `cd frontend; npm run build`（`tsc -b && vite build`） | **成功**，41 modules，`dist/assets/index-DIcIZby1.js` 207.54 kB |
| `frontend/dist` 时间戳 | 更新为 2026-09-18 16:52（原为 2026-09-15 11:36 的陈旧包） |
| 起真实 host + `GET /api/history/{run_id}`（合成 run：FAIL/`out_no_outcome` + 2 条 trace） | `results[...].raw_result.out.ops.status = "RFU(0000)"`、`status_bits="0000"`、`resp_code=""`、`trace[...]` 2 条（COMMAND/`3250…`、RESPONSE/SW=9000）——与 UI 渲染字段逐个对应；`GET /` → 200（新 dist 已被服务） |

> 局限：**未做视觉/交互验证**（仓库无前端测试框架，也无法在此环境开浏览器），
> 故"能构建 + 数据契约实测吻合"是本次能达到的最强证据，不代表 UI 观感已验收。

### 13.2 新发现 F5（**阻断级、且是既有缺陷**）：前端在 HEAD 上根本构建不通过

`npm run build` 在本会话之前即失败，共 **2 个独立缺陷**，均已修（否则 §13.1 无法交付）：

| # | 缺陷 | 证据 | 修复 |
| --- | --- | --- | --- |
| F5a | `AddCasePage.tsx:1171` JSX **文本**里写了裸 `<=`（`模数长度 <= 256 字节`），JSX 文本中的 `<` 必须转义 → `TS1003: Identifier expected` | `git show HEAD:frontend/src/pages/AddCasePage.tsx` 同行为 `<=`；`git status` 显示该文件本会话前未被修改 | 改为 `&lt;=`（渲染结果不变） |
| F5b | `AddCasePage.tsx:602/604/608` 引用未定义标识符 `sch`（`emptyDraft(sch)` / `emptyGroupDraft(sch)` / `applyTemplate(sch)`）→ `TS2304`。函数签名是 `(brand: string)`，是 `366f95c`（`scheme`→`brand`）**改名未改全**的残留 | 同上，HEAD 即如此 | 三处 `sch` → `brand` |

判定：**既有缺陷，非本会话引入**（两处都在 HEAD 的未修改文件里）。影响：**`frontend/dist` 长期是 2026-09-15 的陈旧构建**，任何人跑 `npm run build` 都会失败——即此前所有前端改动都未能真正发布。建议**单独提交**（"fix(web): unblock frontend build"），与本轮 v2 改动分开。

**同类残留（未改，仅报告）**：`AddCasePage.tsx:583/585/587`（`applyTemplate(sch: string)` 形参）与 `:635`（`const sch = brand;`）仍是旧命名，能编译但属 `366f95c` 改名的遗漏（Fowler: Mysterious Name）。另 `AddCasePage.tsx:290/1071` 仍在编辑/展示 legacy `pos_params`（v2 的 wire 数据是 `transaction.act`）——同上属 Q8 待裁决范围。

### 13.3 README 同步为 v2（已完成）

`README.md` 34 插入 / 28 删除：横幅（`v1.2`→`v2 硬切换`、信封 `v=2`、握手 `"2.0"`）、拓扑图
（`result 回传`→`out 回传`）、编排模型（`config_push{pos_config}/pos_kernel_config`+`transaction_result`
→ `test_info → config → act → out`，判定落盘后 ack）、用例 JSON 示例（`pos_params`→`act`、
`tags.gen_ac.9F26`→`out.datarecord.9F26`）、运行语义（失败原因码改 v2；
**删除了"`session_abort` 双方必 ack"这一在 v2 已不成立的承诺**）、mock 说明（`transaction_result`→`out`）。
残余 `v1.2` 字样经逐条审计**均为历史/否定语境**（如"已删除""legacy"），无一处把 v1.2 当作现行行为。

**未做的文档**：`docs/POS机应用开发手册.md` 未检查，可能仍是 v1.2（需单独核）。
