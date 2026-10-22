import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Avatar, Button, Drawer, Dropdown, Grid, Layout, Menu, Space, Tag, Typography, message } from "antd";
import type { MenuProps } from "antd";
import {
  DownOutlined,
  KeyOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuOutlined,
  MenuUnfoldOutlined,
} from "@ant-design/icons";
import { api, clearToken, getToken } from "./api";
import { MeContext, ROLE_LABELS, type Me } from "./me";
import Login from "./pages/Login";
import Dramas from "./pages/Dramas";
import DramaEdit from "./pages/DramaEdit";
import Tags from "./pages/Tags";
import GroupsPage, { GroupDetailPage } from "./pages/Groups";
import { HomeOpsPage } from "./pages/HomeOps";
import Moderation from "./pages/Moderation";
import Rewards from "./pages/Rewards";
import Staff from "./pages/Staff";
import ApiKeys from "./pages/ApiKeys";
import Suppliers from "./pages/Suppliers";
import PartnerDramas from "./pages/PartnerDramas";
import { ChangePasswordModal } from "./components/ChangePasswordModal";
import {
  CommentsPage,
  DashboardPage,
  FinancePage,
  // OpsCenterPage, // 运营中心入口已关闭，恢复时连同 ITEMS 与 /ops 路由一起放回
  PlansPage,
  UsersPage,
} from "./pages/StagePages";

const { Sider, Header, Content } = Layout;
const { useBreakpoint } = Grid;

const SIDER_WIDTH = 208;
/** lg breakpoint (antd `lg`): below this the sider becomes a drawer so it
 *  stops squeezing the page on tablets and phones. */
const DESKTOP_QUERY = "(min-width: 992px)";

