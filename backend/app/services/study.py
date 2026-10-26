from __future__ import annotations

import random
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai import prompts
from app.ai.contracts import EvalAndSummary, Evaluation, MapResult, QuestionBatch, QuestionResult, SummaryResult
from app.ai.gateway import BudgetError, NoCredentialError, complete, resolve_credential
from app.ai.providers import ProviderError
from app.models import AnswerPhoto, Group, Misconception, Session as StudySession, SessionQuestion, Subtopic, TelegramConfig, Topic, User, UserSettings
from app.services.planner import PlannedSlot, SubtopicView, plan_session
from app.services.scoring import mastery_level, next_schedule, session_percent, subtopic_ewma, topic_ewma
from app.security import as_utc
from app.services.selector import TopicCandidate, select_topic

_KINDS = {"concept", "math", "application", "pitfall"}
_IMPORTANCE = {"core", "supporting"}
CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ"
_SKIP_SCORE = min(0, 1)


def completed_today(moment: datetime | None, tz_name: str, now: datetime) -> bool:
    if moment is None:
        return False
    tz = ZoneInfo(tz_name)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz).date() == now.astimezone(tz).date()


def candidates_for_group(db: Session, user: User, group: Group, now: datetime) -> list[TopicCandidate]:
    topics = db.query(Topic).filter(Topic.group_id == group.id, Topic.user_id == user.id).all()
    rows = []
    for topic in topics:
        last_done = (
            db.query(StudySession)
            .filter(StudySession.topic_id == topic.id, StudySession.status == "completed")
            .order_by(StudySession.completed_at.desc())
            .first()
        )
        in_progress = (
            db.query(StudySession).filter(StudySession.topic_id == topic.id, StudySession.status == "active").first()
            is not None
        )
        rows.append(
            TopicCandidate(
                id=topic.id,
                name=topic.name,
                sessions_completed=topic.sessions_completed,
                ewma_score=topic.ewma_score,
                last_session_at=topic.last_session_at,
                interval_days=topic.interval_days,
                completed_today=completed_today(last_done.completed_at if last_done else None, user.timezone, now),
                in_progress=in_progress,
                active=topic.active,
            )
        )
    return rows


def ensure_subtopic_map(db: Session, user: User, topic: Topic, *, regenerate: bool = False) -> str:
    existing = list(topic.subtopics)
    if existing and not regenerate:
        return "ready"
    credential = resolve_credential(db, user, topic.group)
    if credential is None:
        return "needs_api_key"
    if regenerate:
        for row in existing:
            if row.source == "ai" and row.attempts == 0:
                db.delete(row)
        db.commit()
    try:
        result = complete(
            db,
            user,
            credential=credential,
            purpose="subtopic_regenerate" if regenerate else "subtopic_map",
            schema=MapResult,
            system=prompts.MAP_SYSTEM,
            user_prompt=_map_prompt(topic),
            temperature=0.4,
            max_tokens=1200,
            group_id=topic.group_id,
            topic_id=topic.id,
        )
    except BudgetError:
        return "budget"
    except Exception:
        return "failed"
    _store_map(db, user, topic, result.parsed)
    return "ready"


