# 商店桥协议 `ghost-store/1`（spec-store-bridge v1）

远程商店页（`https://store.ghostproxifier.com/v1/`）嵌在本地壳 `plugins.html` 的 iframe 里，两者用 `postMessage` 通信。

**这份规范的读者是商店页的开发者**（也就是官方自己）。插件作者用不到它。

拒绝原因码见 [`spec-errors.md`](spec-errors.md)。
> 本文出现的数值上限、超时与正则以 [`spec-limits.md`](spec-limits.md) 为准；这里写出的数字是它的投影，改动必须两处同步。

---

## 1. 商店页不在信任链里

先说清楚这条，因为协议的每个约束都由它导出：

**商店页能说的只有一句「装 `com.x.y`」。** 仓库地址、开发者公钥、包哈希、**装哪个版本**全部由原生侧从**已验签的注册表**解出（见 [`spec-release.md`](spec-release.md)）。商店页被完全攻破的最坏结果，是弹出一个用户必须点确认的安装框，而装进去的仍然是那个插件**当前**的、已上架、已签名的版本。

推论，协议里因此**没有**这些东西：

- 任何 URL（包地址、注册表地址、下载源）
- 任何哈希、签名、公钥
- 任何文件路径
- **任何版本号**

⚠️ **最后一条是后加的，因为它差点漏掉。** 早期草案让 `install` 带一个可选的 `version`。那么一个被攻破的商店页（或 §1.1 里同样不信任的 CDN）可以指定一个**真实、已上架、签名有效**的旧版本——只要它没被逐条吊销——于是首次安装的用户拿到一个已知有洞的版本，而每一道验签都通过。「最坏结果只是一个确认框」这句话字面为真，用户关心的性质却没有交付。现在：**桥协议里没有 `version`，装的永远是当前版本**，而版本下限由注册表条目的 `minVersion` 兜底。

商店页给出 id，原生侧自己去查。**新增消息时守住这条**：一旦某个字段让商店页能影响「装的是哪串字节」，边界就没了。

## 2. iframe，以及为什么必须是独立子域

```html
<iframe id="storeFrame"
        sandbox="allow-scripts allow-same-origin allow-forms"
        referrerpolicy="no-referrer"
        src="https://store.ghostproxifier.com/v1/?app=1.3.0&lang=zh&mode=dark"></iframe>
```

⚠️ **商店页不能放在 `https://ghostproxifier.com/store/v1/`。** 侧栏广告是 `https://ghostproxifier.com/ad-v<AD_VERSION>.html`（4 个页面各嵌一份），两者会是**同一个 origin**，于是 §4 第一道校验 `event.origin !== STORE_ORIGIN` 对广告 frame **完全不起作用**，两个 frame 的权限也无法区分。换成独立子域，origin 才重新是一个有效判据。

⚠️ **没有 `allow-popups`。** 它是 §4 里那个「带着对的 origin 的窗口」攻击面的唯一来源，而 `openLink` 已经覆盖了外链需求。

URL 参数只有三个：

| 参数 | 值 |
|---|---|
| `app` | 本机应用版本的**核心三段**（`1.1.5-SNAPSHOT.42` 发 `1.1.5`），用于隐藏装不上的插件 |
| `lang` | `zh` \| `en` |
| `mode` | `dark` \| `light` \| `system`（跟随系统时按解析后的实际值发，不发 `system`） |

**不带 machineId、不带许可状态。** 已装插件列表**经 postMessage 发**，理由见 §6.1——那不是「不发」，写清楚比含糊好。

## 3. 信封

每条消息都是：

```json
{ "ch": "ghost-store/1", "v": 1, "type": "<类型>", "…": "…" }
```

`ch` 与 `v` 不对 → 忽略（页面上还有广告 iframe 和插件 UI iframe，它们也会发 message）。

## 4. 壳侧的三道校验

```js
window.addEventListener('message', function (event) {
    if (event.origin !== STORE_ORIGIN) return;                 // 1. 来源对
    if (event.source !== storeFrame.contentWindow) return;     // 2. 确实是那个 frame
    const m = event.data;
    if (!m || m.ch !== 'ghost-store/1' || m.v !== 1) return;   // 3. 信封对
    …
});
```

