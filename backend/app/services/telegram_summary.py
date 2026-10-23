from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from app.models import AiUsage, Group, Misconception, Session as StudySession, SessionQuestion, Subtopic, TelegramConfig, Topic, User, UserSettings
from app.security import as_utc, decrypt_secret
from app.services.accounts import audit

_sender = None
_lookup = None
_updates = None
_scheduler = None


class TelegramError(Exception):
    pass


def set_sender(sender) -> None:
    global _sender
    _sender = sender


def reset_sender() -> None:
    global _sender, _lookup, _updates
    _sender = None
    _lookup = None
    _updates = None


def set_updates(fetcher) -> None:
    global _updates
    _updates = fetcher


def set_lookup(lookup) -> None:
    global _lookup
    _lookup = lookup


def send_message(token: str, chat_id: str, text: str) -> None:
    if _sender is not None:
        _sender(token, chat_id, text)
        return
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise TelegramError("Telegram could not be reached") from exc
    if response.status_code >= 400:
        detail = "Telegram rejected the message"
        try:
            description = response.json().get("description")
        except ValueError:
            description = None
        if isinstance(description, str) and token not in description:
            detail = description
        raise TelegramError(detail)


def lookup_chats(token: str) -> list[dict]:
    if _lookup is not None:
        return _lookup(token)
    try:
        response = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
    except httpx.HTTPError as exc:
        raise TelegramError("Telegram could not be reached") from exc
    if response.status_code >= 400:
        raise TelegramError("Telegram rejected the token")
    found: dict[str, str] = {}
    for update in response.json().get("result") or []:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if "id" not in chat:
            continue
        label = chat.get("title") or chat.get("username") or chat.get("first_name") or str(chat["id"])
        found[str(chat["id"])] = str(label)
    return [{"chat_id": chat_id, "label": label} for chat_id, label in found.items()]


def register_commands(token: str) -> None:
    if _sender is not None:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/setMyCommands",
            json={
                "commands": [
                    {"command": "usage", "description": "Today's token use"},
                    {"command": "status", "description": "Groups and topics"},
                ]
            },
            timeout=20,
        )
    except httpx.HTTPError:
        return


def fetch_updates(token: str, offset: int | None) -> list[dict]:
    if _updates is not None:
        return _updates(token, offset)
    params: dict = {"timeout": 0, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    try:
        response = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=20)
    except httpx.HTTPError as exc:
        raise TelegramError("Telegram could not be reached") from exc
    if response.status_code >= 400:
        raise TelegramError("Telegram rejected the token")
    return response.json().get("result") or []


