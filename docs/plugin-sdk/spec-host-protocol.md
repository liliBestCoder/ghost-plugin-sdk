# 宿主协议（spec-host-protocol v1）

三方两段：主进程 ↔ `ghost_plugin_host.exe` ↔ 插件进程。

代码侧唯一真源：`src/shared/contract_plugin_host.h`（三侧共用同一份 include，**op 名与字段名都在里面**）。本文是它的文档投影。

拒绝原因码见 [`spec-errors.md`](spec-errors.md)。
> 本文出现的数值上限、超时与正则以 [`spec-limits.md`](spec-limits.md) 为准；这里写出的数字是它的投影，改动必须两处同步。

---

## 0. 插件作者只需要读 §3

§1、§2、§4、§5 是主进程与宿主之间的事，写在这里是为了让协议完整。**写插件只要实现 §3 的一次握手**：从 stdin 读一行 JSON，往 stdout 写一行 JSON。

## 0.1 字段名一组，两跳共用

早期草案让两跳各起各的名字（`id`/`pluginId`、`dir`/`pluginDir`、`pluginToken`/`token`），三个名字乘两跳全靠人记。现在**一组名字**，由 `contract_plugin_host.h` 的常量定义：

| 字段 | 含义 |
|---|---|
| `v` | 协议版本，恒 `1` |
| `op` | 仅主进程 → 宿主 |
| `pluginId` | 插件 id |
| `pluginDir` | 包目录（`plugins\<id>\<version>\`） |
| `dataDir` | 插件私有数据目录（`plugins\<id>\.data\`） |
| `entry` / `args` / `runtime` | 启动方式 |
| `token` | **收方视角**：给你的那一个。宿主收到的是宿主 token，插件收到的是插件 token |
| `apiBase` | 控制接口地址 |
| `permissions` / `settings` / `license` / `lang` | 见各节 |
| `stopEvent` | 优雅停止的命名事件，**由宿主生成并下发** |
| `uiUrl` / `ok` | 插件 → 宿主的应答 |

---

## 1. 主进程 → 宿主（stdin，JSON-lines）

每条消息一行 UTF-8 JSON，以 `\n` 结尾。宿主按行读，**一行一条，不跨行**；解析失败或 `v` 不认识的行**丢弃并继续**（不是断开——一条坏行不该让所有插件下线）。一行最长 `kMaxHostLineBytes`（16 KB），超长的行整行丢弃（不截断解析）。`hello` 之前的行一律丢弃，第二条 `hello` 也丢弃；stdin 到 EOF 等同 `shutdown`。字段类型不对的行也整行丢弃（先判类型再取，绝不抛出）。

`hello` 的 `token` 会进 HTTP 头（§4），所以必须是**头安全**的：`[0-9A-Za-z{}-]`，≤ 128 字符（主进程铸的是带花括号的 GUID）；`apiBase` 必须恰好是 `http://127.0.0.1:<端口>`（无路径、无尾斜杠）——宿主把状态 POST 到这个端口，并原样交给每个插件。

| op | 时机 | 负载 |
|---|---|---|
| `hello` | 宿主启动后第一条 | `{"v":1,"op":"hello","token":"…","dataDir":"…","apiBase":"http://127.0.0.1:23551"}` |
| `start` | 启用一个插件 | 见下 |
| `stop` | 停用一个插件 | `{"v":1,"op":"stop","pluginId":"com.example.x"}` |
| `shutdown` | 主进程退出前 | `{"v":1,"op":"shutdown"}` |

```json
{
  "v": 1,
  "op": "start",
  "pluginId": "com.example.events-viewer",
  "pluginDir": "C:\\Users\\me\\AppData\\Local\\GhostProxifier\\plugins\\com.example.events-viewer\\1.0.0",
  "dataDir":   "C:\\Users\\me\\AppData\\Local\\GhostProxifier\\plugins\\com.example.events-viewer\\.data",
  "entry": "run.cmd",
  "args": [],
  "runtime": { "kind": "python", "interpreter": "C:\\Python312\\python.exe" },
  "token": "…",
  "permissions": ["events.read.control"],
  "settings": {},
  "license": { "licensed": true, "trial": false, "expiresAt": "2027-09-20T00:00:00Z" },
  "lang": "zh"
}
```