**三条缺一不可。** 只查 `origin` 不够：页面上还有别的 frame，而一个跳到商店域的窗口也会带着对的 origin。查 `source` 才钉住「就是我放进去的那个 frame」。

被拦下的消息**只记本地日志**（`bad_origin` / `bad_source` / `bad_envelope` / `unknown_type` / `bad_payload`），**不回消息给发送方** —— 那等于告诉一个可能是攻击者的页面它哪里被拦了。

### 4.1 改状态的消息：前台、用户激活、一次一个（PR ⑤ 评审补充）

三道校验只说明「消息确实来自商店页」。商店页本身不可信，所以还要限制它**何时**能让壳弹框：

- **`install` / `upgrade` / `uninstall` / `enable` / `disable` 以及 `open`，只在到达时三条同时成立才处理**：商店页签在前台；有用户激活（`navigator.userActivation.isActive`）；**且键盘焦点在商店 iframe 上**（`document.activeElement === storeFrame`）。只看激活不够——激活是整页的，用户在本地页面上点页签、点「取消」都算，于是商店页可以在用户点「取消」的下一刻、或切到商店页签的那一下，借本地的点击再弹一次框；焦点只有在用户最后一次点击或按键落在 iframe 里时才在 iframe 上。否则计数（`hidden_tab` / `no_activation` / `no_store_focus`）并忽略，不回话。
- **取消之后冷却**：用户对一个商店发起的确认框点了「取消」（或 Esc），此后 **2 秒**内的改状态消息一律忽略（计为 `store_cooldown`）——否则商店页能在框被关掉的同时把它原样再弹出来。
- 安装要先做只读预检（可能还要先刷新注册表），这之间用户可能已经离开商店。**弹框前再判一次**「商店页签在前台、焦点仍在商店」，不成立就回 `user_cancelled`、不弹框。
- **一次一个**：从一个商店操作的 `accepted` 起，到它的确认框被回答（或它不经确认框就结束）为止，另一条改状态的消息会得到 `accepted`，紧接着 `result{ok:false, error:"user_cancelled"}`（计为 `store_busy`）——商店页得到一个确定的答复去还原按钮，用户那边什么都不发生。用户回答之后，已确认的操作可能还在跑（下载），它的进度与结果照常转发，下一个商店操作可以开始。
- **确认框从不被替换**：框开着时再来的确认请求一律立刻答「取消」，开着的那个原样不动（不论它是商店发起的还是用户在「已安装」页签点出来的）。框里的「确认」按钮在内容就位后 **1 秒**内保持禁用，焦点先落在「取消」上（Esc 可用）——一次已经在路上的点击不会落到刚出现在指针下的按钮上。框开着时本窗口一旦失去焦点（例如焦点被移进商店 iframe），焦点立刻回到「取消」：iframe 抢不走键盘、Esc 一直有效，商店页也不会在用户作答期间「拥有焦点」。
- 同时跟踪的商店操作至多 **8** 个；一个迟迟等不到结果的操作 **10 分钟**后被静默遗忘（之后它的结果不再转发）。

**出站方向一律 `postMessage(msg, STORE_ORIGIN)`，绝不用 `'*'`。** iframe 可能已经把自己导航走了（虽然 `FrameNavigationStarting` 会拦，但这是第二道），`'*'` 会把 `machineId` 发给它当前所在的任意源。

## 5. 商店 → 壳

| type | 负载 | 改状态 | 确认框 |
|---|---|---|---|
| `ready` | — | | |
| `install` | `{id}` | ✅ | 插件 id、来源仓库、开发者公钥指纹（前 16 位十六进制）、已装版本（若有，写明「将升级到当前发布的版本」）——全部来自本地已验签注册表的**只读预检**（§6.0 `plugin.resolve`），不是商店页给的。**权限不在这里确认**：新装的权限进 `pendingPermissions`，**启用时**逐条确认（用户裁定 2026-09-26，`plugin-center.md` §8.4） |
| `upgrade` | `{id}` | ✅ | 同上（标题为「升级插件」）；权限比已装版本**多**时，升级后插件停用，启用时高亮新增项 |
| `uninstall` | `{id}` | ✅ | 写明会删掉插件私有数据 |
| `enable` | `{id}` | ✅ | 有待确认权限时是逐条权限框（新增项高亮），否则单句确认 |
| `disable` | `{id}` | ✅ | 单句确认 |
| `open` | `{id}` | | 无。切到「已安装」页签并聚焦该插件：已在运行则打开它的 UI；已启用但还没 `running` 则显示等待态；**未启用则只聚焦，不代替用户启用** |
| `activate` | `{license}` | ✅ | 展示**验签后**的许可范围，见 §5.2。`license` 必须是 1..16 KB 的字符串，否则 `bad_payload`（PR ①b 起处理） |
| `requestMachineId` | — | ✅ | 「把本机指纹交给商店页用于购买绑定？」框里写出指纹；**同意之后**才发 `machineId{opId, value}`（PR ①b 起处理） |
| `openLink` | `{url}` | | 无，但只放行 `https:`，要用户激活，且至多一秒一个（见 §7） |
| `resize` | `{height}` | | 无。壳自己夹取，范围见 `spec-limits.md` §8 |

