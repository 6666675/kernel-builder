# CNB Kernel Builder

可独立托管的 AOSP GKI 构建脚本仓库。面板和用户的 `kernel_build` 仓库只负责传参与触发；CNB 拉取本仓库的固定提交后执行脚本。

```text
React / Go 面板
  → 用户组织/kernel_build（仅保留 .cnb.yml）
    → 拉取 kernel-builder 固定提交
    → 同步 GKI → 自定义脚本/文件覆盖 → 编译 → 重打包 → 上传附件
```

此目录不依赖上层项目的 Go、React 或文档文件，可整个复制出去作为新 Git 仓库。当前只准备本地源码，不包含真实远程仓库地址。

## 目录

| 路径 | 用途 |
| --- | --- |
| `Dockerfile` | Ubuntu 22.04 编译环境，固定 repo 与 AOSP mkbootimg 代码版本 |
| `.cnb.yml` | 脚本仓库自身的测试，以及手动发布环境镜像的流水线 |
| `scripts/entrypoint.sh` | 统一入口：预备提取、正式构建、校验、上传 |
| `scripts/sync_sources.sh` | 按 manifest 分支同步最新源码，复用 mirror 缓存 |
| `scripts/build_gki.sh` | 校验修改包、执行自定义脚本、应用文件覆盖、Kleaf/legacy 编译 |
| `scripts/repack_boot.sh`、`boot_image.py` | 替换基线内核并验证 ramdisk、cmdline 和其他 header 字段 |
| `scripts/file_overrides.py` | Makefile 预备包和修改包协议 |
| `scripts/cnb_artifacts.py` | 清理当前 commit 的旧 boot.img，上传并确认新附件 |
| `tools/render_cnb.py` | 生成用户 kernel_build 仓库的 `.cnb.yml` |
| `tools/make_overrides.py` | 从原始预备包和编辑目录制作修改包 |
| `tools/check.sh`、`tests/` | Shell 检查、模拟编译、文件包与镜像重打包测试 |

## 1. 建立独立远程仓库

将本目录复制到单独位置，然后在新目录中执行。把示例组织路径换成自己的真实地址：

```bash
git init -b main
git add .
git commit -m "Add standalone GKI builder"
git remote add origin https://cnb.cool/YOUR_GROUP/kernel-builder.git
git push -u origin main
```

脚本仓库的 `.cnb.yml` 在 push 时检查代码。首次使用前，在 CNB 页面手动触发 `web_trigger_image`，构建并发布环境镜像：

```text
docker.cnb.cool/YOUR_GROUP/kernel-builder:<脚本仓库完整提交SHA>
```

该镜像只提供编译依赖和固定版本工具；业务脚本仍从 Git 拉取。默认使用源码带来的 clang、Bazel 和 JDK，不另装一套版本不匹配的 Android 工具链。

## 2. 给用户 kernel_build 仓库接入流水线

在已独立提交的脚本仓库中运行：

```bash
BUILDER_SHA=$(git rev-parse HEAD)
python3 tools/render_cnb.py \
  --repo-url https://cnb.cool/YOUR_GROUP/kernel-builder.git \
  --ref "$BUILDER_SHA" \
  --image "docker.cnb.cool/YOUR_GROUP/kernel-builder:$BUILDER_SHA" \
  --output /path/to/kernel_build/.cnb.yml
```

生成器默认拒绝覆盖已有文件；有意更新时加 `--force`。把生成的 `.cnb.yml` 提交到用户 `kernel_build` 的 CI 分支，再在面板中选择该分支。**不需要把脚本、内核源码或 Dockerfile 复制到用户仓库。**

模板包含 `api_trigger_prepare`、`api_trigger_kernel`，与当前面板事件一致。拉取脚本时校验完整提交 SHA，预备和正式构建应使用同一 CI commit、脚本提交、`REQUEST_ID` 和源码参数。升级脚本时修改配置并重新提交，避免编辑期间脚本逻辑悄然变化。

