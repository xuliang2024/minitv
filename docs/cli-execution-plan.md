# 剧集管理 CLI 执行方案（v2，2026-10-21）

> 前置：`docs/drama-cli-plan.md`（总体设计）、`docs/admin-api-key-remediation.md`（已上线）
> 本文件只回答两件事：**接下来按什么顺序做**、**每一步交付什么、怎么验收**。

## 1. 现状

| 项 | 状态 |
|---|---|
| P0-1 API Key 体系 | ✅ 已上线（server v2026.10.21.6 / admin v2026.10.21.7），线上实测可用 |
| P0-2 建剧重名兜 409 | ❌ 未做：`create_drama` 没 catch `IntegrityError`，重名返回 500 |
| P0-3 分集删除（只删空壳） | ❌ 未做：没有 `DELETE /episodes/{id}` |
| P0-4 TUS 重签 + 续传 | ❌ 未做：签名 1h 过期后无法续，只能等转码失败重来 |
| P1-P4 CLI 与 Agent 接入 | ❌ 0 行代码 |

**已具备、可直接被 CLI 使用的能力**：37 个内容接口、key 鉴权、scope 收紧、写事件、限流。
也就是说：**只读命令现在就能做，不需要任何后端改动。**

## 2. 推荐路线（一句话）

**先发只读版 CLI 接进 Agent，再按 P0-2 → P0-3 → P0-4 的顺序逐层开写权限。** 不要等三个后端缺口都补完才开始写 CLI。

理由：

1. 只读命令（`doctor` / `whoami` / `drama list|get` / `episode list` / `tag list`）**零后端依赖**，今天就能在线上用现有 key 跑通，Agent 当天就能接。
2. 写命令的阻塞点是「幂等」和「清理」：建剧重名 500 会让 Agent 无法安全重试，删不掉空壳会让错误无法回收。这两个不修，写命令只能算半成品。
3. 上传的阻塞点只有一个（签名过期），但它是硬阻塞：>200MB 或慢链路上必然踩。放最后是因为它工作量最大、且前两层的命令可以先支撑「建剧 + 配元数据 + 发布已有分集」。

## 3. 迭代 0：CLI 骨架 + 只读（规模 M，无后端依赖）

### 3.1 交付物

| 文件 | 内容 |
|---|---|
| `cli/minitv` | 单文件、纯标准库（argparse + urllib + json + getpass）的 CLI 本体，带 shebang，可直接执行 |
| `cli/install.sh` | 下载安装 + 可选写 key + 自检的引导脚本（发布到 `https://minitv.us/cli/install.sh`） |
| `docs/cli-reference.md` | **由 `minitv guide` 生成**后提交入库：命令表、参数、示例、错误码、典型流程（给 Agent 读） |
| `admin/src/pages/ApiKeys.tsx` | 密钥页新增「接入 Agent」入口 + 签发弹窗里的一键命令（见 §3.7） |

单文件的取舍：37 个命令约 1500–1900 行，仍可读；换来「丢进任何有 python3 的沙箱就能跑」，不用装包、不用 venv。超过 2000 行再拆包。

**兼容下限是 Python 3.8**，所以配置用 JSON 而不是 TOML（`tomllib` 要 3.11，而 macOS 自带 python3 可能更老）。少一个解析依赖，也少一条「版本不够」的失败路径。

### 3.2 内部结构：SPEC 驱动（help 与实现同源）

37 个命令如果 help 手写，必然和实现漂移。所以只有一份注册表，argparse、`--help`、`help --json`、`guide` 全部由它生成：

```python
SPEC = [
    Group("drama", "剧集：列表 / 详情 / 建改 / 上下线 / 排期 / 封面 / 标签", [
        Cmd("list", "列出剧集",
            args=[Arg("--status", choices=[...], help="按状态筛选"),
                  Arg("--q", help="标题关键字（只匹配标题）")],
            examples=["minitv drama list --status draft",
                      "minitv drama list --json --fields id,title"],
            next="minitv drama get <id>  查看单部详情"),
        ...
    ]),
]

main()  →  gen_parser(SPEC) | render_help(SPEC, path) | catalog(SPEC) | guide(SPEC)
Client  →  request() / paginate() / upload()
```

一个 `Cmd` 同时产出：argparse 定义、终端 help、JSON 目录、Markdown 手册。加命令只改一处。

### 3.3 帮助系统（Agent 的主要入口）

分三层 + 两个附加入口：