### 确认闸门

**所有标了「改状态」的消息都先弹本地模态框**（复用 `shell.css` 的 `.modal-overlay` / `.modal-container` / `.btn-modal`）。那个框由本地页面渲染、本地 JS 判定，远程 iframe 既点不到也关不掉。

用户点取消 → 回 `result{ok:false, error:"user_cancelled"}`。

`requestMachineId` 也要确认：零知识原则（`.agents/AGENTS.md` §9）下，机器指纹只在用户**主动买码**时离开本机。

### 5.1 `opId` 由壳生成，并且立刻回给商店页

商店页发 `install` 时**不带** `opId`（它不该给本地操作起名字）。壳收到后、弹确认框**之前**生成一个 `opId`，并立即回一条：

```json
{ "ch":"ghost-store/1", "v":1, "type":"accepted", "id":"com.example.x", "opId":"op-7f3a" }
```

没有这一条，商店页在第一个 `progress` 到达之前无法把自己的按钮和后续消息对上——而用户完全可能连点两个插件的安装。`accepted` 只表示「收到了，正在问用户」，**不表示会装**：用户随后点取消就是 `result{ok:false, error:"user_cancelled"}`。

`opId` 也随命令下发给主进程，主进程在每条进度与结果里**原样回显**。生成方是壳而不是主进程：手动激活等入口没有商店页，却需要同一个关联 id。

### 5.2 `activate` 要先验后弹框

> v1 里商店页递来的码**不是商店服务端签的**：服务端手里没有签名钥匙（`plugin-center.md` §2.4b）。它交付的是维护者离线签好、存进商店后台的码。对壳来说这没有区别——壳只验签，不管码的来路。

确认框要展示「这张码给你什么」，而那要先验签。但 `POST /api/activate` 是**验签并写盘**的，不能为了显示就先写。若壳自己 base64url 解开直接显示，那就是**未验签**内容——一个恶意商店页可以让框里写着「pro + 全部插件 + 2099 到期」，用户点了确认才失败。

所以命令层支持 `{"key": "…", "dryRun": true}`：**验签、解析、返回能力集，不写任何文件、不改内存里的能力集**。壳的顺序是

```
dryRun 验 ──失败─▶ 直接报错，不弹框（一张伪造的码不值得占用户一次确认）
     │成功
     ▼
弹框展示范围 ──取消─▶ user_cancelled
     │确认
     ▼
不带 dryRun 再发一次 ──▶ 写盘、重载能力集
```

手动粘贴激活码那条路走同样两步。⚠️ `dryRun` 必须**真的不写**——它带着一个未经用户同意的、来自远程页面的输入。

壳这一侧（`store-bridge.js`，PR ①b）：`activate` 与 `requestMachineId` 与其它改状态的消息过**同一道闸**（前台、激活、焦点在商店、取消后冷却、一次一个），`accepted`/`result` **不带 `id` 键**（§6）。`dryRun` 失败直接回 `verify_failed`、细码只进本地提示、**不弹框也不发第二次请求**；框里的内容来自 `dryRun` 的**验签后**答复（`licenseId`、绑机或任意机器、到期、能力位名、插件），不是商店页对这张码的说法；确认后第二次请求体**恰好**是 `{key}`；成功后重读插件快照（付费插件的判定可能变了），`state` 仍不含任何许可信息。`requestMachineId` 在弹框前从 `GET /api/license-info` 取一次指纹、写进框里，**同意之后**才 `postMessage` 给 `STORE_ORIGIN`；取消则 `user_cancelled`、指纹从不离开本机。

