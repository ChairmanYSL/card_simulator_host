# POS 机应用开发手册（Android 7.1.2）

> **⚠️ 本文档描述的是 v1.1 口径，尚未逐节升级到 v1.2。**
> v1.2 的 POS 端**权威且完整**的口径请看
> [`docs/protocol-change-notice-v1.2.md`](protocol-change-notice-v1.2.md)（变更通知），
> 协议权威文档见 `docs/protocol.md` **§12**（v1.2 差异节，优先于旧正文）。
> 阅读本文档时请按下列 v1.2 差异自行替换：
>
> | 本文档（v1.1） | v1.2 实际要求 |
> |---|---|
> | 握手 `protocol_version:"1.1"`，缺省宽容 | **必须** `"1.2"`；缺失或 `<1.2` 一律被拒 |
> | `device_config`（若提及） | **已删除**；心跳 10s / 判死 30s / ack 15s 用缺省 |
> | 只有 `pos_config` 需要 ack | **新增** `pos_kernel_config`（AID/CAPK/TERM）与 `session_abort`，二者也必 ack |
> | `case_started` 无需 ack | `case_started` **必 ack**（15s） |
> | `transaction_result` 发出即算上报 | host **先落盘再 ack**；收到 `persisted`/`duplicate` 后**不要重发**，仅 `ok:false` 才重试 |
> | 未 ack / POS 离线仅是告警 | 一律**该案例 FAIL**，并**立即停止整轮** |
> | 无异常停止通知 | 异常收尾双方各收一次 `session_abort`（必 ack） |
>
> **读者**：POS 交易应用开发人员（POS 端）。
> **权威依据**：`docs/protocol.md`（唯一权威协议，v1.2 见 §12；已吸收 H1–H10 / O1–O7 /
> N1–N12）。本手册只讲 POS 端需要实现的部分，若与 protocol.md 冲突，以
> protocol.md 为准。变更索引见 `docs/CHANGELOG.md`。
> **目标系统**：Android 7.1.2（API 25）。

---

## 1. 角色与连接总览

```
┌─────────────────────┐        TCP :9100        ┌──────────────────────┐
│ POS 交易应用（安卓） │◄───────────────────────►│ EMV Test Host（上位机）│
│ 操作员手动操作        │  handshake/ack/心跳/      │ Windows，单端口 9100   │
│ 交易结束自动回传结果  │  pos_config/case_started │  Web/API/WS :8000     │
└─────────────────────┘  transaction_result     └──────────────────────┘
```

- POS 是 **TCP 客户端**，主动连 host 的 **9100** 端口；
- 连接方式二选一：
  - USB 调试：`adb forward tcp:9100 tcp:9100`（设备与电脑同网或 USB 直连）；
  - 局域网：POS 直接连 host 的 IP（如 `192.168.x.x:9100`），仅限隔离测试网络；
- **无 TLS**；token 只在握手时使用，之后凭连接会话身份；
- host 对每种客户端只接受 **一个连接**；同一 `device_id` 重连会**替换旧会话**
  （旧连接被 host 关闭），不同 `device_id` → `already_connected`；
- POS 全人工操作：操作员在 POS 上完成交易，POS 应用在**交易结束后自动回传
  `transaction_result`**，host 按 `attempt_id` 归属并判定用例。

## 2. Android 7.1.2 工程约束

| 项 | 要求 |
|---|---|
| minSdkVersion | 25（7.1.2） |
| 语言 | Java 8 或 Kotlin 均可；`org.json` 为系统自带，无需依赖 |
| 权限 | `<uses-permission android:name="android.permission.INTERNET"/>` |
| 明文 TCP | API 28 之前默认允许 cleartext，7.1.2 无需配置 `networkSecurityConfig` |
| 线程 | 所有 socket 收发必须在**后台线程**（NetworkOnMainThreadException） |
| 保活 | 交易进行中建议持 **PARTIAL_WAKE_LOCK**（或前台 Service），防止 7.x Doze/锁屏断流；应用保持前台 |
| 界面 | 交易中保持屏幕常亮（`FLAG_KEEP_SCREEN_ON`），操作员需看着金额/提示 |
| 超时默认值 | POS 不接收 device_config，使用协议缺省：心跳 10s / 判死 30s / ack 超时 15s |

## 3. 帧格式（锁定）