| 入口 | 内容 | 典型用法 |
|---|---|---|
| `minitv --help` | 30 秒上手：能干什么、能力地图、去哪读更多 | Agent 被丢进来第一件事 |
| `minitv <group> --help` | 该组每个子命令一行说明 | 缩小范围 |
| `minitv <group> <cmd> --help` | 完整规格（见下） | 真正执行前 |
| `minitv help errors` / `output` / `auth` / `upload` | 专题说明 | 排错 |
| `minitv guide [--format md\|json]` | 完整手册打到 stdout | `minitv guide > context.md` 喂给模型 |
| `minitv help --json` | 机器可读目录：name / summary / args / examples / exits | Agent 自己发现能力 |

**单条命令 help 的固定 8 段**（顺序不许变，Agent 可稳定解析）：

```
用途    一句话说明这条命令做什么
用法    minitv drama create --title T [--upsert]
参数    每个参数一行：名称、是否必填、默认值、说明
示例    至少 2 条：一条人类可读、一条 --json（可直接复制）
输出    成功时返回哪些字段；表格模式显示哪几列
退出码  0 成功 / 2 用法 / 3 认证 / 4 不存在 / 5 冲突 / 6 上传 / 7 网络
常见错误  title_exists / scope_denied / rate_limited 及各自处置
相关命令 下一步该敲什么（如 create 后接 update、publish）
```

四条硬性要求：

1. **help 不发网络请求、不需要 key、配置损坏也能看**（`minitv --help` 必须在首次安装、未配置时可用）；
2. 每条命令**至少 2 个示例**，且示例必须能直接复制执行；
3. 每条命令都写「相关命令 / 下一步」——Agent 是照着串命令的，不是读散文的；
4. 退出码和错误码写进每条 help，不用让 Agent 去翻文档。

`minitv --help` 的能力地图大致长这样：

```
minitv — MiniTV 剧集管理 CLI

开始
  minitv config set-key       配置 API Key（首次）
  minitv doctor               自检：连通 / 身份 / 权限 / 限流

能做什么
  drama     剧集：列表、详情、建改、上下线、排期、封面、标签
  episode   分集：列表、建改删、发布、上传、转码对账
  tag       标签库：列表、增改删
  group     首页分组：列表、成员、排序
  export    批量导出（给模型做上下文）

给程序/Agent
  minitv <任意命令> --json    结构化输出
  minitv help --json          机器可读的命令目录
  minitv guide                完整手册

更多
  minitv help errors          错误码与处置
  minitv <group> --help       分组内命令
```

### 3.4 配置与密钥（`minitv config`）

**两个文件**，密钥与配置分离，`config.json` 可以随手拷给别人：

```
~/.config/minitv/config.json      # 非敏感：环境、默认 profile、沙箱剧 id（0600 也无妨）
~/.config/minitv/credentials      # 0600，一行一个：prod=mk_live_...
```

```json
{
  "default_profile": "prod",
  "profiles": {
    "prod": { "base_url": "https://api.minitv.us", "sandbox_drama_id": 0 },
    "local": { "base_url": "http://127.0.0.1:18110", "sandbox_drama_id": 0 }
  }
}
```

| 命令 | 行为 |
|---|---|
| `minitv config init` | 交互式引导：选环境 → 隐藏输入粘贴 key → 验证 → 写盘（目录 0700 / 文件 0600） |
| `minitv config set-key [--profile P] [--stdin]` | 设置/替换 key；`--stdin` 供 `echo "$KEY" \| minitv config set-key --stdin` |
| `minitv config show` | 打印 profile、base_url、**key 前缀与长度**、到期、文件路径；永不打印明文 |
| `minitv config test` | 用当前 key 调 `/auth/me` + `/auth/keys`，打印身份 / scopes / 到期 |
| `minitv config use <profile>` | 切换默认 profile |
| `minitv config path` | 打印两个文件路径（排错用） |
| `minitv config rm-key [--profile P]` | 删除该 profile 的 key（不删配置） |

规则：

1. **没有 `--key` 参数**：传了直接报错并提示用 `--stdin` 或交互输入（避免进 shell history 与进程列表）；
2. **写入前先验证**：拿 key 调一次 `/auth/me`，失败就不落盘，避免存进一个错 key 之后每次都要排查；
3. **密钥只存 `credentials`**，`config.toml` 里不出现；`config show` 只回前缀；
4. **环境变量优先**：`MINITV_API_KEY` 存在时直接用它，不读文件（Agent 沙箱/容器最省事）；
5. 优先级：`--base-url` / `MINITV_API_KEY` > `--profile` > `default_profile`。

