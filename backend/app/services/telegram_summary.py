from __future__ import annotations

import base64
import html
import re
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from app.config import ROOT
from app.models import AiUsage, AnswerPhoto, Group, Misconception, Session as StudySession, SessionQuestion, Subtopic, TelegramConfig, Topic, User, UserSettings
from app.security import as_utc, decrypt_secret
from app.services.accounts import audit
from app.services.study import CODE_LETTERS

_sender = None
_lookup = None
_updates = None
_files = None
_scheduler = None
_CODE_RE = re.compile(rf"[{CODE_LETTERS}]{{4}}")
_PHOTO_WAIT = timedelta(seconds=45)
_MAX_PHOTOS = 4
_IMAGE_TYPES = {"image/jpeg": "image/jpeg", "image/jpg": "image/jpeg", "image/png": "image/png", "image/webp": "image/webp"}


class TelegramError(Exception):
    pass


def set_sender(sender) -> None:
    global _sender
    _sender = sender


def reset_sender() -> None:
    global _sender, _lookup, _updates, _files
    _sender = None
    _lookup = None
    _updates = None
    _files = None


def set_files(fetcher) -> None:
    global _files
    _files = fetcher


def set_updates(fetcher) -> None:
    global _updates
    _updates = fetcher


def set_lookup(lookup) -> None:
    global _lookup
    _lookup = lookup


def send_message(token: str, chat_id: str, text: str) -> int | None:
    if _sender is not None:
        result = _sender(token, chat_id, text)
        return result if isinstance(result, int) else None
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
    try:
        message_id = int((response.json().get("result") or {}).get("message_id") or 0)
    except (TypeError, ValueError):
        return None
    return message_id or None


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


def fetch_file(token: str, file_id: str) -> tuple[str, bytes]:
    if _files is not None:
        return _files(token, file_id)
    try:
        meta = httpx.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id}, timeout=20)
    except httpx.HTTPError as exc:
        raise TelegramError("Telegram could not be reached") from exc
    if meta.status_code >= 400:
        raise TelegramError("Telegram could not read that photo")
    path = str((meta.json().get("result") or {}).get("file_path") or "")
    if not path or path.startswith("/") or ".." in path:
        raise TelegramError("Telegram could not read that photo")
    try:
        blob = httpx.get(f"https://api.telegram.org/file/bot{token}/{path}", timeout=30)
    except httpx.HTTPError as exc:
        raise TelegramError("Telegram could not be reached") from exc
    if blob.status_code >= 400:
        raise TelegramError("Telegram could not read that photo")
    return _mime_from_path(path), blob.content


def photo_request_text(question: SessionQuestion, session: StudySession) -> str:
    snippet = " ".join((question.question_text or "").split())
    if len(snippet) > 240:
        snippet = snippet[:237] + "..."
    return "\n".join(
        [
            f"Answer code {_esc(question.photo_code or '')}",
            f"Question {question.idx} of {session.question_count}",
            _esc(snippet),
            "",
            "Send one or more photos with this code in the caption.",
            "Send DONE to grade them now. Otherwise they are graded together about a minute after the last photo.",
        ]
    )


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
            match = next((row for row in rows if row.chat_id == chat_id), None)
            if match is None:
                continue
            user = db.get(User, match.user_id)
            settings = db.query(UserSettings).filter(UserSettings.user_id == match.user_id).one_or_none()
            if user is None or not user.is_active or settings is None:
                continue
            done_code = _done_code(message.get("text") or "")
            if done_code is not None and _message_image(message) is None:
                if _grade_done(db, token, chat_id, user, done_code):
                    handled += 1
                continue
            image = _message_image(message)
            if image is not None:
                if _save_telegram_photo(db, token, chat_id, user, message, image, moment):
                    handled += 1
                continue
            command = _command(message.get("text") or "")
            if command is None:
                continue
            if command in {"usage", "useage"}:
                text = build_usage_message(db, user, settings, moment)
            elif command == "status":
                text = build_status_message(db, user, moment)
            else:
                text = "Send /usage for today's token use.\nSend /status for each group and topic.\nSend a photo with its 4-letter answer code, then DONE to grade every photo with that code."
            try:
                send_message(token, chat_id, text)
                handled += 1
            except Exception as exc:
                audit(db, "telegram_failed", user_id=user.id, meta={"error": exc.__class__.__name__})
        for row in rows:
            row.update_offset = next_offset
            owner = db.get(User, row.user_id)
            if owner is not None and owner.is_active:
                handled += _grade_due_photos(db, token, row.chat_id, owner, moment)
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


