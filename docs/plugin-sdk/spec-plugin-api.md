# 插件 API 与权限（spec-plugin-api v1）

插件通过本地控制接口读取 Ghost 的数据。凭据是**每插件一次性 token**，可达范围由**权限白名单**决定，**默认拒绝**。

拒绝原因码见 [`spec-errors.md`](spec-errors.md)。
> 本文出现的数值上限、超时与正则以 [`spec-limits.md`](spec-limits.md) 为准；这里写出的数字是它的投影，改动必须两处同步。

---

## 1. 调用方式

```
GET  http://127.0.0.1:23551/process-stats
X-Ghost-Plugin-Token: <握手里拿到的 token>
Origin: http://127.0.0.1:23551
```

- 地址取握手里的 `apiBase`，**不要硬编码端口**（它是常量，但取自握手才不会在改号时静默失效）。
- 头名是 `X-Ghost-Plugin-Token`，**不是** `X-Ghost-Token`。后者是界面自己的会话 token，插件拿不到；宿主用的是第三个头 `X-Ghost-Host-Token`，那把钥匙只能调一条命令。
- `Origin`：**不带**（原生进程本来就没有页面），或者带就必须**恰好是** `apiBase` 那个值。任何别的源——包括你自己 UI 页所在的 `http://127.0.0.1:<你的端口>`——一律 401，token 对不对都一样（§8）。
- token 在**停用、卸载插件时立刻作废**；**宿主丢失的那一刻**所有插件 token 一起作废（被丢掉的宿主可能正是被读走 token 的那个），宿主重启后每个插件拿到**新的** token；插件自己崩溃、由宿主原地重启时 token **不变**。进程内存、不落盘、重启应用即换。插件不要缓存到文件里。
- **每个插件有自己的调用预算**：每秒 20 次、突发 100（[`spec-limits.md`](spec-limits.md) §7.1）。每一次带你 token 的请求都算一次——**包括被 403 拒掉的**（否则一个 403 循环是免费的），打开 `/events` 也算一次。超出答 **429** `{"status":"error","error":"rate_limited"}`，不带 `Retry-After`：预算按时间连续回补，稍等再试即可。别的插件、界面、watchdog 各有各的预算，互不挤占。
- **三种拒绝都不写应用日志**：401（没有身份）、403（身份不够）、429（超预算）。否则一个拿到 token 的循环就能把 1000 条的日志环冲掉。所以日志里看不到你被拒——检查的是你自己收到的状态码。
- **独立运行（没有宿主）时没有 token，本文的一切都不可用**，即使 Ghost 正在运行。判据与理由见 [`spec-host-protocol.md`](spec-host-protocol.md) §3.0。插件要为这种情况准备一条降级路径，而不是崩溃。

### 为什么不给完整会话 token

会话 token 能调全部路由：`GET /config` 读走 SOCKS5 明文凭据、`POST /save-config` 改上游、`POST /api/launch-target` 启动注入。「第三方代码永不进主进程」这条边界，会被一个能远程遥控主进程的外部进程架空。

## 2. 权限

清单 `permissions[]` 声明所需权限，安装时逐条展示给用户确认。**未声明的权限即使在表里也不放行。**

| 权限 | 放行的命令 | 内容 |
|---|---|---|
| `events.read.control` | `events.stream` | 事件流的 **Control / System 平面**：注入、ACL、崩溃、许可、你自己写的日志 |
| `events.read.data` | `events.stream` | 事件流的 **Data 平面**，**只有** Data —— 不是 control 的超集；两个都要就两个都申请。见下方警告 |
| `stats.read` | `stats.processes`、`node.latencies`、`stats.throughput` | 进程统计（含被管目标的列表与吞吐，**每个目标的 `env` 整个删掉**，见 §4 末）、节点延迟、吞吐历史 |
| `config.read` | `config.get` | **脱敏后**的配置，见 §4 |
| `log.write` | `log.ingest` | 往应用日志写，见 §5 |
| `plugin.assets` | `plugin.icon` | 读**你自己**包里的 `icon` |
| `upstream.connect` | `upstream.list`、`upstream.tunnel` | **经用户的上游节点连接任意地址**：Ghost 建隧道、把连好的 socket 交给你的进程（TCP；在 UDP 中继已验证且健康的 SOCKS5 节点上还有 UDP）。见 §10。**Ghost 1.2.1 起** |

表按**命令名**索引，不按路由路径 —— 权限判定发生在命令层，而「路由 ↔ 命令」在这个仓库是漂移高发区（`test_router` 与 `test_command_contract` 已经是两张各自手写的表，再加第三张只会更糟）。每条命令对应的路由在 §3–§6 与 §10 各自写明。