`hello` 里的 `token` 是**宿主专用**的（§4），`start` 里的是**那一个插件专用**的。两者不同，都不落盘，进程退出即作废。

> ⚠️ **token 走 stdin，绝不上命令行。** 命令行任何进程都能经 WMI 或读 PEB 拿到。这条与 `ghost_watchdog.exe` 同源，理由记在 `src/modules/log/service/watchdog_host.h` 顶部。

### 1.1 「丢弃并继续」需要一个补偿

丢弃一条 `start` 意味着那个插件永远不启动，而主进程唯一的反馈通道（§4）只报**已存在**的插件——它不会说「你让我起的那个我没收到」。

所以主进程对每条 `start` 起一个超时计时器（`kStartAckTimeoutMs`）。到期仍未见该 id 的任何状态回报，判 `host_unavailable` 并重拉宿主。

### 1.2 stdin 是长期通道，写方要有界

这是与 watchdog 的**唯一实质差别**：watchdog 只用 stdin 递一次握手，这里它长期开着。

于是宿主停止读取时，主进程的写会**阻塞**。`host_bridge` 在自己的线程里写并带超时，超时即判宿主失联。`watchdog_host.h` 恰好记录过一次这类挂起，那是这条写在这里的原因。

## 2. 宿主的进程管理

