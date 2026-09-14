# EMV Test Host v1.2 — 测试执行报告（L1–L4 + T-D0）

> 被测方案：`docs/test-plan-v1.2.md`（编写日期 2026-09-11）
> 被测仓库：`E:\Work\ZT\card_simulator_host`（执行时无任何 commit，`git status` 全为 `??`）
> 执行日期：2026-09-14
> 执行环境：Windows，Python 3.14.5（`C:\Python314`）、pytest 9.1.1、pydantic 2.12.5、fastapi 0.141.1、uvicorn 0.52.4
> 报告与证据：`docs/test-report-v1.2/REPORT.md`（本文，人工分析）、`DEFECTS.md`（缺陷/偏差分级与最小复现）、`EVIDENCE.md`（命令↔产物索引）、`evidence/`（原始落盘）、`scripts/`（本轮全部脚本）
> 证据纪律：结论一律以落盘 JSON / host.log / mock 日志为准；下文每项均标明「直接观测」或「推断」。

---

## 0. 执行摘要

**L1–L4 与 T-D0 全部可执行用例通过：26 个场景 / 187 项断言全部通过，0 个场景失败。**

断言分布：L3 场景（A–K、M、§4.2 硬切换）107 项 + T-D0 §6.5 四个变体 22 项 + L4（§5.1–5.6）58 项 = **187 项**；§6.1–6.4 为数据/规则核对，见 §4，不计入该断言数。汇总见 `evidence/L3_summary.json`（由 `scripts/rebuild_summary.py` 从各场景证据回算，`all_passed: true`）。

| 级别 | 用例数 | 通过 | 失败 | 未执行 | 备注 |
|---|---|---|---|---|---|
| L1 静态检查 | 4 | 4 | 0 | 0 | |
| L2 单元测试 | 130 用例 + §3.2 22 条红线 + §3.4 7 项 | 130/130、22/22、7/7 | 0 | 0 | §3.3「通过数 ≥100」满足 |
| L3 本地三进程（mock） | 13（A–K + M 缺陷取证 + §4.2 硬切换反证） | 13 | 0 | 0 | §4.5 的 C、§4.11 的 I 均含在列 |
| L4 故障注入（mock） | 9（§5.1–5.6，含 §5.3 拆两条、§5.2 边界一条） | 9 | 0 | 0 | |
| T-D0 内核参数 | 5（§6.1–6.5；§6.5 含 4 个变体） | 5 | 0 | 0 | 生成 visa 一份 `kernel_params.json` |
| L5 真机联调 | 阻塞 | 0 | 0 | 全部 | 待两端升级到 1.2 并各自 `[READY]` |
| L6 跨案例残留 | 阻塞 | 0 | 0 | 全部 | 同上 |

**是否出现 P0/P1 缺陷：无 P0、无 P1。**
共记录 **3 条被测仓库内缺陷（P2×2、P3×1）** 与 **5 条方案（文档）缺陷/偏差**，另 3 条环境/工具观察项。完整分级与最小复现见 `DEFECTS.md`。

**一句话结论**：v1.2 P0+P1 的语义（协议 1.2 硬切换、POS 内核参数下发与顺序、FAIL 即整轮停止 + 剩余落 SKIPPED、`transaction_result` 先落盘后 ack、原子落盘、加载器容错、Web `kind=group`）在 mock 环境下**全部按方案预期工作**；未通过的是两条**可观测性/字段一致性**问题（P2）与一条**产物 schema 不一致**（P3），以及**方案文档本身**的 5 处与实现不符之处。**本轮结论不覆盖 P2/P3 未实现部分**（见 §9）。

---

## 1. 执行前的基线与授权

### 1.1 基线核对（直接观测）

* `git rev-list --count HEAD` 失败：仓库无 commit，与方案 §0.2 一致 → **无 git 回退基线**。
* `runs/.host.lock` 为陈旧锁：`{"pid": 60656, ...}`（2026-09-09），pid 60656 已死。
  方案 §4.1 要求手工 `Remove-Item`；**实际未手工删**——`appconfig.py:151-164` 的 `acquire_lock()` 对死 pid 锁会自动替换并打 warning。首轮 host 启动日志实测出现
  `removing stale lock from dead pid 60656`，锁随后换成新 pid，退出时被 `cli.py:265-269` 正常解除（`Test-Path runs\.host.lock` 在每轮结束后均为 `False`）。
* 端口 8000/9100 执行前空闲；Python 依赖齐全（pytest/pydantic/fastapi/uvicorn/websockets/httpx 均可导入）。
* `cases/` 冷加载：7 个 scheme、0 error、0 warning（见 L1-4）。

### 1.2 已获用户授权的范围（严格限定，未越界）