```
+---------------------+-------------------------+
| 4 字节大端无符号长度 | UTF-8 JSON payload      |
+---------------------+-------------------------+
```

- 长度 = payload 的 **UTF-8 字节数**；最大帧 **1 MiB**，超出 → host 断开；
- payload 必须是 JSON **对象**；单连接累计 **5 个坏帧** → host 断开；
- 每帧信封：`{"v":1,"type":"<消息类型>","msg_id":"<uuid-hex>","ts":<epoch秒>}`；
- POS 发送方注意：确保发出去的每一帧都是合法 JSON 对象（不要发裸字符串/数组）。

## 4. 握手（POS 连接后 10 秒内必须发送）

```jsonc
// POS -> host
{"v":1,"type":"handshake","msg_id":"…","ts":…,
 "client_type":"pos","token":"<tokens.json 里 pos 的 token>",
 "device_id":"<本机唯一标识，如序列号>","app_version":"1.0.0",
 "device_model":"<机型>","protocol_version":"1.1"}

// host -> POS
{"v":1,"type":"handshake_ack","msg_id":"…","ts":…,
 "session_id":"pos-<ts>-<port>","protocol_version":"1.1","server_time":…}
```

错误码（POS 端处理方式）：

| code | 含义 | POS 动作 |
|---|---|---|
| `auth_failed` | token/client_type 错 | 检查 token 配置，提示后**可重连重试** |
| `already_connected` | 已有一台 POS 连接且 device_id 不同 | 提示另一台 POS 已占用，稍后重试 |
| `handshake_timeout` | 10s 内未握手 | 重连重试 |
| `protocol_mismatch` | 版本 < 1.1 | **确定性失败，不要重连**，明确报错升级 |

## 5. 心跳与断线重连（POS 侧 watchdog）

- host 每 **10s** 发 `heartbeat`，POS 必须回 `heartbeat_ack`（见 §6）；
- POS **watchdog**：连续 **30s** 未收到 host 任何帧（含 heartbeat）→ 判死 →
  主动断开 → 按 **1/2/4/8/16/30s 封顶** 指数退避自动重连（同 device_id）；
- 重连成功走新握手（host 会替换旧会话）；
- 交易进行中断线：POS 应**保留本次交易结果**，重连后仍按原 `case_id/attempt_id/
  msg_id` 重发 `transaction_result`（host 按 attempt 归属，仍可能被判定）。

## 6. POS 必须实现的消息（全表）

### 6.1 POS 收（host → POS）

| type | 字段 | POS 动作 |
|---|---|---|
| `config_push`（`pos_config`） | `config_type, case_id, attempt_id, pos_params[{tag,name,value}]` | **先 ack**（应用成功），再把参数填到本次交易的对应栏位（金额/币种/交易类型等，按 tag 解释，见 §8）；记录 case_id/attempt_id |
| `case_started` | `case_id, attempt_id, brand, entry_mode, operator_instructions{amount,currency,entry_mode,pin_hint,notes}` | **先 ack**；显示操作员指引（金额/币种/PIN 提示/备注），进入人工交易；`pos_config` 若已下发则以其参数为准（金额等可自动填） |
| `heartbeat` | `ref_msg_id` | 回 `heartbeat_ack`（任何帧都算存活，但要回心跳） |
| `error` | `code, message, ref_msg_id?` | 记录并提示（如 auth/protocol 错误） |

时序约定：host 在**每用例**按 `pos_config` →（ack）→ `case_started` 顺序下发；
POS 在收到 `case_started` 前收到的 `pos_config` 属于**下一个用例**，先缓存到
`case_started` 到达时一起应用（以 case_id/attempt_id 配对）。

### 6.2 POS 发（POS → host）

| type | 字段 | 是否要等 ack |
|---|---|---|
| `handshake` | 见 §4 | 等 handshake_ack |
| `ack` | `ack_msg_id, ok, detail` | — |
| `heartbeat_ack` | `ref_msg_id` | 不 ack |
| `transaction_result` | `case_id, attempt_id, device_id, result`（§7） | **host 必 ack** |
| `log`（可选） | `level, message, case_id?, attempt_id?` | 不 ack |

### 6.3 ack 与重发（transaction_result）