- 每个插件一个 **Job Object**，带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`。宿主一死（正常退出、崩溃、被任务管理器杀），插件及其自己拉起的子进程一起被内核终止。没有这一条，强杀宿主会留下一串还占着端口的孤儿进程，而症状是「插件装好了但打不开」。

  ⚠️ **顺序是 `CreateProcess(CREATE_SUSPENDED)` → `AssignProcessToJobObject` → `ResumeThread`。** 先 Resume 再 Assign，一个启动即 spawn 子进程的插件会有子进程**逃出 Job**——而 `cmd.exe /c` 正是这个形状，也就是最常见的那种入口。逃出去的恰好是这条机制要管的东西。

- `CREATE_NO_WINDOW`；stdout 是管道（只读第一行，见 §3），stdin 是管道（只写握手，64 KB 缓冲——插件不读 stdin 也不会卡住宿主），**stderr 接 NUL**（插件往 stderr 写的警告若与 stdout 共管道，可能被当成握手行）。插件**恰好**继承这三个句柄（`PROC_THREAD_ATTRIBUTE_HANDLE_LIST`）——宿主自己的 stdin 里有每个插件的 token，别的插件的管道正在另一个线程上创建。
- **入口按句柄钉住**：从数据根起逐级按句柄打开到 `pluginDir`，再到 `entry`，链接、挂载点、目录、硬链接一律 `plugin_entry_missing`；`pluginDir`/`dataDir` 必须恰好是 `<hello.dataDir>\plugins\<id>\<version>` 与 `...\.data`，否则 `plugin_spawn_failed`。`CreateProcessW` 仍要路径——给的是钉住文件的最终路径，句柄持有到握手结束；`.exe` 入口在挂起状态下核对映像与钉住的文件是同一个（文件 id），不同即杀。解释器脚本（`.cmd`/`.py`/`.js`）之后由解释器按路径再读，这一段残余 TOCTOU 写在 `supervisor.cpp` 里——宿主以用户自己的权限运行（§7.10），同用户进程在握手之后换掉脚本，得不到它本来得不到的东西。
- 工作目录 = `pluginDir`。
- **环境变量**：宿主在插件进程的环境里加这几个，内容与握手 JSON 里的同名字段完全一致（**同一个值的两种取法，不是两个来源**）。给的是那些不方便解析 JSON 的入口，比如一个 `.cmd`：

  | 变量 | 值 |
  |---|---|
  | `GHOST_PLUGIN_ID` | 插件 id |
  | `GHOST_PLUGIN_DIR` | 包目录 |
  | `GHOST_PLUGIN_DATA_DIR` | 私有数据目录 |
  | `GHOST_PLUGIN_API_BASE` | 控制接口地址 |
  | `GHOST_PLUGIN_STOP_EVENT` | 停止事件名（与握手的 `stopEvent` 同值） |
  | `GHOST_PLUGIN_PYTHON` / `GHOST_PLUGIN_NODE` | 解析出来的解释器绝对路径，仅在 `runtime.kind` 对应时设置 |

  ⚠️ **token 不在环境变量里**，只走 stdin。子进程会继承环境块，而插件很可能自己拉子进程。
  ⚠️ 用 `GHOST_PLUGIN_PYTHON` 而不是自己再找一遍 `python.exe` —— 自己找可能找到与前置版本检查不同的那一个。

- **有界重拉**：进程退出且不是被我们停的 → 重拉，次数与退避见 [`spec-limits.md`](spec-limits.md) §1。用尽后报 `plugin_restart_exhausted`，状态置 `crashed`，不再重试。
- **优雅停止**：先设 `stopEvent` 请求退出，超时后 `TerminateJobObject`。

### 2.1 停止事件的名字由宿主给，不许两侧各自拼

⚠️ **这是一条踩过就很难查的坑。** 宿主 `CreateProcess` 出来的可能是 `cmd.exe`（`.cmd` 入口）或 `python.exe`（`.py` 入口），而插件脚本里 `GetCurrentProcessId()` 拿到的是**它自己**的 pid。两侧各按「自己的 pid」拼事件名，名字就对不上，**优雅停止永远不触发**——每次停用都是超时后硬杀，而且不报任何错。

所以名字由宿主生成一次，经握手的 `stopEvent` 字段（以及 `GHOST_PLUGIN_STOP_EVENT`）下发。插件**照着用**，不自己拼。

`Local\` 而非 `Global\`：同会话，不需要 `SeCreateGlobalPrivilege`。

名字的形状是 `Local\GhostPlugin_Stop_<宿主 pid，8 位小写十六进制>_<插件 id>`（`FormatStopEventName`）。**id 原样嵌入而不是取哈希**：早先用 id 的 CRC-32 求定宽，两个 CRC 相同的 id 会共用一个事件，停一个就停了两个；id 的字符集（`[a-z0-9.-]`，≤ 64 字节）本身就是安全的对象名分量。插件不必关心形状——照握手给的值用即可。宿主创建事件时若名字**已存在**（`ERROR_ALREADY_EXISTS`），判定是别人抢注的，拒绝启动该插件（`plugin_spawn_failed`）。同一个宿主进程里，一个 id 的事件只在第一次 `start` 时创建、之后一直持有并复用（每次拉起前复位）——停用再启用、崩溃重拉都不会撞上自己上一次建的那个名字。

## 3. 宿主 ↔ 插件（握手）

### 3.0 先判断有没有宿主 —— 独立运行模式

插件**可以不依赖 Ghost 独立运行**（用户双击、命令行启动、或作为普通程序分发）。所以握手是**条件性的**，插件启动的第一件事是判断自己是不是被宿主拉起的：

```python
import os
hosted = "GHOST_PLUGIN_ID" in os.environ
```

判据是环境变量 `GHOST_PLUGIN_ID` 存不存在（§2 的环境变量表），**不是** stdin 是否可读——在终端里 stdin 永远可读，`readline()` 会一直阻塞，插件看起来像挂了。

| | 宿主模式 | 独立模式 |
|---|---|---|
| 判据 | `GHOST_PLUGIN_ID` 存在 | 不存在 |
| 握手 | 读 stdin 一行、写 stdout 一行 | **不读 stdin、不写握手** |
| token / `apiBase` / `permissions` | 有 | **没有**。Ghost 的接口一律不可用 |
| `dataDir` | 宿主给 | 自己定，建议 `%LOCALAPPDATA%\<id>\`（**不要**去写 Ghost 的 `plugins\<id>\.data\`，那是 Ghost 拥有并会在卸载时删掉的目录） |
| `license` | 宿主给声明 | 没有；要门控就自己收激活码，见 `spec-license.md` §7.1 |
| `stopEvent` | 宿主给 | 没有；按普通程序处理 Ctrl+C / 关窗 |
| UI | 回 `uiUrl` 给宿主嵌进 iframe | 自己开浏览器（`os.startfile(url)`）或打印地址 |
| 单实例 | 宿主保证每个插件只有一个 | **不保证**。用户可能在 Ghost 里启用了它、又手动开了一个——别假设单实例，端口用 `0`，需要单实例就自己拿命名互斥量 |

**独立模式下没有 Ghost 的任何东西，包括在 Ghost 正在运行时。** token 只经宿主握手发放，没有「后来附着上去」的路——那需要一条不经用户确认就把凭据交给任意本地进程的通道，正是整个身份设计要避免的。想要 Ghost 的数据，就从 Ghost 里启用它。

独立模式对 Ghost 是不可见的：主进程不知道、不管理、不重拉这个进程。清单里的 `standalone: true`（`spec-manifest.md` §2）只是告诉商店页和插件页「这个可以单独跑」，不改变任何行为。

### 3.1 宿主写一行到插件的 stdin

```json
{
  "v": 1,
  "pluginId": "com.example.events-viewer",
  "pluginDir": "…\\plugins\\com.example.events-viewer\\1.0.0",
  "dataDir": "…\\plugins\\com.example.events-viewer\\.data",
  "apiBase": "http://127.0.0.1:23551",
  "token": "…",
  "permissions": ["events.read.control"],
  "settings": {},
  "license": { "licensed": true, "trial": false, "expiresAt": "2027-09-20T00:00:00Z" },
  "lang": "zh",
  "stopEvent": "Local\\GhostPlugin_Stop_00001f40_com.example.events-viewer"
}
```

- `dataDir` 是插件的**私有**目录，已创建好，升级不清空，卸载才删。插件写文件只往这里写。
- `token` 配 `permissions` 使用，见 [`spec-plugin-api.md`](spec-plugin-api.md)。PR ④ 起它就是请求头 **`X-Ghost-Plugin-Token`** 的值，可达范围恰好是 `permissions`（**已授予**的那组——待确认的新权限不在这里）；它随这一次 `start` 生、随停用/卸载/宿主丢失而作废，每插件一份调用预算。它**不是** `hello` 里的宿主 token，两者放错头名都是 401。
- `settings` 在 v1 **恒为 `{}`**：没有任何 API、桥消息或界面能写它。字段保留是为了握手结构稳定。
- `license` 是**给插件看的声明**，供它展示或自检。**执行点在主进程**：没有许可，主进程根本不会下发 `start`。插件不必也不应把它当安全判据。PR ①b 起由主进程按 `spec-license.md` §7 判定后填入（`PluginStartSpec::license`，`BuildStartLine` 只在有它时写这个键）：付费插件是 `{licensed:true, trial:false, expiresAt:<许可到期>}` 或 `{licensed:false, trial:true, expiresAt:<试用结束>}`；免费插件 `{licensed:false, trial:false}`。宿主自己的重拉复用上一份 `start`，所以会话中途到期的试用在这里看不到变化（`spec-license.md` §7 的已知边界）。
- `lang` 取自应用当前语言（`settings.ui.lang`）。**用户中途切换语言不会通知已在运行的插件**——v1 没有重新握手，新语言在下次启用时生效。要跟随切换的插件可以自己轮询，但不必。

### 3.2 插件写一行到自己的 stdout

成功：

```json
{"v":1,"ok":true,"uiUrl":"http://127.0.0.1:53211/"}
```

失败：

```json
{"v":1,"ok":false,"error":"missing dependency: mitmproxy"}
```

| 规则 | 说明 |
|---|---|
| 超时 | 在 `kHandshakeTimeoutMs` 内必须写完这一行，否则 `plugin_handshake_timeout`，进程被终止、不重拉。**stdout 关闭而进程还活着**按同一条处理（等到超时，不当成退出） |
| 长度 | 第一行最长 `kMaxHandshakeLineBytes`（4 KB）；超过而仍无 `\n` 立刻判 `plugin_handshake_invalid`，不等到超时 |
| 只读第一行 | 第一行之后的 stdout **一律丢弃**，不转日志。一个话多的插件不该能撑爆宿主，也不该能借宿主往主进程日志里灌东西。要记日志用 `log.write` 权限，那条路上有限流 |
| `uiUrl` | 清单 `ui.embedded == true` 时必填；必须匹配 `^http://127\.0\.0\.1:\d{1,5}/`，否则该字段被丢弃并按无界面处理 |
| `ok:false` | 按 `plugin_declined` 处理，**不重拉** —— 插件自己说了不行，重试三次也还是不行。⚠️ 插件的 `error` 文本**不会**到达主进程：宿主 → 主进程的状态体里 `error` 只收码（§4），日志里只有 `plugin_declined` 这个码并注明「原因不经协议传递」。要看原因，独立运行插件 |
| 编码 | UTF-8，不带 BOM，以 `\n` 结尾 |

