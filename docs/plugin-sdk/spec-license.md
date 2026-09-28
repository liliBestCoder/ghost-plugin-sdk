# 许可与激活码（spec-license v1）

激活码**就是**离线许可块。v1 里每一张都由维护者**离线手动签发**；服务器与商店页只负责交付，从不签发（`plugin-center.md` §2.4b）。客户端只做一件事：**验签**。

> **状态：§1–§6、§8、§9 已落地（PR ①a）；§7 付费插件与试用已落地（PR ①b）。**

拒绝原因码见 [`spec-errors.md`](spec-errors.md)。
> 本文出现的数值上限、超时与正则以 [`spec-limits.md`](spec-limits.md) 为准；这里写出的数字是它的投影，改动必须两处同步。

---

## 1. 形状

```
base64url(payload) "." base64url(sig)
```

- `payload`：UTF-8 JSON 的原始字节
- `sig`：官方**根私钥**对 `payload` 原始字节的 ECDSA P-256 / SHA-256 签名，原始 `r‖s` 64 字节
- base64url：RFC 4648 §5 字母表，**不带填充**
- 整串不含空白；粘贴框接受前后空白与换行，先 trim 再解析

> ⚠️ 与 [`spec-release.md`](spec-release.md) §1 同一条纪律：**签的是 payload 解码后的原始字节，不做 JSON 规范化。** 签名侧和验证侧各写一版「规范形式」，签的和验的就不是同一串字节。

## 2. payload

```json
{
  "v": 1,
  "licenseId": "GP-2026-0001",
  "machineId": "a3f2b81c9d4e0f57",
  "caps": {
    "pro": true, "plugins": true, "udpRelay": false,
    "manager": false, "qos": false, "aiAnalysis": false
  },
  "plugins": ["com.ghost.mcp-capture"],
  "issuedAt": "2026-09-20T00:00:00Z",
  "expiresAt": "2027-09-20T00:00:00Z"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `v` | int | ✅ | `1`。不认识 → `license_malformed` |
| `licenseId` | string | ✅ | 唯一，`^[A-Za-z0-9-]{1,64}$`。**同时是文件名**，所以字符集就是转义防线 |
| `machineId` | string | ✅ | 16 位小写 hex，或 `"*"` 表示不绑机 |
| `caps` | object | ✅ | 六个 bool，**缺失的键读作 `false`** |
| `plugins` | string[] | — | 授权的插件 id，默认 `[]` |
| `issuedAt` | string | ✅ | UTC，**严格** `YYYY-MM-DDTHH:MM:SSZ`（`src/shared/util_utc_time.h`：无小数秒、无偏移、无空白） |
| `expiresAt` | string | ✅ | 同上 |

`plugins` 至多 `kMaxLicensePlugins`（64）项，每项必须过 `IsPluginId`。payload 开头带 UTF-8 BOM 即 `license_malformed`（nlohmann 会悄悄跳过 BOM，客户端不跟着跳）。未知字段忽略。签发工具写出的 payload 是 `json.dumps(sort_keys=True, separators=(",",":"))` 的紧凑形式，`caps` 六键全写——但客户端**不做规范化**，只验收到的字节。

## 3. 验证顺序

```
0. 整块（trim 后）不超过 kMaxLicenseBlockBytes（16 KB）   失败 → license_malformed（读一个字节之前）
1. 形状：恰好一个 "."，两侧都是规范 base64url，sig 64 字节  失败 → license_malformed
2. payload 是合法 JSON、无 BOM、v 是整数 1，且**每个字段的
   类型与形状**都对（licenseId、machineId、caps、plugins、
   两个时间戳）                                          失败 → license_malformed
