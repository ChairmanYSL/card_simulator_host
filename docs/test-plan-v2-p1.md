# 测试文档：协议 v2 P1（host CTEL2 核心）验证

> 用途：本会话沙箱无法运行 pytest（DSH 沙箱对 Python 自建目录的枚举报 WinError 5），
> 请在**普通终端**（不经 DSH 沙箱）执行下列命令，并把报告回传。
> 对应代码：`13195f6`（P1 核心）+ 本次 P1c-1（用例断言迁移 / `test_runner_result.py` 迁移 / 测试文档）。

## 0. 前置

- 仓库：`E:\Work\ZT\card_simulator_host`，测试在 `backend\` 下跑。
- 环境：Python 3.14 + pytest 9.1 + pytest-asyncio + anyio（沿用现有环境）。
- 不需要启动 host / 卡端 / POS，不需要设备。

## 1. 聚焦验证（**应全绿**）

```powershell
cd E:\Work\ZT\card_simulator_host\backend
python -m pytest -q --ignore=tests/test_runner_timeout.py --ignore=tests/test_v12_fixes.py
```

**预期**：`0 failed / 0 error`。该子集覆盖：

- 协议信封 `v=2` / 握手 `protocol_version="2.0"`：`test_protocol_server.py`、`test_launch.py`；
- CTEL2 OUT：判定+落盘+ack 语义+msg_id 去重+unattributed quarantine：`test_runner_result.py`（本次已改为 v2）；
- 用例断言路径 `out.datarecord.*` 迁移：`test_loader_store.py` / `test_api_cases.py` / `test_asserter.py` 等；
- 其余既有单测（帧格式、导出工具、DF05、shutdown、run_loop_crash）。

## 2. 全量验证（记录已知 v1.2 残留失败）

```powershell
cd E:\Work\ZT\card_simulator_host\backend
python -m pytest -q
```

**预期**：

- 除 `tests/test_runner_timeout.py`、`tests/test_v12_fixes.py` 外应全部通过；
- 这两个文件仍按 **v1.2** 断言：调用已删除的 `_on_transaction_result`、等待
  `case_started` / `transaction_result` / `pos_config` / `pos_kernel_config` / `session_abort`。
  这些失败是**已知待迁移项（P1c-2）**，不是回归。
- 例外：若 1 子集里出现失败，那才是真正要马上修的。

## 3. 请回传的内容

1. 1 的 `-q` 汇总行（`N passed / M failed / K error`）；
2. 2 的 `-q` 汇总行；
3. 2 的失败清单（`文件::用例名` 一句话列出即可）+ 任意 12 条完整 traceback（确认都是 v1.2 残留）；
4. 1 若失败：失败用例的完整 traceback（这是要立即修的）。

## 4. 本轮明确不在测试范围

- 卡端 APDU 判定（`card.gpo.pdol.*` / `card.gac.cdol1.*`）P2，尚未实现；
- 前端（RunPage/History/AddCase）v2 展示P1c-2；
- `docs/protocol.md` v2 重写P1c-2；
- Mastercard POS 配置 + CAPK 入库P4。