### 3.3 最小实现（Python）

```python
import json, os, sys, http.server, socketserver

hosted = "GHOST_PLUGIN_ID" in os.environ          # §3.0：没有宿主就是独立模式
hs = None
if hosted:
    hs = json.loads(sys.stdin.readline())
    if hs.get("v") != 1:
        print(json.dumps({"v": 1, "ok": False, "error": "unsupported protocol"}), flush=True)
        sys.exit(1)

with socketserver.TCPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler) as srv:
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    if hosted:
        print(json.dumps({"v": 1, "ok": True, "uiUrl": url}), flush=True)
    else:
        os.startfile(url)                          # 独立模式：自己开浏览器
    srv.serve_forever()      # 宿主模式想优雅退出就另起一个线程等 hs["stopEvent"]
```

⚠️ `flush=True` 不能省：Python 的 stdout 在管道上是块缓冲的，不刷新就会撞上握手超时，而症状是「插件启动失败」而不是「忘了刷新」。

⚠️ 端口用 `0` 让系统分配，别硬编码——用户机器上什么都可能被占。

## 4. 宿主 → 主进程（`POST /api/plugin-status`）

宿主持 `hello` 里那个 token，请求头 **`X-Ghost-Host-Token`**，`Origin: http://127.0.0.1:23551`。

