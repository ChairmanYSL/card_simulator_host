# 卡端变更通知：MSD（磁条）模式支持

> 影响端：**卡端 Android app**（HCE 卡模拟）。交付：本通知由 host 侧生成，只读交付；
> host **不修改卡端仓库**。
> 触发案例：`3G06-1241-Case07` / `3G06-1241-Case08`（参考日志
> `E:\Work\KSAPI\ksapi-entrypoint-arch-prj\Log\3G06-1241-Case07-08.txt`）。
> 现状：**卡端不支持 MSD**，这两个案例目前只能落到 `FAIL/超时`；本文档给出实现口径。

## 1. 为什么需要（日志事实）

该日志是**一个 ACT + 两次拍卡**，两笔都走 **Kernel 2 磁条(MSD)模式**：
`AIP=0200` → 终端判定 `EMV mode not supported by card (AIP Byte2 bit8=0)`，
终端 kernel_config=0x20（终端支持 EMV+MSD+CDCVM）→ **选 MSD 模式**。
两笔的卡端交互逐字节如下（`<apdu send>` / `<rapdu>`）：

| 步骤 | 终端 → 卡 | 卡 → 终端 |
|---|---|---|
| SELECT PPSE | `00 A4 04 00 0E 325041592E5359532E4444463031 00` | `6F 3D 84 0E 325041592E5359532E4444463031 A5 2B BF0C 28 61 0C 4F 07 A0000000041010 87 01 01 61 0C 4F 07 A0000000043060 87 01 02 61 0A 4F 05 B012345678 87 01 03 90 00` |
| SELECT ADF | `00 A4 04 00 07 A0000000041010 00` | `6F 1A 84 07 A0000000041010 A5 0F 50 0A 4D415354455243415244 87 01 01 90 00`（**无 9F38 PDOL、无 BF0C**） |
| GPO | `80 A8 00 00 02 83 00 00` | `77 16 82 02 0200 94 10 08010100 10010101 18010100 20010200 90 00` |
| READ RECORD | `00 B2 01 0C 00`（SFI 1/1） | `70 24 9F65 02 000E 9F66 02 0E70 9F6B 13 5413339000001513D49121019000990000000F 9F67 01 03 90 00` |
| **MSD AC** | `80 2A 8E 80 04 00 00 02 31 00` | Case07：`77 0B 9F36 02 0105 DF4B 03 000100 90 00`<br>Case08：`77 10 9F61 02 EB85 9F36 02 0105 DF4B 03 000000 90 00` |

两笔只差最后一步的 `DF4B` / `9F61`：
`DF4B[1]&0x03` ≠ 0 → 内核要求**二次拍卡**（`END APPLICATION` + `Start=B`）；
否则走 CVC3 路径，金额 10.01 > CVM 限额 10.00 → 内核报**卡片数据错误**（`DF8115 L2=6`）。

## 2. 需要实现的行为

卡端在 **profile 含任一 `msd*` 字段** 时进入 MSD 流程（`acBehavior` 保持现值即可，MSD 下不使用）：

1. **SELECT PPSE**：按 `aids[]` 返回候选列表（`BF0C{61{4F,87}}`，优先级取 `priority`）；
2. **SELECT ADF**：FCI = `6F{84 <aid>, A5{50 <applicationLabel>, 87 <priority>}}`；
   **profile 无 `pdol` 时不得带 `9F38`**，也不要带 `BF0C`；
3. **GPO**：无 PDOL → 收到 `83 00` 即应答 `77{82 <aip>, 94 <afl>}`（**不得因缺 PDOL 报错**）；
4. **READ RECORD**：按 AFL 提供记录；MSD 模式只读 **AFL 首项**（日志只读 SFI 1/1）；
5. **MSD AC**（`CLA=80 INS=2A P1=8E P2=80`，命令名注册表 **`CCC`**，别名 `MSD_AC`）：应答
   `77{9F61?, 9F36, DF4B?}`：`9F36` **必发**（2B，取卡端 ATC）；`msdCvc3Track2` 为空串时**不发 `9F61`**；
   `msdPosInteractionInfo` 为空串时**不发 `DF4B`**。TLV 顺序按 §1 日志（Case07：`9F36 → DF4B`；
   Case08：`9F61 → 9F36 → DF4B`）—— 功能上内核按 tag 查表、顺序无关，但验收口径是
   "与 §1 逐字节一致（ATC 除外）"（2026-10-10 协商项 D 定稿）。
   MSD 模式下不会收到 `80 AE`（GENERATE AC）。

## 3. 新增 profile 字段（camelCase，与现有 token 同风格）