默认脚本仓库应对流水线可读。私有脚本仓库可以在 CNB 的密钥导入配置中提供只读 `BUILDER_GIT_TOKEN`；模板通过临时 Git askpass 读取，不把令牌写进 URL、Git remote 或 `.cnb.yml`。附件上传使用平台注入的 `CNB_TOKEN`，不需要传递面板中的个人 Token。

## 3. 参数与入口

所有构建参数均沿用面板的 `env`，值为字符串。

| 参数 | 要求与含义 |
| --- | --- |
| `REQUEST_ID` | 必填，字母、数字和连字符组成，最长 128 字符 |
| `GKI_MANIFEST_URL` | 必填，不含查询参数或内嵌凭据的 HTTPS manifest 仓库地址 |
| `GKI_BRANCH` | 必填，例如 `common-android14-6.1` |
| `GKI_ARCH` | 必填，`arm64` 或 `x86_64` |
| `BOOT_IMG_URL` | 正式构建必填，配套基线 boot.img 的 HTTPS 地址 |
| `EDIT_FILES_JSON` | 预备阶段必填，例如 `["common/Makefile","common/scripts/Makefile.build"]` |
| `CUSTOM_SCRIPT_URL` | 可选，编译前执行的可信 Bash 脚本 HTTPS 地址 |
| `SOURCE_OVERRIDES_URL`、`SOURCE_OVERRIDES_SHA256` | 可选，修改包地址及其 64 位 SHA-256；同时提供 |
| `EXTRA_BAZEL_ARGS` | 可选，按空白拆分为参数数组，不进行 shell 展开；不包含独立的 `--` |
| `BAZEL_TARGET` | 可选，分支目标名不同时指定，例如 `//common:kernel_aarch64_dist` |
| `GKI_SYNC_JOBS`、`GKI_BUILD_JOBS` | 可选，默认同步 8 并发，编译使用可用 CPU 数 |
| `BOOT_PARTITION_SIZE` | 可选，设备 boot 分区容量，单位字节；提供后检查输出大小 |
| `ARTIFACT_TTL_DAYS` | 可选，附件保留天数，默认 30，范围 0–180；0 为永久 |

CNB 自动提供 `CNB_BUILD_WORKSPACE`、`CNB_COMMIT`、`CNB_REPO_SLUG`、`CNB_BUILD_ID` 和 `CNB_TOKEN`。本地调用用 `WORKSPACE` 和 `CI_COMMIT_SHA` 代替前两个变量。

统一入口：

```bash
bash scripts/entrypoint.sh validate-prepare  # 只校验参数，不同步源码
bash scripts/entrypoint.sh validate-kernel
bash scripts/entrypoint.sh prepare          # 同步并提取原文件
bash scripts/entrypoint.sh kernel           # 同步、编译并输出 artifact/boot.img
bash scripts/entrypoint.sh compile          # 分阶段调用：只同步、校验、应用修改和编译
bash scripts/entrypoint.sh repack           # 分阶段调用：替换并校验镜像
bash scripts/entrypoint.sh publish-prepare  # 上传预备包，需要 CNB 上下文
bash scripts/entrypoint.sh publish-kernel   # 上传本次已验证的 boot.img
```

CNB 模板按顺序调用 `compile → repack → publish-kernel`，分别设置 8 小时、1 小时、1 小时超时；预备事件调用 `prepare → publish-prepare`。面板继续通过原来的构建 API 触发即可。不会因为 push 用户配置仓库就自动执行大型内核构建。

## 4. Makefile 编辑流程

1. 面板选择 `api_trigger_prepare`，输入文件路径。成功后下载 `source-<REQUEST_ID>.tar.gz`。
2. 保留原始包，解压出编辑目录，只编辑其中 `files/` 下的文件，保持制表符和原有换行。
3. 使用原始包和编辑目录生成修改包：