## 6. 壳 → 商店

| type | 负载 | 时机 |
|---|---|---|
| `state` | `{appVersion, lang, theme, installed:[{id, version, enabled, state}]}` | 收到 `ready` 后，以及此后每次状态变化 |
| `accepted` | `{id?, opId}` | 收到一条改状态的消息后**立即**，早于确认框（见 §5.1）。`activate` 与 `requestMachineId` 没有插件 id，那两条的 `accepted` **省略 `id`** |
| `progress` | `{id, opId, phase, pct}` | 安装/升级过程中 |
| `result` | `{id?, opId, ok, error?}` | 操作结束。`activate` 与 `requestMachineId` 没有插件 id，那两条的 `result` **省略 `id`**，只靠 `opId` 对应 |
| `machineId` | `{opId, value}` | 用户同意 `requestMachineId` 之后 |

`installed[].state`：`stopped` \| `running` \| `crashed`。（这是宿主报上来的**运行**状态，PR ③ 起才有；PR ② 的 `GET /api/plugins` 快照里的 `state` 是**安装记录**的 `ok` \| `broken`，两者不是一回事。）

`phase`：`resolving` \| `downloading` \| `verifying` \| `extracting` \| `committing`。内部安装状态机有 8 个状态，到这 5 个阶段的映射表在 `plugin-center.md` §6 —— **两边名字不同是刻意的**（内部状态会细分，对外阶段不该跟着变），但映射必须查得到，否则两个 PR 各猜一套。`pct` 只在 `downloading` 有意义，其余为 `-1`。主进程发出的进度事件已经用的就是这 5 个名字（事件字段 `phase` / `pct`），壳原样转发即可。

### 6.0 壳 → 本地控制接口（PR ② 已就位）

改状态的消息在用户确认之后，由壳经 `ghostFetch` 调下面的命令（形状的权威在 `plugin-center.md` §6.3）：

| 桥消息 | 本地请求 | 请求体 | 立即答复 |
|---|---|---|---|
| `install` / `upgrade`（确认**之前**） | `GET /api/plugins/resolve?id=<id>` | — | `{"status":"ok","registry":{seq,state},"listed","entry"?,"installed"?}`，只读、不联网；`registry.state` 为 `none` 时壳先 `POST /api/plugins/refresh`、等 `op:"refresh"` 的结果再问一次 |
| `install` / `upgrade` | `POST /api/plugins/install` | `{id, opId}` | `{"status":"accepted"}`；结果随后作为 `op:"install"` 的事件到达 |
| `uninstall` | `POST /api/plugins/uninstall` | `{id, opId}` | 同上，`op:"uninstall"` |
| `enable` | `POST /api/plugins/enable` | `{id, confirmPermissions?}` | `{"status":"ok"}` 或错误 |
| `disable` | `POST /api/plugins/disable` | `{id}` | 同上 |

- `upgrade` 与 `install` 是**同一条命令**：装的永远是注册表里该插件的最新发布；已装的更旧就是升级，已装的就是它就直接成功，已装的更新答 `installed_newer`。
- `enable` 的 `confirmPermissions` 是**确认框里展示给用户的那组待确认权限**（取自快照的 `pendingPermissions`）。只有它与主进程此刻待确认的集合**恰好相等**才启用；否则答 `needs_confirm`、什么都不改，壳要重读快照、重新问。没有待确认权限时它被忽略。
- 错误一律是 `{"status":"error","error":"<spec-errors 码>"}`（包括 `invalid_json`）。这些细码**只进本地**；回给商店页前按 §6.2 折成三个粗粒度分类之一。
- **预检 `plugin.resolve`（PR ⑤）**：确认框的内容（仓库、开发者公钥指纹、是否已装）来自**本地、已验签**的注册表，只读、不联网、不排队；它不给版本号与权限——版本要等安装取到并验过发布描述才知道，所以权限改在**启用时**确认（§5 表）。`listed:false`、`entry.status:"delisted"`、`entry.revokedAll:true` 都不弹框，直接回 `verify_failed` 并在本地提示原因。刷新失败而注册表仍是 `none` 时，回刷新自己的失败（通常是 `registry_unreachable` → `network_failed`），不回「不在目录中」。不要在壳里自己去 GitHub 取清单：那份未经验证的清单不能拿来请用户同意。