首次使用的完整体验（三次输入以内）：

```
$ minitv config set-key
环境     prod  https://api.minitv.us
凭证     粘贴 API Key（输入不回显）：********
校验     GET /auth/me → admin（管理员）· 读 · 写 · 删除 · 2027-10-21 到期
已保存   ~/.config/minitv/credentials（0600）
下一步   minitv doctor
```

### 3.5 输出契约（Agent 最关心的部分）

| 场景 | 行为 |
|---|---|
| stdout 是 TTY | 表格 |
| 非 TTY / `--json` | `{"ok":true,"data":...,"meta":{"request_id","elapsed_ms"}}` |
| 失败 | `{"ok":false,"error":{"code","message","http_status","hint"}}` |
| `--fields id,title` | 裁剪字段，省 token |
| `--quiet` | 只输出主键（id），给脚本串联 |

退出码：`0` 成功 / `2` 用法 / `3` 认证（过期/撤销/禁用）/ `4` 不存在 / `5` 状态冲突 / `6` 上传失败 / `7` 网络。

### 3.6 命令集（迭代 0）

```
minitv config init | set-key | show | test | use | path | rm-key
minitv doctor                      # 连通 / 身份 / 权限 / 服务端 / 限流
minitv auth whoami                 # 由 /auth/me + /auth/keys 组合
minitv version                     # 显示 CLI 版本（等同 --version）
minitv guide [--format md|json]
minitv help [--json] [errors|output|auth|upload]
minitv drama list   [--status S] [--q 关键字] [--tag ID] [--page N]
minitv drama get    <id>
minitv episode list --drama ID [--status S]
minitv tag list
minitv group list | get <id> | dramas <id>
minitv export dramas [--status published] --out FILE   # 给 Agent 做大上下文
```

`doctor` 的检查项（每项一行，失败即非 0 退出）：

1. `base_url` 可达（`GET /health`）；
2. 凭证有效（`GET /auth/me`）；
3. 打印**密钥 scopes 与到期**；若 scopes 含 `delete` 或绑定 `admin`，打警告；
4. `GET /auth/keys` 确认前缀与当前 key 一致；
5. 服务端版本/环境（来自 `/health` 的 `env`、postgres、redis）。

> 这条警告不是装饰：今天那把 key 绑的是 `admin` 且带 `delete`，等价超管。`doctor` 应当当场指出。

### 3.7 分发与一键初始化

发布方式：`cli/` 目录随 R2 发到 `cli/` 前缀（不带 index.html，所以要 `--allow-no-index`）。

```
python3 scripts/release.py r2 --dist cli --source cli --prefix cli --allow-no-index \
  --note "CLI 单文件 + 安装脚本"
```

产物地址固定，不随版本变：

| URL | 内容 |
|---|---|
| `https://minitv.us/cli/install.sh` | 安装引导 |
| `https://minitv.us/cli/minitv` | 单文件本体（由 install.sh 下载） |

**两条一行命令**：

A. 给 Agent 的完整初始化（含 key，只在「密钥已生成」弹窗出现一次）

```
curl -fsSL https://minitv.us/cli/install.sh | MINITV_API_KEY='mk_live_...' sh
```

B. 不含 key 的安装（页面顶部「接入 Agent」用）

```
curl -fsSL https://minitv.us/cli/install.sh | sh && minitv config set-key
```

`install.sh` 的行为（幂等）：

1. 找 `python3`，低于 3.8 直接报错并给安装提示，不留半成品；
2. 下载 `cli/minitv` → `~/.local/bin/minitv`（`--prefix` 可改），`chmod 0755`；
3. `~/.local/bin` 不在 PATH 时打印 `export PATH=...` 提示（不自动改 rc 文件）；
4. 若 `MINITV_API_KEY` 存在 → `printf '%s' "$MINITV_API_KEY" | minitv config set-key --stdin`
   —— **key 只走 stdin，不进 argv**，`ps` 里看不到；
5. 跑 `minitv doctor`（`--no-doctor` 可跳过）；失败也保留已装好的文件，方便复诊；
6. 重复执行 = 覆盖升级；没给 `MINITV_API_KEY` 时**不动已保存的 key**。

UI 改动（`admin/src/pages/ApiKeys.tsx`）：

