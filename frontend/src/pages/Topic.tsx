import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, SessionState, Subtopic, TopicDetail } from "../api";
import { submit } from "../form";

export function TopicPage() {
  const { topicId } = useParams();
  const navigate = useNavigate();
  const [topic, setTopic] = useState<TopicDetail | null>(null);
  const [name, setName] = useState("");
  const [importance, setImportance] = useState("core");
  const [error, setError] = useState("");

  async function load() {
    setTopic(await api<TopicDetail>(`/api/topics/${topicId}`));
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, [topicId]);

  async function addSubtopic(event: FormEvent) {
    await submit(event, async () => {
      await api(`/api/topics/${topicId}/subtopics`, {
        method: "POST",
        body: JSON.stringify({ name, importance, kind: "concept" }),
      });
      setName("");
      await load();
    }, setError);
  }

  async function askSubtopic(row: Subtopic) {
    setError("");
    try {
      const started = await api<SessionState>(`/api/topics/${topicId}/subtopics/${row.id}/practice`, { method: "POST" });
      if (started.session_id) navigate(`/sessions/${started.session_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not ask that subtopic");
    }
  }

  async function removeSubtopic(row: Subtopic) {
    if (!window.confirm(`Delete “${row.name}”? Past answers stay, but this subtopic will not be asked again.`)) return;
    setError("");
    try {
      await api(`/api/topics/${topicId}/subtopics/${row.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the subtopic");
    }
  }

  async function removeTopic() {
    if (!topic || !window.confirm(`Delete “${topic.name}”? Its subtopics and sessions are removed.`)) return;
    try {
      await api(`/api/topics/${topic.id}`, { method: "DELETE" });
      navigate(`/groups/${topic.group_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the topic");
    }
  }

  if (!topic) return <main className="page">{error || "Loading topic…"}</main>;

  return (
    <main className="page stack">
      <p><Link to={`/groups/${topic.group_id}`}>Back to group</Link></p>
      <div className="row">
        <h1>{topic.name}</h1>
        <button className="secondary" type="button" onClick={removeTopic}>Delete topic</button>
      </div>
      <p className="muted">{topic.mastery_level} · {topic.sessions_completed} sessions{topic.next_review_at ? ` · next review ${new Date(topic.next_review_at).toLocaleString()}` : ""}</p>
      {error ? <p className="error">{error}</p> : null}
      <section className="card">
        <h2>Subtopics</h2>
        {topic.subtopics.length === 0 ? <p>No subtopics yet. Add an AI key and create the topic again, or add one below.</p> : null}
        <table>
          <thead>
            <tr><th>Subtopic</th><th>Kind</th><th>Tries</th><th>Weakness</th><th>UCB</th><th></th></tr>
          </thead>
          <tbody>
            {topic.subtopics.map((row: Subtopic) => (
              <tr key={row.id}>
                <td>
                  {row.name} {row.retest_next ? <span className="pill">retest</span> : null}
                  {row.untested ? <span className="pill">new</span> : null}
                  <div className="muted">{row.description}</div>
                </td>
                <td>{row.importance} · {row.kind}</td>
                <td>{row.attempts}</td>
                <td>{row.weakness == null ? "—" : row.weakness.toFixed(2)}</td>
                <td>{row.untested ? "untested" : row.ucb == null ? "—" : row.ucb.toFixed(2)}</td>
                <td>
                  <div className="row">
                    <button type="button" onClick={() => askSubtopic(row)}>Ask</button>
                    <button className="secondary" type="button" onClick={() => removeSubtopic(row)}>Delete</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <form className="card stack" onSubmit={addSubtopic}>
        <h2>Add a subtopic</h2>
        <label>Name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>Importance
          <select value={importance} onChange={(event) => setImportance(event.target.value)}>
            <option value="core">core</option>
            <option value="supporting">supporting</option>
          </select>
        </label>
        <button type="submit">Save for later sessions</button>
      </form>
    </main>
  );
}
