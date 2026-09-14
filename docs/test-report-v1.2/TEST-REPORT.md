# EMV Test Host v1.2 — 完整测试情况报告

| 项 | 内容 |
|---|---|
| 报告性质 | **自包含完整版**（整合 `REPORT.md` + `DEFECTS.md` + `EVIDENCE.md`，可单独交付评审） |
| 被测方案 | `docs/test-plan-v1.2.md`（编写日期 2026-09-11） |
| 被测对象 | 本仓库 `E:\Work\ZT\card_simulator_host` 已完成的 v1.2 **P0+P1** 改动（T1–T6、T8–T13，T19 的 mock 部分） |
| 测试范围 | L1 静态检查、L2 单元测试、L3 本地三进程（mock）、L4 故障注入、T-D0 内核参数；L5/L6 记为阻塞 |
| 执行日期 | 2026-09-14 |
| 执行环境 | Windows；Python 3.14.5（`C:\Python314`）；pytest 9.1.1；pydantic 2.12.5；fastapi 0.141.1；uvicorn 0.52.4；websockets 15.0.1；httpx 0.28.1 |
| 仓库状态 | **无任何 commit**（`git rev-list --count HEAD` 失败，`git status` 全为 `??`）→ 无 git 基线，回退依赖目录快照 |
| 一言结论 | **26 个场景 / 187 项断言全部通过；无 P0、无 P1。** 记录仓库内缺陷 3 条（P2×2、P3×1）与方案文档缺陷 7 条；L5/L6 待两端升级后另行开跑。 |

---

## 0. 结论摘要

### 0.1 通过情况

| 级别 | 名称 | 用例数 | 通过 | 失败 | 未执行 |
|---|---|---|---|---|---|
| L1 | 静态检查 | 4 | **4** | 0 | 0 |
| L2 | 单元测试（130 用例 + §3.2 红线 22 条 + §3.4 边界 7 项） | 159 | **159** | 0 | 0 |
| L3 | 本地三进程（mock）A–K + M + §4.2 硬切换 | 13 | **13** | 0 | 0 |
| L4 | 故障注入 §5.1–5.6 | 9 | **9** | 0 | 0 |
| T-D0 | 内核参数（§6.1–6.5） | 5 | **5** | 0 | 0 |
| **小计** | | **190** | **190** | **0** | **0** |
| L5 | 真机联调 | — | 0 | 0 | **全部（阻塞）** |
| L6 | 跨案例残留 | — | 0 | 0 | **全部（阻塞）** |

断言级统计（机器可读汇总见 `evidence/L3_summary.json`，`all_passed: true`）：

| 分组 | 场景数 | 断言数 |
|---|---|---|
| L3（A–K、M、§4.2 硬切换） | 13 | 107 |
| T-D0 §6.5（反向 + 三个失败变体） | 4 | 22 |
| L4（§5.1–5.6） | 9 | 58 |
| **合计** | **26** | **187** |

### 0.2 缺陷总览

| 级别 | 数量 | 编号 |
|---|---|---|
| **P0**（数据损坏/崩溃/静默误判） | **0** | — |
| **P1**（语义与方案不符） | **0** | — |
| **P2**（可观测性/字段缺失） | **2** | F1（mock ack 自锁）、F2（`abort_reason` 缺案例号） |
| **P3**（文案/日志/文档） | **1** | F3（`attempt`/`attempts` schema 分裂） |
| 方案（文档）缺陷/偏差 | 7 | P-D1 … P-D7 |

### 0.3 三条核心语义的实测结论（本轮最关键验收点）

| 方案语义 | 实测 | 证据 |
|---|---|---|
| FAIL 即整轮立即停止，剩余案例落 SKIPPED | **成立**。VISA-CL-001 FAIL → run `ABORTED`；002/003 为 `SKIPPED` 且 `note=run_stopped_after_failure:VISA-CL-001`；POS 端仅 1 次 `case_started` | `evidence/L3_H_fail_then_skipped.json` |
| `transaction_result` 先落盘后 ack + 重传去重 | **成立**。首次 ack `detail=persisted`；同 `msg_id` 重传 ack `detail=duplicate`；host 日志 `duplicate transaction_result ignored`；`quarantine.jsonl` 不存在 | `evidence/L3_L4_2_duplicate_result.json` |
| `pos_kernel_config` 早于 `pos_config` 下发 | **成立**。POS 日志 `pos_kernel_config:` 出现在 `pos_config applied:` 之前（字节偏移 275 < 441） | `evidence/L3_A_single_pass.json` |

### 0.4 「v1.1 行为差异」三条的实测确认

| 项 | v1.1 | v1.2 实测 |
|---|---|---|
| POS 离线 | 仅 warn | **案例 FAIL + `pos_not_connected`**，run ABORTED |
| 卡端断线 | — | **案例 FAIL + `android_disconnected`** |
| POS 断线 | 仅 warn | **案例 FAIL + `pos_disconnected`** |

---

## 1. 测试对象与范围

### 1.1 本轮应测（方案 §0.1）

协议层升到 1.2（硬切换：握手、坏帧、ack 超时、`session_abort`）；POS 内核参数下发通路（`pos_kernel_config`：AID/CAPK/TERM）及其校验；run loop 的 FAIL 语义（任一阶段 ack 超时/拒绝/断线 → 该案例 FAIL → 整轮立即停止，剩余落 SKIPPED）；`transaction_result` 的「先落盘后 ack」（D2）与重传去重；原子落盘、加载器容错、写路径防逃逸、Web `kind=group`。

### 1.2 本轮明确不测（方案 §0.1，本轮结论不覆盖）

* **P2 全部**：`tools/export_kernel_params.py`（已核实**不存在**）、`tools/gen_cases.py` 的 kernel_params 生成、前端 T14–T18、`tools/joint_debug` 的 T20；
* **P3 全部**：`docs/protocol.md` / `README` / `CHANGELOG` / 对端通知文档的 1.1→1.2 更新。

> 因此：文档仍写 v1.1、前端仍用 `kind=selected` 跑组，**均属预期，不作为缺陷上报**。

### 1.3 测试分级与依赖

| 级别 | 依赖 | 本轮状态 |
|---|---|---|
| L1 / L2 | 仅本仓库 + Python | 已完成 |
| T-D0 | 本仓库 + POS 资产**只读** | 已完成 |
| L3 / L4 | 本仓库 + 三个本地进程（mock） | 已完成 |
| L5 / L6 | KS8226 POS 真机 + Android 卡端 + adb | **阻塞**（见 §11） |

---

## 2. 执行前的基线与授权边界

### 2.1 基线核对（直接观测）

| 项 | 结果 |
|---|---|
| git 基线 | `git rev-list --count HEAD` **失败**（无 commit）→ 与方案 §0.2 一致，**无 git 回退基线** |
| 陈旧单实例锁 | `runs\.host.lock` = `{"pid": 60656, "ts": 1788943878.13}`，pid 60656 **已死** |
| 端口 | 8000 / 9100 执行前**空闲** |
| 依赖 | pytest / pydantic / fastapi / uvicorn / websockets / httpx 均可导入 |
| 案例库 | 7 个 scheme、**0 error、0 warning**（见 §4.4） |
| `cases/visa/kernel_params.json` | **不存在**（T-D0 需生成） |

**关于陈旧锁的处理（与方案 §4.1 的差异）**：方案要求手工 `Remove-Item runs\.host.lock`。实际**未手工删**，而是交给 `appconfig.py:151-164` 的 `acquire_lock()` 自动处理——它对「记录 pid 已死」的锁会打 warning 并替换。首轮 host 启动日志实测出现：

```
removing stale lock from dead pid 60656
```

随后锁被写成新 pid，进程退出时由 `cli.py:265-269` 的 `finally` 正常解除。**每轮场景结束后 `Test-Path runs\.host.lock` 均为 `False`**（实测）。此路径另有单元测试覆盖：`test_launch.py::test_stale_lock_from_dead_pid_self_heals`、`test_launch.py::test_startup_failure_removes_lock`，二者均 PASSED。

### 2.2 已获授权范围（严格限定，未越界）

执行前一次性申请并获用户授权 5 项：

