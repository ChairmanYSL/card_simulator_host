# 测试情况报告：协议 v2 P1（host CTEL2 核心）验证

> 依据文档：`docs/test-plan-v2-p1.md`
> 被测仓库：`E:\Work\ZT\card_simulator_host`，HEAD = `ffa28f2`（P1c-1）；开工前工作区已有未提交改动 `AGENTS.md`（本轮未触碰）
> 执行时间：2026-09-18 10:05–10:24（本地）
> 执行方式：**普通终端**（非 DSH 沙箱、非只读约束），按文档 §1/§2 原样执行

## 0. 环境与性质（显式标注）

| 项 | 值 |
| --- | --- |
| OS / Shell | Windows，pwsh |
| Python | 3.14.5 |
| pytest | 9.1.1（pluggy 1.6.0，anyio 4.14.2，asyncio 1.4.0，`asyncio_mode=auto`） |
| rootdir / configfile | `E:\Work\ZT\card_simulator_host\backend` / `pytest.ini` |
| 设备 | **无**。未启动 host、未连卡端、未连 POS |
| 性质 | **纯单元/集成测试（进程内）**，无真机、无 mock 联调脚本 |

本报告所有结论以落盘的 pytest 原始输出与探针原始输出为准（见 §6 证据清单），不引用消息叙述。

## 1. 结论摘要

**与文档预期不符。** 文档 §1 声称"应全绿"的聚焦子集出现 **5 个失败**；其中 **1 个是真实代码缺陷（阻断级）**、**1 个是测试断言与既有设计不符**、**2 个是测试夹具缺陷（服务端无问题）**、**1 个是上一条的同类夹具缺陷**。文档 §2 预测的"仅 2 个 v1.2 残留文件失败"与实测一致（14 个失败全部收敛到同一根因）。

| 轮次 | 命令 | collected | 结果 | 文档预期 | 判定 |
| --- | --- | --- | --- | --- | --- |
| §1 聚焦子集 | `python -m pytest -q --ignore=tests/test_runner_timeout.py --ignore=tests/test_v12_fixes.py` | 154 | **149 passed / 5 failed / 0 error**（592.06s） | 0 failed / 0 error | ❌ 不符 |
| §2 全量 | `python -m pytest -q` | 183 | **164 passed / 19 failed / 0 error**（597.36s） | 仅 `test_runner_timeout.py` + `test_v12_fixes.py` 失败 | ⚠️ 失败集合符合，但含 §1 的 5 个额外失败 |

### 失败构成（19 = 5 + 14）

| 分类 | 数量 | 文件 | 性质 |
| --- | --- | --- | --- |
| **F1** `tools/gen_cases.py` 语法错误 | 2 | `test_export_kernel_params.py` | **真实代码缺陷（阻断级）** |
| **F2** quarantine 落盘断言与实现不符 | 1 | `test_runner_result.py` | **测试断言缺陷**（实现与 v1.2 逐字一致） |
| **F3** 真实子进程 stdout 管道未排空 → 子进程阻塞 | 2 | `test_launch.py`、`test_shutdown.py` | **测试夹具缺陷**（服务端 1.8s 正常启动） |
| **F4** v1.2 残留（P1c-2 待迁移） | 14 | `test_runner_timeout.py`(8)、`test_v12_fixes.py`(6) | 与文档预测一致；根因收敛为同一处 |

## 2. F1 — `tools/gen_cases.py` 语法错误（真实缺陷，阻断级）

**现象**：`test_export_kernel_params.py` 的 2 个用例在 `load_gen_cases()` 动态导入时直接抛 `SyntaxError`，用例根本没进入被测逻辑。

```
E  File "E:\Work\ZT\card_simulator_host\tools\gen_cases.py", line 277
E    def embedded_profile(brand: dict, brand: str, case_id: str, title: str,
E                      ^^^^^^^^^^
E  SyntaxError: duplicate argument 'brand' in function definition
```

**根因（已由 git 取证）**：重命名提交 `366f95c`（`scheme` → `brand`）把**第一个形参**也一并改名，与原本就叫 `brand` 的第二个形参撞名：

