# 后台账号管理整改方案（员工账号：新增 / 修改 / 禁用 / 改密）

> 依据：`main@8ece5be` 代码 + 2026-10-21 线上实测（`admin_users` 3 行、`/admin/v1/auth/*` 仅 3 个端点）。
> 关联：`docs/software-architecture.md` §9 角色矩阵、`docs/admin-membership-ops-plan.md`（同风格的前一份整改方案）。

## 0. 一句话结论

后台的「用户」有两套：侧栏「用户管理」是 **C 端观众**（`users` 表，无密码列，只能看/调账/开会员）；本方案补的是 **后台员工账号**（`admin_users` 表）。后者现在只有登录、登出、看自己三个接口，建号、改角色、停用、改密码全得写 SQL —— 而且线上 `admin` 仍是 `admin-1234`、口令还明文写在前端包里。

本方案补 **6 个接口 + 1 个页面 + 1 个改密弹窗 + 1 个迁移**，删除一律不做（只做禁用），并把默认口令与 seed 复活这两个洞一起堵上。

## 1. 范围界定（先对齐，避免做错东西）

| 对象 | 表 | 有无密码 | 本方案 |
|---|---|---|---|
| 后台员工（运营 / 财务 / 管理员） | `admin_users` | 有（`password_hash`） | **本次范围** |
| C 端观众 | `users` | **没有**（`account/models.py:26` 明确「no password column」，只走邮箱验证码） | 不在范围；侧栏「用户管理」保持现状 |

「改密码」这个需求本身也印证了范围：C 端用户没有密码可改。若后续要给 C 端加「封禁」，另立方案（`users` 目前连 `status` 列都没有）。

命名约定：侧栏新增项叫 **「账号管理」**，路由 `/staff`，避免与已有「用户管理」（C 端）混淆。

## 2. 现状问题

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 1 | 没有员工 CRUD，只有 `login` / `me` / `logout` | `ops/router.py:148-171` | 加人、调岗、停用、离职回收全靠 SQL，无审计 |
| 2 | 没有改密入口 | 同上 | 默认口令从上线用到现在没人改得动（要改得手算 `pbkdf2_sha256`） |
| 3 | 线上 `admin` 仍是 `admin-1234` | 线上实测 2026-10-21 | 任何人拿到用户名即可登录超管后台 |
| 4 | 口令明文打进前端包 | `admin/src/pages/Login.tsx:8` `PRESET_LOGIN` | 打开登录页翻 JS 就能拿到账号密码 |
| 5 | seed 每次部署都跑 | `scripts/deploy_server.sh:142` → `app/seed_admin.py` | 删掉的默认账号下次发布被重新建出来（口令还是默认值） |
| 6 | 角色无 DB 约束 | `ops/models.py:30`（`String(16)`，无 CHECK） | 写错字符串 → 变成「既不能写也不是 admin」的哑角色 |
| 7 | 侧栏菜单不按角色过滤 | `admin/src/App.tsx:45-60` | 财务能看到「剧集管理」等全部入口，点进去才 403 |

## 3. 设计约束（这几条先认同，否则实现必错）

1. **不提供删除**。只 `status: active | disabled`。理由：`audit_logs`、`coin_ledger`、`dramas.updated_by` 等都以 `admin_id` 引用，删行会让历史审计变孤儿；且 `admin_users.username` 唯一，删了之后同名新号会继承旧身份语义。
2. **禁用即时生效**。`resolve_session_token`（`ops/service.py:92`）每次请求都会 `db.get(AdminUser)` 并校验 `status == active`，所以置 `disabled` 后**已登录的 token 下一次请求就 401**，不必等 8h TTL。这一点是现状已有的正确行为，不要改。
3. **不能锁死自己**：任何人都不能禁用/降级自己（`target_id == actor.id` → 400）。
4. **不能没有管理员**：禁用或降级一个 `role=admin & status=active` 的账号前，先数剩余数量，`<= 1` 时 409 拒绝。**实际生效的是第 3 条**（没人能操作自己，所以最后一个管理员永远动不了）；409 这段是兜底，只有将来放开「自己降级」才会走到。
5. **username 不可修改**，只改 `display_name` / `role`。username 是审计与日志里的身份锚点。
6. **口令只以 `pbkdf2_sha256$120000$salt$hash` 存储**，任何接口都不返回 `password_hash`，审计 payload 里也绝不出现明文/密文。
7. **改密必须踢掉旧会话**：Redis 里 `minitv_admin_session:<token> → user_id`，没有反查索引，所以改了密码旧 token 仍能用到 TTL 到期。管理员重置口令、本人改密，两种场景都要主动扫掉该 user 的所有 session，否则「改完密码旧会话还能用」就是漏洞。
8. **禁用不是降级**：`status` 与 `role` 分开操作，避免一次 PATCH 同时改两项时绕过第 4 条校验。

