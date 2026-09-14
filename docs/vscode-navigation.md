# VS Code 代码导航配置（card_simulator_host）

> 目标：在这个工程里用 VS Code 读代码时，**跨文件/文件内符号查找、跳定义、查引用**全部可用。
> 配置落在 [.vscode/settings.json](../.vscode/settings.json)，扩展推荐在 [.vscode/extensions.json](../.vscode/extensions.json)。

---

## 1. 你要的四个操作

| 需求 | 操作 | 说明 |
|---|---|---|
| 全局查找函数符号 | `Ctrl + T` | 菜单名 **转到工作区中的符号…**（英文源的字符串是 `Go to Symbol in Workspace...`）。**输入方法名本身，不要带类名**——原因见第 2 节 |
| 文件内查找局部函数符号 | `Ctrl + Shift + O` | 菜单名 **转到编辑器中的符号…**；输入 `@` 只列符号，输入 `:` 可按行号跳转 |
| 点函数跳转看定义 | `Ctrl + 左键`，或光标处 `F12` | 菜单名 **转到定义**；`Alt + F12` 是速览（Peek）定义，不离开当前文件 |
| 右键查引用并跳转 | 右键 → **转到引用**（`Shift + F12`） | 弹出引用列表，点条目即跳转；`Alt + F12` 式速览为 **查看引用** |

补充几个同类常用项（右键菜单「快速查看」子菜单内）：**转到实现**、**转到类型定义**、**转到声明**。

> 命名提示：VS Code 内置的右键项叫「转到引用 / Go to References」，命令 ID 是 `editor.action.goToReferences`；
> 在 VS Code 里搜「查找所有引用 / Find All References」命中的是另一个东西——侧边栏的
> **引用搜索结果**视图（`references-view.findReferences`）。两者都能列引用并点击跳转，本文档的配置对两者同时生效。

---

## 2. `Ctrl + T` 的匹配规则（别输入 `类名.方法名`）

**搜不到的东西一样能用 `Ctrl + T` 找到，前提是输入对的字符串。** 规则来自 pyright 的实现，不是猜测：

Pylance 的文件索引会把**类的方法也收进索引**（`SymbolIndexer.indexSymbols` 递归收集
Class / Function 的成员，且索引时不按可见性过滤），但检索时是拿 **`symbol` 自己的 name** 去匹配的：

```js
// pyright.bundle.js — WorkspaceSymbolProvider.appendWorkspaceSymbolsRecursive
if (stringUtils.OP(this._query, symbolData.name)) { /* 命中 */ }
// containerName 只用于展示，不参与匹配
```

而 `OP` 就是 `isPatternInSymbol`，**纯子序列匹配**（大小写不敏感，每个字符按顺序出现即算命中，不要求连续）：

```js
function isPatternInSymbol(typedValue, symbolName) {
  // 逐个吃掉 typedValue 的字符，symbolName 用完即止
  return typedPos === typedLength;
}
```

直接按代码走一遍 `RunExecutor._judge`：

| 你输入的 | 匹配对象 | 结果 |
|---|---|---|
| `RunExecutor._judge` | 方法名 `_judge`（7 字符） | **失败**——`R`,`u`,`n`… 在 `_judge` 里吃不到，队列没走完就耗尽 |
| `_judge` | 方法名 `_judge` | **命中**，直接列出 `executor.py:658` |
| `judge` | 方法名 `_judge` | 命中（`_` 被跳过，其余按序命中） |
| `_jdg` | 方法名 `_judge` | 命中（子序列，不要求连续） |
| `exejudge` | 方法名 `_judge` | 失败——`e`、`x`、`e` 在 `_judge` 中不存在，队列耗尽即失败 |

结论与对策：

- **输入方法名本身**（`_judge` / `judge` / `jdg`），不要输入 `类名.方法名`。
- 也叫不到带前缀的写法：`RunExecutor._judge`、`executor._judge`、`emv_host.runner.executor._judge` 全都不行。
- 因此 `Ctrl + T` 只能检索**裸符号名**；限定到某个类或文件要另想办法（见下）。
- 注意子序列匹配比较宽松，单字母查询会命中大量无关符号（`jdg` 这种就比 `j` 好得多）。

### 如果想让 `Ctrl + T` 直接命中带类名的写法

这是 `Ctrl + T` 的固有行为（不是本工程的配置问题，改设置解决不了），现实选择只有把方法改名，
让名字本身带上语义。本工程 `_judge` 的情况：全仓只有 3 处提及，且**只有一处定义**
（`backend/emv_host/runner/executor.py:658`）：

```
executor.py:639    return self._judge(session, case, frame)     # 调用
executor.py:658    def _judge(self, session, run: ...)           # 定义
executor.py:698    # PASS/WARN/FAIL already persisted by _judge   # 注释
```

所以改名成本极低，例如 `_judge` → `_judge_case`，之后 `judge`/`judgecase`/`jc` 都能命中。
**这一步会改动源码，需要你明确同意后我才做。**

