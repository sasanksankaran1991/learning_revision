import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Group, SessionState, Topic } from "../api";
import { submit } from "../form";

export function GroupPage() {
  const { groupId } = useParams();
  const navigate = useNavigate();
  const [group, setGroup] = useState<Group | null>(null);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [prompt, setPrompt] = useState("");
  const [importText, setImportText] = useState("");
  const [adding, setAdding] = useState(false);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [notice, setNotice] = useState("");
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");

  async function load() {
    const groups = await api<Group[]>("/api/groups");
    const found = groups.find((item) => String(item.id) === groupId) ?? null;
    setGroup(found);
    setPrompt(found?.guidance_prompt ?? "");
    setTopics(await api<Topic[]>(`/api/groups/${groupId}/topics`));
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, [groupId]);

  async function savePrompt(event: FormEvent) {
    await submit(event, async () => {
      await api(`/api/groups/${groupId}`, { method: "PATCH", body: JSON.stringify({ guidance_prompt: prompt }) });
      setNotice("Prompt saved. Existing subtopic maps stay as they are until you regenerate one.");
    }, setError);
  }

  async function importTopics(event: FormEvent) {
    setWorking("Asking the model for subtopics. This stays until each new topic returns.");
    setNotice("");
    await submit(event, async () => {
      const result = await api<{ created: Topic[]; skipped: string[] }>(`/api/groups/${groupId}/topics/import`, {
        method: "POST",
        body: JSON.stringify({ text: importText }),
      });
      setImportText("");
      setAdding(false);
      const lines = result.created.map(mapLine);
      if (result.skipped.length) lines.push(`Already in the group: ${result.skipped.join(", ")}`);
      setNotice(lines.join("\n") || "Nothing new to add.");
      await load();
    }, setError);
    setWorking("");
  }

  async function start() {
    setError("");
    setNotice("");
    setWorking("Choosing a topic and asking the model for the first question.");
    try {
      const result = await api<SessionState>(`/api/groups/${groupId}/sessions/next`, { method: "POST" });
      if (result.outcome === "nothing_today") {
        setNotice("Every topic in this group already has a session today. The next one opens after midnight.");
        return;
      }
      if (result.outcome === "needs_subtopics") {
        setNotice("That topic has no subtopics yet. Add an AI key in Settings, or add subtopics on the topic page.");
        return;
      }
      if (result.outcome === "no_topics") {
        setNotice("Add a topic before starting.");
        return;
      }
      if (result.session_id) navigate(`/sessions/${result.session_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start");
    } finally {
      setWorking("");
    }
  }

  async function removeGroup() {
    if (!group || !window.confirm(`Delete “${group.name}”? Its topics, subtopics, and sessions are removed.`)) return;
    try {
      await api(`/api/groups/${group.id}`, { method: "DELETE" });
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the group");
    }
  }

  async function removeTopic(topic: Topic) {
    if (!window.confirm(`Delete “${topic.name}”? Its subtopics and sessions are removed.`)) return;
    setError("");
    try {
      await api(`/api/topics/${topic.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the topic");
    }
  }

  if (!group) return <main className="page">{error || "Loading group…"}</main>;

  return (
    <main className="page stack">
      <div className="row">
        <h1>{group.name}</h1>
        <button type="button" className="secondary" onClick={removeGroup}>Delete group</button>
        <button type="button" onClick={start} disabled={working !== ""}>Start next session</button>
      </div>
      {working ? <p className="working">{working}</p> : null}
      {notice ? <p className="notice">{notice}</p> : null}
      {error ? <p className="error">{error}</p> : null}
      <details className="card fold" open={adding} onToggle={(event) => setAdding(event.currentTarget.open)}>
        <summary>Add topics</summary>
        <form className="stack" onSubmit={importTopics}>
          <label>One topic per line<textarea value={importText} onChange={(event) => setImportText(event.target.value)} placeholder={"KNN\nDecision Trees\nProbability"} required /></label>
          <button type="submit" disabled={working !== ""}>{working ? "Working…" : "Add topics"}</button>
        </form>
      </details>
      <details className="card fold" open={editingPrompt} onToggle={(event) => setEditingPrompt(event.currentTarget.open)}>
        <summary>Group prompt</summary>
        <form className="stack" onSubmit={savePrompt}>
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Interview level, include the math, cover common pitfalls." />
          <button type="submit">Save prompt</button>
        </form>
      </details>
      <section className="topics">
        {topics.map((topic) => (
          <article key={topic.id} className="card stack">
            <div className="row">
              <Link className="topic" to={`/topics/${topic.id}`}>
                <strong>{topic.name}</strong>
              </Link>
              <span className={`pill ${topic.status}`}>{label(topic.status)}</span>
              <button className="secondary" type="button" onClick={() => removeTopic(topic)}>Delete</button>
            </div>
            <dl className="stats">
              <div><dt>Mastery</dt><dd>{label(topic.mastery_level)}</dd></div>
              <div><dt>Score</dt><dd>{topic.ewma_score == null ? "—" : Math.round(topic.ewma_score)}</dd></div>
              <div><dt>Sessions</dt><dd>{topic.sessions_completed}</dd></div>
              <div><dt>Subtopics</dt><dd>{topic.subtopic_count ?? 0}</dd></div>
              <div><dt>Unanswered</dt><dd>{topic.unanswered_count ?? 0}</dd></div>
            </dl>
          </article>
        ))}
        {topics.length === 0 ? <p>No topics yet.</p> : null}
      </section>
    </main>
  );
}

function label(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
}

function mapLine(topic: Topic): string {
  const count = topic.subtopic_count ?? 0;
  if (topic.map_status === "ready") return `${topic.name}: ${count} subtopic${count === 1 ? "" : "s"} added.`;
  if (topic.map_status === "needs_api_key") return `${topic.name}: saved. Add an API key before a map can be built.`;
  if (topic.map_status === "budget") return `${topic.name}: saved. The daily token budget is used, so no map was built.`;
  return `${topic.name}: saved. The model call failed, so no subtopics were added.`;
}
