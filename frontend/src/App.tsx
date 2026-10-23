import { useEffect, useState } from "react";
import { Link, NavLink, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { api, endSession, getToken, isNative, Me, refreshSession, setToken } from "./api";
import { AdminPage } from "./pages/Admin";
import { GroupPage } from "./pages/Group";
import { HomePage } from "./pages/Home";
import { LoginPage, PasswordPage } from "./pages/Login";
import { SessionPage } from "./pages/Session";
import { SettingsPage } from "./pages/Settings";
import { TopicPage } from "./pages/Topic";
import { UsagePage } from "./pages/Usage";

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    refreshSession()
      .then(async (ok) => {
        if (!ok && !getToken()) return;
        setMe(await api<Me>("/api/me"));
      })
      .catch(() => setToken(null))
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!isNative()) return;
    let remove = () => undefined;
    import("@capacitor/app").then(({ App: NativeApp }) => {
      const handle = NativeApp.addListener("appStateChange", ({ isActive }) => {
        if (isActive) refreshSession().catch(() => undefined);
      });
      remove = () => {
        handle.then((listener) => listener.remove());
      };
    });
    return () => remove();
  }, []);

  if (!ready) return <p className="page">Checking your session…</p>;

  return (
    <Routes>
      <Route path="/login" element={me ? <Navigate to={me.must_change_password ? "/password" : "/"} replace /> : <LoginPage onLogin={setMe} />} />
      <Route path="/password" element={me ? <PasswordPage onDone={() => setMe(null)} /> : <Navigate to="/login" replace />} />
      <Route element={me && !me.must_change_password ? <Shell me={me} onLogout={() => setMe(null)} /> : <Navigate to={me ? "/password" : "/login"} replace />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/groups/:groupId" element={<GroupPage />} />
        <Route path="/topics/:topicId" element={<TopicPage />} />
        <Route path="/sessions/:sessionId" element={<SessionPage />} />
        <Route path="/usage" element={<UsagePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/admin" element={me?.role === "admin" ? <AdminPage /> : <Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

function Shell({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const navigate = useNavigate();

  async function logout() {
    await endSession();
    onLogout();
    navigate("/login");
  }

  return (
    <div className="shell">
      <nav className="nav">
        <Link to="/" className="brand">
          <img src="/favicon.svg" alt="" width="32" height="32" />
          <span>Learning revision</span>
        </Link>
        <div className="nav-links">
          <NavLink to="/" end>Groups</NavLink>
          <NavLink to="/usage">Usage</NavLink>
          <NavLink to="/settings">Settings</NavLink>
          {me.role === "admin" ? <NavLink to="/admin">Admin</NavLink> : null}
        </div>
        <button className="secondary" onClick={logout}>Log out</button>
      </nav>
      <Outlet />
    </div>
  );
}
