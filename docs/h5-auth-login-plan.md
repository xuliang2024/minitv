# H5 登录方案：邮箱验证码 + Google 授权登录

> 目标：让 h5.minitv.us 具备「登录/注册」闭环，并把点赞、收藏、评论、解锁等必须登录的动作接通。
> 本文只描述方案与改动面，不含实现代码。

## 1. 现状与结论

| 项 | 现状 | 影响 |
|---|---|---|
| H5 登录入口 | 无。`h5/src` 内 `setAuthToken()` 从未被调用 | 所有需要登录的接口（点赞/收藏/评论/解锁）线上实测 401 |
| 邮箱验证码 | 后端已具备：`POST /v1/auth/email/code`、`POST /v1/auth/email/register`、`POST /v1/auth/email/login` | 功能可用，但注册与登录是两套 `purpose`，前端无法预知账号是否存在 |
| Google 登录 | 后端已具备 `POST /v1/auth/google`，验证 ID token / 邮箱合并逻辑完整 | `GOOGLE_CLIENT_ID` 本地与服务器均未配置，线上无按钮可点 |
| 会话 | Redis token（`_issue_session`，30 天），`GET /v1/me` 可自省 | 可直接复用，无需引入 JWT |
| 邮件发件 | 本地 `RESEND_FROM` 为空；服务器 `.env` 连 `RESEND_API_KEY` 都没有 | 线上发码会 503，这是邮箱登录的硬阻塞 |
| H5「我」页 | 仍是 `coming` 占位 | 登录后无落点、无退出入口 |

**一句话结论：后端能力已备齐，缺的是「统一认证语义 + 配置下发 + H5 登录层」。**

## 2. 必须先解决的外部前置（P0，不写代码也要先定）

1. **Resend 发件域名**：`RESEND_FROM` 必须是 Resend 已验证域名下的地址（如 `no-reply@minitv.us`）。当前 API key 所属账号未验证 `minitv.us`，需要先在 Resend 添加域名并配好 SPF/DKIM，再填 `RESEND_FROM`。
2. **服务器环境变量**：`/opt/minitv/.env` 目前没有 `RESEND_API_KEY`、`RESEND_FROM`、`GOOGLE_CLIENT_ID` 三项，需补齐后重启 `minitv-api`。
3. **Google OAuth Client**：Google Cloud Console 建 Web 类型 OAuth Client，Authorized JavaScript origins 填 `https://h5.minitv.us` 与 `http://localhost:5175`，拿到 client id 写入服务器 `.env`。

## 3. 接口契约

### 3.1 新增/调整

**A. 统一邮箱认证（新增）**

```
POST /v1/auth/email/authenticate
{ "email": "a@b.com", "code": "123456" }
→ { "token": "...", "user_id": 12, "public_id": "1234567890", "created": false }
```

- 语义：校验通过后，账号存在则登录，不存在则**同一验证码直接注册**并登录。
- 目的：消灭「注册/登录两个 purpose 两条路」的分裂——用户只看到一个「输入邮箱收码」入口。
- 兼容：保留 `/auth/email/register`、`/auth/email/login` 不动，老客户端不受影响。
- 错误：`400` 验证码错误/过期、`429` 发码超限、`503` 邮件投递失败。

**B. 发码统一（调整默认值）**

```
POST /v1/auth/email/code
{ "email": "a@b.com", "purpose": "login" }   // purpose 缺省 = login
```

- 白名单校验 `purpose ∈ {login, register, bind}`，其它值 422。
- 前端只传 `login`，避免用户在同一邮箱上收到两份不同的码。

**C. 认证能力下发（新增）**

```
GET /v1/auth/providers
→ {
  "email": { "enabled": true },
  "google": { "enabled": true, "client_id": "xxxx.apps.googleusercontent.com" }
}
```

- `enabled` 由环境变量是否存在决定，**不把 client_id 硬编码进前端**，换域名/换项目无需重新构建 H5。
- `enabled=false` 时前端隐藏对应按钮，绝不出现「点了没反应」的假按钮。

### 3.2 复用

| 接口 | 用途 |
|---|---|
| `GET /v1/me` | 启动时校验 token；回填头像、昵称、金币余额 |
| `POST /v1/auth/google` | body `{id_token}`，服务端验签 + 邮箱合并（已实现） |
| `POST /v1/reactions`、`POST /v1/comments` | 登录后即可用，无需改动 |

## 4. 后端改动面

| 文件 | 改动 |
|---|---|
| `backend/app/modules/account/service.py` | 新增 `login_or_register_with_code()`：抽出 `_verify_code()`（校验/过期/尝试次数），登录失败于 `email not registered` 时走 `_new_user()`；`issue_code()` 的 purpose 规范化 |
| `backend/app/modules/public_flow.py` | 新增 `/auth/email/authenticate`、`/auth/providers`；`CodeIn.purpose` 默认改 `login`，加白名单 |
| `backend/app/modules/account/google.py` | 无需改动；`google_configured()` 已可供 providers 使用 |
| `deployments/` / 运维 | 服务器 `.env` 补三项；重启服务；`CORS_ORIGINS` 确认含 `https://h5.minitv.us` |

> 数据表无需迁移：`users`、`third_party_identities`、`verification_codes` 都已存在。

## 5. 前端改动面（h5/）