| 授权项 | 批准范围 | 实际用途 |
|---|---|---|
| 目录快照 | 写 `E:\Work\ZT\_snapshot\card_simulator_host_20260914\`，**仅源码+cases+docs** | 已生成 **131 文件 / 6.78 MB**（排除 `logs/`、`runs/`、`tmp/`、`frontend/node_modules`、`frontend/dist`、`.git`、`__pycache__`、`.pytest_cache`） |
| POS 资产 | **只读** `E:\Work\ZT\KS8226\L2-SELFTEST-APP\app\src\main\assets\emv\params` | T-D0 唯一数据来源；**POS 仓库零写入** |
| 报告位置 | `docs/test-report-v1.2/`（报告 + 证据分文件） | 见 §12 |
| §5.3 注入法 | 目录占位法 | 见 §8.3 |
| `kernel_params.json` | **仅 visa** | 见 §6 |

执行中途另获一次授权：清理执行者自身泄漏的测试进程（详见 §10.3）。

### 2.3 边界遵守的**可验证**证明

测试结束后，用**执行前的快照**对当前工作树做 SHA256 逐文件比对：

```
snapshot files compared : 131
MODIFIED since snapshot : 0
MISSING (deleted)       : 0
```

即：**未修改、未删除任何既有文件**（含源码、测试、`cases/` 生产案例、`tools/` 下的 mock、文档）。
本轮**新增**的文件仅两类：

| 新增 | 数量 | 说明 |
|---|---|---|
| `cases/visa/kernel_params.json` | 1 | T-D0 产物；**gitignored**（见 §6.4）；含 CAPK 模数（密钥材料），已确认未外发 |
| `docs/test-report-v1.2/**` | 166 | 本报告 + 证据 + 脚本 |

（另 `backend/logs_tmp/host.log`、`backend/logs_tmp2/host.log` 为 2026-09-02 的 0 字节旧文件，`frontend/dist/*` 为 2026-09-07 旧产物——均在快照排除列表内，**非本轮产生**。）

**未 git commit**（遵循方案 §0.2）。

---

## 3. 总体结果矩阵

| 编号 | 测试项 | 方案条目 | 通过/总数 | 判定 |
|---|---|---|---|---|
| L1-1 | 字节编译 | §2 | 1/1 | PASS |
| L1-2 | 导入与常量装配 | §2 | 1/1 | PASS |
| L1-3 | CLI 装配 | §2 | 1/1 | PASS |
| L1-4 | 案例库冷加载 | §2 | 1/1 | PASS |
| L2-1 | 全套单元测试 | §3.1/§3.3 | 130/130 | PASS |
| L2-2 | 回归红线 | §3.2 | 22/22 | PASS |
| L2-3 | 边界断言 U-1…U-7 | §3.4 | 7/7 | PASS |
| T-D0-1 | 资产读取 | §6.1 | 1/1 | PASS |
| T-D0-2 | 生成 `kernel_params.json` | §6.2 | 1/1 | PASS |
| T-D0-3 | 校验生成物 | §6.3 | 1/1 | PASS（含 P-D2 差异） |
| T-D0-4 | 确认不提交 | §6.4 | 1/1 | PASS |
| T-D0-5 | 下发路径端到端 | §6.5 | 4/4 | PASS（含 P-D1 差异） |
| L3-A | 单案例 PASS + §6.5 正向 | §4.3 | 27/27 | PASS |
| L3-B | 离线拒付 | §4.4 | 4/4 | PASS |
| L3-C | 联机批准 | §4.5 | 2/2 | PASS |
| L3-D | 断言不符 → FAIL 且停轮 | §4.6 | 8/8 | PASS（含 F2 记录） |
| L3-E1 | 卡端未连接 | §4.7 | 4/4 | PASS |
| L3-E2 | `card_config` ack 超时 | §4.7 | 5/5 | PASS |
| L3-F | POS 离线即 FAIL | §4.8 | 6/6 | PASS（含 P-D3 记录） |
| L3-G | 结果超时 | §4.9 | 8/8 | PASS |
| L3-H | 停轮后剩余 SKIPPED | §4.10 | 12/12 | PASS |
| L3-I | `kind=group` | §4.11 | 5/5 | PASS |
| L3-J | 手动 abort | §4.12 | 9/9 | PASS |
| L3-K | 正常关闭不发 `session_abort` | §4.13 | 3/3（含于 A） | PASS |
| L3-M | POS mock ack 取证（新增） | — | 6/6 | PASS → 缺陷 F1 |
| L3-HS | 协议 1.2 硬切换反证 | §4.2 | 11/11 | PASS |
| L4-1 | 坏帧注入 | §5.1 | 16/16 | PASS |
| L4-2 | 结果重传去重 | §5.2 | 6/6 | PASS |
| L4-2b | 去重窗口边界（新增） | §5.2 补充 | 5/5 | PASS（记录 P-D4） |
| L4-3a | 落盘失败 → 等待至超时 | §5.3 | 7/7 | PASS |
| L4-3b | 落盘失败后重传 → PASS | §5.3 | 7/7 | PASS |
| L4-4a | 卡端断线 | §5.4 | 5/5 | PASS |
| L4-4b | POS 断线 | §5.4 | 5/5 | PASS |
| L4-5 | 加载器容错与重复 id | §5.5 | 5/5 | PASS |
| L4-6 | 原子写无残留 | §5.6 | 2/2 | PASS |

**合计：190 个用例项全部 PASS，0 失败、0 未执行（L5/L6 除外）。**

---

## 4. L1 静态检查（4/4）

### 4.1 L1-1 字节编译

命令（cwd = `backend`）：对方案列出的 12 个模块执行 `python -m py_compile`。

**实际：退出码 0，无任何输出。** → PASS
证据：`evidence/L1-1_py_compile.log`

### 4.2 L1-2 导入与常量装配

| 命令 | 期望 | 实际 |
|---|---|---|
| `import emv_host, emv_host.web.api, emv_host.runner.executor, emv_host.cases.loader` | `import ok` | `import ok`，exit 0 |
| `from emv_host.protocol.messages import PROTOCOL_VERSION_STRING as v, MSG_SESSION_ABORT as s, CONFIG_TYPE_POS_KERNEL as k` | `1.2 session_abort pos_kernel_config` | **逐字一致**，exit 0 |
| `from emv_host.appconfig import AppConfig; c=AppConfig()` | `15.0 15.0 3.0 3.0` | **逐字一致**，exit 0 |

→ PASS　证据：`evidence/L1-2_imports.log`

### 4.3 L1-3 CLI 装配

| 命令（cwd = 仓库根） | 期望 | 实际 |
|---|---|---|
| `python -m emv_host --help` | exit 0；含 `--card-ack-timeout` 与 `--pos-ack-timeout`（默认 15）；**不出现** `Uvicorn running` | exit 0；两开关均在，帮助文本为 `seconds to wait for a card-side ack (default 15)` / `... (default 15)`；`Uvicorn running` 出现次数 **0** |
| `python -m emv_host --version` | `0.1.0` | `0.1.0` |
| `python -m emv_host run --help` | 列出 `--scheme/--mode/--all/--case/--resume` | 五个开关齐备 |
| `python -m emv_host status`（host 未起） | **非零退出**，报 `cannot reach server at 127.0.0.1:8000` | exit **1**，报 `cannot reach server at 127.0.0.1:8000: <urlopen error [WinError 10061] ...>`（属正常行为，非缺陷） |

> 补充说明：仓库根的 `emv_host/` 是零安装 shim（`emv_host/__init__.py`，把 `backend` 插到 `sys.path` 首位后重导入真包），因此上述命令在**仓库根**直接可用，无需 `PYTHONPATH`。这与方案 §L1-3 的描述一致。

→ PASS　证据：`evidence/L1-3_help.txt`、`L1-3_version.txt`、`L1-3_run_help.txt`、`L1-3_status_no_host.txt`

### 4.4 L1-4 案例库冷加载

```
schemes: ['amex', 'discover', 'jcb', 'mastercard', 'pure', 'unionpay', 'visa']
errors: []
visa cases: ['OPBD-001-01', 'OPBD-001-02', 'OPBD-001-03', 'OPBD-001-04',
             'VISA-CL-001', 'VISA-CL-002', 'VISA-CL-003', 'VISA-CL-004']
```

* scheme 数 **7**，与期望一致；
* **errors 为空**，与期望一致；
* visa/contactless 含 `VISA-CL-001..004` 与 `OPBD-001-*`（4 个子案例），与期望一致；
* **加强验证（超出方案要求）**：全库逐 scheme / 逐 mode 展开，并把 issues **全表**（不只 error）打印出来 →
  `amex 1`、`discover 1`、`jcb 1`、`mastercard 4`、`pure 1`、`unionpay 12`、`visa 8` 共 **28** 例，**issues 全表为空（0 error / 0 warning）**。

→ PASS　证据：`evidence/L1-4_load_library.log`、`L1-4_all_schemes.log`

---

## 5. L2 单元测试（130/130 + 22/22 + 7/7）

### 5.1 全套结果（方案 §3.1 / §3.3）

```
cd backend && python -m pytest -v
collecting ... collected 130 items
============================== warnings summary ===============================
...
====================== 130 passed, 4 warnings in 19.31s =======================
PYTEST_EXIT=0
```

| 项 | 方案标准 | 实际 |
|---|---|---|
| 通过数 | ≥ 100 | **130** |
| failed | 0 | **0** |
| error | 0 | **0** |
| rootdir / configfile | — | `...\backend` / `pytest.ini` |

→ PASS。**注意：方案 §0.3 描述的两类环境现象本轮均未出现**（见 §5.4）。

### 5.2 回归红线逐条核对（方案 §3.2，22/22）

22 条红线**全部出现在日志中、且全部 PASSED**，`missing from log: 0`、`present but not PASSED: 0`。

| # | 用例 | 断言要点 | 结果 |
|---|---|---|---|
| 1 | `test_protocol_server.py::test_handshake_auth_and_different_device_rejected` | 握手 ack 的 `protocol_version == "1.2"` | PASSED |
| 2 | `::test_v11_protocol_version_rejected` | 广告 1.1 → `error{code:"protocol_mismatch"}` 且**不注册**该 client | PASSED |
| 3 | `::test_missing_protocol_version_rejected` | 不带 `protocol_version` → 同样被拒（v1.2 不再宽容） | PASSED |
| 4 | `::test_newer_protocol_version_accepted` | 广告 1.3 → 接受 | PASSED |
| 5 | `::test_bad_frames_are_skipped_until_five` | 第 1 个坏帧后仍能处理后续正常帧；第 5 个坏帧才断连 | PASSED |
| 6 | `::test_close_fails_pending_ack_with_connection_closed` | 连接关闭时 pending ack 抛 `ConnectionClosed`（非 `CancelledError`） | PASSED |
| 7 | `test_runner_result.py::test_result_is_persisted_before_ack` | ack 到达时 `results/<case>.json` 已存在，detail=`persisted` | PASSED |
| 8 | `::test_persist_failure_acks_ok_false_and_does_not_resolve_waiter` | 落盘异常 → `ok=false` + `detail=persist_failed`，waiter 未 resolve | PASSED |
| 9 | `::test_duplicate_msg_id_is_acked_duplicate` | 同 `msg_id` 重传 → detail=`duplicate`，`quarantine.jsonl` **不存在** | PASSED |
| 10 | `test_runner_timeout.py::test_result_timeout_fails_case_stops_run_and_skips_remainder` | `failure_reason=result_timeout`；run `ABORTED` + `abort_reason=case_failed:C-01:result_timeout`；后续 `SKIPPED` + `note=run_stopped_after_failure:C-01`；两端各一次 `session_abort` | PASSED |
| 11 | `::test_pos_offline_fails_case_instead_of_warning` | POS 离线 → `FAIL` + `pos_not_connected`（不再是 warn） | PASSED |
| 12 | `::test_android_error_keeps_status_error_and_stops_run` | `status:error` → 案例 ERROR，`results[...].android_error == {code,message}` | PASSED |
| 13 | `::test_kernel_config_is_pushed_before_pos_config` | `pos_kernel_config` 在 `pos_config` **之前**；帧内 `kernel_params.aid[0]` 以 `9F06` 开头 | PASSED |
| 14 | `::test_case_level_kernel_params_override_scheme` | 案例级 `kernel_params` 完全覆盖 scheme 默认 | PASSED |
| 15 | `test_loader_store.py::test_duplicate_case_id_rejected_and_first_kept` | 同号案例记 error；保留**独立文件**（`cases/<ID>.json`）那一份 | PASSED |
| 16 | `::test_case_id_colliding_with_group_id_is_an_error` | `case_id` 与 `group_id` 互斥，报「互斥」 | PASSED |
| 17 | `::test_atomic_write_leaves_no_tmp_and_reads_back` | 无 `*.tmp` 残留；覆盖后内容完整 | PASSED |
| 18 | `::test_local_kernel_params_are_loaded` | `cases/<scheme>/kernel_params.json` 被加载且 0 error | PASSED |
| 19 | `test_api_cases.py::test_write_path_guard_refuses_escape` | `../` 路径 → HTTP 400 +「用例库之外」 | PASSED |
| 20 | `::test_group_payload_cannot_carry_kernel_params` | 总案例带 `kernel_params` → 校验失败 | PASSED |
| 21 | `::test_websocket_since_garbage_is_tolerated` | `/ws?since=abc` → 仍先收到 `kind=snapshot`，`seq=0` | PASSED |
| 22 | `test_launch.py::test_pid_alive_true_for_self_false_for_dead_pid` | 本进程 pid 存活；死 pid 与 0/-1 为 False | PASSED |

→ PASS　证据：`evidence/L2_redlines.txt`（逐条判定）、`evidence/L2_pytest_full.log`（原始输出）

### 5.3 边界断言 U-1…U-7（方案 §3.4，7/7）

用一次性脚本确认（结论同时落 `evidence/L2_U1_U7.json`）：

| 编号 | 断言 | 实测（直接观测） | 判定 |
|---|---|---|---|
| **U-1** | `ber_length` 短/长形式 | `0x7F=b'\x7f'`、`0x80=b'\x81\x80'`、`248=b'\x81\xf8'` | PASS |
| **U-2** | 248 字节模数的 CAPK 编码后能被 `decode_tlv_map` 原样解回 | `DF02` 长度 = 248 B、值 identical=True；解回 tags 恰为 `9F06/9F22/DF02/DF03/DF04/DF05/DF06/DF07` | PASS |
| **U-3** | 缺 `checksum` 时 `encode_capk` 自动算出 DF03 | `DF03=FDBBB121294034B5D899A25854FB15547CDA274E`，与 `SHA1(9F06‖9F22‖DF02‖DF04)` **逐字相同** | PASS |
| **U-4** | `DF05="PHWWIPQI"` 在**模型层**即被拒（`ValueError`），不进入后续校验 | 抛 `ValueError`（`1 validation error for CapkParam`）。根因：`models.py:146-151` 要求非空时必须 8 位数字 | PASS |
| **U-5** | 9F06 为 9 字节 → 模型层拒绝；7/8 字节 → 接受 | 9 B rejected=True；7 B / 8 B accepted=True。规则见 `models.py:112`（`(?:[0-9A-F]{2}){5,8}`） | PASS |
| **U-6** | `validate_kernel_params(None, x)` 无任何 issue | `issues=[]` | PASS |
| **U-7** | 空 `KernelParams().empty is True`，因此**不会**产生 `pos_kernel_config` 帧 | `empty=True`；行为侧印证：`executor.py:738` 为 `if kernel is not None and not kernel.empty:` → 空集不下发 | PASS |

→ PASS　证据：`evidence/L2_U1_U7.log`、`L2_U1_U7.json`

### 5.4 关于方案 §0.3 的环境限制（**本轮未出现**）

方案 §0.3 声明两类「非被测代码缺陷」的环境现象。本轮实测：

| 现象 | 方案预期 | 本轮实测 |
|---|---|---|
| DSH 沙箱挡住 pytest 临时目录 → `WinError 5` / `PermissionError`，`25 passed, 55 errors` | 需普通 shell 或 `--basetemp` | **`WinError 5` 0 次、`PermissionError` 0 次**；未使用 `--basetemp`；**130 passed, 0 error** |
| `CTRL_BREAK_EVENT` 不可用 → `test_launch.py::test_serve_from_backend_dir_uses_repo_defaults` 与 `test_shutdown.py` 子进程用例失败 | 无控制台时会失败 | 二者均 **PASSED**；且 harness 用 `CTRL_BREAK_EVENT` 停止 host，退出码 **0**、日志 `bye`、锁被解除 |

**归因（推断，非直接观测）**：本会话文件策略为 `danger-full-access`，且运行于有控制台的终端，因此 §0.3 描述的前提不成立。
**结论**：§0.3 应视为**条件性**免责（仅在受限沙箱/无控制台时适用）。记入 §9 的 P-D6。

### 5.5 4 条 warning 的定性（均非功能性）

| warning | 内容 | 定性 |
|---|---|---|
| `PytestConfigWarning` | `No files were found in testpaths; ... Searching recursively from the current directory instead`（`pytest.ini` 有 `testpaths=["tests"]`，但依然从 `tests/` 收集到 130 项，exit 0） | 纯提示，不影响收集结果 |
| `StarletteDeprecationWarning` | `Using httpx with starlette.testclient is deprecated; install httpx2 instead` | 第三方依赖弃用提示 |
| `PytestCollectionWarning` ×2 | `cannot collect test class 'TestCase'`（`emv_host/cases/models.py:407` 的业务模型被 pytest 误判为测试类，来自 `test_runner_result.py` / `test_runner_timeout.py`） | 命名碰撞提示，无功能影响 |

---

## 6. T-D0 内核参数数据准备与校验（§6.1–6.5，5/5）

### 6.1 §6.1 资产读取（只读）

**注意一个方案表述偏差**：`params` 是**单个文件**（17,828 字节），不是目录。

| 项 | 方案声明 | 实测（直接观测） |
|---|---|---|
| AID | 19 条 | **19 条**；`9F06` 均在首；AID 长度集合 `{5,6,7,8}` 字节 |
| CAPK | 29 条 | **29 条**；RID 均 5 B、`DF06=01`、`DF07=01`、`DF04=03`；模数长度集合 `{128,144,176,248}`（恰好等于 `MODULUS_LENGTHS`） |
| TERM | 1 条 | **1 条**：`9F6604360000809F1A0201569F7A0101` |
| `DF03 = SHA1(9F06‖9F22‖DF02‖DF04)` | 29/29 吻合 | **29/29 吻合**（用脚本**自带的独立实现**复算，未调用被测代码，避免自证） |

### 6.2 §6.2 生成 `cases/visa/kernel_params.json`

**方法说明**：方案 §6.2 要求执行者手工生成（P2 的 `tools/export_kernel_params.py` 未实现且**不测**）。本轮的生成脚本刻意放在 `docs/test-report-v1.2/scripts/build_kernel_params.py`，**不放 `tools/`**，以免与 P2 交付物混淆。该脚本使用**自带的独立 BER-TLV 走查器**，不依赖 `emv_host.protocol.kernel_tlv`。

生成日志：

```
asset size          : 17828 bytes
AID lines           : 19 -> written 19
CAPK lines          : 29 -> written 28 (skipped 1: ["A000000333/08 DF05='PHWWIPQI'"])
TERM lines          : 1
asset DF03 verify   : 29/29 match SHA1(9F06||9F22||DF02||DF04)
modulus byte lengths: [128, 144, 176, 248] (allowed [128, 144, 176, 248])
aid lengths (bytes) : [5, 6, 7, 8]
bare aids (no field): ['D9999999992020', 'D666666666', 'A0000002282010', 'A0000007231010']
term                : 9F6604360000809F1A0201569F7A0101
wrote               : cases\visa\kernel_params.json (35594 bytes)
```

**关键决策**：

* **`aid` 不含 `9F06` 标签，只有值**；`fields` 中不含 `9F06`（按 `encode_aid` 的约定）；
* **`expiry` 为 8 位数字 ASCII**（如 `20441231`），非 hex；
* **`checksum` 显式写入**（便于人工核对），并与独立复算一致；
* **唯一排除** `RID=A000000333 / 9F22=08`（其 `DF05="PHWWIPQI"`）——依据 §6.2 的明文要求，且 U-4 已证实模型层直接 `ValueError`；
* **4 条「只有 `9F06`、无其它元素」的 AID 原样保留**：模型层接受 5–8 字节 AID，它们只在 §6.3 校验时产生 warning，不影响 `errors: []`。

### 6.3 §6.3 校验生成的 JSON（**用被测代码自身校验**）

```
errors  : []
warnings: ['kernel_params.aid[15] D9999999992020 除 9F06 外没有任何元素',
           'kernel_params.aid[16] D666666666 除 9F06 外没有任何元素',
           'kernel_params.aid[17] A0000002282010 除 9F06 外没有任何元素',
           'kernel_params.aid[18] A0000007231010 除 9F06 外没有任何元素']
AID 19 CAPK 28 term True
empty   : False
payload aid[0] : 9F0607A0000000041010DF01 ...   aid[0] starts with 9F06: True
```

* `errors: []` → **符合**方案预期；
* 4 条 warning 均为「AID 无附加元素」，来自资产本身，**非缺陷**；
* **数量差异**：方案 §6.3 期望「AID/CAPK 数量与资产一致」，但按 §6.2 排除 PHWWIPQI 后为 **AID 19 / CAPK 28 / TERM 1**，资产为 29 条 CAPK。
  → 这是**方案 §6.2 与 §6.3 自相矛盾**，按 §6.2 执行。记入 §9 的 **P-D2**。

### 6.4 §6.4 确认不会被提交

```
git check-ignore -v cases/visa/kernel_params.json
  .gitignore:24:cases/*/kernel_params.json	cases/visa/kernel_params.json
