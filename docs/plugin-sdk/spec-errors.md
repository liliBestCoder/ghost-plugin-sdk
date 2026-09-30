# 拒绝原因码（spec-errors v1）

> **这是全部原因码的唯一定义处。** 其余规范一律引用本表，不重复定义、不自造。
> 代码侧的唯一真源是 `src/shared/contract_plugin_errors.h`；本文是它的文档投影，两者逐条对应。
>
> 那个头文件**只含已有代码产生的码**（目前是 PR ② 的信任链与安装）。本文还列着授权、宿主、权限与商店桥的码——它们的定义先写在这里、随产生它们的 PR 进头文件，每一节的标题旁标着是哪个 PR。**「本文有、头文件没有」就是「还没有任何代码会返回它」**，不是漏抄。

原因码是**稳定标识符**：一旦发布就不改名、不改语义。要表达新情况就加新码。

界面文案由 `plugins.err.<code>` 的 i18n 键给出，**不是**把原因码直接显示给用户——但日志里写的是原因码，因为那是提 issue 时唯一有用的东西。

---

> ⚠️ **码本身不带前缀。** 下面的分节标题只是给人看的分组，线上传的就是 `package_hash_mismatch` 这样的裸串。别按标题拼成 `trust.package_hash_mismatch`。

## 1. 信任链（PR ②，已落地）

| 码 | 含义 | 用户看到的大意 |
|---|---|---|
| `registry_unreachable` | 注册表主备两处、每条路由都拉不到（含响应超过 `kMaxRegistryBytes` / `kMaxSigBytes` 而被传输放弃的）。本地已验过的缓存**照常在用**（插件页显示为 `stale`），这个码只说「这次刷新没成」 | 无法连接插件服务，请检查网络 |
| `registry_bad_sig` | 注册表签名用内置根公钥验不过 | 插件服务数据校验失败，已忽略 |
| `registry_malformed` | 注册表能验签但结构不合法（字段缺失、类型不对、`v` 不认识、同一 id 出现两次、`paid: true`——规则见 [`spec-release.md`](spec-release.md) §2.2） | 同上 |
| `registry_rollback` | 拉到的 `seq` 小于本地记录的最大值 | 同上（并继续用缓存） |
| `plugin_not_listed` | 该 id 不在注册表 `plugins[]` 里 | 这个插件不在官方目录中 |
| `plugin_delisted` | 注册表条目 `status: "delisted"` | 该插件已下架，无法安装 |
| `plugin_revoked` | 命中注册表 `revoked[]`。`"*"` 在第 3 步判；**精确版本在第 4 步（发布签名）之后判**——版本号只有验过签才算数，见 [`spec-release.md`](spec-release.md) §4。启用一个已被吊销的已装版本也答这个码 | 该插件已被撤回（附 `reason`） |
| `plugin_below_min_version` | 要装的版本低于注册表条目的 `minVersion` | 该版本已被官方停止支持，请安装最新版 |
| `release_unreachable` | 发布描述或 `.sig` 拉不到（含超过 `kMaxReleaseBytes` / `kMaxSigBytes` 而被放弃的） | 无法获取插件发布信息 |
| `release_bad_sig` | 发布描述签名用注册表里的 `devKey` 验不过 | 插件包校验失败，已取消安装 |
| `release_malformed` | 发布描述结构不合法 | 同上 |
| `release_id_mismatch` | 发布描述的 `id` 与请求安装的 id 不符 | 同上 |
| `package_unreachable` | `.gpkg` 下不到 | 插件包下载失败 |
| `package_size_mismatch` | 声明的 `package.size` 超过 `kMaxPackageBytes`（下载前就判），或实际字节数与 `package.size` 不符（多到一个字节即中止，或响应提前正常结束）。**这是信任失败，不是网络失败：不回落直连重试** | 插件包校验失败 |
| `package_hash_mismatch` | SHA-256 与 `package.sha256` 不符 | 同上 |
| `manifest_missing` | 包根没有 `manifest.json` | 插件包格式错误 |
| `manifest_malformed` | `manifest.json` 结构不合法，或超过 `kMaxManifestBytes` | 同上 |
| `manifest_mismatch` | 清单的 `id` / `version` 与发布描述不符 | 插件包校验失败 |
| `app_too_old` | 本机应用版本 < `minAppVersion` | 需要升级 Ghost Proxifier 到 x.y.z |

