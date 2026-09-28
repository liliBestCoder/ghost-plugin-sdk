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

表按**命令名**索引，不按路由路径 —— 权限判定发生在命令层，而「路由 ↔ 命令」在这个仓库是漂移高发区（`test_router` 与 `test_command_contract` 已经是两张各自手写的表，再加第三张只会更糟）。每条命令对应的路由在 §3–§6 各自写明。

> ⚠️ **`events.read.data` 等同于用户的完整访问历史。** Data 平面携带每个被管目标的**每一次 DNS 查询与连接目的地**。对一个隐私工具来说这是全部数据里最敏感的那份，所以它和 `events.read.control` 是两个独立权限，用户可以只给后者。申请它要有真实理由，安装确认框会明确告诉用户这意味着什么。

**v1 就这六个。** 没有任何写配置、改目标、启动注入类权限。数据面抓包、限速、PAT 注入是 issue #12 的子项目 D，三个插件各自立项，各自论证要新增哪个权限位——那类权限能做的事比上面几个大一个量级，不该顺带加进来。

### 2.1 默认拒绝

`src/modules/api/wire/plugin_permissions.h` 是唯一那张表。**不在表里的命令，对插件身份一律 403**（`permission_denied`），与插件声明了什么无关。于是将来新增的任何路由天然对插件关闭，不依赖谁记得去加一条禁止。「不在表里」也包括：未知路径、`OPTIONS` 预检、方法不对的已知路径——插件原生进程不发预检，`Access-Control-Allow-Headers` 里也**不列** `X-Ghost-Plugin-Token`。

**只有已授予的权限算数。** 升级后新申请、还没被用户确认的权限（`pendingPermissions`）从不进 `start` 行，也就从不进这张表的判定——`test_plugin_service` 38p 钉住：一次把 `{stats.read}` 扩成 `{events.read.control}` 的升级之后，哪怕记录被启用，启动参数里仍是旧的 `{stats.read}`。清单里写了表外的名字，安装就以 `unknown_permission` 整包拒绝；即便如此，这张表对传进来的名字**再判一次**，不认识的名字什么都不放行。

`test_plugin_permissions` 遍历**整张路由表**断言这一点，期望值是测试里**手写**的名单（不是从被测的表反推）：持有全部六个权限的插件恰好够得到六条命令，一个权限都没有的插件什么都够不到，宿主只够得到 `plugin.status`。每加一条新路由，若没有同时把它加进权限表，对插件的答案必然仍是拒绝。

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
- **未脱敏的上游凭据**。
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