```bash
python3 tools/make_overrides.py \
  --prepared source-example-123.tar.gz \
  --edited ./edited \
  --output ./overrides.tar.gz
```

4. 把修改包放到自己的 HTTPS 文件服务，将 URL 和工具输出的 SHA-256 填入面板；正式事件使用与预备阶段一致的任务 ID、源码参数及 CI 配置 SHA。

包结构为 `index.json`、`files/<源码相对路径>`；预备包另含 `manifest.xml`。仅接收 repo 管理项目中已跟踪的普通 `Makefile`、`Makefile.*`、`*.mk` 文件。包内目标、文件哈希、任务信息、路径和链接类型均会验证。原文变化时在自定义脚本前停止；全部通过后执行脚本，最后用编辑文件覆盖。修改包不能新增任意源码文件。

## 5. 构建、镜像与缓存

- 每次使用新的 `gki-src/` 工作树，拒绝复用非空源码目录；同步失败最多重试三次。节点本地 CoW 卷只保存 manifest/分支对应的 mirror 和 Bazel 缓存，不缓存用户修改后的源码。
- 检测 `tools/bazel` 后查询目标并编译，默认 `arm64 → kernel_aarch64_dist`、`x86_64 → kernel_x86_64_dist`；不存在时明确失败，可通过 `BAZEL_TARGET` 适配。没有 Bazel 入口时使用 `build/build.sh` 和对应 `build.config.gki.*`。两条路径均使用 thin LTO。
- 支持标准 boot header v0–v4，根据基线选择 arm64 原始 Image、gzip、LZ4 或 x86_64 bzImage。需要时从原始 arm64 Image 生成同格式压缩产物。
- 使用固定提交的 AOSP 工具重打包，再次解包比较内核、ramdisk、second、DTB/recovery DTBO、cmdline 及保留的 header 字段。输出不做 AVB 签名，移除旧 v4 boot signature 和原 AVB 尾部。验证结果写入 `artifact/build-metadata.json`。
- 模板为两个事件声明同一个仓库级 CNB 锁，覆盖同步、构建及上传；这不等同于面板端“下载保存后才释放”的持久化交付队列。当前面板仍只有页面会话内的并发限制。
- 只在新镜像验证成功后清理当前 CI commit 下名称恰为 `boot.img` 的旧附件，再获取上传地址、PUT 文件并确认。上传失败可能使该 commit 暂时没有 boot.img，不能把失败任务当成成功结果。

未提供分区容量时只验证镜像结构；设备启动、KMI 和模块兼容性需使用配套内核及基线另行验证。

## 6. 本地检查和打包

```bash
bash tools/check.sh
docker build --platform linux/amd64 -t gki-builder:local .
docker run --rm --platform linux/amd64 -v "$PWD:/workspace" -w /workspace gki-builder:local bash tools/check.sh
python3 tools/package_repository.py
```

本地没有 AOSP 工具时，镜像重打包测试会跳过；可把 `MKBOOTIMG_DIR` 指向 Dockerfile 中固定提交的官方 mkbootimg 仓库再运行。测试使用模拟源码和编译命令，不下载或编译整套 Android 内核。真实 CNB 的依赖镜像构建、附件上传和完整 GKI 编译需要部署后验证。

打包结果为 `dist/kernel-builder.tar.gz`，仅包含独立仓库源码，不包含 Git 历史、Token、缓存、下载源码及镜像产物。

参考：[CNB 语法](https://docs.cnb.cool/zh/build/grammar.html)、[内置环境变量](https://docs.cnb.cool/zh/build/build-in-env.html)、[附件 API](https://api.cnb.cool/swagger.json)、[Kleaf 构建](https://android.googlesource.com/kernel/build/+/refs/heads/main/kleaf/docs/kleaf.md)、[AOSP mkbootimg](https://android.googlesource.com/platform/system/tools/mkbootimg/+/d2bb0af5ba6d3198a3e99529c97eda1be0b5a093/)。
