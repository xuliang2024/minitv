# DSH 快速截图插件 · 实现方案

> ⚠️ **2026-10-14 更新：先看 [§14 开源生态调查](#14-开源生态调查2026-10-14-补充)。**
> 这个功能**已经被别人做出来了**，至少 7 个 DSH 插件直接对标 Appshots，其中两个是 macOS 原生「双 ⌘ 截当前窗口」实现。
> **结论：不要再从零写，优先"装 + 调 + 必要时二开"；§1–§13 从"从零实现方案"降级为"自研/二开时的设计参考"。**
>
> 目标：给 DeepSeek Harness（本机 Desktop 版，profile `desktop`）做一个"按两个键 → 当前窗口/屏幕截图 → 直接变成对话附件"的插件，对标 Codex 的 **Appshots**。
>
> 本文所有 API、路径、行为均来自本机实际产物核对：
> - 应用包：`/Applications/DeepSeek Harness.app/Contents/Resources/app.asar`（版本 `0.2.0-rc.2`，已解包到 `/tmp/dsh-asar/out`）
> - 运行中 profile：`~/.dsh/profiles/desktop/`（`cordis.yml` / `cordis.patch.yml` / `package.json`）
> - 运行中进程：主进程 `DeepSeek Harness`(pid 638) → 宿主 `DeepSeek Harness --expose-internals …/dsh-desktop-host/lib/cli.js`(pid 1030)

## 0. 先说清楚"Codex 那个插件"是什么

调研结论（含出处）：

| 项目 | 事实 |
|---|---|
| 功能名 | **Appshots**，是 Codex macOS 应用**内置功能，不是插件** |
| 触发 | **同时按两个 ⌘ 键**（Windows 上 ChatGPT 桌面版是同时按两个 Alt） |
| 抓取范围 | **只有最前面的那个窗口**（不是全屏、不是拖选区域），另外附带该窗口可获取的文本 |
| 落到哪 | 作为**普通附件**进入线程；默认新线程，60 秒内操作过某线程则进那个线程 |
| 权限 | macOS「屏幕与系统音频录制」+「辅助功能」 |
| 出处 | [Codex Appshots 文档镜像](https://raw.githubusercontent.com/crasuna/openai-dev-docs-cn-mirror/refs/heads/main/sources/en/codex/appshots.md)、[learn.chatgpt.com/docs/appshots](https://learn.chatgpt.com/docs/appshots) |

也就是说：**"Codex 那个截图插件"其实不是插件**。另外 OpenAI 确实有一个 `screenshot` skill（`~/.codex` 那套 curated skills），但它是"模型自己决定调用"的，**没有快捷键**。

所以本方案按你的真实诉求做：
- 键位最低门槛与 Appshots 一致 → **双键和弦**（DSH 快捷键系统原生支持双普通键和弦，见 §2）；
- 默认抓取范围对齐 Appshots → **当前窗口**；另外提供**全屏**与**拖选区域**两个变体（你说的"当前页面"和"当前屏幕"就是这两种）。

## 1. 目标行为（一句话验收）

装好后，在 DSH 窗口里：

1. 按 `⌘ + ⇧ + S`（可改）→ 抓**当前最前窗口** → 输入框附件栏立刻出现缩略图；
2. 按 `⌘ + ⇧ + A` → 抓**整个屏幕**；
3. 按 `⌘ + ⇧ + R` → 进入系统**拖选区域**模式（`Esc` 取消）；
4. 也可以点输入框工具栏的**相机按钮**用鼠标选这四种模式；
5. 截图**不自动发送**：留在草稿里，你可以补一句话再回车（可选开关改成"截完即发"）。

## 2. 技术前提（已核对，不是推测）

| # | 事实 | 依据 |
|---|---|---|
| P1 | DSH 是 Cordis 插件框架；插件包 = 一个 npm 包 + `cordis.patch.yml` 行 | `~/.dsh/profiles/desktop/cordis.yml`（190 行插件行） |
| P2 | 一个包可以**同时有宿主半边和浏览器半边**：`main` 走 Node，`dsh.client` 声明 `./client` 浏览器 bundle | `dsh-client-ui-open-in-app/package.json` 的 `dsh.client.inject/platform`，`dsh-client-modules` README |
| P3 | 客户端 bundle 格式是固定的 CJS 壳：`window.__ModuleLoader__.load({ id, factory: (require) => … })`，`exports.apply = apply; exports.inject = inject` | 任意 `dsh-client-ui-*/lib/client.js` 第 1–10 行 |
| P4 | 平台内置静态模块（`staticModules`）：`react`、`react/jsx-runtime`、`react-dom`、`react-dom/client`、`@deepseek-ai/cordis`、`dsh-client-store`、`dsh-client-ui-slots`、`dsh-client-ui-primitives`、`dsh-client-ui-dockkit`；其它依赖要写进 `dsh.client.external` | `dsh-web-frontend/dist/assets/index-*.js` 里的 `rM()` |
| P5 | 快捷键服务：`ctx.shortcuts.register({ id, label, defaults: { "desktop:macos": { code, modifiers } , … }, regions, modals, resolve })`，返回 disposer，必须在 `ctx.effect()` 里注册；`modifiers` 用 `primary` 表示 ⌘/Ctrl；**双键和弦**在 macOS/Windows 桌面端受支持 | `dsh-client-shortcuts/README.md` + `dsh-client-ui-open-in-app/lib/client.js:777` |
| P6 | 输入框工具栏插槽：`conversation.input.dock`、`conversation.composer.dock`、`conversation.input.activity` 等（共 60+ 个 slot） | 全量 `slots.inject("…")` 扫描结果 |
| P7 | 把图片变成"已就绪草稿附件"的**现成 API**：`ctx.conversation.createDrafts(sessionId, File[])` → `{id}[]`，再交给 composer shell 的 `addFiles(ids)` | `dsh-client-ui-conversation/lib/client.js:3477`、`18389` |
| P8 | 大字节回传有专用通道，不用挤 RPC：`POST /api/session/uploadFileBinary?sessionId=&name=`（`fileUploads.uploadStream`，走 `ctx.connection.fetch`），返回 `{receiptId, file}`；客户端 `ctx.fileUpload.upload(sessionId, ReadableStream\|Uint8Array, name, signal, onProgress)` | `dsh-client-file-upload/lib/index.js`、`lib/client.js:179` |
| P9 | 附件限制（默认值）：单图 20 MiB、单条消息 20 张 / 200 MiB、源图最长边 8192 px、总像素 64 Mpx；入库时统一归一化成 8-bit sRGB，超限自动降采样重编码到 1568 px 档 | `dsh-attachment-local/lib/index.js` 的 `LocalAttachmentStore.Config` |
| P10 | 宿主进程就是 Electron 可执行文件本身（`ELECTRON_RUN_AS_NODE=1`），因此子进程继承应用包标识 `com.deepseek.dsh` 的 **TCC 屏幕录制授权** | `ps` 输出 pid 1030；`runtime/cli/bin/dsh` 启动脚本 |
| P11 | 宿主可以无 shell 起原生进程，仓库自带该模式 | `dsh-native-command`（`runNativeCommand`）、`dsh-host-directory-picker-native` |
| P12 | 装插件有三条路：Web GUI「插件」页 / 会话内 `plugin_manager` 工具 / 应用自带 CLI `dsh plugin --profile desktop add <pkg>`（CLI 不接受 boot，但接受 pnpm 转发） | `dsh-plugin-manager/README.md`、实测 `dsh plugin --profile desktop --help` 输出 pnpm 11.7.0 |
| P13 | 版本兼容闸门只看 `peerDependencies` 里对 `@deepseek-ai/dsh` / `dsh-*` 的声明；**不声明就没有约束**，不需要 `compatibility.json` 豁免 | `dsh-app-boot/README.md`「Profiles」 |
| P14 | 宿主不需要"原生 IPC 桥"：直接 `spawn` 系统截图命令即可，**不用改 Electron、不用重签、不怕升级覆盖** | P10 + P11 |
| P15 | `screencapture` 在本机存在（`/usr/sbin/screencapture`），支持 `-x`（静音）`-i`（交互拖选）`-w`（窗口）`-R`（矩形）`-t png/jpg` | 实测 `screencapture` usage |

由此得到最重要的**架构选型**：

> **纯 DSH 插件实现，不碰 Electron 外壳。** 宿主半边 spawn 平台原生截图命令，把 PNG/JPEG 字节通过既有上传路由交给客户端，客户端组装 `File` 塞进 composer 草稿附件。全程复用 DSH 现成的附件管线（校验、归一化、持久化、历史回放、多模态发给模型）。

三条被否掉的路线（附理由）：

| 路线 | 否掉的原因 |
|---|---|
| 改 `app.asar` 里的 `lib/main.js` + `preload-app.cjs`，加 `desktopCapturer` IPC 通道 | 破坏应用签名；每次升级被覆盖；且对"抓整个屏幕"并不比 `screencapture` 更好 |
| 浏览器半边直接用 `navigator.mediaDevices.getDisplayMedia()` | 每次调用都会弹系统/Electron 选择器，做不到"按两个键就出图"；Electron 下还需要 `setDisplayMediaRequestHandler` |
| 让模型自己跑 bash `screencapture` 再 `read` 图片 | 可用但不是快捷键体验，要等一个回合；作为**兜底方案 B** 保留（§9） |

## 3. 架构

```
┌─────────────────────────── 浏览器半边（Web GUI 页面） ───────────────────────────┐
│ quick-screenshot/client.js                                                       │
│  ├─ ctx.shortcuts.register ×4  ⌘⇧S 窗口 / ⌘⇧A 全屏 / ⌘⇧R 区域 / ⌘⇧X 多屏          │
│  ├─ slots.inject("conversation.input.dock") → 相机按钮 + 模式气泡                 │
│  ├─ 拿 sessionId（ctx.sessions）+ File(list) + createDrafts + shell.addFiles      │
│  └─ 状态：capturing / ready / error → toast + 按钮禁用防连点                       │
└───────────────▲──────────────────────────────────────────────┬──────────────────┘
                │ ctx.remote.screenshot.capture({mode,name})    │ GET /api/session/uploadFileBinary
                │ ← {path, bytes, mediaType, screenshotId}      │ (POST 原始字节)
┌───────────────┴──────────────────────────────────────────────┴──────────────────┐
│ 宿主半边（Node，跑在 Electron 宿主进程里）                                        │
│ quick-screenshot/lib/index.js                                                    │
│  ├─ class ScreenshotService extends TypertRemoteService，@Remote("capture")      │
│  │    └─ 串行队列（同一时刻只允许一次截图）+ AbortSignal + 超时                     │
│  ├─ 平台适配器                                                                   │
│  │    darwin : screencapture -x [-o -w | -R x,y,w,h | -D n] -t png|jpg <tmp>     │
│  │    win32  : powershell -NoProfile -Command <System.Drawing 抓屏脚本>           │
│  │    linux  : gnome-screenshot -f / spectacle -b -n -o / grim / scrot / import  │
│  └─ 只写 DSH 缓存目录 sandbox：$DSH_HOME/cache/screenshots/<ts>-<uuid>.<ext>      │
└──────────────────────────────────────────────────────────────────────────────────┘
                                  │ 复用
                                  ▼
        dsh-client-file-upload（上传路由） + dsh-attachment-local（校验/归一化/持久化）
                                  │
                                  ▼
        session 事件：user/message 带 image block → 模型直接看图（多模态）
```

关键设计点：

1. **双半边的唯一耦合面是 `ctx.remote.screenshot.capture`（Typert Remote）+ 现成的上传路由**，不新增自定义 HTTP 端点，不需要 `dsh.client.external`（只依赖内置模块）。
2. **字节不走 RPC**（避免超大 base64 帧），RPC 只回传一个很小的 JSON：`{path, bytes, mediaType, name}`；页面再用 `fetch(path)` 流式 POST 到上传路由。宿主写盘留档也顺便给你一个"这图在哪"的路径。
3. **截图动作全程在宿主侧串行化**，`screencapture -i` 这种阻塞式交互不会卡住页面（子进程 + 可取消）。
4. **不做自动发送**：截图进草稿，人工决定何时发。这样"截图 → 补一句话 → 回车"最自然，也避免误触把屏幕内容直接丢给模型。

## 4. 文件清单

插件仓库（建议放 `~/codes/dsh-quick-screenshot`，可 `pnpm link` 或直接 `file:` 安装）：

```
dsh-plugin-quick-screenshot/
├─ package.json            # name/version/dsh.client/dsh.bundle/exports/peerDependencies
├─ cordis.patch.yml        # 本 bundle 的宿主行 + 客户端行（见下）
├─ lib/
│  ├─ index.js             # 宿主半边：ScreenshotService + 平台适配器 + schemastery Config
│  ├─ client.js            # 浏览器半边：必须手写成 __ModuleLoader__ 壳（P3/P4）
│  └─ platform/
│     ├─ darwin.js         # screencapture 参数拼装（纯函数，可单测）
│     ├─ win32.js          # PowerShell 脚本字符串（纯函数）
│     └─ linux.js          # 后端探测顺序（纯函数）
└─ test/
   ├─ platform.spec.js     # 参数矩阵：mode × platform × format
   └─ service.spec.js      # 串行化、超时、取消、错误码映射（注入假 runner）
```

### 4.1 `package.json`

```json
{
  "name": "dsh-plugin-quick-screenshot",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "main": "lib/index.js",
  "exports": {
    ".": { "default": "./lib/index.js" },
    "./client": { "default": "./lib/client.js" },
    "./package.json": "./package.json"
  },
  "dsh": {
    "bundle": { "patch": "./cordis.patch.yml" },
    "client": {
      "platform": "web",
      "inject": [
        "@deepseek-ai/dsh-api-remotes",
        "@deepseek-ai/dsh-client-file-upload",
        "@deepseek-ai/dsh-client-locale",
        "@deepseek-ai/dsh-client-shortcuts",
        "@deepseek-ai/dsh-client-ui-conversation",
        "@deepseek-ai/dsh-client-ui-primitives",
        "@deepseek-ai/dsh-tool-subagent"
      ]
    }
  },
  "peerDependencies": { "@deepseek-ai/cordis": "~4.0.4" },
  "devDependencies": { "@deepseek-ai/cordis": "4.0.4", "@types/react": "~18.3.1", "react": "^18.2.0" }
}
```

要点：
- **不要**声明任何 `@deepseek-ai/dsh` / `dsh-*` 的 `peerDependencies` → 版本闸门（P13）直接放行，不需要豁免文件。
- `dsh.bundle.patch` 让这个包成为"可选 bundle"，装完可在 GUI 插件页一键开关。
- 客户端 `inject` 里的都是**已存在**的客户端插件 id（用来保证加载顺序），无需 `dsh.client.external`。

### 4.2 `cordis.patch.yml`

```yaml
- insert:
    - id: quick-screenshot
      name: 'dsh-plugin-quick-screenshot'
      config:
        format: png          # png | jpeg
        jpegQuality: 85
        maxBytes: 12582912   # 12 MiB，超过就降质量/降分辨率
        captureTimeoutMs: 20000
        windowScreenshot: true

    - id: ui-quick-screenshot
      name: 'dsh-plugin-quick-screenshot/client'
```

（`insert` 行格式与 `dsh-base/cordis.patch.yml`、`dsh-web-app/cordis.patch.yml` 完全一致。）

### 4.3 安装（三条路，任选）

```bash
# A. 应用自带 CLI（实测可用：它把参数转发给 profile 目录里的 pnpm 11.7.0）
"/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh" \
  plugin --profile desktop add /Users/m007/codes/dsh-quick-screenshot

# B. GUI：左侧「插件」页 → 安装 → 选本地目录或包名（勾选启用 bundle）

# C. 会话内（Creator 模式的 plugin_manager 工具）
#    install_bundle(spec="/Users/m007/codes/dsh-quick-screenshot", enabled=true)
```

装完后：profile 的 `package.json.dsh.profile.bundles` 多一项，`cordis.patch.yml` 多上面的 insert；`dsh-hmr` 已启用会**热重载**，没有热重载则重启应用。

## 5. 宿主半边实现要点（`lib/index.js`）

```js
import { Remote, TypertRemoteService } from "@deepseek-ai/dsh-typert-protocol";
import { runNativeCommand } from "@deepseek-ai/dsh-native-command";
import { mkdir, rm, stat } from "node:fs/promises";
import { join } from "node:path";

/** 四种取图模式，沿用 Appshots 的语义：window 优先。 */
const MODES = ["window", "fullscreen", "region", "multiDisplay"];

class ScreenshotService extends TypertRemoteService {
  static inject = ["subprocess"];          // 与 dsh-host-open-in-app / -native 同款依赖

  #chain = Promise.resolve();              // 串行化：同一时刻只跑一次截图
  #inflight;                               // 供 cancel() 用

  @Remote("capture")
  async capture(request, signal) {         // request: { mode, display?, name? }
    // 1) 校验 mode；2) 排队；3) 组参数 → runNativeCommand（无 shell、可 abort、超时）
    // 4) stat 校验产物（存在、非 0 字节）——macOS 权限被拒时会得到 0 字节或直接报错
    // 5) 返回 { path, bytes, mediaType, screenshotId }
  }
}
export { ScreenshotService, Config };
export default ScreenshotService;
```

平台参数矩阵（纯函数，先写测试）：

| mode | darwin | win32 | linux |
|---|---|---|---|
| `window` | `screencapture -x -o -w <out>`（`-w` 光标变相机，点哪个窗口抓哪个；亦可 `-l <windowid>` 免交互） | PowerShell `[System.Windows.Forms.Screen]::FromHandle(GetForegroundWindow)` + `CopyFromScreen` | `gnome-screenshot -w -f` / `spectacle -a -b -n -o`（窗口后端不可用则降级全屏并标注） |
| `fullscreen` | `screencapture -x <out>` | `Screen.PrimaryScreen.Bounds` | `gnome-screenshot -f` / `grim` / `scrot` / `import -window root` |
| `region` | `screencapture -x -i <out>`（`Esc` 取消） | PowerShell 抓虚拟屏后由客户端裁剪，或先做全屏再让用户裁 | `gnome-screenshot -a -f` / `spectacle -r -b -n -o` / `slurp \| grim -g -` |
| `multiDisplay` | `screencapture -x -D <n> <out>` | `Screen.AllScreens[i].Bounds` | 同上 + `--display` |

工程约束：
1. **无 shell 起进程**（复用 `dsh-native-command`），杜绝注入面；参数全量白名单化。
2. **超时**：`captureTimeoutMs` 到点杀进程树；`region` 模式给更长（等人操作）。
3. **退出码 → 稳定错误码**：`USER_CANCELLED`（Esc/取消）、`PERMISSION_DENIED`（TCC 拒绝）、`NO_DISPLAY`、`TOOL_MISSING`（Linux 没装后端）、`TIMEOUT`、`UNSUPPORTED_MODE`。
4. **产物校验**：文件存在 + 字节数 > 0，否则一律 `PERMISSION_DENIED` 并附上"打开 系统设置 → 隐私与安全性 → 屏幕与系统音频录制"的指引文案。
5. **落盘位置**：`$DSH_HOME/cache/screenshots/`，启动时不清理，保留最近 N 张（配置 `keepLast`，默认 50）——既是调试证据，也方便你之后手动拖进对话。

## 6. 浏览器半边实现要点（`lib/client.js`）

```js
window.__ModuleLoader__.load({
  id: "dsh-plugin-quick-screenshot",
  factory: (require) => {
    const react = require("react");
    var module = { exports: {} }; var exports = module.exports;

    /** 依赖的客户端服务：槽位、会话、会话编排、文件上传、快捷键、远程网关。 */
    const inject = ["slots", "sessions", "conversation", "fileUpload", "shortcuts", "remote"];

    async function capture(ctx, mode) {
      // 1) sessionId：当前 Session 的 Agent 作用域（含 subagent 拒绝上传的短路）
      // 2) const r = await ctx.remote.screenshot.capture({ mode }); 失败 → 稳定错误码 → i18n toast
      // 3) const bytes = await (await fetch(r.path)).arrayBuffer();   // 宿主同机，localhost
      // 4) const file = new File([bytes], r.name, { type: r.mediaType });
      // 5) const [draft] = ctx.conversation.createDrafts(sessionId, [file]);
      // 6) shell.addFiles([], [draft.id])  ← 复用 composer 的 drop 处理语义
      // 7) 可选 autoSend：ctx.conversation.send(sessionId, "") —— 默认关闭
    }

    function apply(ctx) {
      // ① 四个快捷键（均在 ctx.effect 内注册，卸载即摘除）
      ctx.effect(() => ctx.shortcuts.register({
        id: "quickScreenshot.window", label: () => t("shot.window"),
        defaults: {
          "desktop:macos":  { code: "KeyS", modifiers: ["primary", "shift"] },
          "desktop:windows":{ code: "KeyS", modifiers: ["primary", "shift"] },
          "desktop:linux":  { code: "KeyS", modifiers: ["primary", "shift"] },
          "web:macos":      { code: "KeyS", modifiers: ["primary", "shift"] }
        },
        regions: ["page", "editable"], modals: [],
        resolve: () => target() === undefined
          ? { status: "blocked", reason: t("shot.noSession") }
          : { status: "handled", run: () => capture(ctx, "window").catch(report) }
      }), "quick-screenshot: window command");
      // ② …fullscreen(⌘⇧A) / region(⌘⇧R) / multiDisplay(⌘⇧X) 同构
      // ③ 工具栏按钮：ctx.slots.inject("conversation.input.dock", () => ctx.slots.register(
      //      { name: "conversation.input.dock" }, ScreenshotButton))
      // ④ 防连点：模块级 store 记录 phase = idle|capturing|error
    }

    exports.apply = apply; exports.inject = inject;
    return module.exports;
  }
});
```

要点：
1. **快捷键冲突已排查**：现有默认键位只用到 `KeyB/KeyT/KeyP/KeyO/Comma/Backquote` + `Mod`/`Mod+Alt`/`Mod+Shift` 组合；`KeyS`、`KeyA`、`KeyR`、`KeyX` 未被占用，`Mod+Shift+S/A/R/X` 与系统（⌘⇧3/4/5 是 macOS 自带截图，⌘⇧S 是"存储为"）不冲突，可安全作为默认值。用户在 设置 → 快捷键盘 可随时改。
2. **`resolve()` 必须同步返回**，权限/目标校验放在 `run()` 里异步做（与 `dsh-client-ui-open-in-app` 一致）。
3. **`regions: ["page", "editable"]`**：输入框里也能按（截图常发生在"打字打一半想贴图"），`modals: []` 表示弹窗时也不拦。
4. **locale**：用 `ctx.locale.register(NS, {zh, en})` 注册中英文案，跟现有插件同款。
5. **不做截图历史面板（v1）**：先只做"抓完进草稿"。

## 7. 配置项（`Config` = schemastery，映射到宿主半边）

| 字段 | 默认 | 含义 |
|---|---|---|
| `format` | `png` | `png` 无损；`jpeg` 体积更小、同样进图片管线 |
| `jpegQuality` | `85` | 仅 `format: jpeg` |
| `maxBytes` | `12582912` | 超过就自动降质量/降分辨率（复用附件归一化档位思路） |
| `captureTimeoutMs` | `20000` | 单次截图超时；`region` 模式建议 ×6 |
| `windowScreenshot` | `true` | 关掉后只留全屏/区域 |
| `keepLast` | `50` | 缓存目录保留最近 N 张 |

## 8. 隐私、权限与安全

1. **TCC 屏幕录制授权**：宿主进程身份是 `com.deepseek.dsh`（P10），第一次调用 `screencapture` 时 macOS 会弹授权；**必须在"系统设置 → 隐私与安全性 → 屏幕与系统音频录制"里给 DeepSeek Harness 打勾，然后重启应用**。授权被拒时 `screencapture` 会产出 0 字节/纯桌面壁纸，插件必须把它识别成 `PERMISSION_DENIED` 并给可操作的指引——这是最容易踩的坑，列为 T0 验收项。
2. **权限模式**：截图不写工作区、不联网、不改用户文件，只写 `$DSH_HOME/cache`。即使当前会话是 `read-only` 预设也应可用；不引入任何"请求提权"路径。
3. **字节不外泄**：截图仅存在本机 DSH 附件库里，只有你把它发送给模型时才进入请求（和手动贴图完全同路径）。
4. **不静默发送**：默认不自动发（§3 设计点 4）。
5. **无 shell 注入面**：所有平台命令用参数数组直接 spawn，`mode`/`display` 走白名单与整数校验。

## 9. 备选方案（Plan B/C，用于 Web 部署或权限不可用时）

| 方案 | 形态 | 代价 |
|---|---|---|
| **B. 模型侧 skill** | 写一个 DSH skill（`dsh-skill-filesystem` 已挂载）：让模型用 `bash` 跑 `screencapture -x /tmp/x.png` 再 `read_image` 看图 | 不是快捷键；一次往返几秒；受沙箱策略影响。**可作为 v1 的"零插件"版先落一版** |
| **C. `getDisplayMedia` 版客户端插件** | 纯浏览器半边，`navigator.mediaDevices.getDisplayMedia()` → `ImageCapture`/`canvas` → `File` → 同一条 `createDrafts` 路径 | 每次弹选择器，做不到"按两个键就出图"；但**不需要宿主半边**，可给 Web 版兜底 |
| **D. 原生 IPC 版** | 改 `app.asar`：`main.js` 加 `ipcMain.handle("dsh-desktop:screenshot")`（`desktopCapturer` + `nativeImage`），`preload-app.cjs` 暴露 `dshDesktop.captureScreenshot()` | 抓窗口零权限弹窗、最快；但**破坏签名、升级即失效**，仅在 A 遇到硬阻塞时启用 |

## 10. 验证与测试

**静态/装载层**
```bash
APP="/Applications/DeepSeek Harness.app/Contents/Resources"
"$APP/runtime/cli/bin/dsh" --profile desktop --dump-config | grep -n quick-screenshot   # 行已进合成配置
"$APP/runtime/cli/bin/dsh" plugin --profile desktop ls --depth 0                        # 包已装进 desktop profile
```
- 抓 `~/.dsh/profiles/desktop/cordis.patch.yml` 的备份（改动前），回滚只需删掉 insert 行。
- 先在**临时 profile** 里验证装载：`dsh plugin --profile shotdev add …` + `dsh shotdev --dump-config`，避免把没验证过的插件直接插进桌面版（桌面版启动失败要 `sanitizeProfile` 恢复）。

**端到端（人工）**
1. 应用启动 → 左侧「插件」页出现 `quick-screenshot` 且为启用；Web 控制台无 boot audit 失败。
2. 设置 → 快捷键盘 里能看到四条命令（窗口/全屏/区域/多屏），改键即时生效。
3. 首次按 `⌘⇧S`：若弹系统权限 → 授权 → 重启应用 → 再按，输入框出现缩略图，**且缩略图内容正确**（不是黑图/壁纸）。
4. `⌘⇧R` 拖选一块 → 缩略图尺寸等于框选尺寸；`Esc` → 静默取消 + 一次"已取消"提示。
5. 连续快速按两次：第二次被拒（`capturing` 状态）而不是起两个 `screencapture`。
6. 补一句"这个报错怎么修" → 回车 → 会话里出现图片附件；模型回答能引用图里内容（多模态链路验证）。
7. 会话重启/切 Session 后历史里的截图仍在（`dsh-attachment-local` 持久化，P9）。
8. 回归：确认 `⌘⇧S/A/R/X` 没有覆盖任何已有默认键位（对照 §6 要点 1 的清单）。

**失败路径**
- 权限拒绝（在系统设置里取消勾选）→ 提示 `PERMISSION_DENIED` 文案 + 引导路径，不出现空白附件。
- 宿主无显示（SSH/headless）→ `NO_DISPLAY`。
- Linux 无任何后端 → `TOOL_MISSING` 并列出安装建议。

## 11. 风险表

| 风险 | 概率 | 影响 | 处置 |
|---|---|---|---|
| TCC 屏幕录制未授权 → 黑图/空图 | 高 | 高 | 首启自检：抓一张探针图并校验非空；未授权时在设置页给红色提示，而不是静默失败 |
| macOS 升级/换机后 TCC 记录失效 | 中 | 中 | 同上自检 + 明确文案；`screencapture` 退出码与字节数双重判定 |
| 桌面版是应用自有 profile，第三方 bundle 装载失败会拖累启动 | 中 | 高 | 先在临时 profile 验证；插件不声明 dsh-* peer；失败用 `sanitizeProfile` 恢复（`cordis.patch.yml` → `.bak-<ts>`） |
| 键位与用户自定义/系统冲突 | 中 | 低 | 默认键位经排查；冲突时注册会报错而非静默；提供设置页改键 |
| 大图传输/超限 | 低 | 中 | 默认 PNG→必要时 JPEG；`maxBytes` 前置压缩；上限对照 P9（20 MiB/8192 px/64 Mpx）留足余量 |
| 应用升级后插件被跳过（bundle 兼容检查） | 低 | 中 | 无 dsh-* peer 声明 → 不触发版本闸门；升级后跑一次 §10 静态检查 |
| `window` 模式在 Electron 里点到 DSH 自己 → 抓到自窗口（递归感） | 中 | 低 | 预期行为（Appshots 也如此）；在提示里说明，或配置 `windowScreenshot:false` 只留全屏/区域 |

## 12. 分期落地

| 阶段 | 交付 | 预估 |
|---|---|---|
| **P0** | 先落 **Plan B（skill 版）**：一个 skill 教会模型用 `screencapture` + `read_image`，今天就能"截图给模型看" | 0.5 天 |
| **P1** | 插件骨架：`package.json` + `cordis.patch.yml` + 空 apply；临时 profile 装载通过；GUI 插件页可见 | 0.5 天 |
| **P2** | 宿主半边：darwin 适配器 + `capture` Remote + 串行化/超时/错误码 + 单测 | 1 天 |
| **P3** | 浏览器半边：快捷键 ×4 + 相机按钮 + `createDrafts` 接线 + i18n + 状态与 toast | 1 天 |
| **P4** | win32/linux 适配器 + 失败路径 + §10 全量验收 + 简短使用文档 | 1 天 |
| **P5**（可选） | 截图历史面板、自动发送开关、"抓哪个窗口/显示器"选择器、把窗口文本一起带进上下文（对齐 Appshots 的文本能力） | 2 天 |

**建议先做 P1+P2+P3（2.5 天）拿到端到端可用版本**，P0 可以作为今天的临时方案。

## 13. 待确认（需要你拍板）

1. 默认键位：按方案给 `⌘⇧S`(窗口) / `⌘⇧A`(全屏) / `⌘⇧R`(区域)，还是学 Appshots 用**双键和弦**（如 `F S` 同时按）？
2. 截图**抓完是否自动发送**（默认否），还是截完即发？
3. 是否接受"默认抓最前窗口"（可能抓到别的 App）——还是默认抓**整个屏幕**、窗口作为次要？
4. 插件放哪：`~/codes/dsh-quick-screenshot` 独立仓库，还是放进当前 lite_tv 仓库的 `plugins/` 目录？（与 lite_tv 业务无关，建议独立放）

---

## 14. 开源生态调查（2026-10-14 补充）

调查方法：插件市场公开 API（`https://api.deepseek1024.com/v1/plugins/search?q=…`）+ GitHub API + npm registry + **实际下载 npm tarball 核对包内 API**。
市场收录规模：`awesome-deepseek-harness-plugins`（[仓库](https://github.com/imsai-sh/awesome-deepseek-harness-plugins)，278★）自称收录 **13775** 个插件；`screenshot` 关键词命中 68 个、`appshot` 命中 2 个、`截图` 命中 67 个。

### 14.1 直接对标 Appshots 的实现（第一梯队）

| 项目 | 平台 | 触发 | 安装 | 状态（2026-10-14 核对） |
|---|---|---|---|---|
| **[TaurusWood/dsh-plugin-appshot](https://github.com/TaurusWood/dsh-plugin-appshot)** | macOS 14+ arm64 / Win10 x64 | 全局**左⌘+右⌘**（Win 双 Ctrl），可在设置面板改双击 ⌘ 等 | npm **`dsh-plugin-appshot@0.4.2`**，MIT | 5★/1 fork，**最后推送 09-28**，42 MB 预构建（内含 Swift `Appshot Agent.app`，ScreenCaptureKit） |
| **[WongYuYe/dsh-appshots](https://github.com/WongYuYe/dsh-appshots)** | macOS / Windows | 全局**双 ⌘**（Win 双 Ctrl）+ 输入框相机按钮 | npm **`dsh-appshots@0.1.8`**，MIT | 7★，**最后推送 09-18**；含 arm64 Mach-O `bin/appshot-capture`（168 KB，ScreenCaptureKit + 辅助功能读窗口文字） |
| **[paicat1/dsh-screenshot](https://github.com/paicat1/dsh-screenshot)** | Windows 优先（浏览器热键跨平台可用） | `Ctrl+Alt+S` 交互式（点桌面=全屏 / 悬停窗口=吸附截该窗 / 拖拽=框选）、`Ctrl+Shift+Alt+S` 全屏 | npm **`@paicat1/dsh-screenshot`**，MIT | 5★，09-08；**纯 PowerShell 零二进制**；"传路径不传图"设计 |
| [lsnnn123/dsh-screenshot-win](https://github.com/lsnnn123/dsh-screenshot-win) | Windows | 输入框相机按钮（无全局热键） | 本地目录安装 | MIT，10-08；**完整度最高**：`lib/`(26 KB) + `client/`(24 KB) + 5 个 PowerShell 采集脚本 + 3 套测试 + `tools/check-patch.mjs` |
| [alain-prot0s5/dsh-screenshot](https://github.com/alain-prot0s5/dsh-screenshot) | Windows 10/11 | 相机按钮 + 全局热键 `Alt+A` | — | 2★，08-24 |
| [ai-yucheng/dsh-composer-image-tools](https://github.com/ai-yucheng/dsh-composer-image-tools) | 跨平台（Electron） | 输入框图片工具（上传 + 区域截图） | — | 1★，08-21；**用 Electron `desktopCapturer` + 零依赖纯客户端注入草稿图片轨**（对应本文 Plan D 路线） |
| [Max-Null/dsh-capture](https://github.com/Max-Null/dsh-capture) | — | 双引擎截图，支持框选 + 红框标注 | — | 2★，09-28 |
| [XEsx630/dsh-screenshot](https://github.com/XEsx630/dsh-screenshot) | — | 截屏 + 选区，图片附加到消息 | — | 1★，08-16 |

### 14.2 兼容性核对（关键：本机运行时是 `0.2.0-rc.2`）

我把两个 appshot 的 npm tarball 下载后逐一比对其用到的 API 与当前运行时：

**TaurusWood/dsh-plugin-appshot 0.4.2 —— API 面全部对得上：**

| 插件调用 | 本机 0.2.0-rc.2 | 结论 |
|---|---|---|
| `conversation.createDrafts(sessionId, [File])` | ✅ `dsh-client-ui-conversation/lib/client.js:3477` | 与本文 §2-P7 同一 API |
| `conversation.resolveDraftAttachments` / `releaseDraftAttachment` | ✅ | |
| `ctx.sessions.scope(id)` + `conversation.input.for(scope)` | ✅ 两处都在（如 `dsh-client-ui-chat:12431`） | |
| `ctx.attachments.saveImage` / `readImage` | ✅ `dsh-attachment/lib/types/index.js`（`saveImages`/`saveImage`/`readImage` 均在） | |
| `ctx.webServer.register` / `registerUpgrade` | ✅ `dsh-host-open-in-app` 同款用法 | |
| `ctx.settings` | ✅ 服务存在（`dsh-settings/lib/index.js:330`） | 且插件是**防御式** `if (settings) { try {…} }`，拿不到也不炸 |
| peerDependencies | **无任何 peer 声明** | 版本闸门（§2-P13）直接放行，不需 `compatibility.json` 豁免 |

→ **判定：0.4.2 可以在本机 DSH 0.2.0-rc.2 上跑**。唯一的版本注记是 CHANGELOG 里 `0.4.2` 的标题为 "mount screenshots on Harness **0.1.7** composer APIs"，即它针对 0.1.7 的 composer API 对齐过，而 0.2.0-rc.2 保留了同一组 API。README 里"DSH 0.1.7"是写作时的版本，不是硬约束。

> ⚠️ **上面这条"API 面对得上"的结论只在浏览器半边成立。实装后宿主半边加载失败，真正的阻塞项在 §15。**

**WongYuYe/dsh-appshots 0.1.8 —— 有一处真实的不兼容：**

| 插件调用 | 本机 0.2.0-rc.2 | 结论 |
|---|---|---|
| `shell.addAttachments` / `shell.addImages` / `conversation.createDraftImages` | ❌ 不存在 | 但它做了双路回退（`createDrafts` ✅ / `addAttachments` ✅），**不影响贴图** |
| `sctx.settings.register(ns, schema, …)`（`lib/index.js:38`） | ❌ `SettingsForms` 只有 `configure()`/`update()`，**没有 `register`** | 在 `ctx.inject(['settings'], …)` 里，失败只让该回调挂掉，可能表现为"设置页没有 dsh-appshots 一节" |
| `webServer.register` | ✅ | |

→ **判定：大概率能装、能截、能贴图，但设置面板这条路（hotkeyMode/skipSelf/attachText 等 8 个配置项）在 0.2.0-rc.2 上会失效**；因为无法用设置改热键，风险高于上面那个。它比 TaurusWood 多两个能力：**跳过 DSH 自身窗口**、**把窗口辅助功能文本作为隐藏上下文注入**（对齐 Appshots 的文本能力）。

### 14.3 生态里不值得自己再写的相邻能力（都已有成熟实现）

| 能力 | 已有项目 | 备注 |
|---|---|---|
| 纯文本模型"看图"桥接（截图/粘贴图 → 视觉模型转写） | [ysr666/dsh-vision-router](https://github.com/ysr666/dsh-vision-router) 1147★、[Anionex/dsh-vision-toolkit](https://github.com/Anionex/dsh-vision-toolkit) 887★、[MC5lan/dsh-multimodal](https://github.com/MC5lan/dsh-multimodal) | 若当前会话模型不吃图，装这个而不是改截图插件 |
| 截图 → 前端 UI 还原 / 转代码 / 转 UI 规格 | dsh-vision-toolkit、[satan9394/dsh-screenshot-to-code](https://github.com/satan9394/dsh-screenshot-to-code)、[yumimanji/dsh-ui-spec](https://github.com/yumimanji/dsh-ui-spec) | |
| Computer Use（截图 + 点击 + 输入闭环） | [TideSparrow/computer-use-dsh](https://github.com/TideSparrow/computer-use-dsh)、[988hj7tczd-oss/dsh-computer-use](https://github.com/988hj7tczd-oss/dsh-computer-use) 47★、[Admaing/dsh-plugin-computer-use](https://github.com/Admaing/dsh-plugin-computer-use) | 这是"Agent 自己截图"，与"人按快捷键截图"是两条线 |
| 浏览器页面截图（非桌面） | [e.g. dsh-chrome / dsh-browser-tools 系列](https://github.com/Kerberos255/dsh-browser-tools) | 你的需求是"当前屏幕"，不需要 |
| 大截图 Retina/4K 超限降采样 | [zyf2492313716-cloud/dsh-auto-image-optimizer](https://github.com/zyf2492313716-cloud/dsh-auto-image-optimizer) | 对应附件限制（§2-P9）的兜底 |
| 标记/圈选网页元素（不截图） | [TheYoungChen/dsh-annotate](https://github.com/TheYoungChen/dsh-annotate) | 思路不同，值得知道 |

### 14.4 对本文自研方案的影响（结论）

1. **§12 的 P1–P4（自研插件）应当取消或降级。** 同样的东西有 7 个开源实现，其中一个 API 面已核对兼容本机运行时。
2. **自研仅剩的差异化理由**（若你仍想自己写，才有意义）：
   - 用 DSH **自身快捷键系统**（`ctx.shortcuts`，设备本地、可在 设置→快捷键盘 改键）而不是全局原生热键——两者不冲突但体验不同：全局热键在 DSH 失焦时也能截图（Appshots 语义），DSH 快捷键只在窗口内有效；
   - 不依赖任何原生二进制/Swift 编译（本方案的 `screencapture` 派生子进程路线更"轻"，但少了"跳过自身窗口"和"抓被遮挡窗口"）；
   - 想同时支持 Linux（现有实现全是 macOS/Windows）。
   如果这三条你都不需要，**二开不如直接用**。
3. **推荐路线（替换 §12）**：

   | 步骤 | 动作 | 风险 |
   |---|---|---|
   | **R0** | 会话内 `plugin_manager` → `inspect` 一下 `dsh-plugin-appshot`，确认可安装 | 无 |
   | **R1** | 装 `dsh-plugin-appshot@0.4.2`（GUI 插件页输入包名 / `install_bundle` / `dsh plugin --profile desktop add dsh-plugin-appshot`），重启应用 | 中：桌面 profile 是应用自持，装失败需 `sanitizeProfile` 回滚（§11） |
   | **R2** | 系统设置 → 隐私与安全性：给 DSH Desktop 开**屏幕录制** + **辅助功能**（双 ⌘ 靠辅助功能监听），重启 | 高（不授权=黑图/不触发） |
   | **R3** | 验收：按双 ⌘ → DSH 前置 → 缩略图进草稿 → 补文字 → 发送 → 模型能引用图内内容 | — |
   | **R4** |（可选）不满意再 fork 成自己的：加 DSH 快捷键、Linux 支持、抓全屏/区域模式 | 1–2 天 |

4. 什么时候才回到"从零写"：R1 装不上（bundle 被版本闸门拒）、R2 后仍不触发，或者你明确要 Linux / 不要原生二进制 / 要用 DSH 内建键位——那时 §4–§7 的文件清单与代码骨架可直接用。

### 14.5 出处

- 插件市场：[awesome-deepseek-harness-plugins](https://github.com/imsai-sh/awesome-deepseek-harness-plugins)、在线站 [deepseek1024.com](https://deepseek1024.com/)、查询 API `https://api.deepseek1024.com/v1/plugins/search?q=screenshot`
- 两个 appshot：npm [`dsh-plugin-appshot`](https://www.npmjs.com/package/dsh-plugin-appshot)、npm [`dsh-appshots`](https://www.npmjs.com/package/dsh-appshots)
- Codex 侧参照：[Codex Appshots 文档镜像](https://raw.githubusercontent.com/crasuna/openai-dev-docs-cn-mirror/refs/heads/main/sources/en/codex/appshots.md)、[learn.chatgpt.com/docs/appshots](https://learn.chatgpt.com/docs/appshots)
- 本机核对产物：`/tmp/appshot2/package`（dsh-plugin-appshot@0.4.2）、`/tmp/appshot-probe/package`（dsh-appshots@0.1.8）、`/tmp/dsh-asar/out`（DSH 0.2.0-rc.2）

---

## 15. 实装记录：dsh-plugin-appshot 装进 desktop profile 的完整排障（2026-10-14）

这是把 §14 的 R1–R3 实际跑一遍的过程记录，**核心是一个没人在文档里写过的坑**。

### 15.1 现象

| 步骤 | 结果 |
|---|---|
| `dsh plugin --profile desktop add dsh-plugin-appshot` | ✅ 成功，`0.4.2` 装好，CLI 顺手写进 `dsh.profile.bundles`（= 已启用） |
| 重启 DSH Desktop | 应用正常起来，页面可用 |
| **原生助手 `appshot-macos` 进程** | ❌ 没有 |
| **插件路由 `GET /plugins/appshot/config`** | ❌ 404（对照：内建 `/open-in-app/apps` 返回 **401 = 路由已注册待认证**；不存在路径返回 404） |

两个独立证据都指向：**宿主半边没进合成树**。

### 15.2 逐步排除（每一步都有对照）

| 假设 | 验证方式 | 结果 |
|---|---|---|
| 页面路由探测不可靠？ | 对比 `/open-in-app/apps`(401) vs `/nonexistent`(404) | 可靠，appshot 确实是 404 |
| bundle 被版本闸门拒了？ | 用**应用真实的** `loadProfileDirectory()` 加载 desktop profile | ✅ 6/6 bundle 全部装载，`skippedBundles` 为空 |
| 包本身有毛病？ | 建隔离 profile `shotprobe` 只装 base + 插件，`--dump-config` | ✅ 364 行，插件行在，零报错 |
| 应用用了别的 profile 目录？ | `lsof` 查运行中宿主 fd | ✅ 就是 `~/.dsh/profiles/desktop`（读的是刚落盘的 `cordis.yml`） |
| 装完之后宿主没重新合成？ | 重启应用后再查 | ❌ 仍是 404 → 不是时序问题 |
| **模块能不能被 import？** | 直接 `import()` 宿主的 `dist/index.js` | ❌ **`ERR_MODULE_NOT_FOUND: Cannot find package 'react'`** |

### 15.3 根因：宿主半边顶层 `import React from "react"`，而 0.2.0-rc.2 已不提供 react

```js
// node_modules/dsh-plugin-appshot/dist/index.js:1788
import React, { useState, useEffect } from "react";   // ← 唯一的非 node: 外部依赖
```

- 该 import 在**宿主半边**（`dist/index.js`，Node 侧），不在浏览器半边；整个宿主 bundle 只依赖这一个外部包。
- 应用安装树里是 **`react` 不存在**：`desktop-runtime.json` 的 `sharedPackages` 共 280+ 项，`react`/`react-dom` **不在其中**（0.1.7 时代存在，0.2.0-rc.2 的 desktop 运行时已移除）。
- profile 的 `node_modules` 原本只有插件自己（hoisted linker）→ 解析链上**没有任何地方能提供 react** → ESM 解析直接抛错 → bundle 的模块导入失败。

**A/B 对照（决定性证据）**：

| profile | 有 `react`？ | `import("…/dsh-plugin-appshot/dist/index.js")` |
|---|---|---|
| `desktop`（原始） | ❌ | ❌ `ERR_MODULE_NOT_FOUND: Cannot find package 'react'` |
| `shotprobe`（`add react@18.3.1` 后） | ✅ | ✅ 导入成功，导出 16 个符号 |

### 15.4 修复与验证

```bash
dsh plugin --profile desktop add react@18.3.1     # 幂等，约 300ms
```

装完立刻验证三件事，全部通过：

1. `import("…/dsh-plugin-appshot/dist/index.js")` → ✅ 成功；
2. 真跑 `apply()`（用假 ctx，`DSH_DISABLE_AGENT_SPAWN=1` 防止真拉起助手）→ ✅ 正常返回，副作用：
   - `webServer.register(/plugins/appshot/events)`
   - `settings.register("appshot", applies:live)`（说明 §14.2 担心的 `ctx.settings` 兼容性**其实没问题**）
   - `webServer.register(/plugins/appshot/config)`
3. 原生助手 `doctor` 自检 → `{"code":"SCREEN_PERMISSION_DENIED", …}` = **接下来只差 TCC 授权**（可执行位由插件自己 `chmod`，无需手工处理）。

### 15.5 给上游的 bug 报告（要点）

> **标题**：host half fails to load on DSH 0.2.0-rc.2 — `dist/index.js` imports `react`, which the desktop runtime no longer provides
>
> **复现**：`dsh plugin --profile desktop add dsh-plugin-appshot` → 重启 DSH Desktop → 无 `appshot-macos` 进程，`GET /plugins/appshot/config` 返回 404（对照 `/open-in-app/apps` 返回 401）。
> **证据**：`node -e 'import("…/dist/index.js")'` → `ERR_MODULE_NOT_FOUND: Cannot find package 'react'`；`desktop-runtime.json` 的 `sharedPackages` 里没有 react。
> **建议修法**（任一）：
> 1. 把 `AppshotSettingsSection`（唯一用到 React 的部分，源码约 `dist/index.js:1788`）从宿主入口拆到独立模块，**动态 import**：`const { AppshotSettingsSection } = await import("./settings-panel.js")`；宿主半边就可以零 React 依赖；
> 2. 或把 `react` 声明为插件的 `dependencies`（npm 包是预构建产物，直接依赖更省事）；
> 3. 或在 `cordis.patch.yml` 的说明里写明"0.2.0-rc.2 需先 `add react`"。
>
> **补充**：客户端半边不受影响（浏览器由平台 `staticModules` 提供 react）。所以症状是"插件装了、UI 有反应、但按快捷键毫无反应"——很容易被误判成权限问题。

### 15.6 附带发现：这台机器上已有一个独立实现的 Appshot

- `~/.workbuddy/skills/appshot/`（今天 10:45–10:56 构建）是一个 **WorkBuddy 版 Appshots**，常驻 LaunchAgent `com.workbuddy.appshot.agent`（`state = running`，进程 `AppshotAgent agent`），屏幕录制 + 辅助功能**已授权**。
- 触发键是**同时按左右两个 ⌥(Alt)**；产出一套三件套（`shot.png` / `text.txt` / `meta.json`）到 `~/.workbuddy/appshot/captures/<时间>-<App>/`，并提供 `cmd.jsonl` 命令通道。
- **与 DSH 插件不冲突**（`⌥⌥` vs `⌘⌘`），可以共存。若只想留一个，`bash ~/.workbuddy/skills/appshot/scripts/uninstall.sh` 可停掉它。
- 顺带印证了 §2-P10 的判断：**宿主进程继承了应用的 TCC**，所以"从宿主 spawn 原生截图"这条路是通的（WorkBuddy 就是这么拿到授权的）。

### 15.7 当前状态与后续动作

| 项 | 状态 |
|---|---|
| `dsh-plugin-appshot@0.4.2` | ✅ 已装、已启用（`bundles` 末项） |
| `react@18.3.1`（补的依赖） | ✅ 已装进 desktop profile |
| 宿主半边装载 | ✅ 已修复（模块导入 + `apply()` 均验证通过） |
| **生效** | ⏳ **需要重启 DSH Desktop**（宿主进程 11:09 启动，仍有旧内存态） |
| **TCC 授权** | ⏳ 系统设置 → 隐私与安全性：给 **Appshot Agent** 勾**屏幕录制** + **辅助功能**（受限提示：另一个独立助手 `AppshotAgent` 是 WorkBuddy 的，别混淆） |
| 回滚 | 删 `~/.dsh/profile-backups/20261014-appshot/` 之外的改动即可：`dependencies` 去掉 `dsh-plugin-appshot`/`react`、`bundles` 去掉 `dsh-plugin-appshot` |

**注意（重要）**：`react` 是我为了绕过上游 bug 手工补的依赖，它**不在插件的 package.json 里**。若将来插件升级、profile 被 `sanitizeProfile` 恢复、或重装 profile，这一条会丢，症状会复现成"按快捷键没反应"。所以要么记住这条补丁，要么把 §15.5 的修法 1/2 推到上游。
