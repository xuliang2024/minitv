# minitv 0.3.0 — MiniTV 剧集管理 CLI

单文件、零依赖（Python >= 3.8）。TTY 输出表格，非 TTY 输出 JSON。

## 快速开始

```bash
minitv version    # 当前版本
```

```bash
curl -fsSL https://minitv.us/cli/install.sh | sh
minitv config set-key
minitv doctor
```

## 命令

### config — 配置与 API Key

- `minitv config init [--profile P] [--base-url URL]` — 首次初始化：创建配置目录并写入 API Key
- `minitv config set-key [--profile P] [--stdin]` — 设置或替换当前 profile 的 API Key（先校验再落盘）
- `minitv config show` — 查看当前配置（永不打印明文密钥）
- `minitv config test` — 在线校验密钥：身份、权限、到期
- `minitv config use <profile>` — 切换默认 profile
- `minitv config path` — 打印配置文件与凭据文件的路径
- `minitv config rm-key [--profile P]` — 删除某个 profile 的密钥（保留其余配置）

### auth — 身份

- `minitv auth whoami` — 打印当前密钥绑定的账号与权限

### drama — 剧集：列表、详情、建改、上下线、排期、封面、标签

- `minitv drama list [--status S] [--q 关键字] [--tag ID] [--page N] [--page-size N]` — 列出剧集（按更新时间倒序）
- `minitv drama get <id>` — 查看单部剧的详情（含标签、集数、播放量）
- `minitv drama create --title <T> [--upsert]` — 新建一部剧（默认 draft），标题全局唯一
- `minitv drama update <id> [--title T] [--synopsis S] [--cover-url U] [--free N] [--coin N] [--tags 1,2]` — 修改剧集元数据（只传要改的字段）
- `minitv drama status <id> --to draft|in_review|published|unpublished` — 单步切换剧集状态（不走自动补跳）
- `minitv drama publish <id>` — 上线：按状态机自动补跳直到 published
- `minitv drama unpublish <id>` — 下架剧集（同时作废已签发的播放地址）
- `minitv drama tag <id> <tag_ids>` — 覆盖设置剧集的标签（全量替换）
- `minitv drama delete <id> --confirm <id>` — 删除剧集（不可恢复；会连带删除分集与用户权益）
- `minitv drama cover-set <id> <file>` — 上传封面（JPG/PNG/WebP，≤5MB）
- `minitv drama cover-clear <id>` — 清除封面
- `minitv drama schedule <id> --at 2026-10-25T20:00+08:00` — 设置定时上线时间（需要外部触发 apply 才生效）

### episode — 分集：列表、建改删、发布、上传、转码对账

- `minitv episode list --drama ID [--status S] [--page N] [--page-size N]` — 列出某部剧的分集
- `minitv episode create --drama ID --number N [--title T]` — 登记一集（空壳，尚无视频）
- `minitv episode bulk --drama ID --count N` — 批量登记 1..N 集（已存在的跳过）
- `minitv episode update <id> [--title T] [--number N]` — 修改分集标题或集号
- `minitv episode delete <id> --confirm <id>` — 删除空壳分集（not_uploaded / transcode_failed）
- `minitv episode publish <id>` — 发布单集
- `minitv episode unpublish <id>` — 下架单集（已签发的播放地址立即失效）
- `minitv episode publish-all --drama ID` — 批量发布某剧里所有待发布/已下架的集
- `minitv episode sync <id>` — 手动对账转码状态
- `minitv episode upload <id> <file>` — 上传一集的视频（TUS 直传，不过 API）
- `minitv episode upload-all --drama ID --dir DIR [--start N] [--title-rule en|cn|none] [--publish]` — 把目录里的视频按自然序灌进一部剧
- `minitv episode subtitle <id> <file>` — 上传字幕（.vtt / .srt，≤2MB）

### tag — 标签库

- `minitv tag list` — 列出全部标签及其剧集数
- `minitv tag create --name-en Name [--name-zh 中文] [--category main|sub]` — 新建标签
- `minitv tag update <id> [--name-en N] [--name-zh N] [--category C] [--status S]` — 修改标签
- `minitv tag delete <id> --confirm <id>` — 删除标签（仍被剧集引用时会 409 并列出引用）

### group — 首页分组与排序

- `minitv group list` — 列出首页分组
- `minitv group dramas <id>` — 列出某个分组里的剧
- `minitv group create --name NAME [--position N]` — 新建首页分组（楼层）
- `minitv group update <id> [--name N] [--position N] [--style S] [--badge B] [--status S]` — 改分组名 / 排序 / 样式 / 角标 / 上下线
- `minitv group add <id> <drama_id> [--confirm]` — 把剧加进分组（不带 --confirm 只返回容量预览）
- `minitv group remove <id> <drama_id>` — 把剧移出分组
- `minitv group move <id> <drama_id> --position N` — 调整组内位置

### export — 批量导出（给模型做上下文）

- `minitv export dramas [--status S] [--out FILE]` — 导出全部剧集为 JSON

### version — 显示 CLI 版本

- `minitv version` — 显示当前安装的 minitv 版本

### schedule — 定时上架

- `minitv schedule apply` — 把已到期的定时排期推上线（后台没有 cron，需要外部触发）

### doctor — 自检：连通 / 身份 / 权限 / 服务端

- `minitv doctor` — 一次性检查网络、密钥、权限与服务端

## 退出码

- `0` 成功
- `2` 用法错误
- `3` 认证 / 权限（过期、撤销、账号禁用、scope 不足）
- `4` 对象不存在
- `5` 状态冲突（重复、非法迁移、限流）
- `6` 上传失败
- `7` 网络错误

## 认证

  认证与密钥

    key 前缀   mk_live_<8 hex>_<43 字符>
    绑定       某个 staff 账号；权限 = min(账号角色, scopes)
    scopes     read / write / delete / billing / staff
    存储       ~/.config/minitv/credentials（0600）
    环境变量   MINITV_API_KEY 优先，适合容器与沙箱

    不提供 --key 参数：密钥走 stdout/交互输入，避免进入 shell 历史与进程列表。
