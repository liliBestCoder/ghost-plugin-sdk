# Ghost Proxifier 插件开发规范

> 状态：**规范已定稿，运行时已实现（许可 ① 除外）。** 落地进度见 [`../architecture/plugin-center.md`](../architecture/plugin-center.md) §14 的分期 PR 表。
> 信任链与安装（②）、宿主（③）、插件 API 权限（④）、插件页与商店桥（⑤）已落地；PR ⑥ 给了 `examples/` 下的四件套与 `gpkg.py` 的两条维护者子命令。

---

## 一分钟版本

一个 Ghost 插件就是一个**普通的可执行程序**，它：

1. 从 **stdin 读一行 JSON**（握手），
2. 往 **stdout 写一行 JSON**（回执，可带一个 `uiUrl`），
3. 然后随便干什么 —— 比如起一个本地网页当自己的界面，或者拿握手里给的 token 去读 Ghost 的事件流。

它跑在**自己的进程**里，由 `ghost_plugin_host.exe` 拉起和监督。**第三方代码永远不进 Ghost 的主进程，也不进注入 DLL。**

它也可以**不依赖 Ghost 单独跑**：没有 `GHOST_PLUGIN_ID` 环境变量就是独立模式，跳过握手、没有 token、自己开浏览器。细节见 [`spec-host-protocol.md`](spec-host-protocol.md) §3.0。

```python
import json, os, sys
if "GHOST_PLUGIN_ID" in os.environ:                       # 有宿主
    hs = json.loads(sys.stdin.readline())                 # 握手进来
    print(json.dumps({"v": 1, "ok": True}), flush=True)   # 回执出去
# …剩下是你自己的事；没有宿主就是独立模式，直接干活
```

⚠️ `flush=True` 不能省。Python 的 stdout 在管道上是块缓冲的，不刷新就会撞上握手超时，而症状是「插件启动失败」而不是「忘了刷新」。

---

## 规范目录

| 文档 | 读它如果你要… |
|---|---|
| [`spec-manifest.md`](spec-manifest.md) | 写 `manifest.json`：id、版本、入口、运行时、权限声明 |
| [`spec-host-protocol.md`](spec-host-protocol.md) | 实现握手（**大多数插件作者只需要读这一份的 §3**） |
| [`spec-plugin-api.md`](spec-plugin-api.md) | 调 Ghost 的接口：token、七个权限、各自能读什么或能做什么（`upstream.connect`：经用户的上游节点建连接，§10） |
| [`spec-release.md`](spec-release.md) | 发版：签名、GitHub Release 资产、上架与换钥 |
| [`spec-license.md`](spec-license.md) | 做付费插件：许可块格式、试用、能力集 |
| [`spec-store-bridge.md`](spec-store-bridge.md) | 开发**商店页**本身（官方内部用，插件作者用不到） |
| [`spec-errors.md`](spec-errors.md) | 查一个原因码是什么意思 —— **全部原因码的唯一定义处** |
| [`spec-limits.md`](spec-limits.md) | 查一个上限、超时或正则 —— **全部数值与语法的唯一定义处** |

后两份是「唯一定义处」：一个原因码、一个上限、一个正则只在那里写一遍，其余文档引用它。两处各写一份的值会漂移，而漂移那天不会有任何东西报错。

---

## 从零到上架

SDK 在本仓库的 [`examples/`](../../examples/) 下给了四件套（外加**官方参考插件**，见表后），**每个子目录是将来一个独立仓库的全部内容**，都有测试守着（见文末）：

| 目录 | 将来的仓库 | 是什么 |
|---|---|---|
| `examples/plugin-template/` | `liliBestCoder/ghost-plugin-template`（GitHub 模板仓库） | 最小插件：握手、等停止事件、独立模式；外加发版工作流 `.github/workflows/release.yml` |
| `examples/plugin-events-viewer/` | `liliBestCoder/ghost-plugin-events-viewer` | 完整示例：自己的界面、用插件 token 读 `/events`、一行 `log.write`、响应停止事件、独立运行。Python 标准库，自带测试 `test_plugin.py` |
| `examples/registry-template/` | `liliBestCoder/ghost-plugin-registry` | 官方注册表仓库的布局与发布工作流 `publish.yml`（**只验签、从不签名**）——维护者用 |
| `examples/store-static/` | 部署到 `https://store.ghostproxifier.com/v1/` | 说 `ghost-store/1` 的最小静态商店页——维护者用 |

