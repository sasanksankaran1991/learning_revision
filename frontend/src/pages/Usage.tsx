import { FormEvent, useEffect, useState } from "react";
import { api, Usage, UsageBucket, UsageDay } from "../api";
import { submit } from "../form";

const LABELS: Record<string, string> = {
  first_subtopics: "First-time subtopic maps",
  recurring_subtopics: "Weekly subtopics",
  revision: "Revision sessions",
  other: "Other",
};

const RANGES = [
  { id: "1", label: "Today" },
  { id: "7", label: "7 days" },
  { id: "30", label: "30 days" },
  { id: "90", label: "90 days" },
];

export function UsagePage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [range, setRange] = useState("7");
  const [custom, setCustom] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function load(query: string) {
    setUsage(await api<Usage>(`/api/me/usage${query}`));
    setSelectedDay(null);
  }

  useEffect(() => {
    load("?days=7").catch((err: Error) => setError(err.message));
  }, []);

  async function chooseRange(id: string) {
    setRange(id);
    setCustom(false);
    setError("");
    try {
      await load(`?days=${id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load usage");
    }
  }

  async function chooseCustom(event: FormEvent) {
    await submit(event, async () => {
      setCustom(true);
      setRange("");
      await load(`?start=${start}&end=${end}`);
    }, setError);
  }

  if (!usage) return <main className="page">{error || "Loading usage…"}</main>;

  const focus = usage.by_day.find((day) => day.date === selectedDay) ?? null;
  const categories = focus ? focus.by_category : usage.by_category;
  const heading = focus ? dayLabel(focus.date) : `${dayLabel(usage.start)} – ${dayLabel(usage.end)}`;
  const totals: UsageBucket = focus ?? usage.period;
  const peak = Math.max(...usage.by_day.map((day) => day.cost_inr), 0);

  return (
    <main className="page stack">
      <h1>Usage</h1>
      <p className="muted">Costs use your timezone, {usage.timezone}. Click a day to see that day alone.</p>
      <div className="row">
        {RANGES.map((item) => (
          <button key={item.id} type="button" className={range === item.id && !custom ? "" : "secondary"} onClick={() => chooseRange(item.id)}>
            {item.label}
          </button>
        ))}
      </div>
      <form className="row" onSubmit={chooseCustom}>
        <label>From<input type="date" value={start} onChange={(event) => setStart(event.target.value)} required /></label>
        <label>To<input type="date" value={end} onChange={(event) => setEnd(event.target.value)} required /></label>
        <button type="submit">Show period</button>
      </form>
      {error ? <p className="error">{error}</p> : null}

      <section className="card stack">
        <h2>{heading}</h2>
        <p>{totals.calls.toLocaleString()} calls · {totals.total_tokens.toLocaleString()} tokens · ${totals.cost_usd.toFixed(4)} · ₹{totals.cost_inr.toFixed(2)}</p>
        <div className="legend">
          {Object.keys(LABELS).map((name) => (
            <span key={name}><i className={`swatch ${name}`} />{LABELS[name]}</span>
          ))}
        </div>
        <div className="days">
          {usage.by_day.map((day) => (
            <button
              key={day.date}
              type="button"
              className={day.date === selectedDay ? "day-col on" : "day-col"}
              onClick={() => setSelectedDay(day.date === selectedDay ? null : day.date)}
              title={`${dayLabel(day.date)} · ₹${day.cost_inr.toFixed(2)}`}
            >
              <span className="day-track">
                <DayStack day={day} peak={peak} />
              </span>
              <span className="day-label">{shortDay(day.date)}</span>
            </button>
          ))}
        </div>
        {focus ? <button type="button" className="secondary" onClick={() => setSelectedDay(null)}>Show the whole period</button> : null}
      </section>

      <table>
        <thead>
          <tr><th>Use</th><th>Calls</th><th>Tokens</th><th>USD</th><th>INR</th></tr>
        </thead>
        <tbody>
          {Object.keys(LABELS).map((name) => {
            const row = categories[name];
            return (
              <tr key={name}>
                <td><i className={`swatch ${name}`} /> {LABELS[name]}</td>
                <td>{row.calls}</td>
                <td>{row.total_tokens.toLocaleString()}</td>
                <td>${row.cost_usd.toFixed(4)}</td>
                <td>₹{row.cost_inr.toFixed(2)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </main>
  );
}

function DayStack({ day, peak }: { day: UsageDay; peak: number }) {
  const scale = peak > 0 ? 120 / peak : 0;
  return (
    <>
      {Object.keys(LABELS).map((name) => (
        <span key={name} className={`seg ${name}`} style={{ height: `${day.by_category[name].cost_inr * scale}px` }} />
      ))}
    </>
  );
}

function dayLabel(iso: string) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function shortDay(iso: string) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