| 位置 | 内容 |
|---|---|
| 页面右上角 | 「接入 Agent」按钮 → 弹窗三步：① 安装（B 命令）② 设置 key ③ `minitv doctor`；每段带复制按钮 |
| 密钥弹窗（签发 / 轮换后） | 明文下方增加「复制给 Agent 的一键命令」，内容为 A 形式（含该 key）；旁边红字提示「命令含密钥，勿粘贴到公开渠道」 |
| 列表行 | 不加。明文不在列表里，行内命令无法含 key，加了只会误导 |

安全提示（必须写进 UI 文案）：

- A 形式的 key 会留在执行方的 shell history / Agent 日志里，共享机器请用 B 形式手动 `set-key`；
- 安装脚本与 CLI 只从 `minitv.us` 取，不接受第三方镜像。

**顺序约束**：这个按钮必须与 `cli/` 的首次发布同一批上线，否则用户复制到的是 404。

### 3.8 验收

- [ ] `minitv --help` 在**没有配置文件、没有 key、断网**时正常输出并返回 0；
- [ ] 每条命令的 `--help` 都含 8 段，且示例可直接复制执行；
- [ ] `minitv help --json` 能列出全部命令，字段与 `--help` 一致（同源生成）；
- [ ] `minitv guide` 输出可被 `>` 重定向成 markdown，且与 `docs/cli-reference.md` 逐字一致（CI 里 diff）；
- [ ] `minitv config set-key` 写入 0600/0700；`config show` 不出现明文；`--key` 参数被拒绝；
- [ ] 错误的 key 不会被写盘（校验失败即退出 3）；
- [ ] 断网 / 错误 key / 过期 key 三种情况都给出可区分 `code` 与退出码；
- [ ] `minitv drama list --json` 与 `curl /admin/v1/dramas` 字段一致；
- [ ] `--fields id,title` 输出只含两列；
- [ ] 在线上用**只读 key** 跑通全部只读命令（不产生任何写事件）；
- [ ] 单文件 `python3 -m py_compile` 通过，无第三方 import；
- [ ] 干净环境（无 `~/.config/minitv`、无 `~/.local/bin/minitv`）跑 A 命令，90 秒内完成安装 + 配置 + doctor；
- [ ] `ps` 输出里看不到 key（stdin 传递）；
- [ ] 重复执行 `install.sh` 幂等，已保存的 key 不被空值覆盖；
- [ ] 无 python3 / python<3.8 时报错明确，且不留下半个可执行文件；
- [ ] 页面「接入 Agent」与密钥弹窗里的命令字符串来自同一个常量（改一处两边同步）。

## 4. 迭代 1：P0-2 + 剧写入（规模 S+M）

### 4.1 后端改动（`ops/router.py`）

```python
# create_drama
try:
    db.commit()
except IntegrityError:
    db.rollback()
    existing = db.scalar(select(Drama).where(Drama.title == body.title))
    raise HTTPException(409, {"code": "title_exists",
                              "message": "同名剧已存在",
                              "drama_id": existing.id if existing else None})
```

- `update_drama` 改名撞车同样兜 409（目前也会 500）；
- 补测试：同标题建两次 → 第二次 409 且带 `drama_id`；改名撞车 → 409。

### 4.2 CLI 命令

```
minitv drama create --title T [--upsert]     # 409 时回读同名片并复用（幂等）
minitv drama update <id> [--synopsis] [--cover-url] [--free N] [--coin N] [--tags 1,2]
minitv drama status <id> --to draft|in_review|published|unpublished
minitv drama publish <id>                    # 自动补跳（draft→in_review→published）
minitv drama unpublish <id>
minitv drama schedule <id> --at ISO8601
minitv drama tags set <id> 1,2,3
minitv drama cover set <id> FILE | clear <id>
```

### 4.3 验收

- [ ] `--upsert` 连跑两次只产生一部剧；
- [ ] `drama publish` 对 draft 一次调用即上线（内部两次状态迁移）；
- [ ] 非法状态迁移返回退出码 5，且 `error.code=conflict`；
- [ ] 写操作在 `api_key_events` 里有对应记录。

## 5. 迭代 2：P0-3 + 分集命令（规模 M）

### 5.1 后端：`DELETE /admin/v1/episodes/{id}`

