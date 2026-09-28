# 发布与注册表（spec-release v1）

两份文档、两把钥匙。本文定义它们的字节形状与签名方式。

拒绝原因码见 [`spec-errors.md`](spec-errors.md)。
> 本文出现的数值上限、超时与正则以 [`spec-limits.md`](spec-limits.md) 为准；这里写出的数字是它的投影，改动必须两处同步。

---

## 1. 签名约定（两份文档共用）

- 算法：**ECDSA P-256 + SHA-256**。
- 签名值：原始 `r‖s` 共 **64 字节**，base64（标准字母表，带 `=` 填充）。
- 载体：**分离式 `.sig` 文件**。`X.json` 旁边放 `X.json.sig`，内容就是那串 base64，允许结尾有换行/空白（精确规则见 `spec-limits.md` §5）。
- 覆盖范围：**被签文件的原始字节，一个字节不多不少。**
- 公钥：未压缩点去掉 `0x04` 前缀，即 `X‖Y` 共 64 字节，base64（88 字符）。

> ⚠️ **不做 JSON 规范化。** 不排序键、不压缩空白、不重新转义。规范化是验签实现里最经典的错误来源：签名侧和验证侧各写一版「规范形式」，签的和验的就不是同一串字节，而失败形态是「偶尔验不过」——最难查的那一类。验证方哈希的，就是它刚下载、马上要 `json::parse` 的那串字节。
>
> 推论：**文件下载后不得做任何处理再验签**，包括去 BOM、转行尾、trim。要验的是网络上来的原始 body。

---

## 2. 注册表 `registry.json`（官方根钥签）

### 2.1 位置

| 用途 | URL |
|---|---|
| 主 | `https://github.com/liliBestCoder/ghost-plugin-registry/releases/latest/download/registry.json` |
| 备 | `https://ghostproxifier.com/plugins/registry.json` |
| 签名 | 以上两个 URL 各自 `+ ".sig"` |

两处必须**逐字节相同**。每个来源的文档与它的 `.sig` 从**同一个来源、同一条路由**取。主拉不到（或验不过、解析不了、`seq` 回滚）用备；都不行时，本地已验过的缓存**继续用于显示与吊销，但不用于安装**——安装只依据本次运行里刷新成功、不超过 `kInstallRegistryMaxAgeMs` 的那份注册表，否则先刷新，刷新失败就以刷新的码失败（见 `plugin-center.md` §2.3）。

主来源经 GitHub 的 `latest/download` 入口：它先 302 到 `releases/download/<标签>/registry.json`，再到存储主机。这一跳（**只对注册表仓库、只对 `registry.json` 与 `registry.json.sig` 两个文件名**）在重定向白名单里，见 `spec-limits.md` §3。

### 2.2 内容