def build_summary(db: Session, user: User, settings: UserSettings, moment: datetime, day=None) -> str | None:
    tz = ZoneInfo(user.timezone)
    local = moment.astimezone(tz)
    today = day or local.date()
    start, end = _local_bounds(today, tz)
    sessions = [
        row
        for row in db.query(StudySession).filter(StudySession.user_id == user.id, StudySession.status == "completed").all()
        if row.completed_at is not None and start <= as_utc(row.completed_at) < end
    ]
    usage = [
        row
        for row in db.query(AiUsage).filter(AiUsage.user_id == user.id).all()
        if start <= as_utc(row.created_at) < end
    ]
    scores = [row.session_score for row in sessions if row.session_score is not None]
    average = round(sum(scores) / len(scores)) if scores else 0
    prompt = sum(row.prompt_tokens for row in usage)
    completion = sum(row.completion_tokens for row in usage)
    total = sum(row.total_tokens for row in usage)
    usd = sum(row.cost_usd for row in usage)
    inr = sum(row.cost_inr for row in usage)
    categories = defaultdict(lambda: {"tokens": 0, "usd": 0.0, "inr": 0.0})
    by_topic: dict[int, dict] = defaultdict(lambda: {"tokens": 0, "scores": []})
    by_subtopic: dict[int, int] = defaultdict(int)
    for row in usage:
        bucket = categories[row.category if row.category in {"first_subtopics", "recurring_subtopics", "revision", "other"} else "other"]
        bucket["tokens"] += row.total_tokens
        bucket["usd"] += row.cost_usd
        bucket["inr"] += row.cost_inr
        if row.topic_id:
            by_topic[row.topic_id]["tokens"] += row.total_tokens
        if row.subtopic_id:
            by_subtopic[row.subtopic_id] += row.total_tokens
    for row in sessions:
        by_topic[row.topic_id]["scores"].append(row.session_score)
    if total <= 0:
        return None
    lines = [
        f"📚 Daily learning summary — {today.day} {today.strftime('%b %Y')}",
        f"Sessions: {len(sessions)} · Avg score: {average}",
        f"Tokens: {_count(total)} (in {_count(prompt)} / out {_count(completion)}) · Est. cost: {_money(usd, inr)}",
        f"• First subtopic maps: {_bucket(categories['first_subtopics'])}",
        "• Weekly subtopics: off" if not settings.weekly_enrich_enabled else f"• Weekly subtopics: {_bucket(categories['recurring_subtopics'])}",
        f"• Revision: {_bucket(categories['revision'])}",
    ]
    week_tokens = _tokens_over(db, user.id, tz, today - timedelta(days=7), today)
    if week_tokens == 0 or total > week_tokens * 0.10:
        lines.append(f"Token use is higher than 10% of the last 7 days: {_count(total)} on this day, {_count(week_tokens)} across those days.")
    if by_topic:
        lines.append("")
        lines.append("By topic:")
        for topic_id, item in sorted(by_topic.items(), key=lambda pair: pair[1]["tokens"], reverse=True)[:8]:
            topic = db.get(Topic, topic_id)
            if topic is None:
                continue
            group = db.get(Group, topic.group_id)
            group_name = group.name if group else "Group"
            known = [score for score in item["scores"] if score is not None]
            score = f" · {round(sum(known) / len(known))}/100" if known else ""
            lines.append(f"• {_esc(group_name)} › {_esc(topic.name)} — {_count(item['tokens'])} tok{score}")
    ranked = sorted(by_subtopic.items(), key=lambda pair: pair[1], reverse=True)[:2]
    if ranked:
        names = []
        for subtopic_id, tokens in ranked:
            subtopic = db.get(Subtopic, subtopic_id)
            if subtopic is not None:
                names.append(f"{_esc(subtopic.name)} ({_compact(tokens)})")
        if names:
            lines.append("Top token subtopics: " + ", ".join(names))
    weak = [
        row.summary
        for row in db.query(Misconception).filter(Misconception.user_id == user.id, Misconception.resolved.is_(False)).all()
    ][:3]
    if weak:
        lines.append("Weak spots: " + ", ".join(_esc(item) for item in weak))
    tomorrow = today + timedelta(days=1)
    due = []
    for topic in db.query(Topic).filter(Topic.user_id == user.id, Topic.active.is_(True)).all():
        if topic.next_review_at is not None and as_utc(topic.next_review_at).astimezone(tz).date() == tomorrow:
            due.append(topic.name)
    if due:
        lines.append("Due tomorrow: " + ", ".join(_esc(name) for name in due[:8]))
    week = local.isocalendar()[:2]
    added = []
    for subtopic in db.query(Subtopic).filter(Subtopic.user_id == user.id, Subtopic.source == "ai_weekly").all():
        created = as_utc(subtopic.created_at).astimezone(tz)
        if created.isocalendar()[:2] != week:
            continue
        topic = db.get(Topic, subtopic.topic_id)
        reason = f" ({_esc(subtopic.reason)})" if subtopic.reason else ""
        added.append(f"{_esc(topic.name if topic else 'Topic')} → {_esc(subtopic.name)}{reason}")
    if added:
        lines.append("New subtopics this week: " + "; ".join(added[:3]))
    lines.extend(["", *_group_lines(db, user, tz)])
    return "\n".join(lines)


def build_usage_message(db: Session, user: User, settings: UserSettings, moment: datetime) -> str:
    tz = ZoneInfo(user.timezone)
    today = moment.astimezone(tz).date()
    start, end = _local_bounds(today, tz)
    usage = [
        row
        for row in db.query(AiUsage).filter(AiUsage.user_id == user.id).all()
        if start <= as_utc(row.created_at) < end
    ]
    prompt = sum(row.prompt_tokens for row in usage)
    completion = sum(row.completion_tokens for row in usage)
    total = sum(row.total_tokens for row in usage)
    usd = sum(row.cost_usd for row in usage)
    inr = sum(row.cost_inr for row in usage)
    categories = defaultdict(lambda: {"tokens": 0, "usd": 0.0, "inr": 0.0})
    for row in usage:
        bucket = categories[row.category if row.category in {"first_subtopics", "recurring_subtopics", "revision", "other"} else "other"]
        bucket["tokens"] += row.total_tokens
        bucket["usd"] += row.cost_usd
        bucket["inr"] += row.cost_inr
    lines = [
        f"Usage — {today.day} {today.strftime('%b %Y')}",
        f"Tokens: {_count(total)} (in {_count(prompt)} / out {_count(completion)})",
        f"Est. cost: {_money(usd, inr)}",
        f"• First subtopic maps: {_bucket(categories['first_subtopics'])}",
        "• Weekly subtopics: off" if not settings.weekly_enrich_enabled else f"• Weekly subtopics: {_bucket(categories['recurring_subtopics'])}",
        f"• Revision: {_bucket(categories['revision'])}",
    ]
    if total <= 0:
        lines.append("No tokens used today.")
    else:
        week_tokens = _tokens_over(db, user.id, tz, today - timedelta(days=7), today)
        if week_tokens == 0 or total > week_tokens * 0.10:
            lines.append(f"Token use is higher than 10% of the last 7 days: {_count(total)} today, {_count(week_tokens)} across those days.")
    return "\n".join(lines)


