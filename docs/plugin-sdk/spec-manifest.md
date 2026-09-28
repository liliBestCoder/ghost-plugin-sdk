# 插件清单 `manifest.json`（spec-manifest v1）

包（`.gpkg`）根目录下必须有这个文件。它描述插件**是什么**、**怎么启动**、**要什么权限**。

拒绝原因码见 [`spec-errors.md`](spec-errors.md)。
> 本文出现的数值上限、超时与正则以 [`spec-limits.md`](spec-limits.md) 为准；这里写出的数字是它的投影，改动必须两处同步。

---

## 1. 完整示例

```json
{
  "v": 1,
  "id": "com.example.events-viewer",
  "version": "1.0.0",
  "name":        { "zh": "事件查看器", "en": "Events Viewer" },
  "description": { "zh": "实时显示 Ghost 的事件流。", "en": "Live view of Ghost's event stream." },
  "author":      { "name": "Example Corp", "url": "https://github.com/example" },
  "homepage": "https://github.com/example/events-viewer",
  "category": "dev",
  "icon": "icon.png",
  "entry": "run.cmd",
  "args": [],
  "runtime": { "kind": "python", "minVersion": "3.9" },
  "ui": { "embedded": true },
  "standalone": true,
  "permissions": ["events.read.control", "stats.read"]
}
```

## 2. 字段表

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `v` | int | ✅ | 必须是**整数** `1`（`true`、`1.0` 不算）。不认识的值 → `manifest_malformed`（**不是**「尽力解析」） |
| `id` | string | ✅ | 反向域名，至少 3 段：`^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?){2,}$`，≤ 64 字节，**第一段不得是 Windows 设备名**（`con`/`prn`/`aux`/`nul`/`com0-9`/`lpt0-9`）。**必须与发布描述、注册表条目逐字相等** |
| `version` | string | ✅ | 三段数字，每段 ≤ 65535，**规范形式**：除单独一个 `0` 外不许前导零（`1.0.0` 可以，`01.0.0` 拒绝）。**必须与发布描述逐字相等** |
| `name` | object | ✅ | `{zh, en}`，至少有 `en`；每个值 1–64 个码点；其他语言键忽略 |
| `description` | object | ✅ | `{zh, en}`，至少有 `en`；每个值 ≤ 240 个码点 |
| `author` | object | ✅ | `{name: string ≤64 个码点, url?: https URL}` |
| `homepage` | string | — | https URL（以 `https://` 开头、后面至少一个字符、不含 ≤ 0x20 的字节与 0x7F。宿主只显示与在浏览器里打开它，从不去取） |
| `category` | string | ✅ | `network` \| `dev` \| `security` \| `productivity` \| `other`；未知值按 `other` |
| `icon` | string | — | 包内相对**文件**路径（同 zip 条目名规则），扩展名 `.png` / `.webp`（不分大小写）。文件大小与边长上限见 `spec-limits.md` §2；**文件缺失或超限不是拒绝理由**，界面换默认图标。缺失用默认图标 |
| `entry` | string | ✅ | 包内相对路径，见 §3 |
| `args` | string[] | — | 传给 `entry` 的参数，≤ 16 条、每条 ≤ 256 字节。默认 `[]` |
| `runtime` | object | — | 见 §4。默认 `{"kind":"none"}`；`minVersion` 为一到三段数字（`3.9`、`18`、`3.11.2`） |
| `ui` | object | — | `{embedded: bool}`。默认 `{"embedded": false}`。见 §5 |
| `standalone` | bool | — | 该插件**不依赖 Ghost 也能运行**（[`spec-host-protocol.md`](spec-host-protocol.md) §3.0）。默认 `false`。**只影响展示**（商店页与插件页的「可独立运行」徽标），不改变 Ghost 怎么启动它，也不改变宿主模式下的任何行为 |
| `permissions` | string[] | — | 见 [`spec-plugin-api.md`](spec-plugin-api.md) §2。默认 `[]` |

**未列出的字段一律忽略**（前向兼容），但**类型不对的已知字段是整包拒绝**（`null` 也算类型不对），不是「取默认值」——一个把 `permissions` 写成字符串的清单，更可能是写错了而不是想要空数组。

文件本身：UTF-8 JSON，顶层是对象，**开头不许有 UTF-8 BOM**（一些编辑器会悄悄加上；`gpkg.py pack` 同样拒它），不许注释、`NaN`、`Infinity`、落单的代理项转义，≤ 64 KB（`kMaxManifestBytes`）。「字符」一律指 Unicode 码点，与 Python 的 `len()` 一致。

⚠️ **未知的权限名是整包拒绝**（`unknown_permission`），这是「忽略未知字段」的一个刻意例外。权限要逐条展示给用户确认，一个宿主不认识的权限名无法展示，而放过它等于让用户在一个看不见的条目上点了同意。代价是：将来 v2 新增权限名时，老宿主会拒装用了它的插件——这正是 `minAppVersion` 存在的意义，声明新权限的插件要同时抬高它。