3. 根公钥验 sig（对 payload 原始字节）                 失败 → license_bad_sig
4. expiresAt > 现在                                   失败 → license_expired
5. issuedAt <= 现在 + 24h（时钟偏差容限）              失败 → license_not_yet_valid
6. machineId == "*" 或 == 本机指纹                     失败 → license_machine_mismatch
```

**先验签，后看内容。** 一个签名无效的块，它写的到期日和机器号都不值得读，更不值得据此给用户一条「已过期」的提示——那会把「这码是伪造的」说成「这码过期了」。

「先验签」说的是**判定**（到期、未生效、绑机）只对签过的字节做；**字段形状**在验签之前查（PR ①a 计划 D7）：一份连形状都不对的文档答 `license_malformed` 比答 `license_bad_sig` 更诚实，也免得为一坨垃圾跑一次 ECDSA。

base64url 是**严格**的（`src/shared/util_base64url.h`）：只认 url 字母表、不带填充、长度不为 4k+1、**末字符未用的位必须为 0**——否则 `Zg` 与 `Zh` 都解成 `f`，同一张码有两种拼法。这条与 `.sig` 文件「不要求为 0」的裁定刻意不同：`.sig` 来自别人的编码器，许可块只来自我们自己的工具。

判定本体是 `src/modules/config/domain/license_verify.h` 的**纯逻辑**（`VerifyLicense(text, verifier, nowSec, machineId, doc)`）：收整块文本、一个注入的验签函数、当前时间、本机指纹，返回原因码或空串与解析出的文档；能力集由 `capability_compose.h` 的 `ComposeCapabilities` 从基线与所有被接受的文档组合出来。CNG 调用（与自动升级共用的 `EcdsaP256Verify`，见 `plugin-center.md` §2.2）在它外面，于是它能拿已知向量单测。

## 4. 存储与多块并存

```
%LOCALAPPDATA%\GhostProxifier\licenses\<licenseId>.key
```

一块一文件。启动时逐个重验，能力取**并集**、`plugins[]` 取并集。按文件名排序、至多读 `kMaxLicenseFiles`（64）个，多出来的以 `license_too_many` 拒绝；激活一个**新** id 而目录里已有 64 个 `*.key` 时，写之前就答 `license_too_many`（`dryRun` 预览同样答它；替换一个已有 id 不算新增）——否则激活答 ok、下次启动却跳过那个文件；每个文件读之前先看大小（`kMaxLicenseBlockBytes`）。**文件名不是判据**——`foo.key` 装着 `GP-X` 照样接受，界面按文件名列出。

⚠️ `licenses\` 在用户可写的数据目录下，而 Ghost 常以管理员运行——与 `plugins\` 同一处境。所以存储层（`src/modules/config/service/license_store.{h,cpp}`）**没有一处按路径碰它**：数据目录是经 `ghost_safefs::OpenAnchorDir` 打开的锚点（它自己或祖先是链接即拒绝），`licenses\` 相对它打开且不跟随链接，每个文件相对读取；写入是一个**每次新取名**的临时文件（`.<id>.key.<16 hex>.tmp`）再按句柄改名。`licenses\` 或数据目录是链接时：加载答基线并说明原因，激活答 `io_error`。`*.key` 名字上是链接或硬链接 → `io_error`（不读穿）；是删除挂起（delete-pending）的文件 → 视作「列目录之后被删了」，静默跳过。一次被中断的激活留下的临时文件，由下一次激活按句柄删掉（只删这个精确形状的名字）。

**坏块跳过并记一条 Control/Warn，不连累其余。** 买了三个插件、其中一张码过期，不该让另外两个一起失效。

⚠️ **到期日是按位的，不是全局一个。** A 块给 `pro` 明天到期、B 块给 `plugins` 2030 到期——取最晚会让界面显示 2030 而 `pro` 明天失效，那是在误导。每个能力位各带自己的 `expiry` 与 `source`，界面按位显示（见 §6）。

⚠️ **已知限制：只在启动时重验。** 会话中途到期的许可要到下次启动才失效。这是刻意的简化（定时重扫要么持锁要么复制一份状态，而收益只是让一个诚实用户早几小时看到过期提示），但要写出来——别让人以为有个定时器。

`POST /api/activate` 收到 `{"key": "<许可块>"}`：验签 → 通过则写 `<licenseId>.key`（trim 后的块，逐字节；同 id 覆盖）→ 重新加载全部块 → 返回 `{status:"ok", dryRun:false, licenseId, machineId, issuedAt, expiresAt, caps:[位名], plugins:[…]}`。验不过则**不写盘**，返回 `{status:"error", error:"<原因码>"}`；非 `dryRun` 的拒绝记一条 Control/Warn（tag `"License"`）。坏 body（`_bodyInvalid`、`key` 不是字符串、`dryRun` 不是布尔）答 `{status:"error", error:"invalid_json"}`——与 `plugin.*` 同形，不是 `update.*` 的旧形状。写入与重新加载在一把锁里，两个并发激活不会让内存状态对应错一次写入。

`GET /api/license-info` 答 `{status:"ok", valid, machineId, caps:{六位各 {on, source:"builtin"|"license"|"trial", expiry}}, licensedPlugins:{id:{on,source,expiry}}, licenses:[{file, licenseId, machineId, issuedAt, expiresAt, caps, plugins}], rejected:[{file, error}]}`。**`valid` = `pro.on`**（页面的 `AppState.isLicensed` 与进程卡片的「受限」读它）；旧的 `userId`/`type`/`expiry` 已删除；没有 `baseline` 键——按位的 `source == "builtin"` 已经说明同一件事。两条命令**插件与宿主都够不到**（`api/wire/plugin_permissions.h` 的表里没有它们）。

**`{"key": "…", "dryRun": true}` 只验不写**：验签、解析、返回能力集，一个文件都不碰。界面要先把「这张码给你什么」摆给用户看，再问要不要激活，而那需要一次不产生副作用的验证。顺序见 [`spec-store-bridge.md`](spec-store-bridge.md) §5.2；两条激活路径（商店页递来的、用户手动粘的）走同一个两步。

⚠️ `dryRun` 带着的是**未经用户同意的、可能来自远程页面的输入**，所以它必须真的不写盘、不改内存里的能力集、**一条日志都不记**（连 Warn 都不记）—— 它唯一的输出是返回值。`test_command_contract` 钉住「状态不变、没有 `licenses\`、事件序号不动」。

## 5. 机器指纹

- v2：`SHA-256(以太网 MAC + "|" + 盐)` 取**前 8 字节**，小写 hex（**16 字符**）。
- 盐由 `GenerateRandomBytes`（CSPRNG）生成 —— v1 用的是 `rand()` 配 `srand(time(NULL))`，而同一个文件里就有 CSPRNG。
- **盐和结果一起持久化，且只用 HKCU**：`HKCU\SOFTWARE\GhostProxifier` 的 `MachineIdV2` 与 `MachineSaltV2`。两条理由：
  1. 只存**结果**不存盐，缓存一丢（重装、清注册表）就会生成一个新的随机盐 → 新的 machineId → **全部绑机许可 `license_machine_mismatch`**。盐是指纹的一部分，不是临时值。
  2. 现有的读法是 **HKLM 优先、HKCU 回落**，而本进程是 asInvoker，**HKLM 写不进**。于是普通运行落 HKCU、管理员运行落 HKLM，同一台机器两个指纹。v2 两个键都只读写 HKCU。
- 旧键 `MachineId` **不迁移、不读取**。迁移一个非密码学指纹没有意义，而两键并存让回滚到旧版本仍能用。
- 盐是 **16 字节、32 位小写 hex**。读取顺序（`src/platform/machine_id.{h,cpp}` 的 `MachineIdV2`）：已持久化且形状合法的 `MachineIdV2` **原样返回、不读 MAC**（换网卡、拔网线、MAC 随机化都不能让绑机码失效）→ 否则用合法的盐重算并写回 id（删 id 留盐 → 得到同一个 id）→ 盐缺失或损坏 → 新生成盐（损坏时出参说明「salt regenerated」）。**CSPRNG 失败时什么都不持久化**（绝不写全零的盐），本次答空串——本次没有指纹，绑机码一律 `license_machine_mismatch`，下次启动再试。
- `machine_id.cpp` 的代码里**没有** `HKEY_LOCAL_MACHINE`（`test_machine_id_v2` 源码级钉住：动态用例分辨不出「只读 HKCU」与「先读 HKLM 再回落」）。
- 用户在 license 页能看到自己的指纹并复制。

⚠️ 指纹**不是**安全边界，是绑定用的标识。它只在用户主动买码时离开本机（见 [`spec-store-bridge.md`](spec-store-bridge.md) §5 的 `requestMachineId`）。

## 6. 能力集

```cpp
struct Capability {
    bool on = false;
    std::string expiry;                                  // 该位自己的到期
    enum class Source { Builtin, License, Trial } source = Source::Builtin;
};
struct Capabilities {
    Capability pro, plugins, udpRelay, manager, qos, aiAnalysis;
    std::map<std::string, Capability> licensedPlugins;   // 按插件 id
};
```

**基线（无任何许可）** 是 `src/shared/policy_capability_baseline.h` 的**一个常量** `kUnlicensedBaseline`，`ComposeCapabilities` 是它唯一的消费点。**今天 = `{pro, plugins, udpRelay}` 开，`manager/qos/aiAnalysis` 关**（用户裁定 2026-09-27：存量用户暂不降级；升级前的桩对所有人给 PRO，UDP 中继从不看许可）。本节早先写的 `pro=false, plugins=true` 是**将来降级时要改成的目标值**，不是今天的值。`ghost_capability_baseline_test` 把六个位逐个写死——翻这个常量的提交必须同时改那条测试，转红就是目的。基线给的位 `source == Builtin`、`expiry == ""`，许可不能把它变成会到期的位（基线是地板）。

**插件中心本身免费可用** —— 装插件、启用免费插件、用商店，都不需要许可。付费的是个别插件与下面那张清单。

### 一个功能只读一个能力位

| 功能 | 读哪个位 | 不满足时 |
|---|---|---|
| 同步握手 `sync`（`target.update-config` **与** `target.add`） | `pro` | `requires_pro`（拒绝，不再「接受后压平」；判在任何赋值之前） |
| 每目标独立 DNS 模式（免费的是 `""`/`"system"`/`"default"`，其余读 `pro`；`src/modules/config/domain/policy_pro_gate.h`） | `pro` | `requires_pro` |
| 卡片组批量启停（`cardgroup.launch`/`cardgroup.stop`） | `pro` | `requires_pro` |
| 进程卡片的「受限」显示（`/process-stats`） | `pro` | 卡片标受限 |
| UDP 中继（`FillNodeCfg` 的 `udpRelayActive` 第一条子句，唯一的执行点） | **`udpRelay`** | 保持阻断（fail-closed 方向不变） |
| 付费插件启用 | `licensedPlugins[id]` | `requires_license` / `trial_expired` |

⚠️ **UDP 中继读 `udpRelay`，不读 `pro`。** 早期草案把它同时列进「`pro` 清单」并保留独立的 `udpRelay` 位——两个开关管一件事，而且没有优先级。签发方要开这个功能就置 `udpRelay`。

⚠️ **`manager` / `qos` / `aiAnalysis` 今天不门控任何东西。** 它们属于企业版 manager 域与 issue #12 的子项目 D，保留在 schema 里只是为了签发格式稳定。写在这里免得有人去找那个不存在的执行点。

基础代理接管、DNS 防泄漏、进程注入、规则管理保持**离线可用、无需登录**（`.agents/AGENTS.md` §9.1 的业务边界）。

### 门控只管「改」，不管「已经存着的」（将来翻基线前必须裁定）

上表的 `pro` 门控拦的是**设置的变更**（`target.update-config`、`target.add` 收到的 `sync`/`dnsMode`）与卡片组启停。**已经存进 `targets.json` 的 `syncHandshake`/`dnsMode` 在启动目标时照常生效**——`AppendGhostEnvVars` 不读能力位。今天基线给 PRO，这不可见；将来若把基线翻成 `pro=false`，就要先裁定：存量目标上已开的同步握手与每目标 DNS 是保留（grandfather）、启动时忽略、还是启动时拒绝。本 PR 不替产品做这个决定。

同一次裁定还要管界面：`dnsMode:"default"` 意为「跟随全局 `dns` 设置」，而全局 `dns.enabled` 为真时它**解析成 DoT**（`node_repository.cpp` 的 `dnsModeTarget == "default"` 分支）——免费集合里的 `"default"` 实际可能给出 `dot` 的效果；新建目标对话框的 DNS 下拉**默认选中 `dot`**（`index.html` 的 `#newTargetDns` 第一项）。翻基线之后，一个无 PRO 的用户用默认值新建目标会直接收到 `requires_pro`。所以翻基线要连同这两处的界面决定一起做（默认值改成 `system`？`default` 在无 PRO 时怎么解析？）。

