# 从零到上架：开发者逐条命令走查

[`README.md`](README.md) 的「从零到上架」是提纲，这里是照着敲就能走通的命令。例子是 SDK 的示例插件 `examples/plugin-events-viewer/`；换成你自己的插件，只改 id、名字与仓库。

前提：Python ≥ 3.9（`pack` 与 `check-registry` 只要标准库；`keygen` / `sign` / `verify` 要 `pip install cryptography`）、`git`、GitHub CLI `gh`。签名也可以只用 PowerShell 7 的 `tools/plugin/sign.ps1`。下文 `<sdk>` 指**公开 SDK 仓库**的一份检出——里面有 `tools/plugin/`（即 [`liliBestCoder/ghost-plugin-sdk`](https://github.com/liliBestCoder/ghost-plugin-sdk)）。

---

## 1. 建仓库

```bash
gh repo create yourname/my-plugin --public --template liliBestCoder/ghost-plugin-template --clone
cd my-plugin
```

模板仓库还没建好之前，等价的做法是把 `<sdk>/examples/plugin-template/` 的内容（含 `.github/` 与 `.gitignore`）拷进一个新仓库。

改 `manifest.json`：

- `id`：你自己的反向域名，至少三段、全小写（`io.github.yourname.my-plugin`）。**上架后不能改**——它是 `plugins\<id>\` 的目录名，也是注册表钉住开发者公钥的那一行的键。
- `version`：规范三段（`1.0.0`，不能有前导零）。
- `name` / `description`（`en` 必填，`zh` 可选）、`author`、`homepage`。
- 要界面就加 `"ui": {"embedded": true}`，并在握手回执里给 `uiUrl`；要读 Ghost 的数据就在 `permissions` 里写需要的那几个（[`spec-plugin-api.md`](spec-plugin-api.md) §2）。

先在本机检查清单与打包：

```bash
python <sdk>/tools/plugin/gpkg.py pack --src . --out ../dist
```

`--out` 必须在 `--src` 之外（否则包里会装着自己，`out_inside_src`）。失败时打印的是 [`spec-errors.md`](spec-errors.md) 里的码——和用户那边安装失败时看到的是同一个。

## 2. 独立运行一次

```bash
python main.py            # 没有 GHOST_PLUGIN_ID 就是独立模式：不读 stdin，打印 URL
```

示例插件还带自测：`python test_plugin.py`（测试扮演宿主与 Ghost 的控制接口，只用标准库）。

想在真 Ghost 里跑起来、又还没上架：用**开发者模式**从本地 `.gpkg` 安装（[`README.md`](README.md) 的「本地开发：开发者模式」）——不用签名，结构性检查与正式安装相同。

## 3. 密钥（一次性）

```bash
python <sdk>/tools/plugin/gpkg.py keygen --out ~/ghost-keys/
cp ~/ghost-keys/dev-public.b64 .
git add dev-public.b64 && git commit -m "the developer public key"
```

`dev-public.b64` 放在仓库根，也就是包目录里：它会随包发出去。它是**公钥**，放进包里无害（还能让用户自己核对签名）；私钥永远不在这里。

- `~/ghost-keys/dev-private.pem` 放在**仓库之外**。`.gitignore` 挡着 `*.pem`、`*.key`、`.keys/`，`pack` 见到私钥会以 `secret_in_package` 拒绝——两道兜底都不该用上。
- 把私钥的全部内容存成仓库 secret：

  ```bash
  gh secret set GHOST_DEV_KEY_PEM < ~/ghost-keys/dev-private.pem
  ```

- 然后把本地那份放到安全的地方（离线介质、密码管理器）。丢了私钥的后果见 [`spec-release.md`](spec-release.md) §6。

## 4. 申请上架（一次性）

在 [`ghost-plugin-registry`](https://github.com/liliBestCoder/ghost-plugin-registry) 提 issue，附 `id`、仓库 `owner/name`、`dev-public.b64` 的内容、免费还是付费。维护者离线签好新一版注册表发布之后，你的插件才出现在用户的插件页里。**这一步只做一次**，之后发版不需要任何人。

## 5. 发版

```bash
# manifest.json: "version": "1.0.0"
git commit -am "1.0.0"
git tag v1.0.0
git push origin HEAD v1.0.0
```

`.github/workflows/release.yml` 接手，按顺序：

1. 先守门：`SDK_REPO`/`SDK_REF` 还是模板占位符就失败——维护者公布公开 SDK 仓库与推荐提交后，把两者填进你的 `release.yml`；然后按 `SDK_REF`（40 位 sha）检出 `$SDK_REPO` 的 `tools/plugin`，按带哈希的 `.github/requirements-release.txt` 装 `cryptography`；
2. **tag 守门**：`v$(manifest.version)` 必须等于 tag，否则失败——Ghost 只从 `releases/download/v<version>/` 取包；
3. `gpkg.py pack --src plugin --out dist --min-app-version "$MIN_APP_VERSION"`；
4. 把 secret 写进 `$RUNNER_TEMP/dev-private.pem`（工作区之外），`gpkg.py sign`，立刻删除（`trap` 保证出错也删）；
5. `gpkg.py verify --pubkey dev-public.b64`：secret 与你提交的公钥对不上，在这里失败，而不是在每个用户那里以 `release_bad_sig` 失败；
6. `gh release create` 上传三个资产。

核对：

```bash
gh release view v1.0.0 --json assets --jq '.assets[].name'
# ghost-plugin.json
# ghost-plugin.json.sig
# io.github.yourname.my-plugin-1.0.0.gpkg
curl -sL https://github.com/yourname/my-plugin/releases/latest/download/ghost-plugin.json
```

手动发版（不用工作流）：

```bash
python <sdk>/tools/plugin/gpkg.py pack   --src . --out ../dist
python <sdk>/tools/plugin/gpkg.py sign   --key ~/ghost-keys/dev-private.pem --dist ../dist
python <sdk>/tools/plugin/gpkg.py verify --dist ../dist --pubkey dev-public.b64
gh release create v1.0.0 ../dist/ghost-plugin.json ../dist/ghost-plugin.json.sig ../dist/*.gpkg
```

## 6. 用户那边发生什么

用户打开插件页 → 商店里点安装 → 本地确认框（id、仓库、你的公钥指纹前 16 位）→ Ghost 取你 Release 里的发布描述、用注册表钉住的**你的**公钥验签、下载包、比对大小与 SHA-256、解包 → 装好但**未启用** → 用户启用时逐条确认你声明的权限 → 宿主拉起你的入口。

之后每次你发版，用户在打开插件页时（每插件每 24 小时至多一次）看到「可升级」；**从不自动安装**。新版本要的权限比旧版多时，升级后插件停用，用户重新确认。

## 7. 出错了看哪

| 现象 | 多半是 |
|---|---|
| `release_bad_sig` | secret 与注册表里登记的公钥不是一对；或发布描述在签名之后被改过（哪怕一个换行） |
| `release_unreachable` | tag 不是 `v<version>`，或 Release 没标成 latest，或资产名不对 |
| `package_hash_mismatch` / `package_size_mismatch` | 包在 `pack` 之后被重新打过，或上传了另一次 `pack` 的产物 |
| `plugin_below_min_version` | 注册表对这个插件设了 `minVersion`，你的版本低于它 |
| 启用后 `plugin_handshake_timeout` | 回执没 `flush`，或回执之前往 stdout 写了别的东西 |
| 停用时总被强杀 | 自己拼了停止事件名，而不是用握手里的 `stopEvent` |

原因码的完整定义在 [`spec-errors.md`](spec-errors.md)。