- POS 发出 `transaction_result` 后**等待 host ack**（超时 15s）；
- 未收到 ack → **用同一个 msg_id** 按 **1/2/4/8/16/30s 封顶** 指数退避重发
  （host 按 msg_id 幂等去重），重试 ≥6 次仍失败 → 记本地日志、保留结果待重连后
  再试一次，并提示操作员；
- 收到 ack 即结束：`detail:"persisted"` = 已被 host 落盘判定；`detail:
  "quarantined"` = 迟到/无主结果已存档（不参与判定），**停止重发**；
- `ack` 帧格式：`{"v":1,"type":"ack","msg_id":"…","ts":…,"ack_msg_id":"<原msg_id>",
  "ok":true,"detail":""}`。

## 7. transaction_result 结果报文（判定唯一依据）

```jsonc
{
  "v":1, "type":"transaction_result", "msg_id":"<新uuid>", "ts":…,
  "case_id":"UPI-CL-007", "attempt_id":"<case_started 原样回显>",
  "device_id":"<本机 device_id>",
  "result": {
    "resp_code":"00", "outcome":"approved", "online":"offline",
    "approval_code":"112233", "cvm":"no_cvm",
    "amount":"100.00", "currency":"156", "entry_mode":"contactless",
    "tags": {
      "gpo":   {"82":"5800", "94":"18010000", "9F36":"0001"},
      "read_record": {"5F20":"5649534120544553542043415244", "5F24":"281231"},
      "gen_ac":{"9F26":"AABBCCDDEEFF0011", "9F27":"8000000000",
                "9F10":"060A03A00000", "9F36":"0001", "5F2A":"0156"}
    }
  }
}
```

字段要求：

- `case_id` / `attempt_id`：**必须原样回显** `case_started` 的值；缺失/过期 →
  quarantine，不判定；
- `resp_code`：交易响应码字符串（如 `00`/`05`/`91`/`80`）；
- `outcome`：`approved` / `declined`（POS 内核真实结果）；
- `online`：`offline` / `online`（是否走在线授权）；
- `approval_code`：授权码，无则 `""`；
- `cvm`：`no_cvm` / 具体 CVM 结果 / **`unknown`**（`unknown` 不算失败）；
- `amount` / `currency` / `entry_mode`：实际交易值（与 operator 指引一致）；
- `tags`：内核能拿到的 tag 按 `gpo` / `read_record` / `gen_ac` 分组，值为 **hex 字符串**；
  拿不到的 tag 不要编造——host 断言用 `present/absent` 会如实判定；
- **永远上报真实结果**，不要为了“通过用例”伪造字段。

## 8. pos_params（交易参数，按 tag 下发）

`config_push{pos_config}` 下发本用例参数，POS 按 tag 解释应用（示例模板）：

| tag | 名称（示例） | 值示例 | POS 侧建议 |
|---|---|---|---|
| `9F02` | amount | `100.00` | 交易金额自动填入 |
| `9F03` | amount other | `0.00` | 现金返还金额 |
| `5F2A` | currency | `156` | 币种（ISO 数字码） |
| `9C` | transaction_type | `00` | 交易类型（00=purchase） |
| `9F1A` | terminal_country | `156` | 终端国家码 |
| `9F33` | terminal_capabilities | `E0F0C8` | 终端能力 |

- tag 为 1–3 字节**大写 hex**（EMV tag）；name/value 为辅助字段，POS 端按
  自己的内核接口解释——**tag 语义由 POS 端映射，与内核字段对齐**；
- 未知 tag：忽略该项并打日志即可（host 允许自定义 tag）。

## 9. 卡片「静默（不响应）」时的 POS 行为（N12 相关）

`profile.noResponseCommands` 会让卡片对某指令（如 GPO）**不发任何响应字节**。

POS 内核应具备的超时处理（联调重点）：

1. 读卡器发指令后设置**响应超时**（建议 500ms–1s 级，按内核可配置）；
2. 超时后按内核策略**有限重试**（如重发 1 次），仍无响应 → **终止本次交易**；
3. 终止后**照常回传 `transaction_result`**，如实填内核给出的终止码/结果
   （例如终止场景 resp_code=`80`、outcome=`declined`/对应取消码——以 POS 内核
   实际返回为准，host 的用例断言按你们约定的值写）；