def build_status_message(db: Session, user: User, moment: datetime) -> str:
    tz = ZoneInfo(user.timezone)
    local = moment.astimezone(tz)
    return "\n".join([f"Status — {local.day} {local.strftime('%b %Y')}", *_group_lines(db, user, tz)])


def _group_lines(db: Session, user: User, tz: ZoneInfo) -> list[str]:
    lines = ["Groups and topics:"]
    groups = db.query(Group).filter(Group.user_id == user.id, Group.archived.is_(False)).order_by(Group.name).all()
    if not groups:
        lines.append("• No groups yet")
        return lines
    for group in groups:
        topics = db.query(Topic).filter(Topic.group_id == group.id, Topic.active.is_(True)).order_by(Topic.name).all()
        topic_ids = [topic.id for topic in topics]
        subtopics = db.query(Subtopic).filter(Subtopic.topic_id.in_(topic_ids)).all() if topic_ids else []
        unanswered = {}
        counts = {}
        for subtopic in subtopics:
            counts[subtopic.topic_id] = counts.get(subtopic.topic_id, 0) + 1
            if subtopic.attempts == 0:
                unanswered[subtopic.topic_id] = unanswered.get(subtopic.topic_id, 0) + 1
        lines.append(f"• {_esc(group.name)} — {len(topics)} topics · {len(subtopics)} subtopics")
        if not topics:
            lines.append("  No topics yet")
            continue
        for topic in topics:
            score = "unscored" if topic.ewma_score is None else f"{round(topic.ewma_score)}/100"
            review = ""
            if topic.next_review_at is not None:
                review_day = as_utc(topic.next_review_at).astimezone(tz).date()
                review = f" · next review {review_day.day} {review_day.strftime('%b')}"
            lines.append(
                f"  {_esc(topic.name)} — {topic.status} · {topic.mastery_level} · {score} · {topic.sessions_completed} sessions · {unanswered.get(topic.id, 0)} unanswered{review}"
            )
    return lines


def dispatch_due(db: Session, moment: datetime | None = None) -> int:
    moment = moment or datetime.now(timezone.utc)
    sent = 0
    configs = db.query(TelegramConfig).filter(TelegramConfig.enabled.is_(True)).all()
    for config in configs:
        user = db.get(User, config.user_id)
        settings = db.query(UserSettings).filter(UserSettings.user_id == config.user_id).one_or_none()
        if user is None or not user.is_active or settings is None:
            continue
        local = moment.astimezone(ZoneInfo(user.timezone))
        report_day = local.date() if _reached(local, settings.telegram_summary_time) else local.date() - timedelta(days=1)
        marker = report_day.isoformat()
        if config.last_sent_on == marker:
            continue
        text = build_summary(db, user, settings, moment, report_day)
        if text is None:
            config.last_sent_on = marker
            db.commit()
            continue
        try:
            send_message(decrypt_secret(config.bot_token_enc), config.chat_id, text)
        except Exception as exc:
            audit(db, "telegram_failed", user_id=user.id, meta={"error": exc.__class__.__name__})
            db.commit()
            continue
        config.last_sent_on = marker
        db.commit()
        sent += 1
    return sent


def poll_commands(db: Session, moment: datetime | None = None) -> int:
    moment = moment or datetime.now(timezone.utc)
    configs = db.query(TelegramConfig).filter(TelegramConfig.enabled.is_(True)).all()
    by_token: dict[str, list[TelegramConfig]] = {}
    for config in configs:
        try:
            token = decrypt_secret(config.bot_token_enc)
        except Exception:
            continue
        by_token.setdefault(token, []).append(config)
    handled = 0
    for token, rows in by_token.items():
        offset = max((row.update_offset or 0) for row in rows)
        try:
            updates = fetch_updates(token, offset or None)
        except Exception as exc:
            audit(db, "telegram_failed", user_id=rows[0].user_id, meta={"error": exc.__class__.__name__})
            db.commit()
            continue
        next_offset = offset
        for update in updates:
            update_id = int(update.get("update_id") or 0)
            if update_id < offset:
                continue
            next_offset = max(next_offset, update_id + 1)
            message = update.get("message") or {}
            chat_id = str((message.get("chat") or {}).get("id", ""))
            command = _command(message.get("text") or "")
            match = next((row for row in rows if row.chat_id == chat_id), None)
            if match is None or command is None:
                continue
            user = db.get(User, match.user_id)
            settings = db.query(UserSettings).filter(UserSettings.user_id == match.user_id).one_or_none()
            if user is None or not user.is_active or settings is None:
                continue
            if command in {"usage", "useage"}:
                text = build_usage_message(db, user, settings, moment)
            elif command == "status":
                text = build_status_message(db, user, moment)
            else:
                text = "Send /usage for today's token use.\nSend /status for each group and topic."
            try:
                send_message(token, chat_id, text)
                handled += 1
            except Exception as exc:
                audit(db, "telegram_failed", user_id=user.id, meta={"error": exc.__class__.__name__})
        for row in rows:
            row.update_offset = next_offset
        db.commit()
    return handled


