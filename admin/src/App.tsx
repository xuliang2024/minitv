import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { Layout, Menu, Space, Tag, Typography } from "antd";
import { api, getToken } from "./api";
import Login from "./pages/Login";
import Dramas from "./pages/Dramas";
import DramaEdit from "./pages/DramaEdit";
import Tags from "./pages/Tags";

const { Sider, Header, Content } = Layout;

type Me = { id: number; username: string; display_name: string; role: string };

const ROLE_LABELS: Record<string, string> = {
  ops: "运营",
  finance: "财务",
  admin: "管理员",
};

export default function App() {
  const navigate = useNavigate();
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

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider width={208}>
        <div style={{ color: "#E53B3E", fontSize: 18, fontWeight: 600, padding: "20px 16px 8px" }}>
          MiniTV 后台
        </div>
        <Menu
          mode="inline"
          defaultSelectedKeys={["/dramas"]}
          items={[
            { key: "/dramas", label: "剧集管理" },
            { key: "/tags", label: "标签库" },
          ]}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ display: "flex", justifyContent: "flex-end", alignItems: "center" }}>
          <Space>
            {me && (
              <>
                <Typography.Text>{me.display_name || me.username}</Typography.Text>
                <Tag color="red">{ROLE_LABELS[me.role] ?? me.role}</Tag>
              </>
            )}
          </Space>
        </Header>
        <Content style={{ padding: 24 }}>
          <Routes>
            <Route path="/" element={<Navigate to="/dramas" replace />} />
            <Route path="/dramas" element={<Dramas />} />
            <Route path="/dramas/:id" element={<DramaEdit />} />
            <Route path="/tags" element={<Tags />} />
            <Route path="*" element={<Navigate to="/dramas" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}
