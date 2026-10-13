# 阶段 0 验收记录：本地基线

- 日期：2026-10-13
- 执行人：WorkBuddy（Agent）；复核：待用户确认
- 依据：`docs/development-plan.md`「阶段 0：本地基线」4 条验收标准
- Git 提交：验收时点 HEAD `039eae9`（`chore: stage 0 baseline`）；收尾变更（检查脚本、计划补充、本记录、`.gitignore`）随本阶段收尾提交入库
- 结论：**通过**。4 条验收全部满足；客户端工具链按计划拆出，仅 iOS runtime 留作阶段 8 环境门

## 1. 验收执行

### 1.1 `up-local.sh` 与 `/health`

`18110` 端口已有等价服务在运行，为避免打断，冷启动复跑使用 `18111` 端口（同一脚本、同一流程）：

    $ cd /Users/m007/codes/lite_tv
    $ API_PORT=18111 zsh scripts/up-local.sh

脚本输出（节选）：

    Container minitv-redis  Running
    Container minitv-postgres  Running
    INFO:     Started server process [72507]
    INFO:     Application startup complete.
    INFO:     Uvicorn running on http://127.0.0.1:18111 (Press CTRL+C to quit)
    INFO:     127.0.0.1:63437 - "GET /health HTTP/1.1" 200 OK

健康检查：

    $ curl -s http://127.0.0.1:18111/health
    {"status":"ok","env":"local","postgres":"16.13","redis":"7.4.6"}

    $ curl -s http://127.0.0.1:18110/health
    {"status":"ok","env":"local","postgres":"16.13","redis":"7.4.6"}

- `status=ok`，带 PostgreSQL 16.13、Redis 7.4.6。**验收 1 通过。**
- 验证完成后 18111 实例已停止（端口已释放）；18110 服务保持运行。

### 1.2 端口绑定

    $ docker compose ps
    NAME              STATUS                    PORTS
    minitv-postgres   Up 23 minutes (healthy)   127.0.0.1:15432->5432/tcp
    minitv-redis      Up 23 minutes (healthy)   127.0.0.1:16379->6379/tcp

    $ lsof -nP -iTCP:15432 -sTCP:LISTEN
    com.docke  90847 m007 ... TCP 127.0.0.1:15432 (LISTEN)
    $ lsof -nP -iTCP:16379 -sTCP:LISTEN
    com.docke  90847 m007 ... TCP 127.0.0.1:16379 (LISTEN)

- PostgreSQL 与 Redis 均仅绑定 `127.0.0.1`，不监听 `0.0.0.0`。**验收 2 通过。**

### 1.3 `git status` 与 `.env`

    $ git status --porcelain
     M .gitignore
     M docs/development-plan.md
     M scripts/check_local_env.py
     M scripts/fetch_litetv_catalog.py

- 无 `.env` 条目；`.env.example` 正常跟踪。**验收 3 通过。**
- 前 3 个 modified 为本次收尾变更；`scripts/fetch_litetv_catalog.py` 为工作区并行修改（非本阶段范围），未纳入收尾提交。

### 1.4 环境检查脚本

任意解释器运行都会自动重入项目 `.venv`（已用无 psycopg 的系统解释器验证重入生效）：

    $ python3 scripts/check_local_env.py
    [OK] DATABASE_URL: postgresql://minitv:minitv_local@127.0.0.1:15432/minitv
    [OK] REDIS_URL: redis://:minitv_local@127.0.0.1:16379/0
    [OK] PostgreSQL: 16.13
    [OK] Redis: 7.4.6
    --- stage 8 environment gates (do not fail stage 0) ---
    [OK] Flutter (stage gate): Flutter 3.47.7 • channel stable • https://github.com/flutter/flutter.git
    [OK] adb (stage gate): /opt/homebrew/share/android-commandlinetools/platform-tools/adb
    [OK] Android emulator (stage gate): Pixel_6_API34
    [DEFER] iOS Simulator runtime (stage gate): no iOS runtime - re-check before stage 8
    RESULT PASS
    DEFERRED iOS Simulator runtime (stage 8 gates; re-check before client stages)
    $ echo $?
    0

- 数据库 / Redis 全部 OK；客户端工具链单独列出，缺失项（iOS runtime）标注「阶段 8 前再验」，不计入本阶段失败。**验收 4 通过。**
- 备注：Flutter 实为已安装（3.47.7 stable，`~/development/flutter`，PATH 由 `~/.zshrc` 配置）。旧版脚本仅查 PATH 而误报缺失；新版探测路径与 `up-local.sh` 对齐后判定正确。

## 2. 失败用例

无。4 条验收全部通过，检查脚本 `exit 0`，失败用例归零。

## 3. 手工验收截图

无。本阶段为命令行验收，证据即以上命令输出。

## 4. 未验证项（明确声明）

| 项 | 状态 | 何时再验 |
|---|---|---|
| iOS Simulator runtime | 未安装 | 阶段 8 开工前（环境门） |
| 真 Bunny 资源 | 未创建（Storage / Stream / Pull Zone 均为 0） | 阶段 3 接真资源前 |
| 真 R2 凭据 | 未配置 | 阶段 15 发布演练前 |
| Android 模拟器启动录制 | 未做（AVD `Pixel_6_API34` 已列出） | 阶段 8 |

## 5. 收尾变更清单（随本记录一并提交）

1. `scripts/check_local_env.py`：自动重入 `.venv`；客户端工具链（Flutter / adb / 模拟器 / iOS）拆为阶段 8 环境门；Flutter 探测路径与 `up-local.sh` 对齐。
2. `.gitignore`：排除 `data/.cache/` 与 `.DS_Store`；git 仓库初始化 + 基线提交 `039eae9`。
3. `docs/development-plan.md`：规则 7 记录落点、阶段 12 前置依赖、阶段 1 措辞、阶段 0 范围（git 基线 + 客户端门）、基线表 Flutter 行、阶段记录更新。
4. `docs/acceptance/phase-00.md`：本记录。
