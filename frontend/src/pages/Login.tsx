import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, API_ORIGIN, getServerUrl, isNative, Me, setServerUrl, setToken, storeRefreshToken } from "../api";
import { submit } from "../form";

export function LoginPage({ onLogin }: { onLogin: (me: Me) => void }) {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [server, setServer] = useState(getServerUrl());
  const [error, setError] = useState("");
  const native = isNative();

  async function onSubmit(event: FormEvent) {
    await submit(event, async () => {
      if (native) {
        if (!server.trim()) throw new Error(`Enter the server address, such as ${API_ORIGIN}`);
        await setServerUrl(server);
      }
      const data = await api<{ access_token: string; must_change_password: boolean; refresh_token?: string | null }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password, client: native ? "apk" : "web" }),
      });
      setToken(data.access_token);
      if (data.refresh_token) await storeRefreshToken(data.refresh_token);
      const me = await api<Me>("/api/me");
      onLogin(me);
      navigate(data.must_change_password ? "/password" : "/");
    }, setError);
  }

  return (
    <form className="auth stack" onSubmit={onSubmit}>
      <h1>Learning revision</h1>
      <p className="muted">{native ? "The app loads your groups and sessions from this server." : "Sign in to continue the session on this browser."}</p>
      {native ? <label>Server<input value={server} onChange={(event) => setServer(event.target.value)} placeholder={API_ORIGIN} autoCapitalize="none" autoCorrect="off" /></label> : null}
      <label>Username<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" /></label>
      <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" /></label>
      {error ? <p className="error">{error}</p> : null}
      <button type="submit">Sign in</button>
    </form>
  );
}

export function PasswordPage({ onDone }: { onDone: () => void }) {
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState("");

  async function onSubmit(event: FormEvent) {
    await submit(event, async () => {
      await api("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      setToken(null);
      await storeRefreshToken(null);
      onDone();
      navigate("/login");
    }, setError);
  }

  return (
    <form className="auth stack" onSubmit={onSubmit}>
      <h1>Change password</h1>
      <p className="muted">After this change you sign in again. The new password needs at least 10 characters.</p>
      <label>Current password<input type="password" value={current} onChange={(event) => setCurrent(event.target.value)} /></label>
      <label>New password<input type="password" value={next} onChange={(event) => setNext(event.target.value)} /></label>
      {error ? <p className="error">{error}</p> : null}
      <button type="submit">Save password</button>
    </form>
  );
}
