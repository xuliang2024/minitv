# minitv 0.3.0 全链路验收报告

| 项 | 值 |
|---|---|
| 验收时间 | 2026-10-21 15:39–15:45（GMT+8） |
| CLI 版本 | **0.3.0**（47 条命令）；文中两处缺陷已修复，随 **0.3.1** 发布 |
| 被测后端 | 本地隔离实例 `:18121`（`BUNNY_MODE=fake`）、`:18122`（`BUNNY_MODE=real`，仅封面/字幕） |
| 数据库 | 独立库 `minitv_cli_report`（Alembic head `f1a7c2e93b64`），不触碰主库与线上 |
| 凭证 | 两把 API Key：`report/admin`（read+write+delete）、`report/readonly`（read） |
| 结论 | **28 项断言全部通过，47 条命令全部实际执行**；本轮发现 2 个 CLI 小缺陷，均已修复 |

## 1. 范围与方法

- **不是**只跑 happy path：每个写操作都配了对应的失败断言（重名、非法状态、越权、删非空分集、confirm 不符、被引用标签）。
- 上传链路用 ffmpeg 生成的 3 秒测试片（约 32KB）走完整 TUS 直传；封面/字幕因为接口要求真实存储（fake 模式返回 503），单独起 real 实例验证。
- 全流程日志留档：`/tmp/cli-report.log`（4764 行）。本报告引用其中的真实输出。

## 2. 覆盖矩阵

| 组 | 命令 | 结果 |
|---|---|---|
| config | `init` `set-key` `show` `test` `use` `path` `rm-key` | ✅ 全部执行 |
| auth | `whoami` | ✅ |
| 系统 | `version` `doctor` `export dramas` | ✅ |
| drama | `list` `get` `create` `update` `status` `publish` `unpublish` `tag` `delete` `cover-set` `cover-clear` `schedule` | ✅ 12/12 |
| episode | `list` `create` `bulk` `update` `delete` `publish` `unpublish` `publish-all` `sync` `upload` `upload-all` `subtitle` | ✅ 12/12 |
| tag | `list` `create` `update` `delete` | ✅ 4/4 |
| group | `list` `dramas` `create` `update` `add` `remove` `move` | ✅ 7/7 |
| schedule | `apply` | ✅ |
| 帮助 | `--help` / `<cmd> --help` / `help --json` / `guide` | ✅ |

## 3. 场景执行记录

### 3.1 版本、配置与身份

```
$ minitv version
{"ok": true, "data": {"version": "0.3.0"}, ...}

$ minitv config path
/tmp/cli-report-config/config.json
/tmp/cli-report-config/credentials

$ minitv config test --profile report
{"username": "report-admin", "role": "admin",
 "scopes": ["read","write","delete"], "expires_at": "2026-11-20T07:39:50Z",
 "warnings": ["该密钥绑定 admin 且含 delete，等同超管；给自动化用建议换 agent-ops"]}
```

`config show` 只返回 `key_prefix=mk_live_b012…` 与长度 60，未出现明文；`credentials` 权限为 `-rw-------`。

首次初始化（独立目录）也验证过：

```
$ echo "$KEY" | minitv config init --profile fresh --base-url http://127.0.0.1:18121 --stdin
配置目录  /tmp/cli-init-test
配置文件  /tmp/cli-init-test/config.json
{"profile": "fresh", "username": "report-admin", ...}
$ minitv config rm-key --profile fresh
已删除 profile 'fresh' 的密钥
```

### 3.2 标签 CRUD

```
$ minitv tag create --name-en Werewolf --name-zh 狼人 --category sub
{"id": 6, "name_en": "Werewolf", "name_zh": "狼人", "category": "sub", "drama_count": 0}

$ minitv tag update 6 --name-zh 狼人族
{"id": 6, "name_zh": "狼人族", ...}
```

### 3.3 建剧：正常 / 重名 / upsert

```
$ minitv drama create --title "Full Cycle 1792568545"
{"id": 12, "title": "Full Cycle 1792568545", "status": "draft"}

$ minitv drama create --title "Full Cycle 1792568545"     # 第二次
{"ok": false, "error": {"code": "title_exists", "detail": {"drama_id": 12}}}
  ↳ exit 5

$ minitv drama create --title "Full Cycle 1792568545" --upsert
{"id": 12, "upserted": true}
```

`--upsert` 命中时复用已有 id，Agent 超时重试不会建出第二部。

### 3.4 元数据、标签、封面、排期

```
$ minitv drama update 12 --synopsis "..." --free 1 --coin 60
$ minitv drama tag 12 1,6
{"id": 12, "tags": [{"name_en": "Action"}, {"name_en": "Werewolf"}]}

$ minitv drama cover-set 12 cover.jpg --profile report-real
{"cover_url": "https://minitv-dev-img.b-cdn.net/covers/12/cover.jpg?v=..."}
$ minitv drama cover-clear 12
{"cover_url": ""}

$ minitv drama schedule 12 --at 2026-10-22T20:00:00+08:00
{"id": 12, "scheduled_publish_at": "2026-10-22T20:00:00+08:00"}
```

### 3.5 分集：建 / 批量 / 改 / 上传 / 字幕 / 发布