const ITEMS: { key: string; label: string; roles?: string[] }[] = [
  { key: "/dashboard", label: "数据看板", roles: ["ops", "finance", "admin"] },
  { key: "/dramas", label: "剧集管理", roles: ["ops", "admin"] },
  { key: "/groups", label: "首页分组", roles: ["ops", "admin"] },
  { key: "/users", label: "用户管理", roles: ["finance", "admin"] },
  // 运营中心（推送记录）暂不开放：当前只是台账占位，没有真实推送通道，先隐藏入口。
  // 恢复时把下面一行和 /ops 路由一起放回来即可。
  // { key: "/ops", label: "运营中心" },
  { key: "/home-ops", label: "首页运营", roles: ["ops", "admin"] },
  { key: "/rewards", label: "金币与任务", roles: ["ops", "admin"] },
  { key: "/comments", label: "评论审核", roles: ["ops", "admin"] },
  { key: "/moderation", label: "自动审核", roles: ["ops", "admin"] },
  { key: "/finance", label: "财务订单", roles: ["finance", "admin"] },
  { key: "/plans", label: "会员套餐", roles: ["finance", "admin"] },
  { key: "/tags", label: "标签库", roles: ["ops", "admin"] },
  // 员工账号只对管理员开放：运营/财务看到入口也只会拿到 403。
  { key: "/staff", label: "账号管理", roles: ["admin"] },
  // 密钥签发是超管动作，与账号管理同级。
  { key: "/api-keys", label: "API 密钥", roles: ["admin"] },
  { key: "/suppliers", label: "上剧方管理", roles: ["admin"] },
  // 上剧方（partner）只看到自己的剧集；其它入口一律不渲染。
  { key: "/partner/dramas", label: "我的剧集", roles: ["partner"] },
];

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const token = getToken();
  const [me, setMe] = useState<Me | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  // Desktop sidebar can be folded away so wide tables get the whole viewport.
  const [siderCollapsed, setSiderCollapsed] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const screens = useBreakpoint();
  // useBreakpoint() is empty on the first render; fall back to a media query so
  // phones do not flash the desktop shell before hydration settles.
  const [desktopFallback] = useState(
    () => typeof window !== "undefined" && window.matchMedia(DESKTOP_QUERY).matches
  );
  const isDesktop = screens.lg ?? desktopFallback;
  const isNarrow = screens.md === false;

  useEffect(() => {
    if (!token) return;
    api<Me>("/admin/v1/auth/me")
      .then(setMe)
      .catch(() => {
        // Suppliers have no access to /admin/v1; ask their own surface.
        api<Me>("/partner/v1/me")
          .then(setMe)
          .catch(() => setMe(null));
      });
  }, [token]);

  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  /**
   * 退出登录：先让服务端销毁 Redis 会话，再清本地 token。
   * 服务端失败（会话已过期 / Redis 不可用）也必须清掉本地凭据，不能卡住退出。
   * token 清空后 `token` 变 null，整个路由切回登录页。
   */
  const onLogout = async () => {
    setLoggingOut(true);
    try {
      const path = me?.role === "partner" ? "/partner/v1/auth/logout" : "/admin/v1/auth/logout";
      await api(path, { method: "POST" });
    } catch {
      /* 本地凭据仍然要清，退出不能被服务端错误阻塞 */
    } finally {
      clearToken();
      setMe(null);
      setLoggingOut(false);
      message.success("已退出登录");
      navigate("/login", { replace: true });
    }
  };

  const accountMenu = (account: Me): MenuProps => ({
    items: [
      {
        type: "group",
        label: `登录账号：${account.username}（${ROLE_LABELS[account.role] ?? account.role}）`,
        children: [
          {
            key: "password",
            icon: <KeyOutlined />,
            label: "修改密码",
          },
          {
            key: "logout",
            danger: true,
            icon: <LogoutOutlined />,
            label: "退出登录",
          },
        ],
      },
    ],
    onClick: ({ key }) => {
      if (key === "logout") onLogout();
      if (key === "password") setPasswordOpen(true);
    },
  });

  if (!token) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  // A supplier must never land on a staff page (deep link, old bookmark).
  if (me?.role === "partner" && !location.pathname.startsWith("/partner")) {
    return <Navigate to="/partner/dramas" replace />;
  }

  const selectedItem = [...ITEMS]
    .sort((a, b) => b.key.length - a.key.length)
    .find((item) => location.pathname === item.key || location.pathname.startsWith(`${item.key}/`));
  const selected = selectedItem?.key ?? "/dramas";
  const pageTitle = selectedItem?.label ?? "剧集管理";

  // Role-gated entries stay hidden until `/auth/me` resolves, so a non-admin
  // never sees a menu item that would only 403. Unrestricted items pass through.
  const visibleItems = ITEMS.filter(
    (item) => !item.roles || (me !== null && item.roles.includes(me.role))
  );

  const nav = (
    <>
      {!(isDesktop && siderCollapsed) && (
        <div
          style={{ color: "#E53B3E", fontSize: 18, fontWeight: 700, padding: "20px 16px 8px" }}
        >
          {me?.role === "partner" ? "MiniTV 合作方" : "MiniTV 后台"}
        </div>
      )}
      <Menu
        mode="inline"
        style={{ borderInlineEnd: "none" }}
        selectedKeys={[selected]}
        items={visibleItems}
        onClick={({ key }) => {
          navigate(key);
          setNavOpen(false);
        }}
      />
    </>
  );

  return (
    <MeContext.Provider value={me}>
      <Layout style={{ minHeight: "100vh", background: "#f5f6f8" }}>
        {isDesktop ? (
          <Sider
            width={SIDER_WIDTH}
            collapsedWidth={0}
            collapsed={siderCollapsed}
            trigger={null}
            theme="light"
            style={{ overflow: "hidden" }}
          >
            {nav}
          </Sider>
        ) : (
          <Drawer
            open={navOpen}
            placement="left"
            width={SIDER_WIDTH}
            closable={false}
            onClose={() => setNavOpen(false)}
            styles={{ body: { padding: 0 } }}
          >
            {nav}
          </Drawer>
        )}
        <Layout>
          <Header
            style={{
              background: "#fff",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: 12,
              paddingInline: isNarrow ? 12 : 24,
              borderBottom: "1px solid #f0f0f0",
            }}
          >
            <Space size={4}>
              {isDesktop ? (
                <Button
                  type="text"
                  aria-label={siderCollapsed ? "展开导航" : "收起导航"}
                  icon={siderCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                  onClick={() => setSiderCollapsed((value) => !value)}
                />
              ) : (
                <Button
                  type="text"
                  aria-label="打开导航菜单"
                  icon={<MenuOutlined />}
                  onClick={() => setNavOpen(true)}
                />
              )}
              <Typography.Text strong style={{ fontSize: 15 }}>
                {pageTitle}
              </Typography.Text>
            </Space>
            <Space size={8}>
              {me && (
                // 账号区即退出入口：头像 + 名字 + 角色，点开是「退出登录」菜单。
                <Dropdown menu={accountMenu(me)} placement="bottomRight" trigger={["click"]}>
                  <Button
                    type="text"
                    aria-label="账号菜单"
                    loading={loggingOut}
                    style={{ paddingInline: 8, height: 40 }}
                  >
                    <Space size={8}>
                      <Avatar size={26} style={{ background: "#E53B3E", fontSize: 13 }}>
                        {(me.display_name || me.username).slice(0, 1)}
                      </Avatar>
                      <Typography.Text ellipsis style={{ maxWidth: isNarrow ? 88 : 160 }}>
                        {me.display_name || me.username}
                      </Typography.Text>
                      {!isNarrow && (
                        <Tag color="red" style={{ marginInlineEnd: 0 }}>
                          {ROLE_LABELS[me.role] ?? me.role}
                        </Tag>
                      )}
                      <DownOutlined style={{ fontSize: 10, color: "rgba(0,0,0,0.45)" }} />
                    </Space>
                  </Button>
                </Dropdown>
              )}
            </Space>
          </Header>
          {/* Fluid container: the table fills the viewport instead of leaving a
              dead column of whitespace on wide monitors. */}
          <Content style={{ padding: isNarrow ? 12 : 24, width: "100%" }}>
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/dramas" element={<Dramas />} />
              <Route path="/dramas/:id" element={<DramaEdit />} />
              <Route path="/groups" element={<GroupsPage />} />
              <Route path="/groups/:id" element={<GroupDetailPage />} />
              <Route path="/users" element={<UsersPage />} />
              {/* 入口已关闭：/ops 落到下面的 * 通配，自动回数据看板。 */}
              {/* <Route path="/ops" element={<OpsCenterPage />} /> */}
              <Route path="/home-ops" element={<HomeOpsPage />} />
              <Route path="/rewards" element={<Rewards />} />
              <Route path="/comments" element={<CommentsPage />} />
              <Route path="/moderation" element={<Moderation />} />
              <Route path="/finance" element={<FinancePage />} />
              <Route path="/plans" element={<PlansPage />} />
              <Route path="/tags" element={<Tags />} />
              <Route path="/staff" element={<Staff />} />
              <Route path="/suppliers" element={<Suppliers />} />
              <Route path="/partner/dramas" element={<PartnerDramas />} />
              <Route path="/api-keys" element={<ApiKeys />} />
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </Content>
        </Layout>
      </Layout>
      <ChangePasswordModal open={passwordOpen} onClose={() => setPasswordOpen(false)} />
    </MeContext.Provider>
  );
}
