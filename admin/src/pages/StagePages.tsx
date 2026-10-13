import { useEffect, useState } from "react";
import { Button, Card, Form, Input, InputNumber, Select, Space, Table, Tag, Typography, message } from "antd";
import { api } from "../api";

function useLoad<T>(path: string, fallback: T) {
  const [data, setData] = useState<T>(fallback);
  const reload = () => api<T>(path).then(setData).catch((e) => message.error(String(e?.detail ?? e?.message ?? e)));
  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);
  return { data, reload };
}

export function DashboardPage() {
  const { data } = useLoad<any>("/admin/v1/dashboard", {});
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Typography.Title level={4}>数据看板 · {data.timezone}</Typography.Title>
      <Space>
        <Card title="活跃用户">{data.active_users ?? "-"}</Card>
        <Card title="新增用户">{data.new_users ?? "-"}</Card>
        <Card title="营收（分）">{data.revenue_cents ?? "-"}</Card>
        <Card title="完播率（万分比）">{data.completion_rate ?? "-"}</Card>
      </Space>
      <Table rowKey="day" dataSource={data.trend ?? []} pagination={false} columns={[{ title: "日期", dataIndex: "day" }, { title: "活跃", dataIndex: "active_users" }, { title: "营收", dataIndex: "revenue_cents" }]} />
    </Space>
  );
}

export function UsersPage() {
  const { data, reload } = useLoad<{ items: any[] }>("/admin/v1/users", { items: [] });
  const [form] = Form.useForm();
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Typography.Title level={4}>用户管理</Typography.Title>
      <Table rowKey="id" dataSource={data.items} pagination={false} columns={[
        { title: "昵称", dataIndex: "display_name" },
        { title: "ID", dataIndex: "public_id" },
        { title: "金币", dataIndex: "coins" },
        { title: "会员", dataIndex: "member", render: (v) => (v ? "有效" : "无") },
        { title: "注册时间", dataIndex: "created_at" },
      ]} />
      <Card title="调整金币（仅管理员）">
        <Form form={form} layout="inline" onFinish={async (values) => {
          try {
            await api(`/admin/v1/users/${values.user_id}/coins`, { method: "POST", body: JSON.stringify({ delta: values.delta, reason: values.reason }) });
            message.success("已入账");
            reload();
          } catch (e: any) {
            message.error(String(e?.detail ?? e.message));
          }
        }}>
          <Form.Item name="user_id" rules={[{ required: true }]}><InputNumber placeholder="用户 ID" /></Form.Item>
          <Form.Item name="delta" rules={[{ required: true }]}><InputNumber placeholder="变动" /></Form.Item>
          <Form.Item name="reason" rules={[{ required: true }]}><Input placeholder="原因" /></Form.Item>
          <Button htmlType="submit" type="primary">确认调账</Button>
        </Form>
      </Card>
    </Space>
  );
}

export function HomeOpsPage() {
  const [tabs, setTabs] = useState("Discover,Ranking,New,English,Asian,Free");
  return (
    <Space direction="vertical">
      <Typography.Title level={4}>首页运营</Typography.Title>
      <Input.TextArea value={tabs} onChange={(e) => setTabs(e.target.value)} rows={3} />
      <Button type="primary" onClick={async () => {
        const codes = tabs.split(",").map((s) => s.trim()).filter(Boolean);
        for (let i = 0; i < codes.length; i++) {
          const code = codes[i];
          await api(`/admin/v1/tabs?code=${code}&title_en=${code}&position=${i}`, { method: "POST" });
        }
        message.success("Tab 顺序已保存");
      }}>保存 Tab 顺序</Button>
    </Space>
  );
}

export function RewardsPage() {
  const [amounts, setAmounts] = useState("30,30,50,50,30,60,90");
  return (
    <Card title="金币与任务（次日生效）">
      <Space>
        <Input value={amounts} onChange={(e) => setAmounts(e.target.value)} style={{ width: 360 }} />
        <Button type="primary" onClick={async () => {
          const parsed = amounts.split(",").map((n) => Number(n.trim()));
          const row = await api<any>("/admin/v1/rewards/config", { method: "POST", body: JSON.stringify({ code: "checkin", amounts: parsed }) });
          message.success(`将于 ${row.effective_on} 生效`);
        }}>保存</Button>
      </Space>
    </Card>
  );
}

export function CommentsPage() {
  const { data, reload } = useLoad<{ items: any[] }>("/admin/v1/comments", { items: [] });
  return (
    <Table rowKey="id" dataSource={data.items} pagination={false} columns={[
      { title: "内容", dataIndex: "body" },
      { title: "剧集", dataIndex: "drama_id" },
      { title: "用户", dataIndex: "user_id" },
      { title: "状态", dataIndex: "status" },
      { title: "操作", render: (_, row) => (
        <Space>
          <Button size="small" onClick={async () => { await api(`/admin/v1/comments/${row.id}`, { method: "POST", body: JSON.stringify({ status: "approved" }) }); reload(); }}>通过</Button>
          <Button size="small" danger onClick={async () => { await api(`/admin/v1/comments/${row.id}`, { method: "POST", body: JSON.stringify({ status: "deleted" }) }); reload(); }}>删除</Button>
        </Space>
      ) },
    ]} />
  );
}

export function FinancePage() {
  const { data } = useLoad<{ items: any[] }>("/admin/v1/finance/orders", { items: [] });
  return <Table rowKey="order_no" dataSource={data.items} pagination={false} columns={[{ title: "订单号", dataIndex: "order_no" }, { title: "用户", dataIndex: "user_id" }, { title: "金额（分）", dataIndex: "amount_cents" }, { title: "状态", dataIndex: "status" }, { title: "时间", dataIndex: "created_at" }]} />;
}