### 存量用户：已裁定，不降级

升级前 `LoadLicense()` 无条件 `valid = true`，每一个现存用户都是 PRO。**已裁定（2026-09-27）：暂不降级**——基线常量今天就等于那个桩的行为，上表的门控在任何安装上都不会触发，直到有人翻那个常量。门控仍然写好并测好（`test_command_contract` 的「pro gate」节用 `SetLicenseState` 把 `pro` 关掉驱动它们），前端的四个调用点对 `requires_pro` 显示翻译过的提示，所以将来翻基线时页面不会静默失败。

对冲：`%LOCALAPPDATA%\GhostProxifier\first-seen.json`（`src/modules/config/service/first_seen.h`）在本版本第一次启动时**只写一次**本机的首见时间、版本号，以及当时已存在的状态文件中最早的创建时间（`priorStateAt`——全新安装没有这个键）。**什么都不读它**；将来若降级，grandfather 规则需要的本地证据就是它。零回传，与其余一切同样可伪造。

## 7. 付费插件与试用

> **状态：已落地（PR ①b）。** 判定是一个纯函数 `JudgePaid`（`src/modules/plugin/domain/paid_gate.h`，`test_paid_gate` 逐行写死的真值表），插件服务在两处问它（`plugin_service.cpp`，`test_plugin_service` 41–45）。