def start_session(db: Session, user: User, group: Group) -> dict:
    active = (
        db.query(StudySession)
        .filter(StudySession.group_id == group.id, StudySession.user_id == user.id, StudySession.status == "active")
        .one_or_none()
    )
    if active is not None:
        return _resume(db, user, active)

    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    now = datetime.now(timezone.utc)
    selection = select_topic(
        candidates_for_group(db, user, group, now),
        rng=random.SystemRandom(),
        pending_ratio=settings.pending_ratio,
        w_error=settings.w_error,
        w_recency=settings.w_recency,
        w_attempts=settings.w_attempts,
        floor=settings.min_weight_floor,
        now=now,
    )
    if selection.outcome != "selected" or selection.topic_id is None:
        return {"outcome": selection.outcome}
    topic = db.get(Topic, selection.topic_id)
    if topic is None or not list(topic.subtopics):
        return {"outcome": "needs_subtopics", "topic_id": selection.topic_id, "topic_name": None if topic is None else topic.name}

    plan = plan_session(
        _views(topic.subtopics),
        question_count=settings.questions_per_session,
        ucb_c=settings.ucb_c,
        max_retests=settings.max_retests_per_session,
        rng=random.SystemRandom(),
    )
    if not plan:
        return {"outcome": "needs_subtopics", "topic_id": topic.id, "topic_name": topic.name}

    session = StudySession(
        user_id=user.id,
        group_id=group.id,
        topic_id=topic.id,
        status="active",
        selection_pool=selection.pool or "pending",
        selection_prob=selection.probability,
        selection_debug_json=selection.debug,
        planned_subtopics_json=[_slot_dict(slot) for slot in plan],
        question_count=len(plan),
        current_index=0,
    )
    db.add(session)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        active = (
            db.query(StudySession)
            .filter(StudySession.group_id == group.id, StudySession.user_id == user.id, StudySession.status == "active")
            .one()
        )
        return _resume(db, user, active)
    db.refresh(session)
    _ensure_questions(db, user, session, topic)
    return _session_payload(db, session, topic, outcome="selected")


def submit_answer(
    db: Session,
    user: User,
    session: StudySession,
    idx: int,
    answer: str,
    image: tuple[str, str] | None = None,
) -> dict:
    practice = session.status == "practice"
    if session.status not in {"active", "practice"} or session.user_id != user.id:
        raise ValueError("That question is not part of the open session")
    topic = db.get(Topic, session.topic_id)
    questions = _questions(db, session.id)
    current = next((row for row in questions if row.idx == idx), None)
    if current is None or any(row.idx < idx and row.answer_text is None for row in questions):
        raise ValueError("Answer the current question first")
    if current.answer_text is not None:
        return _answer_payload(db, session, topic, current)

    credential = resolve_credential(db, user, topic.group)
    if credential is None:
        raise NoCredentialError("Add an AI key in settings before answering")
    last = (not practice) and idx >= session.question_count
    photos = _image_list(image)
    stored = answer.strip() or ("Photo of handwritten working." if len(photos) <= 1 else f"{len(photos)} photos of handwritten working.")
    prompt = _eval_prompt(topic, current, stored, _digest(questions), bool(photos), len(photos) > 1, last)
    schema = EvalAndSummary if last else Evaluation
    result = complete(
        db,
        user,
        credential=credential,
        purpose="final_summary" if last else "evaluate",
        schema=schema,
        system=prompts.EVAL_SUMMARY_SYSTEM if last else prompts.EVAL_SYSTEM,
        user_prompt=prompt,
        temperature=0.2,
        max_tokens=2500,
        allow_over_budget=True,
        group_id=session.group_id,
        topic_id=topic.id,
        subtopic_id=current.subtopic_id,
        session_id=session.id,
        question_id=current.id,
        image=photos or None,
    )
    evaluation = result.parsed if schema is Evaluation else result.parsed.evaluation
    evaluation = _maybe_escalate(db, user, credential, session, topic, current, evaluation, prompt, schema, photos or None)
    _apply_evaluation(db, current, evaluation, stored, official=not practice)
    if not practice and evaluation.misconception and current.subtopic_id:
        _upsert_misconception(db, user, topic, current, evaluation.misconception)
    db.flush()
    summary = _finish(db, session, topic, result.parsed.summary) if last else None
    if practice:
        session.status = "practiced"
    db.commit()
    if summary is not None:
        from app.services.telegram_summary import notify_session_finished

        notify_session_finished(db, user, session, topic, summary["summary"])
    return _answer_payload(db, session, topic, current, summary)


