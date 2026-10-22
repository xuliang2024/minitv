import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Button, Card, Form, Input, message } from "antd";
import { api, setToken, takeLogoutReason } from "../api";

// 本地开发才预填默认账号，方便反复登录；生产构建里这个常量会被 Vite 直接裁掉，
// 不会再随 JS 下发明文口令（旧版本把 admin/admin-1234 打进了线上包）。
const PRESET_LOGIN = import.meta.env.DEV
  ? { username: "admin", password: "admin-1234" }
  : undefined;

/** 后端 detail.code / 本地原因 -> 一句人话，解释「为什么被踢回来了」。 */
const REASON_TEXT: Record<string, string> = {
  expired: "登录状态已过期（后台会话有效期 8 小时），请重新登录。",
  invalid_session: "登录状态已失效（会话已过期或已在别处退出），请重新登录。",
  missing_credential: "登录状态已失效，请重新登录。",
  account_disabled: "该账号已被停用，请联系平台管理员。",
  key_revoked: "该 API 密钥已被吊销，请用账号密码登录。",
  key_expired: "该 API 密钥已过期，请用账号密码登录。",
  invalid_key: "凭据无效，请用账号密码登录。",
};

/**
 * 上一次为什么被踢回来：只在文档加载时读一次（读完即清），不要放进
 * useState 初始化器 —— StrictMode 会调用两次，第二次就已经读不到了。
 */
const LOGOUT_REASON = takeLogoutReason();

export default function Login() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [loading, setLoading] = useState(false);
  const expired = params.get("expired") === "1";
  const hint = LOGOUT_REASON
    ? REASON_TEXT[LOGOUT_REASON] ?? null
    : expired
      ? REASON_TEXT.expired
      : null;

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const res = await api<{ token: string; user?: { role?: string } }>("/admin/v1/auth/login", {
        method: "POST",
        body: JSON.stringify(values),
      });
      setToken(res.token);
      // 用登录响应里的角色决定落地页：以前固定跳 /dramas，上剧方账号会先到
      // 员工页吃一次 403 才被重定向回来。
      if (res.user?.role === "partner") {
        navigate("/partner/dramas", { replace: true });
        return;
      }
      // 会话过期抛出来的 next 优先，但必须是站内路径。
      const next = params.get("next");
      const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : "/dramas";
      navigate(safeNext, { replace: true });
    } catch (err: any) {
      message.error(err?.status === 401 ? "账号或密码错误" : `登录失败：${err?.message ?? err}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
        background: "#0C0A0B",
      }}
    >
      <Card title="MiniTV 管理后台登录" style={{ width: "100%", maxWidth: 360 }}>
        {hint && (
          <Alert
            type={LOGOUT_REASON === "account_disabled" ? "error" : "warning"}
            showIcon
            message={hint}
            style={{ marginBottom: 16 }}
          />
        )}
        <Form layout="vertical" onFinish={onFinish} initialValues={PRESET_LOGIN}>
          <Form.Item name="username" label="账号" rules={[{ required: true, message: "请输入账号" }]}>
            <Input placeholder="如 ops / admin" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  );
}