**判定顺序是契约**：先信任（注册表验签 → `seq` → 上架/吊销/`minVersion` → 发布签名），后一致性（`release_id_mismatch` → size → hash → `manifest_mismatch`），最后兼容性（`app_too_old`）。逐步顺序见 [`spec-release.md`](spec-release.md) §4。

顺序决定用户看到哪一条，而「签名不对」比「版本太老」更该先说——一个被换过的包不该以「请升级」收场。

## 2. 包与解压（PR ②，已落地）

数值上限见 [`spec-limits.md`](spec-limits.md) §2 —— 本表只说**什么情况**触发哪个码，不重复写阈值。

| 码 | 含义 |
|---|---|
| `zip_invalid` | 不是合法 zip、结构对不上、**压缩方法不是 store**（v1 只支持 store，见 `spec-limits.md` §2）、加密、zip64、多卷、带归档注释、**布局不规范**（首个本地头不在偏移 0、条目之间有空隙/重叠/乱序）、本地头与中央目录不一致、CRC 不符、未置 UTF-8 标志位却含非 ASCII 名、两个条目名按 NTFS 大小写规则相同或一个文件名同时是另一条目的目录前缀、目录条目大小非 0 |
| `zip_unsafe_path` | 条目名不满足 `src/shared/policy_zip_name.h` 的 `IsSafeZipEntryName`（`..`/`.` 段、以 `/` 开头、含 `\` 或 `:`、段尾是 `.` 或空格、DOS 设备名、`~` 后跟数字的 8.3 别名、控制字符与 `<>"\|?*`、非法 UTF-8、超过 240 字节），或条目是符号链接 / 重解析点 |
| `zip_too_large` | 单条目或全包超过上限。从本地安装（PR ⑦）时，请求体超过 `kMaxPackageBytes` 的包在被解析之前就答它（页面先按文件大小拒绝，见 §8） |
| `zip_too_many_entries` | 条目数超过上限 |
| `disk_full` | 本机写入失败且错误是 `ERROR_DISK_FULL` 或 `ERROR_HANDLE_DISK_FULL`（下载的目标文件，或解包） |
| `io_error` | 其余本机失败：文件系统操作失败、`plugins.json` 拒写（见下）、卸载删不干净、SHA-256 算不出来 |

`plugins.json` 读不干净时（读不了、过大、不是 JSON、字段类型损坏、`v` 不是 1）插件中心**只读运行**：安装、卸载、刷新、启停都在碰任何东西之前答 `io_error`，并另记一条 Warn 说 `plugins.json` 需要修复。理由见 `plugin-center.md` §11。

## 3. 网络（插件服务目前一个都不产生）

| 码 | 含义 |
|---|---|
| `network_unreachable` | 经上游与直连两条路都失败 |
| `http_error` | 收到非 2xx（响应码进 `detail`） |
| `redirect_blocked` | 重定向目标不在白名单主机内，或超过跳数上限，或出现非 https 跳 |
| `tls_error` | TLS 握手或证书校验失败 |
| `rate_limited` | **PR ④ 起由闸门产生，HTTP 429**：一个插件的 token 用完了它自己的调用预算（`spec-limits.md` §7.1：每秒 20、突发 100，每插件一个）。每一次带插件 token 的请求都花一次——被 403 拒掉的也花，打开 `/events` 也花；同一插件已开着 2 条 `/events` 流时再开第 3 条也答它（`kMaxPluginEventStreams`）；错的 token 不属于任何插件，不花任何人的。不带 `Retry-After`，**不记日志**。**`log.ingest` 与 `plugin.status` 的批内超限不产生它**：那是条目预算，丢弃并计数（`rateDropped`），请求本身照常答 `ok`——一批里多出来的几条不该让整批变成错误 |

⚠️ **PR ② 的插件服务不返回本节任何一个码**（`rate_limited` 是闸门的，不是服务的），`network_unreachable` 虽已进头文件也一样。它的每一次请求都是去拿**一份具体的文档**，而「哪份拿不到」才是有用的答案：注册表 → `registry_unreachable`，发布描述 → `release_unreachable`，包 → `package_unreachable`。HTTP 状态码、重定向被拒、TLS 失败这些细节进日志行的正文，不进码。本节的码留给以后不针对某一份文档的请求。

