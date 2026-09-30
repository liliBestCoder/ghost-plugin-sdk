# 限值与语法总表（spec-limits v1）

> **这是全部数值上限、超时、正则的唯一定义处。** 其余规范一律引用本表，不重复写数字。
> 代码侧的唯一真源是 `src/shared/contract_plugin_limits.h` 与 `src/shared/policy_plugin_id.h`；本文是它们的文档投影。

同 [`spec-errors.md`](spec-errors.md) 之于原因码：一个数字在两份文档里各写一遍，两份就会漂移，而漂移的那天没有任何东西会报错。**改一个值 = 改本表 + 改那个头文件，两处，不多不少。**（开发者工具 `tools/plugin/gpkg.py` 顶部另有一份 Python 镜像，它是便利不是闸门，但也要跟着改。）

表里「常量」一栏写的名字都在 `contract_plugin_limits.h` 里，**除非**旁边标了「PR ③」之类——那是还没有代码用到、随那个 PR 进头文件的常量。

---

## 1. 宿主与插件进程

| 项 | 值 | 常量 |
|---|---|---|
| 握手超时（插件必须在此时间内写完 stdout 第一行） | **10 秒** | `kHandshakeTimeoutMs` |
| 读取插件 stdout 的行数 | **1**（其余丢弃，不转日志） | — |
| 重拉次数上限 | **3** | `kMaxRestarts` |
| 重拉退避 | **1 / 4 / 9 秒** | `kRestartBackoffSec[]` |
| 稳定运行后重拉预算归零（插件与宿主都是） | **10 分钟** | `kStableRunResetMs` |
| 宿主 stdin 一行 | **16 KB**（超长整行丢弃） | `kMaxHostLineBytes` |
| 握手应答（stdout 第一行） | **4 KB**（超长即 `plugin_handshake_invalid`） | `kMaxHandshakeLineBytes` |
| `uiUrl` | **≤ 2048 字节**，规则见 `spec-host-protocol.md` §4 | `kMaxUiUrlBytes` |
| 宿主退出时冲刷状态报告 | **3 秒**（在每个插件的优雅停止之后） | `kHostStatusFlushMs` |
| 优雅停止等待（之后 `TerminateJobObject`） | **3 秒** | `kGracefulStopMs` |
| `POST /api/plugin-status` 每批条数 | **50**（超出截断，不整批拒） | `kMaxStatusBatch` |
| `start` 发出后等首次状态回报 | **30 秒**，超时判宿主失联并重拉 | `kStartAckTimeoutMs` |

主进程一侧（`host_bridge.h` 的 `HostBridgeConfig` 默认值，不在 `contract_plugin_limits.h` 里——它们是主进程怎么对待宿主，不是协议的一部分）：宿主重拉至多 **3** 次、退避 `1000*n*n` 毫秒；stdin 单行写入 **5 秒**无返回判失联；`Stop()` 发 shutdown 后等 **8 秒**（`kGracefulStopMs` + `kHostStatusFlushMs` + 2 秒，长过宿主自己的有序退出）再终止 Job；报过 `running` 的 pid 每 **1 秒**核对一次是否还在；宿主的状态 POST 失败重发 **1** 次、间隔 **500 ms**；Shell 尚未就绪时重试 **6** 次、间隔 **10 秒**。

重拉次数与退避刻意与 `ghost_watchdog.exe` 相同（`log/service/watchdog_host.h` 的 `Supervise(maxRestarts=3)`）——两处若要分家，先想清楚为什么一个插件比一个 watchdog 更值得重试。

## 2. 包与解压