## 4. 数据模型变更（1 个迁移）

`admin_users` 已有 `id / username / password_hash / display_name / role / status / created_at / updated_at`，够用。只需：

```python
# alembic/versions/xxxx_staff_account_hardening.py   (down_revision = "e8c1f4a7b203")
op.add_column("admin_users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
op.create_check_constraint("ck_admin_users_role", "admin_users", "role IN ('ops','finance','admin')")
op.create_check_constraint("ck_admin_users_status", "admin_users", "status IN ('active','disabled')")
```

- `last_login_at`：登录成功时顺手 UPDATE（`ops_service.login` 内），用于「最近登录」列和识别僵尸账号。可空，回滚无需 drop。
- 两个 CHECK 把问题 6 堵死。线上现有 3 行都是合法值，迁移不会失败（迁移前先 `SELECT DISTINCT role, status` 自检一遍）。
- 不加 `must_change_password`；强制首次改密放 P3（见 §9）。

## 5. 接口设计

权限：**全部 `require_admin_role`**（仅 `admin`，`flow_router.py:273`）。`GET /staff` 也仅 admin —— 员工名册不该对运营/财务开放。只读的「我是谁」仍走已有的 `/auth/me`。

### 5.1 列表

```
GET /admin/v1/staff?q=&role=&status=
```

```json
{ "items": [
  { "id": 3, "username": "admin", "display_name": "管理员", "role": "admin",
    "status": "active", "last_login_at": "2026-10-21T01:55:12+00:00",
    "created_at": "2026-10-13T15:09:56+00:00", "updated_at": "..." }
] }
```

`q` 模糊匹配 `username` / `display_name`；按 `id` 升序（员工数量级很小，不分页，上限 200）。

### 5.2 新增

```
POST /admin/v1/staff
{ "username": "ops-lily", "display_name": "李莉", "role": "ops", "password": "…" }
→ 201 { …staff… }
```

- `username`：`^[a-z0-9][a-z0-9._-]{2,31}$`（3–32 位、小写），不合法 422。
- `role` ∈ `ops|finance|admin`，其它 422。
- `password`：8–128 位；等于 username 时 422。
- 重名：先查再插，捕获 `IntegrityError` 兜底 → **409**（不要漏成 500）。
- 审计：`action="staff_create"`, `target_type="admin_user"`, `target_id=新 id`, `payload={"role": …}`。

### 5.3 修改（显示名 / 角色）

```
PATCH /admin/v1/staff/{id}
{ "display_name": "李莉", "role": "finance" }
```

- 两个字段都可选，只更新传入项。
- 降级 admin 时走 §3 第 4 条校验。
- 返回 `{ "before": {...}, "after": {...} }`，前端用它提示「运营 → 财务」。
- 审计：`action="staff_update"`, `payload={"changed": ["role"], "from": {"role":"ops"}, "to": {"role":"finance"}}`。

### 5.4 启用 / 禁用

```
POST /admin/v1/staff/{id}/status
{ "status": "disabled", "reason": "离职 2026-10-21" }
→ { "id": 2, "status": "disabled", "revoked_sessions": 2 }
```

- `status` 只接受 `active` / `disabled`。
- 禁用时：置状态 → `revoke_user_sessions(id)` 扫掉 Redis 会话（返回踢掉的数量，即使 DB 校验已能拦住，清掉更干净）。
- 启用时不踢会话（本来就没有有效会话）。
- 自己 → 400。最后一个 active admin 的 409 校验保留为兜底（见 §3 第 4 条，当前不可达）。
- 审计：`action="staff_disable" | "staff_enable"`, `payload={"reason": …}`（reason 可空，长度 ≤200）。

### 5.5 管理员重置他人口令

```
POST /admin/v1/staff/{id}/password
{ "new_password": "…" }
→ 204
```

- 不需要原密码（这是「重置」）；同样 8–128 位校验。
- 成功后 `revoke_user_sessions(id)`，被重置的人下次请求 401，必须用新口令重登。
- 审计：`action="staff_password_reset"`, `payload={"revoked_sessions": n}` —— **不记口令任何形式**。

### 5.6 本人改密

```
POST /admin/v1/auth/password
{ "old_password": "…", "new_password": "…" }
→ { "revoked_sessions": 1 }
```

- 校验 `verify_password(old_password, user.password_hash)`，失败 **400**（不是 401，避免前端把它当成掉登录）。
- 新旧相同 → 422。
- 成功后踢掉该 user 的**全部**会话（含当前这条），前端收到 200 就 `clearToken()` 跳登录页，用新口令重登。这样不会出现「改完了当前会话还能用、别的设备也在」的中间态。