## 4. 授权（已落地：PR ①a；`requires_license`/`trial_expired` 随 PR ①b 落地）

前六条由 `license.activate` 与启动时加载 `licenses\*.key` 产生（`config/domain/license_verify.h`，顺序见 `spec-license.md` §3）；`requires_pro` 由四处门控产生。`requires_license`/`trial_expired` 由付费插件的判定产生（`plugin/domain/paid_gate.h`，`spec-license.md` §7）：`plugin.enable` 同步答它们（什么都不写），服务发出的每一次 start 被它拒绝时记一条带码的 Warn、不下发 `start`；`plugin.list` 每条记录的 `license.verdict` 取 `ok`/`requires_license`/`trial_expired`。回给商店页时两者都折成 `verify_failed`。

| 码 | 含义 |
|---|---|
| `license_malformed` | 整块超过 16 KB，或不是 `base64url(payload).base64url(sig)` 的规范形状，或 payload 不是合法 JSON / 带 BOM / `v` 不是整数 1，或任一字段的类型与形状不对（`licenseId`、`machineId`、`caps`、`plugins`、时间戳）——**在验签之前判** |
| `license_bad_sig` | 用内置根公钥验不过 |
| `license_expired` | `expiresAt <= now`——**相等也算已到期**（有效条件是 `expiresAt > now`，严格大于） |
| `license_machine_mismatch` | `machineId` 既不是 `"*"` 也不等于本机指纹 |
| `license_not_yet_valid` | `issuedAt` 晚于当前时间，超出时钟偏差容限（`spec-limits.md` §6） |
| `license_too_many` | `licenses\` 已有 `kMaxLicenseFiles`（64）个 `*.key`：启动时超出的文件（按名字序）以它拒绝；`license.activate` 对一个**新** id 在写之前答它（`dryRun` 同样），替换已有 id 不算新增 |
| `requires_license` | 该付费插件无许可且注册表没给试用期。**注册表 v1 出不来**（付费条目必须带 1..90 的 `trialDays`），判定对它仍有答案 |
| `trial_expired` | 该付费插件无许可，试用期已用尽（`起点 + trialDays × 86400 <= now`；起点读不出来也算用尽） |
| `requires_pro` | 该功能在 `pro` 清单里而当前无 `pro` 能力。四个产生点：`target.update-config` 的 `sync`/非免费 `dnsMode`（判在任何赋值之前）、`target.add` 的同两项（判在 `missing_target` 之前）、`cardgroup.launch`、`cardgroup.stop`。今天的基线给 PRO，没有安装会收到它；页面的四个调用点对它显示翻译过的提示 |

## 5. 宿主与插件进程（PR ③，已落地）

前三条由**主进程**（`host_bridge`）产生，写进运行态；其余七条由**宿主**产生，经 `plugin.status` 条目的 `error` 字段到达（`IsHostReportedCode` 只放行这七条）。

| 码 | 谁产生 | 含义 |
|---|---|---|
| `host_unavailable` | 主进程 | 宿主缺失或拉不起来；**拉起计划拒绝**（提权的 Ghost 没有可用的 Shell 令牌：没有 Shell、别的用户/会话、AppContainer/受限/低于 Medium/System 令牌、UAC 开启而 Shell 是 Full 提权令牌、Shell 是不拆分的 High 令牌而 Ghost 自己的令牌不是——`policy_host_spawn.h`，原因进日志原句，不另设码）；Shell 尚未就绪且有界重试已用完；运行时探测被同一计划拒绝；宿主重拉预算用尽；一条 `start` 在 `kStartAckTimeoutMs` 内没等到 `running`/`crashed` |
| `runtime_missing` | 主进程 | 清单 `runtime.kind` 要求的解释器在**宿主将要用的** PATH（拉起计划所用令牌的环境块）里找不到；找到的程序答 `--version` 却没有版本号（如商店的别名桩）也算 |
| `runtime_too_old` | 主进程 | 找到了但版本低于 `runtime.minVersion`（按段数值比较） |
| `plugin_entry_missing` | 宿主 | 从数据根按句柄走到 `entry` 时：不存在、是链接/挂载点、是目录、或是硬链接。PR ② 的安装器不查它（只查 `entry` 的名字与扩展名）；开发者工具 `gpkg.py pack` 打包时就查 |
| `plugin_spawn_failed` | 宿主 | `CreateProcessW` 失败；停止事件的名字**已被别人建过**（`ERROR_ALREADY_EXISTS`）；`pluginDir`/`dataDir` 不是约定的布局；`.exe` 入口的映像不是钉住的那个文件；入 Job 失败；`.cmd` 入口的路径或参数含 `%`、`"`、换行；需要解释器却没有绝对路径 |
| `plugin_handshake_timeout` | 宿主 | 超时内没收到 stdout 第一行（含 stdout 关闭而进程还活着） |
| `plugin_handshake_invalid` | 宿主 | 第一行不是合法握手应答，或超过 `kMaxHandshakeLineBytes` 仍无换行 |
| `plugin_declined` | 宿主 | 插件自己回了 `ok:false`。插件给的原因文本**不经协议传递**（状态的 `error` 只收码），日志注明这一点 |
| `plugin_crashed` | 宿主（主进程兜底） | 进程非正常退出，每次重拉之前报一次（带退出码）。主进程也会产生它：报过 `running` 的 pid 已不存在而没有任何报告时（不带退出码） |
| `plugin_restart_exhausted` | 宿主 | 重拉次数用尽后仍然退出 |