| 项 | 规则 |
|---|---|
| 允许删除 | `not_uploaded`、`transcode_failed`（空壳） |
| 拒绝 | 其余状态 → 409 `{"code":"episode_not_empty","message":"请先下架再删"}` |
| 清理 | `media_uploads`、`cdn_purges`；`bump_generation` 让旧 URL 失效 |
| 审计 | `audit_logs(action="episode_delete", target_type="episode")` |
| scope | `delete`（路径策略已覆盖 `DELETE /episodes/{id}`） |

前端 `EpisodesPanel` 增加行内「删除」，仅对空壳显示。

### 5.2 CLI 命令

```
minitv episode create --drama ID --number N [--title T]
minitv episode bulk   --drama ID --count N
minitv episode update <id> [--title T] [--number N]
minitv episode delete <id> --confirm <id>
minitv episode publish|unpublish <id>
minitv episode publish --drama ID --all       # 批量，跳过无视频的
minitv episode sync <id>                      # 手动对账转码状态
```

### 5.3 验收

- [ ] 删 `in_review` 集 → 409 且提示下架；
- [ ] 删空壳 → 204，`media_uploads`/`cdn_purges` 同步消失；
- [ ] `bulk --count 64` 幂等（重复执行不报错、不重复建）。

## 6. 迭代 3：P0-4 + 上传（规模 L）

### 6.1 后端：`POST /episodes/{id}/upload-headers`

续传的唯一入口，不需要 CLI 本地存状态：

```json
// 请求 {} ；前置：episode.status == "transcoding" 且存在未完成的 media_upload
{"video_id":"...","upload_url":"https://video.bunnycdn.com/tusupload",
 "headers":{...},"expire_at":"2026-10-21T15:10:00Z"}
```

- 复用 `bunny.tus_headers(video_id, ttl=3600)`；
- upload 已 completed / failed → 409 `upload_not_resumable`；
- 该接口不改变任何状态，可重复调用。

### 6.2 CLI 上传流程（无本地状态）

```
1. POST upload-session              → video_id, headers
2. TUS POST (create)                → Location
3. HEAD Location                    → Upload-Offset（续传从这来）
4. PATCH bytes 从 offset 开始        → 断点续传天然成立
   401/403 → 重新 upload-headers → 回到 3（一次重试）
5. POST sync-status 轮询（5s，10min 上限）→ in_review / transcode_failed
6. 失败项 POST upload-failed，保留 video_id 供下次 resume
```

```
minitv episode upload <id> FILE [--resume] [--wait]
minitv episode upload-all --drama ID --dir DIR
        [--start N] [--title-rule en|cn|custom:"第{n}集"]
        [--concurrency 2] [--publish] [--retry 2]
```

`upload-all` 语义：

- 目录内 `.mp4/.mov` 按文件名自然序 → 集号（可 `--start` 偏移）；
- 缺的集自动建（已存在则复用，按集号匹配）；
- 并发上限 2（与后台前端一致，Bunny 限流友好）；
- 单项失败不中断队列，最后汇总；`--retry` 只重跑失败项；
- `--publish` 在全部 `in_review` 后逐集发布，再按需推剧上线。

### 6.3 验收

- [ ] 正常 200MB 文件一次通过；
- [ ] 上传中断（断网/杀进程）后 `--resume` 从正确 offset 续上，不重传已完成字节；
- [ ] 人为让签名过期后仍可续传（验证 upload-headers）；
- [ ] `upload-all` 12 个文件：并发 2、失败 1 个、重跑只补那 1 个；
- [ ] 全流程结束后 `api_key_events` 有条目、剧/集状态符合预期。

## 7. 迭代 4：Agent 接入（规模 M）

1. `~/.workbuddy/skills/minitv-ops/SKILL.md`：**不要复述命令细节**——只写「先跑 `minitv guide` 拿手册，按 help 的『相关命令』串流程」，再补三条典型流程（建剧并配齐元数据 / 补传分集 / 全量下架）与禁忌（未经确认不 `delete`、不碰 `staff`/`billing`）。手册以 `minitv guide` 为唯一真源，避免 Skill 文档与 CLI 漂移。
2. 可选 `minitv mcp`：把同一批函数注册成 MCP stdio 工具（手写 JSON-RPC），只有宿主支持 MCP 时才用。
3. 灰度：`read` key 跑一周 → `read+write` → 临时 `delete`。
4. 收尾：用 CLI 替换 `scripts/publish_ready_dramas.py`、`repair_stalled_episodes.py`、`import_hotlist.py`（三者仍在用已失效的 `admin-1234` 默认口令）。

## 8. 明确不做