| 文件 | 改动 |
|---|---|
| `src/auth/AuthProvider.tsx`（新增） | Context：`user / token / status(unknown\|guest\|user) / requireAuth(action) / logout`。启动时若有 token 调 `/v1/me`；401 统一清 token |
| `src/auth/LoginSheet.tsx`（新增） | 底部弹层，两态：① 邮箱输入 + Send code；② 6 位验证码 + 重发倒计时。内嵌 Google 按钮位 |
| `src/auth/google.ts`（新增） | 按需注入 `https://accounts.google.com/gsi/client`；`initialize({client_id, callback})` + `renderButton` 自定义样式；回调拿 `credential` 交后端 |
| `src/api.ts` | 新增 `getMe / sendCode / verifyEmail / googleLogin / getAuthProviders`；`send()` 解析 `detail`，401 触发 `onUnauthorized` 钩子 |
| `src/App.tsx` | 外层包 `AuthProvider`；渲染全局 `LoginSheet`；「我」tab 换成 `MePage` |
| `src/pages/MePage.tsx`（新增） | 未登录：品牌 + 「Sign in」大按钮；已登录：头像/昵称/邮箱/金币 + 退出登录 |
| `src/pages/PlayPage.tsx` | 点赞、收藏用 `requireAuth(() => react(...))` 包裹；未登录点击先弹登录，成功后**自动续跑原动作** |
| `src/pages/ForYouPage.tsx` | 同上，避免两处交互行为不一致 |
| `src/copy.ts` | 补 `sign_in / email / send_code / code_sent / resend_in / verify / continue_with_google / code_error / rate_limited / sign_out / welcome` 等中英文案 |
| `src/styles.css` | 复用 `.sheet` 体系新增 `.auth-*` 样式；底部安全区 `env(safe-area-inset-bottom)` |

**不做的事**：不引入第三方 auth SDK / 状态库，不新增构建依赖（GIS 走 script 按需加载）。

## 6. 关键数据流

```
启动 ── token? ──yes──▶ GET /v1/me ──200──▶ user（保持登录）
                │                  └─401──▶ 清 token → guest
                └─no──▶ guest

未登录点❤️ ──▶ requireAuth(action) ──▶ 打开 LoginSheet
                                    ├─ 邮箱：输入 → /auth/email/code → 验证码 → /auth/email/authenticate → token
                                    └─ Google：GIS credential → /auth/google → token
                     └── 成功后：写 token → /v1/me → 关闭弹层 → 执行 action
```

- token 存 `localStorage.minitv_h5_token`（已有 key），30 天有效。
- 发码按钮 60s 冷却（前端）+ 后端 5 次/小时限流双保险；验证码 10 分钟过期、最多 5 次尝试。
- `purpose` 统一为 `login`，同一邮箱不会出现两份冲突验证码。

## 7. 异常与降级

| 场景 | 处理 |
|---|---|
| Resend 未配置 / 发信失败（503） | 弹层内联提示「邮件发送失败，请稍后再试」，不发假成功 |
| Google client_id 为空 | 不渲染 Google 按钮 |
| GIS 脚本被墙/加载失败 | 按钮进入 disabled，仅保留邮箱登录 |
| 429 发码超限 | 显示剩余冷却时间，不重复请求 |
| token 过期（401） | 静默转 guest，互动前重新要求登录 |
| 老 iOS 无 `navigator.share` | 与登录无关，保持现状 |

## 8. 验收清单

1. 新邮箱：收码 → 验证 → 账号创建 + token 返回（DB `users` 多一行，钱包有注册奖励）。
2. 老邮箱：同一入口收码 → 验证 → 直接登录（不新建用户）。
3. 错误码：错码 400、过期 400、超限 429、邮件失败 503，前端文案与后端一致。
4. Google：`h5.minitv.us` 下点按钮 → 选账号 → 登录成功；同邮箱老用户被合并而非新建（校验 `user_id` 不变）。
5. 持久化：刷新页面仍登录；退出后 `localStorage` 无 token。
6. 互动续跑：未登录点❤️ → 登录 → 点赞落库（`reactions` 有行），计数 +1。
7. 移动端 375×812 与 iPhone 安全区下弹层不遮挡输入框，键盘弹起可滚动。

## 9. 分期

| 阶段 | 内容 | 依赖 |
|---|---|---|
| P0 | Resend 域名验证、服务器环境变量、Google OAuth Client 创建 | 用户提供发件地址 / Google 项目 |
| P1 | 后端统一认证 + `/auth/providers` + 单测 | P0 的 client_id（可先用空值跑通邮箱） |
| P2 | H5 AuthProvider + 邮箱登录弹层 + 「我」页 | P1 |
| P3 | Google 按钮 + 账号合并验证 | P0 的 Google Client |
| P4 | 点赞/收藏/评论接登录续跑 + 真实计数（上一轮未完成项） | P2 |

## 10. 风险

- **发件域名是唯一硬阻塞**：域名未验证前，邮箱登录在生产不可用，只能本地用测试码或 Resend 测试地址验证流程。
- Google 登录在部分内置浏览器（微信/QQ）中会被拦截，需保留邮箱兜底——这正是选择「双通道」而非单一 Google 的原因。
- token 存 localStorage 存在 XSS 面；当前 H5 无用户输入渲染（评论上线后需转义），可接受。
