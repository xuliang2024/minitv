# 官网试播入口（Web 免费预览）验收记录

- 日期：2026-10-14
- 需求：官网参考竞品 LiteTV 增加「试播」入口；点进去展示可公开的数据（每个分类的前 7 部剧）；网页上只能播放免费集，付费集不能播放；付费集与「查看全部」引导去下载 App。
- 结论：功能完成，自测通过。后端新增用例 5 条全绿（全量 163 passed）；官网端到端 27/27 通过（无头 Chrome）。

## 1. 交付内容

### 1.1 后端（公开只读面 `/v1`，只暴露已发布内容）

| 接口 | 说明 |
|---|---|
| `GET /v1/preview` | 试播目录：`All shows` 伪分类 + 有内容的分类，每个分类最多前 **7** 部「可试播」剧。可试播 = 已上架 + 至少 1 集已发布且有视频 + 至少 1 集免费。分类来自启用标签（主分类在前），剧集按播放量降序、其次更新时间。返回 `episode_count` / `free_playable_count` 供前端标注。 |
| `GET /v1/preview/dramas/{id}` | 试播详情：剧元数据 + 已发布分集列表（每集带 `free` 标记）+ `total_episode_count`。锁定集会在列表中返回（前端展示为锁），但响应里不存在任何播放地址。未上架 / 无免费集的剧返回 404。 |
| `POST /v1/playback`（沿用） | 游客只有免费集能拿到 5 分钟签名 HLS 地址；锁定集返回 `allowed=false`，没有 `url`。 |

实现：`backend/app/modules/content/public_router.py`（`PREVIEW_LIMIT = 7`；查询集中在路由内，全部 published-only）。

### 1.2 前端（`web/`）

- **路由**：hash 路由新增 `#preview`（试播页）与 `#watch/<id>`（播放器），首页锚点（`#shows`、`#download` 等）保持不变。页面切换瞬时定位，页内锚点仍平滑滚动（`src/routes.ts`）。
- **首页入口**（3 个）：
  1. 顶栏导航新增 `Free Preview`；
  2. hero 主按钮改为 `Watch free episodes`（`#preview`）；
  3. 新增 Free Preview 区块（6 张卡片直达对应播放器 `#watch/<id>`）+ 文案与 FAQ（新增 “Can I watch on the website?”）同步更新。
- **试播页**（`src/preview.tsx`）：12 个分类行；每行最多 7 部、卡片尺寸一致（不足 7 部也保持同尺寸左对齐）；卡片角标 `N free episodes`，悬停提示锁定集数量；每行 `View all in the app →` 全部指向 `#download`；底部再给一次下载 CTA。
- **播放器**（`src/watch.tsx`）：9:16 竖屏播放器（hls.js 动态加载，Marketing 页面不加载播放器包）；只要浏览器有 MSE（Chrome / Firefox / 桌面 Safari / iOS 17.1+）就走 hls.js，仅无 MSE 的旧版 iOS Safari 回退原生 HLS；右上角**清晰度菜单**（档位来自真实 HLS manifest，`Auto / 720p / 480p / 360p / 240p`，手动选档会临时关闭按尺寸限档）。右侧剧集宫格（锁定集带锁图标）、剧情、标签、`Unlock all episodes in the app` CTA、`View all episodes in the app →`。
- **播放策略**：
  - 点击免费集 → `POST /v1/playback` → 播放签名 HLS；
  - 免费集播完自动连播下一免费集；下一集是付费集时**停下**并弹出下载引导；
  - 清晰度切换：菜单档位由播放器解析出的 manifest levels 生成，默认 `Auto`（ABR + 按播放器尺寸限档）；手动选档立即切换（`hls.currentLevel`）并关闭限档，切回 `Auto` 恢复自适应当前档位显示为 `Auto · 360p`；换集重置为 `Auto`；
  - 点击锁定集直接弹「下载引导」覆盖层，**从不**请求 `/v1/playback`；
  - 所有「解锁 / 查看全部」按钮都落到官网 `#download`（App Store / Google Play / 二维码）。
- **网页不实现**金币、会员、解锁等付费能力——付费只在 App 内。

## 2. 验收证据

### 2.1 后端

```
cd backend && ../.venv/bin/python -m pytest -q          # 163 passed
cd backend && ../.venv/bin/python -m pytest tests/test_public_preview.py -q   # 5 passed
```

`backend/tests/test_public_preview.py` 覆盖：