> ⚠️ **`events.read.data` 等同于用户的完整访问历史。** Data 平面携带每个被管目标的**每一次 DNS 查询与连接目的地**。对一个隐私工具来说这是全部数据里最敏感的那份，所以它和 `events.read.control` 是两个独立权限，用户可以只给后者。申请它要有真实理由，安装确认框会明确告诉用户这意味着什么。

> ⚠️ **`upstream.connect` 是唯一一个以用户身份在网络上行事的权限**，其余六个只是读 Ghost 知道的东西。插件拿不到节点凭据，但它经这条路建立的每一条连接都以**用户的代理身份**出现在目的地面前。启用确认框在这一条下面另加一行警告（`plugins.perm_warn.upstream.connect`：只授予你信任的插件）。

**v1 就这七个**：最初的六个（PR ②–④），加上 PR ⑩ 的 `upstream.connect`。没有任何写配置、改目标、启动注入类权限。数据面抓包、限速、PAT 注入是 issue #12 的子项目 D，三个插件各自立项，各自论证要新增哪个权限位——那类权限能做的事比上面几个大一个量级，不该顺带加进来；`upstream.connect` 就是这样单独立项、单独论证的一个（设计 `docs/superpowers/specs/2026-09-29-port-forwarder-plugin-design.md` Part A）。

⚠️ **声明 `upstream.connect` 的插件，发布描述的 `minAppVersion` 必须 ≥ `1.2.1`。** 更老的 Ghost 不认识这个名字，按 [`spec-manifest.md`](spec-manifest.md) §2 整包拒绝（`unknown_permission`）——用户看到的是一个费解的码，而不是「请升级」。`gpkg.py pack` 对更低的 `--min-app-version` 以工具码 `min_app_version_too_low` 拒绝打包（默认值 `1.2.0` 也会被拒，要显式传 `--min-app-version 1.2.1`）。

### 2.1 默认拒绝

`src/modules/api/wire/plugin_permissions.h` 是唯一那张表。**不在表里的命令，对插件身份一律 403**（`permission_denied`），与插件声明了什么无关。于是将来新增的任何路由天然对插件关闭，不依赖谁记得去加一条禁止。「不在表里」也包括：未知路径、`OPTIONS` 预检、方法不对的已知路径——插件原生进程不发预检，`Access-Control-Allow-Headers` 里也**不列** `X-Ghost-Plugin-Token`。

**只有已授予的权限算数。** 升级后新申请、还没被用户确认的权限（`pendingPermissions`）从不进 `start` 行，也就从不进这张表的判定——`test_plugin_service` 38p 钉住：一次把 `{stats.read}` 扩成 `{events.read.control}` 的升级之后，哪怕记录被启用，启动参数里仍是旧的 `{stats.read}`。清单里写了表外的名字，安装就以 `unknown_permission` 整包拒绝；即便如此，这张表对传进来的名字**再判一次**，不认识的名字什么都不放行。

`test_plugin_permissions` 遍历**整张路由表**断言这一点，期望值是测试里**手写**的名单（不是从被测的表反推）：持有全部七个权限的插件恰好够得到八条路由命令（`events.stream` 不在路由表里，见 §2.2），只持有 `upstream.connect` 的恰好够得到 `upstream.list`/`upstream.tunnel` 两条，一个权限都没有的插件什么都够不到，宿主只够得到 `plugin.status`。每加一条新路由，若没有同时把它加进权限表，对插件的答案必然仍是拒绝。

### 2.2 `/events` 不在路由表里

`GET /events` 是在 `HandleHttpClient` 里被提前截走的（它的响应永不结束，走不了返回完整响应串的路由管线），所以路由表里没有它，也没有对应的命令名。

权限表为它保留一个**伪命令名 `events.stream`**，只在权限判定里使用，不注册进路由表、不出现在 `kRegistry` 里。**这一条要写在两边的注释里**：一个只看路由表的人会以为 `/events` 对插件是默认拒绝的，而它走的是另一条分支。

## 3. `events.read.*` —— 事件流

```
GET /events?after=<seq>
Accept: text/event-stream
X-Ghost-Plugin-Token: …
```

标准 SSE 帧，每帧一个 JSON 事件（键表是 `src/modules/api/wire/wire_event_json.h`，与 `/app-logs` 的 `events[]` 同一份，共十个键）：

```
id: 12345
data: {"seq":12345,"ts":"…","plane":"control","level":"info","src":"ui",
       "pid":0,"targetId":"","tag":"Plugin","fields":{},"text":"…"}
```