```

**加强验证（必要，因方案原有检查在无 commit 仓库中是弱证据）**：
因仓库无任何 commit，`git status --porcelain` 只报目录级 `?? cases/`，**即使没有忽略规则也会「看起来通过」**。故补做：

```
git status --porcelain --untracked-files=all      → 1271 项
  其中 cases/visa/ 的兄弟文件（如 .gen_cases_manifest.json）均被列出
  而 cases/visa/kernel_params.json 未出现        → 忽略规则确实生效
```

**密钥材料外泄核查**：对 `docs/test-report-v1.2/evidence/` 全量（含 `proc/`）扫描 8 个模数前缀（`BC853E6B`/`B72A8FEF`/`ADF05CD4`/`BBE9066D`/`A191CB87`/`996AF56F`/`C8D5AC27`/`CF98DFED`）与 `DF0281` 模数 TLV → **全部 0 命中**。
该文件含 CAPK 模数（密钥材料），**未提交、未外发**。

### 6.5 §6.5 下发路径端到端验证（4 个变体全部完成）

| 变体 | 场景 | 方案期望 | 实际（直接观测） | 判定 |
|---|---|---|---|---|
| **正向** | `kernel_params.json` 在位，跑 VISA-CL-001 | POS 端出现 `pos_kernel_config: aid=19 capk=28 term=yes`；其 ack **早于** `pos_config` 的 ack；host 落盘无 `pos_kernel_config_*` 失败原因 | 全部满足：POS 日志逐字一致；字节偏移 `kernel_idx=275 < pos_config_idx=441`；host 日志 `pos_kernel_config applied (AID 19 条 / CAPK 28 条)`；无 `pos_kernel_config_ack_timeout`、无 `pos_kernel_config_apply_failed` | PASS |
| **反向** | 去掉 `kernel_params.json`（用**独立 cases 根**，不动 `cases/`） | POS 端**不应**出现 `pos_kernel_config`；案例仍应 PASS | POS 日志 0 次 `pos_kernel_config`；host 无 `loaded local kernel params`；案例 **PASS**、run `FINISHED` | PASS |
| **失败-a**（方案原文例） | `term` 改为 `9F660436000080` | 「奇数长度 hex」→ 案例应 `FAIL` + `pos_kernel_config_apply_failed` | **方案有误**：该值为 14 个 hex 字符 = 7 字节（**偶数**），且 `decode_tlv_map` 解得**合法** BER-TLV `{'9F66': '36000080'}` → host 校验**无 error**，案例 **PASS** | **P-D1** |
| **失败-b**（真·奇数长度） | `term` = `9F6604360000809`（15 字符） | 方案允许「先被 host 校验拦下、以 error 记入加载 issues」 | host 加载期记 error（`kernel_params.term 不是偶数长度 hex`）；案例**仍 PASS**——因 stock mock 无内核、对任何参数恒回 `ok=true` | PASS（记录实际分支） |
| **失败-c**（reject 变体） | 用回 `ok=false` 的 POS 端 | 案例 `FAIL` + `failure_reason == pos_kernel_config_apply_failed` | 案例 **FAIL**、`failure_reason=pos_kernel_config_apply_failed`、run `ABORTED`、`abort_reason=case_failed:VISA-CL-001:pos_kernel_config_apply_failed`；host 日志 `POS rejected pos_kernel_config`；**未下发 pos_config** | PASS |

**关于失败-b/c 的方法学说明**：方案 §6.5 要求验证「apply 失败」，但 **stock mock 恒回 `ok=true`，该路径在其上不可达**。为真正走到 `executor.py:760-763`，本轮由仓库 mock **文本变换**生成一个 `mock_pos_reject_kernel.py`（对 `pos_kernel_config` 回 `ok=false`，每处替换均断言唯一命中；**仓库原 mock 未被修改**）。这属于「补足方案未覆盖的验证路径」，不是改动被测代码。

---

## 7. L3 本地三进程（mock）

### 7.1 前置与启动

* 三个进程（host、`mock_android.py`、`mock_pos.py`）一律由 `scripts/l3_harness.py` 以**子进程**方式管理，stdout/stderr **重定向到文件**（**不使用管道**）。
* 启动顺序固定：host → android mock → POS mock。每个场景使用**独立 host 实例**（独立 `--log-dir`），互不污染。
* host 关键启动行（实测）：`EMV test host v0.1.0 starting: web http://127.0.0.1:8000, device TCP 0.0.0.0:9100, cases=...`
* 卡端握手（实测）：`[android] connected, session android-...`；host 侧 `handshake ok: android@mock-android (device=mock-android app=mock-0.1 protocol=1.2)`
* 结束时 host 以 `CTRL_BREAK` 优雅退出：退出码 **0**、日志 `bye`、`runs/.host.lock` 被解除。