### 5.7 会话踢除的实现（`ops/service.py` 新增）

```python
def revoke_user_sessions(user_id: int) -> int:
    r = get_redis(); killed = 0
    for key in r.scan_iter(match=SESSION_PREFIX + "*", count=200):
        if r.get(key) == str(user_id):
            r.delete(key); killed += 1
    return killed
```

`scan_iter` 而不是 `keys`（会阻塞 Redis）。当前员工 3 人、会话个位数，全表扫成本可忽略；P3 再换 `minitv_admin_sessions:<user_id>` SET 索引。`RedisError` 统一转 503（复用 `_session_store_503`）。

### 5.8 审计写入

复用 `common/models.py:35` 的 `AuditLog`：

```python
db.add(AuditLog(actor_type="admin", actor_id=actor.id, action="staff_disable",
                target_type="admin_user", target_id=row.id, payload={"reason": reason}))
```

**必须在同一个事务里**与业务改动一起提交（现有 `flow_router` 的调账/改价都是这个写法）。

## 6. 前端设计

### 6.1 新页 `admin/src/pages/Staff.tsx`（路由 `/staff`）

| 列 | 内容 |
|---|---|
| 账号 | `username`（等宽）+ `display_name` |
| 角色 | Tag：运营 / 财务 / 管理员（复用 `App.tsx` 的 `ROLE_LABELS`，建议提到 `me.tsx` 导出） |
| 状态 | Badge：启用绿 / 已禁用灰 |
| 最近登录 | `last_login_at`，空显示「从未登录」 |
| 创建时间 | `created_at` |
| 操作 | 编辑 · 重置密码 · 禁用 / 启用 |

- 顶部：「新增账号」按钮 + 搜索框（`q`，回车触发）。
- **没有删除按钮**，也没有「批量删除」。
- 编辑弹窗：显示名 + 角色下拉；改自己角色时下拉禁用并提示。
- 禁用：走 `confirm.tsx` 的 `useConfirm()`，文案必须写「该账号已登录的会话会立即失效，历史操作记录保留」。
- 重置密码弹窗：前端用 `crypto.getRandomValues` 生成 16 位口令并展示一次（可复制），勾选「已通过安全渠道告知本人」才允许提交；提交后关闭并提示「对方需重新登录」。
- `admin/src/hooks.ts` 新建 `useLoad`（带 `enabled` 开关，非管理员不发请求、不弹 403 toast）与 `formatTime`。**`StagePages.tsx` 里那份同名实现保持原样**：该文件多会话共用，不去动它，新页只引新 hook。

### 6.2 顶部账号菜单加「修改密码」

`App.tsx:111` 的 `accountMenu` 里，在「退出登录」上方加一项「修改密码」，弹窗三字段（原密码 / 新密码 / 确认新密码）。成功后：`clearToken()` → `message.success('密码已修改，请重新登录')` → `navigate('/login')`。

### 6.3 菜单按角色过滤（本次只做这一项）

`ITEMS` 增加可选 `roles?: string[]`，渲染前按 `me.role` 过滤：

```tsx
{ key: "/staff", label: "账号管理", roles: ["admin"] }
```

`me` 尚未加载完（null）时，先用 `roles` 缺省的项兜底，避免菜单闪一下再消失。**其它入口的可见性不动**（财务目前能看见全部菜单，但写操作会 403）——那是独立议题，放 P3，避免一次性改掉所有人的使用习惯。

### 6.4 直达链接兜底

非 admin 手输 `/staff`：后端 403；前端在页面顶部渲染「仅管理员可访问账号管理」空状态，不白屏。

## 7. 配套安全修补（同一批上线）

| 项 | 改动 |
|---|---|
| `admin/src/pages/Login.tsx:8` | 删掉 `PRESET_LOGIN`，或改成 `import.meta.env.DEV ? {...} : undefined`（生产包不带明文） |
| `backend/app/seed_admin.py` | 口令改读 `SEED_ADMIN_PASSWORD`；**只在 `admin_users` 为空时播种**（首次部署），非空直接退出 |
| 同上 | 默认只建 1 个 `admin`；`ops` / `finance` 由管理员在「账号管理」页里加（不再内置弱口令账号） |
| `scripts/deploy_server.sh:142` | 保留调用（空表初始化有用），但脚本内部自判空表，等价于「部署不再复活被删/被禁的账号」 |
| 线上数据 | 用新接口（或一次性 SQL + `hash_password`）把 3 个账号换成强口令；`admin` 口令必须换 |

> 顺序上这条要**先于** §5/§6 上线：漏洞是现网敞开的，不必等页面做完。

## 8. 测试清单（`backend/tests/test_admin_staff.py`）

