import { FormEvent, useEffect, useState } from "react";
import { api, Credential, Settings, TelegramConfig } from "../api";
import { submit } from "../form";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [keys, setKeys] = useState<Credential[]>([]);
  const [telegram, setTelegram] = useState<TelegramConfig | null>(null);
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [telegramEnabled, setTelegramEnabled] = useState(true);
  const [chats, setChats] = useState<{ chat_id: string; label: string }[]>([]);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [keyForm, setKeyForm] = useState({ provider: "openai", label: "", api_key: "", question_model: "gpt-4o-mini", base_url: "", eval_model: "" });
  const [notice, setNotice] = useState("");
  const [checking, setChecking] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [editForm, setEditForm] = useState({ provider: "openai", label: "", api_key: "", question_model: "", base_url: "", eval_model: "" });
  const [error, setError] = useState("");

  async function load() {
    setSettings(await api<Settings>("/api/me/settings"));
    setKeys(await api<Credential[]>("/api/me/ai-credentials"));
    const savedTelegram = await api<TelegramConfig>("/api/me/telegram");
    setTelegram(savedTelegram);
    setChatId(savedTelegram.chat_id);
    setTelegramEnabled(savedTelegram.configured ? savedTelegram.enabled : true);
  }

  useEffect(() => {
    load().catch((err: Error) => setError(err.message));
  }, []);

  async function saveSettings(event: FormEvent) {
    if (!settings) return;
    await submit(event, async () => {
      const saved = await api<Settings>("/api/me/settings", { method: "PUT", body: JSON.stringify(settings) });
      setSettings(saved);
      setNotice("Settings saved.");
    }, setError);
  }

  async function saveKey(event: FormEvent) {
    await submit(event, async () => {
      await api("/api/me/ai-credentials", {
        method: "POST",
        body: JSON.stringify({
          provider: keyForm.provider,
          label: keyForm.label,
          api_key: keyForm.api_key,
          question_model: keyForm.question_model,
          base_url: keyForm.base_url || null,
          eval_model: keyForm.eval_model || null,
        }),
      });
      setKeyForm({ ...keyForm, label: "", api_key: "" });
      await load();
    }, setError);
  }

  function startEdit(item: Credential) {
    setEditing(item.id);
    setEditForm({
      provider: item.provider,
      label: item.label,
      api_key: "",
      question_model: item.question_model,
      base_url: item.base_url ?? "",
      eval_model: item.eval_model ?? "",
    });
    setError("");
  }

  async function saveEdit(event: FormEvent) {
    if (editing === null) return;
    await submit(event, async () => {
      await api(`/api/me/ai-credentials/${editing}`, {
        method: "PATCH",
        body: JSON.stringify({
          provider: editForm.provider,
          label: editForm.label,
          api_key: editForm.api_key,
          question_model: editForm.question_model,
          base_url: editForm.base_url || null,
          eval_model: editForm.eval_model || null,
        }),
      });
      setEditing(null);
      setNotice("Key updated.");
      await load();
    }, setError);
  }

  async function removeKey(id: number) {
    await api(`/api/me/ai-credentials/${id}`, { method: "DELETE" });
    await load();
  }

  async function checkKey(id: number) {
    setChecking(id);
    setError("");
    setNotice("");
    try {
      const result = await api<Credential & { detail: string }>(`/api/me/ai-credentials/${id}/test`, { method: "POST" });
      setNotice(result.detail);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not check the key");
    } finally {
      setChecking(null);
    }
  }

  async function saveTelegram(event: FormEvent) {
    if (!settings) return;
    await submit(event, async () => {
      const savedSettings = await api<Settings>("/api/me/settings", { method: "PUT", body: JSON.stringify(settings) });
      const savedTelegram = await api<TelegramConfig>("/api/me/telegram", {
        method: "PUT",
        body: JSON.stringify({ bot_token: botToken, chat_id: chatId, enabled: telegramEnabled }),
      });
      setSettings(savedSettings);
      setTelegram(savedTelegram);
      setBotToken("");
      setNotice(savedTelegram.enabled ? "This chat is saved on your profile." : "Telegram messages are turned off for your profile.");
    }, setError);
  }

  async function findChats() {
    setError("");
    try {
      const result = await api<{ chats: { chat_id: string; label: string }[] }>("/api/me/telegram/chats", {
        method: "POST",
        body: JSON.stringify({ bot_token: botToken || null }),
      });
      setChats(result.chats);
      setNotice(result.chats.length ? "Choose the chat that should get the summary." : "No chats yet. Send the bot a message in Telegram, then look again.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not look up chats");
    }
  }

  async function testTelegram() {
    setError("");
    try {
      const result = await api<{ detail: string }>("/api/me/telegram/test", { method: "POST" });
      setNotice(result.detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the test");
    }
  }

  async function changePassword(event: FormEvent) {
    await submit(event, async () => {
      await api("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      setNotice("Password changed. Sign in again on your other devices.");
      setCurrentPassword("");
      setNewPassword("");
    }, setError);
  }

  if (!settings) return <main className="page">{error || "Loading settings…"}</main>;

  return (
    <main className="page stack">
      <h1>Settings</h1>
      {notice ? <p>{notice}</p> : null}
      {error ? <p className="error">{error}</p> : null}
      <details className="card fold">
        <summary>Revision rules</summary>
        <form className="stack" onSubmit={saveSettings}>
        <div className="split">
          <NumberField label="Questions per session" value={settings.questions_per_session} min={3} max={10} onChange={(value) => setSettings({ ...settings, questions_per_session: value })} />
          <NumberField label="Pending ratio" value={settings.pending_ratio} step={0.05} onChange={(value) => setSettings({ ...settings, pending_ratio: value })} />
          <NumberField label="Weight: weakness" value={settings.w_error} step={0.05} onChange={(value) => setSettings({ ...settings, w_error: value })} />
          <NumberField label="Weight: recency" value={settings.w_recency} step={0.05} onChange={(value) => setSettings({ ...settings, w_recency: value })} />
          <NumberField label="Weight: fewer attempts" value={settings.w_attempts} step={0.05} onChange={(value) => setSettings({ ...settings, w_attempts: value })} />
          <NumberField label="UCB exploration c" value={settings.ucb_c} step={0.1} onChange={(value) => setSettings({ ...settings, ucb_c: value })} />
          <NumberField label="USD to INR" value={settings.usd_inr_rate} step={0.1} onChange={(value) => setSettings({ ...settings, usd_inr_rate: value })} />
          <NumberField label="Daily token budget" value={settings.daily_token_budget ?? 0} onChange={(value) => setSettings({ ...settings, daily_token_budget: value || null })} />
        </div>
        <label className="row">
          <input type="checkbox" checked={settings.weekly_enrich_enabled} onChange={(event) => setSettings({ ...settings, weekly_enrich_enabled: event.target.checked })} style={{ width: "auto" }} />
          Add one topic's missing subtopics once a week
        </label>
        <p className="muted">This choice is saved. The weekly job itself is not running yet, so turning it on does not spend tokens.</p>
        <label>Weekday
          <select value={settings.weekly_enrich_weekday} onChange={(event) => setSettings({ ...settings, weekly_enrich_weekday: Number(event.target.value) })}>
            {DAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}
          </select>
        </label>
        <button type="submit">Save rules</button>
        </form>
      </details>

      <details className="card fold">
        <summary>Telegram summary</summary>
        <form className="stack" onSubmit={saveTelegram}>
        <p className="muted">Create a bot with BotFather and send it a message. Add that chat to your profile. A finished session is sent right away. At the end of the day, if any tokens were used, you also get that day's usage and how each group and topic stands. In the bot chat, /usage shows today's token use and /status shows every group and topic. The API checks for those commands about every 15 seconds while it is running.</p>
        {telegram?.configured ? <p>Chat on your profile: {telegram.chat_id}</p> : null}
        <label className="row">
          <input type="checkbox" checked={telegramEnabled} onChange={(event) => setTelegramEnabled(event.target.checked)} style={{ width: "auto" }} />
          Send session summaries and the end-of-day message
        </label>
        {settings ? (
          <label>End of day
            <input type="time" value={settings.telegram_summary_time} onChange={(event) => setSettings({ ...settings, telegram_summary_time: event.target.value })} required />
          </label>
        ) : null}
        <label>Bot token<input type="password" value={botToken} onChange={(event) => setBotToken(event.target.value)} placeholder={telegram?.token_last4 ? `Leave blank to keep ••••${telegram.token_last4}` : "123456:ABC..."} /></label>
        <label>Chat id<input value={chatId} onChange={(event) => setChatId(event.target.value)} required /></label>
        {chats.length ? (
          <label>Chats that have messaged this bot
            <select value={chatId} onChange={(event) => setChatId(event.target.value)}>
              <option value="">Choose</option>
              {chats.map((chat) => <option key={chat.chat_id} value={chat.chat_id}>{chat.label} · {chat.chat_id}</option>)}
            </select>
          </label>
        ) : null}
        <div className="row">
          <button type="submit">Add chat to my profile</button>
          <button className="secondary" type="button" onClick={findChats}>Find chats</button>
          <button className="secondary" type="button" onClick={testTelegram}>Send test</button>
        </div>
        </form>
      </details>

      <details className="card fold">
        <summary>AI keys</summary>
        <div className="stack">
        <p className="muted">The full key is encrypted and is not shown again. You only see the last four characters.</p>
        {checking !== null ? <p className="working">Checking this key with the provider.</p> : null}
        {keys.map((item) => (
          <div className="stack" key={item.id}>
            <div className="row">
              <span>{item.label} · {item.provider} · {item.question_model}{item.eval_model ? ` · eval ${item.eval_model}` : ""} · ••••{item.key_last4} · {keyStatus(item)}</span>
              <button className="secondary" type="button" onClick={() => (editing === item.id ? setEditing(null) : startEdit(item))}>
                {editing === item.id ? "Close" : "Edit"}
              </button>
              <button className="secondary" type="button" disabled={checking === item.id} onClick={() => checkKey(item.id)}>
                {checking === item.id ? "Checking…" : "Check key"}
              </button>
              <button className="secondary" type="button" onClick={() => removeKey(item.id)}>Remove</button>
            </div>
            {editing === item.id ? (
              <form className="stack" onSubmit={saveEdit}>
                <label>Provider
                  <select value={editForm.provider} onChange={(event) => setEditForm({ ...editForm, provider: event.target.value })}>
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                    <option value="gemini">Gemini</option>
                    <option value="openai_compatible">OpenAI-compatible</option>
                  </select>
                </label>
                <label>Label<input value={editForm.label} onChange={(event) => setEditForm({ ...editForm, label: event.target.value })} required /></label>
                <label>New API key, optional<input type="password" value={editForm.api_key} onChange={(event) => setEditForm({ ...editForm, api_key: event.target.value })} placeholder={`Leave blank to keep ••••${item.key_last4}`} /></label>
                <label>Question model<input value={editForm.question_model} onChange={(event) => setEditForm({ ...editForm, question_model: event.target.value })} required /></label>
                <label>Stronger eval model, optional<input value={editForm.eval_model} onChange={(event) => setEditForm({ ...editForm, eval_model: event.target.value })} /></label>
                {editForm.provider === "openai_compatible" ? (
                  <label>HTTPS base URL<input value={editForm.base_url} onChange={(event) => setEditForm({ ...editForm, base_url: event.target.value })} placeholder="https://api.example.com/v1" required /></label>
                ) : null}
                <button type="submit">Save changes</button>
              </form>
            ) : null}
          </div>
        ))}
        <form className="stack" onSubmit={saveKey}>
          <label>Provider
            <select value={keyForm.provider} onChange={(event) => setKeyForm({ ...keyForm, provider: event.target.value })}>
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
              <option value="gemini">Gemini</option>
              <option value="openai_compatible">OpenAI-compatible</option>
            </select>
          </label>
          <label>Label<input value={keyForm.label} onChange={(event) => setKeyForm({ ...keyForm, label: event.target.value })} /></label>
          <label>API key<input type="password" value={keyForm.api_key} onChange={(event) => setKeyForm({ ...keyForm, api_key: event.target.value })} /></label>
          <label>Question model<input value={keyForm.question_model} onChange={(event) => setKeyForm({ ...keyForm, question_model: event.target.value })} /></label>
          <label>Stronger eval model, optional<input value={keyForm.eval_model} onChange={(event) => setKeyForm({ ...keyForm, eval_model: event.target.value })} /></label>
          {keyForm.provider === "openai_compatible" ? (
            <label>HTTPS base URL<input value={keyForm.base_url} onChange={(event) => setKeyForm({ ...keyForm, base_url: event.target.value })} placeholder="https://api.example.com/v1" /></label>
          ) : null}
          <button type="submit">Save key</button>
        </form>
        </div>
      </details>

      <details className="card fold">
        <summary>Password</summary>
        <form className="stack" onSubmit={changePassword}>
        <label>Current<input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} /></label>
        <label>New<input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /></label>
        <button type="submit">Change password</button>
        </form>
      </details>
    </main>
  );
}

function keyStatus(item: Credential): string {
  if (item.is_valid === true) return "key works";
  if (item.is_valid === false) return "key failed";
  return "not checked";
}

function NumberField({ label, value, onChange, step = 1, min, max }: { label: string; value: number; onChange: (value: number) => void; step?: number; min?: number; max?: number }) {
  return (
    <label>{label}
      <input type="number" value={value} step={step} min={min} max={max} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}