### 7.2 §4.2 协议 1.2 硬切换反证（改为直接探测，**不修改仓库文件**）

方案建议「临时把 `mock_common.py` 的握手改成 `1.1`」。为**不改动任何仓库文件**，改用 `scripts/probe_handshake.py` 向 9100 端口发四种握手：

| 广告版本 | 响应（直接观测） | `session_id` | 结论 |
|---|---|---|---|
| `1.1` | `error`，`code=protocol_mismatch`，`message="protocol 1.1 not supported (need 1.2)"` | **None（未注册）** | 硬切换生效 |
| 不带 `protocol_version` | `error`，`protocol_mismatch`，`message="handshake must carry protocol_version=1.2"` | None | v1.2 不再宽容 |
| `1.2` | `handshake_ack`，回显 `protocol_version=1.2` | `android-...` | 接受 |
| `1.3` | `handshake_ack`，回显 `protocol_version=1.2` | `android-...` | 前向兼容（host 回自己的 1.2） |

host 日志对应出现：`handshake rejected from ...`（2 次）、`protocol 1.1 not supported`、`handshake must carry protocol_version=1.2`、`protocol=1.2`、`protocol=1.3`。

→ PASS（11/11 断言）　证据：`evidence/L3_handshake_hard_switch.json`

### 7.3 L3 用例逐条结果（13/13）

#### 用例 A — 单案例 PASS（§4.3）+ §6.5 正向（27/27）

| 顺序 | 观测点（方案 §4.3） | 实测 |
|---|---|---|
| 1 | 卡端收到 `card_config`，含 `trace=...` | `[android] card_config: profile=VISA-CL-001 acBehavior=offline_approved keys=no trace=True noResponseCommands=(none)` |
| 2 | 卡端 `status: ready` | 出现 |
| 3 | 卡端 `status: emulating`，随后 `trace: emitted 2 APDU pairs` | 均出现 |
| 4 | POS `pos_config applied: 4 tag 参数`（VISA-CL-001 有 4 条） | `pos_config applied: 4 tag 参数`（9F02/5F2A/9C/9F1B） |
| 5 | POS `case_started case=VISA-CL-001` | 出现 |
| 6 | POS `ack for transaction_result: ok=True detail=persisted` | **不可达**（缺陷 F1，见 §8.1）。替代证据：host `transaction result received for VISA-CL-001 attempt <id> -> pass` + 落盘 `status=PASS` |
| 7 | 卡端收到 `stop_emulation` 并 `status: stopped` | `[android] status: stopped` 出现 |
| 8 | host `[VISA-CL-001] final: PASS`；`run ... ended: FINISHED` | 均出现 |

落盘核对：`run.json` → `status=FINISHED`、`abort_reason=null`、`case= PASS`；
`trace/VISA-CL-001.jsonl` **存在且恰为 4 行**（2 对 APDU）；
**加强验证**：`results/VISA-CL-001.json` 的 `raw_result`、`verdict.rules` 四条规则全 true；
目录下**无** `session_abort` 相关产物。

> 方案 §4.3 的核对命令 `Select-Object case_id,status,attempts` 有一处文档问题：PASS 结果文件并无 `attempts` 字段（见缺陷 F3 / P-D5）。实测该字段为 `null`，实际字段是 `attempt: 0`。

→ PASS　证据：`evidence/L3_A_single_pass.json`

#### 用例 B — 离线拒付（§4.4，4/4）

POS mock 以 `--outcome declined_offline` 启动，跑期望 approved 的 **VISA-CL-002**（该案例期望 `resp_code=05 / declined / offline`）。

实测：案例 **PASS**、run `FINISHED`；`raw_result.resp_code=05`、`outcome=declined`、`online=offline`。
**该用例的价值**：证明 PASS/FAIL 判定确实按 `expected_results` 走，而不是「只要收到结果就算过」——同一 mock 输出在用例 D 中让 VISA-CL-001 判 FAIL，在本例中让 VISA-CL-002 判 PASS。

→ PASS　证据：`evidence/L3_B_declined_offline_pass.json`

#### 用例 C — 联机批准（§4.5，2/2）

`--outcome approved_online` 跑 **VISA-CL-003**：案例 **PASS**、run `FINISHED`；`raw_result.online=online`、`resp_code=00`。方案要求的「`online=online` 断言满足」成立。

→ PASS　证据：`evidence/L3_C_approved_online_pass.json`

#### 用例 D — 断言不符 → FAIL 且整轮停止（§4.6，8/8）

用 `declined_offline` 去跑期望 approved/offline 的 VISA-CL-001：

| 观测 | 方案期望 | 实测 |
|---|---|---|
| 案例 | FAIL | **FAIL** |
| `failure_reason` | `assertion_failed` | **`assertion_failed`** |
| run | ABORTED | **`ABORTED`** |
| `abort_reason` | 形如 `case_failed:VISA-CL-001:assertion_failed` | **`case_failed:assertion_failed`**（**缺案例号**）→ 见 F2 / P-D |
| 两端 `session_abort` | 各收到 | host `session_abort broadcast (reason=assertion_failed, case=VISA-CL-001)`；**卡端与 POS 端均**收到 `session_abort reason=assertion_failed` |

> 取证说明：POS mock 因 F1 在结果上报后会被阻塞 15 s，故本用例在读日志前**额外等待 17 s**，以确认 POS 端确实收到 `session_abort`（这是「异常路径必须通知两端」的关键证据，值得等待）。

→ PASS（含偏差记录）　证据：`evidence/L3_D_assertion_failed_stops_run.json`

#### 用例 E1 — 卡端未连接（§4.7 前半，4/4）

host 以 `--card-ack-timeout 2 --pos-ack-timeout 2` 启动，**不启动**卡端 mock，只启动 POS mock。