在决定改名之前，下面这些定位手段不需要动代码：

| 想定位到 | 用什么 |
|---|---|
| 某个类里的方法 | 打开该文件 → `Ctrl + Shift + O` → 输入方法名（编辑器符号只列本文件，天然按类归属） |
| 全仓同名符号在哪 | `Ctrl + T` 输入名字片段，靠结果里的**文件路径列**区分 |
| 方法被谁调用 | 光标停在方法名 → `Shift + F12`（转到引用） |
| 看类的全貌 | 「资源管理器」下方 **大纲（Outline）** 视图，按类折叠列出成员 |
| 按名字模糊找任意文件里的符号 | `Ctrl + P` → 输入 `#` 前缀进入同一套工作区符号搜索 |

---


---

## 3. 为什么需要额外配置（本工程的坑）

工程没有 `.vscode/` 目录、没有任何 Pylance 配置，而且**没有 `pip install`**：

- 包的真身在 `backend/emv_host/`，仓库根目录的 `emv_host/__init__.py` 只是一个 **零安装 shim**，
  它在导入时把 `backend` 插到 `sys.path` 最前面，再把真包换进来。
- 运行时靠 `card_simulator_host.bat` 里的 `set "PYTHONPATH=%ROOT%backend"` 生效。
- 但 **Pylance 不会执行那段 shim 逻辑**。不告诉它 `backend` 的位置，
  它就无法解析 `from emv_host.runner.executor import RunExecutor` 这类导入，
  于是 `Ctrl + 左键` 会跳不动（或退化成纯文本搜索），`Shift + F12` 也列不全。

所以 `settings.json` 里的关键是这一条：

```jsonc
"python.analysis.extraPaths": ["${workspaceFolder}/backend"]
```

### 已实测验证（在本机用 `PYTHONPATH=backend` 复现 Pylance 的解析路径）

```
emv_host  -> E:\Work\ZT\card_simulator_host\backend\emv_host\__init__.py
executor  -> E:\Work\ZT\card_simulator_host\backend\emv_host\runner\executor.py
pytest    -> 80 tests collected in 0.93s
```

即 `backend` 加到搜索路径后，导入解析到的是真包、且不产生重复模块，`extraPaths` 这一项是正确解法。

---

## 4. 配置项逐条说明

### 包解析

```jsonc
"python.defaultInterpreterPath": "C:\\Python314\\python.exe",
"python.analysis.extraPaths": ["${workspaceFolder}/backend"],
"python.analysis.autoSearchPaths": false
```

- 本工程**没有 venv**，依赖装在系统 Python（`C:\Python314\python.exe`，已确认 `fastapi 0.141.1` / `uvicorn 0.52.4` / `pydantic 2.12.5` / `websockets 15.0.1` / `pytest 9.1.1` 齐全）。
- 若你换了 Python 安装位置，改这一行即可。

### 索引范围（决定 `Ctrl + T` 和「转到引用」能搜到什么）

```jsonc
"python.analysis.include": [
  "${workspaceFolder}/backend",
  "${workspaceFolder}/emv_host",
  "${workspaceFolder}/tools"
]
```

全仓 `.py` 共 **41 个**，全部落在这三个目录内；`frontend/` 和 `tmp/` 里 `.py` 数量为 0，所以排除它们不会漏掉任何符号。

```jsonc
"python.analysis.indexing": true,          // 符号索引，Ctrl+T / 查引用 依赖它
"python.analysis.persistAllIndices": true, // 索引落盘，重开窗口免重建
"python.analysis.useLibraryCodeForTypes": true, // fastapi / pydantic 等三方库也能跳
"python.analysis.includeExtraPathSymbolsInSymbolSearch": true
```

最后一条是 Pylance 的开关，官方说明为「Include symbols from `python.analysis.extraPaths` in
Workspace Symbol search」，**默认 `false`**，即默认情况下 `extraPaths` 里的符号**不进** `Ctrl+T`。
本工程的 `backend` 已经同时写在 `python.analysis.include` 里，所以功能上并不依赖它；
打开它是防御性的：万一以后 `include` 被改动，`Ctrl+T` 仍覆盖 `backend`。代价是符号检索会稍慢
（官方注明 “may slow down Workspace Symbol search”），本仓 41 个文件可忽略。

> 另有一条同类开关 `python.analysis.includeVenvInWorkspaceSymbols`（默认 `false`），
> 本工程无 venv，不涉及，故未设置。

### 排除高噪声目录

`logs/` **725 MB**、`frontend/node_modules/` 59.5 MB、`tmp/` 45 MB（内含 Android 卡端 fork），
这三处对 Python 代码导航没有价值，但会被文件监听器盯着，所以：

```jsonc
"files.watcherExclude": {
  "**/logs/**": true, "**/logs_tmp*/**": true,
  "**/runs/**": true, "**/runs_tmp*/**": true,
  "**/tmp/**": true,
  "**/frontend/node_modules/**": true, "**/frontend/dist/**": true,
  "**/__pycache__/**": true, "**/.pytest_cache/**": true
}
```

