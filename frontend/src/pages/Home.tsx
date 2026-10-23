import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, Group, SessionState } from "../api";
import { submit } from "../form";

export function HomePage() {
  const navigate = useNavigate();
  const [groups, setGroups] = useState<Group[]>([]);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [creating, setCreating] = useState(false);
  const [starting, setStarting] = useState<number | null>(null);
  const [notices, setNotices] = useState<Record<number, string>>({});
  const [error, setError] = useState("");

  async function load() {
    setGroups(await api<Group[]>("/api/groups"));
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, []);

  async function onSubmit(event: FormEvent) {
    await submit(event, async () => {
      await api("/api/groups", { method: "POST", body: JSON.stringify({ name, guidance_prompt: prompt }) });
      setName("");
      setPrompt("");
      setCreating(false);
      await load();
    }, setError);
  }

  async function removeGroup(group: Group) {
    if (!window.confirm(`Delete “${group.name}”? Its topics, subtopics, and sessions are removed.`)) return;
    setError("");
    try {
      await api(`/api/groups/${group.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the group");
    }
  }

  async function start(group: Group) {
    setError("");
    setNotices((current) => ({ ...current, [group.id]: "" }));
    setStarting(group.id);
    try {
      const result = await api<SessionState>(`/api/groups/${group.id}/sessions/next`, { method: "POST" });
      if (result.outcome === "nothing_today") {
        setNotices((current) => ({ ...current, [group.id]: "Every topic in this group already has a session today. The next one opens after midnight." }));
        return;
      }
      if (result.outcome === "needs_subtopics") {
        setNotices((current) => ({ ...current, [group.id]: "That topic has no subtopics yet. Add an AI key in Settings, or add subtopics on the topic page." }));
        return;
      }
      if (result.outcome === "no_topics") {
        setNotices((current) => ({ ...current, [group.id]: "Add a topic before starting." }));
        return;
      }
      if (result.session_id) navigate(`/sessions/${result.session_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start");
    } finally {
      setStarting(null);
    }
  }

  return (
    <main className="page stack">
      <h1>Groups</h1>
      <details className="card fold" open={creating} onToggle={(event) => setCreating(event.currentTarget.open)}>
        <summary>Create group</summary>
        <form className="stack" onSubmit={onSubmit}>
          <p className="muted">The prompt tells the tutor how to break topics into subtopics and how to ask.</p>
          <label>Name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="ML & Stats" required /></label>
          <label>How should questions be asked?<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Interview level, include the math, cover common pitfalls." /></label>
          <button type="submit">Create group</button>
        </form>
      </details>
      {error ? <p className="error">{error}</p> : null}
      {groups.length === 0 ? <p>No groups yet.</p> : null}
      <div className="topics">
        {groups.map((group) => (
          <article key={group.id} className="card stack group-card">
            <div className="row">
              <Link className="topic" to={`/groups/${group.id}`}>
                <strong>{group.name}</strong>
                <span className="muted">{group.guidance_prompt ? "Prompt saved" : "No prompt yet"}</span>
              </Link>
              <button className="secondary" type="button" onClick={() => removeGroup(group)}>Delete</button>
            </div>
            <dl className="stats">
              <div><dt>Topics</dt><dd>{group.topic_count ?? 0}</dd></div>
              <div><dt>Subtopics</dt><dd>{group.subtopic_count ?? 0}</dd></div>
              <div><dt>Unanswered</dt><dd>{group.unanswered_count ?? 0}</dd></div>
              <div><dt>Average score</dt><dd>{group.average_score == null ? "—" : group.average_score}</dd></div>
              <div><dt>Sessions</dt><dd>{group.session_count ?? 0}</dd></div>
            </dl>
            {notices[group.id] ? <p className="notice">{notices[group.id]}</p> : null}
            <button type="button" onClick={() => start(group)} disabled={starting === group.id}>
              {starting === group.id ? "Starting…" : group.active_session_id ? "Continue session" : "Start session"}
            </button>
          </article>
        ))}
      </div>
    </main>
  );
}
