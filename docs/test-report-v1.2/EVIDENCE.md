# v1.2 测试 — 命令与产物索引

> 配套：`REPORT.md`（结论与判定）、`DEFECTS.md`（缺陷与偏差）
> 约定：`EVID\` = `docs/test-report-v1.2/evidence\`；`SCR\` = `docs/test-report-v1.2/scripts\`
> 所有 JSON 均为 UTF-8；`EVID\proc\` 为各进程的原始 stdout、`EVID\proc\<场景>_logs\` 为 host 自身的结构化日志文件。

---

## 0. 环境与授权（执行前）

| 项 | 命令 | 结果 |
|---|---|---|
| Python / 依赖 | `python --version`；`python -c "import pytest,pydantic,fastapi,uvicorn"` | 3.14.5；pytest 9.1.1 / pydantic 2.12.5 / fastapi 0.141.1 / uvicorn 0.52.4 |
| 无 commit | `git rev-list --count HEAD`；`git status --porcelain` | 失败（无 HEAD）；全部 `??` |
| 陈旧锁 | `Get-Content runs\.host.lock` | `{"pid": 60656, ...}`（pid 已死） |
| 端口 | `Get-NetTCPConnection -State Listen -LocalPort 8000,9100` | 执行前空 |
| 快照 | `robocopy <repo> E:\Work\ZT\_snapshot\card_simulator_host_20260914 /E /XD logs runs tmp .git node_modules dist __pycache__ .pytest_cache ...` | 131 文件 / 6.78 MB（robocopy 退出码 1 = 成功复制） |
| 报告目录 | `New-Item -ItemType Directory docs\test-report-v1.2` | — |

---

## 1. L1 静态检查

| 编号 | 命令（cwd） | 产物 | 判定 |
|---|---|---|---|
| L1-1 | `python -m py_compile <12 模块>`（backend） | `EVID\L1-1_py_compile.log` | PASS（exit 0、无输出） |
| L1-2 | 3×`python -c`（backend）：导入 / 常量 / AppConfig | `EVID\L1-2_imports.log` | PASS（`1.2 session_abort pos_kernel_config`；`15.0 15.0 3.0 3.0`） |
| L1-3 | `python -m emv_host --help`、`--version`、`run --help`、`status`（仓库根） | `EVID\L1-3_help.txt`、`L1-3_version.txt`、`L1-3_run_help.txt`、`L1-3_status_no_host.txt` | PASS |
| L1-4 | `python SCR\l1_4_load_library.py`、`python SCR\l1_4_all_schemes.py`（`PYTHONPATH=backend`） | `EVID\L1-4_load_library.log`、`L1-4_all_schemes.log` | PASS（7 scheme / 0 error / 0 warning） |

---

## 2. L2 单元测试

| 编号 | 命令（cwd=backend） | 产物 | 判定 |
|---|---|---|---|
| §3.1/§3.3 | `python -m pytest -v` | `EVID\L2_pytest_full.log` | **130 passed, 0 failed, 0 error**，19.31 s，exit 0 |
| §3.2 红线 | `powershell SCR\check_redlines.ps1`（读上表日志，不在其内自追加） | `EVID\L2_redlines.txt` | **22/22 PASSED**，0 缺失 |
| §3.4 U-1…U-7 | `python SCR\l2_u1_u7.py EVID\L2_U1_U7.json` | `EVID\L2_U1_U7.log`、`L2_U1_U7.json` | **7/7 PASS** |

---

## 3. T-D0 内核参数

| 编号 | 命令 | 产物 | 判定 |
|---|---|---|---|
| §6.1 资产读取（只读） | 直接读 `E:\Work\ZT\KS8226\L2-SELFTEST-APP\app\src\main\assets\emv\params`（17,828 B） | 见 `REPORT.md` §4.1 表；复算脚本内嵌于下一条 | 19 AID / 29 CAPK / 1 TERM；DF03 29/29 吻合 |
| §6.2 生成 | `python SCR\build_kernel_params.py <asset> cases\visa\kernel_params.json` | `EVID\TD0_build_kernel_params.log`；产物 `cases\visa\kernel_params.json`（35,594 B，gitignored） | 写出 19 AID / 28 CAPK / TERM；排除 1 条 PHWWIPQI |
| §6.3 校验 | `python SCR\td0_validate_kp.py` | `EVID\TD0_validate_kp.log` | `errors: []`；CAPK 28（与 §6.3 的 29 冲突 → P-D2） |
| §6.4 不提交 | `git check-ignore -v cases/visa/kernel_params.json`；`git status --porcelain --untracked-files=all` | `EVID\TD0_gitignore.log` | 命中 `.gitignore:24`；1271 项未跟踪文件中**不含**该文件（兄弟文件均被列出） |
| §6.5 正向 | `python SCR\l3_scenarios.py A_single_pass` | `EVID\L3_A_single_pass.json` | PASS（含 `pos_kernel_config: aid=19 capk=28 term=yes`、顺序 kernel<pos_config） |
| §6.5 反向 | `python SCR\l3_scenarios.py TD0_reverse_no_kernel_params` | `EVID\L3_TD0_reverse_no_kernel_params.json` | PASS（无 `pos_kernel_config`，案例仍 PASS） |
| §6.5 失败-a（方案原文例） | `python SCR\l3_scenarios.py TD0_fail_plan_example_term` | `EVID\L3_TD0_fail_plan_example_term.json` | PASS（证明方案例无效 → P-D1） |
| §6.5 失败-b（真奇数长度） | `python SCR\l3_scenarios.py TD0_fail_odd_length_term` | `EVID\L3_TD0_fail_odd_length_term.json` | PASS（加载期 error，案例仍 PASS） |
| §6.5 失败-c（reject 变体） | `python SCR\l3_scenarios.py TD0_fail_reject_kernel` | `EVID\L3_TD0_fail_reject_kernel.json` | PASS（`pos_kernel_config_apply_failed`） |

生成物 `cases/visa/kernel_params.json` **未被提交、未外发**；已核查 `EVID\` 全量（含 `proc\`）无任何模数片段（8 个模数前缀 + `DF0281` 均 0 命中）。

---

## 4. L3 本地三进程（mock）

统一入口：`python SCR\l3_scenarios.py <场景名>…`（`all` 表示全部）。
`EVID\L3_summary.json` 为汇总；每个场景另有 `EVID\L3_<场景>.json`（含 `checks[] / overall_ok / logs{host,android,pos}`）与原始进程日志 `EVID\proc\<场景>_{host,android,pos}.log`。

| 场景 | 方案条目 | 产物 | 判定 |
|---|---|---|---|
| `A_single_pass` | §4.3 + §6.5 正向 | `EVID\L3_A_single_pass.json` | PASS（27 断言） |
| `B_declined_offline_pass` | §4.4 | `EVID\L3_B_declined_offline_pass.json` | PASS |
| `C_approved_online_pass` | §4.5 | `EVID\L3_C_approved_online_pass.json` | PASS |
| `D_assertion_failed_stops_run` | §4.6 | `EVID\L3_D_assertion_failed_stops_run.json` | PASS（含 F2 偏差记录） |
| `E1_android_not_connected` | §4.7 前半 | `EVID\L3_E1_android_not_connected.json` | PASS |
| `E2_card_config_ack_timeout` | §4.7 后半 | `EVID\L3_E2_card_config_ack_timeout.json` | PASS |
| `F_pos_not_connected` | §4.8 | `EVID\L3_F_pos_not_connected.json` | PASS（含 P-D3 偏差记录） |
| `G_result_timeout` | §4.9 | `EVID\L3_G_result_timeout.json` | PASS（实测 15.6 s） |
| `H_fail_then_skipped` | §4.10 | `EVID\L3_H_fail_then_skipped.json` | PASS |
| `I_kind_group` | §4.11 | `EVID\L3_I_kind_group.json` | PASS（404/400 反例齐备） |
| `J_manual_abort` | §4.12 | `EVID\L3_J_manual_abort.json` | PASS |
| K（§4.13，含在 A 内） | §4.13 | 同 `A`：`session_abort` 计数 0/0/0 | PASS |
| `M_pos_ack_deadlock` | （新增取证） | `EVID\L3_M_pos_ack_deadlock.json`、`proc\M_pos_ack_deadlock_pos.log` | PASS → 缺陷 F1 |
| §4.2 硬切换反证 | §4.2 | `python SCR\probe_handshake.py` → `EVID\L3_handshake_hard_switch.json`、`L3_handshake_summary.json` | PASS（1.1/缺失拒、1.2/1.3 收） |

辅助（一次性）：
* `python SCR\cases_factory.py timeout15|nokp|badterm` → `EVID\L3_cases_factory.log`；在 `tmp\v12test\cases\<name>` 建独立 cases 根，**不动 `cases/`**。
* `python SCR\make_mocks.py` → `EVID\L3_make_mocks.log`；由仓库 mock **文本变换**生成三个变体（每处替换均断言唯一命中）：`mock_android_silent.py`（去掉 config_push ack）、`mock_pos_dup.py`（同 msg_id 重发）、`mock_pos_reject_kernel.py`（对 `pos_kernel_config` 回 `ok=false`）。**仓库原 mock 未被修改。**
* `python SCR\smoke.py` → `EVID\L3_smoke.json`；harness 自检 + 陈旧锁自动清理取证。

---

## 5. L4 故障注入

统一入口：`python SCR\l4_scenarios.py all` → `EVID\L4_suite_run.log`、`EVID\L4_summary.json`。

| 场景 | 方案条目 | 产物 | 判定 |
|---|---|---|---|
| `L4_1_bad_frames` | §5.1 | `EVID\L3_L4_1_bad_frames.json`、`proc\L4_1_*.log`；注入端 `SCR\inject_badframe.py` | PASS（16 断言） |
| `L4_2_duplicate_result` | §5.2 | `EVID\L3_L4_2_duplicate_result.json`；客户端 `SCR\raw_pos_dup.py` | PASS（`persisted` → `duplicate`，无 quarantine） |
| `L4_2b_late_resend_is_quarantined` | §5.2 边界 | `EVID\L3_L4_2b_late_resend_is_quarantined.json` | PASS（记录 P-D4 边界） |
| `L4_3a_persist_failed_then_timeout` | §5.3 前半 | `EVID\L3_L4_3a_persist_failed_then_timeout.json` | PASS |
| `L4_3b_persist_failed_then_retransmit_passes` | §5.3 后半 | `EVID\L3_L4_3b_persist_failed_then_retransmit_passes.json`；客户端 `SCR\raw_pos_retry.py` | PASS（ack 序列 `persist_failed` → `persisted`） |
| `L4_4a_android_disconnect` | §5.4 | `EVID\L3_L4_4a_android_disconnect.json` | PASS |
| `L4_4b_pos_disconnect` | §5.4 | `EVID\L3_L4_4b_pos_disconnect.json` | PASS |
| `L4_5_loader_tolerance` | §5.5 | `EVID\L3_L4_5_loader_tolerance.json` | PASS |
| `L4_6_atomic_no_tmp` | §5.6 | `EVID\L3_L4_6_atomic_no_tmp.json`（`runs_tmp: []`、`cases_tmp: []`） | PASS |

> §5.3 的落盘失败注入**未使用**方案建议的 `attrib +R`（Windows 下目录只读属性不阻止其中新建文件），改用**同名目录占位**使 `RunStore._atomic_write_json` 的 `os.replace` 失败；`L4_3a` 在观测到失败后移除占位，使后续超时记录能正常写入。

---

## 6. L5 / L6

未执行。阻塞条件与解除条件见 `REPORT.md` §9。
本轮已具备的可复用前置：`cases/visa/kernel_params.json` 就位；`/api/health` 不含 `protocol_version` 已核实（判 1.2 需看 host 日志 `handshake ok: ... protocol=1.2`）。

---

## 7. 脚本清单（`docs/test-report-v1.2/scripts/`）

| 文件 | 用途 |
|---|---|
| `l3_harness.py` | 进程/host/双 mock 编排、REST 驱动、断言收集、残留进程自清理 |
| `l3_scenarios.py` | L3 场景 A–K、M、T-D0 §6.5 四个变体 |
| `l4_scenarios.py` | L4 场景 §5.1–5.6（含边界与拆分） |
| `cases_factory.py` | 生成独立 cases 根（timeout15 / nokp / badterm / badlib），不改 `cases/` |
| `make_mocks.py` | 由仓库 mock 生成三个受断言保护的变体，不改原 mock |
| `inject_badframe.py` | §5.1 坏帧注入（合法长度前缀 + 非法 JSON） |
| `raw_pos_dup.py` | §5.2 专用 POS 客户端（独立 reader，可真正读到 ack） |
| `raw_pos_retry.py` | §5.3 后半 重传客户端（新 msg_id） |
| `probe_handshake.py` | §4.2 协议硬切换探测（1.1/缺失/1.2/1.3） |
| `l2_u1_u7.py` | §3.4 边界断言 U-1…U-7 |
| `td0_validate_kp.py` | §6.3 用被测代码校验生成的 kernel_params |
| `build_kernel_params.py` | §6.2 生成 kernel_params（自带独立 TLV 解析） |
| `l1_4_load_library.py` / `l1_4_all_schemes.py` | §L1-4 案例库冷加载 |
| `check_redlines.ps1` | §3.2 22 条红线逐条核对 |
| `smoke.py` | harness 自检 + 陈旧锁清理取证 |

---

## 8. 复现整个 L1–L4 轮次（供复核）

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
```

> 复现前请确认 8000/9100 空闲且无残留 host；harness 每轮开始会自动清理**本 harness 自己**的孤儿进程，但不会碰其它工具。