def skip_question(db: Session, user: User, session: StudySession, idx: int) -> dict:
    practice = session.status == "practice"
    if session.status not in {"active", "practice"} or session.user_id != user.id:
        raise ValueError("That question is not part of the open session")
    topic = db.get(Topic, session.topic_id)
    questions = _questions(db, session.id)
    current = next((row for row in questions if row.idx == idx), None)
    if current is None or any(row.idx < idx and row.answer_text is None for row in questions):
        raise ValueError("Answer the current question first")
    if current.answer_text is not None:
        return _answer_payload(db, session, topic, current)
    last = idx >= session.question_count
    _apply_evaluation(
        db,
        current,
        Evaluation(score=_SKIP_SCORE, correction="This question was skipped, so it is scored 0.", confidence=1),
        "Skipped.",
        official=not practice,
    )
    summary = None
    if practice:
        session.status = "practiced"
    elif last:
        summary = _finish(
            db,
            session,
            topic,
            SummaryResult(recommendation="A skipped question was scored 0 and will be asked again."),
        )
    db.commit()
    if summary is not None:
        from app.services.telegram_summary import notify_session_finished

        notify_session_finished(db, user, session, topic, summary["summary"])
    return _answer_payload(db, session, topic, current, summary)


def assign_photo_code(db: Session, user: User, session: StudySession, idx: int) -> SessionQuestion:
    config = db.query(TelegramConfig).filter(TelegramConfig.user_id == user.id, TelegramConfig.enabled.is_(True)).one_or_none()
    if config is None or not config.chat_id:
        raise ValueError("Add your Telegram chat in Settings before sending a photo there.")
    if session.status not in {"active", "practice"} or session.user_id != user.id:
        raise ValueError("That question is not part of the open session")
    questions = _questions(db, session.id)
    current = next((row for row in questions if row.idx == idx), None)
    if current is None or any(row.idx < idx and row.answer_text is None for row in questions):
        raise ValueError("Answer the current question first")
    if current.answer_text is not None:
        raise ValueError("That question is already answered")
    if not current.photo_code:
        current.photo_code = _new_photo_code(db)
        db.commit()
        db.refresh(current)
    return current


def photo_code_status(db: Session, user: User, session: StudySession, idx: int) -> dict:
    if session.user_id != user.id:
        raise ValueError("That question is not part of the open session")
    topic = db.get(Topic, session.topic_id)
    current = next((row for row in _questions(db, session.id) if row.idx == idx), None)
    if current is None:
        raise ValueError("That question is not part of the open session")
    received = db.query(AnswerPhoto).filter(AnswerPhoto.question_id == current.id).count()
    if current.answer_text is not None:
        return {"code": current.photo_code, "status": "graded", "delivered": True, "received": received, "result": _answer_payload(db, session, topic, current)}
    if current.photo_code:
        return {"code": current.photo_code, "status": "waiting", "delivered": True, "received": received, "result": None}
    return {"code": None, "status": "none", "delivered": True, "received": 0, "result": None}


def question_for_photo_code(db: Session, user: User, code: str) -> SessionQuestion | None:
    row = db.query(SessionQuestion).filter(SessionQuestion.photo_code == code.upper()).one_or_none()
    if row is None:
        return None
    session = db.get(StudySession, row.session_id)
    if session is None or session.user_id != user.id:
        return None
    return row


def _new_photo_code(db: Session) -> str:
    for _ in range(12):
        code = "".join(secrets.choice(CODE_LETTERS) for _ in range(4))
        if db.query(SessionQuestion).filter(SessionQuestion.photo_code == code).first() is None:
            return code
    raise ValueError("Could not create an answer code. Try again.")


def abandon_session(db: Session, session: StudySession) -> None:
    if session.status == "active":
        session.status = "abandoned"
        db.commit()


def public_session(db: Session, session: StudySession) -> dict:
    topic = db.get(Topic, session.topic_id)
    return _session_payload(db, session, topic, outcome="resume" if session.status == "active" else session.status)


def _resume(db: Session, user: User, session: StudySession) -> dict:
    topic = db.get(Topic, session.topic_id)
    if len(_questions(db, session.id)) < len(_plan(session)):
        _ensure_questions(db, user, session, topic)
    return _session_payload(db, session, topic, outcome="resume")