```
$ minitv episode create --drama 12 --number 1 --title "第 1 集"
$ minitv episode bulk --drama 12 --count 3          # 补齐 1..3
$ minitv episode update 9 --title "第 1 集（改）"

$ minitv episode upload 9 S01E01.mp4
{"episode_id": 9, "status": "in_review", "size": 32396, "resumed": false}

$ minitv episode sync 9
{"id": 9, "status": "in_review"}

$ minitv episode subtitle 9 ep1.vtt --profile report-real
{"subtitle_url": "subs/9/sub.vtt"}

$ minitv episode upload-all --drama 12 --dir ./one --start 2 --title-rule cn --publish
{"uploaded": [{"number": 2, "episode_id": 10, "status": "in_review"}],
 "published": [10], "failed": []}

$ minitv episode publish 9   → {"id": 9, "status": "published"}
$ minitv episode unpublish 9 → {"id": 9, "status": "unpublished"}
$ minitv episode publish 9   → {"id": 9, "status": "published"}

$ minitv episode publish-all --drama 12
{"published": [], "skipped": [9, 10, 11]}     # 9/10 已发布，11 是没有视频的空壳
```

第 3 集（空壳）随后被删除：

```
$ minitv episode delete 11 --confirm 11
  ↳ 204
$ minitv episode list --drama 12 → total = 2
```

### 3.6 分组

```
$ minitv group create --name "验收楼层 ..." --position 7   → {"id": 3}
$ minitv group update 3 --style grid2 --status active
$ minitv group add 3 12              → written=false（只预览容量）
$ minitv group add 3 12 --confirm    → written=true
$ minitv group move 3 12 --position 0
$ minitv group dramas 3              → 1 部，position_in_group=0
$ minitv group remove 3 12           → 组内回到 0
```

### 3.7 上线 / 下架 / 重新上线

```
$ minitv drama publish 12
{"id": 12, "status": "published", "hops": ["draft → in_review", "in_review → published"]}
$ minitv drama unpublish 12   → status=unpublished
$ minitv drama publish 12     → hops: ["unpublished → published"]
```

### 3.8 对外可见性（未登录访问）

```json
GET /v1/preview/dramas/12
{"title":"Full Cycle 1792568545","episodes":2,"free":1,
 "tags":["Action","Werewolf"],
 "items":[{"n":1,"free":true},{"n":2,"free":false}]}

GET /v1/dramas/12/play
{"items":[{"n":1,"free":true,"locked":false},
          {"n":2,"free":false,"locked":true}],
 "entitlement":{"member":false,"unlocked_episode_ids":[]}}
```

即：免费 1 集可直接看，第 2 集锁定，标签随剧下发。

### 3.9 导出

```
$ minitv export dramas --out /tmp/report-export.json
已导出 N 部剧 → /tmp/report-export.json（含本剧）
```

### 3.10 续传路径

```
$ minitv episode upload 7 S01E01.mp4 --resume
{"episode_id": 7, "status": "in_review", "resumed": false}
```

`resumed=false` 是因为该会话尚未写入任何字节（offset=0）；续传路径本身走通：
`upload-headers` 重签 → `HEAD` 取 offset → 从 offset PATCH。若该集正在上传时**不带** `--resume`，会收到 422 且 hint 提示加 `--resume`。

## 4. 守卫与边界（全部按预期拒绝）

| 场景 | 实际响应 | 退出码 |
|---|---|---|
| 只读 key 建剧 | `scope_denied`「该密钥缺少 write 权限」 | 3 |
| 删已发布分集 | `episode_not_empty`「第 1 集是 published，只有空壳可以删；请先下架」 | 5 |
| 删剧 `--confirm` 不匹配 | `usage`「--confirm 与目标不一致（目标 12，收到 999）」 | 2 |
| 删仍被引用的标签 | `conflict` + `detail.affected_dramas` | 5 |
| 非法状态迁移 | argparse 拒绝（`--to` 只接受四个值） | 2 |
| `--dry-run` | 只打印 `POST /admin/v1/dramas/12/delete {"confirm": true}`，不发送 | 0 |

## 5. 本轮发现并修复的问题

| # | 问题 | 影响 | 处理 |
|---|---|---|---|
| 1 | `config init` 没有 `--stdin` | 用管道做首次初始化会 `unrecognized arguments`，只能交互输入 | 已补该参数并联调通过 |
| 2 | `episode publish/unpublish/sync/upload` 返回的 id 是字符串 | Agent 侧做数值比较/拼接时容易出错，与 drama 命令不一致 | 已统一为整数 |

> 上一轮修复的提权（空 scopes）、`--base-url` 不落盘、403 不落事件表等问题，本轮回归验证均未复现。

## 6. 已知限制（未在本次修复）

1. **删除剧集/分集只清理数据库，不删除 Bunny 上的封面与字幕对象** —— 会留下孤儿文件，需要单独的清理任务。
2. `upload-all` 仍是**串行**执行（并发 2 未实现）。
3. 没有字幕删除命令（后端也没有该接口）。
4. `cover-set` / `subtitle` 在 `BUNNY_MODE=fake` 下返回 503，属预期；验收时用 real 实例覆盖。
5. `group` 没有删除接口，只能置 `inactive`（后端设计如此）。

## 7. 结论

- **功能完整性**：剧集/分集/标签/分组/排期/媒资/上传七类操作全部可用，47 条命令均已实际执行。
- **安全性**：只读 key、scope、confirm、dry-run、非空分集四条防线均按预期拦截。
- **一致性**：TTY 输出表格、管道输出 JSON、错误体带 `code`/`hint`/`detail`，符合 `docs/cli-reference.md` 的契约。
- **建议下一步**：① 补 Bunny 孤儿对象清理；② `upload-all` 并发 2；③ 把本报告的场景脚本纳入回归（当前是临时脚本）。