| 字段名 | tag | 格式 | Case07 | Case08 | 出现位置 |
|---|---|---|---|---|---|
| `msdMode` | —（不落线） | `"msd"` / `"emv"` / 缺省=自动 | `msd` | `msd` | profile 顶层（2026-10-10 协商项 A：显式开关，缺省行为=本通知原口径"任一 `msd*` 非空即 MSD"） |
| `msdPcvc3Track2` | `9F65` | b，2B | `000E` | `000E` | READ RECORD SFI 1/1 |
| `msdTtq` | `9F66` | b，2B | `0E70` | `0E70` | 同上 |
| `msdTrack2Data` | `9F6B` | **ASCII track2 的 hex**（同 `track2EquivalentData` 口径：`'='`→`'D'`，奇数长度补 `'F'`；不得直接写 BCD） | `353431333333393030303030313531333D3439313231303139303030393930303030303030` → BCD `5413339000001513D49121019000990000000F` | 同左 | 同上 |
| `msdNatcTrack2` | `9F67` | b，1B | `03` | `03` | 同上 |
| `msdPosInteractionInfo` | `DF4B` | b，3B | `000100` | `000000` | MSD AC 应答 |
| `msdCvc3Track2` | `9F61` | b，2B；空串=不发 | `""` | `EB85` | MSD AC 应答 |

记录布局：`records: [{sfi:1, record:1, fields:[msdPcvc3Track2, msdTtq, msdTrack2Data, msdNatcTrack2]}]`（顺序即日志顺序）。

## 4. 验收（host 侧已就绪）

1. 用 `cases/mastercard/contactless/cases/3G06-1241-Case07.json` / `…Case08.json` 跑（配置 `PPS_MChip1`）；
2. 卡端按第 1 节表格**逐字节**应答；APDU trace 必须逐条上送（v2 既有要求），
   host 已把 MSD AC（INS `2A`）纳入 `card.gac`：`card.gac.mode = "msd"`、`card.gac.response` 可见 `DF4B`/`9F61`；
3. 期望 OUT（host 断言，取自日志）：

| | Case07 | Case08 |
|---|---|---|
| OPS | `40 10 F0 30 70 F0 FF 00`（END APPLICATION / Start=B / ui_on_restart / DataRecord / DiscData） | `40 F0 F0 F0 90 F0 FF 0D`（END APPLICATION / ui_on_outcome / DiscData） |
| DataRecord | `50="MASTERCARD" 84=AID 9F6D=0001 9F6B=<track2>` | 无（`data_record_present=0`） |
| DiscData | `DF812B=9000990000000F` + `DF8115=0000000000FF` | `DF812B=9000990000000F` + `DF8115=**00060000001C**`（L2=6） |
| UIRD | 无 | `1C000000130000000000000000` |

4. 自测口径：卡端只需保证**发出去的 APDU 与第 1 节一致**；OUT 由 POS 决定（host 只判 POS 的 OUT）。
   两个案例把这一点落成可判定规则：`card.gac.response` 用 **regex** 锁 TLV 顺序与固定字段
   （Case07 `^770B9F3602[0-9A-F]{4}DF4B030001009000$`、Case08 `^77109F6102EB859F3602[0-9A-F]{4}DF4B030000009000$`，
   ATC 通配）——顺序不一致即 FAIL。

## 5. 易错点

- `AIP` 必须是 `0200`：byte2 bit8=0（卡不支持 EMV 模式）才会被终端选成 MSD；写成 `0280` 之类会走 EMV 模式，案例必失败；
- FCI 千万不能带 `9F38`，否则 GPO 会索取 PDOL 数据，命令就不再是 `83 00`；
- `msdTrack2Data` 是 **ASCII 的 hex**（与 `track2EquivalentData` 同口径），不是 BCD；打包后应等于 `5413339000001513D49121019000990000000F`（末字节 `0F` 来自奇数位补 `F`）；
- `DF4B` 与 `9F61` **不能省**：它们决定内核走"二次拍卡"还是"卡片数据错误"两条不同路径；
- ATC 由卡端自增即可（日志为 `0105`，host 不断言其值）；`9F36` 长度固定 2B。

## 5.1 卡端侧已知边界（2026-10-10 回复，host 认可）

- **长度校验**：CCC 应答的 `9F61`/`DF4B` 若配了但长度不对 → 回 `6700`（不静默省略）。
  认可：静默省 `9F61` 会把 Case08 的路径悄悄换成 Case07 的路径，回错码更安全；
- **不做读取限制**：MSD 只读 AFL 首项是**终端**行为，卡端只按 `(sfi, record)` 应答；
  本案例 AFL 后三项未配置记录，若终端去读会得 `6A83` —— 与案例库 warn 一致，属预期（不臆造记录）；
- **不计算真实 CVC3**：`9F61` 取自档案。本两案例不校验 CVC3（Case08 的错误来自金额超
  CVM 限额 `DF21`，不是 CVC3 比对）；若将来用带 MSD 密钥的配置跑其它 MSD 用例，需另议；
- **PPSE 目录省 `50`**（协商项 C，host 接受）：与 §1 日志逐字节一致；ADF FCI 的 `50` 仍输出，
  终端取应用名不受影响；
- **非 MSD 档案字节级零变化 / wire 协议零变化**：EMV 分支的兼容补丁保持原样，
  host 侧 `card.gac.mode="msd"` 由 trace 的 `INS=2A` 自行推导，无需卡端新增字段。

## 6. 回报要求

1. 确认第 3 节字段名/格式（如与卡端命名冲突，先回复口径再改）；
2. 实现后在真机跑 `3G06-1241-Case07` / `Case08`，回传两侧日志（卡端 logcat + POS 日志）与本通知第 1 节 APDU 的逐字节比对结果；
3. 升级版本号 + commit。