4. 不要无限重试导致用例超时（host 侧案例默认 180s，超时判 INCONCLUSIVE）；
5. 部分终端/ROM 对“卡片静默”的表现可能有差异，联调时双方核对真实行为
   （见 `docs/N12_上位机确认回复.md`）。

## 10. POS 状态机与生命周期

```
START → CONNECTING（退避重连）
      → CONNECTED：10s 内发 handshake → 等 handshake_ack
      → READY（空闲）：收 pos_config → 缓存参数；收 case_started → ack + 显示指引
      → IN_TRANSACTION：操作员完成交易（含卡片静默超时处理）
      → SENDING_RESULT：发 transaction_result（同 msg_id 退避重发）
      → （收到 ack persisted/quarantined）→ READY
断线/30s watchdog 超时 → 保结果 → CONNECTING（1/2/4/8/16/30s 封顶）
```

## 11. Android 参考实现（Java 8，org.json 内置）

骨架代码（示意，线程模型请按自家架构封装）：

```java
import org.json.JSONObject;
import java.io.*;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.*;

public final class HostLink {
    private final String host; private final int port;
    private final String token; private final String deviceId;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final ConcurrentHashMap<String, CompletableFuture<JSONObject>> acks =
            new ConcurrentHashMap<>();
    private Socket socket; private DataInputStream in; private OutputStream out;
    private volatile boolean running;

    public HostLink(String host, int port, String token, String deviceId) {
        this.host = host; this.port = port;
        this.token = token; this.deviceId = deviceId;
    }

    private static byte[] frame(String type, JSONObject body) throws Exception {
        body.put("v", 1).put("type", type)
            .put("msg_id", UUID.randomUUID().toString().replace("-", ""))
            .put("ts", System.currentTimeMillis() / 1000.0);
        byte[] payload = body.toString().getBytes(StandardCharsets.UTF_8);
        byte[] out = new byte[4 + payload.length];
        out[0] = (byte) (payload.length >>> 24);
        out[1] = (byte) (payload.length >>> 16);
        out[2] = (byte) (payload.length >>> 8);
        out[3] = (byte) (payload.length);
        System.arraycopy(payload, 0, out, 4, payload.length);
        return out;
    }

    private static JSONObject readFrame(DataInputStream in) throws Exception {
        int len = in.readInt();                       // 4 字节大端长度
        if (len <= 0 || len > 1024 * 1024) throw new IOException("bad length " + len);
        byte[] buf = new byte[len]; in.readFully(buf);
        return new JSONObject(new String(buf, StandardCharsets.UTF_8));
    }

    public void connectLoop() {                       // 常驻后台线程
        int delay = 1;
        while (running) {
            try {
                connect();                            // 连接+握手成功才返回
                delay = 1;
                readLoop();                           // 阻塞到断线
            } catch (Exception e) {
                log("link: " + e.getMessage());
            }
            sleep(delay); delay = Math.min(delay * 2, 30);   // 1/2/4/8/16/30s
        }
    }

    private void connect() throws Exception {
        socket = new Socket(host, port);
        socket.setSoTimeout(30000);                   // watchdog 30s 判死
        in = new DataInputStream(socket.getInputStream());
        out = socket.getOutputStream();
        JSONObject hs = new JSONObject()
            .put("client_type", "pos").put("token", token)
            .put("device_id", deviceId).put("app_version", "1.0.0")
            .put("device_model", android.os.Build.MODEL)
            .put("protocol_version", "1.1");
        sendRaw(frame("handshake", hs));
        JSONObject ack = readFrame(in);               // 10s 内应收到
        if (!"handshake_ack".equals(ack.optString("type"))) throw new IOException(
            "handshake rejected: " + ack);
        log("connected session=" + ack.optString("session_id"));
    }

    private void readLoop() throws Exception {
        while (running) {
            JSONObject f = readFrame(in);
            String type = f.optString("type");
            if ("heartbeat".equals(type)) {           // host 心跳 → 必须回
                sendRaw(frame("heartbeat_ack",
                        new JSONObject().put("ref_msg_id", f.optString("msg_id"))));
            } else if ("ack".equals(type)) {          // 我们的请求被 ack
                CompletableFuture<JSONObject> fut =
                        acks.remove(f.optString("ack_msg_id"));
                if (fut != null) fut.complete(f);
            } else if ("config_push".equals(type)) {  // pos_config：先 ack 再应用
                sendAck(f.optString("msg_id"), "applied");
                onPosConfig(f);                       // 应用 pos_params（缓存待 case_started）
            } else if ("case_started".equals(type)) {
                sendAck(f.optString("msg_id"), "");
                onCaseStarted(f);                     // UI 显示指引，操作员开始交易
            } else if ("error".equals(type)) {
                onError(f);
            }
        }
    }

    public JSONObject sendAndWait(JSONObject body, String type, long timeoutS)
            throws Exception {
        JSONObject f = new JSONObject(body.toString());   // body 已含业务字段
        byte[] raw = frame(type, f);
        CompletableFuture<JSONObject> fut = new CompletableFuture<>();
        acks.put(f.optString("msg_id"), fut);
        sendRaw(raw);
        return fut.get(timeoutS, TimeUnit.SECONDS);      // 超时 → 退避重发同 msg_id
    }

    // 交易结束调用：result 为 §7 的 result 对象；case_id/attempt_id 原样回显
    public void reportResult(String caseId, String attemptId, JSONObject result,
                             Callback cb) {
        io.execute(() -> {
            JSONObject body = new JSONObject()
                .put("case_id", caseId).put("attempt_id", attemptId)
                .put("device_id", deviceId).put("result", result);
            byte[] raw = frame("transaction_result", body);
            String msgId = body.optString("msg_id");
            int delay = 1, tries = 0;
            while (running && tries++ < 6) {
                CompletableFuture<JSONObject> fut = new CompletableFuture<>();
                acks.put(msgId, fut);
                try {
                    sendRaw(raw);
                    JSONObject ack = fut.get(15, TimeUnit.SECONDS);
                    cb.onAck(ack.optString("detail"));   // persisted | quarantined
                    return;
                } catch (Exception e) {
                    sleep(delay); delay = Math.min(delay * 2, 30);
                }
            }
            cb.onFail("host ack timeout after 6 tries");
        });
    }

    private void sendAck(String ackMsgId, String detail) throws Exception {
        sendRaw(frame("ack", new JSONObject()
                .put("ack_msg_id", ackMsgId).put("ok", true).put("detail", detail)));
    }
    // sendRaw / log / sleep / onPosConfig / onCaseStarted / onError / Callback 略
}
```

