# 发布与版本管理（多 Agent 安全）

目标：任何一个上线动作都对应一个明确的版本号、一个 git 提交、一条发布记录；多个 Agent 同时想发布时自动排队，杜绝"两个发布同时改线上"。

## 1. 版本号规则

- 格式 `vYYYY.MM.DD.N`（Asia/Shanghai），如 `v2026.10.14.1` = 2026-10-14 的第 1 次发布。
- 每次发布（R2 或服务器）分配一个独立版本号；由 `scripts/release.py` 在锁内自动分配，并打 annotated git tag。
- 已分配的版本号永不复用：即使发布失败也保留记录（失败版本 tag 会被删除，但记录文件保留）。

## 2. 并发控制（共享锁）

- 锁文件：`<git 公共目录>/minitv-release.lock`（`git rev-parse --git-common-dir`），`flock` 独占。
- 进程退出（含崩溃）锁自动释放，不会留下死锁；锁文件内写有持有者信息（用户/Hostname/PID/Codex 会话 ID/目标/版本/开始时间），`release.py status` 可查看。
- **跨 worktree 生效**：worktree 共享同一 ` .git` 目录，同一台机器上所有 Agent / 终端共用一个锁。
- 锁被占用时：默认等待 60 秒（`--wait N` 可调，`--wait 0` 立即失败退出，exit code 3）。
- 局限：多台机器 / 多份 clone 之间目前没有共享锁（仓库还没有 remote）。所有发布必须在本仓库进行；接入 remote 后再补服务端锁。

## 3. 发布记录（Git 版本管理）

- 每次发布落一条记录：`deployments/releases/{version}.json`，随 git 自动提交（仅提交该文件，不碰其他会话改动）。
- 记录内容：版本、状态、git sha、文件清单与摘要、目标桶/前缀、操作者（含 Codex 会话 ID）、备注、dirty 标记。
- 版本 tag 指向被发布的代码提交；发布失败会自动删除 tag 并写入 `status: failed` 记录。
- 回滚写入 `deployments/releases/rollback-*.json`。

## 4. R2 发布（官网 / CMS 静态页）

```bash
python3 scripts/release.py r2 --dist web/dist --note "官网首版"
```

流程（全程持锁）：

1. 校验：`web/` 工作区干净（`--allow-dirty` 可豁免并记录 dirty 标记）；`dist` 必须含 `index.html`；dist 落后于源码时打印警告。
2. 分配版本号，给 HEAD 打 tag。
3. 上传完整版本到 `releases/{version}/`。
4. 提升到桶根目录（即"当前版本"）：先传其余文件、**index.html 最后传**，再写 `releases/current.json` 指针。
5. 写发布记录并提交 git；如配置了 `R2_PUBLIC_BASE_URL` 会做一次线上内容校验。

选项：

| 选项 | 作用 |
| --- | --- |
| `--dry-run` | 只校验和展示计划，不写任何东西（会做桶可达性检查） |
| `--no-promote` | 只上传版本目录、不切换根目录（预发布/彩排） |
| `--allow-dirty` | 允许带未提交改动发布（记录里标 dirty） |
| `--wait N` | 等锁秒数（默认 60） |

实现说明：上传走 Cloudflare REST API（`CLOUDFLARE_API_TOKEN` + `.env` 的 `R2_ACCOUNT_ID`），**不需要 S3 凭据**。
缓存策略：`index.html` → `no-cache`；`assets/**` → 一年 immutable；其余 → 5 分钟。

前置条件（每个 R2 自定义域名只配一次）：R2 自定义域名**不会**自动把 `/` 映射到 `index.html`，必须在 zone 里配一条 URL Rewrite 转换规则：

- 表达式：`((http.host eq "minitv.us" or http.host eq "www.minitv.us") and http.request.uri.path eq "/")`
- 动作：rewrite path → `/index.html`

`minitv.us` 已配置（ruleset `67dfb14054bc4c4bac4355e83a58b259`，规则 id `63bbd494400d40af903c0dbea1580585`）。缺这条规则时根路径返回 R2 404 页。

`admin.minitv.us` 同样已配置（2026-10-14）：R2 自定义域名挂在 `minitv-web` 桶，根路径 Rewrite 到 `/admin/index.html`（CMS 静态页发布在桶内 `/admin/` 前缀）。`api.minitv.us` 为 A 记录指向服务器 `YOUR_SERVER_IP`（Cloudflare 代理）。

## 5. 服务器发布

- 目标服务器：`root@YOUR_SERVER_IP`（主机名 `iv-yex8aqiy9s3z47gll3wc`，Byteplus 新加坡）。
  本机免密登录：`ssh byteplus-sg-1`（直连）。如遇代理端点封锁 22 端口的临时情况，处理方式见 `AGENTS.md`。

服务器部署接入后统一包一层：

```bash
python3 scripts/release.py run --target server -- ./scripts/deploy_server.sh
```

- 默认要求整树干净（`--allow-dirty` 可豁免并记录）；命令在锁内执行，退出码非 0 视为失败。
- 失败：删除本次 tag，保留 failed 记录。
- 部署命令自身应保证幂等 / 可重试，并在命令内部做健康检查。

## 6. 回滚

```bash
python3 scripts/release.py rollback r2 --to v2026.10.14.3
```

把该版本从 `releases/{version}/` 重新提升为根目录（index.html 最后、持锁、写 rollback 记录），不新增版本号。

## 7. 状态查看

```bash
python3 scripts/release.py status          # 锁状态 + 最近发布 + 线上当前版本
python3 scripts/release.py status --json   # 给脚本 / Agent 读取
```

## 8. 故障处理

- 发布中途失败：脚本自动删 tag、写 failed 记录。失败发生在根目录切换前 → 线上内容不变；提升阶段出错 → 用 `rollback` 恢复，记录里有完整文件清单。
- 锁"残留"：flock 随进程释放，不存在死锁；如 status 显示可疑在途版本，检查记录和线上状态后人工决定回滚或补记。
- 无权上传：确认 shell 里有 `CLOUDFLARE_API_TOKEN`、`.env` 里有 `R2_ACCOUNT_ID`。
- 线上校验 403（error code 1010）：Cloudflare 按 UA 拦截默认脚本 UA（`Python-urllib` 会被拦）。`release.py` 已带 `lite-tv-release/1.0` UA；自己写脚本访问 `minitv.us` 时同样要设置正常 UA。

## 9. 相关文件

| 文件 | 用途 |
| --- | --- |
| `scripts/release.py` | 唯一发布入口 |
| `deployments/releases/` | 发布记录（git 管理） |
| `AGENTS.md` | Agent 强制的发布纪律 |
| `docs/technical-architecture.md` §5 / §8 | 架构与发布设计 |
