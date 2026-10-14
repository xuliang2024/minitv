# deployments

发布记录目录（git 管理）。每次发布（R2 / 服务器）自动生成 `releases/{version}.json`，回滚生成 `releases/rollback-*.json`。

不要手改记录文件；发布唯一入口是 `python3 scripts/release.py`，规则见 `docs/release-process.md` 与 `AGENTS.md`。