def _mime_from_path(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def _message_image(message: dict) -> dict | None:
    photos = message.get("photo") or []
    if photos:
        best = photos[-1]
        return {"file_id": str(best.get("file_id") or ""), "media_type": "image/jpeg", "file_size": int(best.get("file_size") or 0)}
    document = message.get("document") or {}
    media = _IMAGE_TYPES.get(str(document.get("mime_type") or "").lower())
    if media and document.get("file_id"):
        return {"file_id": str(document["file_id"]), "media_type": media, "file_size": int(document.get("file_size") or 0)}
    return None


def _codes_in(message: dict) -> list[str]:
    caption = str(message.get("caption") or message.get("text") or "").upper()
    return _CODE_RE.findall(caption)


def _done_code(text: str) -> str | None:
    parts = text.strip().split()
    if not parts:
        return None
    head = parts[0].split("@", 1)[0].lower()
    if head not in {"done", "/done"}:
        return None
    if len(parts) >= 2 and _CODE_RE.fullmatch(parts[1].upper()):
        return parts[1].upper()
    return ""


def _save_telegram_photo(db: Session, token: str, chat_id: str, user: User, message: dict, image: dict, moment: datetime) -> bool:
    from app.schemas import _photo_bytes

    question = _question_for_message(db, user, message)
    if question is None:
        send_message(token, chat_id, "That photo has no answer code. On the question, choose Send on Telegram, then send the photo with that code in the caption.")
        return True
    code = question.photo_code or ""
    if question.answer_text is not None:
        send_message(token, chat_id, f"Answer code {_esc(code)} is already graded.")
        return True
    held = db.query(AnswerPhoto).filter(AnswerPhoto.question_id == question.id).count()
    if held >= _MAX_PHOTOS:
        send_message(token, chat_id, f"Answer code {_esc(code)} already has {_MAX_PHOTOS} photos. Send DONE to grade them.")
        return True
    if not image.get("file_id") or int(image.get("file_size") or 0) > 4_000_000:
        send_message(token, chat_id, f"That photo is too large for answer code {_esc(code)}. Send a smaller image with the code in the caption.")
        return True
    session = db.get(StudySession, question.session_id)
    if session is None or session.status not in {"active", "practice"}:
        send_message(token, chat_id, f"Answer code {_esc(code)} is not part of an open session.")
        return True
    try:
        media_type, raw = fetch_file(token, image["file_id"])
        if image["media_type"] != "image/jpeg":
            media_type = image["media_type"]
        _photo_bytes(media_type, base64.b64encode(raw).decode())
        folder = ROOT / "data" / "answer_photos" / str(question.id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{secrets.token_hex(8)}{_suffix(media_type)}"
        path.write_bytes(raw)
        db.add(AnswerPhoto(user_id=user.id, question_id=question.id, media_type=media_type, path=str(path), created_at=moment))
        db.commit()
    except Exception as exc:
        audit(db, "telegram_failed", user_id=user.id, meta={"error": exc.__class__.__name__})
        db.commit()
        send_message(token, chat_id, f"The photo for answer code {_esc(code)} could not be saved. Send it again with the same code.")
        return True
    send_message(token, chat_id, f"Saved {held + 1} photo{'s' if held else ''} for {_esc(code)}. Send more with this code, or send DONE to grade now.")
    return True


def _grade_done(db: Session, token: str, chat_id: str, user: User, code: str) -> bool:
    from app.services.study import question_for_photo_code

    if code:
        question = question_for_photo_code(db, user, code)
        if question is None:
            send_message(token, chat_id, f"Answer code {_esc(code)} is not waiting for a photo.")
            return True
    else:
        waiting = _waiting_photo_questions(db, user)
        if len(waiting) == 1:
            question = waiting[0]
        elif not waiting:
            send_message(token, chat_id, "Send a photo with its answer code before DONE.")
            return True
        else:
            send_message(token, chat_id, "Send DONE and the 4-letter answer code.")
            return True
    if question.answer_text is not None:
        send_message(token, chat_id, f"Answer code {_esc(question.photo_code or '')} is already graded.")
        return True
    if db.query(AnswerPhoto).filter(AnswerPhoto.question_id == question.id).count() == 0:
        send_message(token, chat_id, f"No photo has arrived for answer code {_esc(question.photo_code or '')}.")
        return True
    _grade_saved(db, token, chat_id, user, question)
    return True


def _grade_due_photos(db: Session, token: str, chat_id: str, user: User, moment: datetime) -> int:
    graded = 0
    for question in _waiting_photo_questions(db, user):
        rows = db.query(AnswerPhoto).filter(AnswerPhoto.question_id == question.id).all()
        if not rows:
            continue
        newest = max(as_utc(row.created_at) for row in rows)
        if as_utc(moment) - newest < _PHOTO_WAIT:
            continue
        _grade_saved(db, token, chat_id, user, question)
        graded += 1
    return graded


def _waiting_photo_questions(db: Session, user: User) -> list[SessionQuestion]:
    rows = (
        db.query(SessionQuestion)
        .join(AnswerPhoto, AnswerPhoto.question_id == SessionQuestion.id)
        .filter(AnswerPhoto.user_id == user.id, SessionQuestion.answer_text.is_(None))
        .all()
    )
    found: dict[int, SessionQuestion] = {}
    for row in rows:
        session = db.get(StudySession, row.session_id)
        if session is not None and session.user_id == user.id and session.status in {"active", "practice"}:
            found[row.id] = row
    return list(found.values())


def _grade_saved(db: Session, token: str, chat_id: str, user: User, question: SessionQuestion) -> None:
    from app.services.study import submit_answer

    code = question.photo_code or ""
    session = db.get(StudySession, question.session_id)
    rows = db.query(AnswerPhoto).filter(AnswerPhoto.question_id == question.id).order_by(AnswerPhoto.id).all()
    if session is None or not rows:
        return
    images = []
    for row in rows[:_MAX_PHOTOS]:
        raw = Path(row.path).read_bytes()
        images.append((row.media_type, base64.b64encode(raw).decode()))
    try:
        result = submit_answer(db, user, session, question.idx, "", images)
    except Exception as exc:
        audit(db, "telegram_failed", user_id=user.id, meta={"error": exc.__class__.__name__})
        db.commit()
        send_message(token, chat_id, f"The photos for answer code {_esc(code)} could not be graded. Send DONE to try again.")
        return
    _delete_photos(db, rows)
    score = (result.get("evaluation") or {}).get("score")
    count = len(images)
    send_message(token, chat_id, f"Answer code {_esc(code)} scored {score}/5 from {count} photo{'s' if count != 1 else ''}.")


def _delete_photos(db: Session, rows: list[AnswerPhoto]) -> None:
    folders: set[Path] = set()
    for row in rows:
        path = Path(row.path)
        folders.add(path.parent)
        path.unlink(missing_ok=True)
        db.delete(row)
    db.commit()
    for folder in folders:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def _suffix(media_type: str) -> str:
    if media_type == "image/png":
        return ".png"
    if media_type == "image/webp":
        return ".webp"
    return ".jpg"


def _question_for_message(db: Session, user: User, message: dict) -> SessionQuestion | None:
    from app.services.study import question_for_photo_code

    for code in _codes_in(message):
        row = question_for_photo_code(db, user, code)
        if row is not None:
            return row
    reply = message.get("reply_to_message") or {}
    reply_id = reply.get("message_id")
    if reply_id is None:
        return None
    row = db.query(SessionQuestion).filter(SessionQuestion.photo_message_id == int(reply_id)).one_or_none()
    if row is None:
        return None
    session = db.get(StudySession, row.session_id)
    if session is None or session.user_id != user.id:
        return None
    return row


def ensure_telegram_columns(db: Session) -> None:
    from sqlalchemy import text

    bind = db.get_bind()
    if bind is None or not str(bind.url).startswith("sqlite"):
        return
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(telegram_configs)"))}
    if columns and "update_offset" not in columns:
        db.execute(text("ALTER TABLE telegram_configs ADD COLUMN update_offset INTEGER"))
        db.commit()
    question_columns = {row[1] for row in db.execute(text("PRAGMA table_info(session_questions)"))}
    if question_columns and "photo_code" not in question_columns:
        db.execute(text("ALTER TABLE session_questions ADD COLUMN photo_code VARCHAR(8)"))
        db.commit()
    if question_columns and "photo_message_id" not in question_columns:
        db.execute(text("ALTER TABLE session_questions ADD COLUMN photo_message_id INTEGER"))
        db.commit()
    if question_columns:
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_session_question_photo_code ON session_questions(photo_code)"))
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