| 提交 | 签名 |
| --- | --- |
| `574b568`（重命名前） | `def embedded_profile(scheme: dict, brand: str, ...)` |
| `366f95c`（重命名后） | `def embedded_profile(brand: dict, brand: str, ...)` ← 重复形参 |

函数体内两者语义确实不同（`brand.get("aid")` 是 dict，`brand.upper()` 是 str），9 处调用点均为 `embedded_profile(brand, brand, ...)`，即 `(dict, str)` 的实参传递本身是对的——**只是形参名撞了**。

**影响面**：
- `tools/gen_cases.py` **整个文件不可导入/执行**，任何 `python tools/gen_cases.py` 调用直接失败（属 P1c-2 之外的独立缺陷，与 v2 协议无关）；
- `test_export_kernel_params.py` 2 个用例失败（该文件 11 个用例中未调用 `load_gen_cases` 的 9 个仍通过）。

**建议修复（待裁决）**：把第一个形参改为语义明确的名字（如 `brand_conf`），同步函数体内 `brand.get(...)` / `brand["..."]` 引用；或把第二个形参改为 `brand_name`（正文 9 处调用点无需改动）。**命名方案需用户确认后再动手。**

## 3. F2 — `test_unattributed_out_is_quarantined` 断言与实现不符（测试缺陷）

**现象**：`tests/test_runner_result.py:228`

```
E  assert 1 == 2
E   +  where 1 = len(['{"at": ..., "reason": "case_id mismatch", ... "msg_id": "q-2" ...}'])
```

**根因**：实现刻意只对"有 session"的隔离事件落盘（无 run 则无 run 目录可写）：

```python
# backend/emv_host/runner/executor.py:498-503
entry = {"at": time.time(), "reason": reason, "frame": frame}
if session is not None:
    self.store.write_quarantine(session.run, entry)   # ← 无 session 时不写盘
await self.log("warn", f"quarantined transaction result ({reason}): ...")
await self.publish("quarantine", entry)
```

**关键取证：这不是本轮回归。** `_quarantine` 在 v1.2（`a3c3791`）与 P1（`13195f6`）**逐字相同**（`git show` 对比一致），"无 session 不落盘"是既有设计。

**引入过程**：P1c-1（`ffa28f2`）把 v1.2 的两个用例合并为一个：
- 旧 `test_stale_and_foreign_and_missing_attempt_are_quarantined`：有 session，断言落盘 **3 行**；
- 旧 `test_result_with_no_active_run_is_quarantined`：无 session，**只断言 ack detail，不断言落盘**；
- 合并后写成 `== 2`，把"无 session 不落盘"的那一条也算成了 1 行。

**建议修复（二选一，待裁决）**：
- (a) **推荐**：断言改为 `== 1`，并保留"无 session 时 `conn.last["detail"] == "quarantined"`"的 ack 断言（与既有设计一致，测试缺陷就地修正）；
- (b) 若产品上要求"无活跃 run 的迟到 OUT 也落盘"，需先确定落盘位置（如全局隔离目录而非 run 目录）——这属**产品行为变更**，不应在测试迁移里顺手改。

## 4. F3 — 真实子进程用例：stdout 管道未排空导致子进程阻塞（测试夹具缺陷）

**失败用例**：
- `tests/test_launch.py::test_serve_from_backend_dir_uses_repo_defaults` → `Failed: server did not start (default path resolution broken?)`
- `tests/test_shutdown.py::test_real_process_ctrl_c_exit_clean` → `AssertionError: server did not start in time`

**注意**：两者报的是"启动失败"，但**服务端本身没有启动问题**——同一命令行在 stdout 指向文件或管道被读取时 **1.8s 即起服务**。

**复现与隔离（三次对照，命令与 cwd 与失败用例一致）**：