### 6.1 交出去的东西要说得清

§2 说 URL 里不放已装列表，理由是那会进服务端访问日志。**但经 postMessage 无条件发过去只是少一行日志，不是不发。** 所以逐项写明：

| 数据 | 怎么给 | 理由 |
|---|---|---|
| `appVersion` / `lang` / `theme` | 自动发 | 商店页要靠它渲染 |
| `installed[]`（id + version + 运行状态） | **自动发，这是一次有意的披露** | 没有它，商店页无法显示「已安装 / 可更新」，商店就不成立。范围限于 id、版本与运行状态——**不含**安装路径、不含 `settings`、不含 `grantedPermissions` |
| `licensedPlugins[]` | **不发** | 商店页的服务端从它自己的账号体系就知道用户买了什么。客户端再告诉它一遍，是白送一份关联 |
| `machineId` | 用户在确认框点同意之后才发 | 零知识原则 |

### 6.2 `result.error` 是粗粒度分类，不是内部原因码

回给商店页的 `error` 只有三个值：`verify_failed` / `network_failed` / `user_cancelled`。

细码（`package_hash_mismatch`、`release_bad_sig`、`registry_rollback` …）**只进本地日志与本地界面**。理由与 §4 那条一样，只是适用面更广：它们比 `bad_origin` 精确得多，回给一个明确标注「不信任」的页面，等于告诉它信任链断在第几环。商店页需要的只是「失败了，还原按钮」，以及区分「让用户查网络」还是「让用户报问题」。

## 7. `openLink`

只放行 `https:`。壳先自己判一次，宿主侧再判一次（`IsShellOpenableUrl`，**同样只放行 `https:`**，拒绝 `http:`、`file:`、`ms-settings:`、UNC、带空格参数串等）。壳另要求一次用户激活，且**至多一秒打开一个**——一次激活持续数秒，没有这条，一段握着激活的脚本能一口气开一串标签页。

两侧都判是刻意的：壳那次是为了给用户即时反馈，宿主那次才是闸门——壳是 JS，改得动。⚠️ 正因为壳不算数，**宿主那一侧放行的集合不能比策略大**：两边都是 `https:` only。

## 8. 离线

iframe 加载失败（超时见 `spec-limits.md` §3，或 `onerror`）→ 壳显示离线态与重试按钮。

**「已安装」页签完全不依赖商店页**：启停、卸载、升级（用缓存的注册表）、打开插件 UI、手动粘贴激活码，断网全部可用。这是「核心功能离线可用」（`.agents/AGENTS.md` §9.1）在插件中心上的落点——商店是扩展服务，插件管理不是。

> v1 **没有**「从文件安装 `.gpkg`」入口，理由见 `plugin-center.md` §9.2：一个孤立的 `.gpkg` 旁边没有发布描述也没有签名，走不了信任链，而给它开例外就是开一条离线可用的装任意代码入口。

## 9. 前端测试

`src/tests/frontend/suite_store_bridge.js`（`ctest -L frontend`，本机跑；CI 不跑前端）钉住下面九条，外加 §4.1 的全部（前台 / 激活 / 焦点不在商店三种丢弃——含「本地页面上的点击带来的激活」；取消后冷却；弹框前焦点已离开商店；同一时刻的第二、三个操作答 `user_cancelled`；框开着时商店消息被丢弃、框原样不动——商店发起的与用户自己点出来的各一条；8 个上限与 10 分钟遗忘）、预检的四种拒绝与「刷新失败仍 `none`」、`openLink` 一秒一个、`resize` 夹取、离线与重试、快照变化去抖后恰一条 `state`：