实测：案例 **FAIL**、`failure_reason=android_not_connected`、run `ABORTED`、`abort_reason=case_failed:VISA-CL-001:android_not_connected`。
方案 §4.7 判断「设备未连接优先于 ack 超时」→ **成立**（未退化为 ack 超时）。

→ PASS　证据：`evidence/L3_E1_android_not_connected.json`

#### 用例 E2 — `card_config` ack 超时（§4.7 后半，5/5）

用一个「连上但收到 `config_push` **不回 ack**」的副本 mock（由仓库 mock 文本变换生成：删掉 `on_message` 中 `config_push` 分支的 `send_ack`，替换唯一命中已断言；**仓库原 mock 未被修改**）。

实测：案例 **FAIL**、`failure_reason=card_config_ack_timeout`、run `ABORTED`、`abort_reason=case_failed:VISA-CL-001:card_config_ack_timeout`。
**方案反证成立**：卡端日志中 `start_emulation` **0 次** —— 即 ack 超时后 host **不会**继续要求卡端开始模拟。

→ PASS　证据：`evidence/L3_E2_card_config_ack_timeout.json`

#### 用例 F — POS 离线即 FAIL（§4.8，6/6）

只启动卡端 mock，**不启动** POS mock。

实测：案例 **FAIL**、`failure_reason=pos_not_connected`、run `ABORTED`、`abort_reason=case_failed:VISA-CL-001:pos_not_connected`。
**这是与 v1.1 行为差异最大的一条**：旧版此处只是 warn → 现已确认为 FAIL。

> **一处方案偏差**：方案 §4.8 要求「卡端收到 `stop_emulation`」。实测卡端**未**收到 `stop_emulation`，而是收到 `session_abort reason=pos_not_connected case=VISA-CL-001`。
> **根因（代码定位）**：`pos_not_connected` 在**内核参数下发步骤**即抛出（`executor.py:755-759`），此时尚未进入 `_wait_result()`；而向卡端发 `stop_emulation` 的 `_send_stop(CLIENT_ANDROID, ...)` **只在 `_wait_result()` 的 `finally` 中调用**（`executor.py:873-880`）。
> **定性**：卡端**已被通知停止**（`session_abort` 是 v1.2 的异常收尾通道），故**不是 host 缺陷**；属方案观测点描述不准。见 P-D3。

→ PASS（含偏差记录）　证据：`evidence/L3_F_pos_not_connected.json`

#### 用例 G — 结果超时（§4.9，8/8）

用独立的 `timeout_seconds=15` cases 根（**不改动 `cases/`**），POS mock 以 `--delay 9999` 启动（= 永远不在预算内上报结果）。

实测：案例 **FAIL**、`failure_reason=result_timeout`、run `ABORTED`、`abort_reason=case_failed:VISA-CL-001:result_timeout`；
**实测耗时 15.6 s**（与 15 s 案例预算吻合，含收尾开销）；
host `session_abort broadcast (reason=result_timeout, case=VISA-CL-001)`；卡端收到 `session_abort reason=result_timeout`。

> 方法学偏差：方案建议用 `--interactive`（等回车）制造「POS 不回结果」。该方式依赖控制台 stdin，在子进程化运行下不可靠（`input()` 可能立即 EOF 致 mock 异常退出），故改用等价的 `--delay 9999`。见 P-D7。
> POS 端侧 `session_abort` 不作断言：POS mock 此时停在 `on_message` 内的 `asyncio.sleep(9999)`，其 reader 无法消费该帧（F1 的同一根因），host 侧会打 `session_abort to pos not acked: ... ack timeout`。

→ PASS　证据：`evidence/L3_G_result_timeout.json`

#### 用例 H — 整轮停止后剩余案例为 SKIPPED（§4.10，12/12）

以 `--case VISA-CL-001 --case VISA-CL-002 --case VISA-CL-003` 开跑，POS mock 用 `declined_offline` 让第一个案例断言不符。

| 观测 | 方案期望 | 实测 |
|---|---|---|
| VISA-CL-001 | FAIL（断言不符） | **FAIL** |
| VISA-CL-002 / 003 | SKIPPED | **SKIPPED** |
| 其 `results/*.json` 的 `note` | `run_stopped_after_failure:VISA-CL-001` | **逐字一致**（两份） |
| run | ABORTED | **ABORTED** |
| `GET /api/history/<run_id>` 的 `results` | 三份齐全 | HTTP 200，**3 份齐全** |
| **反证**：VISA-CL-001 之后不应再出现 `case_started` | — | POS 端 `case_started  case=` **恰好 1 次**；`case_started  case=VISA-CL-002` **0 次** |

→ PASS　证据：`evidence/L3_H_fail_then_skipped.json`

#### 用例 I — `kind=group`（§4.11，5/5）

前端 T17 未实现（属预期），故仅用 API 直调。

| 检查 | 期望 | 实测 |
|---|---|---|
| `group_id='NOPE-001'` | HTTP 404 | **404** |
| 不带 `group_id` | HTTP 400 | **400** |
| `group_id='OPBD-001'` | 200 + `{ok:true, run_id}` | **200**，返回 `run_id` |
| 该 run 的 `queue` | **只含** OPBD-001 的子案例 | **恰为** `['OPBD-001-01','OPBD-001-02','OPBD-001-03','OPBD-001-04']` |

附带观测：该组内 `OPBD-001-01 = PASS`、`-02 = FAIL`、`-03/-04 = SKIPPED`（因为 `approved_offline` 与 -02 的期望不符，触发停轮）——这本身再次印证了 §4.10 的语义。

→ PASS　证据：`evidence/L3_I_kind_group.json`

#### 用例 J — 控制命令（§4.12，9/9）

在 `WAIT_RESULT` 窗口（POS mock `--delay 9999`）执行 `python -m emv_host abort`：

| 观测 | 方案期望 | 实测 |
|---|---|---|
| `abort` 返回 | 成功 | exit **0** |
| 案例状态 | `ABORTED`（**不是** FAIL） | **ABORTED** |
| `failure_reason` | **为空**（不得继承上一个案例的失败原因） | **为空**（`None`） |
| run | `aborted` | `ABORTED` |
| `abort_reason` | 走 `manual abort` 分支 | **`manual abort`** |
| 两端 `session_abort` | 仍收到，reason=`manual_abort` | host `session_abort broadcast (reason=manual_abort, case=VISA-CL-001)`；卡端收到 `session_abort reason=manual_abort` |

→ PASS　证据：`evidence/L3_J_manual_abort.json`

#### 用例 K — 正常关闭不发 `session_abort`（§4.13）

用例 A 正常跑完后，在**同一轮证据**中核对：

| 检查 | 期望 | 实测 |
|---|---|---|
| host 日志 `session_abort broadcast` | **不应出现** | **0 次** |
| POS 端 `session_abort` | 不应出现 | **0 次** |
| 卡端 `session_abort` | 不应出现 | **0 次** |
| 产物目录 | 不应有 `session_abort` 相关产物 | 无 |

这是「只在异常路径通知」的反向验证 → PASS。

#### 用例 M — POS mock ack 取证（新增，非方案条目，6/6）

为定位 §4.3 观测点 6 为何缺失而新增的探针用例。实测（等待超过 15 s ack 预算）：

```
[pos] ack timeout (15.0s) for transaction_result
[pos] WARNING: no ack for transaction_result (not persisted?)
[pos] transaction_result reported: resp=00 approved (offline)
```

**同一轮** host 侧证据表明结果**已落盘并已 ack**：`transaction result received for VISA-CL-001 attempt ... -> pass`、`results/VISA-CL-001.json` = `PASS`、run `FINISHED`。
→ 稳定复现缺陷 **F1**（详见 §8.1）→ PASS（取证成功）　证据：`evidence/L3_M_pos_ack_deadlock.json`

---

## 8. L4 故障注入（9/9）

### 8.1 §5.1 坏帧注入（16/16）

注入端先完成 1.2 握手，然后发 5 个「长度前缀合法、JSON 非法」的帧（`{not json`），**前 4 个各跟 1 个正常 `log` 帧**，第 5 个后预期被断连。

| 观测 | 期望 | 实测 |
|---|---|---|
| 第 1–4 个坏帧 | `skipping bad frame (n/5)`，且**后续正常帧仍被处理** | `skipping bad frame (1/5)` … `(4/5)` **各 1 次**；`[android] alive0`…`alive3` **各 1 次** |
| 第 5 个坏帧 | `5 bad frames, dropping`，连接断开 | host 日志 `bad frames, dropping`；注入端观测到 **EOF**（`connection_dropped=true`） |
| 全程 | host **不重启、不崩** | `/api/health` 仍 200；host 进程存活 |

规则来源：`protocol/frame.py:24` `MAX_BAD_FRAMES = 5`；`connection.py:181-186`（计数与丢弃）、`connection.py:206-214`（单帧跳过并 `continue`）；`server.py:82-83`（`ConnectionClosed` → `connection closed: ...`）。

→ PASS　证据：`evidence/L3_L4_1_bad_frames.json`、`evidence/proc/L4_1_bad_frames_host.log`

### 8.2 §5.2 `transaction_result` 重传（D2 去重，6/6）

> **方法学说明**：方案要求「复制一份 `mock_pos.py`，连续 `send` 两次同一帧」。因缺陷 **F1**，stock mock **无法观测到 ack**，该做法无法验证 `detail=duplicate`。故本轮自建 `scripts/raw_pos_dup.py`——**带独立 reader task 的 POS 客户端**，其读循环不被 `on_message` 阻塞，因而**能真正读到 ack 并报告 `detail`**。

单案例、重传间隔 0.5 s（重传落在**同一 attempt 窗口**内）：

| 观测 | 期望 | 实测 |
|---|---|---|
| 第一次 ack | `detail=persisted` | **`persisted`** |
| 第二次（同 `msg_id`）ack | `detail=duplicate` | **`duplicate`** |
| host 日志 | `duplicate transaction_result ignored` | 出现 1 次 |
| `results/VISA-CL-001.json` | 只写一次 | 单份 `PASS` |
| `quarantine.jsonl` | **不存在** | **不存在** |
| run | FINISHED | **FINISHED** |