def _ensure_questions(db: Session, user: User, session: StudySession, topic: Topic) -> None:
    plan = _plan(session)
    missing = plan[len(_questions(db, session.id)) :]
    if not missing:
        return
    credential = resolve_credential(db, user, topic.group)
    if credential is None:
        raise NoCredentialError("Add an AI key in settings before starting a session")
    result = complete(
        db,
        user,
        credential=credential,
        purpose="question",
        schema=QuestionBatch,
        system=prompts.QUESTION_SYSTEM,
        user_prompt=_batch_prompt(db, topic, missing),
        temperature=0.7,
        max_tokens=min(4000, 500 * len(missing) + 200),
        group_id=session.group_id,
        topic_id=topic.id,
        session_id=session.id,
    )
    generated = result.parsed.questions
    if len(generated) < len(missing) or any(not item.question.strip() for item in generated[: len(missing)]):
        raise ProviderError("The model did not return the session questions")
    for slot, item in zip(missing, generated):
        _store_question(db, session, slot, item)
        db.flush()
    open_question = next((row for row in _questions(db, session.id) if row.answer_text is None), None)
    if open_question is not None:
        session.current_index = open_question.idx
    db.commit()


def _store_question(db, session, slot: PlannedSlot, generated: QuestionResult | None, commit: bool = False) -> SessionQuestion:
    if generated is None or not generated.question.strip():
        raise ProviderError("The model did not return the next question")
    subtopic = db.get(Subtopic, slot.subtopic_id)
    if slot.is_retest and subtopic is not None:
        subtopic.retest_next = False
    row = SessionQuestion(
        session_id=session.id,
        subtopic_id=slot.subtopic_id,
        idx=len(_questions(db, session.id)) + 1,
        qtype=slot.qtype,
        is_retest=slot.is_retest,
        ucb_at_pick=slot.ucb,
        question_text=generated.question.strip(),
        rubric_json=generated.rubric.model_dump(),
        asked_at=datetime.now(timezone.utc),
    )
    session.current_index = row.idx
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    return row


