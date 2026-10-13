import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, Form, Input, message } from "antd";
import { api, setToken } from "../api";

export default function Login() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const res = await api<{ token: string }>("/admin/v1/auth/login", {
        method: "POST",
        body: JSON.stringify(values),
      });
      setToken(res.token);
      navigate("/dramas");
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
        background: "#0C0A0B",
      }}
    >
      <Card title="MiniTV 管理后台登录" style={{ width: 360 }}>
        <Form layout="vertical" onFinish={onFinish} initialValues={{ username: "", password: "" }}>
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