1. 目录只暴露「已上架 + 有免费已发布集」的剧；草稿 / 无已发布集 / 无免费集的剧全部不可见；卡片计数正确。
2. 每个分类强制截断在前 7 部（造 8 部验证 `total=8, dramas=7`）。
3. 详情返回已发布分集与 `free` 标记；未发布分集不出现；响应全文没有 `m3u8` / `bcdn_token` / `token=`。
4. 未上架 / 无免费集详情 404。
5. 游客请求锁定集播放：`allowed=false` 且无任何播放地址（后端兜底，防止绕过前端）。

### 2.2 前端

```
cd web && npm run build          # tsc + vite build 通过
cd web && npm run accept:preview # headless Chrome，27/27 checks passed
```

端到端脚本 `web/scripts/preview-acceptance.mjs`（报告：`docs/acceptance/walkthrough/preview-e2e-results.json`）逐项验证：

- 首页导航 / hero / 区块三个入口存在；区块可达。
- 试播页 12 行；每行卡片 ≤ 7（实测 `7,2,2,2,4,3,2,2,2,2,5,3`）；每行都有指向 App 的 `View all`。
- 播放器打开目标剧；剧集宫格与 API 一致；锁定集渲染为锁。
- 点击免费集：视频真实播放（`readyState=4`、`currentTime>0.5`），且该集只授权 1 次。
- 清晰度菜单：列出 `Auto` + 真实 manifest 档位（实测 `Auto · 360p | 720p | 480p | 360p | 240p`）；手动选 `720p` 后视频真实解码为 720x1280（绕过按尺寸限档）；再选 `240p` 解码为 198x352；切回 `Auto` 按钮回到自适应。
- 点击锁定集：出现「Episode N is locked / Download the app」覆盖层，**未产生任何播放授权请求**。
- 播放到最后一集免费集的结尾：自动停止在付费集上弹出同样的下载覆盖层，且付费集从未被授权。
- 锁定 CTA 与「View all」都落在 `#download` 下载区（App Store / Google Play / 二维码可见）。
- API 兜底：游客请求付费集 `allowed=false`、无 URL。
- 移动端（390×844）：试播页与播放器页无横向溢出。

截图（`docs/acceptance/walkthrough/`）：

| 文件 | 内容 |
|---|---|
| `preview-01-home-entry.png` | 首页 hero + 导航试播入口 |
| `preview-07-home-teaser.png` | 首页 Free Preview 区块 |
| `preview-02-catalog.png` | 试播页（All shows + Drama 行 + View all 引导） |
| `preview-03-category-row.png` | 分类行特写（不足 7 部时卡片尺寸一致） |
| `preview-04-player-playing.png` | 免费集播放中（宫格 / 锁定集 / 解锁 CTA） |
| `preview-11-quality-menu.png` | 播放器右上角清晰度菜单（Auto + 真实档位） |
| `preview-12-quality-switched.png` | 手动切到指定档位后的播放画面 |
| `preview-05-player-locked.png` | 点击付费集的下载引导覆盖层 |
| `preview-08-auto-advance-locked.png` | 免费集播完自动停在付费集的引导 |
| `preview-06-download-gate.png` | 引导落地：下载区（App Store / Google Play / 二维码） |
| `preview-09-mobile-catalog.png` / `preview-10-mobile-player.png` | 移动端试播页 / 播放器 |

### 2.3 本地数据说明

试播只展示「已上架 + 有已发布免费集」的剧。当前本地库 14 部剧满足（“E2E Smoke Bunny” 只有转码中的分集、“测试”无免费集，均按要求不出现）。

## 3. 文档更新

- `docs/software-architecture.md`：原文「官网不提供登录、播放和支付」更新为「官网不做登录和支付，只读取公开内容、下载配置和免费试播」，并注明分集签名地址与付费集策略。

## 4. 边界与后续

- 网页签名地址 TTL 300s；播放器在网络错误时自动重签 1 次，仍失败则提示并引导 App。
- 清晰度档位以 manifest 实际转码结果为准（Bunny 播单目录名，如 `240p/`）；`/v1/playback` 的 `renditions` 只是提示字段，已改为按源片短边推导（不再硬编码 1080P）。Bunny 不放大：源片 480×854 的剧最多只能选到 480p。
- 试播页每分类固定取前 7（`PREVIEW_LIMIT`）；若产品要调整数量或增加「精选/推荐位」，改常量与排序即可。
- 全量 pytest 163 passed；本次改动不涉及 App、Admin 和支付链路。
