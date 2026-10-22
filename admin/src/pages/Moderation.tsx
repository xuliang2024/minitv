import { useEffect, useState } from "react";
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Checkbox,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Segmented,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import {
  ApiOutlined,
  ExperimentOutlined,
  KeyOutlined,
  ReloadOutlined,
  SaveOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { api, errorText } from "../api";
import { useConfirm } from "../confirm";

/**
 * 自动审核配置：用 OpenAI 模型在评论落库前给出 allow / review / block 判定。
 * 密钥可以直接在本页填写（加密后存库、只回显尾号），留空/清除时回退到服务器
 * 环境变量 OPENAI_API_KEY。
 */

type KeyStatus = {
  configured: boolean;
  masked: string;
  source: "database" | "env" | "none";
  name: string;
};

type Settings = {
  enabled: boolean;
  base_url: string;
  model: string;
  categories: string[];
  system_prompt: string;
  approve_threshold: number;
  block_threshold: number;
  fallback_action: "pending" | "approved";
  timeout_seconds: number;
  updated_at: string | null;
  updated_by: number | null;
  key: KeyStatus;
  key_from_database: boolean;
  key_env_configured: boolean;
  default_base_url: string;
  default_categories: string[];
  default_prompt: string;
};

type TestResult = {
  enabled: boolean;
  status: string;
  result: {
    verdict: string;
    score: number;
    reason: string;
    model: string;
    latency_ms: number;
  };
};

const CATEGORY_LABELS: Record<string, string> = {
  sexual: "色情露骨",
  violence: "暴力血腥",
  hate: "仇恨歧视",
  harassment: "骚扰攻击",
  spam: "广告引流",
  privacy: "隐私信息",
  illegal: "违法内容",
};

const MODEL_OPTIONS = [
  { value: "deepseek-flash" },
  { value: "gpt-4o-mini" },
  { value: "gpt-4.1-mini" },
  { value: "gpt-4o" },
];

const VERDICT_META: Record<string, { color: string; label: string }> = {
  allow: { color: "green", label: "通过" },
  review: { color: "orange", label: "转人工" },
  block: { color: "red", label: "拦截" },
};

const STATUS_LABEL: Record<string, string> = {
  approved: "直接发布",
  pending: "进入待审核",
  deleted: "直接删除",
};

const SAMPLE = "Best episode yet! Where can I watch the next one?";

export default function Moderation() {
  const [form] = Form.useForm<Settings>();
  const { confirm, confirmModal } = useConfirm();
  const [data, setData] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testOpen, setTestOpen] = useState(false);
  const [testText, setTestText] = useState(SAMPLE);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [keyOpen, setKeyOpen] = useState(false);
  const [keyDraft, setKeyDraft] = useState("");
  const [keySaving, setKeySaving] = useState(false);
  const [keyChecking, setKeyChecking] = useState(false);
  const [keyCheck, setKeyCheck] = useState<TestResult | null>(null);

  const apply = (next: Settings) => {
    setData(next);
    form.setFieldsValue(next);
  };

  const load = async () => {
    setLoading(true);
    try {
      apply(await api<Settings>("/admin/v1/moderation/settings"));
    } catch (e) {
      message.error(`读取配置失败：${errorText(e)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async (values: Settings) => {
    setSaving(true);
    try {
      const next = await api<Settings>("/admin/v1/moderation/settings", {
        method: "POST",
        body: JSON.stringify({
          enabled: values.enabled,
          base_url: values.base_url,
          model: values.model,
          categories: values.categories ?? [],
          system_prompt: values.system_prompt ?? "",
          approve_threshold: values.approve_threshold,
          block_threshold: values.block_threshold,
          fallback_action: values.fallback_action,
          timeout_seconds: values.timeout_seconds,
        }),
      });
      apply(next);
      message.success(
        next.enabled ? "已保存，新评论将自动判定" : "已保存，自动审核当前关闭",
      );
    } catch (e) {
      message.error(`保存失败：${errorText(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    if (!testText.trim()) {
      message.warning("先输入一段评论文本");
      return;
    }
    setTesting(true);
    try {
      setTestResult(
        await api<TestResult>("/admin/v1/moderation/test", {
          method: "POST",
          body: JSON.stringify({ text: testText }),
        }),
      );
    } catch (e) {
      message.error(`测试失败：${errorText(e)}`);
    } finally {
      setTesting(false);
    }
  };

  /** Only the policy fields, so key changes never carry unsaved prompt edits. */
  const policyBody = (source: Settings) => ({
    enabled: source.enabled,
    base_url: source.base_url,
    model: source.model,
    categories: source.categories ?? [],
    system_prompt: source.system_prompt ?? "",
    approve_threshold: source.approve_threshold,
    block_threshold: source.block_threshold,
    fallback_action: source.fallback_action,
    timeout_seconds: source.timeout_seconds,
  });

  const closeKey = () => {
    setKeyOpen(false);
    setKeyDraft("");
    setKeyCheck(null);
  };

  const submitKey = async (payload: { api_key?: string; clear_api_key?: boolean }) => {
    if (!data) return;
    setKeySaving(true);
    try {
      const next = await api<Settings>("/admin/v1/moderation/settings", {
        method: "POST",
        body: JSON.stringify({ ...policyBody(data), ...payload }),
      });
      apply(next);
      closeKey();
      message.success(
        payload.clear_api_key
          ? "已清除后台密钥，改用服务器环境变量"
          : `密钥已保存，当前生效：${next.key.masked}`,
      );
    } catch (e) {
      message.error(`密钥保存失败：${errorText(e)}`);
    } finally {
      setKeySaving(false);
    }
  };

  const saveKey = () => {
    const value = keyDraft.trim();
    if (value.length < 8) {
      message.warning("密钥至少 8 位，且不能包含空格");
      return;
    }
    void submitKey({ api_key: value });
  };

  const clearKey = () => {
    confirm({
      title: "清除后台保存的密钥",
      content: data?.key_env_configured
        ? "清除后将改用服务器环境变量 OPENAI_API_KEY。"
        : "清除后自动审核将无法调用模型，评论会按降级策略进入待审核。",
      danger: true,
      okText: "清除",
      onOk: () => submitKey({ clear_api_key: true }),
    });
  };

  /** Verify the pasted key with a sample comment; nothing is written. */
  const checkKey = async () => {
    const value = keyDraft.trim();
    if (value.length < 8) {
      message.warning("先粘贴要验证的密钥");
      return;
    }
    setKeyChecking(true);
    setKeyCheck(null);
    try {
      setKeyCheck(
        await api<TestResult>("/admin/v1/moderation/test", {
          method: "POST",
          body: JSON.stringify({ text: SAMPLE, api_key: value }),
        }),
      );
    } catch (e) {
      message.error(`验证失败：${errorText(e)}`);
    } finally {
      setKeyChecking(false);
    }
  };

  const key = data?.key;

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={20}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <Space direction="vertical" size={4}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            自动审核
          </Typography.Title>
          <Typography.Text type="secondary">
            评论提交时调用 OpenAI 模型判定，通过的直接发布，疑似违规的转人工，失败的按降级策略处理。
          </Typography.Text>
        </Space>
        <Space>
          <Button icon={<ExperimentOutlined />} onClick={() => setTestOpen(true)}>
            测试判定
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
        </Space>
      </div>

      {key?.configured ? (
        <Alert
          type="success"
          showIcon
          icon={<KeyOutlined />}
          message={`审核密钥已配置（${key.masked} · ${
            key.source === "database" ? "后台配置，加密存储" : "服务器环境变量"
          }）`}
          description="更换或清除密钥可直接在右侧操作，保存后立即生效、无需重启；完整密钥不会回显。"
          action={
            <Button size="small" icon={<KeyOutlined />} onClick={() => setKeyOpen(true)}>
              更换密钥
            </Button>
          }
          style={{ borderRadius: 8 }}
        />
      ) : (
        <Alert
          type="warning"
          showIcon
          icon={<KeyOutlined />}
          message="未配置自动审核密钥"
          description="自动审核无法调用模型，评论会按降级策略处理。点右侧填写密钥（加密存库），或写入服务器 /opt/minitv/.env 的 OPENAI_API_KEY。"
          action={
            <Button size="small" type="primary" icon={<KeyOutlined />} onClick={() => setKeyOpen(true)}>
              配置密钥
            </Button>
          }
          style={{ borderRadius: 8 }}
        />
      )}

      <Form form={form} layout="vertical" onFinish={save} initialValues={{ enabled: false }}>
        <Space direction="vertical" style={{ width: "100%" }} size={16}>
          <Card
            size="small"
            title={
              <Space size={8}>
                <ApiOutlined />
                <span>模型与开关</span>
              </Space>
            }
            extra={
              <Form.Item name="enabled" valuePropName="checked" noStyle>
                <Switch checkedChildren="启用" unCheckedChildren="停用" />
              </Form.Item>
            }
            style={{ borderRadius: 10 }}
          >
            <Row gutter={[16, 0]}>
              <Col xs={24} md={10}>
                <Form.Item
                  label="模型"
                  name="model"
                  rules={[{ required: true, message: "请填写模型名称" }]}
                  extra="必须是该接口支持 chat/completions 且返回 JSON 的模型"
                >
                  <AutoComplete
                    options={MODEL_OPTIONS}
                    placeholder="deepseek-flash"
                    filterOption={(input, option) =>
                      String(option?.value ?? "").includes(input.toLowerCase())
                    }
                  />
                </Form.Item>
              </Col>
              <Col xs={24} md={14}>
                <Form.Item
                  label="接口地址（Base URL）"
                  name="base_url"
                  rules={[
                    { required: true, message: "请填写接口地址" },
                    { pattern: /^https?:\/\//, message: "必须以 http:// 或 https:// 开头" },
                  ]}
                  extra="兼容 OpenAI 协议的中转地址也可以；末尾不要带 /chat/completions"
                >
                  <Input placeholder="https://codex.apiz.ai/v1" />
                </Form.Item>
              </Col>
              <Col xs={24} md={8}>
                <Form.Item label="超时（秒）" name="timeout_seconds">
                  <InputNumber min={1} max={30} style={{ width: "100%" }} />
                </Form.Item>
              </Col>
              <Col xs={24} md={16}>
                <Form.Item
                  label="模型不可用时的降级"
                  name="fallback_action"
                  extra="超时、限流、密钥失效等情况下，评论落到哪个状态"
                >
                  <Segmented
                    options={[
                      { label: "进入待审核", value: "pending" },
                      { label: "直接发布", value: "approved" },
                    ]}
                  />
                </Form.Item>
              </Col>
            </Row>
          </Card>

          <Card
            size="small"
            title={
              <Space size={8}>
                <SafetyCertificateOutlined />
                <span>审核规则</span>
              </Space>
            }
            extra={
              <Button
                size="small"
                onClick={() => form.setFieldValue("system_prompt", data?.default_prompt ?? "")}
              >
                恢复默认规则
              </Button>
            }
            style={{ borderRadius: 10 }}
          >
            <Form.Item
              label="审核维度"
              name="categories"
              extra="维度会写进系统提示词，模型只在这些范围内判断"
            >
              <Checkbox.Group
                options={(data?.default_categories ?? Object.keys(CATEGORY_LABELS)).map((value) => ({
                  value,
                  label: CATEGORY_LABELS[value] ?? value,
                }))}
              />
            </Form.Item>

            <Form.Item
              label="系统提示词"
              name="system_prompt"
              rules={[{ required: true, message: "系统提示词不能为空" }]}
              extra="必须要求模型返回 JSON：verdict / score / reason；{categories} 会被替换为上方维度"
            >
              <Input.TextArea rows={11} style={{ fontFamily: "var(--font-mono, monospace)" }} />
            </Form.Item>

            <Row gutter={[16, 0]}>
              <Col xs={24} md={12}>
                <Form.Item
                  label="自动通过阈值"
                  name="approve_threshold"
                  extra="allow 且置信度 ≥ 该值时直接发布"
                >
                  <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item
                  label="自动拦截阈值"
                  name="block_threshold"
                  extra="block 且置信度 ≥ 该值时直接删除，否则转人工"
                >
                  <InputNumber min={0} max={1} step={0.05} style={{ width: "100%" }} />
                </Form.Item>
              </Col>
            </Row>

            <Alert
              type="info"
              showIcon
              style={{ borderRadius: 8 }}
              message="判定区间"
              description="allow + 高分 → 已发布；block + 高分 → 已删除；其余（review、低分、模型失败）→ 待审核。人工审核结果始终优先。"
            />
          </Card>
        </Space>

        <Space style={{ marginTop: 20 }}>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
            保存配置
          </Button>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {data?.updated_at
              ? `上次保存：${new Date(data.updated_at).toLocaleString()}`
              : "尚未保存过"}
          </Typography.Text>
        </Space>
      </Form>

      <Modal
        open={testOpen}
        title="测试自动判定"
        okText="开始测试"
        cancelText="关闭"
        confirmLoading={testing}
        onOk={() => void runTest()}
        onCancel={() => setTestOpen(false)}
        width={620}
      >
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          <Typography.Text type="secondary">
            使用已保存的配置调用模型，不会写入评论表。修改模型或提示词后请先保存再测试。
          </Typography.Text>
          <Input.TextArea
            rows={4}
            value={testText}
            onChange={(event) => setTestText(event.target.value)}
            placeholder="输入一条评论文本"
          />
          {testResult && (
            <Card size="small" style={{ borderRadius: 8 }}>
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                <Space wrap>
                  <Tag color={VERDICT_META[testResult.result.verdict]?.color ?? "default"}>
                    {VERDICT_META[testResult.result.verdict]?.label ?? testResult.result.verdict}
                  </Tag>
                  <Typography.Text>置信度 {testResult.result.score}</Typography.Text>
                  <Typography.Text type="secondary">
                    {testResult.result.model} · {testResult.result.latency_ms} ms
                  </Typography.Text>
                  <Tag color={testResult.status === "approved" ? "green" : testResult.status === "deleted" ? "red" : "orange"}>
                    落库状态：{STATUS_LABEL[testResult.status] ?? testResult.status}
                  </Tag>
                </Space>
                <Typography.Text>{testResult.result.reason || "（模型未给出原因）"}</Typography.Text>
                {!testResult.enabled && (
                  <Typography.Text type="warning">
                    自动审核当前处于停用状态，正式评论不会走模型判定。
                  </Typography.Text>
                )}
              </Space>
            </Card>
          )}
        </Space>
      </Modal>

      <Modal
        open={keyOpen}
        title="自动审核密钥"
        width={560}
        onCancel={closeKey}
        footer={
          <Space>
            {data?.key_from_database && (
              <Button danger type="text" onClick={clearKey} loading={keySaving}>
                清除后台密钥
              </Button>
            )}
            <Button onClick={closeKey}>取消</Button>
            <Button
              type="primary"
              onClick={saveKey}
              loading={keySaving}
              disabled={keyDraft.trim().length < 8}
            >
              保存密钥
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            密钥加密后保存在数据库，保存即刻生效、无需重启服务；页面只回显尾号，完整值无法再读出。
          </Typography.Text>
          <Input.Password
            value={keyDraft}
            onChange={(event) => setKeyDraft(event.target.value)}
            placeholder="sk-..."
            autoComplete="new-password"
            prefix={<KeyOutlined style={{ color: "#bfbfbf" }} />}
          />
          <Space wrap size={8}>
            <Button size="small" onClick={() => void checkKey()} loading={keyChecking}>
              验证密钥
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {data?.key_env_configured
                ? "服务器环境变量 OPENAI_API_KEY：已配置（清除后台密钥后自动兜底）"
                : "服务器环境变量 OPENAI_API_KEY：未配置"}
            </Typography.Text>
          </Space>
          {keyCheck && (
            <Space wrap size={8}>
              <Tag color={VERDICT_META[keyCheck.result.verdict]?.color ?? "default"}>
                验证通过 · {VERDICT_META[keyCheck.result.verdict]?.label ?? keyCheck.result.verdict}
              </Tag>
              <Typography.Text type="secondary">
                {keyCheck.result.model} · {keyCheck.result.latency_ms} ms
              </Typography.Text>
            </Space>
          )}
        </Space>
      </Modal>

      {confirmModal}
    </Space>
  );
}