→ PASS　证据：`evidence/L3_L4_2_duplicate_result.json`

### 8.2b §5.2 边界补充：去重窗口 = 单次 attempt（5/5，新增）

**为何要加这一条**：`Session.begin_attempt()` 会在**每个案例尝试开始时重置** `judged_msg_ids`（`session.py:61`），因此去重窗口 = 当前 attempt。为界定该窗口，另做一条：两案例运行，重传在**下一个案例已开始之后**才到达。

| 观测 | 方案 §5.2 的表述 | 实测 |
|---|---|---|
| 第一次 ack | `persisted` | **`persisted`** |
| 晚到重传 ack | 期望 `duplicate` | **`quarantined`** |
| host 日志 | 「不应有 quarantine」 | `quarantined transaction result (case_id mismatch)` |
| `quarantine.jsonl` | **不应存在** | **存在**（reason=`case_id mismatch`） |

**定性**：实现**自洽**——晚到结果本就该进隔离区（`quarantine.jsonl` 的设计目的即「晚到/无法归属的交易结果」）；是方案 §5.2 的绝对化表述不准确。见 **P-D4**。

→ PASS（记录边界）　证据：`evidence/L3_L4_2b_late_resend_is_quarantined.json`

### 8.3 §5.3 落盘失败（`persist_failed`，7/7 + 7/7）

