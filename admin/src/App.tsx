import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Layout, Menu, Space, Tag, Typography } from "antd";
import { api, getToken } from "./api";
import Login from "./pages/Login";
import Dramas from "./pages/Dramas";
import DramaEdit from "./pages/DramaEdit";
import Tags from "./pages/Tags";
import {
  CommentsPage,
  DashboardPage,
  FinancePage,
  HomeOpsPage,
  OpsCenterPage,
  PlansPage,
  RewardsPage,
  UsersPage,
} from "./pages/StagePages";

const { Sider, Header, Content } = Layout;

type Me = { id: number; username: string; display_name: string; role: string };

const ROLE_LABELS: Record<string, string> = {
  ops: "运营",
  finance: "财务",
  admin: "管理员",
};

const ITEMS = [
  { key: "/dashboard", label: "数据看板" },
  { key: "/dramas", label: "剧集管理" },
  { key: "/users", label: "用户管理" },
  { key: "/ops", label: "运营中心" },
  { key: "/home-ops", label: "首页运营" },
  { key: "/rewards", label: "金币与任务" },
  { key: "/comments", label: "评论审核" },
  { key: "/finance", label: "财务订单" },
  { key: "/plans", label: "会员套餐" },
  { key: "/tags", label: "标签库" },
];

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const token = getToken();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    if (!token) return;
    api<Me>("/admin/v1/auth/me")
      .then(setMe)
      .catch(() => setMe(null));
  }, [token]);

  if (!token) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  const selected = ITEMS.find((item) => location.pathname.startsWith(item.key))?.key ?? "/dramas";

  return (
    <Layout style={{ minHeight: "100vh", background: "#f5f6f8" }}>
      <Sider width={208} theme="light">
        <div style={{ color: "#E53B3E", fontSize: 18, fontWeight: 700, padding: "20px 16px 8px" }}>
          MiniTV 后台
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          items={ITEMS}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: "#fff", display: "flex", justifyContent: "flex-end", alignItems: "center", borderBottom: "1px solid #eee" }}>
          <Space>
            {me && (
              <>
                <Typography.Text>{me.display_name || me.username}</Typography.Text>
                <Tag color="red">{ROLE_LABELS[me.role] ?? me.role}</Tag>
              </>
            )}
          </Space>
        </Header>
        <Content style={{ padding: 24, maxWidth: 1440 }}>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/dramas" element={<Dramas />} />
            <Route path="/dramas/:id" element={<DramaEdit />} />
            <Route path="/users" element={<UsersPage />} />
            <Route path="/ops" element={<OpsCenterPage />} />
            <Route path="/home-ops" element={<HomeOpsPage />} />
            <Route path="/rewards" element={<RewardsPage />} />
            <Route path="/comments" element={<CommentsPage />} />
            <Route path="/finance" element={<FinancePage />} />
            <Route path="/plans" element={<PlansPage />} />
            <Route path="/tags" element={<Tags />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}