- 你看到的平面由权限决定：`events.read.control` → System + Control；`events.read.data` → **只有** Data；两个都有 → 全部。**过滤在服务端**（不是让插件自己丢——客户端的过滤是插件可以不跑的过滤）：被滤掉的事件不发帧，但**游标照样越过它**，断线续传也不会再读到它；它也**不算流量**——一个只收到被滤掉事件的连接，照常收到 keepalive 注释帧。
- 答复：没有身份 **401**；宿主的 token、或两个 `events.read.*` 都没有的插件 **403** `permission_denied`；超预算 **429** `rate_limited`（打开一次连接算一次调用）；同时已开着 2 条流再开第 3 条也是 **429**。都不记日志。
- **流活不过它的 token。** 服务端在每一轮（250 ms）的等待之后、**以及发出每一批帧之前**都重新问一次：这个 token 是否仍指向同一个插件、同样的权限。停用、卸载、宿主丢失（token 作废）或权限变化之后，已经开着的流至多再收到**一批**（≤ 256 帧，服务端的单批上限），并在**一个轮询间隔加一批**之内被**服务端关掉**——不是硬性的 250 ms。另有一个窗口：从打开时的判定（`DecideEventStream`）到第一次复查之间撤销的，也是在第一批之前被发现。重连时按新的身份与平面重新判定。
- `?after=` 压过 `Last-Event-ID`。冷启动不传 `after` 则从**当前游标**开始（不是 0 —— 那会把几千条留存一次性重放给你）。
- **不能用 `EventSource`**：它设不了请求头，而这条路要 token。用 `fetch` + 流式读，或任何能设头的 HTTP 客户端。
- 数据平面是高频的。即使拿到了 `events.read.data`，也要自己做限速与丢弃，别把它原样转发到界面上。

## 4. `config.read` —— 脱敏后的配置

```
GET /config
```

返回 `config.json` 的内容，先过 `src/modules/log/service/diag_redact.h` 的 **`RedactConfigForExport`**：

| 字段 | 处理 |
|---|---|
| `upstream[].user` / `upstream[].pass` | → `"***"` |
| 空值 | **保持空**，不填 `***` —— 填了等于告诉读者这里本来有密码 |

⚠️ 这是与界面自己拿到的 `/config` **不同的响应**。同一条路由，按身份走两条路径：会话 token 拿原文，插件 token 拿脱敏版。

**只有这一条规则，不再另加。** `RedactConfigForExport` 就是排障包导出 `config.json` 时用的那一个函数；给插件另写一份「插件专用脱敏」等于多一张要与它保持同步的名单。其余字段里没有秘密：`udpRelay` 记录不含地址，`dns.doh` 的 URL 校验本来就拒绝 userinfo（`user:pass@`），`settings.*` 是界面偏好。

⚠️ **这条路由里没有目标进程的环境变量。** `config.json` 只有 `dns` / `settings` / `stunServers` / `upstream` 四个顶层键；被管目标（及其 `env`，可能含 `GHOST_PROXY_USER`/`PASS`）在 **`targets.json`**，不经这条路由，也没有任何权限能读它。`diag_redact` 里处理 env 的 `RedactTargetsForExport` 是给排障包用的，与这里无关——**别照着它写一条 env 脱敏规则，那一行在这条路由上永远不会触发**。

**但 `stats.processes`（`stats.read`）的每个 group 带着那个目标的 `env`**（界面用它显示与编辑）。对插件，这个键被**整个删掉**，不是逐键脱敏：环境变量里既可能有 `GHOST_PROXY_USER/PASS`，也是用户为自己的程序随手写的自由文本。`path`、`alias`、`name` 与子进程行保留——「哪个程序在用多少流量」要的就是它们（产品裁定的默认值）。

## 5. `log.write` —— 写日志

```
POST /api/log-ingest
{ "entries": [ { "level": "info", "text": "started", "fields": { "port": "53211" } } ] }
```

⚠️ **顶层数组的键是 `entries`，不是 `items`，而且不带 `v`。** 这是既有端点 `CmdLogIngest` 的形状，换个键名直接得到 `{"status":"error","error":"invalid_json"}`。

服务端**覆盖**三个字段，让日志里的来源无法被伪造：

| 字段 | 被强制为 |
|---|---|
| `src` | `Plugin` |
| `tag` | 你的插件 id |
| `plane` | `Control` |

`level` 只认 `info` / `warn` / `error`；`debug` 被提升为 `info`（不是丢弃——为一个字段拼错丢掉一次真实观测是亏的）。条数、`text` 长度、`fields` 数量与令牌桶见 [`spec-limits.md`](spec-limits.md) §7；超出是**截断**并在响应里报 `truncated`，不是整批拒绝。