- 一个插件收不收费，由**官方注册表**的 `paid` 字段决定（不是插件清单自述，理由见 [`spec-release.md`](spec-release.md) §2.5）。取的是**正在使用的注册表**里该 id 的条目（上架或下架都算——下架不会让付费插件变免费）；没有注册表、或注册表里没有这个 id，读作免费：注册表是 `paid` 的唯一来源。
- 启用时的判定顺序：
  1. `caps.licensedPlugins` 含该 id → 放行
  2. 否则若 `paid.trialDays` 存在且没有该 id 的试用记录 → 写入当前时间，放行
  3. 试用期内 → 放行
  4. 用尽 → `trial_expired`，**主进程不下发 `start`**
  5. `paid` 存在但无 `trialDays` 且无许可 → `requires_license`——**注册表 v1 出不来这个形状**：`ParseRegistry` 拒绝 `paid:true`，付费条目必须带 1..90 的 `trialDays`。判定仍对它有答案（将来的注册表 v2 可能允许），`test_paid_gate` 有这一行，服务层测试不构造它。

  「试用期内」是 `起点 + trialDays × 86400 > now`，**严格大于**——到点即用尽。起点读不出来（不是 `YYYY-MM-DDTHH:MM:SSZ`，`plugins.json` 的读法已把它换成 `1970-01-01T00:00:00Z`）读作**已用尽**，绝不读作「没有记录」（那等于送一次新试用）。

