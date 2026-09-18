# 测试文档：协议 v2 P1c-1 修复验证（Round 2）

> 依据：`docs/test-report-v2-p1/REPORT.md`（F1/F2/F3）；前置与执行环境同 `docs/test-plan-v2-p1.md`。
> 请在**普通终端**（非 DSH 沙箱）执行并把报告回传。

## 0. 本轮修了什么

| 项 | 修复 |
| --- | --- |
| **F1** | `tools/gen_cases.py` 重命名撞名。**实际有 9 个函数撞名**（报告只发现第 1 处：`ast.parse` 不检测重复形参）。已按裁定把「品牌定义 dict」形参统一改名为 **`brand_conf`**（含函数体引用与 9 处调用点语义不变），`: dict` 注解保留。 |
| **F2** | `tests/test_runner_result.py`：`test_unattributed_out_is_quarantined` 落盘行数断言 `== 2`  **`== 1`**（无 session 不落盘是既有设计，见报告 3）。 |
| **F3** | `tests/test_launch.py` / `tests/test_shutdown.py`：真实子进程 stdout 由 `PIPE`（未排空）改为**文件重定向**，保留早退诊断。 |
| 附加 | `tests/test_frames.py` 新增 **信封 `v=2`** 断言；`executor._pos_signal` 区分 `*_ack_timeout`（对端未答，`ConnectionClosed`）与 `*_error`（本地/编程/传输异常），修正报告 5.3 的 reason 混用。 |

> 说明：本会话沙箱无法运行 pytest（DSH 沙箱对 Python 自建目录枚举报 WinError 5）。
> 本地仅做了 `compile()` 语法校验（**不再用 `ast.parse`**，它漏检重复形参）。

## 1. 命令（普通终端）

```powershell
cd E:\Work\ZT\card_simulator_host\backend

#  聚焦子集（上轮 5 failed，本轮应为 0 failed）
python -m pytest -q --ignore=tests/test_runner_timeout.py --ignore=tests/test_v12_fixes.py

#  全量（应只剩 F4 的 14 个 v1.2 残留）
python -m pytest -q
```

## 2. 预期

-  **0 failed / 0 error**（F1 的 2 个 + F2 的 1 个 + F3 的 2 个应全消）；
-  失败**仅**收敛在 `tests/test_runner_timeout.py`（8）与 `tests/test_v12_fixes.py`（6）
  这是 **F4 / P1c-2 待迁移**（两个文件的 `StubProtocol` 缺 `send_to_and_ack_signal`，
  并按 v2 帧重写断言），不是回归；
- 若  仍有失败：那是本轮修复未覆盖，请附完整 traceback。

## 3. 请回传

1.  的 `-q` 汇总行（`N passed / M failed`）；
2.  的 `-q` 汇总行；
3.  的失败清单（`文件::用例名`）确认仍是 `test_runner_timeout` + `test_v12_fixes`；
4.  若有失败：失败用例名 + 完整 traceback。

## 4. 仍待你裁决 / 未做（不在本轮测试范围）

- **F4 / P1c-2**：迁移 `test_runner_timeout.py` / `test_v12_fixes.py` 到 v2；`docs/protocol.md` 重写为 v2；前端 v2 展示（`out.ops.status` + APDU）。
- **F3 附带**：启动期 per-config INFO（87 行、约 29 KB）是否降级为 DEBUG**属日志治理，请裁决**。
- **信封 `v` 接收侧校验**：目前仅发不收（无 `frame.get("v")` 校验）；是否要在服务端拒绝 `v != 2` 的入站帧**属行为变更，请裁决**。