> **注入方法（经用户事先确认的方案偏离）**：方案建议对 `runs\<run_id>\results\` 设 `attrib +R`。**在 Windows 上，目录的只读属性不会阻止在其中新建文件**，该方法很可能根本触发不了失败。故改用**同名目录占位法**：在 `results\` 下创建一个名为 `VISA-CL-001.json` 的**目录**，使 `RunStore._atomic_write_json`（`run_store.py:30-51`）的 `os.replace(tmp, path)` 失败；观测到失败后即移除该占位，使后续的超时记录能正常写入。该方法**可靠且可逆**。

#### L4_3a — 落盘失败 → run 继续等待至超时（7/7）

| 观测 | 方案期望 | 实测 |
|---|---|---|
| POS 收到 | `ok=false, detail=persist_failed` | host 侧 `_ack(..., ok=False, detail="persist_failed")`（`executor.py:454`）；POS 端受 F1 影响只观测到 `ack timeout`，故以 host 侧为准 |
| host 日志 | `结果落盘失败，未 ack 成功` | **逐字出现** |
| waiter 是否 resolve | **不应** resolve | host 日志中 `transaction result received for VISA-CL-001` **0 次** |
| run | 仍在等待，不判 PASS/FAIL | 解除占位前 run 保持 `RUNNING` |
| 之后 | 超时 → `result_timeout` | `failure_reason=result_timeout`、run `ABORTED` |

#### L4_3b — 解除阻塞后以**新 `msg_id`** 重传 → PASS（7/7）

同一案例、同一 attempt，由 `scripts/raw_pos_retry.py`（带独立 reader）按 `persist_failed` 自动重试。实测 ack 序列：

```json
"retry_attempts": [
  {"n": 1, "ok": false, "detail": "persist_failed"},
  {"n": 2, "ok": true,  "detail": "persisted"}
]
```

随后案例 **PASS**、run **FINISHED** —— 完整验证了方案 §5.3 的「释放后重传同一结果（新 `msg_id`）应能正常落盘并判 PASS」。

→ PASS　证据：`evidence/L3_L4_3a_persist_failed_then_timeout.json`、`L3_L4_3b_persist_failed_then_retransmit_passes.json`

### 8.4 §5.4 断线（5/5 + 5/5）

| 子项 | 观测 | 方案期望 | 实测 |
|---|---|---|---|
| **L4_4a 卡端断线** | 在 `WAIT_RESULT` 窗口切断卡端 mock | 案例 FAIL、`android_disconnected`、run ABORTED | **全部满足**，且 `abort_reason=case_failed:VISA-CL-001:android_disconnected` |
| **L4_4b POS 断线** | 同法切断 POS mock | 案例 FAIL、`pos_disconnected`（v1.1 只是 warn） | **全部满足**，`abort_reason=case_failed:VISA-CL-001:pos_disconnected` |

→ PASS　证据：`evidence/L3_L4_4a_android_disconnect.json`、`L3_L4_4b_pos_disconnect.json`

### 8.5 §5.5 坏文件与重复 id（加载器容错，5/5）

在**独立临时 cases 根**中做（不污染 `cases/`）：

| 检查 | 期望 | 实测 |
|---|---|---|
| 放入内容为 `{ not json` 的 `BROKEN-001.json` 后 `load_library` | **不抛异常** | 未抛异常 |
| 仍能正常列出 | 能列出 `VISA-CL-001` | 已列出 |
| `BROKEN-001` | 以 error 记入 issues | error 记录的 **path 字段**含 `BROKEN-001`（message 文本里不含文件名，故断言需查 path） |
| 同一 `case_id` 同时存在独立文件与组内副本 | 报「重复」error，且**保留独立文件那一份** | 报「重复」error；实际保留的标题为 `STANDALONE COPY WINS`（组内副本标题为 `GROUP COPY LOSES`，未被采用）——**加强验证**：用可区分的标题精确判定「保留哪一份」 |

→ PASS　证据：`evidence/L3_L4_5_loader_tolerance.json`

### 8.6 §5.6 原子写无残留（2/2）

在**本轮全部用例（含 §5.3 的落盘失败注入）之后**扫描：

| 检查 | 期望 | 实测 |
|---|---|---|
| `runs/` 下 `*.tmp` | **空** | **空**（`runs_tmp: []`） |
| 临时 cases 根下 `*.tmp` | — | **空**（`cases_tmp: []`） |

→ PASS　证据：`evidence/L3_L4_6_atomic_no_tmp.json`

---

## 9. 缺陷与偏差清单

### 9.1 被测仓库内缺陷（无 P0 / 无 P1）

#### F1 — **P2** — stock POS mock 永远读不到 host 的 ack（`on_message` 内等待 ack 阻塞同一 reader 循环）

**现象（直接观测）**：用仓库自带 `tools/mock_clients/mock_pos.py` 跑任一案例，POS 端在上报结果后必然出现

```
[pos] ack timeout (15.0s) for transaction_result
[pos] WARNING: no ack for transaction_result (not persisted?)
```

而同一轮 host 侧证据表明结果**已持久化并已 ack**（`results/<case>.json` = PASS、host `transaction result received ... -> pass`、run 正常收尾）。

**根因（代码定位）**

| 位置 | 内容 |
|---|---|
| `tools/mock_clients/mock_common.py:152` | `MockClient.run()` 读循环**顺序**执行 `await self.on_message(self, frame)` |
| `tools/mock_clients/mock_pos.py:142` | `on_message()` 末尾 `ack = await client.send_and_wait_ack(...)` |
| `tools/mock_clients/mock_common.py:106-128` | `send_and_wait_ack()` 注册 future 后 `await asyncio.wait_for(fut, timeout)` |

唯一能读 `MSG_ACK` 的代码是 `run()` 的读循环，而它此刻**正阻塞在 `on_message` 内部**等待该 ack → 自锁至 15 s 超时。
`mock_android.py` 不使用 `send_and_wait_ack`（一律 `send_ack` 即发即忘），故**只有 POS mock 受影响**。

**影响**

1. 方案 **§4.3 观测点 6**（`ack for transaction_result: ok=True detail=persisted`）**无法产生**；
2. 方案 **§5.2** 期望的 POS 侧「第二次 ack `detail=duplicate`」**无法观测**（host 侧 `duplicate transaction_result ignored` 仍可观测，本轮已用自建客户端补齐）；
3. D2「先落盘后 ack」在 POS 端侧**无法验证**；更关键的是该 warning 把「host 已落盘」与「host 根本没 ack」渲染成**同一种表现**，会**掩盖真实回归**；
4. 连带效应：任何耗时较长的 `on_message` 都会阻塞读循环，使上游帧（`session_abort`、`stop_emulation`）无法及时消费。已实测 `--delay 9999` 期间 POS 收不到 `session_abort`，host 打 `session_abort to pos not acked: ... ack timeout`。

**最小复现**

```powershell
cd E:\Work\ZT\card_simulator_host
python -m emv_host serve --trace-enabled                     # 终端 A
python tools\mock_clients\mock_android.py --tap-delay 0.5     # 终端 B
python tools\mock_clients\mock_pos.py --delay 1               # 终端 C
python -m emv_host run --scheme visa --mode contactless --case VISA-CL-001
# 观察：终端 C 在 case_started 约 1s 后打印 ack timeout (15.0s) + WARNING
# 对照：runs\<日期>\<run_id>\results\VISA-CL-001.json 已是 PASS
```

自动化复现：`scripts/l3_scenarios.py M_pos_ack_deadlock`（6/6 断言通过 = 缺陷稳定复现）。

**定级说明**：**host 自身行为正确**（先落盘、后 ack、去重、隔离均按预期），缺陷位于测试工具链 `tools/mock_clients/`。因它使方案**两处验收观测点不可达**，按 §8.2 归为**可观测性缺失 P2**；若将「方案记载的关键日志行必须出现」视为验收硬条件，可上调为 P1 —— **该定级请统筹裁决**。

**修复方向（仅建议，未实施）**：`MockClient` 将消息处理派发到独立 task（`asyncio.create_task(self.on_message(...))`）以保持读循环空闲；或把「等 ack」移到读循环之外（收发分离，更贴近真实客户端并发模型）。

#### F2 — **P2** — `run.abort_reason` 格式随失败路径不一致（断言失败路径缺案例号）

| 失败路径 | 实测 `run.json.abort_reason` | 含案例号 |
|---|---|---|
| **断言不符** | `case_failed:assertion_failed` | **否** |
| 卡端未连接 | `case_failed:VISA-CL-001:android_not_connected` | 是 |
| POS 未连接 | `case_failed:VISA-CL-001:pos_not_connected` | 是 |
| 结果超时 | `case_failed:VISA-CL-001:result_timeout` | 是 |
| 内核参数被拒 | `case_failed:VISA-CL-001:pos_kernel_config_apply_failed` | 是 |

方案 §4.6 记载的形式**含**案例号，与实测不符。

**根因**：`executor.py:181-183` 的 `_fail()` 会设置 `fatal_reason = f"case_failed:{case_id}:{reason}"`（含案例号）；`executor.py:553-558` 若 `fatal_reason` 已置则直接用它，否则走 `stopped_by_failure` 分支写 `f"case_failed:{stop_reason}"`（**不含**）。断言失败源自 `_judge` 的判定结果（`executor.py:857-859` 置 `failure_reason="assertion_failed"`），**从不经过 `_fail()`**，故落入后者。

**影响**：同一字段两种语义，消费方需分别处理；案例号仍可从 `results/<case>.json` 或其余案例的 `note=run_stopped_after_failure:<case_id>` 恢复，**非数据丢失** → P2。

**最小复现**：`scripts/l3_scenarios.py D_assertion_failed_stops_run`，与 `E1`/`F`/`G` 的 `abort_reason` 对比。
**修复方向（仅建议）**：让断言失败也经 `_fail()`，或在 `stopped_by_failure` 分支同样拼上 `session.current_case_id`（注意 §3.2 对应红线只覆盖 `result_timeout` 路径，不会因此变红）。

#### F3 — **P3** — `results/<case>.json` schema 随写入路径分裂（`attempt` vs `attempts`）

| 结果来源 | 实际字段 |
|---|---|
| 真实交易结果（PASS/WARN/FAIL），`executor.py:970` | `"attempt": <0 基整数>`，**无** `attempts` |
| 未收到结果的收尾（ABORTED/SKIPPED/ERROR），`executor.py:1011-1024` | `"attempts": <1 基计数>`，**无** `attempt` |

实测：VISA-CL-001 PASS 记录含 `"attempt": 0`；同轮手动 abort 的记录为 `{"status":"ABORTED","attempts":1,"note":"run aborted"}`。
方案 §4.3 的落盘核对命令读的正是 `attempts` → 对 PASS 案例**恒为空**（实测 `null`），易被误读为「字段缺失」。
另：`run["results"][case_id]` 内部索引恒用 `attempts`（`executor.py:987/1008`），三者不一致。

**影响**：纯可观测性/契约问题，不影响判定正确性 → P3。
**最小复现**：对比 `evidence/L3_A_single_pass.json`（PASS）与 `evidence/L3_J_manual_abort.json`（ABORTED）的 `extra.result`。
**修复方向（仅建议）**：统一两个字段并存，或只留一个并同步方案与前端读取处。

### 9.2 方案（文档）缺陷与偏差（7 条）

> 均为 `docs/test-plan-v1.2.md` 自身问题，**不是被测代码缺陷**，但会影响后续轮次验收判定，建议随本轮结论一并修订。

| 编号 | 方案位置 | 问题 | 建议 |
|---|---|---|---|
| **P-D1** | §6.5 失败验证 | 举例 `9F660436000080` 称为「奇数长度 hex」，实际 **14 字符（偶数）且是合法 TLV**（`{9F66:36000080}`）→ 按该例**不会**失败；且 stock mock 恒回 `ok=true`，`pos_kernel_config_apply_failed` 在其上**不可达** | 换成真正不可解析的值（奇数长度或非法 TLV）；并标注「apply 失败需 POS 端支持才可端到端验证」 |
| **P-D2** | §6.2 vs §6.3 | §6.2 要求排除 `PHWWIPQI` 那条 CAPK；§6.3 又要求「数量与资产一致」→ **互斥**（28 vs 29） | §6.3 改为「AID 19 / CAPK 28（资产 29 减去 1 条异常样例）/ TERM 1」并注明排除原因 |
| **P-D3** | §4.8 | 期望卡端收到 `stop_emulation`；实际收到 `session_abort reason=pos_not_connected`（该失败在 `WAIT_RESULT` 之前抛出，`_send_stop` 只在 `_wait_result` 的 finally 中调用） | 改为 `session_abort`，或区分「`WAIT_RESULT` 之前失败 → 仅 `session_abort`」与「之后失败 → `stop_emulation` + `session_abort`」 |
| **P-D4** | §5.2 | 「quarantine.jsonl 不存在」只对**同一 attempt 内**的重传成立；跨案例晚到重传会被隔离并写出 `quarantine.jsonl` | 限定为「同一 attempt 窗口内」，并补一条「跨案例晚到重传 → `quarantined` + 写 `quarantine.jsonl`」作为预期行为 |
| **P-D5** | §4.3 落盘核对 | 示例命令读 `attempts`，但 PASS 结果文件无该字段（见 F3） | 改为 `attempt`，或与 F3 的修复方向同步 |
| **P-D6** | §0.3 | 所述两类环境限制本轮**均未出现**（无 WinError 5、CTRL_BREAK 可用） | 明确写成「**受限沙箱/无控制台时**才出现」的条件性免责 |
| **P-D7** | §4.9 | 建议 `--interactive` 制造无结果；依赖控制台 stdin，子进程化运行不可靠 | 改述为「把 `--delay` 设为远大于 `timeout_seconds` 的值」 |

### 9.3 与方案不一致的执行方式（均已标注，非隐瞒）

| 方案要求 | 实际做法 | 理由 |
|---|---|---|
| §4.1 手工删 `runs/.host.lock` | 交给 `acquire_lock()` 自动清死锁 | 等价且更安全，日志留证；有单测覆盖 |
| §4.2 改 `mock_common.py` 为 1.1 | 独立握手探测脚本 | 避免改动仓库文件；覆盖 1.1/缺失/1.2/1.3 |
| §4.9 `--interactive` | `--delay 9999` | 不依赖控制台 stdin |
| §4.9 / §5.5 / §6.5 改动或临时放入 `cases/` | 一律用 `tmp/v12test/cases/<name>` 独立 cases 根 | **生产案例零改动** |
| §5.2 复制 `mock_pos.py` 发两次 | 自建带独立 reader 的客户端 | stock mock 因 F1 无法观测 ack |
| §5.3 `attrib +R` | 同名目录占位法 | Windows 下目录只读属性不阻止新建文件；经用户确认 |
| §0.2 全目录快照 | 源码+cases+docs 快照（131 文件 / 6.78 MB） | 经用户确认；排除 logs/依赖/tmp/runs |

---

## 10. 环境观察与执行过程记录

### 10.1 环境/工具观察（非缺陷）

| 项 | 观测 | 定性 |
|---|---|---|
| 对端被**强杀**时的日志噪声 | host 打 `ERROR emv_host.protocol \| unexpected connection error for ('127.0.0.1', <port>): [WinError 64] ...` 并附**完整 traceback**（`server.py:84-85` 的 `logger.exception`）；正常 `close` 则为 INFO `connection closed: ... peer closed connection`（`server.py:82-83`） | 本轮所有 traceback 均发生在用例**判定完成之后**的收尾阶段，不影响任何判定。建议后续降噪（把 `ConnectionResetError` 归入常规断连分支） |
| pytest 配置 warning | `PytestConfigWarning: No files were found in testpaths`，但 `testpaths=["tests"]` 下仍正常收集 130 项、exit 0 | 纯提示 |
| 控制台代码页乱码 | 部分由 `>` 重定向捕获的 `*.log` 中文为 GBK/UTF-8 混读所致 | 落盘 JSON 均显式 UTF-8，未见乱码 |

### 10.2 用户授权记录

执行前一次性申请并获授权 5 项（快照范围、POS 资产只读、报告位置、§5.3 注入法、`kernel_params.json` 仅 visa）；执行中途另经一次授权处理进程泄漏。**全部实际操作未超出上述范围**。

### 10.3 执行者自身问题的处置（透明记录，均已修复并复测）

1. **日志自追加死循环**：首轮 L1-3 使用 `Select-String -Path $log | Tee-Object -Append $log`，**读同一文件的同时把结果追加回去**，日志涨到 **76 MB** 并被 120 s 超时中断。
   → 已删除该文件，改为「命令输出 → 独立文件；grep → 控制台」，重跑通过。
2. **误建目录**：一条命令路径拼写错误创建了空的 `etmp/`；确认无任何文件后删除。
3. **进程泄漏（影响过一次判定，已纠正）**：`stop_all()` 中 `Proc.stop()` 对已停止进程二次调用抛 `ValueError`，**异常中断了停止循环**，导致 **2 个 host + 2 个 mock** 残留，占用 8000/9100 与单实例锁；后续用例因此被**上一个泄漏 host** 接管，产生了 `L4_2`、`L4_2b`、`L4_3b`、`L4_4b` 的**假失败**。
   → 经用户授权按 PID 精确清理（仅限本会话子进程；**未触碰**用户的 `openviking-server` 与两个 VS Code 语言服务器）。
   → harness 加固三项：`Proc.stop()` **幂等**；`stop_all()` **逐个 try/except**（单个失败不跳过其余）；每个用例开始前 `cleanup_stragglers()`，仅清理「命令行含本 harness 标记**且父进程已消失**」的孤儿进程。
   → 加固后重跑 L4 全套：**9/9 通过**；轮末实测**无任何残留进程、无 8000/9100 监听、无锁文件**。

> **重要澄清**：日志中早期出现的 L4 失败**是我的工具问题，不是被测代码问题**。上表 §3/§8 的数据均为**加固后重跑**的结果。

---

## 11. L5 / L6 阻塞状态

**未执行**，依据方案 §7.1 的硬性前置：v1.2 是硬切换，host 拒绝任何 `<1.2` 或缺失 `protocol_version` 的客户端（本轮已实测：1.1 与缺失版本均被拒且不注册）。两端尚未升级，故 L5 只会得到 `protocol_mismatch`，**不构成任何有效结论**。

**解除条件**：

| 端 | 必须完成 |
|---|---|
| 卡端（card_simulator） | 握手带 `protocol_version:"1.2"`；不再期待 `device_config`；从 `card_config.trace_enabled` 读 trace 开关；新增 `session_abort` 必 ack；心跳/判死/ack 超时改用缺省 10s/30s/15s；坏帧单帧跳过不拆链 |
| POS 端（L2-SELFTEST-APP） | 握手带 1.2；实现 `pos_kernel_config`（`updateAidParam` / `updateCAPKParam` / `setTerminalParam`）；`case_started` 必 ack；`transaction_result` 视为落盘确认；新增 `session_abort` 必 ack；运行时断线/超时一律按案例 FAIL 处理 |

**流程**：两端升级完成并各自发 `[READY]` → 过 §7.2 preflight（`devices.android.connected` 与 `devices.pos.connected` 均为 `true`，且两端 `app_version` 与期望版本一致）→ 由统筹发起开跑。**任一不满足不开跑**（沿用 AGENTS.md §3 轮次门控）。

**已核实的 §7.2 提示**：`/api/health` **不返回** `protocol_version`；判断 1.2 需看 host 日志 `handshake ok: ... protocol=1.2`（本轮已实测该行确实存在）。

**本轮已备好的可复用前置**：`cases/visa/kernel_params.json` 已就位（L5 下发内核参数所需）；§7.3/§7.4 涉及的案例（`VISA-CL-001`、`VISA-CL-002`、`UPI-CL-002`）在案例库中均在列。
**注意 §7.3 的提醒**：`tools/joint_debug/run_cases.py` 的 `--on-failure` 默认 `continue`，而 v1.2 **一律停止**；报告时不得以「因为 `on_failure=continue` 所以继续跑」作为归因。该工具的 T20 适配属 P2，本轮未做。

---

## 12. 需明确排除的「伪缺陷」（方案 §8.3 复核）

| 方案列的伪缺陷 | 本轮是否出现 | 说明 |
|---|---|---|
| pytest `WinError 5` | **未出现** | 本会话非受限沙箱；实测 0 次 |
| `CTRL_BREAK_EVENT` 相关失败 | **未出现** | 有控制台；CTRL_BREAK 停 host 退出码 0、`bye` 正常 |
| 终端中文乱码 | 部分 `*.log` 出现 | 已归为环境；落盘 JSON 均 UTF-8 |
| `runs/` 下空目录 / 残留 `pt_*` | **未产生** | 本轮未使用 `--basetemp` |
| 文档仍写 v1.1 | 未核查 | P3 未做，**属预期** |
| 前端仍用 `kind=selected` 跑组 | 未核查 | T17 未做，**属预期** |

---

## 13. 最终结论

1. **通过率**：L1 **4/4**；L2 **130/130**（含 §3.2 **22/22** 红线、§3.4 **7/7** 边界）；L3 **13/13**（A–K + M + §4.2 硬切换）；L4 **9/9**（§5.1–5.6）；T-D0 **5/5**（§6.1–6.5）。
   **合计 26 个场景、187 项断言全部通过；190 个用例项全部 PASS，0 失败、0 未执行（L5/L6 除外）。**

2. **P0/P1**：**无**。未发现数据损坏、进程崩溃、静默误判；方案列为 P1 的四种现象（「应 FAIL 却 PASS」「FAIL 后整轮未停」「`session_abort` 未发」「`pos_not_connected` 仍为 warn」）**均未出现，反而都被正向验证**。

3. **P2×2 / P3×1**：均为**可观测性与字段/schema 一致性**问题（F1 / F2 / F3）。其中 **F1 使方案两处验收观测点不可达**，且会把「已落盘」与「未 ack」渲染成同一表现、可能掩盖真实回归，**建议优先修**。

4. **方案需修订 7 处**（P-D1…P-D7）。其中 **P-D1**（§6.5 示例不是坏数据、失败路径在 stock mock 上不可达）与 **P-D2**（§6.2/§6.3 自相矛盾）会**直接影响后续轮次的验收判定**，建议随本轮结论一并更正。

5. **L5/L6 阻塞**：待两端升级至 1.2 并各自 `[READY]`；解除条件见 §11。

6. **本轮结论不覆盖 P2/P3 未实现部分**：`tools/export_kernel_params.py`（已确认不存在）、`tools/gen_cases.py` 的 kernel_params 生成、前端 T14–T18、`tools/joint_debug` 的 T20，以及 `docs/protocol.md` / `README` / `CHANGELOG` / 对端通知文档的 1.1→1.2 更新，**一律未测试、未评价**。

7. **纪律确认**：用执行前快照做 SHA256 逐文件比对（131 文件）→ **0 修改、0 删除**；未越界写 POS 仓库；未 git commit；轮末无残留进程/端口/锁。

---

## 14. 待统筹/用户裁决事项

1. **F1 的定级**：P2（可观测性缺失）还是 P1（方案记载的关键日志行不可达）？
2. **F1 是否在本轮修**：它属 `tools/mock_clients/`（测试工具链），不在方案 §0.1 列出的 host P0+P1 范围内；修与不修都会影响后续 L5 证据的可比性。
3. **F2 / F3 是否本轮修**：两者都涉及「字段语义」，一旦改字段会影响前端与 `tools/joint_debug` 的读取，建议与方案修订一起做。
4. **P-D1…P-D7 的修订归属**：由我落到 `docs/test-plan-v1.2.md`（或另出一份勘误），还是仅保留本清单由统筹处理？
5. **`cases/visa/kernel_params.json` 的去留**：含 CAPK 模数（密钥材料），已确认 gitignore 生效、未外发；L5 需要它。是否在你确认前保留，或以 `.bak` 暂存？

---

## 附录 A — 产物索引

| 路径 | 内容 |
|---|---|
| `docs/test-report-v1.2/TEST-REPORT.md` | **本文件**（自包含完整版） |
| `docs/test-report-v1.2/REPORT.md` | 分文件版主报告（结论与逐条判定） |
| `docs/test-report-v1.2/DEFECTS.md` | 分文件版缺陷/偏差清单（含最小复现） |
| `docs/test-report-v1.2/EVIDENCE.md` | 分文件版命令↔产物索引 |
| `docs/test-report-v1.2/evidence/` | **143 个原始产物，合计约 4.5 MB**（其中 `proc/` 93 个为该轮各进程的原始 stdout 与 host 结构化日志） |
| `docs/test-report-v1.2/scripts/` | **20 个脚本**（harness、场景、注入、专用客户端、校验） |
| `cases/visa/kernel_params.json` | T-D0 生成物，35,594 B，**gitignored**，含密钥材料 |
| `E:\Work\ZT\_snapshot\card_simulator_host_20260914\` | 执行前快照，131 文件 / 6.78 MB（用于 §2.3 的零修改证明与回退） |

关键证据文件速查：

| 证据 | 内容 |
|---|---|
| `evidence/L3_summary.json` | 26 场景 / 187 断言机器可读汇总（`all_passed: true`） |
| `evidence/L2_pytest_full.log` | L2 原始输出（130 passed / 0 failed / 0 error，19.31 s） |
| `evidence/L2_redlines.txt` | §3.2 22 条红线逐条判定 |
| `evidence/L2_U1_U7.json` | §3.4 边界断言结构化结论 |
| `evidence/L3_A_single_pass.json` | 用例 A + §6.5 正向（kernel 下发与顺序） |
| `evidence/L3_H_fail_then_skipped.json` | §4.10 FAIL→停轮→SKIPPED |
| `evidence/L3_M_pos_ack_deadlock.json` | **F1 缺陷取证** |
| `evidence/L3_L4_2_duplicate_result.json` | §5.2 去重（`persisted` → `duplicate`） |
| `evidence/L3_L4_3b_persist_failed_then_retransmit_passes.json` | §5.3 重传（`persist_failed` → `persisted`） |
| `evidence/L3_handshake_hard_switch.json` | §4.2 协议 1.2 硬切换四种探测 |
| `evidence/TD0_build_kernel_params.log` / `TD0_validate_kp.log` | T-D0 生成与校验 |
| `evidence/TD0_gitignore.log` | §6.4 忽略规则 + 强化验证 |

## 附录 B — 复现步骤

```powershell
# 0) 环境（建议 UTF-8，避免控制台代码页乱码）
$env:PYTHONIOENCODING='utf-8'
cd E:\Work\ZT\card_simulator_host

# 1) L1
cd backend
python -m py_compile emv_host\protocol\messages.py emv_host\protocol\connection.py `
  emv_host\protocol\kernel_tlv.py emv_host\cases\models.py emv_host\cases\validation.py `
  emv_host\cases\loader.py emv_host\store\run_store.py emv_host\appconfig.py emv_host\cli.py `
  emv_host\runner\session.py emv_host\runner\executor.py emv_host\web\api.py
python -m pytest -v
cd ..

# 2) L2 边界 + §3.2 红线
python docs\test-report-v1.2\scripts\l2_u1_u7.py
powershell -File docs\test-report-v1.2\scripts\check_redlines.ps1

# 3) T-D0 数据生成（需 POS 资产只读权限）
python docs\test-report-v1.2\scripts\build_kernel_params.py `
  "E:\Work\ZT\KS8226\L2-SELFTEST-APP\app\src\main\assets\emv\params" cases\visa\kernel_params.json
python docs\test-report-v1.2\scripts\td0_validate_kp.py

# 4) L3（含 §4.2 硬切换、§6.5 四个变体）
python docs\test-report-v1.2\scripts\l3_scenarios.py all
python docs\test-report-v1.2\scripts\probe_handshake.py

# 5) L4
python docs\test-report-v1.2\scripts\l4_scenarios.py all

# 6) 汇总回算
python docs\test-report-v1.2\scripts\rebuild_summary.py
```

> 复现前请确认 8000/9100 空闲且无残留 host。harness 每轮开始会自动清理**本 harness 自己**的孤儿进程，**不会**触碰其它工具的进程。

## 附录 C — 本轮未覆盖范围（明确声明）

* P2 全部：`tools/export_kernel_params.py`（不存在）、`tools/gen_cases.py` 的 kernel_params 生成、前端 T14–T18、`tools/joint_debug` 的 T20；
* P3 全部：`docs/protocol.md` / `README.md` / `docs/CHANGELOG.md` / 对端通知文档的 1.1→1.2 更新；
* 接触式（contact）与其余 scheme（amex/discover/jcb/mastercard/pure/unionpay）的端到端行为；
* L5 真机联调与 L6 跨案例 AID 覆盖（POS 端 DELETE-then-ADD）语义；
* 任何「未在设备上落地」的结论。