**注意这里没有 `paid` / `trialDays`。** 收费与否是官方注册表的字段，不是开发者自述的 —— 见 [`spec-release.md`](spec-release.md) §2.5。

## 3. `entry`

- 相对包根，与 zip 条目名**同一条规则**（`src/shared/policy_zip_name.h` 的 `IsSafeZipEntryName`：只许正斜杠，不许 `..`、绝对路径、驱动器前缀、`:`、段尾的 `.` 或空格、设备名、8.3 别名……），且不能以 `/` 结尾（是文件不是目录）。
- 必须在包里真实存在，否则 `plugin_entry_missing`（`gpkg.py pack` 在打包时查；宿主在启用时查——PR ③）。
- 允许的扩展名：`.exe`、`.cmd`、`.bat`、`.py`、`.js`、`.mjs`。
- 启动方式：
  - `.exe` → 直接 `CreateProcessW`
  - `.cmd` / `.bat` → `cmd.exe /c <entry>`
  - `.py` → `<python> <entry>`（解释器按 `runtime` 解析）
  - `.js` / `.mjs` → `<node> <entry>`
- 工作目录 = 包目录（`plugins\<id>\<version>\`）。
- 全部以 `CREATE_NO_WINDOW` 启动；插件**不能**指望有控制台。

## 4. `runtime`

```json
{ "kind": "none" | "python" | "node", "minVersion": "3.9" }
```

- `none`：自带全部依赖（`.exe` 或包内附解释器）。**推荐**。
- `python` / `node`：需要系统上已有解释器。

**它有两个作用，不要只记住一个：**

1. **前置条件检查**（始终生效）：启用前在 `PATH` 里找 `python.exe` / `node.exe` 并比对 `minVersion`。找不到 → `runtime_missing`；版本太低 → `runtime_too_old`。两者都是拒绝启用并提示用户自行安装，不是静默失败。
2. **决定用什么解释器启动**（仅当 `entry` 是 `.py` / `.js` / `.mjs`）。

所以 `entry: "run.cmd"` 配 `runtime.kind: "python"` 是**合法且常见**的：`.cmd` 由 `cmd.exe` 启动，而 `runtime` 在这里只做第 1 件事——声明「没有 Python 我跑不起来」。解析出来的解释器路径仍会经 `start` 消息（`runtime.interpreter`）与环境变量 `GHOST_PLUGIN_PYTHON` / `GHOST_PLUGIN_NODE` 交给进程，`.cmd` 里直接用它，不必自己再找一遍——**自己找一遍就可能找到与前置检查不同的那一个**。

**v1 不代装运行时。** 下载并安装一个 Python 发行版是一件需要用户明确同意、且失败形态很多的事，不该藏在「启用插件」后面。

## 5. `ui.embedded`

- `true`：插件在自己的进程里起一个 HTTP 服务，握手时把 `uiUrl` 回给宿主，插件页用 iframe 嵌入。
- `false`：无界面（后台插件）。插件页只显示运行状态。

`uiUrl` 必须是 `http://127.0.0.1:<port>/...`，见 [`spec-host-protocol.md`](spec-host-protocol.md) §3。端口不能是 23551（Ghost 自己的控制接口），也不能是 **80**（浏览器导航时会去掉默认端口，插件 frame 的端口绑定就永远对不上）。插件页把它放进名为 `ghost-plugin-<port>` 的 iframe；`uiUrl` 变了会换一个新 iframe（PR ⑤）。

⚠️ 该 iframe 与本地控制接口**不同源**：拿不到 `window.GHOST_TOKEN`，`fetch` 到控制接口会被源校验拒绝。插件 UI 要数据，走自己进程的 API，由插件进程用它自己的插件 token 去取。

## 6. 校验在哪一侧

解析与校验是 `src/modules/plugin/domain/plugin_docs.h` 的 `ParseManifest`（**纯逻辑**，零系统头、可单测；同一个头也解析注册表与发布描述）。它在**包的哈希验过之后、解包之前**跑：清单直接从内存里那份已验过的包字节中按索引取出（CRC 也核对），任一条不合法就删 staging、现有版本不动，一个字节都不写到插件目录。

判定顺序：所有 `manifest_malformed` 的检查在前，未知权限名（`unknown_permission`）在最后——一个既坏了又要了未知权限的清单答 `manifest_malformed`（`gpkg.py` 的 `validate_manifest` 同序）。

`gpkg.py pack` 在打包时跑同一套规则的 Python 实现，让开发者在本机就发现问题。⚠️ 两份实现必须同步改；Python 那份是**便利**，C++ 那份是**闸门**。