**条目预算是每插件一个**（每秒 5 条、突发 50），与 watchdog 用的端点预算**分开**、也比它低——你的日志与 Ghost 自己的注入、ACL、崩溃、许可事件**共用同一个 1000 条的 Control 环**。**按插件算**：一个插件满速写，约 (1000 − 50) / 5 秒、三分多钟把环刷一遍（突发 50 条立刻进去）；k 个插件一起写就快 k 倍，再加 k 个 50 条的突发——这个速率约束的是单个插件，不是整个环。在此之内：你刷屏挤不掉 Event Log 的发现，也挤不掉别的插件。超出的条目计入 `rateDropped`，并由 Ghost 自己每分钟至多写一条汇总（`tag="Ingest"`、`src=ui`、带 `plugin=<你的 id>` 字段）——那是 Ghost 在报告它丢了什么，不是你的日志。这与 §1 的调用预算是两回事：一次请求花一次调用预算，请求里的每一条花一次条目预算。

你的日志**不镜像到 Windows 事件日志**（`settings.logging.eventLogMirror` 开着也不镜像）：Application 日志是本机的审计记录，其他软件在读它，第三方的文字不该出现在那里（产品裁定的默认值）。它们照常进 `ghost.log` 与日志面板，带着你的 id。日志面板对 `src` 为 `plugin` 的行**不解析文本里的前缀**：时间、pid、tag 取自事件字段（tag 就是你的 id），整段文本作为消息，行上带插件标记（`[plugin:<id>]`）——写一段 `[12:00:00.000][4242] [ACL] …` 并不能让你的行看起来像 Ghost 自己的。DNS 页也从不把插件行当作 DNS 记录。面板渲染的每一段文字都经 HTML 转义（`fix/log-panel-escape`）。

> 实现备注（与插件作者无关）：插件与 watchdog 走同一条 `CmdLogIngest`，三个被覆盖的值按调用者分叉——watchdog 是 `System` / `Src::Watchdog` / `"EventLog"`（`eventlog_sink::ShouldMirror` 的两条防回环靠它们），插件是 `Control` / `Src::Plugin` / 插件 id（`ShouldMirror` 的**第三条**按 `src == Plugin` 拒绝）。调用者来自参数袋里的 `_pluginId`，只由路由器按解析出的身份写入，请求体里的同名键先被删掉。

## 6. `plugin.assets` —— 你的图标

```
GET /api/plugins/icon?id=<你自己的 id>
```

答原始字节，`Content-Type` 是**按字节嗅探**出的 `image/png` 或 `image/webp`，`Cache-Control: no-store`（升级会在同一 URL 下换掉字节）。只能读**自己**的 `icon`：`id` 与 token 绑定的插件不符即 403。界面（会话身份）可以读任何已安装插件的图标。

| 情况 | HTTP | `error` |
|---|---|---|
| 找到了 | 200 | —（原始字节） |
| `id` 不是合法插件 id | 400 | `bad_id` |
| 插件读**别人**的图标 | 403 | `permission_denied` |
| 没有 `plugin.assets` 权限 | 403 | `permission_denied`（闸门拒，不到命令层） |
| 插件服务不在 | 503 | `plugin_unavailable` |
| 该 id 的安装或卸载正在排队或进行 | 409 | `busy` |
| 没有记录，或记录是 `broken` | 404 | `plugin_not_installed` |
| 其余一切：清单读不出或与记录不符、清单没写 `icon`、文件不在、超过 256 KB、嗅探不是 PNG/WebP | 404 | `not_found` |

读取全程**按句柄**：包目录钉住、图标路径的每一级目录逐级相对打开（挂载点/符号链接不跟随）、文件本身不跟随链接也不接受硬链接。界面拿到 404 就用默认图标。

存在这条路由是因为 `<img src>` 带不了请求头，而把插件图标做成免 token 的静态资源，正是这个仓库在壁纸库上踩过的坑。插件页用 `ghostFetch(...).blob()` + `URL.createObjectURL` 取图。

## 7. 你**拿不到**什么

写清楚以免浪费时间去试：

- **界面的会话 token**，以及**宿主的 token**。
- **未脱敏的上游凭据**——`upstream.connect` 也不给：节点地址与凭据只在 Ghost 进程里用于握手，`upstream.list` 与 `upstream.tunnel` 的答复里都没有（§10.6 写着这为什么**不是**安全边界）。
- **经上游的入站连接**（SOCKS5 BIND）与「只许用某几个节点」的每插件名单：`upstream.connect` 只有出站 CONNECT 与 UDP ASSOCIATE，给了就是全部节点。
- **`targets.json`** —— 被管目标的路径、别名与环境变量，没有任何权限能读。
- **修改任何配置**，包括你自己的 `settings`（该字段 v1 恒为 `{}`，没有写入路径）。
- **启动、停止、注入任何目标进程。**
- **管理插件**：`GET /api/plugins`、`GET /api/plugins/resolve`（PR ⑤ 的只读预检）与 `POST /api/plugins/{refresh,install,uninstall,enable,disable}`（命令 `plugin.*`）只给界面的会话身份。它们不在 §2 的权限表里，所以对插件与宿主**默认拒绝**——一个插件不能装、卸、启停别的插件，也不能读到装了哪些。形状见 `plugin-center.md` §6.3。
- **网络数据面的 payload**。抓包是子项目 D 的事，它需要注入侧配合，不是一个 API 权限能给的。

