import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AnswerResult, api, Question, SessionState } from "../api";
import { submit } from "../form";
import { Markdown } from "../markdown";

export function SessionPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const [session, setSession] = useState<SessionState | null>(null);
  const [answer, setAnswer] = useState("");
  const [feedback, setFeedback] = useState<AnswerResult | null>(null);
  const [subtopicName, setSubtopicName] = useState("");
  const [savedNote, setSavedNote] = useState("");
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<SessionState>(`/api/sessions/${sessionId}`)
      .then(setSession)
      .catch((err: Error) => setError(err.message));
  }, [sessionId]);

  const question: Question | null = feedback ? null : session?.question ?? null;

  async function send(event: FormEvent) {
    if (!question) return;
    setWorking("The model is reading your answer.");
    await submit(event, async () => {
      const result = await api<AnswerResult>(`/api/sessions/${sessionId}/questions/${question.idx}/answer`, {
        method: "POST",
        body: JSON.stringify({ answer }),
      });
      setFeedback(result);
      setAnswer("");
      setSavedNote("");
      if (session) setSession({ ...session, question: null, session_score: result.session_score });
    }, setError);
    setWorking("");
  }

  function continueNext() {
    if (!feedback?.next_question || !session) return;
    setSession({ ...session, question: feedback.next_question });
    setFeedback(null);
  }

  async function addSubtopic(event: FormEvent) {
    if (!session?.topic_id) return;
    await submit(event, async () => {
      await api(`/api/topics/${session.topic_id}/subtopics`, {
        method: "POST",
        body: JSON.stringify({ name: subtopicName, importance: "core", kind: "concept" }),
      });
      setSubtopicName("");
      setSavedNote("Saved. It will be available from the next session.");
    }, setError);
  }

  async function nextTopic() {
    if (!session?.group_id) return;
    const result = await api<SessionState>(`/api/groups/${session.group_id}/sessions/next`, { method: "POST" });
    if (result.session_id) navigate(`/sessions/${result.session_id}`);
    else setError(result.outcome === "nothing_today" ? "Nothing left today. Come back after midnight." : "No topic is ready.");
  }

  if (!session) return <main className="page">{error || "Loading session…"}</main>;

  return (
    <main className="page stack">
      <p><Link to={session.group_id ? `/groups/${session.group_id}` : "/"}>Back to group</Link></p>
      <p className="muted">{session.topic_name} · question {question?.idx ?? session.answered ?? 0} of {session.question_count}</p>
      {error ? <p className="error">{error}</p> : null}
      {working ? <p className="working">{working}</p> : null}

      {feedback ? (
        <section className="card stack">
          <div className="score">{feedback.evaluation?.score ?? "–"}<span className="muted"> / 5</span></div>
          <Claim title="Correct" items={feedback.evaluation?.correct_claims ?? []} />
          <Claim title="Incomplete" items={feedback.evaluation?.incomplete_claims ?? []} />
          <Claim title="Incorrect" items={feedback.evaluation?.incorrect_claims ?? []} />
          {feedback.evaluation?.misconception ? <p><strong>Misconception. </strong>{feedback.evaluation.misconception}</p> : null}
          {feedback.evaluation?.correction ? <Markdown text={feedback.evaluation.correction} /> : null}
          <form className="stack" onSubmit={addSubtopic}>
            <label>Missing a subtopic?<input value={subtopicName} onChange={(event) => setSubtopicName(event.target.value)} placeholder="Name it and save it for later" /></label>
            <button className="secondary" type="submit">Save subtopic</button>
            {savedNote ? <p className="muted">{savedNote}</p> : null}
          </form>
          {feedback.completed ? (
            <div className="stack">
              <h2>Session score {feedback.session_score}</h2>
              <p>{feedback.mastery_level}{feedback.next_review_at ? ` · next review ${new Date(feedback.next_review_at).toLocaleString()}` : ""}</p>
              {feedback.summary?.recommendation ? <p>{feedback.summary.recommendation}</p> : null}
              <button type="button" onClick={nextTopic}>Next topic</button>
            </div>
          ) : (
            <button type="button" onClick={continueNext}>Continue</button>
          )}
        </section>
      ) : null}

      {!feedback && question ? (
        <form className="card stack" onSubmit={send}>
          {question.is_retest ? <span className="pill">Retest from last time</span> : null}
          <p className="muted">{question.subtopic_name} · {question.qtype}</p>
          <div className="question"><Markdown text={question.text} /></div>
          <label>Your answer<textarea value={answer} onChange={(event) => setAnswer(event.target.value)} /></label>
          <button type="submit" disabled={working !== ""}>{working ? "Checking…" : "Submit answer"}</button>
        </form>
      ) : null}
    </main>
  );
}

function Claim({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <strong>{title}</strong>
      <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
    </div>
  );
}