| # | 用例 | 期望 |
|---|---|---|
| 1 | admin 列员工 | 200，含 3 行，无 `password_hash` 字段 |
| 2 | ops / finance 调任意 staff 接口 | 403（逐个端点参数化） |
| 3 | 新增合法账号 | 201；能立刻用它登录 |
| 4 | 新增重名 | 409 |
| 5 | username 大写 / 过短 / 含空格 | 422 |
| 6 | role 非法（如 `owner`） | 422 |
| 7 | 口令 7 位 / 等于 username | 422 |
| 8 | 改 display_name | 200，`/auth/me` 同步 |
| 9 | 禁用普通账号 → 该账号旧 token 请求 | **401**（关键） |
| 10 | 禁用自己 | 400 |
| 11 | 不能禁用自己 | 400 |
| 12 | 不能改自己的角色（降级/升级都不行） | 400 |
| 13 | 重置他人口令 | 204；旧 token 401；旧口令 401；新口令 200 |
| 14 | 本人改密：原密码错 | 400，且会话仍有效 |
| 15 | 本人改密成功 | 200；当前 token 401；新口令可登录 |
| 16 | 审计写入 | `audit_logs` 有对应 action；`payload` 序列化后不含明文口令子串 |
| 17 | seed：空表 | 建 1 个 admin，口令取 env |
| 18 | seed：非空表 | 不新增、不改已有行 |

前端无测试框架（`admin/package.json` 只有 dev/build），用手工验收代替，见 §10。

## 9. 实施顺序

| 阶段 | 内容 | 交付 |
|---|---|---|
| **P0（当天）** | 清 `PRESET_LOGIN`、seed 改 if-empty + env 口令、线上 3 个账号换强口令 | 单独一次发布，堵住现网洞 |
| **P1** | 迁移（`last_login_at` + 2 个 CHECK）、`ops/service.py` 加 `revoke_user_sessions`、6 个接口、审计、§8 全部用例 | 后端先上，接口可用 curl 验收 |
| **P2** | `Staff.tsx` 页面、`ITEMS.roles` 过滤、账号菜单「修改密码」、`hooks.ts` | 与 P1 同批发布（页面依赖新接口） |
| **P3（可选）** | 会话 SET 索引、登录失败锁定（5 次 / 15 分钟）、`must_change_password` 强制首登改密、按角色过滤全部菜单、员工操作日志查询页 | 独立排期 |

发布纪律：只提交本任务文件、先 commit 再 `python3 scripts/release.py`（admin 走 R2，后端走 `run` 部署）；改价/删剧这类已有 `require_admin_role` 的行为不受影响。

## 10. 验收步骤

```bash
# 1) 登录拿超管 token
curl -s -X POST https://api.minitv.us/admin/v1/auth/login -H 'Content-Type: application/json' \
  -H 'User-Agent: Mozilla/5.0 …' -d '{"username":"admin","password":"<新口令>"}'

# 2) 建号
curl -s -X POST .../admin/v1/staff -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"username":"ops-lily","display_name":"李莉","role":"ops","password":"<16位>"}'

# 3) 用新号登录 → 禁用 → 再请求应 401
# 4) 重置口令 → 旧 token / 旧口令均应失败
# 5) psql 查审计：select action, target_id, payload from audit_logs where target_type='admin_user' order by id desc;
```

UI 走查：`admin.minitv.us/staff` 用 admin 能进、用 ops 登录后菜单里看不到该入口且直达 403；右上角「修改密码」改完自动回登录页。

## 11. 风险与回滚

| 风险 | 处置 |
|---|---|
| 迁移加 CHECK 失败（历史脏值） | 迁移前 `SELECT DISTINCT role, status FROM admin_users` 自检；脏值先 UPDATE 归位再跑 |
| 把自己锁在门外 | 代码层已拦（400/409）；兜底：`update admin_users set status='active', role='admin' where username='admin';`（服务器 psql，只改这一行） |
| 改密踢会话误伤 | 只影响本人/被重置者，重登即可；`revoked_sessions` 会返回数量便于对账 |
| 前端菜单过滤写错导致入口消失 | `roles` 缺省的项不过滤，admin 永远全可见；发布后先用 admin 走一遍 11 个菜单 |
| 回滚 | 迁移只加列/约束，回滚 `alembic downgrade -1` 安全；前端回滚用 `release.py rollback r2 --to <version>` |

## 12. 暂不做（写清楚，避免验收时被追问）

- 删除账号（按需求明确不做）。
- 员工操作日志的**查询界面**（数据已写入 `audit_logs`，页面放 P3）。
- C 端用户封禁 / 注销（`users` 无 `status` 列，另立方案）。
- 2FA、SSO、IP 白名单（P3 以后）。