超时与重拉次数见 [`spec-limits.md`](spec-limits.md) §1。

## 6. 权限

| 码 | 含义 |
|---|---|
| `permission_denied` | 插件（或宿主）调用了未被授予、或不在白名单表里的命令（HTTP 403）。**两半都已产生**：宿主（PR ③）调 `plugin.status` 之外的任何命令；插件（PR ④）调它已授予权限之外的任何命令（`plugin_permissions.h`）——两者都含 OPTIONS、未知路径、错方法。`/events` 同样答它：宿主，或两个 `events.read.*` 都没有的插件。`plugin.icon` 里插件读**别人**的图标也答它（命令层，同样 403）。带宿主或插件 token 请求静态资源不受影响（静态资源先于凭据判定）。**不记日志** |
| `unknown_permission` | （PR ②，已落地）清单声明了一个不认识的权限名。**这是整包拒绝**，不是「忽略未知字段」：未知权限名无法展示给用户确认，而放过它等于让用户在看不见的条目上点了同意 |

## 7. 商店桥（PR ⑤，已落地）

这几条**不返回给商店页**（那会告诉一个可能是攻击者的页面它哪里被拦了），只记本地（控制台与 `ghostStore.dropped()` 计数）。除下表外，壳还按同样方式、不回话地计数这几种丢弃（它们是本地计数器的名字，不是原因码，不进 `contract_plugin_errors.h`）：`bad_envelope`（`ch`/`v` 不对）、`hidden_tab`（改状态的消息或 `open` 到达时商店页签不在前台）、`no_activation`（它们或 `openLink` 到达时没有用户激活）、`no_store_focus`（有激活，但焦点不在商店 iframe 上——那次点击不是在商店里）、`store_cooldown`（用户刚取消了一个商店发起的确认框，2 秒内）、`link_rate`（一秒内的第二个 `openLink`）、`store_busy`（另一个商店操作还在等用户回答——这一条**会**回 `accepted` 与 `result{user_cancelled}`，见 `spec-store-bridge.md` §4.1；名字刻意不叫 `busy`，那是 §8 的细码）。

| 码 | 含义 |
|---|---|
| `bad_origin` | `event.origin` 不是商店源 |
| `bad_source` | `event.source` 不是那个 iframe 的 `contentWindow` |
| `unknown_type` | 消息 `type` 不认识 |
| `bad_payload` | 字段缺失或类型不对 |
| `user_cancelled` | 用户在确认框点了取消 —— **这一条会**回给商店页，因为它需要据此还原按钮状态 |

## 8. 操作与命令层（PR ②，已落地）

`plugin.*` 命令（`/api/plugins*`，见 `plugin-center.md` §6.3）与插件服务自己的判定。