| 项 | 值 | 常量 |
|---|---|---|
| 整个 `.gpkg` | **64 MB**。store 格式下包体就是解压后的总量；包整个读进内存校验后才写盘 | `kMaxPackageBytes` |
| 从本地安装的请求体（PR ⑦） | **恰好 `kMaxPackageBytes`**，**只对会话身份、只这一条路由**（`POST /api/plugins/install-local`，`http_router.h` 的 `BodyCeiling`）；其余任何路由、任何身份的请求体上限都是 **2 MB**。请求头决定一切：超限答 413 且一个字节的体都不读；`chunked` 答 411；每进程同一时刻至多**一个**超过 2 MB 上限的请求在读体（第二个 503），读体 30 秒空闲即断、总时限 `kPackageTimeoutMs` | `kMaxPackageBytes` / `ghost_http::kDefaultMaxBodyBytes` |
| 单条目 | **64 MB** | `kMaxEntryBytes` |
| 条目数 | **4096** | `kMaxEntries` |
| 条目名 | ≤ **240** 字节 UTF-8，规则见 `src/shared/policy_zip_name.h` | `ghost_zip::kMaxZipEntryNameBytes` |
| 图标文件 | **256 KB**，边长 **512 px** | `kMaxIconBytes` / `kMaxIconEdge`（PR ⑤） |
| 支持的压缩方法 | **只有 store**（方法 0）。**不支持** deflate / zip64 / 加密 / 多卷 / 归档注释 | — |
| 布局 | **规范布局**：首个本地头在偏移 0，条目首尾相接、按中央目录顺序排列，最后一个条目紧接中央目录，EOCD 是文件最后 22 字节。Python `zipfile`（`gpkg.py pack`）与 .NET `ZipArchive` 默认就写这个形状；`gpkg.py` 不写归档注释 | — |

store-only 意味着没有 zip bomb：压缩比恒为 1，所以没有压缩比上限。超限一律整包拒绝（`zip_too_large` / `zip_too_many_entries`），不合规范的结构一律 `zip_invalid`，删 staging，现有版本不动。

图标超限**不是**拒绝理由：安装器不读图标，超限或缺失的图标由插件页换成默认图标（`gpkg.py pack` 在打包时就拒超限的图标，让开发者早知道）。

条目名的规则里有两条不显然的：段尾不许 `.` 或空格（Win32 会把它们剥掉，`a./b` 打开的是 `a\b`），以及**不许 `~` 后跟数字**（8.3 短名别名：`LONGDI~1` 可能是某个已有长名目录的另一种拼法，两个看起来不同、每条名字检查都能过的条目会落进同一个目录——git 的 `protectNTFS` 规则）。

## 3. 网络

| 项 | 值 | 常量 |
|---|---|---|
| 重定向跳数上限 | **3**，且每一跳必须是 https。与自动升级**数值相同**（两个常量，见下） | `kMaxRedirects` |
| 文档请求总时限（注册表、发布描述、`.sig`，每条路由一次） | **30 秒** | `kDocumentTimeoutMs` |
| 包下载总时限（每条路由一次） | **300 秒** | `kPackageTimeoutMs` |
| 升级检查间隔 | **每插件每 24 小时至多一次**，且只在用户刷新注册表（打开插件页）时 | `kUpgradeCheckIntervalHours` |
| 安装所依据的注册表的最大年龄 | **10 分钟**，且必须是**本次运行**里刷新成功的那份；否则先刷新 | `kInstallRegistryMaxAgeMs` |
| 商店 iframe 加载超时 | **8 秒**，之后显示离线态 | 前端常量（PR ⑤） |

⚠️ **跳数上限是两个常量，不是一个。** 插件域不许链接升级域，所以 `ghost_plugin::kMaxRedirects` 与 `ghost_update::kMaxRedirects`（`update_policy.h`）各有一份，按意图相等；`test_update_http_redirect` 断言两者相等，哪天有人只改了一边就转红。

**可以去哪、可以被重定向到哪**不是一张主机表，而是按**路径前缀**判的（`src/modules/plugin/domain/plugin_urls.h`；一张含 `github.com` 的主机表等于放行所有人的 Release 资产）：

| 判据 | 放行 |
|---|---|
| 首个 URL | 注册表两个来源**逐字**（及各自的 `.sig`）；外加——只在安装与升级检查时——**正在处理的那个插件**在注册表条目里的 `repo` 的 `releases/latest/download/<资产>` 或 `releases/download/v<规范版本>/<资产>`，资产名 `[A-Za-z0-9._-]`、非空、≤ 160 字符、不以 `.` 开头：别的插件的 Release（哪怕也在注册表里）首跳与重定向都到不了。整条 URL 不得含控制字符、空格、`..`、`\`、`%` |
| 重定向目标 | 以上全部；外加注册表仓库 `liliBestCoder/ghost-plugin-registry` 的**标签跳** `releases/download/<标签>/registry.json` 与 `.../registry.json.sig`（标签 `[A-Za-z0-9._-]{1,64}`，不以 `.` 开头、不含 `..`——GitHub 把 `latest/download` 先 302 到这里，没有这一跳主来源永远读不到；只作重定向目标，不作首个 URL）；以及 `https://objects.githubusercontent.com/` 与 `https://release-assets.githubusercontent.com/` 下的任意路径（签名 URL 带百分号编码的查询串，只查控制字符；那里的内容靠发布描述的 `sha256` 被信任，而不是靠地址） |