> 与 `tools/mock_clients/mock_pos.py` 行为对齐（它是协议参照实现）：握手 →
> ack pos_config → 打印参数 → ack case_started → 展示指引 → 回传 result → 等
> host ack。联调前可先跑 mock 验证 host 侧。

## 12. 联调检查清单（POS 端）

- [ ] token 与 host `tokens.json` 的 pos 一致；`protocol_version:"1.1"`；
- [ ] 收到 `pos_config` 后先 ack、参数正确填入（金额/币种/交易类型）；
- [ ] 收到 `case_started` 后 ack 并显示操作员指引（金额/PIN/notes）；
- [ ] `transaction_result` 原样回显 `case_id/attempt_id`，msg_id 每次新生成；
- [ ] host ack 前退避重发同 msg_id；`quarantined` 停止重发；
- [ ] 心跳回 `heartbeat_ack`；30s 静默判死 + 退避重连（同 device_id）；
- [ ] 卡片静默（noResponseCommands）场景：内核超时/有限重试/终止并如实回传；
- [ ] 结果如实：`outcome/online/resp_code/tags` 与内核一致，不伪造；
- [ ] USB 调试用 `adb forward tcp:9100 tcp:9100`；LAN 用 host IP，隔离网络；
- [ ] 在 host 历史页核对每个用例 PASS/FAIL 与断言明细；迟到结果看 quarantine。

## 13. 常见问题

| 现象 | 原因/处理 |
|---|---|
| 握手被拒 `auth_failed` | token 与 tokens.json 不一致 |
| `already_connected` | 已有 POS 在线且 device_id 不同；同设备重连应被接受并替换 |
| 结果被判定为 INCONCLUSIVE | 案例 180s 超时；POS 不要在卡片静默时无限重试 |
| 收到 ack `quarantined` | attempt 已过期/无运行；结果已存档但不判定，停止重发 |
| 长时间无心跳告警 | host 侧会显示心跳超时，检查网络与后台线程 |
| 帧被断开（5 坏帧） | 发送的 payload 不是合法 JSON 对象，或手工组帧长度错误 |
