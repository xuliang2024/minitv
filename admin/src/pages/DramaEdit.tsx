import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Card, Form, Image, Input, InputNumber, Select, Space, Tag, Typography, message } from "antd";
import { api } from "../api";

type Drama = {
  id: number;
  title: string;
  synopsis: string;
  cover_url: string;
  status: string;
  free_episode_count: number;
  coin_price_per_episode: number;
  tags: { id: number; name_en: string; name_zh: string }[];
  episode_count: number;
};

type TagOption = { id: number; name_en: string; name_zh: string; status: string; drama_count: number };

const STATUS_LABELS: Record<string, { text: string; color: string }> = {
  draft: { text: "草稿", color: "default" },
  in_review: { text: "审核中", color: "orange" },
  published: { text: "已上架", color: "green" },
  unpublished: { text: "已下架", color: "red" },
};

export default function DramaEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [drama, setDrama] = useState<Drama | null>(null);
  const [tagOptions, setTagOptions] = useState<TagOption[]>([]);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const [d, t] = await Promise.all([
      api<Drama>(`/admin/v1/dramas/${id}`),
      api<{ items: TagOption[] }>("/admin/v1/tags"),
    ]);
    setDrama(d);
    setTagOptions(t.items);
    form.setFieldsValue({
      title: d.title,
      synopsis: d.synopsis,
      cover_url: d.cover_url,
      free_episode_count: d.free_episode_count,
      coin_price_per_episode: d.coin_price_per_episode,
      tag_ids: d.tags.map((x) => x.id),
    });
  }, [id, form]);

  useEffect(() => {
    load().catch((e) => message.error(`加载失败：${e?.message ?? e}`));
  }, [load]);

  const onSave = async (values: any) => {
    setSaving(true);
    try {
      await api(`/admin/v1/dramas/${id}`, { method: "PATCH", body: JSON.stringify(values) });
      message.success("已保存");
      await load();
    } catch (e: any) {
      message.error(`保存失败：${e?.detail ?? e?.message ?? e}`);
    } finally {
      setSaving(false);
    }
  };

  if (!drama) return <Card loading />;

  const statusMeta = STATUS_LABELS[drama.status] ?? { text: drama.status, color: "default" };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Card>
        <Space>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {drama.title}
          </Typography.Title>
          <Tag color={statusMeta.color}>{statusMeta.text}</Tag>
          <Typography.Text type="secondary">
            ID #{drama.id} · {drama.episode_count} 集
          </Typography.Text>
          <Button onClick={() => navigate("/dramas")}>← 返回列表</Button>
        </Space>
      </Card>
      <Card title="基本信息">
        <Form form={form} layout="vertical" onFinish={onSave} style={{ maxWidth: 720 }}>
          <Form.Item label="封面（3:4，URL 登记）" name="cover_url" style={{ maxWidth: 360 }}>
            <Input placeholder="https://cdn.example/cover.jpg" />
          </Form.Item>
          {form.getFieldValue("cover_url") && (
            <Image
              src={form.getFieldValue("cover_url")}
              width={90}
              height={120}
              style={{ objectFit: "cover", borderRadius: 8, marginBottom: 16 }}
              preview={false}
              fallback="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciLz4="
            />
          )}
          <Form.Item
            label="剧名"
            name="title"
            rules={[{ required: true, message: "剧名必填" }]}
            style={{ maxWidth: 360 }}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item label="简介" name="synopsis">
            <Input.TextArea rows={4} maxLength={20000} showCount />
          </Form.Item>
          <Form.Item label="标签" name="tag_ids" style={{ maxWidth: 480 }}>
            <Select
              mode="multiple"
              placeholder="选择标签"
              options={tagOptions.map((t) => ({
                value: t.id,
                label: `${t.name_zh || t.name_en}（${t.name_en}）`,
              }))}
            />
          </Form.Item>
          <Space size="large">
            <Form.Item label="免费集数" name="free_episode_count">
              <InputNumber min={0} />
            </Form.Item>
            <Form.Item label="单集金币" name="coin_price_per_episode">
              <InputNumber min={0} />
            </Form.Item>
          </Space>
          <Button type="primary" htmlType="submit" loading={saving}>
            保存
          </Button>
        </Form>
      </Card>
    </Space>
  );
}