def start_practice(db: Session, user: User, topic: Topic, subtopic: Subtopic) -> dict:
    if topic.user_id != user.id or subtopic.topic_id != topic.id:
        raise ValueError("That subtopic is not on this topic")
    slot = PlannedSlot(
        subtopic_id=subtopic.id,
        name=subtopic.name,
        is_retest=False,
        ucb=None,
        untested=subtopic.attempts == 0,
        weakness=None,
        qtype="derive" if subtopic.kind == "math" else "explain",
    )
    session = StudySession(
        user_id=user.id,
        group_id=topic.group_id,
        topic_id=topic.id,
        status="practice",
        selection_pool="practice",
        selection_prob=0,
        planned_subtopics_json=[_slot_dict(slot)],
        question_count=1,
        current_index=0,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    _ensure_questions(db, user, session, topic)
    return _session_payload(db, session, topic, outcome="practice")


def _apply_evaluation(db: Session, question: SessionQuestion, evaluation: Evaluation, answer: str, official: bool = True) -> None:
    score = max(0, min(5, int(evaluation.score)))
    question.answer_text = answer
    question.score = score
    question.correct_claims_json = evaluation.correct_claims
    question.incomplete_claims_json = evaluation.incomplete_claims
    question.incorrect_claims_json = evaluation.incorrect_claims
    question.misconception_text = evaluation.misconception
    question.correction_text = evaluation.correction
    question.answered_at = datetime.now(timezone.utc)
    if official and score <= 2 and question.subtopic_id:
        subtopic = db.get(Subtopic, question.subtopic_id)
        if subtopic is not None:
            subtopic.retest_next = True


def _maybe_escalate(db, user, credential, session, topic, question, evaluation: Evaluation, prompt: str, schema, image=None) -> Evaluation:
    if evaluation.confidence >= 0.6 or not credential.eval_model or credential.eval_model == credential.question_model:
        return evaluation
    result = complete(
        db,
        user,
        credential=credential,
        purpose="final_summary" if schema is EvalAndSummary else "evaluate",
        schema=Evaluation,
        system=prompts.RUBRIC,
        user_prompt=prompt,
        model=credential.eval_model,
        temperature=0.2,
        max_tokens=800,
        allow_over_budget=True,
        group_id=session.group_id,
        topic_id=topic.id,
        subtopic_id=question.subtopic_id,
        session_id=session.id,
        question_id=question.id,
        image=image,
    )
    return result.parsed


def _finish(db: Session, session: StudySession, topic: Topic, summary: SummaryResult) -> dict:
    questions = _questions(db, session.id)
    for question in questions:
        if question.subtopic_id is None or question.score is None:
            continue
        subtopic = db.get(Subtopic, question.subtopic_id)
        subtopic.ewma_score = subtopic_ewma(subtopic.ewma_score, question.score)
        subtopic.attempts += 1
        subtopic.last_tested_at = question.answered_at
        _resolve_misconceptions(db, subtopic, question)
    scores = [question.score or 0 for question in questions]
    percent = session_percent(scores)
    now = datetime.now(timezone.utc)
    interval, ease = next_schedule(
        percent,
        interval_days=topic.interval_days,
        ease=topic.ease,
        sessions_completed=topic.sessions_completed,
    )
    previous = topic.mastery_level
    topic.interval_days = interval
    topic.ease = ease
    topic.sessions_completed += 1
    topic.status = "revised"
    topic.last_session_at = now
    topic.ewma_score = topic_ewma(topic.ewma_score, percent)
    topic.next_review_at = now + timedelta(days=interval)
    recent = [
        row.session_score
        for row in db.query(StudySession)
        .filter(StudySession.topic_id == topic.id, StudySession.status == "completed")
        .order_by(StudySession.completed_at)
        .all()
        if row.session_score is not None
    ]
    recent.append(percent)
    cores = [row for row in topic.subtopics if row.importance == "core"]
    core_ready = all((row.attempts or 0) >= 1 and (row.ewma_score or 0) >= 4 and row.open_misconceptions == 0 for row in cores) if cores else True
    topic.mastery_level = mastery_level(
        sessions_completed=topic.sessions_completed,
        recent_scores=recent[-3:],
        core_ready=core_ready,
        previous_level=previous,
        latest_score=percent,
    )
    weak_ids = [question.subtopic_id for question in questions if question.score is not None and question.score <= 3]
    stored = summary.model_dump()
    stored["weak_subtopic_ids"] = weak_ids
    session.total_score = sum(scores)
    session.session_score = percent
    session.summary_json = stored
    session.status = "completed"
    session.completed_at = now
    return {"session_score": percent, "summary": stored, "mastery_level": topic.mastery_level, "next_review_at": topic.next_review_at}


def _store_map(db: Session, user: User, topic: Topic, result: MapResult) -> None:
    seen = {row.name.strip().lower() for row in topic.subtopics}
    kept = 0
    for item in result.subtopics:
        name = item.name.strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        db.add(
            Subtopic(
                topic_id=topic.id,
                user_id=user.id,
                name=name[:160],
                description=item.description.strip()[:2000],
                importance=_clean(item.importance, _IMPORTANCE, "supporting"),
                kind=_clean(item.kind, _KINDS, "concept"),
                source="ai",
            )
        )
        kept += 1
        if kept >= 15:
            break
    if kept == 0:
        raise ProviderError("The model did not return any subtopics")
    topic.subtopic_map_version += 1
    db.commit()


def _upsert_misconception(db: Session, user: User, topic: Topic, question: SessionQuestion, text: str) -> None:
    summary = " ".join(text.split())
    key = summary.lower()[:240]
    row = (
        db.query(Misconception)
        .filter(Misconception.subtopic_id == question.subtopic_id, Misconception.resolved.is_(False))
        .all()
    )
    match = next((item for item in row if " ".join(item.summary.lower().split())[:240] == key), None)
    now = datetime.now(timezone.utc)
    if match is None:
        db.add(
            Misconception(
                user_id=user.id,
                topic_id=topic.id,
                subtopic_id=question.subtopic_id,
                summary=summary[:500],
                first_seen_at=now,
                last_seen_at=now,
            )
        )
    else:
        match.times_seen += 1
        match.last_seen_at = now
    subtopic = db.get(Subtopic, question.subtopic_id)
    subtopic.open_misconceptions = (
        db.query(Misconception).filter(Misconception.subtopic_id == subtopic.id, Misconception.resolved.is_(False)).count()
        + (0 if match else 1)
    )


def _resolve_misconceptions(db: Session, subtopic: Subtopic, question: SessionQuestion) -> None:
    if question.score is None or question.score < 4:
        subtopic.open_misconceptions = (
            db.query(Misconception).filter(Misconception.subtopic_id == subtopic.id, Misconception.resolved.is_(False)).count()
        )
        return
    open_rows = db.query(Misconception).filter(Misconception.subtopic_id == subtopic.id, Misconception.resolved.is_(False)).all()
    good = (
        db.query(SessionQuestion)
        .filter(SessionQuestion.subtopic_id == subtopic.id, SessionQuestion.score >= 4, SessionQuestion.answered_at.is_not(None))
        .all()
    )
    for row in open_rows:
        seen = row.first_seen_at
        later = [
            item
            for item in good
            if item.answered_at is not None and seen is not None and as_utc(item.answered_at) >= as_utc(seen)
        ]
        if len(later) >= 2:
            row.resolved = True
    subtopic.open_misconceptions = sum(1 for row in open_rows if not row.resolved)


def _questions(db: Session, session_id: int) -> list[SessionQuestion]:
    return db.query(SessionQuestion).filter(SessionQuestion.session_id == session_id).order_by(SessionQuestion.idx).all()


def _plan(session: StudySession) -> list[PlannedSlot]:
    slots = []
    for raw in session.planned_subtopics_json or []:
        slots.append(
            PlannedSlot(
                subtopic_id=raw["subtopic_id"],
                name=raw["name"],
                is_retest=raw["is_retest"],
                ucb=raw.get("ucb"),
                untested=raw.get("untested", False),
                weakness=raw.get("weakness"),
                qtype=raw["qtype"],
            )
        )
    return slots


def _views(rows) -> list[SubtopicView]:
    return [
        SubtopicView(
            id=row.id,
            name=row.name,
            importance=row.importance,
            kind=row.kind,
            ewma_score=row.ewma_score,
            attempts=row.attempts,
            open_misconceptions=row.open_misconceptions,
            retest_next=row.retest_next,
        )
        for row in rows
    ]


def _slot_dict(slot: PlannedSlot) -> dict:
    return {
        "subtopic_id": slot.subtopic_id,
        "name": slot.name,
        "is_retest": slot.is_retest,
        "ucb": slot.ucb,
        "untested": slot.untested,
        "weakness": slot.weakness,
        "qtype": slot.qtype,
    }


def _question_public(db: Session, row: SessionQuestion | None) -> dict | None:
    if row is None:
        return None
    subtopic = db.get(Subtopic, row.subtopic_id) if row.subtopic_id else None
    return {
        "idx": row.idx,
        "subtopic_id": row.subtopic_id,
        "subtopic_name": None if subtopic is None else subtopic.name,
        "qtype": row.qtype,
        "is_retest": row.is_retest,
        "text": row.question_text,
        "photo_code": row.photo_code,
    }


def _evaluation_public(row: SessionQuestion) -> dict | None:
    if row.score is None:
        return None
    return {
        "score": row.score,
        "correct_claims": row.correct_claims_json or [],
        "incomplete_claims": row.incomplete_claims_json or [],
        "incorrect_claims": row.incorrect_claims_json or [],
        "misconception": row.misconception_text,
        "correction": row.correction_text,
    }


def _session_payload(db: Session, session: StudySession, topic: Topic, *, outcome: str) -> dict:
    questions = _questions(db, session.id)
    open_question = next((row for row in questions if row.answer_text is None), None)
    return {
        "outcome": outcome,
        "session_id": session.id,
        "group_id": session.group_id,
        "status": session.status,
        "topic_id": topic.id,
        "topic_name": topic.name,
        "pool": session.selection_pool,
        "question": _question_public(db, open_question),
        "session_score": session.session_score,
        "answered": sum(1 for row in questions if row.score is not None),
        "question_count": session.question_count,
        "practice_result": _evaluation_public(next((row for row in questions if row.score is not None), None))
        if session.status == "practiced"
        else None,
    }


def _answer_payload(db, session, topic, current, summary: dict | None = None) -> dict:
    db.refresh(session)
    db.refresh(topic)
    questions = _questions(db, session.id)
    nxt = next((row for row in questions if row.idx == current.idx + 1), None)
    body = {
        "evaluation": _evaluation_public(current),
        "next_question": None if session.status in {"completed", "practiced"} else _question_public(db, nxt),
        "completed": session.status in {"completed", "practiced"},
        "counts": session.selection_pool != "practice",
        "session_score": session.session_score,
        "mastery_level": topic.mastery_level,
        "next_review_at": topic.next_review_at,
    }
    if summary is not None or session.summary_json:
        body["summary"] = summary["summary"] if summary and "summary" in summary else session.summary_json
    return body


def _map_prompt(topic: Topic) -> str:
    return (
        f"Group instructions:\n{topic.group.guidance_prompt or 'Cover the ideas needed to explain and use this topic.'}\n\n"
        f"Topic: {topic.name}\nNotes: {topic.notes or 'none'}"
    )


def _batch_prompt(db: Session, topic: Topic, slots: list[PlannedSlot]) -> str:
    lines = [
        f"Group instructions:\n{topic.group.guidance_prompt or 'Check real understanding.'}",
        "",
        f"Topic: {topic.name}",
        "Write one question for each slot, in order.",
        "",
    ]
    for number, slot in enumerate(slots, start=1):
        subtopic = db.get(Subtopic, slot.subtopic_id)
        name = subtopic.name if subtopic is not None else slot.name
        description = subtopic.description if subtopic is not None and subtopic.description else "core idea"
        lines.extend(
            [
                f"Question {number}",
                f"Subtopic: {name}",
                f"Scope: {description}",
                f"Question type: {slot.qtype}",
                f"Retest: {'yes' if slot.is_retest else 'no'}",
                "",
            ]
        )
    return "\n".join(lines)


def _image_list(image) -> list[tuple[str, str]]:
    if not image:
        return []
    if isinstance(image, tuple) and len(image) == 2 and isinstance(image[0], str):
        return [image]
    return list(image)


def _eval_prompt(topic, question: SessionQuestion, answer: str, digest: str, attached: bool = False, several: bool = False, summarize: bool = False) -> str:
    rubric = question.rubric_json or {}
    lines = [
        f"Topic: {topic.name}",
        f"Question: {question.question_text}",
        f"Hidden rubric key points: {rubric.get('key_points', [])}",
        f"Hidden common errors: {rubric.get('common_errors', [])}",
        f"Earlier answers this session: {digest or 'none'}",
        f"<learner_answer>\n{answer}\n</learner_answer>",
    ]
    if attached:
        lines.append(
            "Photos of handwritten working are attached, in the order they were sent. Read that handwriting as part of the answer."
            if several
            else "A photo of handwritten working is attached. Read that handwriting as part of the answer."
        )
    if summarize:
        lines.append("This is the last question. Include the session summary.")
    return "\n".join(lines)


def _digest(questions: list[SessionQuestion]) -> str:
    parts = []
    for row in questions:
        if row.score is None:
            continue
        parts.append(f"Q{row.idx} score {row.score}: {(row.misconception_text or 'no misconception')[:120]}")
    return " | ".join(parts[-3:])


def _clean(value: str, allowed: set[str], fallback: str) -> str:
    return value if value in allowed else fallback