## 8. 插件自己的 UI

`ui.embedded == true` 时，插件在自己进程里起 HTTP 服务，握手回 `uiUrl`，插件页用 iframe 嵌入（带 `sandbox` 与 `referrerpolicy`，与商店 iframe 同等对待 —— 它装的是第三方代码起的服务）。

该 iframe 与 `http://127.0.0.1:23551` **不同源**：

- `fetch` 到控制接口会被 `Origin` 校验拒绝（`ghost_auth::AllowedOrigin()` 只认一个固定源，这条不会为插件放宽）——**这是第一道防线**：即使 iframe 通过别的途径拿到了一个 token（会话的、宿主的或某个插件的），从它的源发出的请求在读 token 之前就是 401。
- 拿不到 `window.GHOST_TOKEN`：那个注入脚本同时判 origin 与「是不是顶层 frame」（⓪，已合入）——**第二道防线**。
- 想把自己导航到本地源来变成同源，会被 `FrameNavigationStarting` 按 frame 绑定的白名单拦下：插件页把这个 iframe 命名为 `ghost-plugin-<port>`，宿主在 frame 创建时读一次名字，把它绑到 `http://127.0.0.1:<port>/` 这一个前缀上（PR ⑤）。`uiUrl` 换了端口，插件页就**换一个新的 iframe 元素**，而不是改旧元素的 `src`。
- 只有**已启用、状态 ok、未被吊销、运行中**的插件才会有这个 iframe；同一时刻插件页至多开一个。

所以插件 UI 要 Ghost 的数据，路径是：**UI → 你自己的插件进程 → 控制接口**。插件进程持有插件 token，UI 不持有。

## 9. 稳定性承诺

- **权限名**稳定，不改名。
- **路由与响应字段**遵循主程序自身的兼容惯例：字段只增不减，按 key 取值而不是按位置。
- 一个权限**放行的命令集合可能变大**（加新路由进去），**不会变小**；要收窄就是一个新权限名。
- 协议版本升到 `v2` 时，`v1` 的插件至少再工作一个大版本。

## 10. `upstream.connect` —— 经用户的上游节点连接（PR ⑩，Ghost 1.2.1 起）

> 编号排在最后而不是插进 §7 之前：§1–§9 的章节号已被 SDK 示例与别的规范引用，插一节会让它们全部错位。

**Ghost 建隧道，插件拿 socket。** 插件说「经节点 X，连 host:port」；Ghost（不是插件）连到节点、用节点的协议握手（SOCKS5 带 RFC 1929 凭据，或 HTTP CONNECT 带 RFC 7617 Basic），节点说隧道已通之后，用 `WSADuplicateSocketW` 把**连好的 socket 复制进你的进程**、关掉自己那份，把 `WSAPROTOCOL_INFOW` 的原始字节交给你。你收养它，得到一个普通的阻塞 socket，另一端就是目的地。**插件里不需要、也不应该有任何代理协议代码**；节点凭据从不离开 Ghost 进程。UDP 另有语义（§10.4）。

实现：命令层 `CmdUpstreamList` / `CmdUpstreamTunnel`（`src/modules/api/service/command_handler.cpp`），隧道代理 `src/modules/proxy/service/plugin_tunnel.{h,cpp}`（`ghost_tunnel::Broker`），请求语法 `src/shared/policy_tunnel_target.h`。

### 10.1 只给原生 x64 `.exe`、不依赖运行时的插件

socket 被复制进**宿主报告的那个 pid**（[`spec-host-protocol.md`](spec-host-protocol.md) §4），而不是请求里说的任何东西——请求体里的 `pid` 从不被读取。所以：