1. 错误 `origin` 的 `install` → 壳无任何反应
2. 正确 `origin` 但错误 `source` 的 `install` → 同上
3. `ch` / `v` 不符 → 忽略
4. `install` 弹出确认框，**在用户确认之前不发任何 `ghostFetch`**
5. 确认后发出的请求**恰好**是 `{id, opId}`（没有 URL、没有哈希、**没有 version**），`opId` 与 `accepted` 里的一致；确认前只有那一条 `resolve` GET
6. 出站 `postMessage` 的 `targetOrigin` 是 `STORE_ORIGIN` 而不是 `'*'`
7. `openLink` 带 `file:` 或 `http:` → 不 postMessage 给宿主
8. `state` 不含 `licensedPlugins`
9. `result.error` 只可能是三个粗粒度值之一（`coarseError` 对 `contract_plugin_errors.h` 的每一个码逐个断言，含九个许可码——一律 `verify_failed`）
10. `activate`（PR ①b）：缺 `license`/非字符串/空/超 16 KB → `bad_payload` 不回话；后台/无激活/焦点不在商店 → 丢弃；`accepted` **没有 `id` 键**；确认前恰一次 `POST /api/activate` 且体为 `{key, dryRun:true}`；`dryRun` 失败 → 无框、`verify_failed`、没有第二次请求；取消 → `user_cancelled`；确认 → 第二次请求体**恰好** `{key}`、`result` 无 `id`；之后的 `state` 仍恰好四个字段
11. `requestMachineId`（PR ①b）：`accepted` 无 `id`；框开着时没有 `machineId`；取消 → `user_cancelled` 且从不发 `machineId`；确认 → `machineId{opId, value}`（假后端的 16 位 hex）发往 `STORE_ORIGIN`，再 `result` ok

「确认」延迟、确认框不被替换、名字去控制字符与双向控制字符在 `suite_plugins_shell.js`。

⚠️ **做变异验证，而且要逐条做**：三道校验各删一条、`targetOrigin` 改成 `'*'`、把细码透传出去——每一个变异都必须有断言转红。三道校验只被一条测试覆盖，就等于只有一道校验。

## 10. 商店页的参考实现（PR ⑥）

[`examples/store-static/v1/`](../../examples/store-static/) 是这份协议**商店一侧**的最小实现，将来原样部署到 `https://store.ghostproxifier.com/v1/`（托管要求在它的 README 与 `maintainer.md` §5（私有，不在本仓库））。它不是商店设计——没有框架、没有图片、没有账号——而是一份照着写不会写错的样板：

- `store.js` 暴露 `window.ghostStorePage`。`build` 是九个**纯**构造器（`ready`/`install`/`upgrade`/`uninstall`/`enable`/`disable`/`open`/`openLink`/`resize`），每条消息是信封加**至多**一个 `id`、`url` 或 `height`——**没有 `version`**（§1）；`catalog.json` 里的 `version` 只用来画「可升级」按钮。
- 出站只有 `post()` 一处：`window.parent.postMessage(msg, SHELL_ORIGIN)`，`SHELL_ORIGIN` 固定为 `http://127.0.0.1:23551`，绝不 `'*'`。
- 入站镜像壳的三道校验（`origin`、`source === window.parent`、信封），再判类型与字段；拦下的计数（`dropped()`，含 `unknown_op`：不是自己发起的操作）且**不回话**。参考实现不发 `activate`/`requestMachineId`，收到 `machineId` 仍按 `unknown_type`（壳一侧 PR ①b 已处理，见 §5.2）。
- 每个插件同一时刻一个在途操作；壳对它丢弃的消息不回话，所以 5 秒内没等到 `accepted` 就把按钮还给用户。`result.error` 三个粗粒度值各一句固定文案，其余通用文案，`user_cancelled` 不显示错误。
- 外来字符串一律 `textContent`、去控制与双向控制字符；页面除同源 `catalog.json` 外不发任何请求；CSP `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'`，无内联脚本与样式。

测试：`ghost_fe_store_page_test`（`suite_store_page.js`，页面单独）与 `ghost_fe_store_contract_test`（`suite_store_contract.js`：把 `store.js` 注入真实的 `plugins.html`，两边背靠背互喂——商店页构造的每条消息过壳的全部校验、壳的 `dropped()` 一个都不动；壳发出的每条消息过商店页的全部校验、只允许 `unknown_op` 计数；商店页的 `state()` 与壳最后一条 `state` 逐键相等）。两套都由 `harness.py mount=<examples/store-static>=store` 挂载源目录；CI 不跑前端，所以文本层面的不变量（CSP、单一出站源、无 `innerHTML`、目录与清单/注册表一致）另由 `ghost_examples_pack_test` 与 `ghost_plugin_examples_test` 在 CI 里守着。