> ⚠️ **不是 `X-Ghost-Token`。** 身份按头名区分（[`spec-plugin-api.md`](spec-plugin-api.md) §1）：用会话头就会被判成界面自己，于是宿主能调全部命令（现 50 条路由），包括读明文上游凭据、改配置、启动注入。而插件进程是宿主的**子进程**，同用户同完整性级别，`OpenProcess(PROCESS_VM_READ)` 就能把宿主内存里的 token 读走——给宿主一把全权钥匙等于绕过整个权限表把它给了插件。
>
> `Host` 身份的白名单**恰好一条**：`plugin.status`。别的命令一律 403。

```json
{ "entries": [
  { "pluginId": "com.example.events-viewer", "state": "running",
    "uiUrl": "http://127.0.0.1:53211/", "pid": 12345, "restarts": 0 },
  { "pluginId": "com.example.other", "state": "crashed", "exitCode": 1, "restarts": 3 }
] }
```

> 顶层键是 **`entries`**，与既有的 `log.ingest` 一致 —— 两个端点在文档里被称作「同一个模板」，那就该连信封也一样，否则「同一个模板」这句话每读一次都要在脑子里打个折。这里**不带 `v`**，同样与 `log.ingest` 对齐（stdin 那条通道才带 `v`，它是跨进程线格式；这条是本机 HTTP，版本随应用走）。

每条的字段：`pluginId`、`state`，可选 `uiUrl`、`pid`（1..2^32-1）、`exitCode`（DWORD，NTSTATUS 也要完整到达）、`restarts`（0..`kMaxRestarts`），以及 **`error`（PR ③ 新增，可选）**：宿主自己能产生的 [`spec-errors.md`](spec-errors.md) §5 码之一（`plugin_entry_missing` / `plugin_spawn_failed` / `plugin_handshake_timeout` / `plugin_handshake_invalid` / `plugin_declined` / `plugin_crashed` / `plugin_restart_exhausted`，`IsHostReportedCode`）。不收自由文本（那是一条往用户眼前放任意字的路），也不收主进程自己的码（宿主没资格替主进程说它失败了）——别的值**丢这个字段**。宿主的状态：`declined`、握手超时/非法、入口缺失、拉不起来都报 `crashed` 加对应的码且不重拉；意外退出每次重拉前报 `crashed`/`plugin_crashed`，用尽报 `crashed`/`plugin_restart_exhausted`；稳定运行满 `kStableRunResetMs` 之后退出，重拉计数从零开始。

宿主一侧：一次 POST 失败（连不上、非 200）**重发一次**，隔 500 ms，停止会取消这次重发；之后不再重试——下一次状态变化本身就是完整的陈述。主进程一侧另有兜底：报过 `running` 的 pid 已不存在而没有任何报告时，bridge 每秒一次的检查把它改成 `crashed`/`plugin_crashed`。