```json
{
  "v": 1,
  "seq": 42,
  "updatedAt": "2026-09-20T00:00:00Z",
  "plugins": [
    {
      "id": "com.example.events-viewer",
      "repo": "example/events-viewer",
      "devKey": "BEl3…（base64，88 字符）",
      "status": "listed",
      "paid": false
    },
    {
      "id": "com.ghost.mcp-capture",
      "repo": "liliBestCoder/ghost-plugin-mcp-capture",
      "devKey": "BKm1…",
      "status": "listed",
      "paid": { "trialDays": 7 }
    }
  ],
  "revoked": [
    { "id": "com.bad.thing", "versions": ["*"], "reason": "malware" },
    { "id": "com.ok.thing",  "versions": ["1.2.0"], "reason": "credential leak in 1.2.0" }
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `v` | int | 必须是整数 `1` |
| `seq` | int | 非负整数（≤ `LLONG_MAX`），**单调递增**。小于本地记录的最大值 → `registry_rollback` 并继续用缓存 |
| `updatedAt` | string | 可选，ISO 8601 UTC，仅供显示 |
| `plugins[].id` | string | 同 [`spec-manifest.md`](spec-manifest.md) 的 id 语法。**一份注册表里唯一**，重复即整份 `registry_malformed` |
| `plugins[].repo` | string | `owner/name`，`^[A-Za-z0-9._-]{1,39}/[A-Za-z0-9._-]{1,100}$`，两段都不以 `.` 开头，整串不含 `..` |
| `plugins[].devKey` | string | 该插件的开发者公钥，base64 88 字符 |
| `plugins[].status` | string | `listed` \| `delisted` |
| `plugins[].paid` | false \| object | 缺失、`false` 或 `{"trialDays": 1..90}`。**`true` 拒绝**（没说试用多久） |
| `plugins[].minVersion` | string | 允许安装的**最低**版本（规范三段），与 `revoked[]` 一起维护。缺失读作 `0.0.0` |
| `revoked` | array | 可选 |
| `revoked[].id` | string | 同 id 语法 |
| `revoked[].versions` | string[] | `["*"]` 表示全部版本，否则精确的规范版本列表。**可以为空**（什么都不吊销，但不拒整份注册表） |
| `revoked[].reason` | string | 可选，≤ 200 个码点，会展示给用户 |

注册表只有维护者写，但仍按不可信输入解析——签名覆盖的是字节，签过名的错误还是错误。**严格**只用在宽松读法会改变一次信任判定的地方（重复 id 会让「用哪个 `devKey`」取决于扫描顺序；`paid: true` 没有试用长度），其余从宽（空的 `versions` 什么都不吊销）：过严也有真实代价——被客户端拒掉的注册表，里面**新增的吊销**也就永远送不到。

### 2.3 为什么需要**两个**计数器

签名验证对**重放**毫无办法，而重放有两种形状，各需要一个计数器：

| 机制 | 挡的是 |
|---|---|
| `seq` 单调 | 「换一整份真实的旧注册表」，把一次吊销回滚掉 |
| 条目 `minVersion` | 「在当前这份注册表里挑一个真实的旧版本」 |

两份文件都从没被篡改过，每一道验签都通过。少了 `seq`，一次吊销可以被整体回滚；少了 `minVersion`，被攻破的商店页或 CDN 中间人可以把首次安装的用户钉在一个已知有洞、但签名完全有效的旧版本上。两者不能互相代替。

`minVersion` 因此是**发版流程的一部分**：修掉一个安全问题之后，除了发新版，还要把注册表条目的 `minVersion` 抬到该版本——只发新版不改它，旧版本仍可安装。

### 2.4 为什么注册表里没有展示信息

没有 name / description / 分类 / 图标 / 下载量 / 截图。

- 商店页的展示数据由远程服务端自己出——它本来就在渲染自己的页面。
- 「已安装」页签的展示数据来自已装包里的 `manifest.json`，离线可用。

于是注册表是一份纯粹的**信任 + 定位**文档：它小、改动少、每次改都要动根钥。把文案放进来，等于让「改一句介绍」也走一次根钥签名。

### 2.5 `paid` 为什么在这里而不在清单里

收费与否是**官方**的商业事实，许可又只由官方密钥签发。如果让开发者在自己的清单里自述 `paid`，那么一个想白送的开发者写 `false`、官方却在卖，两边就对不上；更实际的是，清单是开发者可改的，而这个字段决定要不要查许可。

---

## 3. 发布描述 `ghost-plugin.json`（开发者钥签）

### 3.1 位置

插件仓库的 GitHub Release，tag `v<version>`，三个资产：

| 资产 | 说明 |
|---|---|
| `ghost-plugin.json` | 本文档 |
| `ghost-plugin.json.sig` | 开发者私钥对上一行**文件字节**的签名 |
| `<id>-<version>.gpkg` | 包本体 |

查最新版走 GitHub 的稳定重定向入口（**不用 GitHub API**，匿名限流 60 次/小时）：

```
https://github.com/<repo>/releases/latest/download/ghost-plugin.json
```

指定版本：

```
https://github.com/<repo>/releases/download/v<version>/<asset>
```

**只有这两种形状能被请求**（`<repo>` 必须是注册表里**这个插件**条目的 `repo`——白名单每次只放行正在处理的那一个插件的仓库；`<version>` 必须是规范三段；`<asset>` 只含 `[A-Za-z0-9._-]`、非空、不以 `.` 开头），见 `spec-limits.md` §3。

安装时：发布描述与 `.sig` 取自 `latest/download`（所以**安装的永远是最新发布**），包取自 `download/v<发布描述里的 version>/<package.name>`。GitHub 把 `latest/download/<资产>` 先 302 到 `releases/download/<标签>/<资产>`，这一跳只因标签恰好是 `v<version>` 才在白名单里——**标签不按 `v<version>` 打，插件就取不到**。

### 3.2 内容

```json
{
  "v": 1,
  "id": "com.example.events-viewer",
  "version": "1.0.0",
  "minAppVersion": "1.3.0",
  "releasedAt": "2026-09-20T00:00:00Z",
  "package": {
    "name": "com.example.events-viewer-1.0.0.gpkg",
    "size": 148213,
    "sha256": "3b1f…（64 位小写 hex）"
  },
  "notes": { "zh": "首个版本。", "en": "First release." }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `v` | int | ✅ | 整数 `1` |
| `id` | string | ✅ | 必须与注册表条目、清单逐字相等 |
| `version` | string | ✅ | 规范三段。必须与清单逐字相等，且与 tag `v<version>` 一致 |
| `minAppVersion` | string | ✅ | 规范三段。本机版本更低 → `app_too_old`。本机版本取 exe 的 FileVersion 的三段核心（`1.2.0-SNAPSHOT.42` → `1.2.0`）；读不出来就当作对一切都太旧，fail-closed |
| `releasedAt` | string | — | ISO 8601 UTC，仅供显示 |
| `package.name` | string | ✅ | 同 Release 里的资产名，不含路径，语法见 `spec-limits.md` §5（`gpkg.py` 写的是 `<id>-<version>.gpkg`，但规则只是语法） |
| `package.size` | int | ✅ | 非负字节数。**先看这个再下载**：超出上限即拒；下载时多一个字节即中止，提前结束也拒 |
| `package.sha256` | string | ✅ | 64 位小写 hex |
| `notes` | object | — | `{zh?, en?}`，每个可选、≤ 2000 个码点，升级提示里展示 |

`gpkg.py pack` 生成它（`json.dumps(indent=2)` 加一个结尾换行），**签的就是写盘的那串字节**，此后没有任何东西重新序列化它。

---

## 4. 验证顺序（四环）

```
 1. 根公钥（编译进 exe） 验 registry.json.sig           失败 → registry_bad_sig
 2. registry.seq >= 本地最大值                          失败 → registry_rollback
 3. 目标 id 在 plugins[] 且 status==listed 且没有被 "*" 吊销
                                                       失败 → plugin_not_listed / plugin_delisted / plugin_revoked
 4. 该条目的 devKey 验 ghost-plugin.json.sig             失败 → release_bad_sig
 4b. 发布描述的 version 没有被精确吊销                    失败 → plugin_revoked
 5. 发布描述的 id == 请求安装的 id                       失败 → release_id_mismatch
 6. 发布描述的 version >= 条目的 minVersion              失败 → plugin_below_min_version
 7. package.size 在上限之内（下载前就判）                 失败 → package_size_mismatch
 8. 下载 .gpkg，实际字节数 == package.size               失败 → package_size_mismatch
 9. sha256 == package.sha256                            失败 → package_hash_mismatch
10. 根 manifest.json 的 id/version 与本文档逐字相等      失败 → manifest_mismatch
11. minAppVersion <= 本机版本                            失败 → app_too_old
```

**第 3 步的「不在 `revoked[]`」拆成两半，这是一次刻意的读法，有测试钉着。** `"*"` 那一半只要 id，在第 3 步判；**精确版本**那一半要发布描述里的 `version`，而那个字符串只有在第 4 步验过签之后才算证据——所以它紧跟第 4 步（4b）。凡第 4 步通过的输入，这样得出的答案与「全在第 3 步判」相同；第 4 步不通过时，答的是 `release_bad_sig` 而不是 `plugin_revoked`——不让一份未经认证的文档挑选用户读到哪一条拒绝。

发布描述在**解析之前**先验签（第 4 步验的是刚下载的原始字节）；验过签才 `json::parse`，解析不了才是 `release_malformed`。注册表同理。

第 10 步取的清单是从内存里那份**已验过哈希**的包字节中按索引取出的（CRC 也核对），在解包之前；第 11 步也在解包之前。于是任一条失败都不会有一个字节写进插件目录。

第 5 步看起来多余（既然是按 id 去查的注册表），但它挡的是**内容主机返回了另一个插件的发布描述**——一个把两个 URL 弄混的 CDN，或一次刻意的替换。第 4 步只证明「这份描述由某个已上架开发者签发」，不证明「是我要的那一个」。

第 7 步在**下载之前**：`package.size` 是描述里的声明，先拿它和上限比，省掉一次把 800 MB 拉完才发现超限的下载。第 8 步才是「声明与实际是否一致」。

顺序是契约，理由见 [`spec-errors.md`](spec-errors.md) §1 末尾。

判定本体是 `src/modules/plugin/domain/trust_chain.h` 的 `EvaluateTrust`（**纯逻辑**）：收验签结果与已解析的结构体，返回第一条不满足的判据。开网络、读文件、调 CNG 都在它外面。

它在安装的每个阶段各调一次，**调用方点名阶段**（`TrustStage`），而不是让它从哪些指针恰好非空去猜：

| 阶段 | 需要的输入 | 判的步骤 |
|---|---|---|
| `Registry` | 注册表 | 1–3 |
| `Release` | + 发布描述（及其验签结果） | 1–7（含 4b） |
| `Downloaded` | + 实际大小与 SHA-256 | 1–9 |
| `Unpacked` | + 清单 | 1–11 |

某阶段自己的输入缺失、或出现了**更晚**阶段的输入（注册表阶段就有发布描述、发布阶段就有摘要……），一律 `internal_error`，在任何一步之前判——那是调用方的 bug，而「没人算哈希就跳过哈希检查」正是「缺了就跳过」这种规则会藏住的错误。只判本阶段的步骤，使每个阶段的答案都是完整答案的**前缀**：一个阶段永远不会答出一个在更多输入下会排在别的失败之后的码。这也是 `app_too_old`（第 11 步）要等到清单阶段的原因：`minAppVersion` 在发布阶段就已知，但提前判它，会抢在排序更高的 `package_hash_mismatch` 之前。

信任链之外，安装器在 `Release` 阶段通过后还做两件与信任无关的判定，都在下载之前：已装的正是这个版本（记录完好、目录在）→ 直接成功，什么都不下；已装版本**更高** → `installed_newer`（v1 不做降级）。

---

## 5. 开发者发版流程

```bash
# 一次性：生成密钥对，把公钥提交给官方上架
python tools/plugin/gpkg.py keygen --out %USERPROFILE%\.ghost-plugin-keys\dev
# → %USERPROFILE%\.ghost-plugin-keys\dev\dev-private.pem（自己保管，别提交）  %USERPROFILE%\.ghost-plugin-keys\dev\dev-public.b64（交给官方）

# 每次发版
python tools/plugin/gpkg.py pack  --src ./plugin --out dist/     # 校验清单 + 打 .gpkg（store、规范布局、无注释）
python tools/plugin/gpkg.py sign  --key %USERPROFILE%\.ghost-plugin-keys\dev\dev-private.pem --dist dist/
# → dist/<id>-<version>.gpkg  dist/ghost-plugin.json  dist/ghost-plugin.json.sig
python tools/plugin/gpkg.py verify --dist dist/ --pubkey %USERPROFILE%\.ghost-plugin-keys\dev\dev-public.b64   # 自检
gh release create v1.0.0 dist/* --repo <owner>/<name>
```

SDK 附一份 GitHub Actions 模板 [`examples/plugin-template/.github/workflows/release.yml`](../../examples/plugin-template/.github/workflows/release.yml)：`git tag v1.0.0 && git push origin v1.0.0` 即跑完上面全部步骤——tag 守门（`v<manifest 的 version>`）、按固定的 `SDK_REF` 取工具、私钥（repo secret `GHOST_DEV_KEY_PEM`）只在签名那一步写进 `$RUNNER_TEMP` 并随即删除、用提交的 `dev-public.b64` 复核、上传三个资产。逐条命令见 [`publish-walkthrough.md`](publish-walkthrough.md)。

注册表一侧对应的是 [`examples/registry-template/.github/workflows/publish.yml`](https://github.com/liliBestCoder/ghost-plugin-registry/blob/main/.github/workflows/publish.yml)：推到 `main` 的 `registry.json` + `.sig` 先过 `gpkg.py check-registry`（§2.2 的客户端解析规则）、再过 `gpkg.py verify-sig`（根**公钥**、原始字节），新 `seq` 必须严格大于已发布的最大 `seq-<N>`，然后发 Release `seq-<N>`（latest）。**它不持有任何 secret、从不签名**——根私钥离线，每一版注册表都在离线机器上签（维护者清单 `maintainer.md`，私有，不在本仓库）。

不想装 Python `cryptography` 时，签名这一步可以换成 `pwsh -NoProfile -File tools/plugin/sign.ps1 -Key %USERPROFILE%\.ghost-plugin-keys\dev\dev-private.pem -File dist/ghost-plugin.json`（PowerShell 7 / .NET，一次一个文件，产物与 `gpkg.py sign` 同形：原始字节、64 字节 `r‖s` 的 base64 加一个 `\n`；`-PublicKey` 打印 `X‖Y` 公钥；不带 `-Force` 不覆盖已有 `.sig`）。用法与保证见 [`README.md`](README.md)「不装 Python 也能签」。

`pack` 只需要标准库；`keygen` / `sign` / `verify` 需要 Python `cryptography`（没有就提示并以 2 退出）。`pack` 写出的 `.gpkg` 是 **store 方法、规范布局、无归档注释**（宿主只认这个形状，见 `spec-limits.md` §2），并在写盘前用宿主的规则检查一遍自己的产物；源目录里的链接、`*.pem` / `*.key` 以及内容含 `PRIVATE KEY` 的文件一律拒打（`.git` / `.hg` / `.svn` 跳过）。

**上架**：向官方仓库 `ghost-plugin-registry` 提 issue，附 `id`、`repo`、`dev-public.b64`。官方审过后把条目加进注册表并用根钥重签。**此后每次发版不再需要官方介入** —— 这正是三层链存在的理由。

## 6. 密钥丢了怎么办

开发者私钥泄漏或丢失 → 向官方申请**换钥**：官方把注册表里的 `devKey` 换成新公钥，并按需把受影响版本加进 `revoked[]`。旧钥签的旧版本从此验不过。

这是三层链相对「官方一把钥匙」的另一处收益：换一个开发者的钥不影响其他任何插件，而根钥泄漏才是全盘事件。根私钥离线保管，只在签注册表与签许可时使用。