- 清单的 `entry` 必须是 `.exe`（不分大小写）**且** `runtime.kind` 是 `"none"`（或缺省）。别的组合一律 `tunnel_unsupported`（`HostBridge::TunnelTargetOf`）。`.cmd`/`.py`/`.js` 入口的被报告 pid 是 `cmd.exe` 或解释器，不是你的代码；一个声明了 runtime 的 `.exe` 其实照样被直接拉起，但规则只有一条、不开例外。`gpkg.py pack` 在打包时就以工具码 `entry_kind_unsupported` 拒绝这种组合（客户端的清单解析**接受**它——拒绝发生在运行时）。
- **只支持 x64。** 一个 WOW64（32 位）进程答 `tunnel_unsupported`：收养一个复制进来的 socket 要有它自己的一套，v1 不做；「判断不出是不是 WOW64」同样拒绝。
- 复制之前 Ghost 打开那个进程并核对：不是 WOW64（上一条）、还活着、**映像文件就是 `<pluginDir>\<entry>` 本身**（按卷序列号 + 128 位文件 id 比，不按路径字符串）；并且**在网络 I/O 之前**就核对——一个伪造的 pid 连一次经用户代理的连接都换不来。打不开、已退出、映像不符都是 `plugin_not_running`。进程句柄从核对一直持有到复制完成，pid 不会在这中间变成别的进程。

### 10.2 `GET /api/upstream/list`（`upstream.list`）

```json
{ "status": "ok", "active": "n1",
  "nodes": [ { "id": "n1", "name": "香港", "type": "socks5", "active": true, "valid": true, "udp": true },
             { "id": "n2", "name": "办公室", "type": "http", "active": false, "valid": false, "udp": false } ] }
```

- 每个节点**恰好六个键**：`id`、`name`、`type`（`"socks5"` | `"http"`，`Mixed` 归一为 `http`）、`active`、`valid`（地址是否可用——`false` 的节点隧道答 `upstream_invalid`）、`udp`（UDP 闸门的全部四条子句：能力位、开关、SOCKS5 + 已验证、健康）。**没有地址、没有凭据、没有延迟。**
- 顶层 `active` 是 `via:"active"` 此刻会用的那个节点的 id：**第一个** `active` 为布尔 `true` 的节点（与启动被管目标时选的是同一个），没有则 `null`。节点行上的 `active` 只是那个节点自己的字段，手改过的 `config.json` 里可能不止一个为真——以顶层为准。
- 没有 `id` 的节点（手改的 `config.json`）不列出：它没法被 `via:"node"` 点名。
- 会话（界面）也能调它，答同一份。解析**从不写日志**——地址非法的节点只是 `valid:false`，一个每秒 20 次的插件冲不掉 Control 环。
- **不要长时间缓存。** 节点会被用户增删改，`udp` 随健康检测每几十秒到几分钟变化；缓存十秒左右即可。

### 10.3 `POST /api/upstream/tunnel`（`upstream.tunnel`）

```json
{ "via": "node", "nodeId": "n1", "proto": "tcp", "host": "example.com", "port": 443 }
```

| 字段 | 规则 |
|---|---|
| `via` | `"active"`（激活节点）或 `"node"`（按 `nodeId`）。是显式字段而不是给 `nodeId` 一个魔法值——节点 id 是自由串 |
| `nodeId` | 只在 `via:"node"` 时读，`^[A-Za-z0-9_.-]{1,64}$`。**只按 id 精确匹配**：一个 `name` 恰好等于它的节点不算；找不到就 `upstream_not_found`，**从不回落到激活节点**，也从不直连 |
| `proto` | `"tcp"` 或 `"udp"`，区分大小写 |
| `host` | **恰好是**三者之一，否则 `bad_target`：严格点分四段 IPv4（无前导零——`inet_addr` 把 `010` 读成八进制 8）；不带方括号的 IPv6（无 zone id，≤ 45 字节）；主机名（标签 1–63 个 `[A-Za-z0-9_-]`、不以 `-` 开头结尾、无空标签，**最后一个标签不能是数**：必须含字母且不是 `0x…`——于是 `127.1`、`1.2.3`、`0x7f000001` 不会被当成名字发出去，而代理可能把它们读成地址）。≤ 253 字节。主机名**以名字交给节点**（SOCKS5 ATYP 3，远程 DNS），本机不解析 |
| `port` | JSON **整数**（`443.0`、`"443"` 是 `invalid_json`），1–65535（越界是 `bad_target`） |

字段类型不对（缺失、不是字符串、`port` 不是整数）答 `invalid_json`，**先于**一切值检查；然后是语法（`bad_target`），然后才看有没有隧道代理、你的进程、节点——**每一个便宜的拒绝都在打开任何 socket 之前**。会话身份调它答 `permission_denied`（界面没有进程可以接收 socket）。

**成功：**

```json
{ "status": "ok", "proto": "tcp",
  "protocolInfo": "<标准 base64>", "protocolInfoBytes": 628,
  "node": { "id": "n1", "name": "香港", "type": "socks5" } }
```