| 不做 | 原因 |
|---|---|
| 交互式 TUI / 进度条动画 | Agent 不需要，人用 `--json` + `jq` 足够 |
| 本地状态库（sqlite/缓存） | 续传靠 HEAD 取 offset，服务端才是真源，多机也能续 |
| 多租户 / 多 key 自动切换 | 一 key 一用途，切换用 `--profile` |
| Windows 兼容 | 现有脚本都是 macOS/Linux 系 |
| 给 `schedules/apply` 加 cron | 独立议题，与 CLI 无关 |
| 在 CLI 里做权限提升 | `scopes` 只能收窄，CLI 只读不写 key |

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| Agent 误删内容 | 默认 key 无 `delete`；`--confirm` 必须与被删对象一致；`api_key_events` 可追溯 |
| 上传把 Bunny 限流打爆 | 并发固定 2，失败指数退避；`--retry` 只补失败项 |
| 命令语义与后台 UI 漂移 | 命令直接映射现有接口，唯一「编排」是 `publish` 自动补跳，写进手册 |
| 单文件过大难以维护 | 先单文件；超过 2000 行按 `commands/`、`client.py` 拆包，保持入口不变 |
| 输出契约被破坏（Agent 依赖） | 契约放进 `docs/cli-reference.md` 并加一条冒烟测试：三种错误码 + 两种成功输出 |

## 10. 顺序总表

| 迭代 | 后端依赖 | 交付能力 | 规模 |
|---|---|---|---|
| 0 | 无 | 骨架 + `--help`/`guide`/`help --json` + `config set-key` + 只读命令 + `cli/` 分发（install.sh）+ 后台「接入 Agent」入口 | M |
| 1 | P0-2 | 建剧 / 改剧 / 上下线 / 标签 / 封面 | S+M |
| 2 | P0-3 | 分集增改删（空壳）/ 发布 / 对账 | M |
| 3 | P0-4 | 单集上传 / 目录批量上传 / 续传 / 自动发布 | L |
| 4 | — | Skill + MCP + 灰度 + 老脚本退场 | M |

**建议**：迭代 0 与 P0-2 可以并行（互不依赖），迭代 1 的 CLI 部分等 P0-2 合并后接上；P0-3 与迭代 0 也可并行。

## 11. 实施记录：全链路验收（2026-10-21 15:20）

CLI **0.2.0** 把迭代 1–2 写完，并做了迭代 3 的单发上传（不含断点续传）。用独立库 + `BUNNY_MODE=fake` 跑了完整验收，12 项全过。

### 新增能力

| 层 | 内容 |
|---|---|
| CLI | `drama create/update/status/publish/unpublish/tag/delete`；`episode create/bulk/update/delete/publish/unpublish/publish-all/sync/upload/upload-all`；全局 `--dry-run`；错误体透出 `detail`（如 `title_exists` 的 `drama_id`） |
| 后端 | 建剧/改名重名 → `409 title_exists`（带 `drama_id`）；`DELETE /admin/v1/episodes/{id}` 只删空壳（`not_uploaded`/`transcode_failed`），其余 409 `episode_not_empty` |
| 测试 | 后端 358 passed（新增 5 项守卫测试） |

### 验收脚本（真实顺序）

```
doctor → 建剧(draft) → 重名(409) → --upsert 复用 → 改简介/免费集数/定价
→ 设标签(Action/Rebirth) → 目录批量上传 2 集(自动建集+上传+发布)
→ 分集 published → drama publish(自动补跳 draft→in_review→published)
→ 未登录访问 /v1/preview/dramas/{id}（2 集、1 集免费、标签可见）
→ /v1/dramas/{id}/play（第 1 集 free/unlocked，第 2 集 locked）
→ 删非空分集(409) → --confirm 不符(exit 2) → dry-run → 整剧删除
```

结果：**12/12 PASS**；公开接口返回 `episode_count=2 / free_playable_count=1 / tags=[Action,Rebirth]`，播放数据 `locked` 状态正确。

### 还没做

- **断点续传**（P0-4 `upload-headers` 未实现）：大文件在 1h 签名过期后需重跑，会新建会话从头传。
- `upload-all` 目前**串行**，计划里的并发 2 放在后续。
- 封面 / 字幕 / 排期 / `tag` 与 `group` 的写命令仍未进 CLI。
- 上传时 CLI 不探测分辨率与时长（传 0），分集列表里时长显示 `-`；真实 Bunny 转码后由回调回填。
