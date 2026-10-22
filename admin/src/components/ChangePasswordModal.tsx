import { useState } from "react";
import { Form, Input, Modal, Typography, message } from "antd";
import { api, clearToken, errorText } from "../api";
import { useMe } from "../me";

/**
 * Self-service password change for the signed-in staff account.
 *
 * On success the backend revokes every session of this account — including the
 * one that made the request — so the only sane follow-up is a full reload into
 * the login page. `clearToken()` alone would not re-render the shell, because
 * `App` reads the token during render rather than from state.
 */
export function ChangePasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const me = useMe();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      const path =
        me?.role === "partner" ? "/partner/v1/auth/password" : "/admin/v1/auth/password";
      await api(path, {
        method: "POST",
        body: JSON.stringify({
          old_password: values.old_password,
          new_password: values.new_password,
        }),
      });
      clearToken();
      message.success("密码已修改，请用新密码重新登录");
      window.location.href = "/login";
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title="修改密码"
      okText="保存并重新登录"
      cancelText="取消"
      onCancel={() => {
        form.resetFields();
        onClose();
      }}
      onOk={submit}
      confirmLoading={saving}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          label="原密码"
          name="old_password"
          rules={[{ required: true, message: "请输入原密码" }]}
        >
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item
          label="新密码"
          name="new_password"
          rules={[
            { required: true, message: "请输入新密码" },
            { min: 8, message: "新密码至少 8 位" },
          ]}
        >
          <Input.Password autoComplete="new-password" placeholder="至少 8 位" />
        </Form.Item>
        <Form.Item
          label="确认新密码"
          name="confirm"
          dependencies={["new_password"]}
          rules={[
            { required: true, message: "请再次输入新密码" },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue("new_password") === value) return Promise.resolve();
                return Promise.reject(new Error("两次输入不一致"));
              },
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        修改成功后，该账号所有已登录会话立即失效，需要用新密码重新登录。
      </Typography.Text>
    </Modal>
  );
}