| 码 | 含义 |
|---|---|
| `plugin_not_installed` | 卸载 / 启用 / 停用一个 `plugins.json` 里没有的 id。启停一条 `state:"broken"` 的记录（卸载删了一半、等下次启动重试）也答它 |
| `busy` | 同一 id 已有一个安装或卸载在排队或在跑。**立刻答，从不排队**；启停遇到它也答它；`plugin.icon`（PR ④）遇到它答 **HTTP 409**——那棵树马上要换或要删，此刻钉住它读会让卸载的删除失败 |
| `not_found` | （PR ④）`plugin.icon`：这个插件没有本程序愿意送出的图标——清单读不出或 id/版本与记录不符、清单没写 `icon`、文件不在或路径上有挂载点/链接、超过 `kMaxIconBytes`、按字节嗅探不是 PNG/WebP。HTTP 404；页面退回默认图标。`plugin_not_installed` 在这条路由上同样是 404 |
| `plugin_unavailable` | 本进程里没有插件服务（`ActivePluginService()` 为空），或服务已 `Stop()`——六条命令都答它，`plugin.list` 也是（快照要服务才有） |
| `bad_id` | 请求的 `id` 不满足 `IsPluginId`（缺失也算） |
| `bad_op_id` | `opId` 不是 1–64 个 `[A-Za-z0-9-]`（它会成为 `.staging\<opId>` 这个目录名，所以服务自己也判一遍） |
| `installed_newer` | 已装版本**高于**这次要装的发布。那等于降级，v1 不支持；在下载任何东西之前就拒 |
| `needs_confirm` | 启用一个升级后权限变多、尚未确认的插件，而请求的 `confirmPermissions` 不**恰好**是（按集合比）此刻待确认的那一组。什么都不改；页面重读快照、重新问用户。见 `plugin-center.md` §8.4 |
| `dev_mode_off` | （PR ⑦）插件开发者模式（`settings.plugins.devMode`）是关的，而这个操作要它开着：`plugin.install-local`（预览与安装都是，包括排队时还开着、轮到时已被关掉的本地安装）、启用一条来源不是 `registry` 的记录（本地或来源未知）。宿主从不报它 |
| `plugin_local_override` | （PR ⑦）注册表路径的安装（`plugin.install`、商店、升级）遇到一条来源不是 `registry` 的 `ok` 记录：本地安装的版本覆盖了这个 id，直到它被卸载。在任何网络请求之前拒，提交时的编辑内部再判一次。商店页收到的是 `verify_failed` |
| `internal_error` | 本程序的 bug：调用方给信任判定的输入与它声明的阶段不符（`trust_chain.h` 的 `TrustStage`），或某个端口抛了异常。**拒绝，不跳过**——「没人算哈希就跳过哈希检查」正是它防的那种错误。记 Error 级 |

开发者工具 `tools/plugin/gpkg.py` 另有四个**只在工具里出现**的码，宿主从不返回：`secret_in_package`（源目录里有 `*.pem` / `*.key` 或含 `PRIVATE KEY` 的文件）、`out_inside_src`（输出目录在源目录里），以及 PR ⑩ 的两个（`pack` 的 `check_tool_rules`，`validate_manifest` 仍是 C++ `ParseManifest` 的逐行镜像、不判它们）：`min_app_version_too_low`（清单声明的某个权限比 `--min-app-version` 更新——`upstream.connect` 要 `1.2.1` 起，表 `PERMISSION_MIN_APP`；更老的 Ghost 会以 `unknown_permission` 整包拒绝，这个码让开发者在打包时就知道，而不是让用户在安装时收到一个费解的码）与 `entry_kind_unsupported`（声明了 `upstream.connect`，而 `entry` 不是 `.exe` 或 `runtime.kind` 不是 `none`：客户端接受这样的清单，但隧道永远答 `tunnel_unsupported`，见 §9）。注册表仓库发布前的两道检查（PR ⑥）答的则是**客户端同名的码**，好让维护者在发布前看到用户会看到的那一个：`gpkg.py check-registry` 失败即 `registry_malformed`（`plugin_docs.h` `ParseRegistry` 的镜像，另加客户端下载上限 `kMaxRegistryBytes`）；`gpkg.py verify-sig` 对名为 `registry.json` 的文件失败即 `registry_bad_sig`，对其余文件即 `release_bad_sig`（验原始字节，`.sig` 同样受 `kMaxSigBytes` 约束）。`--pubkey` 读不出一把公钥（含空文件——注册表模板的 `root-public.b64` 在维护者填入之前刻意是空的）是用法错误 `bad_public_key`，退出码 2。`check-registry --strict`（注册表工作流用它）另有一个只在工具里出现的码 `placeholder_dev_key`：某条的 `devKey` 是 64 个零字节——注册表模板的占位。客户端**接受**这样的注册表（那是一把语法合法、永远验不过任何签名的钥匙），所以它不是 `registry_malformed`，也不在不带 `--strict` 的镜像里。

