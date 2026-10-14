# AGENTS.md — LiteTV 仓库工作须知

## 项目服务器（2026-10-14 起）

本项目对应的线上服务器：

| 项 | 值 |
|---|---|
| IP | `YOUR_SERVER_IP` |
| SSH | `ssh byteplus-sg-1`（等价 `ssh root@YOUR_SERVER_IP`，Key 免密） |
| 主机名 | `iv-yex8aqiy9s3z47gll3wc` |
| 系统 | Ubuntu 24.04 LTS x86_64（Byteplus，新加坡） |

- 直连 22 端口正常（2026-10-14 验证）。若某代理端点封锁出站 22 端口导致连不上，可临时经 `ali-sz-1` 跳板（在 `~/.ssh/config` 的 `byteplus-sg-1` 块内加 `ProxyJump ali-sz-1`）。
- 服务器上的部署 / 发布动作仍必须走 `python3 scripts/release.py`（见下节发布纪律）。

## 发布纪律（多 Agent 强制，违反即事故）

本仓库可能有多个 Agent / 会话同时在执行任务。所有"上线"动作（发布到 Cloudflare R2、部署到服务器）必须遵守：

1. **唯一入口**：只能通过 `python3 scripts/release.py` 发布。禁止手工使用 wrangler / aws / curl / rsync / scp 向 R2 或服务器写入发布内容；禁止绕过脚本直接改桶内对象或线上文件。
2. **一次一个版本**：脚本用仓库级共享锁（位于 git 公共目录，跨 worktree 生效）串行化所有发布。锁被占用时用 `--wait` 等待，或停下来向用户汇报；**禁止删除、绕过或占用锁文件**。
3. **版本号自动分配**：`vYYYY.MM.DD.N` 由脚本在锁内自动生成并打 git tag。禁止手工创建、移动、删除版本 tag；禁止复用已分配过的版本号。
4. **先提交再发布**：发布前把本任务相关的源码改动提交为 git commit。R2 发布默认校验 `web/` 干净；服务器发布要求整树干净。确需带未提交改动发布时，必须显式加 `--allow-dirty` 并在 `--note` 说明原因。
5. **回滚走脚本**：`python3 scripts/release.py rollback r2 --to <version>`，同样经过锁。
6. **先看状态**：发布前后各跑一次 `python3 scripts/release.py status`，确认没有异常在途版本或他人持锁。

流程细则、R2 发布结构与故障处理见 `docs/release-process.md`。

## 并行协作

- 工作区可能同时存在其他会话的未提交改动：只修改和提交自己任务涉及的文件；不要 `git add -A`、不要 `git commit -a`、不要动别人的未提交文件。
- 遇到不属于本任务的改动：不要回滚、不要格式化、不要"顺手修复"。