| 变体 | stdout 接线 | ok | 耗时 | 启动到 `Uvicorn running` 的日志字节数 |
| --- | --- | --- | --- | --- |
| drained | `PIPE` + 读取线程 | **True** | 1.8s | 29032 |
| file | 文件 | **True** | 1.8s | 29032–29033 |
| **pipe** | **`PIPE` 不读取（= 失败用例）** | **False** | 145.0s（轮询耗尽） | **0（从未到达）** |

**根因链（已证实）**：
1. 子进程启动期日志量 = **约 29.0 KB**，全部发生在 `Uvicorn running`（即 web 端口开始监听）**之前**；
2. 用例用 `stdout=subprocess.PIPE` 但**在健康轮询结束前从不 `read()`**；
3. Windows 匿名管道默认缓冲约 **4 KB**（4096）→ 29 KB ≈ 缓冲的 7 倍；
4. 子进程在写 stdout 日志时**阻塞**，尚未 bind 端口，`/api/health` 永远连不上；
5. 首次 pipe 变体运行捕获到子进程 stdout 尾部停在 `brand amex: loaded named POS config CONF_24 ...` 处（正卡在装载日志中间），与该链路一致。

**触发条件（为何此刻才暴露）**：`cases/amex/pos_configs/` 下有 **87 个 `CONF_*.json`**，每个在 INFO 级各打一行 `brand amex: loaded named POS config ...`（由 `e82e944` / `a94e40c` 引入 POS 配置库），使启动日志从不足 4 KB 涨到 29 KB，越过管道缓冲。

**判定**：**测试夹具缺陷，非服务端缺陷，与 v2 协议无关**。子进程存活且未早退（`died=None`），也印证不是默认路径解析坏了（文档注释的猜测不成立）。

**附带成本**：`test_shutdown` 该用例内部 3 次重试 × 约 24s，两轮各多耗约 70–75s（这也是聚焦子集耗时 592s、全量 597s 的主因）。

**建议修复（待裁决）**：
- 用例侧：`stdout=subprocess.DEVNULL`（该用例只判退出码/`bye`），或起读取线程持续排空，或先 `communicate()` 再判；
- 产品侧（可选）：把启动期 per-config 的 INFO 降为 DEBUG，属日志噪音治理，**需用户确认是否纳入 P1c-2**。

## 5. F4 — 14 个已知 v1.2 残留（与文档预测一致，根因收敛为同一处）

### 5.1 清单（`文件::用例名` → 实际报错行）

`tests/test_runner_timeout.py`（14 个用例中 8 失败）：

| # | 用例 | 断言行 | 末行错误 |
| --- | --- | --- | --- |
| 1 | `test_result_timeout_fails_case_stops_run_and_skips_remainder` | :183 | `'test_info_ack_timeout'` |
| 2 | `test_pos_offline_fails_case_instead_of_warning` | :247 | `'test_info_ack_timeout'` |
| 3 | `test_pos_config_rejection_fails_case` | :261 | `'test_info_ack_timeout'` |
| 4 | `test_case_started_rejection_fails_case` | :273 | `'test_info_ack_timeout'` |
| 5 | `test_pos_case_started_ack_timeout_fails_case` | :290 | `'test_info_ack_timeout'` |
| 6 | `test_kernel_config_is_pushed_before_pos_config` | :361 | `IndexError: list index out of range` |
| 7 | `test_kernel_config_rejection_fails_case` | :385 | `'test_info_ack_timeout'` |
| 8 | `test_case_level_kernel_params_override_brand` | :412 | `RuntimeError: coroutine raised StopIteration` |

`tests/test_v12_fixes.py`（15 个用例中 6 失败）：

| # | 用例 | 断言行 | 末行错误 |
| --- | --- | --- | --- |
| 9 | `test_f2_assertion_failure_abort_reason_carries_case_id` | :244 | `'test_info_ack_timeout'` |
| 10 | `test_f2_passing_case_is_not_aborted` | :264 | `'ABORTED'` |
| 11 | `test_f2_every_failure_path_names_the_case` | :290 | `'case_failed:VISA-CL-001:test_info_ack_timeout'` |
| 12 | `test_f3_all_writers_emit_attempt_and_attempts` | :307 | `'FAIL'` |
| 13 | `test_h2_early_failure_still_parks_the_card` | :377 | `'test_info_ack_timeout'` |
| 14 | `test_h2_kernel_rejection_still_parks_the_card` | :392 | `'test_info_ack_timeout'` |