`python.analysis.exclude` 里也写了同样的目录，作为防御性配置（以后若在这些目录新增 `.py` 也不会被卷进索引）。

### 诊断策略

```jsonc
"python.analysis.diagnosticMode": "openFilesOnly",
"python.analysis.typeCheckingMode": "basic"
```

按「导航优先」调优：只对打开的文件做诊断，避免全库静态检查的耗时与刷屏。
**代价**：「问题」面板不会列出未打开文件里的问题。若要全库体检，临时改成 `"workspace"` 即可。

---

## 5. 改完怎么生效 / 怎么自查

1. **改完必须重载窗口**：`Ctrl + Shift + P` → `Developer: Reload Window`。
   （`.vscode/settings.json` 是启动时读取的，不重载不生效。）
2. 打开 `backend/emv_host/runner/executor.py`，看右下角语言指示器是否为 **Python**。
3. 按 `Ctrl + Shift + P` → `Python: Select Interpreter`，确认选中的是 `C:\Python314\python.exe`。
   如果被选成了别的解释器，`defaultInterpreterPath` 不会覆盖已保存的选择。
4. 逐项验证（例子里的符号都已确认存在于本仓）：
   - `Ctrl + T` → 输入 `RunExecutor`（`backend/emv_host/runner/executor.py:57`）或 `encode_frame`
     （`backend/emv_host/protocol/frame.py:39`），应列出跨文件结果；
   - `Ctrl + T` → 输入 `_judge`，应列到 `executor.py:658` 的 `RunExecutor._judge`；
     再输入 `RunExecutor._judge`，会**搜不到**——这是第 2 节的固有行为，不是配置坏了；
   - 打开 `backend/emv_host/protocol/frame.py`，`Ctrl + Shift + O` 应列出该文件内的
     `ProtocolError` / `FrameTooLargeError` / `FrameDecodeError` / `encode_frame`；
   - 打开 `backend/emv_host/runner/executor.py`，`Ctrl + 左键` 点第 24 行导入的 `assert_result`
     （来自 `..judge.asserter`），应跳到 `judge/asserter.py` 的定义；点第 36 行的 `make_frame`
     应跳到 `protocol/messages.py`；
   - 对着 `assert_result` 右键 → **转到引用**，应列出定义处（`judge/asserter.py:123`）+ 调用处
     （`runner/executor.py:674`）+ 测试里的多处调用（`tests/test_asserter.py`），点击可跳转。

### 排障速查

| 现象 | 处理 |
|---|---|
| `Ctrl + T` 搜方法时「什么也搜不到」，但符号明明存在 | 十有八九输入了 `类名.方法名`。只输入方法名本身，见第 2 节 |
| `Ctrl + 左键` 跳不动、或提示「找不到定义」 | 先看右下角语言指示器是不是 Python；再 `Ctrl + Shift + P` → `Developer: Reload Window` |
| 改了配置没反应 | 没重载窗口；或该文件不在 `python.analysis.include` 范围内 |
| `Ctrl + T` 结果为空 / 只有当前文件 | `Ctrl + Shift + P` → `Python: Restart Language Server`，等状态栏索引跑完 |
| 右键没有「转到引用」 | 光标必须停在一个符号上；且当前文件的 provider 未就绪时该项会灰掉 |
| 索引很慢 | 检查 `logs/`、`frontend/node_modules/`、`tmp/` 是否被误加进了 `python.analysis.include` |

### 与运行/测试的关系（配置不影响运行）

Pylance 的 `extraPaths` **只服务编辑器**，不改变运行行为。所以：

- 起服务照旧用仓库根目录的 `card_simulator_host` / `.bat`（脚本内部自己设 `PYTHONPATH=backend`）；
- 若要在 VS Code 集成终端里手工跑，先设环境变量：
  - PowerShell：`$env:PYTHONPATH="$PWD\backend"; python -m emv_host --help`
  - Git Bash：`PYTHONPATH=backend python -m emv_host --help`
- 跑测试要 `cd backend` 再 `python -m pytest`（`pytest.ini` 在 `backend/`，`testpaths = ["tests"]`）。

---

## 6. 环境基线（实测）

| 项 | 值 |
|---|---|
| VS Code | `1.137.0`，安装在 `E:\Microsoft VS Code` |
| Pylance | `ms-python.vscode-pylance 2026.2.1`（要求 VS Code `^1.107.0`，满足） |
| Python 扩展 | `ms-python.python 2026.4.0` |
| 解释器 | `C:\Python314\python.exe`（Python 3.14.5） |
| 界面语言 | `argv.json` 里 `"locale": "zh-cn"` |

> 顺带一提：中文语言包当前是 `1.131.2026090407`，与 VS Code `1.137.0` 存在版本差，
> 较新的界面字符串（例如「转到工作区中的符号…」）可能仍显示英文——这是语言包版本问题，
> 不影响功能；在扩展面板把中文语言包更新到 1.137 对应版本即可消除。
