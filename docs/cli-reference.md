# minitv 0.1.1 — MiniTV 剧集管理 CLI

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

### episode — 分集：列表、建改删、发布、上传、转码对账

- `minitv episode list --drama ID [--status S] [--page N] [--page-size N]` — 列出某部剧的分集

### tag — 标签库

- `minitv tag list` — 列出全部标签及其剧集数

### group — 首页分组与排序

- `minitv group list` — 列出首页分组
- `minitv group dramas <id>` — 列出某个分组里的剧

### export — 批量导出（给模型做上下文）

- `minitv export dramas [--status S] [--out FILE]` — 导出全部剧集为 JSON

### version — 显示 CLI 版本

- `minitv version` — 显示当前安装的 minitv 版本

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