## 9. 上游隧道（PR ⑩，已落地）

`upstream.list` / `upstream.tunnel`（权限 `upstream.connect`，[`spec-plugin-api.md`](spec-plugin-api.md) §10）。**全部由主进程产生**——命令层或隧道代理（`plugin_tunnel.cpp`）——宿主从不报它们，`IsHostReportedCode` 不列（`test_plugin_host_contract` 钉住：`plugin_not_running` 读起来像宿主的 `plugin_*` 那一族，最容易被误加进去）。形状同 §8：`{"status":"error","error":"<码>"}`，HTTP 200。请求体字段类型不对是共用的 `invalid_json`，先于本节任何码。

| 码 | 含义 |
|---|---|
| `bad_target` | 请求的**值**不合语法（任何 socket 之前）：`via` 不是 `active`/`node`；`via:"node"` 而 `nodeId` 缺失或不满足 `^[A-Za-z0-9_.-]{1,64}$`；`proto` 不是 `tcp`/`udp`；`host` 不是严格 IPv4 / 不带方括号的 IPv6 / 主机名三者之一（`policy_tunnel_target.h`；IPv6 另经真 `inet_pton` 复核）；`port` 不在 1–65535 |
| `upstream_not_found` | `via:"node"` 的 id 没有对应节点——**只按 id 匹配**，一个 `name` 相同的节点不算，也**从不回落到激活节点**；或 `via:"active"` 而没有激活节点（含第一个激活节点没有可用 id） |
| `upstream_invalid` | 节点存在但地址不可用（`ValidateUpstreamAddr`：不是 IPv4 字面量加端口），或 HTTP 节点的用户名含冒号（Basic 无法表达）。fail closed，从不直连、从不换节点 |
| `upstream_udp_unavailable` | `proto:"udp"` 而节点不是 SOCKS5，或它的 UDP 中继此刻不满足闸门（开关、已验证、健康、`udpRelay` 能力位）；隧道代理层面另有两种：在握手期间闸门关上（交出之前的最后一问答否），以及已建中继被节点结束或到节点的 socket 出错时日志里记的码 |
| `upstream_unreachable` | 到节点的 TCP 连接失败——含**连接本身**超过 `kTunnelConnectMs`（5 秒）：那是「连不上」而不是「握手慢」 |
| `upstream_timeout` | 连接成功之后，握手（SOCKS5 / CONNECT，UDP 的 ASSOCIATE）超过了总截止时间 `kTunnelDeadlineMs`（10 秒） |
| `upstream_auth_failed` | 节点拒绝凭据：HTTP 407，SOCKS5 的认证被拒或没有可接受的认证方式 |
| `upstream_refused` | 节点作答了，但拒绝了这个目的地（SOCKS5 的 REP 非 0、CONNECT 的非 200 非 407），或它说的话无法解析、握手中途断开（除超时之外的一切握手失败都归这里） |
| `plugin_not_running` | 调用方插件没有可以接收 socket 的进程：宿主没报它 `running`、没有 pid、没有 token、一次 `start` 还在途（重新启用或换版本时 `states_` 里还是**旧实例**的 pid）；以及隧道代理层面：pid 打不开、进程已退出、**映像不是插件的 `entry`**、报告的 pid 是 Ghost 自己——都在任何网络 I/O 之前判。握手之后紧接着的调用可能答它（`running` 报告是异步的），短暂重试即可 |
| `tunnel_unsupported` | 插件的形状不支持隧道：`entry` 不是 `.exe` 或 `runtime.kind` 不是 `none`；目标进程是 WOW64（或判断不出）；socket 提供者不给 IFS 句柄（没有 `XP1_IFS_HANDLES`，复制出去的东西收养不了） |
| `tunnel_limit` | 超过在途或中继上限（`spec-limits.md` §7.2）：每插件 / 全局的在建隧道数，每插件 / 全局的 UDP 中继数 |
| `tunnel_failed` | Ghost 自己这一侧没建成或没交出去：建不了 socket、改不了阻塞模式、`WSADuplicateSocketW` 失败、建不了回环对。已关闭，什么都没泄漏 |
| `tunnel_unavailable` | 本进程里没有隧道代理（`ActiveTunnelPort()` 为空，或没有宿主桥），或它在这次调用期间被 `Stop()`（应用退出）；UDP 另有：中继线程因连续异常而停摆之后。它是这个功能的 `plugin_unavailable` |