响应：`{"status":"ok","accepted":N,"truncated":bool,"rateDropped":N,"dropped":N}`——`dropped` 是被逐条校验丢掉的条数，`rateDropped` 是过了校验、被限流桶丢掉的条数；没有 bridge 答 `{"status":"error","error":"host_unavailable"}`。

**主进程把这个请求体当数据，不当指令**（与 `log.ingest` 同一个模板：token 只证明「本次会话里的某个东西」，不证明「是宿主」）：

| 规则 | |
|---|---|
| `pluginId` 在 plugins.json 里没有记录（`ok` 与 `broken` 都算有——卸载先把记录标 broken，再等插件的 `stopped`） | 丢弃该条 |
| `state` 不在 `running`/`stopped`/`crashed` 里 | 丢弃该条 |
| `uiUrl` 不过 `IsLoopbackUiUrl` | 丢弃**该字段**，不丢整条。比正则严：端口 1–65535 且**不是 23551**（Ghost 自己的控制接口不是插件界面）、**不是 80**（浏览器会去掉默认端口，插件 frame 按 `127.0.0.1:<port>/` 绑定的导航白名单就对不上，PR ⑤）；斜杠后只许可打印 ASCII，且不含空格、`"`、`'`、`<`、`>`、反引号、`\`（这个值在 PR ⑤ 是 iframe 的 src）；整串 ≤ 2048 字节。握手应答里的 `uiUrl` 由宿主用同一个函数判 |
| `error` 不是宿主能产生的 §5 码 | 丢弃**该字段** |
| 每批超过 `kMaxStatusBatch` | 处理前 N 条并在响应里报 `truncated`（**不是整批拒绝** —— 为多出来的一条丢掉五十条好观测是亏的） |
| 频率 | 令牌桶限流（复用 `src/modules/log/domain/rate_limit.h`，20/s、突发 100，每个 bridge 一个桶），超限的条目计入响应的 `rateDropped`——**不是**错误码，请求本身照常答 `ok`。只有通过逐条校验的条目才花预算：垃圾条目挤不掉后面真正的回报，而每请求的工作量由 `kMaxStatusBatch` 封顶 |
| 字段类型 | **先判类型再取**。`json::value()` 类型不符会抛，而 `Dispatch` 把异常变成整批失败：最后一条把 `pid` 写成字符串，前面全部的好数据就都没了 |

## 5. 宿主重启后的重新同步

宿主崩溃 → Job 连坐杀光全部插件 → 主进程重拉。此时**四件事都要做**，漏一件就会留下一个看起来正常的错误状态：

1. 主进程手上那份状态（来自上一批 `plugin.status`）还写着 `running`，**先整体置 `stopped`**。否则界面显示一批其实已经不存在的进程，而且没有任何东西会来纠正它。
2. 新宿主拿**新的**宿主 token（旧的随进程消失即作废）。
3. 对每个 `enabled` 的插件重发 `start`，并**重新生成插件 token**——旧 token 可能已经被那个刚死掉的插件泄漏到别处。
4. 重发按插件 id 逐个来，每条各自走 §1.1 的超时计时器，不是一次性灌进管道。

计时器的应答**只认 `running` 或 `crashed`**：版本切换时旧实例的 `stopped` 会先到，它不是新 `start` 的回答。宿主报 `crashed`/`plugin_crashed`（每次自动重拉之前报，带退出码）时，主进程**不再为它发新 `start`、不铸新 token**——重启会带着它已有的 token 起来；万一一条 `start` 仍在重拉退避期间到达（宿主已报 `crashed`、下一次拉起还没开始），宿主把它当作挂起的 `start`，下一次拉起用**它的** token。宿主重拉至多 3 次（`1000*n*n` 毫秒），稳定运行 10 分钟后预算归零，用尽后等下一次用户启用。宿主侧，停止中到达的 `start` 会被挂起、等旧实例停完用新 token 起（而不是用旧实例的 `running` 回答）；主进程侧，同一版本已在运行或 `start` 在途时再次启用不重发、不换 token。

## 6. 版本协商

- 所有消息带 `v`。**`v` 不认识就丢弃该消息**，不尝试尽力解析。
- 宿主与主进程同属一次安装，版本天然一致，`v` 主要是给将来留的。
- 插件侧：宿主写给插件的握手 `v` 是 `1`；插件应当检查它，不认识就回 `ok:false`（如 §3.3）。
