import { FormEvent, useEffect, useState } from "react";
import { api, Me, UserRow } from "../api";
import { submit } from "../form";

export function AdminPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [error, setError] = useState("");

  async function load() {
    setMe(await api<Me>("/api/me"));
    setUsers(await api<UserRow[]>("/api/admin/users"));
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, []);

  async function create(event: FormEvent) {
    await submit(event, async () => {
      await api("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({ username, password, role, must_change_password: true }),
      });
      setUsername("");
      setPassword("");
      setRole("user");
      await load();
    }, setError);
  }

  async function toggle(user: UserRow) {
    setError("");
    try {
      await api(`/api/admin/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !user.is_active }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the user");
    }
  }

  async function setUserRole(user: UserRow, next: "admin" | "user") {
    setError("");
    try {
      await api(`/api/admin/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ role: next }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the role");
    }
  }

  return (
    <main className="page stack">
      <h1>Users</h1>
      {error ? <p className="error">{error}</p> : null}
      <table>
        <thead><tr><th>Username</th><th>Role</th><th>Status</th><th></th></tr></thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>{user.username}</td>
              <td>{user.role}</td>
              <td>{user.is_active ? "active" : "disabled"}</td>
              <td className="row">
                {user.id === me?.id ? null : (
                  <>
                    <button className="secondary" onClick={() => setUserRole(user, user.role === "admin" ? "user" : "admin")}>
                      {user.role === "admin" ? "Make user" : "Make admin"}
                    </button>
                    <button className="secondary" onClick={() => toggle(user)}>{user.is_active ? "Disable" : "Enable"}</button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="card stack" onSubmit={create}>
        <h2>New user</h2>
        <label>Username<input value={username} onChange={(event) => setUsername(event.target.value)} /></label>
        <label>Temporary password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <label>Role
          <select value={role} onChange={(event) => setRole(event.target.value)}>
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <button type="submit">Create user</button>
      </form>
    </main>
  );
}