def _command(text: str) -> str | None:
    word = text.strip().split()[0] if text.strip() else ""
    word = word.split("@", 1)[0].lower()
    if word in {"/usage", "/useage", "/status", "/start", "/help"}:
        return word[1:]
    return None


def notify_session_finished(db: Session, user: User, session: StudySession, topic: Topic, summary: dict) -> None:
    config = db.query(TelegramConfig).filter(TelegramConfig.user_id == user.id, TelegramConfig.enabled.is_(True)).one_or_none()
    if config is None:
        return
    group = db.get(Group, session.group_id)
    lines = [
        f"Session finished — {_esc(topic.name)}",
        f"Group: {_esc(group.name if group else 'Group')}",
        f"Score: {round(session.session_score or 0)}/100",
        f"Mastery: {_esc(topic.mastery_level)}",
    ]
    for question in db.query(SessionQuestion).filter(SessionQuestion.session_id == session.id).order_by(SessionQuestion.idx).all():
        if question.score is None:
            continue
        subtopic = db.get(Subtopic, question.subtopic_id) if question.subtopic_id else None
        name = subtopic.name if subtopic else f"Question {question.idx}"
        lines.append(f"• {_esc(name)}: {question.score}/5")
    for label, key in (("Strengths", "strengths"), ("Weak spots", "weak_subtopics"), ("Misconceptions", "misconceptions")):
        values = [str(item) for item in (summary.get(key) or []) if item]
        if values:
            lines.append(f"{label}: " + ", ".join(_esc(item) for item in values))
    if summary.get("recommendation"):
        lines.append("Next: " + _esc(str(summary["recommendation"])))
    try:
        send_message(decrypt_secret(config.bot_token_enc), config.chat_id, "\n".join(lines))
    except Exception as exc:
        audit(db, "telegram_failed", user_id=user.id, meta={"error": exc.__class__.__name__})
        db.commit()


def _tokens_over(db: Session, user_id: int, tz: ZoneInfo, start_day, end_day) -> int:
    total = 0
    day = start_day
    while day < end_day:
        start, end = _local_bounds(day, tz)
        total += sum(
            row.total_tokens
            for row in db.query(AiUsage).filter(AiUsage.user_id == user_id).all()
            if start <= as_utc(row.created_at) < end
        )
        day += timedelta(days=1)
    return total


def _local_bounds(day, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def _reached(local: datetime, hhmm: str) -> bool:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return (local.hour, local.minute) >= (hour, minute)


def _esc(value: str) -> str:
    return html.escape(value, quote=False)


def _count(value: int) -> str:
    return f"{value:,}"


def _compact(value: int) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


def _money(usd: float, inr: float) -> str:
    return f"${usd:.3f} (₹{inr:.2f})"


def _bucket(item: dict) -> str:
    return f"{_count(item['tokens'])} tok · {_money(item['usd'], item['inr'])}"


def ensure_telegram_columns(db: Session) -> None:
    from sqlalchemy import text

    bind = db.get_bind()
    if bind is None or not str(bind.url).startswith("sqlite"):
        return
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(telegram_configs)"))}
    if columns and "update_offset" not in columns:
        db.execute(text("ALTER TABLE telegram_configs ADD COLUMN update_offset INTEGER"))
        db.commit()


def align_summary_time(db: Session) -> None:
    from app.models import UserSettings as SettingsRow

    db.query(SettingsRow).filter(SettingsRow.telegram_summary_time == "21:30").update(
        {SettingsRow.telegram_summary_time: "23:59"},
        synchronize_session=False,
    )
    db.commit()


def start_scheduler() -> None:
    global _scheduler
    import os

    if os.environ.get("PYTEST_CURRENT_TEST") or _scheduler is not None:
        return
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.db import SessionLocal

    def _run() -> None:
        db = SessionLocal()
        try:
            dispatch_due(db)
        finally:
            db.close()

    scheduler = BackgroundScheduler()
    def _commands() -> None:
        db = SessionLocal()
        try:
            poll_commands(db)
        finally:
            db.close()

    scheduler.add_job(_run, "interval", minutes=5, id="telegram-summary", max_instances=1, coalesce=True)
    scheduler.add_job(_commands, "interval", seconds=15, id="telegram-commands", max_instances=1, coalesce=True)
    scheduler.start()
    _scheduler = scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