**参考实现是 [`examples/plugin-showcase/`](https://github.com/liliBestCoder/ghost-plugin-showcase)**（`com.ghostproxifier.showcase`，「插件能力示例」，将来的 `liliBestCoder/ghost-plugin-showcase`）：v1 插件能做的每一件事（`upstream.connect` 除外：它只给 `.exe` 插件，而参考插件是 Python），它都在自己页面的一个区块里做一遍，并标出规范章节与代码位置——最初的六个权限（一条 `/events` 按平面拆成 Control/System 与 Data、`stats.read` 三条命令、脱敏配置、`log.write` 被强制改写的字段、自己的图标）、闸门的 401/403 与 429 退避、握手逐字段、清单 `args`、`GHOST_PLUGIN_*`、runtime、许可声明、停止事件、私有数据目录、故意退出看宿主重拉、嵌入与独立运行。**写插件从它抄起**；它的 README 有「能力 → 区块 → 规范 → 代码」对照表与「拿它当起点」一节。

### 本地开发：开发者模式（PR ⑦）

改一行代码就要签名、发版、等注册表，那不是开发循环。Ghost 的设置页有一个默认关闭的开关「插件开发者模式」：

1. `python <sdk>/tools/plugin/gpkg.py pack --src my-plugin --out dist` 打一个包（不用签名）。
2. 设置 → 插件 → 打开「插件开发者模式」。警告框说的是实话：从本地安装的包没有经过任何人审核，签名也不会被校验，它以你的权限运行、能做你授予的每一项权限允许的事。
3. 插件页 →「从本地安装」→ 选 `.gpkg`。先出预览框（id、版本、作者、声明的权限、SHA-256 前 16 位、「未签名」），确认后才装；装好是**停用**的，启用时照常逐条确认权限。
4. 改了代码，**同版本**重打一个包再装：Ghost 先停掉正在跑的旧版本，按句柄把旧目录挪开、装新字节，失败就挪回去、并把旧版本重新启动起来；已授予的权限不变（权限变多时仍要重新确认），`<id>\.data` 保留。降级也允许。
5. 撞上官方目录里的 id 叫「本地覆盖」：预览框会说此后官方安装与升级被拒、**本地包能读官方版本留下的 `.data`**，官方标为付费的 id 照样要许可或试用；卸载本地版本后恢复正常。
6. 关掉开关，本地插件被停用、停止，文件与数据保留；再打开后逐个启用。

本地包的**结构性检查与正式安装完全相同**（zip 上限、条目名、清单、解包），只跳过注册表、发布描述与签名——所以**不检查 `minAppVersion`**（它在发布描述里）。浏览器端先按文件大小拒绝超过 64 MB 的包。

**现场演示**整条链路（维护者与评审用，走的是完整的签名信任链而不是开发者模式）：`pwsh -File examples\demo\run-showcase-demo.ps1` 启动 `bin\ghost_plugin_demo.exe`（x64 测试构建的一部分，不发布）：一次性钥匙签名、假网络、真 `PluginService` 安装、真宿主、真闸门（临时端口、内存里的演示数据、临时数据目录，不碰装好的 Ghost），打印插件页地址，浏览器打开即可看；插件被宿主重拉后打印新地址。

逐条命令的走查（从建仓库到用户装上）在 [`publish-walkthrough.md`](publish-walkthrough.md)；维护者一侧（根钥、注册表仓库、镜像、商店页托管）另有一份维护者清单 `maintainer.md`（私有，不在本仓库）。下面是提纲。

### 1. 建仓库

每个插件一个**独立的 GitHub 仓库**。从模板仓库 `ghost-plugin-template` 点「Use this template」（它的内容就是 `examples/plugin-template/`），或者照 `examples/plugin-events-viewer/` 抄。

```
my-plugin/
  manifest.json                    ← 必须在包根
  main.py                          ← entry（也可以是 .exe / .cmd / .js）
  icon.png                         ← 可选，≤ 256 KB，PNG 或 WebP
  dev-public.b64                   ← 你的开发者公钥（提交它）
  .github/workflows/release.yml    ← 模板给的发版工作流
```

`pack` 打的是目录里的**全部文件**（`.git` 与根上的 `.github/` 除外——后者是 CI 配置，不是插件）；不想进包的东西别放在仓库里。`dev-public.b64` 会随包发出去：它是公钥，放进包里无害。私钥永远不在里面：`*.pem` / `*.key` 或含 `PRIVATE KEY` 的文件会让 `pack` 以 `secret_in_package` 拒绝。

### 2. 生成密钥对（一次性）

```bash
python tools/plugin/gpkg.py keygen --out ~/ghost-keys/     # 仓库之外
cp ~/ghost-keys/dev-public.b64 .
```

- `dev-private.pem` —— **自己保管，永不提交**。把它的全部内容存进仓库 secret `GHOST_DEV_KEY_PEM`；发版工作流只在一个步骤里、在 `$RUNNER_TEMP` 下用它，用完即删。
- `dev-public.b64` —— 提交到仓库根；上架时交给官方。

### 3. 申请上架（一次性）

向官方仓库 [`ghost-plugin-registry`](https://github.com/liliBestCoder/ghost-plugin-registry) 提 issue，附：

- 插件 `id`（反向域名，至少三段）——**上架后不能改**
- 仓库 `owner/name`
- `dev-public.b64` 的内容
- 免费还是付费

官方审过后把你的条目（含公钥）加进注册表，**离线**用官方根钥签名，由注册表仓库的 `publish.yml` 验签后发布。

**此后每次发版不再需要官方介入** —— 这正是三层信任链存在的理由：官方只审一次「这个人是谁」，你自己决定什么时候发版。

### 4. 发版

```bash
# manifest.json 里 "version": "1.0.0"
git tag v1.0.0 && git push origin v1.0.0
```

模板的 `release.yml` 接手：tag 守门（必须是 `v<manifest 的 version>`）→ `gpkg.py pack` → 用 secret `sign` → 用提交的 `dev-public.b64` `verify` → `gh release create` 上传三个资产。它按 `SDK_REPO` + `SDK_REF`（公开 SDK 仓库的一次提交，40 位 sha，不是分支）取 `tools/plugin`：替你签发布的工具不该在你不知情时变化。Actions 全部按提交 sha 钉住，`cryptography` 从带哈希的 `.github/requirements-release.txt` 以 `--require-hashes` 安装——这个工作流握着你的签名私钥。

两份工作流从一个**公开的** SDK 仓库（`SDK_REPO`）按一次**固定提交**（`SDK_REF`，40 位 sha）取 `tools/plugin/`——插件与注册表仓库的 `GITHUB_TOKEN` 读不了私有仓库。模板里两者都是占位符，第一步见到占位符就以 `::error::` 失败；公开 SDK 仓库就是本仓库 [`liliBestCoder/ghost-plugin-sdk`](https://github.com/liliBestCoder/ghost-plugin-sdk)，`SDK_REF` 钉它的一次提交（40 位 sha，不是分支）。

手动发也行：

```bash
python tools/plugin/gpkg.py pack   --src . --out ../dist/       # --out 必须在 --src 之外
python tools/plugin/gpkg.py sign   --key ~/ghost-keys/dev-private.pem --dist ../dist/
python tools/plugin/gpkg.py verify --dist ../dist/ --pubkey dev-public.b64
gh release create v1.0.0 ../dist/ghost-plugin.json ../dist/ghost-plugin.json.sig ../dist/*.gpkg
```

#### Release 资产名与 tag 形状

| 项 | 必须是 | 为什么 |
|---|---|---|
| tag | `v<version>`，`<version>` 与 `manifest.json` 逐字相同（`v1.0.0`，不是 `1.0.0`、`v1.0`） | Ghost 只从 `releases/download/v<version>/` 取包，别的 tag 取不到 |
| 发布描述 | `ghost-plugin.json` | Ghost 取 `releases/latest/download/ghost-plugin.json` |
| 签名 | `ghost-plugin.json.sig` | 同一个 Release 里、同名加 `.sig` |
| 包 | `<id>-<version>.gpkg`（`pack` 起的名字，别改） | 发布描述的 `package.name` 就是它 |
| latest | 新版本的 Release 必须是 latest（不要标 pre-release） | 升级检查读的是 `releases/latest` |

`pack` 打出的 `.gpkg` 是**不压缩**的 zip（store 方法、规范布局、无注释）：v1 宿主只认这个形状，别用别的工具重新打包。整包上限 64 MB，见 [`spec-limits.md`](spec-limits.md) §2。`pack` 与 `check-registry` 只需要 Python 标准库；`keygen` / `sign` / `verify` / `verify-sig` 需要 `pip install cryptography`。

#### 不装 Python 也能签：`tools/plugin/sign.ps1`

只需要 PowerShell 7（纯 .NET，不要 `cryptography`），一次签**一个**文件：

```powershell
pwsh -NoProfile -File tools/plugin/sign.ps1 -Key ~/ghost-keys/dev-private.pem -File ../dist/ghost-plugin.json   # → ../dist/ghost-plugin.json.sig
pwsh -NoProfile -File tools/plugin/sign.ps1 -Key ~/ghost-keys/dev-private.pem -PublicKey                         # 打印 X‖Y 公钥的 base64（88 字符）
```

- 签的是文件的**原始字节**（不去 BOM、不转行尾、不重排 JSON），ECDSA P-256 + SHA-256；`.sig` 内容是 64 字节 `r‖s` 的 base64 加**一个** `\n`，与 `gpkg.py sign` 的产物同形（[`spec-release.md`](spec-release.md) §1）。
- 写盘前先用只由导出的 `X‖Y` 重建的公钥验一遍自己的签名；已有 `.sig` 不覆盖，除非加 `-Force`；`-Out` 指定别的输出路径。
- 私钥只接受未加密的 PEM（`gpkg.py keygen` 写的 PKCS#8 `PRIVATE KEY`，或 SEC1 `EC PRIVATE KEY`），任何输出与报错都不回显私钥内容。
- 它只签名，**不打包、不校验清单**——`.gpkg` 与 `ghost-plugin.json` 仍由 `gpkg.py pack` 生成；维护者用它签 `registry.json` 也是同一条命令。由 CTest `ghost_plugin_sign_tool_test` 用一次性密钥驱动、以客户端自己的验签实现判定。

#### 注册表维护者的两道检查

```bash
python tools/plugin/gpkg.py check-registry --file registry.json                                              # 客户端 ParseRegistry 的镜像，只用标准库
python tools/plugin/gpkg.py verify-sig --pubkey root-public.b64 --file registry.json --sig registry.json.sig  # 原始字节验签，只要公钥
```

失败时答的是**客户端同名的码**（`registry_malformed` / `registry_bad_sig`），见 [`spec-errors.md`](spec-errors.md) §8。注册表仓库的 `publish.yml` 就按这个顺序跑它们（`check-registry` 带 `--strict`：另拒模板里全零的 `devKey` 占位——那不是客户端规则，所以只在工具里），外加「新 `seq` 必须严格大于已发布的最大 `seq-<N>`」。

### 5. 用户怎么拿到升级

Ghost 在用户打开插件页时（每插件每 24 小时至多一次）拉一次
`https://github.com/<你的仓库>/releases/latest/download/ghost-plugin.json`，
看到更高版本就提示。**从不自动安装** —— 升级要用户点，而且如果你的新版本要更多权限，用户会被重新问一遍。

---

## 你能拿到什么、拿不到什么

### 能

- 事件流（`events.read.control` / `events.read.data`）、统计（`stats.read`）、脱敏后的配置（`config.read`）、写应用日志（`log.write`）、读自己的图标（`plugin.assets`）
- **经用户的上游节点连接任意地址**（`upstream.connect`，Ghost 1.2.1 起）：Ghost 连节点、握手，把连好的 socket 复制进你的进程，你用 `WSASocketW(FROM_PROTOCOL_INFO…)` 收养；UDP 由 Ghost 中继，你拿一个回环 socket。插件里不写任何代理协议代码、拿不到节点凭据。**只给原生 x64 `.exe`、`runtime.kind` 为 `none` 的插件**；失败一律 fail closed，不许回落直连。它是唯一一个以用户的代理身份在网络上行事的权限，确认框里另有一行警告（[`spec-plugin-api.md`](spec-plugin-api.md) §10）
- 一个私有数据目录（`dataDir`），升级不清空
- 一个嵌进 Ghost 界面的 iframe（把 `uiUrl` 回给宿主就行）
- 同一个 `uiUrl` 也能在 **Ghost 托管的独立窗口**里显示：用户在插件页点「在新窗口打开」即可，插件不用做任何事（PR ⑨）

### 不能

- Ghost 界面的会话 token，或宿主的 token
- 未脱敏的上游代理凭据（`upstream.connect` 也不给——但它们在 `config.json` 里是明文，同用户进程本来就读得到，所以这**不是**安全边界）
- `targets.json`（被管目标的路径、别名、环境变量）
- 修改任何配置，包括你自己的 `settings`（v1 恒为 `{}`）
- 启动、停止、注入任何目标进程
- 网络数据面的 payload（抓包需要注入侧配合，是子项目 D，不是一个 API 权限能给的）

> `events.read.data` 是独立权限，因为 Data 平面等同于用户的完整访问历史（每一次 DNS 查询与连接目的地）。申请它要有真实理由，用户在安装确认框里会被明确告知这一点。

细节与理由见 [`spec-plugin-api.md`](spec-plugin-api.md)。

### 插件界面的三种形态

| 形态 | 谁打开 | 官方？ |
|---|---|---|
| **嵌入 iframe** | 插件页的插件 UI 面板（默认） | ✅ |
| **Ghost 托管的独立窗口** | 用户在面板头部或插件行上点「在新窗口打开」；Ghost 自己开一个顶层窗口，里面的 WebView2 显示同一个 `uiUrl` | ✅ |
| **插件自己画的原生窗口** | 插件自己的进程（`ui.embedded:false` 时 Ghost 只显示运行状态） | ✅ 正当——插件是用户权限下的进程，本来就能画自己的窗口 |

两种官方形态加载的是**同一个 `uiUrl`**，插件不必区分。独立窗口与 iframe 一样隔离：没有 token、没有 `chrome.webview`、导航出不了你自己的端口，权限请求、下载、Basic 认证框、`alert`/`confirm`/`prompt`、屏幕捕获都会被拒绝或吞掉（设计 `plugin-center.md` §8.6）——别指望在里面弹框。

⚠️ **插件界面打不开外部链接**，两种形态都一样：`<a target="_blank">`、`window.open`、把顶层导航到别处，全都被拒绝，Ghost 也**不会**替你把它交给系统浏览器。要让用户在浏览器里看什么（文档、你的主页、授权页），由**插件自己的进程**去开（例如 UI 发一个请求给你的本地服务，服务里 `os.startfile(url)` / `ShellExecuteW`）——那是你自己的进程、你自己的决定。

⚠️ **在系统浏览器里打开 `uiUrl` 不是官方通道。** `uiUrl` 是一个不认证的回环地址，本机任何进程都能打开它；浏览器 tab 里没有 Ghost 的任何隔离（也没有 token——插件页本来就拿不到——但也不受上面那张表约束）。宿主模式下**不要** `os.startfile(url)`；那是独立模式的做法（[`spec-host-protocol.md`](spec-host-protocol.md) §3.0）。

### 存储：不要依赖 cookie

cookie 按**主机**分桶，不按端口：`127.0.0.1` 上的全部插件与 Ghost 自己**共用一个 cookie jar**。你设的 cookie 别的插件看得到、会随请求发给它们（与 Ghost 的控制接口——它读头时把 `Cookie:` 整行丢掉），你也看得到别人的；一大堆 cookie 还会拖大每个插件页面收到的每个请求。所以：

- 状态放 `localStorage` / `IndexedDB`（按端口分桶，面板与独立窗口看到同一份）或你自己的进程里；
- **不要注册 service worker**：端口被别的插件复用时，它会活在下一个插件身上。

---

## 几条会咬人的

1. **`flush` 你的 stdout。** 见上。
2. **stdout 第一行之后的内容会被直接丢弃**，不转日志。要记日志申请 `log.write` 权限走 API。
3. **端口用 `0` 让系统分配**，不要硬编码。用户机器上什么都可能被占。
4. **监听停止事件**，名字在握手 JSON 的 `stopEvent` 字段里（也在环境变量 `GHOST_PLUGIN_STOP_EVENT`）。**别自己按 pid 拼** —— 宿主启动的可能是 `cmd.exe` 或 `python.exe`，它的 pid 和你的不是一个，拼出来的名字永远等不到信号，于是每次停用都是超时后被 `TerminateJobObject` 强杀。
5. **宿主死了你也会死**（Job Object `KILL_ON_JOB_CLOSE`）。这是刻意的：否则强杀宿主会留下一串还占着端口的孤儿进程。
6. **你的 UI iframe 和 Ghost 不同源**，它拿不到 token 也调不了 Ghost 的接口。数据路径是「UI → 你的进程 → Ghost」。
7. **`v` 不认识就拒绝**，不要尽力解析。
8. **签名覆盖文件原始字节**，下载后别做任何处理（去 BOM、转行尾、trim）再验。
9. **先看 `GHOST_PLUGIN_ID` 在不在环境里，再决定读不读 stdin。** 独立运行时 stdin 是终端，`readline()` 会永远阻塞。
10. **别假设单实例。** 用户可能在 Ghost 里启用了你、又手动开了一个。端口用 `0`，需要单实例自己拿命名互斥量。
11. **`upstream.tunnel` 至少等 30 秒、答复丢了不重试。** 答复里的 socket 已经复制进你的进程；提前放弃就是一个泄漏到进程退出的句柄（[`spec-plugin-api.md`](spec-plugin-api.md) §10.5）。握手后第一次调用答 `plugin_not_running` 是暂时的，短暂重试即可。

---

## 这些示例由什么测试守着

| 测试 | 守什么 |
|---|---|
| `ghost_example_plugin_test`（`examples/plugin-events-viewer/test_plugin.py`） | 示例插件自己：测试同时扮演宿主与 Ghost 控制接口——握手回执、白名单文件服务、`log.write` 恰好一次、`/events` 首连与断线续读、停止事件、403 时存活、独立模式 |
| `ghost_examples_pack_test`（`examples/tests/test_examples.py`） | 示例与模板真的能 `pack`、过 `verify_package`；两份工作流的文本不变量（`SDK_REPO`/`SDK_REF` 占位符与先行守卫、设了真 sha 时 git 核对那次提交的 `gpkg.py` 有工作流用到的每条子命令、actions 按 sha 钉住、`--require-hashes`、secret 只在一个步骤、tag 守门；注册表工作流不碰 secret、不签名、用 `check-registry --strict`）；注册表模板过 `check-registry`；商店页的文本不变量；`examples/` 下没有任何私钥 |
| `ghost_plugin_examples_test`（C++） | 示例文档过**客户端自己的**解析器（`ParseManifest` / `ParseRegistry`），三处 id/仓库一致，商店目录与清单、注册表一致 |
| `ghost_plugin_example_e2e_test`（C++，x64） | 端到端：`gpkg.py pack` → 一次性钥匙经 `sign.ps1` 签 → 真 `PluginService` 刷新并安装 → 带两个权限启用 → 真宿主跑真 `main.py` → 经真闸门 `log.write` 与 `/events` → 停用（停止事件、`stopped.ok`、旧 token 401）→ 卸载 |
| `ghost_example_showcase_test`（`examples/plugin-showcase/tests/test_plugin.py`） | 参考插件自己，两层：单元层导入各模块（握手的每一种拒绝、安全 JSON 解析、SSE 上限、平面拆分与 Data 每秒上限、数据目录、429 指数退避、各区块的转交规则）；进程层扮演宿主与控制接口（它声明的六个权限的每条路由按真闸门对插件的答法应答）——每个请求带插件 token、不带 `Origin` 与会话头，token 不出现在任何页面响应、输出与数据目录，前缀/Host/Origin 检查，停止事件，故意退出的代码。`ghost_examples_pack_test`（按 `release.yml` 打包的手写条目集、`release.yml` = 模板加恰好一步）与 `ghost_plugin_examples_test`（最初的六个权限、客户端解析器、七个模块的源码规则）也覆盖它；`ghost_plugin_demo.exe` 与 e2e 共用 `src/tests/support/plugin_e2e_rig.h`，它本身不是测试 |
| `ghost_fe_store_page_test` / `ghost_fe_store_contract_test` | 商店页单独、以及商店页与真实壳互喂消息（本机跑，CI 不跑前端） |

---

## 相关文档

- [`publish-walkthrough.md`](publish-walkthrough.md) —— 开发者从零到上架的逐条命令
- `maintainer.md` —— 维护者清单：根钥、注册表仓库、镜像、商店页、真机走查（私有，不在本仓库）
- [`../architecture/plugin-center.md`](../architecture/plugin-center.md) —— 架构设计：威胁模型、信任链、进程模型、分期计划
- [`../../.agents/AGENTS.md`](../../.agents/AGENTS.md) §9 / §9.1 —— 隐私与「不做本地防篡改」的产品裁定
- [issue #12](https://github.com/liliBestCoder/ghost-proxifier-ui/issues/12) —— 插件中心的路线图