| 授权项 | 范围 | 实际用途 |
|---|---|---|
| 快照 | 写 `E:\Work\ZT\_snapshot\card_simulator_host_20260914\`，**仅源码+cases+docs** | 已生成：131 文件 / 6.78 MB（排除 `logs/`、`runs/`、`tmp/`、`frontend/node_modules`、`.git`、`__pycache__`） |
| POS 资产 | **只读** `E:\Work\ZT\KS8226\L2-SELFTEST-APP\app\src\main\assets\emv\params` | T-D0 数据来源；POS 仓库零写入 |
| 报告位置 | `docs/test-report-v1.2/`（报告 + 证据分文件） | 本报告 + `evidence/` + `scripts/` |
| §5.3 注入法 | 目录占位法 | 见 §7.3 |
| `kernel_params.json` | **仅 visa** | 见 §5 |

**未修改任何现有源码、未修改任何 `cases/` 生产案例、未修改 `tools/` 下的 mock、未 git commit。**
唯一写入 `cases/` 的是 gitignored 的 `cases/visa/kernel_params.json`（§5）。

---

## 2. L1 静态检查（4/4 通过）

| 编号 | 命令 | 期望 | 实际 | 判定 |
|---|---|---|---|---|
| L1-1 | `python -m py_compile`（方案列出的 12 个模块，cwd=backend） | 无输出、退出码 0 | 退出码 0，无输出 | PASS |
| L1-2 | 三次 `python -c`：导入装配、常量、AppConfig 默认值 | `1.2 session_abort pos_kernel_config`；`15.0 15.0 3.0 3.0` | 逐字一致 | PASS |
| L1-3 | `--help` / `--version` / `run --help` / `status`（host 未起） | help 含两个 ack-timeout 且注明 default 15、不出现 `Uvicorn running`；version=`0.1.0`；run help 列 5 个开关；status 非零退出 | 全部一致；`--card-ack-timeout` 帮助文本为 `seconds to wait for a card-side ack (default 15)`；`Uvicorn running` 未出现；`status` 退出码 1、报 `cannot reach server at 127.0.0.1:8000` | PASS |
| L1-4 | `load_library(<repo>/cases)` | 7 scheme、errors 为空、visa 含 `VISA-CL-001..004` 与 `OPBD-001-*` | `['amex','discover','jcb','mastercard','pure','unionpay','visa']`；`errors: []`；visa contactless 8 例（`OPBD-001-01..04` + `VISA-CL-001..004`）；**issues 全表为空（0 error / 0 warning）** | PASS |

产物：`evidence/L1-1_py_compile.log`、`L1-2_imports.log`、`L1-3_*.txt`、`L1-4_*.log`。

---

## 3. L2 单元测试（130/130 + 22/22 + 7/7）

### 3.1 全套结果（§3.3）

```
cd backend && python -m pytest -v
====================== 130 passed, 4 warnings in 19.31s =======================
PYTEST_EXIT=0
```

* 收集 130、通过 130、**0 failed / 0 error**；`rootdir: ...\backend`，`configfile: pytest.ini`。
* §3.3「通过数 ≥ 100」**满足**。
* **§0.3 的两类环境现象本轮均未出现**：`WinError 5` 0 次、`PermissionError` 0 次、无 `CTRL_BREAK_EVENT` 相关 skip/失败。
  原因（直接观测）：本会话文件策略为 `danger-full-access`，且运行在有控制台的终端下，因此方案 §0.3 描述的「DSH 沙箱挡住 pytest 临时目录」不成立。**属环境差异，非被测代码变化。**
* 4 条 warning 均为非功能性：`PytestConfigWarning: No files were found in testpaths`（尽管如此仍从 `tests/` 收集到 130 项，退出码 0）、starlette 的 httpx 弃用警告、2 条 `TestCase` 不可收集警告（`emv_host.cases.models.TestCase` 被 pytest 误判为测试类）。

### 3.2 §3.2 回归红线逐条核对（22/22 通过）

22 条红线**全部出现在日志中且全部 PASSED**，无缺失、无非 PASS 项。清单与逐条判定见 `evidence/L2_redlines.txt`。要点直接观测：

* `handshake ack` 的 `protocol_version == "1.2"`；
* 广告 1.1 / 不带 `protocol_version` 均被拒且**不注册**；广告 1.3 被接受；
* 第 1 个坏帧后仍能处理后续正常帧，第 5 个坏帧才断连；
* 连接关闭时 pending ack 抛 `ConnectionClosed`（非 `CancelledError`）；
* ack 到达时 `results/<case>.json` 已存在且 `detail=persisted`；
* 落盘异常 → `ok=false` + `detail=persist_failed` 且 waiter 未 resolve；
* 同 `msg_id` 重传 → `detail=duplicate`，`quarantine.jsonl` 不存在；
* `result_timeout` → `failure_reason=result_timeout`、run `ABORTED`、`abort_reason=case_failed:C-01:result_timeout`、后续 `SKIPPED` + `note=run_stopped_after_failure:C-01`、两端各一次 `session_abort`；
* POS 离线 → `FAIL` + `pos_not_connected`（不再 warn）；
* `status:error` → 案例 ERROR 且 `results[...].android_error == {code,message}`；
* `pos_kernel_config` 早于 `pos_config`，且帧内 `kernel_params.aid[0]` 以 `9F06` 开头；
* 案例级 `kernel_params` 完全覆盖 scheme；
* 同号案例记 error 且保留独立文件那一份；`case_id` 与 `group_id` 互斥；
* 原子写无 `*.tmp` 残留；`cases/<scheme>/kernel_params.json` 被加载且 0 error；
* `../` 路径 → HTTP 400 且提示「用例库之外」；总案例带 `kernel_params` → 校验失败；
* `/ws?since=abc` → 仍先收到 `kind=snapshot`、`seq=0`；
* `pid_alive`：本进程 pid 存活，死 pid 与 0/-1 为 False。

### 3.3 §3.4 边界断言 U-1…U-7（7/7 通过）

一次性脚本 `scripts/l2_u1_u7.py`，结论落 `evidence/L2_U1_U7.json`：

| 编号 | 断言 | 实测（直接观测） | 判定 |
|---|---|---|---|
| U-1 | `ber_length` 短/长形式 | `0x7F=b'\x7f'`、`0x80=b'\x81\x80'`、`248=b'\x81\xf8'` | PASS |
| U-2 | 248B 模数 CAPK 编码后可原样解回 | `DF02` 长度 248B、值 identical=True；解回 tags 恰为 `9F06/9F22/DF02/DF03/DF04/DF05/DF06/DF07` | PASS |
| U-3 | 缺 `checksum` 时自动算 DF03 | `DF03=FDBBB121294034B5D899A25854FB15547CDA274E`，与 `SHA1(9F06‖9F22‖DF02‖DF04)` 逐字相同 | PASS |
| U-4 | `DF05="PHWWIPQI"` 在模型层被拒 | `ValueError`（`1 validation error for CapkParam`）；根因 `models.py:146-151` 要求 8 位数字 | PASS |
| U-5 | 9F06 9 字节拒绝、7/8 字节接受 | 9B rejected=True、7B/8B accepted=True | PASS |
| U-6 | `validate_kernel_params(None, x)` 无 issue | `issues=[]` | PASS |
| U-7 | `KernelParams().empty is True` | `empty=True`；行为侧印证 `executor.py:738` `if kernel is not None and not kernel.empty:` → 不下发 | PASS |

---

## 4. T-D0 内核参数数据准备与校验（§6.1–6.5，全部完成）

### 4.1 §6.1 资产读取（只读）

`params` 是**单个文件**（17,828 字节，非目录），行式文本 `AID=` / `CAPK=` / `TERM=`。

| 项 | 方案声明 | 实测 |
|---|---|---|
| AID | 19 条 | **19 条**（`9F06` 均在首；AID 长度 5/6/7/8 字节） |
| CAPK | 29 条 | **29 条**（RID 均 5B、DF06=01、DF07=01、DF04=03；模数长度集合 `{128,144,176,248}`） |
| TERM | 1 条 | **1 条**：`9F6604360000809F1A0201569F7A0101` |
| `DF03 = SHA1(9F06‖9F22‖DF02‖DF04)` | 29/29 吻合 | **29/29 吻合**（用独立实现复算，非调用被测代码） |

### 4.2 §6.2 生成 `cases/visa/kernel_params.json`

用一次性脚本 `scripts/build_kernel_params.py` 生成（**刻意放在 `tmp/`→`docs/.../scripts/`，不放 `tools/`**，以免与 P2 的 `tools/export_kernel_params.py` 混淆；后者确认**不存在**，符合 §0.1）。
解析使用脚本自带的独立 BER-TLV 走查器，**不复用被测代码**，避免「自己生成自己验」。

结果：**AID 19 条 / CAPK 28 条 / TERM 1 条**，35,594 字节。
**唯一被排除的是 `RID=A000000333 / 9F22=08`（DF05=`PHWWIPQI`）**，依据 §6.2 明文要求「不要抄进 JSON」，且 U-4 已证实模型层直接拒绝。
4 条「只有 `9F06`、无其它元素」的 AID（`D9999999992020`、`D666666666`、`A0000002282010`、`A0000007231010`）**原样保留**——模型层接受 5–8 字节 AID，仅在 §6.3 校验时产生 warning，不影响 `errors: []`。

### 4.3 §6.3 校验（用被测代码自身）

```
errors  : []
warnings: ['kernel_params.aid[15] D9999999992020 除 9F06 外没有任何元素', ... ×4]
AID 19 CAPK 28 term True
payload aid[0] : 9F0607A0000000041010DF01 ...   aid[0] starts with 9F06: True
```

* `errors: []` **符合**。
* **数量：CAPK 实际为 28，而资产为 29** —— 这是方案 **§6.2 与 §6.3 自身的冲突**（§6.2 要求排除 PHWWIPQI，§6.3 要求「数量与资产一致」）。按 §6.2 执行，差异记入 `DEFECTS.md` 的 P-D2。

### 4.4 §6.4 确认不会提交

* `git check-ignore -v cases/visa/kernel_params.json` →
  `.gitignore:24:cases/*/kernel_params.json	cases/visa/kernel_params.json`（命中规则）。
* 因仓库无 commit，`git status --porcelain` 只报 `?? cases/`（目录级），**该检查本身是弱证据**。故补做强化验证：
  `git status --porcelain --untracked-files=all` 共 1271 项，其中 `cases/visa/` 下的兄弟文件（如 `.gen_cases_manifest.json`）**均被列出**，而 `cases/visa/kernel_params.json` **未出现** → 忽略规则确实生效。
* 该文件含 CAPK 模数（密钥材料），**未提交、未外发**；已另行核查 `docs/test-report-v1.2/evidence/` 中**不含任何模数片段**（8 个模数前缀 + `DF0281` 全部 0 命中）。

### 4.5 §6.5 下发路径端到端（正向 / 反向 / 失败）

| 变体 | 场景 | 期望 | 实际（直接观测） | 判定 |
|---|---|---|---|---|
| 正向 | `cases/visa/kernel_params.json` 在位，跑 VISA-CL-001 | POS 端出现 `pos_kernel_config: aid=19 capk=28 term=yes`；其 ack 早于 `pos_config` 的 ack；无 `pos_kernel_config_*` 失败原因 | POS 日志逐字一致；`kernel_idx=275 < pos_config_idx=441`；host 日志 `pos_kernel_config applied (AID 19 条 / CAPK 28 条)`；无任何 `pos_kernel_config_ack_timeout` / `_apply_failed` | PASS |
| 反向 | 改名去掉 `kernel_params.json`（用独立 cases 根，不动 `cases/`） | POS 端**不应**出现 `pos_kernel_config`；案例仍 PASS | POS 日志 0 次 `pos_kernel_config`；host 无 `loaded local kernel params`；案例 **PASS**、run FINISHED | PASS |
| 失败-a | 按方案原文把 `term` 改为 `9F660436000080` | 方案称「奇数长度 hex」→ 案例应 FAIL | **方案有误**：该值是 14 个 hex 字符（7 字节，偶数）且解码为合法 BER-TLV `{9F66: 36000080}`；host 校验**无 error**，案例 **PASS** | 方案缺陷 P-D1 |
| 失败-b | 真·奇数长度 `term` = `9F6604360000809`（15 字符） | 方案允许「先被 host 校验拦下、以 error 记入加载 issues」 | host 加载期记 error（`kernel_params.term 不是偶数长度 hex`），run 仍 FINISHED、案例 **PASS**（stock mock 对任何内核参数都 ack ok=true，故 apply 失败无法由它触发） | PASS（记录了实际分支） |
| 失败-c | 用 reject 变体 mock（对 `pos_kernel_config` 回 `ok=false`） | 案例 FAIL + `failure_reason == pos_kernel_config_apply_failed` | 案例 **FAIL**、`failure_reason=pos_kernel_config_apply_failed`、run ABORTED、`abort_reason=case_failed:VISA-CL-001:pos_kernel_config_apply_failed`、host 日志 `POS rejected pos_kernel_config`、且**未下发 pos_config** | PASS |

> 失败-b/c 的设计意图：方案 §6.5 的失败验证在 stock mock 下**不可达**（mock 无内核、恒回 ok=true），故补做 reject 变体以真正走到 `executor.py:760-763`。这属于「补足方案未覆盖的验证路径」，不是改动被测代码。

---

## 5. L3 本地三进程（mock）

### 5.0 前置与启动

* 三个进程一律由 `scripts/l3_harness.py` 以**子进程**方式管理，stdout/stderr **重定向到文件**（不使用管道），符合本机执行沙箱限制。
* 启动顺序固定：host → android mock → POS mock；每轮独立 host（独立 `--log-dir`），用例前后自动清理残留进程（见 §8.3）。
* host 关键启动行实测：`EMV test host v0.1.0 starting: web http://127.0.0.1:8000, device TCP 0.0.0.0:9100, cases=...`。
* 卡端握手实测：`[android] connected, session android-...`；host 侧 `handshake ok: android@mock-android (device=mock-android app=mock-0.1 protocol=1.2)`。

### 5.1 §4.2 协议 1.2 硬切换反证（**改为直接探测，不修改仓库文件**）

方案 §4.2 建议「临时把 `mock_common.py` 的握手改成 `1.1`」。为**不改动任何仓库文件**，改用 `scripts/probe_handshake.py` 向 9100 发四种握手（直接观测）：

| 广告版本 | 响应 | session_id | 结论 |
|---|---|---|---|
| `1.1` | `error` / `code=protocol_mismatch` / `protocol 1.1 not supported (need 1.2)` | **None（未注册）** | 硬切换生效 |
| 不带 `protocol_version` | `error` / `protocol_mismatch` / `handshake must carry protocol_version=1.2` | None | v1.2 不再宽容 |
| `1.2` | `handshake_ack`，回显 `protocol_version=1.2` | `android-...` | 接受 |
| `1.3` | `handshake_ack`，回显 `protocol_version=1.2` | `android-...` | 前向兼容 |

host 日志对应出现 `handshake rejected from ...`（2 次）、`protocol 1.1 not supported`、`handshake must carry protocol_version=1.2`、`protocol=1.2`、`protocol=1.3`。

### 5.2 L3 用例逐条判定（13/13 通过）

| 用例 | 方案条目 | 关键实测（直接观测） | 判定 |
|---|---|---|---|
| **A** 单案例 PASS | §4.3 | run `FINISHED`、`abort_reason` 空、案例 `PASS`；`trace/VISA-CL-001.jsonl` 存在且**4 行**（2 对 APDU）；卡端 `trace=True` → `status: ready` → `status: emulating` → `trace: emitted 2 APDU pairs` → `status: stopped`；POS `pos_config applied: 4 tag`、`ack`；host `[VISA-CL-001] final: PASS` + `ended: FINISHED`；**§6.5 正向**见 §4.5 | PASS |
| **B** 离线拒付 | §4.4 | VISA-CL-002 **PASS**；`raw_result.resp_code=05`、`outcome=declined`、`online=offline` → 证明判定确实按 `expected_results` 走 | PASS |
| **C** 联机批准 | §4.5 | VISA-CL-003 **PASS**；`raw_result.online=online`、`resp_code=00` | PASS |
| **D** 断言不符 → FAIL 且整轮停 | §4.6 | 案例 `FAIL`、`failure_reason=assertion_failed`；run `ABORTED`；`abort_reason=case_failed:assertion_failed`（**方案要求带案例号，见 P-D/F2**）；host `session_abort broadcast (reason=assertion_failed`、卡端与 POS 两端**均**收到 `session_abort reason=assertion_failed` | PASS |
| **E1** 卡端未连接 | §4.7 前半 | `android_not_connected`，run `ABORTED`，`abort_reason=case_failed:VISA-CL-001:android_not_connected` | PASS |
| **E2** `card_config` ack 超时 | §4.7 后半 | 用「连上但不对 `config_push` 回 ack」的副本 mock：`card_config_ack_timeout`，run `ABORTED`；**卡端从未收到 `start_emulation`**（方案反证成立） | PASS |
| **F** POS 离线即 FAIL | §4.8 | `pos_not_connected`，run `ABORTED`，`abort_reason=case_failed:VISA-CL-001:pos_not_connected`；**v1.1 此处仅 warn** 的差异成立。卡端**未**收到 `stop_emulation`，而是收到 `session_abort reason=pos_not_connected`（见 P-D3） | PASS |
| **G** 结果超时 | §4.9 | 用 `timeout_seconds=15` 的独立 cases 根：案例 `FAIL`、`failure_reason=result_timeout`、run `ABORTED`、`abort_reason=case_failed:VISA-CL-001:result_timeout`；实测耗时 **15.6 s**（与 15 s 预算吻合）；host 与卡端均收到 `session_abort reason=result_timeout` | PASS |
| **H** 停轮后剩余 SKIPPED | §4.10 | VISA-CL-001 `FAIL`；VISA-CL-002/003 `SKIPPED` 且 `note=run_stopped_after_failure:VISA-CL-001`；run `ABORTED`；`GET /api/history/<run_id>` 三份结果齐全；**反证**：POS 端仅 1 次 `case_started`，`VISA-CL-002` 从未出现 | PASS |
| **I** `kind=group` | §4.11 | REST `kind=group, group_id=OPBD-001` → HTTP 200 + `ok:true`；run `queue` **恰为** `[OPBD-001-01..04]`；反例 `NOPE-001` → **404**，缺 `group_id` → **400** | PASS |
| **J** 手动 abort | §4.12 | 在 `WAIT_RESULT` 窗口执行 `python -m emv_host abort`：案例 `ABORTED`（**不是 FAIL**）、`failure_reason` **为空**（未继承上一个失败原因）、run `ABORTED`、`abort_reason=manual abort`；host `session_abort broadcast` + `reason=manual_abort`，卡端收到 `reason=manual_abort` | PASS |
| **K** 正常结束不发 abort | §4.13 | 用例 A 正常跑完后：host `session_abort broadcast` 计数 **0**、POS/卡端 `session_abort` 计数 **0**；产物目录内**无** `session_abort` 相关产物 | PASS |
| **M** POS mock ack 取证 | （新增，非方案条目） | 见 `DEFECTS.md` F1：stock POS mock **永远读不到** host 的 ack | PASS（缺陷已复现） |

### 5.3 关于「结果超时」与「手动 abort」的两个实现细节（直接观测）

* 方案 §4.9 建议用 `--interactive`（等回车）制造「POS 不回结果」。因该方式依赖控制台 stdin，本机以子进程方式运行不可靠，改等价做法：**stock mock `--delay 9999`**（案例如期超时且不报结果），观测点完全一致。记为方案执行方式偏差 P-D7。
* 因 POS mock 的缺陷 F1，G/J 两例中 POS 端处于长时间 `on_message`（`asyncio.sleep(9999)`）而**无法消费 `session_abort`**；故这两例的 `session_abort` 只断言 host 与卡端，POS 端不作断言并在证据中注明原因。D/H 两例中 POS 在 15 s 后恢复读取，**已实测收到** `session_abort`。

---

## 6. L4 故障注入（9/9 通过）

| 用例 | 方案条目 | 关键实测（直接观测） | 判定 |
|---|---|---|---|
| **L4_1** 坏帧注入 | §5.1 | 注入端完成 1.2 握手；发 5 个「长度前缀合法、JSON 非法」帧，前 4 个各跟 1 个正常 `log` 帧。host 日志依次出现 `skipping bad frame (1/5)`…`(4/5)`，且**正常帧仍被处理**（`[android] alive0`…`alive3` 各 1 次）；第 5 个坏帧后 `bad frames, dropping`、连接被断（注入端观测到 EOF）；**全程 host 未重启、未崩溃**（`/api/health` 仍 200，进程存活） | PASS |
| **L4_2** 结果重传去重 | §5.2 | 同 `msg_id` 重传：第一次 ack `detail=persisted`；重传 ack **`detail=duplicate`**；host 日志 `duplicate transaction_result ignored`；`results/VISA-CL-001.json` 仅一份且 `PASS`；**`quarantine.jsonl` 不存在**；run `FINISHED` | PASS |
| **L4_2b** 去重窗口边界 | §5.2 补充 | 若重传在**下一个案例已开始**之后到达：`Session.begin_attempt()` 已重置 `judged_msg_ids`（`session.py:61`），重传被**隔离而非去重** → ack `detail=quarantined`、`quarantine.jsonl` **被写出**，与方案「不得存在 quarantine.jsonl」不符。记为方案/实现边界差异 P-D4 | PASS（记录边界） |
| **L4_3a** 落盘失败 → 等待至超时 | §5.3 | 目录占位法使 `os.replace` 失败：host 日志 `结果落盘失败，未 ack 成功`；**waiter 未被 resolve**（host 无 `transaction result received ...`）；run 继续等待 → 15 s 后 `failure_reason=result_timeout`、run `ABORTED`；解除占位后无 `*.tmp` 残留 | PASS |
| **L4_3b** 落盘失败后重传（新 msg_id）→ PASS | §5.3 后半 | 专用客户端（能真正读到 ack）两次上报，实测 ack 序列 `[{ok:false, detail:persist_failed}, {ok:true, detail:persisted}]`；随后案例 **PASS**、run `FINISHED` | PASS |
| **L4_4a** 卡端断线 | §5.4 | 在 `WAIT_RESULT` 窗口切断卡端 mock：案例 `FAIL`、`failure_reason=android_disconnected`、run `ABORTED`、`abort_reason=case_failed:VISA-CL-001:android_disconnected` | PASS |
| **L4_4b** POS 断线 | §5.4 | 同法切断 POS mock：`pos_disconnected`、run `ABORTED`、`abort_reason=case_failed:VISA-CL-001:pos_disconnected`（v1.1 此处仅 warn） | PASS |
| **L4_5** 加载器容错与重复 id | §5.5 | 独立临时 cases 根：放入 `{ not json` 的 `BROKEN-001.json` → **`load_library` 不抛异常**，`VISA-CL-001` 仍被列出，`BROKEN-001` 以 error 记入 issues；再把同一 `case_id` 同时放到 `cases/` 根与组目录 → 报「重复」error，且**保留独立文件那一份**（实测标题为 `STANDALONE COPY WINS`，非组内副本） | PASS |
| **L4_6** 原子写无残留 | §5.6 | **在本轮全部用例（含 5.3 的落盘失败注入）之后**扫描：`runs/` 与全部临时 cases 根下 `*.tmp` 均为 **0 个** | PASS |

---

## 7. 缺陷分级与偏差（摘要）

完整内容（含最小复现步骤、证据文件、归因）见 **`DEFECTS.md`**。

### 7.1 被测仓库内缺陷（无 P0 / 无 P1）

| 编号 | 级别 | 一句话 | 影响面 |
|---|---|---|---|
| **F1** | **P2** | `tools/mock_clients/mock_common.py`：在 `on_message` 内 `await send_and_wait_ack()` 会阻塞**同一个** reader 循环，导致 stock POS mock **永远读不到** host 的 ack（每次结果上报都空等 15 s 并误报 `not persisted?`） | 方案 §4.3 第 6 观测点与 §5.2 的 POS 侧 `detail=duplicate` **不可达**；D2「先落盘后 ack」无法由 stock mock 侧验证。**host 自身行为正确** |
| **F2** | **P2** | `run.abort_reason` 格式随失败路径不一致：断言失败为 `case_failed:assertion_failed`（**无案例号**），其它失败路径为 `case_failed:<case_id>:<reason>` | 与方案 §4.6 记载不符；案例号仍可从 SKIPPED note / 单案例文件恢复，非数据丢失 |
| **F3** | **P3** | `results/<case>.json` schema 随路径不同：PASS/WARN/FAIL 记录写 `attempt`（0 基），ABORTED/SKIPPED/ERROR 记录写 `attempts`（1 基计数） | 消费方按哪一个字段读都会在另一半路径取空；`run["results"][case]` 恒用 `attempts` |

### 7.2 方案（文档）缺陷与偏差

| 编号 | 方案位置 | 问题 |
|---|---|---|
| **P-D1** | §6.5 失败验证 | 原文举例 `9F660436000080` 称为「奇数长度 hex」，实际 14 字符（偶数）且是合法 TLV；按该例**不会**失败。且 stock mock 恒 ack ok=true，`pos_kernel_config_apply_failed` 在其上不可达 |
| **P-D2** | §6.2 vs §6.3 | §6.2 要求排除 `PHWWIPQI` 那条 CAPK，§6.3 又要求「数量与资产一致」；二者互斥（28 vs 29） |
| **P-D3** | §4.8 | 期望卡端收到 `stop_emulation`，实际收到 `session_abort reason=pos_not_connected`（该失败在 `WAIT_RESULT` 之前抛出，而 `_send_stop` 只在 `_wait_result` 的 finally 中调用） |
| **P-D4** | §5.2 | 去重窗口实为**单次 attempt**；重传若在下一案例开始后到达会被隔离并写出 `quarantine.jsonl`，与「quarantine.jsonl 不存在」的绝对表述不符 |
| **P-D5** | §4.3 落盘核对 | 示例命令读取字段 `attempts`，但 PASS 结果文件并无该字段（见 F3） |
| **P-D6** | §0.3 | 所述两类环境限制本轮**均未出现**（无 WinError 5、CTRL_BREAK 可用），该节属条件性免责声明 |
| **P-D7** | §4.9 | 建议用 `--interactive` 制造无结果；依赖控制台 stdin，子进程化运行不可靠，已改用等价 `--delay 9999` |

### 7.3 环境/工具观察（非缺陷）

* host 在**对端被强杀**时会打 `ERROR ... unexpected connection error ... [WinError 64]` 并附完整 traceback（正常 `close` 则为 INFO `peer closed connection`）。本轮 traceback 全部出现在各用例**已判定完成之后**的收尾阶段，不影响判定；建议后续仅作日志降噪处理。
* 控制台代码页导致的**中文乱码**出现在部分 `*.log` 捕获中（`PYTHONIOENCODING=utf-8` 已用于后续各轮）；落盘 JSON 均以 UTF-8 显式写出，未见乱码。
* 本轮执行者自身的两个工具问题（**已修复并复测**，详见 §8.3）：`Select-String | Tee-Object` 自追加导致 76 MB 失控日志（已删）；`stop_all()` 异常中断导致 2 个 host + 2 个 mock 泄漏（经用户授权后按 PID 精确清理，并加固 harness）。

---

## 8. 执行过程记录

### 8.1 用户授权与边界

执行前一次性申请并获授权 5 项（快照范围、POS 资产只读、报告位置、§5.3 注入法、`kernel_params.json` 仅 visa）；进程泄漏事件另经一次授权后处理。**全部实际操作未超出上述范围**；未改动 POS 仓库任何文件，未改动本仓库任何现有源码与生产案例。

### 8.2 与方案不一致的执行方式（均已标注，非隐瞒）

| 方案要求 | 实际做法 | 理由 |
|---|---|---|
| §4.1 手工删 `runs/.host.lock` | 交给 `acquire_lock()` 自动清死锁 | 等价且更安全；实测日志已留证 |
| §4.2 改 `mock_common.py` 为 1.1 | 独立握手探测脚本 | 避免改动仓库文件；覆盖 1.1/缺失/1.2/1.3 四种 |
| §4.9 `--interactive` | `--delay 9999` | 不依赖控制台 stdin |
| §4.9/§5.5/§6.5 改动 `cases/` 或临时放入案例 | 一律用 `tmp/v12test/cases/<name>` 独立 cases 根 | 生产案例零改动 |
| §5.2 复制 `mock_pos.py` 发两次 | 自建带独立 reader 的客户端 | stock mock 因 F1 无法观测 ack |
| §5.3 `attrib +R` | 目录占位法（同名目录使 `os.replace` 失败） | Windows 下目录只读属性不阻止其中新建文件；经用户确认 |
| §0.2 全目录快照 | 源码+cases+docs 快照 | 经用户确认；排除 logs/frontend 依赖/tmp/runs |

### 8.3 执行者自身问题的处置（透明记录）

1. **自追加死循环**：首轮 L1-3 用 `Select-String -Path $log | Tee-Object -Append $log`，读同一个文件同时把结果追加回去，日志涨到 76 MB 并被 120 s 超时中断。已删除该文件、改为「命令输出→独立文件，grep→控制台」，重跑通过。
2. **误建目录**：一条命令的路径拼写错误创建了空的 `etmp/`，确认无文件后删除。
3. **进程泄漏**：`stop_all()` 中 `Proc.stop()` 对已停止进程二次调用抛 `ValueError`，异常中断循环使 2 个 host 与 2 个 mock 残留，占用 8000/9100 与锁，导致后续用例被**上一个泄漏 host** 接管而误判。已按 PID 精确清理（经授权，仅限本会话子进程），并加固 harness：
   * `Proc.stop()` 幂等；
   * `stop_all()` 逐个 `try/except`，单个失败不跳过其余；
   * 每个用例开始前 `cleanup_stragglers()`，仅清理「命令行含本 harness 标记且父进程已消失」的孤儿进程；
   * 加严后重跑 L4 全套：**9/9 通过，轮末无任何残留进程/端口/锁**（已实测：仅剩用户的 openviking-server 与两个 VS Code 语言服务器）。

---

## 9. L5 / L6 阻塞状态

**未执行**，依据方案 §7.1 的硬性前置：v1.2 为硬切换，两端必须各自升级并发出 `[READY]`，否则只会得到 `protocol_mismatch`，不构成任何有效结论。

* 卡端（card_simulator）需：握手带 `protocol_version:"1.2"`；不再期待 `device_config`；从 `card_config.trace_enabled` 读 trace 开关；新增 `session_abort` 必 ack；坏帧单帧跳过不拆链。
* POS 端（L2-SELFTEST-APP）需：握手带 1.2；实现 `pos_kernel_config`（`updateAidParam`/`updateCAPKParam`/`setTerminalParam`）；`case_started` 必 ack；`transaction_result` 视为落盘确认；新增 `session_abort` 必 ack；运行时断线/超时按 FAIL 处理。
* **解除条件**：两端升级完成并各自 `[READY]` → 过 §7.2 preflight（`devices.android.connected` 与 `devices.pos.connected` 均为 true 且 `app_version` 与期望版本一致）→ 由统筹发起，本轮不代跑。
* 已可复用的准备：`cases/visa/kernel_params.json` 已就位（L5 下发内核参数所需）；§7.3/§7.4 的案例（`VISA-CL-001/002`、`UPI-CL-002`）在案例库中均在列。
* §7.2 提示已核实：`/api/health` **不返回** `protocol_version`；判断 1.2 需看 host 日志 `handshake ok: ... protocol=1.2`（本轮已实测该行存在）。

---

## 10. 需明确排除的「伪缺陷」（§8.3 复核）

| 方案列的伪缺陷 | 本轮是否出现 | 说明 |
|---|---|---|
| pytest `WinError 5` | **未出现** | 本会话非受限沙箱 |
| `CTRL_BREAK_EVENT` 相关失败 | **未出现** | 有控制台；`CTRL_BREAK` 停止 host 退出码 0、`bye` 正常 |
| 终端中文乱码 | 部分 `*.log` 出现 | 已归为环境；落盘 JSON 均 UTF-8 |
| `runs/` 下空目录/残留 `pt_*` | 未产生 | 未使用 `--basetemp` |
| 文档仍写 v1.1 | 未核查（P3 未做，属预期） | 超出本轮范围 |
| 前端仍用 `kind=selected` 跑组 | 未核查（T17 未做，属预期） | 超出本轮范围 |

---

## 11. 最终结论

1. **通过率**：L1 4/4；L2 130/130（含 §3.2 22/22 红线、§3.4 7/7 边界）；L3 13/13（A–K + M + §4.2 硬切换）；L4 9/9（§5.1–5.6）；T-D0 5/5（§6.1–6.5）。
   **合计 26 个场景、187 项断言全部通过，0 个场景失败。**
2. **P0/P1**：**无**。未发现数据损坏、崩溃、静默误判，也未发现「应 FAIL 却 PASS」「FAIL 后未停轮」「`session_abort` 未发」「`pos_not_connected` 仍为 warn」等方案列为 P1 的现象。
3. **P2×2 / P3×1**：均为**可观测性与字段/schema 一致性**问题（F1 mock ack 死锁、F2 `abort_reason` 缺案例号、F3 `attempt`/`attempts` 分裂），最小复现见 `DEFECTS.md`。其中 **F1 使方案两处验收观测点不可达**，建议优先修（改法：`MockClient` 把 `on_message` 派发到独立 task，或把 ack 等待移出 reader 循环）。
4. **方案文档需修订 7 处**（P-D1…P-D7），其中 **P-D1（§6.5 示例不是坏数据、失败路径不可达）** 与 **P-D2（§6.2/§6.3 自相矛盾）** 会直接影响后续轮次的验收判定，建议在本轮结论固化时一并更正。
5. **L5/L6 阻塞**：待两端升级至 1.2 并各自 `[READY]`；解除条件见 §9。
6. **本轮结论不覆盖 P2/P3 未实现部分**：`tools/export_kernel_params.py`（已确认不存在）、`tools/gen_cases.py` 的 kernel_params 生成、前端 T14–T18、`tools/joint_debug` 的 T20，以及 `docs/protocol.md`/`README`/`CHANGELOG`/对端通知文档的 1.1→1.2 更新，**本轮一律未测试、未评价**。