### 5.2 根因收敛（14/14 同一根因）

两个测试文件里的 `StubProtocol`（`test_runner_timeout.py:102`、`test_v12_fixes.py:92`）**未实现 v2 新增的 `send_to_and_ack_signal`**；真实实现存在于 `backend/emv_host/protocol/server.py:148`，v2 运行环第一步 `test_info` 在 `backend/emv_host/runner/executor.py:858` 调用它：

```
ERROR emv_host.runner:executor.py:137 [VISA-CL-001] test_info ack failed:
      'StubProtocol' object has no attribute 'send_to_and_ack_signal'
```

链路：run_loop 第一步 `test_info` 抛 `AttributeError` → 用例在第一阶段即终止 → 记录 `failure_reason="test_info_ack_timeout"`；后续用例因"什么都没推过/没有结果"而表现为 `IndexError`（空列表取 `[0]`，`test_runner_timeout.py:361`）或 `StopIteration`（`next()` 空生成器，:412）等**同一根因的次生现象**。

旁证：`_on_transaction_result` 已被删除（现存方法仅 `_on_android_status/_on_trace/_on_pos_msg/_on_out`），而 `test_v12_fixes.py:185` 仍在调用它——即便补上 stub 方法，该文件仍需按 v2 重写。

**判定**：**与文档 §2 预测一致，属 P1c-2 待迁移项，不是本轮回归。** 但从报告要求"确认都是 v1.2 残留"角度，需补一句偏差：文档把机制描述为"等待 `case_started` / `transaction_result` / `pos_config` 等 v1.2 事件"，而**实测的一阶原因是 stub 缺 v2 API**，事件移除只是二阶背景（P1c-2 迁移时应以补齐 stub 为入口，否则会误判迁移范围）。

### 5.3 顺带发现（低风险，标注为推断）

`executor.py` 把 `AttributeError` 这类程序错误也归为 `test_info_ack_timeout`（"超时"这一命名暗示的是对端未应答，而非本地异常）。建议 P1c-2 在 reason 上区分"对端拒绝 / 未应答超时 / 本地异常"，避免后续排障误判。**此为本次观测后的推断，不是已验证的产品缺陷。**

## 6. 文档一致性与覆盖度核查

| 文档 §1 覆盖声明 | 实测核查 |
| --- | --- |
| 握手 `protocol_version="2.0"` | ✅ 有断言：`test_protocol_server.py:64/157`（含拒绝 1.0 / 1.1 / 缺字段，接受 2.1），10 个用例通过 |
| **协议信封 `v=2`** | ❌ **无任何断言**（见下） |
| CTEL2 OUT：判定+落盘+ack+msg_id 去重+quarantine | ✅ `test_runner_result.py` 6 个用例中 5 通过（1 个即 F2） |
| 用例断言 `out.datarecord.*` 迁移 | ✅ `test_loader_store.py`(38)、`test_api_cases.py`(31)、`test_asserter.py`(7) 全通过 |
| 其余既有单测（帧格式/导出/DF05/shutdown/run_loop_crash） | ⚠️ 除 `test_export_kernel_params.py` 2 个(F1)、`test_shutdown.py` 1 个(F3) 外通过 |

**"信封 `v=2`"覆盖声明未落实（证据）**：
- `backend/emv_host/protocol/messages.py:22` 定义 `PROTOCOL_VERSION = 2`，仅在第 104 行由 `make_frame` 写入帧；
- 全 `tests/` 目录**没有任何**断言检查帧的 `v` 字段（`PROTOCOL_VERSION` 在 tests 中出现 0 次）；
- 全 `emv_host/` 目录**没有任何**读取/校验信封 `v` 的代码（`PROTOCOL_VERSION` 除 messages.py 外 0 引用，也无 `frame.get("v")`）。