`ghostproxifier.com` 只作为注册表备用来源的**那一个确切 URL** 出现，不是整个主机都放行：那里今天没有任何东西会重定向，而放行整个主机正是这张表要避免的。未实测：GitHub 会不会把 owner/repo 重定向到另一种大小写（规范拼法）或改名后的新名字——两种情况今天都会被拒，此时由备用来源提供注册表，而不是凭猜测放宽。

插件自己仓库的 `latest/download/<资产>` 同样会先 302 到 `releases/download/<标签>/<资产>`；因为 `spec-release.md` §3.1 把标签定为 `v<version>`，这一跳本来就在首个 URL 的集合里。**标签不是 `v<version>` 的插件经 `latest` 取不到。**

下载**先看 `package.size`** 再决定要不要下：超出 `kMaxPackageBytes` 即拒，不等下完；下载中多到一个字节即中止，响应提前结束也拒（都是 `package_size_mismatch`）。**大小不符是信任失败而不是网络失败：不回落直连重试**——服务端给错了东西，换条路再要一次不会让它变对。

### 3.1 读进内存的文档

每一份都有上限。网络上来的超限响应被传输层**放弃**、算作这条路由失败（每条路由都这样，最终就是 `<文档>_unreachable`），而不是截短后拿去验签或解析。

| 文档 | 上限 | 常量 |
|---|---|---|
| `registry.json`（网络上来的与本地缓存同一上限） | **1 MB** | `kMaxRegistryBytes` |
| `ghost-plugin.json` | **64 KB** | `kMaxReleaseBytes` |
| 任一 `.sig`（网络或缓存） | **256 字节** | `kMaxSigBytes` |
| 包里的 `manifest.json` | **64 KB**，超出即 `manifest_malformed` | `kMaxManifestBytes` |
| 本地 `plugins.json` | **1 MB**，超出即当作读不了（只读运行、从不回写） | `kMaxPluginsFileBytes` |

## 4. 清单字段