回给商店页时本节一律折成 `verify_failed`（`suite_store_bridge.js` §8 逐个断言）——这些是插件自己的隧道，没有一个出自商店操作，`upstream_unreachable` / `upstream_timeout` 虽然说的是网络，也不是商店要求的那次网络。

---

## 在线上的形状

命令层返回：

```json
{ "status": "error", "error": "package_hash_mismatch", "detail": "expected a3f2…, got 91bc…" }
```

- `error` 是本表里的码，**机器读**。
- `detail` 是可选的自由文本，**只给日志和 issue 用**，不进界面文案，也不保证稳定。PR ② 的 `plugin.*` 命令不带它——细节写在同一操作的日志行里。
- 事件总线上的失败事件同样带 `error` 字段（`tag="Plugin"`，Control 平面）。
- **传输层的四个拒绝不是本表的码**（PR ⑦，`src/modules/api/wire/wire_events.h`，不进 `contract_plugin_errors.h`）：请求头就决定、在读任何请求体之前答，形状同上——`payload_too_large`（413，`Content-Length` 超过这个请求的上限）、`length_required`（411，`Transfer-Encoding: chunked`）、`bad_request`（400，`Content-Length` 重复/不是数字/过大，或 `install-local` 的 `Content-Type` 不是 `application/octet-stream`）、`busy`（**503**，同一时刻已有一个大请求体在读——与 §8 的 `busy` 同名，但那个是 200 里的命令答复）。浏览器在上传途中收到 413 看到的是连接被重置而不是 413，所以插件页自己先按文件大小拒绝，并显示 `plugins.err.payload_too_large`。

⚠️ **`plugin.*` 的错误一律是 `{"status":"error","error":"<码>"}`，包括请求体不可用时的 `invalid_json`。** 这与更早的 `update.*` 命令刻意不同——后者对坏请求体答 `{"status":"invalid_json"}`（`invalid_json` 放在 `status` 里）。`invalid_json` 是命令层的共用码、早于插件中心，所以不在 `contract_plugin_errors.h` 里。页面按 `status === "error"` 取 `error`，不要照 `update.*` 的形状去猜。成功的答案是 `{"status":"accepted"}`（安装 / 卸载 / 刷新：结果随后作为事件到达）或 `{"status":"ok"}`（启用 / 停用：同步完成）。

## 给商店页的是粗粒度分类，不是这些码

本表的码只走**本地**：命令层返回、日志、本地界面。回给远程商店页的只有三个分类之一：

| 分类 | 覆盖 |
|---|---|
| `network_failed` | §3 的全部，**以及** §1 里的三个 `*_unreachable`（`registry_unreachable` / `release_unreachable` / `package_unreachable`）——它们就是「拉不到」，该让用户查网络 |
| `user_cancelled` | 用户在确认框点了取消；或另一个商店操作还在等用户回答（`spec-store-bridge.md` §4.1） |
| `verify_failed` | 其余一切：§1 与 §2 的其余码、§5、§8（含 `busy`、`installed_newer`、升级锁）、§9 以及不认识的码 |

折叠在 `store-bridge.js` 的 `coarseError`，`suite_store_bridge.js` 对本表每一个码逐个断言。

理由与 §7 那条一样，只是适用面更广：`package_hash_mismatch` / `release_bad_sig` / `registry_rollback` 比 `bad_origin` 精确得多，把它们回给一个明确标注「不信任」的页面，等于告诉它信任链断在第几环。商店页需要的只是「失败了，把按钮还原」，以及区分「该让用户检查网络」还是「该报告问题」。