export function PlansPage() {
  const { data, reload } = useLoad<{ items: any[] }>("/admin/v1/plans", { items: [] });
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {(data.items ?? []).map((plan) => (
        <Card key={plan.code} title={`${plan.name_zh || plan.name_en} · $${(plan.price_cents / 100).toFixed(2)}`}>
          <Button onClick={async () => {
            const next = Number(prompt("新价格（分）", String(plan.price_cents)));
            if (!next) return;
            await api(`/admin/v1/plans/${plan.code}/price`, { method: "POST", body: JSON.stringify({ price_cents: next, benefits: plan.benefits }) });
            message.success("已写入新版本");
            reload();
          }}>修改价格</Button>
          {plan.benefits?.best ? <Tag color="red">Best</Tag> : null}
        </Card>
      ))}
    </Space>
  );
}

export function OpsCenterPage() {
  const [title, setTitle] = useState("");
  const { data, reload } = useLoad<{ items: any[] }>("/admin/v1/pushes", { items: [] });
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Typography.Title level={4}>运营中心</Typography.Title>
      <Space>
        <Input placeholder="推送标题" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Button type="primary" onClick={async () => { await api(`/admin/v1/pushes?title=${encodeURIComponent(title)}`, { method: "POST" }); setTitle(""); reload(); }}>记录推送</Button>
      </Space>
      <Table rowKey="id" dataSource={data.items} pagination={false} columns={[{ title: "内容", dataIndex: "title" }, { title: "送达", dataIndex: "delivered_count" }, { title: "时间", dataIndex: "sent_at" }]} />
    </Space>
  );
}

export function EpisodesPanel({ dramaId }: { dramaId: string }) {
  const [status, setStatus] = useState<string | undefined>();
  const { data, reload } = useLoad<{ items: any[] }>(`/admin/v1/dramas/${dramaId}/episodes?page_size=100${status ? `&status=${status}` : ""}`, { items: [] });
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space>
        <Select allowClear placeholder="状态" style={{ width: 160 }} value={status} onChange={setStatus} options={["not_uploaded", "transcoding", "transcode_failed", "in_review", "published"].map((v) => ({ value: v, label: v }))} />
        <Button onClick={reload}>筛选</Button>
        <Button onClick={async () => { await api(`/admin/v1/dramas/${dramaId}/episodes/bulk`, { method: "POST", body: JSON.stringify({ count: 64 }) }); reload(); }}>登记 64 集</Button>
      </Space>
      <Table rowKey="id" dataSource={data.items} pagination={{ pageSize: 20 }} columns={[
        { title: "集", dataIndex: "episode_number" },
        { title: "标题", dataIndex: "title" },
        { title: "状态", dataIndex: "status" },
        { title: "操作", render: (_, row) => (
          <Button size="small" onClick={async () => {
            await api(`/admin/v1/episodes/${row.id}/upload-session`, { method: "POST", body: JSON.stringify({ filename: "ep.mp4", mime: "video/mp4", size_bytes: 1024, width: 720, height: 1280 }) });
            message.success("已创建直传会话，文件不经过 API");
            reload();
          }}>上传</Button>
        ) },
      ]} />
    </Space>
  );
}

export function GroupsPanel({ dramaId }: { dramaId: string }) {
  const { data, reload } = useLoad<{ items: any[] }>("/admin/v1/groups", { items: [] });
  const [name, setName] = useState("");
  return (
    <Space direction="vertical">
      <Space>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="分组标题" />
        <Button onClick={async () => { await api("/admin/v1/groups", { method: "POST", body: JSON.stringify({ name, position: data.items.length }) }); setName(""); reload(); }}>新建分组</Button>
      </Space>
      {data.items.map((group) => (
        <Card key={group.id} size="small" title={`${group.name} · 排序 ${group.position}`}>
          <Button onClick={async () => { await api(`/admin/v1/groups/${group.id}/dramas`, { method: "POST", body: JSON.stringify({ drama_id: Number(dramaId), confirm: true }) }); message.success("已加入"); }}>加入本组</Button>
        </Card>
      ))}
    </Space>
  );
}

export function PublishPanel({ dramaId, status }: { dramaId: string; status: string }) {
  const act = async (to_status: string) => {
    await api(`/admin/v1/dramas/${dramaId}/status`, { method: "POST", body: JSON.stringify({ to_status }) });
    message.success(to_status);
    window.location.reload();
  };
  return (
    <Space direction="vertical">
      <Typography.Text>当前状态：{status}</Typography.Text>
      <Space>
        <Button onClick={() => act("in_review")}>提交审核</Button>
        <Button type="primary" onClick={() => act("published")}>上架</Button>
        <Button danger onClick={() => act("unpublished")}>下架</Button>
      </Space>
      <Button danger type="primary" onClick={async () => {
        const preview = await fetch(`/admin/v1/dramas/${dramaId}/delete`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${localStorage.getItem("minitv_admin_token")}` }, body: JSON.stringify({ confirm: false }) });
        const body = await preview.json();
        if (!window.confirm(`删除不可恢复。影响：${JSON.stringify(body.detail?.impact ?? body)}`)) return;
        await api(`/admin/v1/dramas/${dramaId}/delete`, { method: "POST", body: JSON.stringify({ confirm: true }) });
        window.location.href = "/dramas";
      }}>删除剧集</Button>
    </Space>
  );
}