- **两个执行点**（`plugin_service.h` 的「paid plugins and trials」一节）：
  - **启用**（`SetEnabled(true)`）：在 `plugins.json` 的编辑**之内**判，位置是**吊销判定之后、权限确认与翻 `enabled` 之前**——拒绝时什么都不写：`enabled` 不变，待确认的权限（`pendingPermissions`）**仍是待确认**（一个谁都不许运行的付费插件，不该先把权限确认掉）。放行且需要开始试用时，在同一次编辑里写 `trials[id] = now`，并且只在其余检查（`needs_confirm`）都过了之后才写——被 `needs_confirm` 拒绝的启用不开始试用。
  - **服务发出的每一次 start**（`MakeStartSpec`：启用、`Start()` 时对每条已启用记录、`StartEnabled()`、安装后仍启用的升级）：再判一次；拒绝就**不下发 `start`**、记一条 Warn（带原因码）、告诉宿主 `OnDisabled`（升级时可能还有旧版本在跑）、**不改 `enabled`**——页面看到的是「需要许可 / 试用已结束」，不是「被停用了」。一条已启用却没有试用记录的记录（插件在启用之后才变成付费，或手改的 `plugins.json`）在这里**开始试用**：在清单读通**之后**（启动不了的插件不烧掉试用）、start **之前**，于一次 `plugins.json` 编辑里写 `trials[id]`（编辑里再核记录：没了/停用了/换了版本则不启动；启用抢先写了试用则按那个起点重判）。**写不进去就不启动**（fail closed：文件损坏、不可写、`v` 更新都一样），记 Warn（`io_error`）并 `OnDisabled`——否则损坏本身就等于无限期试用。