| 字段 | 约束 | 常量 / 正则 |
|---|---|---|
| `id` | `^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?){2,}$`，≤ **64** 字节，且**第一段不得是 Windows 设备名**（`con`、`prn`、`aux`、`nul`、`com0`–`com9`、`lpt0`–`lpt9`：`plugins\nul.x.y\` 打开的是 NUL 设备而不是一个目录） | `ghost_plugin::IsPluginId` |
| `version` | `^(0\|[1-9]\d{0,4})(\.(0\|[1-9]\d{0,4})){2}$`，每段 ≤ **65535**。**规范形式**：除单独一个 `0` 外不许前导零（`01.0.0` 拒绝——它与 `1.0.0` 值相等，却是另一个目录名、另一个身份） | `ghost_plugin::IsPluginVersion` |
| `name.{zh,en}` | **1–64** 个码点 | `kMaxNameChars` |
| `description.{zh,en}` | ≤ **240** 个码点 | `kMaxDescChars` |
| `author.name` | ≤ **64** 个码点 | `kMaxAuthorChars` |
| `args` | ≤ **16** 条，每条 ≤ **256** 字节（UTF-8） | `kMaxArgs` / `kMaxArgBytes` |
| `runtime.minVersion` | `^[0-9]{1,5}(\.[0-9]{1,5}){0,2}$`（解释器自己的版本，一到三段，允许前导零） | `plugin_docs.h` 的 `IsRuntimeVersion` |
| `permissions` | 只认 [`spec-plugin-api.md`](spec-plugin-api.md) §2 那七个名字（`upstream.connect` 是 PR ⑩ 的第七个，发布描述的 `minAppVersion` 要 ≥ `1.2.1`；它还要 `.exe` 入口与 `runtime.kind` 为 `none`——这两条是 `gpkg.py pack` 的工具检查，客户端解析器不判，见 `spec-manifest.md` §2） | `ghost_plugin::IsKnownPermission`（`contract_plugin_permissions.h`） |

「字符」一律指 **Unicode 码点**（Python 的 `len()` 数的那个），「字节」指 UTF-8 字节——本节只有 `id` 与 `args` 按字节。

`id` 与 `version` 的校验器**同时是拼文件路径时的转义防线**（包目录是 `plugins\<id>\<version>\`）——同 `src/shared/contract_wallpaper_id.h` 的理由：字符集抄错的那一侧不是「校验漏了」，是「拼得出一条 `..\` 逃逸路径」。所以两者共用**一份** `policy_plugin_id.h`，不是两份互相镜像的正则。

## 5. 注册表与发布描述

| 字段 | 约束 |
|---|---|
| `repo` | `^[A-Za-z0-9._-]{1,39}/[A-Za-z0-9._-]{1,100}$`，两段**都不得**以 `.` 开头（于是也不会是 `.` 或 `..`），且整串任何位置都不含 `..` |
| `package.name` | `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.gpkg$`（扩展名区分大小写），且不含 `..` —— 它会被拼进下载 URL，所以和 `id` 一样，字符集就是转义防线。**不要求**等于 `<id>-<version>.gpkg`（`gpkg.py` 写的是这个，但规则只是语法）。下载到 staging 时文件名固定为 `pkg.gpkg`，不用它 |
| `devKey` / 根公钥 | base64，**88 字符**（原始 `X‖Y` 64 字节） |
| 签名 `.sig` | base64，**88 字符**（原始 `r‖s` 64 字节）。结尾的空格、制表符、CR、LF 允许且只剥这些；开头的空白与其他控制字符不剥。第 86 个字符里没用到的低 4 位不要求为 0（Python 的解码器不管它们，拒绝别的编码器写的签名是白拒） |
| `package.sha256` | **64** 位小写 hex |
| `package.size` | 非负整数。超过 `kMaxPackageBytes` 仍是良构文档，由信任链第 7 步拒绝 |
| `minAppVersion` | 规范三段版本，同 `version` |
| `paid` | 缺失、`false`，或 `{"trialDays": 1..90}`；**`true` 拒绝**（它没说试用多久） |
| `paid.trialDays` | **1–90** |
| `revoked[].versions` | `"*"` 或规范版本组成的数组，**可以为空**（什么都不吊销，但不因此拒掉整份注册表——拒掉的注册表里的**新**吊销也就送不到了） |
| `revoked[].reason` | 可选，≤ **200** 个码点（会展示给用户） |
| `notes.{zh,en}` | 各自可选，≤ **2000** 个码点 |
| `seq` | 非负整数，≤ `LLONG_MAX`；单调递增，小于本地最大值即 `registry_rollback` |
| `plugins[].id` | 在一份注册表里**唯一**，重复即整份 `registry_malformed`（否则「用哪个 `devKey`」取决于扫描顺序） |

三份文档（清单、注册表、发布描述）共同的规则：UTF-8 JSON、顶层是对象、**开头带 UTF-8 BOM 即拒**（nlohmann 会悄悄跳过它、Python 的 `json.loads` 会拒，而签名覆盖的是原始字节）；`v` 必须是**整数** `1`（`true`、`1.0` 都不算）；未知字段忽略；已知字段类型不对（含 `null`）整份拒绝。解析在 `src/modules/plugin/domain/plugin_docs.h`，开发者侧镜像是 `gpkg.py` 的 `validate_manifest` / `validate_release`；注册表只有 C++ 一侧（只有维护者写它）。

## 6. 许可

| 项 | 值 |
|---|---|
| `licenseId` | `^[A-Za-z0-9-]{1,64}$`（**同时是文件名**，字符集即转义防线） |
| `machineId` | **16** 位小写 hex（SHA-256 前 **8** 字节），或 `"*"` |
| 时钟偏差容限（`issuedAt` 可比现在晚多少） | **24 小时** |
| 编码 | base64url（RFC 4648 §5），**不带填充**，末字符未用位必须为 0 |
| 整块上限 `kMaxLicenseBlockBytes` | **16 KB**（trim 后；读一个字节之前先看大小；每个 `licenses\*.key` 也按它读） |
| `licenses\` 文件数 `kMaxLicenseFiles` | **64**（按文件名排序，多出来的以 `license_too_many` 拒绝；满 64 个时激活新 id 写之前即答 `license_too_many`） |
| `plugins` 项数 `kMaxLicensePlugins` | **64** |
| 时钟偏差 `kLicenseClockSkewSec` | 86400（即上面的 24 小时） |

**算术要自洽**：64 个 id × 至多 64 字节 + 每项引号逗号 3 字节 ≈ 4.3 KB，加固定字段不到 4.6 KB，base64url 膨胀 ×4/3 ≈ 6 KB，再加 86 字符签名，仍在 16 KB 之内；256 个 id 会超（≈ 17.1 KB 的 payload，再 ×4/3），所以取 64。夹具 `kLicManyPlugins`（64 个 64 字节的 id）就是这个最坏情况的实测（≈ 6.1 KB），`sign_license.py issue` 对超过 16 KB 的整块拒绝出码。

权限名的那张闭集在 [`spec-plugin-api.md`](spec-plugin-api.md) §2 —— 它是一份语义表而不是一个上限，解释「这个权限让你看到什么」的地方才该是它的家。

> ⚠️ **两种 base64 并存，别混。** 许可块用 **base64url 不带填充**（它要进 URL 和输入框）；`.sig` 文件与公钥用**标准 base64 带填充**（它们是独立文件，没有 URL 安全的需求）。这是文档里唯一一处两种编码同时出现的地方，写在这里免得实现时按一种写完两处。

## 7. `log.write`（继承 `log.ingest` 的既有限值）

这一组**不是本设计新定的**，是复用主程序已有的 `log.ingest` 端点，值以 `CmdLogIngest` 的实现为准：

| 项 | 值 |
|---|---|
| 每次条数 | 100（超出截断） |
| 每条 `text` | 2 KB |
| `fields` | ≤ 16 项，每项值 ≤ 512 字节 |
| 令牌桶 | watchdog（端点）：20/s，突发 100。**插件：每插件一个，5/s、突发 50**（`kPluginLogRatePerSec`/`kPluginLogBurst`，PR ④），与端点桶分开；按条目扣，超出计入 `rateDropped`。比 watchdog 的低是刻意的：插件日志落 Control 平面，与注入、ACL、崩溃、许可事件**共用那个 1000 条的 Control 环**。**按插件算**：一个插件满速写，约 (1000 − 50) / 5 秒、三分多钟把环刷一遍（突发 50 条立刻进去）；k 个插件一起写就快 k 倍，再加 k 个 50 条的突发——这个速率约束的是单个插件，不是整个环（20/s 时单个插件不到一分钟） |
| 超限汇总 | 每插件每分钟至多一条 Control/Warn（`tag="Ingest"`、`src=ui`、字段 `plugin=<id>`） |
| `level` | 只认 `info` / `warn` / `error`；`debug` 提升为 `info` |

### 7.1 插件 API 调用（PR ④）

每个插件 token 对本地控制接口的**调用**预算，与上面的条目预算是两回事：一次请求花一次调用，请求里的每一条日志花一次条目。

| 项 | 值 | 常量（`contract_plugin_limits.h`） |
|---|---|---|
| 速率 | 每秒 20 次 | `kPluginApiRatePerSec` |
| 突发 | 100 次 | `kPluginApiBurst` |
| 计费 | 身份解析为 Plugin **之后**、权限判定**之前**扣：被 403 的请求也花一次；错的 token 不属于任何插件，不花；打开 `/events` 花一次 | — |
| 超出 | HTTP 429 `rate_limited`，不带 `Retry-After`，不记日志 | — |
| 生命期 | 跟 token 所在的那条运行记录走。**沿用**（不回满）只有两种情况：记录还在时的再次启用（中间没有停用），与换版本（新 token）。**先停用再启用拿到新桶**——停用会删掉那条运行记录，桶随之而去；卸载同理 | — |
| 同时打开的 `/events` 流 | 每插件至多 2 条；第 3 条答 429 `rate_limited`；流结束（包括 token 被撤销而被服务端结束）即归还名额 | `kMaxPluginEventStreams` |
| `plugin.icon` 读取上限 | `kMaxIconBytes` = 256 KB（与包内图标上限同一个数） | `kMaxIconBytes` |

### 7.2 上游隧道（PR ⑩，`upstream.connect`）

[`spec-plugin-api.md`](spec-plugin-api.md) §10。每一次 `upstream.list` / `upstream.tunnel` 另外照常花 §7.1 的一次调用——调用预算就是一个插件能让 Ghost 经用户节点开连接的频率上限；下表约束的是**同时**有多少。

| 项 | 值 | 常量（`contract_plugin_limits.h`） |
|---|---|---|
| 一次隧道请求的总截止时间（连接节点 + 握手，一个 `steady_clock` 截止时间，UDP 的 ASSOCIATE 也在其中） | **10 秒** | `kTunnelDeadlineMs` |
| 其中到节点的 TCP 连接本身 | **5 秒**（超过答 `upstream_unreachable`，不是 `upstream_timeout`） | `kTunnelConnectMs` |
| 插件等一次隧道答复**至少**要等多久 | **30 秒**。文档性常量，Ghost 不执行它：提前放弃的插件会丢掉一个已经复制进自己进程的 socket，句柄泄漏到进程退出 | `kTunnelClientWaitMs` |
| 同时在建的隧道（每个都是一条阻塞在连接/握手里的请求线程） | 每插件 **8**、全局 **32** | `kMaxPluginTunnelsInFlight` / `kMaxTunnelsInFlight` |
| 同时存在的 UDP 中继 | 每插件 **32**、全局 **128**（一个 `WSAPoll` 线程服务全部） | `kMaxPluginUdpRelays` / `kMaxUdpRelays` |
| UDP 中继空闲拆除（两个方向都没有数据报） | **120 秒**（成功答复里的 `idleTimeoutMs`） | `kUdpRelayIdleMs` |
| UDP 数据报负载 | `65507 − SOCKS5 头长`（成功答复里的 `maxPayload`；超出的数据报丢弃） | — |
| `host` | ≤ **253** 字节（DNS 名字上限，也是 SOCKS5 域名长度字节与 CONNECT 行要承载的最大值）；语法见 `spec-plugin-api.md` §10.3 | `kMaxTunnelHostBytes` |
| `nodeId` | `^[A-Za-z0-9_.-]{1,64}$` | `kMaxNodeIdBytes` |
| `WSAPROTOCOL_INFOW` | **628** 字节（`protocolInfoBytes`；解码后必须恰好等于它才收养） | — |

超出在途或中继上限答 `tunnel_limit`。主进程一侧另有两个值不在 `contract_plugin_limits.h` 里——它们是 Ghost 怎么对待自己的中继，不是协议的一部分（`plugin_tunnel.h` 的 `BrokerConfig` 默认值，组装根只设了日志）：已建 UDP 中继的「空闲 / 进程已退出 / 仍被允许」核对**每 1 秒**一次（`checkEveryMs`）；上游类失败的 Warn 按（插件、节点、码）**每 60 秒至多一条**（`warnEveryMs`），下一条带上被压下的次数。

## 8. 商店桥

| 项 | 值 |
|---|---|
| `resize` 高度夹取范围 | **200 – 4000** px |
| iframe `sandbox` | `allow-scripts allow-same-origin allow-forms` —— **没有 `allow-popups`**，见 `spec-store-bridge.md` §2 |
| iframe `referrerpolicy` | `no-referrer` |
| URL 参数 | 只有 `app`（核心三段）、`lang`、`mode` |
| 同时进行的商店操作 | 从 `accepted` 到确认框被回答：**1** 个；其余答 `user_cancelled` |
| 跟踪中的商店操作 | 至多 **8** 个，每个至多 **10 分钟**（之后静默遗忘） |
| `openLink` | 要用户激活，至多 **1 秒 1 个** |
| 确认框「确认」按钮 | 内容就位后 **1000 ms** 内禁用 |
| 取消后冷却 | 用户取消商店发起的确认框后 **2 秒**内忽略商店的改状态消息 |
