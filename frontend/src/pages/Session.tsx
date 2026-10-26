import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AnswerResult, api, Question, SessionState } from "../api";
import { submit } from "../form";
import { Markdown } from "../markdown";

export function SessionPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const [session, setSession] = useState<SessionState | null>(null);
  const [answer, setAnswer] = useState("");
  const [photo, setPhoto] = useState<Photo | null>(null);
  const [telegramCode, setTelegramCode] = useState<string | null>(null);
  const [telegramPhotos, setTelegramPhotos] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const askedSubtopic = useRef<number | null>(null);
  const [feedback, setFeedback] = useState<AnswerResult | null>(null);
  const [subtopicName, setSubtopicName] = useState("");
  const [savedNote, setSavedNote] = useState("");
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<SessionState>(`/api/sessions/${sessionId}`)
      .then((loaded) => {
        setSession(loaded);
        if (loaded.pool === "practice" && loaded.practice_result) {
          setFeedback({
            evaluation: loaded.practice_result,
            next_question: null,
            completed: true,
            counts: false,
            session_score: null,
            mastery_level: "",
            next_review_at: null,
          });
        }
      })
      .catch((err: Error) => setError(err.message));
  }, [sessionId]);

  const question: Question | null = feedback ? null : session?.question ?? null;
  if (session?.question?.subtopic_id) askedSubtopic.current = session.question.subtopic_id;

  useEffect(() => {
    if (question?.photo_code) setTelegramCode(question.photo_code);
  }, [question?.idx, question?.photo_code]);

  useEffect(() => {
    if (!telegramCode || !sessionId || !question || feedback) return;
    const idx = question.idx;
    let stop = false;
    const timer = window.setInterval(() => {
      api<TelegramPhoto>(`/api/sessions/${sessionId}/questions/${idx}/telegram-photo`)
        .then((status) => {
          if (stop) return;
          setTelegramPhotos(status.received || 0);
          if (status.status !== "graded" || !status.result) return;
          setFeedback(status.result);
          setTelegramCode(null);
          setTelegramPhotos(0);
          setAnswer("");
          setPhoto(null);
          setSession((current) => (current ? { ...current, question: null, session_score: status.result?.session_score } : current));
        })
        .catch(() => undefined);
    }, 3000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [telegramCode, sessionId, question?.idx, feedback]);

  async function send(event: FormEvent) {
    if (!question) return;
    setWorking(photo ? "The model is reading your photo." : "The model is reading your answer.");
    await submit(event, async () => {
      const result = await api<AnswerResult>(`/api/sessions/${sessionId}/questions/${question.idx}/answer`, {
        method: "POST",
        body: JSON.stringify({
          answer: answer.trim(),
          ...(photo ? { image: { media_type: photo.media_type, data: photo.data } } : {}),
        }),
      });
      setFeedback(result);
      setAnswer("");
      setPhoto(null);
      setSavedNote("");
      if (session) setSession({ ...session, question: null, session_score: result.session_score });
    }, setError);
    setWorking("");
  }

  async function skip() {
    if (!question) return;
    if (!window.confirm("Skip this question? It will be scored 0.")) return;
    setWorking("Skipping this question.");
    setError("");
    try {
      const result = await api<AnswerResult>(`/api/sessions/${sessionId}/questions/${question.idx}/skip`, { method: "POST" });
      setFeedback(result);
      setAnswer("");
      setPhoto(null);
      setTelegramCode(null);
      setTelegramPhotos(0);
      if (session) setSession({ ...session, question: null, session_score: result.session_score });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not skip this question");
    }
    setWorking("");
  }

  function continueNext() {
    if (!feedback?.next_question || !session) return;
    setTelegramCode(feedback.next_question.photo_code ?? null);
    setTelegramPhotos(0);
    setSession({ ...session, question: feedback.next_question });
    setFeedback(null);
  }

  async function askTelegram() {
    if (!question) return;
    setError("");
    setWorking("Sending the answer code to Telegram.");
    try {
      const status = await api<TelegramPhoto>(`/api/sessions/${sessionId}/questions/${question.idx}/telegram-photo`, { method: "POST" });
      setTelegramCode(status.code);
      setTelegramPhotos(status.received || 0);
      if (!status.delivered) {
        setError("The answer code is ready. Telegram did not get the message, so click Send on Telegram again. Put the code in the photo caption.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create an answer code");
    }
    setWorking("");
  }

  async function onPhoto(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError("");
    try {
      setPhoto(await shrinkPhoto(file));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not read that photo");
    }
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

  const practice = session?.pool === "practice";

  async function askAgain() {
    const subtopicId = askedSubtopic.current;
    if (!session?.topic_id || !subtopicId) return;
    const started = await api<SessionState>(`/api/topics/${session.topic_id}/subtopics/${subtopicId}/practice`, { method: "POST" });
    if (started.session_id) navigate(`/sessions/${started.session_id}`);
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
      <p><Link to={practice && session.topic_id ? `/topics/${session.topic_id}` : session.group_id ? `/groups/${session.group_id}` : "/"}>{practice ? "Back to subtopics" : "Back to group"}</Link></p>
      <p className="muted">{practice ? `${session.topic_name} · one more try. The session score stays as it is.` : `${session.topic_name} · question ${question?.idx ?? session.answered ?? 0} of ${session.question_count}`}</p>
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
          {practice ? null : (
            <form className="stack" onSubmit={addSubtopic}>
              <label>Missing a subtopic?<input value={subtopicName} onChange={(event) => setSubtopicName(event.target.value)} placeholder="Name it and save it for later" /></label>
              <button className="secondary" type="submit">Save subtopic</button>
              {savedNote ? <p className="muted">{savedNote}</p> : null}
            </form>
          )}
          {feedback.counts === false ? (
            <div className="stack">
              <p>This grade is not part of the session score.</p>
              <button type="button" onClick={askAgain}>Ask again</button>
            </div>
          ) : feedback.completed ? (
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
          <div className="attach">
            <button type="button" className="link" onClick={() => fileRef.current?.click()} disabled={working !== ""}>
              {photo ? "Replace photo" : "Attach a photo"}
            </button>
            <input ref={fileRef} type="file" accept="image/*" hidden onChange={onPhoto} />
            {photo ? (
              <>
                <img className="photo-preview" src={photo.preview} alt="Handwritten working" />
                <button type="button" className="link" onClick={() => setPhoto(null)}>Remove</button>
              </>
            ) : (
              <span className="muted">For working written on paper.</span>
            )}
            <button type="button" className="link" onClick={askTelegram} disabled={working !== ""}>Send on Telegram</button>
          </div>
          {telegramCode ? (
            <p className="answer-code">
              Answer code <strong>{telegramCode}</strong>
              <span className="muted">
                {telegramPhotos ? ` ${telegramPhotos} photo${telegramPhotos === 1 ? "" : "s"} received.` : ""} Send each photo with this code in the caption. Send DONE in Telegram to grade them now, or wait and they are graded together about a minute after the last one.
              </span>
            </p>
          ) : null}
          <div className="row">
            <button type="submit" disabled={working !== "" || (!answer.trim() && !photo)}>{working ? "Checking…" : "Submit answer"}</button>
            {practice ? null : <button type="button" className="secondary" onClick={skip} disabled={working !== ""}>Skip question</button>}
          </div>
        </form>
      ) : null}
    </main>
  );
}

type Photo = { media_type: "image/jpeg"; data: string; preview: string };

type TelegramPhoto = {
  code: string | null;
  status: string;
  delivered: boolean;
  received?: number;
  result: AnswerResult | null;
};

function shrinkPhoto(file: File): Promise<Photo> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      const longest = Math.max(image.width, image.height);
      const scale = longest > 1600 ? 1600 / longest : 1;
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.width * scale));
      canvas.height = Math.max(1, Math.round(image.height * scale));
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        URL.revokeObjectURL(url);
        reject(new Error("Could not read that photo"));
        return;
      }
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      const preview = canvas.toDataURL("image/jpeg", 0.82);
      const data = preview.split(",")[1] || "";
      URL.revokeObjectURL(url);
      if (!data) {
        reject(new Error("Could not read that photo"));
        return;
      }
      resolve({ media_type: "image/jpeg", data, preview });
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Use a JPEG, PNG, or WebP photo."));
    };
    image.src = url;
  });
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