- **`trials` 只由这两处写**，卸载从不动它；快照也不写（`plugin.list` 里「将开始的试用」读作 `verdict:"ok", trial:true`）。
- **激活许可之后**（`license.activate` 成功、非 `dryRun`），命令层调插件服务的 `StartEnabled()`：被判定拒绝过的已启用付费插件现在就能跑，不必等下次启动；对已在运行的插件无副作用。
- **`Start()` 时没有经过验签的注册表**（没有缓存、或缓存验不过/回滚），付费插件在这一次会话里读作免费——注册表是 `paid` 的唯一来源。这仍在「只门诚实用户」之内；但若有一天在已装用户身上**轮换根钥**，旧缓存会验不过，要记得这一条。
- **宿主的 `start` 行**带上同一个判定的声明 `license:{licensed, trial, expiresAt?}`（[`spec-host-protocol.md`](spec-host-protocol.md) §3.1）；免费插件是 `{licensed:false, trial:false}`。许可来自能力集的 `licensedPlugins[id]`（`expiresAt` 取覆盖它的许可里最晚的那个）；有许可时**不开始试用**。
- **`plugin.list` 的每条记录**带 `license:{required, licensed, trial, expiresAt?, verdict:"ok"|"requires_license"|"trial_expired"}`，由同一个判定算出；插件页据此显示「需要许可」「试用已结束」（不给启用、不给打开，给「前往授权」）或「试用至 …」「已授权」徽标。
- **`license.info` 的 `source:"trial"` 今天不出现**：试用记在 `plugins.json`，不进能力集；`licensedPlugins` 只来自许可。
- ⚠️ **已知边界：宿主的自动重拉与 bridge 的重新同步复用上一份 `spec`，不经服务。** 一场会话中途到期的试用会一直撑到下一次**服务级** start（停用再启用、升级、或下次启动 Ghost）。这与 §4「只在启动时重验」是同一条裁定，不另加定时器，也不在 bridge 里重判。

⚠️ **试用记录不能存在插件条目里。** 卸载会删掉整个条目，于是「卸载重装」就刷新了试用期——那不是 §8 里说的「用户改自己机器上的文件」，是**点两下按钮**，任何人都会发现。所以 `trialStartedAt` 放 `plugins.json` 顶层一个与安装状态无关的 `trials` 映射，卸载不删。
- 握手的 `license` 字段把结论告诉插件，供它展示或自检。**执行点在主进程**，不在插件里——一个把 `licensed` 改成 `true` 的插件，仍然不会被 `start`。

### 7.1 独立运行时的付费门控

插件独立运行（[`spec-host-protocol.md`](spec-host-protocol.md) §3.0）时**没有宿主，也就没有 `license` 声明**，主进程那个「无许可不下发 `start`」的执行点根本不在场。

这不是漏洞，是 §8 那条裁定的自然推论：本地门控本来就只面向诚实用户。对付费插件，SDK 提供一个自检助手，插件作者可以用它把门控搬进自己的进程：

```python
from ghost_plugin_sdk import verify_license
lic = verify_license(code)         # code：用户粘进来的激活码，与 Ghost 里那张是同一种
if lic and "com.example.x" in lic.plugins: ...
```