即：信封 `v=2` 目前既无接收侧校验、也无测试断言，**回归不会被测出**。这不构成失败项，但文档 §1 的该项覆盖声明应视为未实现。

**其他核查**：
- `PytestConfigWarning: No files were found in testpaths` 为**无害告警**：rootdir/configfile 已确认是 `backend` / `pytest.ini`，两轮均完整收集 154 / 183 个用例，与逐文件用例数之和一致（见 §6 证据），无漏测。
- 文档 §4 明确不在范围内的项（卡端 APDU 判定 P2、前端 v2 展示 P1c-2、`docs/protocol.md` v2 重写 P1c-2、Mastercard POS 配置 + CAPK P4）**本轮均未测试**，符合文档边界。

### 逐文件用例数（用于核对收集完整性）

`test_api_cases 31 / test_asserter 7 / test_df05_encoding 28 / test_export_kernel_params 11 / test_frames 6 / test_launch 7 / test_loader_store 38 / test_protocol_server 10 / test_run_loop_crash 1 / test_runner_result 6 / test_runner_timeout 14 / test_shutdown 9 / test_v12_fixes 15` = **183**

## 7. 证据清单

| 文件 | 内容 |
| --- | --- |
| `raw/focused-subset-pytest.txt` | §1 聚焦子集完整原始输出（含 5 个失败的全部 traceback），`5 failed, 149 passed in 592.06s` |
| `raw/full-pytest.txt` | §2 全量完整原始输出（含 19 个失败的全部 traceback），`19 failed, 164 passed in 597.36s` |
| `raw/failure-classification.txt` | 脚本对 19 个失败块的逐条根因提取（用例名 / 报错行 / 末行错误） |
| `raw/probe-undrained-pipe.txt` | F3 对照实验：`PIPE` 不读取 → 145.0s 未起服务 |
| `raw/probe-drained-pipe.txt` | F3 对照实验：`PIPE` + 读取线程 → 1.8s 起服务，启动日志 29032 字节 |
| `raw/probe-stdout-to-file.txt` | F3 对照实验：stdout → 文件 → 1.8s 起服务 |
| `scripts/p1_stdio_probe.py` | 上述对照实验脚本（与人工分析分文件，按 AGENTS.md §7 约定） |

## 8. 下一步（**本轮结论：不动代码，仅按本报告推进**）

> **本轮裁决（2026-09-18）**：用户选择"先不动代码，只按此报告推进"。本轮**未修改任何被测代码与测试代码**，
> 仅新增本报告目录（`docs/test-report-v2-p1/`）。下列 1–5 项为**待执行方案**，已记录不再询问的部分如下。

按文档 §3.4，§1 子集的失败"才是真正要马上修的"。但三者性质不同，修复动作也不同：

1. **F1（待修）**：`tools/gen_cases.py:277` 形参改名 —— **命名已裁定：第一个形参改为 `brand_conf`**
   （函数体内 dict 引用与 9 处 `embedded_profile(brand, brand, ...)` 调用点均无需改动）；
2. **F2（建议立即修，测试侧一行）**：`test_runner_result.py:228` 断言 `== 2` 改 `== 1`（保留既有设计的 ack 断言）；
3. **F3（建议立即修，测试夹具）**：两个用例的子进程 stdout 由 `PIPE` 改为 `DEVNULL`/加读取线程；是否顺带把启动期 per-config INFO 降级为 DEBUG，请一并裁决；
4. **F4（按 P1c-2 计划）**：`test_runner_timeout.py` / `test_v12_fixes.py` 的 `StubProtocol` 补 `send_to_and_ack_signal` 并按 v2 协议帧重写（含 `test_v12_fixes.py:185` 对已删除 `_on_transaction_result` 的调用）；
5. **文档修订建议**：`docs/test-plan-v2-p1.md` §1 撤回"协议信封 `v=2`"覆盖声明，或补一条 `v` 字段断言后保留。

> AGENTS.md §7.14：本轮为无设备单测环境，所有结论仅代表本机 pytest 结果，**不代表任何设备侧落地状态**，报告中不含"已修复/已实施"表述。