UDP 另有 `"maxPayload": <每个数据报的最大负载字节数>` 与 `"idleTimeoutMs": 120000`。`node` 告诉你实际用的是哪个节点（`via:"active"` 时尤其有用），同样没有地址与凭据。

**收养：**

```c
// protocolInfo 按标准 base64 解码；解出来的字节数必须 == protocolInfoBytes == sizeof(WSAPROTOCOL_INFOW)
WSAPROTOCOL_INFOW info;
memcpy(&info, decoded, sizeof info);
SOCKET s = WSASocketW(FROM_PROTOCOL_INFO, FROM_PROTOCOL_INFO, FROM_PROTOCOL_INFO,
                      &info, 0, WSA_FLAG_OVERLAPPED | WSA_FLAG_NO_HANDLE_INHERIT);
if (s != INVALID_SOCKET && !SetHandleInformation((HANDLE)s, HANDLE_FLAG_INHERIT, 0)) {
    closesocket(s);                    // 清不掉就别用：它会被你拉起的子进程继承
    s = INVALID_SOCKET;
}
```

- 大小对不上就**别调** `WSASocketW`——那是一次读越界。
- **`info` 只能用一次。** 复制出来的是一个句柄，收养一次就消费了它；再收养一次不会得到第二个连接。
- **必须再调一次 `SetHandleInformation(s, HANDLE_FLAG_INHERIT, 0)`。** 收养时传的 `WSA_FLAG_NO_HANDLE_INHERIT` **管不到**这个句柄：它是 Ghost 用 `WSADuplicateSocketW` 在你进程里**事先**建好的，生来可继承（实测 Windows 11 26100，TCP 与 UDP 都是；Ghost 的 `test_plugin_tunnel` 有一行钉住这个行为）。不清掉，你之后用 `bInheritHandles=TRUE` 拉起的每个子进程都会继承一条经用户代理的连接。更早的句柄可继承窗口（Ghost 复制之后、你收到答复之前）你关不掉，所以插件**不要**用 `bInheritHandles=TRUE` 拉子进程；要继承就用 `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` 列出确切的句柄。`WSA_FLAG_NO_HANDLE_INHERIT` 照传无害。
- TCP 的 socket 是**阻塞模式、没有收发超时**，也没挂任何事件或完成端口——按你自己的需要设（`TCP_NODELAY`、keepalive、超时）。TCP 隧道一经交出就完全是你的：Ghost 不跟踪它，停用插件、改节点都不会关掉它（停用会停掉你的进程，那时它随进程关闭）。
- `getpeername` 显示的是**节点的地址**（你连着的是节点，目的地在它后面）。拿地址不需要任何权限，这不是泄漏，但也意味着节点地址对你不是秘密。

### 10.4 UDP

`proto:"udp"` 只在 SOCKS5 节点上、且该节点的 UDP 中继此刻**开启 + 已验证 + 健康**、`udpRelay` 能力位开着时可用——与 Ghost 给被管目标开 UDP 的是**同一个闸门**（[`../architecture/udp-relay.md`](../architecture/udp-relay.md)），`upstream.list` 的 `udp` 就是它；否则 `upstream_udp_unavailable`。

Ghost 自己做 UDP ASSOCIATE 并**中继**：控制连接与到节点中继的 socket 都留在 Ghost 里，你拿到的是一对**已互相 connect 的回环 UDP socket** 中的一端。

- **一个会话，一个目的地。** 你 `send` 裸负载（不带 SOCKS5 头），全部发往请求里的 `host:port`；要别的目的地就再开一个。
- 超过 `maxPayload` 的数据报被**丢弃**（`65507 − 头长`，头长取决于目的地的形式），像 UDP 本来那样，不报错。
- **只有来自目的地的回包会到你手里**：端口必须一致；目的地是字面地址时地址也必须一致；是主机名时只比端口（节点可能用它解析出的地址作答）。畸形的、分片的（`FRAG ≠ 0`）回包丢弃。
- ⚠️ 有些 SOCKS5 中继的 UDP 不支持主机名目的地（ATYP 3）。这样的节点上用名字开的会话收不到回包——换成字面地址。
- **关闭的信号是 `WSAECONNRESET`。** 中继被拆之后，你的下一次 `send` 之后的 `recv` 失败于它；读到它就当会话已结束、重开一个（如果还被允许）。
- 中继在这些时候被拆：节点结束 association（控制连接上出现任何字节、EOF 或错误——RFC 1928 §7）；你的进程退出；**两个方向都没有数据报满 `idleTimeoutMs`**（入站的也算活动）；你关掉自己那端且之后有数据报回来；以及**不再被允许**——你的 token 变了（停用、卸载、宿主丢失、重启）、`udpRelay` 能力位关了、节点健康离开 Usable、节点被删、节点的地址/端口/用户名/密码被改、节点不再满足 UDP 闸门。「不再被允许」每秒核对一次（Ghost 的中继线程）。