- 助手里**内嵌同一把官方根公钥**，算法、格式、验证顺序与 §3 完全一致。
- 绑机码要拿本机指纹比对，助手读 Ghost 写在 `HKCU\SOFTWARE\GhostProxifier` 的 `MachineIdV2`；Ghost 从没在这台机器上跑过就读不到，此时**只有 `"*"` 不绑机的码能通过**。
- 助手也会顺手读 Ghost 的 `licenses\*.key`——用户在 Ghost 里激活过，独立运行时不必再粘一次。
- **它仍然不是安全边界**。一个把 `if lic` 删掉的用户照样能跑，§8 说清楚了为什么不去防这个。

要不要在独立模式下门控，由插件作者定；Ghost 不替它执行，也不检查它有没有执行。

## 8. 显式非目标：本地防篡改

这条在 `.agents/AGENTS.md` §9.1 里，值得在签发规范里再写一遍，因为「加了签名」最容易让人以为它变成了安全边界：

**不做本地防篡改、不做混淆、不做反调试。** 本产品注入 DLL、改写别人的进程内存；能跑起来它的人翻转一个本地 `bool` 比写一个 hook 容易得多。客户端许可检查不是减速带，是戏，而它招来的混淆是 AV/EDR 误报的头号来源。

签名在这里的作用是**防伪**而不是**防破**：它让「伪造一张别人的许可」变得不可能，让官方能精确地知道一张码属于谁、能不能吊销。它不试图阻止一个用户改自己的机器。

高价值能力靠**服务端确权**（节点列表、规则更新、Manager、AI 分析后端都在服务端），不靠本地那个 bool。

## 9. 签发

```bash
# 一次性：生成根密钥对
python tools/license/sign_license.py keygen --out %USERPROFILE%\.ghost-plugin-keys\root
# → ...\root\root-private.pem（离线保管，**不在任何仓库里**：keygen 拒绝写进 git 工作树）  ...\root\root-public.b64（编进 contract_plugin_pubkey.h）

# 出一张码
python tools/license/sign_license.py issue \
    --key %USERPROFILE%\.ghost-plugin-keys\root\root-private.pem \
    --license-id GP-2026-0001 \
    --machine-id a3f2b81c9d4e0f57 \
    --caps pro,plugins \
    --plugins com.ghost.mcp-capture \
    --days 365
# → 一行激活码

# 自查一张码：按客户端的顺序（§3）判定，打印 JSON 结论与原因码
python tools/license/sign_license.py inspect <激活码> --pub %USERPROFILE%\.ghost-plugin-keys\root\root-public.b64 [--machine-id <16 hex>] [--now <UTC>]

# 打印一把私钥对应的公钥（88 字符 base64，原始 X‖Y）
python tools/license/sign_license.py pubkey --key %USERPROFILE%\.ghost-plugin-keys\root\root-private.pem
```

`keygen` 的产物与 `tools/plugin/gpkg.py keygen` 同形（PKCS#8 PEM + 88 字符 base64），同一对钥匙既签注册表也签许可。`issue` 在本地拒绝客户端会拒的东西（`licenseId`/`machineId` 形状——`machineId` 必须**小写**、能力名闭集、每个插件 id、至多 64 个插件、编码后超过 16 KB 的整块），拒绝时不出码；`--now` 供测试。私钥从不打印，错误消息从不回显文件内容。需要 Python `cryptography`（`tools/update-sign/requirements.txt` 的带哈希 pin），没有就以 2 退出；它的两条测试（`ghost_license_tool_py_test`、`ghost_license_tool_test`）没有它就以 77 跳过。

同一把根私钥签注册表（[`spec-release.md`](spec-release.md) §2）与许可。**离线保管，只在这两件事上使用。**

**v1 不做在线签发，根私钥不上任何服务端**（裁定见 `plugin-center.md` §2.4b）。用户付款并交出机器指纹后，由维护者离线签一张码，再经商店页、邮件或其他渠道交给用户。服务端可以存码、发码，但手里没有签名钥匙。

客户端不区分码从哪条渠道来——验签通过就是有效的码。所以 Ghost 里不需要在线 redeem 协议，主进程也不需要认识任何许可服务端；将来若加在线签发，产物格式不变，客户端不用改（但那时要重新论证密钥方案，见 `plugin-center.md` §2.4b）。
