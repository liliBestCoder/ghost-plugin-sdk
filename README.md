# Ghost Proxifier Plugin SDK

[中文](#中文) · [English](#english)

---

## 中文

这是 Ghost Proxifier 插件中心的公开 SDK：打包、签名、校验插件的工具，以及插件开发规范。

- **工具**：[`tools/plugin/gpkg.py`](tools/plugin/gpkg.py)（Python，打包 / 签名 / 校验）与 [`tools/plugin/sign.ps1`](tools/plugin/sign.ps1)（PowerShell 7，不装 Python 也能签一个文件）。
- **规范**：[`docs/plugin-sdk/`](docs/plugin-sdk/README.md)，从 [`README.md`](docs/plugin-sdk/README.md) 读起；逐条命令的走查在 [`publish-walkthrough.md`](docs/plugin-sdk/publish-walkthrough.md)。
- **参考插件**：[`liliBestCoder/ghost-plugin-showcase`](https://github.com/liliBestCoder/ghost-plugin-showcase) —— v1 插件能做的每一件事各演示一遍，写插件从它抄起。
- **官方注册表**：[`liliBestCoder/ghost-plugin-registry`](https://github.com/liliBestCoder/ghost-plugin-registry) —— 申请上架在那里提 issue。

插件与注册表仓库的发版工作流按 `SDK_REPO: liliBestCoder/ghost-plugin-sdk` + `SDK_REF: <本仓库一次提交的 40 位 sha>` 取 `tools/plugin/`：替你签发布的工具不该在你不知情时变化，所以钉提交而不是分支。

> ⚠️ **规范描述的是 Ghost Proxifier 客户端，而客户端在一个私有仓库里。** 文档里出现的 `src/...`、`examples/...`、`docs/architecture/...`、`.agents/...`、`bin/...`、CTest 测试名、`tools/license/...` 以及 `maintainer.md`，都是指向那个私有仓库的**引用**，不是本仓库里的文件；指向它们的相对链接在这里打不开。规则本身以本仓库的文档为准，工具在两边的行为相同。

### 工具命令

```bash
# 开发者：一次性生成钥匙对（放在仓库之外；dev-private.pem 永不提交）
python tools/plugin/gpkg.py keygen --out ~/ghost-keys/

# 开发者：打包、签名、自检
python tools/plugin/gpkg.py pack   --src ./my-plugin --out ./dist/ [--min-app-version 1.2.0]   # --out 必须在 --src 之外
python tools/plugin/gpkg.py sign   --key ~/ghost-keys/dev-private.pem --dist ./dist/
python tools/plugin/gpkg.py verify --dist ./dist/ --pubkey dev-public.b64

# 注册表维护者：发布前的两道检查（只需要根公钥）
python tools/plugin/gpkg.py check-registry --file registry.json [--strict]
python tools/plugin/gpkg.py verify-sig --pubkey root-public.b64 --file registry.json [--sig registry.json.sig]

# 不装 Python：用 PowerShell 7 签一个文件（原始字节，产物与 gpkg.py sign 同形）
pwsh -NoProfile -File tools/plugin/sign.ps1 -Key <private.pem> -File <path> [-Out <path.sig>] [-Force]
pwsh -NoProfile -File tools/plugin/sign.ps1 -Key <private.pem> -PublicKey    # 打印 X‖Y 公钥的 base64
```

- `pack` 与 `check-registry` 只需要 Python 标准库；`keygen` / `sign` / `verify` / `verify-sig` 需要 `pip install cryptography`（Python ≥ 3.9）。
- 退出码：`0` 通过；`1` 检查失败，输出 `error: <原因码>: <原因>`，原因码见 [`spec-errors.md`](docs/plugin-sdk/spec-errors.md)；`2` 用法错误或缺 `cryptography`。
- **这些工具是便利，不是闸门**：每条规则都是客户端 C++ 实现的 Python 镜像，让你在自己机器上先发现问题。两边不一致时，以客户端为准。

### 规范目录

| 文档 | 内容 |
|---|---|
| [`README.md`](docs/plugin-sdk/README.md) | 一分钟版本、从零到上架、能拿到什么、会咬人的坑 |
| [`publish-walkthrough.md`](docs/plugin-sdk/publish-walkthrough.md) | 从建仓库到用户装上的逐条命令 |
| [`spec-manifest.md`](docs/plugin-sdk/spec-manifest.md) | `manifest.json` |
| [`spec-host-protocol.md`](docs/plugin-sdk/spec-host-protocol.md) | 与宿主的握手协议 |
| [`spec-plugin-api.md`](docs/plugin-sdk/spec-plugin-api.md) | 插件 token 与六个权限 |
| [`spec-release.md`](docs/plugin-sdk/spec-release.md) | 签名、Release 资产、上架与换钥 |
| [`spec-license.md`](docs/plugin-sdk/spec-license.md) | 付费插件的许可 |
| [`spec-store-bridge.md`](docs/plugin-sdk/spec-store-bridge.md) | 商店页协议 `ghost-store/1` |
| [`spec-errors.md`](docs/plugin-sdk/spec-errors.md) | 全部原因码 |
| [`spec-limits.md`](docs/plugin-sdk/spec-limits.md) | 全部上限、超时与语法 |

### 许可

尚未选定许可证；保留所有权利。如需使用，请联系维护者。

---

## English

The public SDK for the Ghost Proxifier plugin centre: the tools that pack, sign and verify a plugin, and the plugin specifications.

- **Tools**: [`tools/plugin/gpkg.py`](tools/plugin/gpkg.py) (Python: pack / sign / verify) and [`tools/plugin/sign.ps1`](tools/plugin/sign.ps1) (PowerShell 7: sign one file without Python).
- **Specifications**: [`docs/plugin-sdk/`](docs/plugin-sdk/README.md) — start at its [`README.md`](docs/plugin-sdk/README.md); the command-by-command walkthrough is [`publish-walkthrough.md`](docs/plugin-sdk/publish-walkthrough.md). The documents are written in Chinese.
- **Reference plugin**: [`liliBestCoder/ghost-plugin-showcase`](https://github.com/liliBestCoder/ghost-plugin-showcase) — every v1 capability, one section each. Start your plugin from it.
- **Official registry**: [`liliBestCoder/ghost-plugin-registry`](https://github.com/liliBestCoder/ghost-plugin-registry) — open an issue there to get listed.

The release workflows of plugin and registry repositories fetch `tools/plugin/` with `SDK_REPO: liliBestCoder/ghost-plugin-sdk` and `SDK_REF: <the 40-character sha of a commit of this repository>` — a tool that signs your releases should not change under you, so it is pinned to a commit, not a branch.

> ⚠️ **The specifications describe the Ghost Proxifier client, which lives in a private repository.** Paths such as `src/...`, `examples/...`, `docs/architecture/...`, `.agents/...`, `bin/...`, CTest test names, `tools/license/...` and `maintainer.md` are **references** into that private repository, not files in this one, and relative links to them do not resolve here. The rules themselves are as stated in these documents; the tools behave the same in both places.

### Tool commands

```bash
# Developer: make a key pair once (outside the repository; never commit dev-private.pem)
python tools/plugin/gpkg.py keygen --out ~/ghost-keys/

# Developer: pack, sign, self-check
python tools/plugin/gpkg.py pack   --src ./my-plugin --out ./dist/ [--min-app-version 1.2.0]   # --out must be outside --src
python tools/plugin/gpkg.py sign   --key ~/ghost-keys/dev-private.pem --dist ./dist/
python tools/plugin/gpkg.py verify --dist ./dist/ --pubkey dev-public.b64

# Registry maintainer: the two pre-publish checks (public root key only)
python tools/plugin/gpkg.py check-registry --file registry.json [--strict]
python tools/plugin/gpkg.py verify-sig --pubkey root-public.b64 --file registry.json [--sig registry.json.sig]

# Without Python: sign one file with PowerShell 7 (raw bytes; same output shape as gpkg.py sign)
pwsh -NoProfile -File tools/plugin/sign.ps1 -Key <private.pem> -File <path> [-Out <path.sig>] [-Force]
pwsh -NoProfile -File tools/plugin/sign.ps1 -Key <private.pem> -PublicKey    # prints base64 of the X||Y public key
```

- `pack` and `check-registry` need only the Python standard library; `keygen` / `sign` / `verify` / `verify-sig` need `pip install cryptography` (Python 3.9+).
- Exit codes: `0` ok; `1` a check failed, printed as `error: <reason code>: <why>` (codes in [`spec-errors.md`](docs/plugin-sdk/spec-errors.md)); `2` usage error or `cryptography` missing.
- **These tools are a convenience, not the gate**: every rule is a Python mirror of the client's C++ one, so you find problems on your own machine first. Where the two disagree, the client is right.

### License

No licence has been chosen yet; all rights reserved. Contact the maintainer before reusing this code.