### 10.5 截止时间契约与失败

- Ghost 为一次隧道请求阻塞至多 **10 秒**（连接节点 + 握手共用一个截止时间，连接本身另有 5 秒上限）。
- ⚠️ **你至少要等 30 秒**（`kTunnelClientWaitMs`），而且**从不重试一个答复丢了的隧道请求**。答复里带着一个**已经复制进你进程**的 socket：你提前放弃、或答复在路上丢了，那个句柄就在你进程里泄漏到进程退出，而且连着一条经用户代理的连接。一次请求、一次收养，超时就当失败处理。
- **握手之后马上调，`plugin_not_running` 是暂时的。** 宿主的 `running` 报告（带你的 pid）是异步送达的，而触发它的正是你刚写出的握手回执——握手后的第一个调用可能跑在它前面。短暂退避后重试几次（例如 200 ms 间隔、数秒为限）。
- **fail closed，永远不回落直连。** 任何失败——节点不可达、超时、认证失败、拒绝、你的插件不被支持——都**不是**「那就直连吧」的理由：用户选节点正是为了不让目的地看到真实地址。转发类插件遇到失败就关掉客户端连接、记下错误码。Ghost 自己的每一条失败路径上，目的地都收不到任何直连。
- `via:"active"` **只在打开时解析一次**：用户之后切换激活节点，已经建好的 TCP 隧道与 UDP 中继仍走原来的节点（UDP 中继在原节点被删或被改时才被拆）。新连接会用新的激活节点。
- 失败的码见 [`spec-errors.md`](spec-errors.md) §9：`bad_target`、`upstream_not_found`、`upstream_invalid`、`upstream_udp_unavailable`、`upstream_unreachable`、`upstream_timeout`、`upstream_auth_failed`、`upstream_refused`、`plugin_not_running`、`tunnel_unsupported`、`tunnel_limit`、`tunnel_failed`、`tunnel_unavailable`；外加共用的 `invalid_json`、`permission_denied`、`rate_limited`。形状都是 `{"status":"error","error":"<码>"}`，HTTP 200（闸门的 401/403/429 除外）。

### 10.6 预算、上限、日志与边界

- **每一次 `list` 与 `tunnel` 都花一次 §1 的调用预算**（每秒 20、突发 100）——它就是一个插件能让 Ghost 经用户节点开连接的频率上限。另有在途与中继上限（[`spec-limits.md`](spec-limits.md) §7.2）：同时在建的隧道每插件 8、全局 32；同时存在的 UDP 中继每插件 32、全局 128；超出答 `tunnel_limit`。
- 日志（Control 平面，`tag="Tunnel"`）：每次运行对每个（插件、节点、协议）的**第一次成功**记一条 Info——谁在用用户的代理身份；上游类失败（`upstream_unreachable`/`timeout`/`auth_failed`/`refused`）、节点结束 UDP association、中继到节点的那个 socket 出错，按（插件、节点、码）每 60 秒至多一条 Warn，带它代表的次数；因不再被允许而拆掉的中继记 Info。**从不记凭据，从不记目的地**；请求校验类的拒绝不记。
- ⚠️ **「API 不交出凭据」不是安全边界。** 节点凭据以明文存在 `%LOCALAPPDATA%\GhostProxifier\config.json`，同用户的任何进程——包括你的插件——本来就能读它。这条设计的意义是让**诚实的**插件不必碰凭据、不必写代理协议代码，并让用户知道哪个插件在用他的代理身份（审计行），而不是挡住一个恶意插件。授予 `upstream.connect` 之前，用户要信任这个插件。
- 被偷走的插件 token（[`../architecture/plugin-center.md`](../architecture/plugin-center.md) §7.11）能以你的身份开隧道，但 socket 只会被复制进**你的**进程（映像核对），不会进偷 token 的那个进程。映像核对从 Ghost 的数据根**按句柄逐级**打开 `plugins\<id>\<version>\<entry>`，不跟随任何 junction / 符号链接（这棵树同用户可写），再与目标进程映像比对文件身份——在 `<version>` 上种一个挂载点，换不来一个「对得上」的映像。
- **重启窗口（已知边界）**：Ghost 拿到「你的进程」与建好隧道之间，你的插件若恰好被重启，TCP socket 会被交给**新**实例（同一个插件、同一个映像，核对照样通过）；新实例没有请求它，这一个句柄会留在新实例里直到它退出。UDP 中继不受此限：它的「仍被允许」判定比对的是请求时那个 token，重启即换 token，中继在一个检查周期内被拆掉。
