from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_ready_user
from app.models import (
    AiUsage,
    Group,
    Misconception,
    Session as StudySession,
    SessionQuestion,
    Subtopic,
    Topic,
    User,
    UserSettings,
)
from app.schemas import AnswerIn, GroupIn, GroupOut, GroupPatch, SubtopicIn, TopicImport, TopicIn, TopicPatch
from app.services.planner import SubtopicView, plan_session, score_subtopics
from app.services.selector import TopicCandidate, select_topic

router = APIRouter(tags=["library"])


def _owned_group(db: Session, user: User, group_id: int) -> Group:
    group = db.get(Group, group_id)
    if group is None or group.user_id != user.id:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


def _owned_topic(db: Session, user: User, topic_id: int) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None or topic.user_id != user.id:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic


def _delete_topic_rows(db: Session, topic: Topic) -> None:
    session_ids = [row.id for row in db.query(StudySession.id).filter(StudySession.topic_id == topic.id)]
    question_ids = []
    if session_ids:
        question_ids = [row.id for row in db.query(SessionQuestion.id).filter(SessionQuestion.session_id.in_(session_ids))]
    subtopic_ids = [row.id for row in db.query(Subtopic.id).filter(Subtopic.topic_id == topic.id)]
    if question_ids:
        db.query(AiUsage).filter(AiUsage.question_id.in_(question_ids)).update({AiUsage.question_id: None}, synchronize_session=False)
    if session_ids:
        db.query(AiUsage).filter(AiUsage.session_id.in_(session_ids)).update({AiUsage.session_id: None}, synchronize_session=False)
        db.query(SessionQuestion).filter(SessionQuestion.session_id.in_(session_ids)).delete(synchronize_session=False)
        db.query(StudySession).filter(StudySession.id.in_(session_ids)).delete(synchronize_session=False)
    if subtopic_ids:
        db.query(AiUsage).filter(AiUsage.subtopic_id.in_(subtopic_ids)).update({AiUsage.subtopic_id: None}, synchronize_session=False)
        db.query(Misconception).filter(Misconception.subtopic_id.in_(subtopic_ids)).delete(synchronize_session=False)
        db.query(Subtopic).filter(Subtopic.parent_subtopic_id.in_(subtopic_ids)).update({Subtopic.parent_subtopic_id: None}, synchronize_session=False)
        db.query(Subtopic).filter(Subtopic.topic_id == topic.id).delete(synchronize_session=False)
    db.query(Misconception).filter(Misconception.topic_id == topic.id).delete(synchronize_session=False)
    db.query(AiUsage).filter(AiUsage.topic_id == topic.id).update({AiUsage.topic_id: None}, synchronize_session=False)
    db.delete(topic)


def _completed_today(moment: datetime | None, tz_name: str, now: datetime) -> bool:
    if moment is None:
        return False
    tz = ZoneInfo(tz_name)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local_now = now.astimezone(tz)
    return moment.astimezone(tz).date() == local_now.date()


def _subtopic_views(rows: list[Subtopic]) -> list[SubtopicView]:
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


def _group_rows(db: Session, groups: list[Group]) -> list[dict]:
    ids = [group.id for group in groups]
    topics = db.query(Topic).filter(Topic.group_id.in_(ids), Topic.active.is_(True)).all() if ids else []
    topics_by_group: dict[int, list[Topic]] = defaultdict(list)
    for topic in topics:
        topics_by_group[topic.group_id].append(topic)
    topic_ids = [topic.id for topic in topics]
    subtopics = db.query(Subtopic).filter(Subtopic.topic_id.in_(topic_ids)).all() if topic_ids else []
    group_of_topic = {topic.id: topic.group_id for topic in topics}
    subtopics_by_group: dict[int, list[Subtopic]] = defaultdict(list)
    for subtopic in subtopics:
        subtopics_by_group[group_of_topic[subtopic.topic_id]].append(subtopic)
    active_sessions = (
        db.query(StudySession).filter(StudySession.group_id.in_(ids), StudySession.status == "active").all() if ids else []
    )
    active_by_group = {session.group_id: session.id for session in active_sessions}
    rows = []
    for group in groups:
        group_topics = topics_by_group[group.id]
        group_subtopics = subtopics_by_group[group.id]
        scores = [topic.ewma_score for topic in group_topics if topic.ewma_score is not None]
        rows.append(
            {
                "id": group.id,
                "name": group.name,
                "guidance_prompt": group.guidance_prompt,
                "archived": group.archived,
                "created_at": group.created_at,
                "topic_count": len(group_topics),
                "subtopic_count": len(group_subtopics),
                "unanswered_count": sum(1 for subtopic in group_subtopics if subtopic.attempts == 0),
                "average_score": round(sum(scores) / len(scores)) if scores else None,
                "session_count": sum(topic.sessions_completed for topic in group_topics),
                "active_session_id": active_by_group.get(group.id),
            }
        )
    return rows


@router.get("/groups", response_model=list[GroupOut])
def list_groups(user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> list[dict]:
    groups = (
        db.query(Group)
        .filter(Group.user_id == user.id, Group.archived.is_(False))
        .order_by(Group.id)
        .all()
    )
    return _group_rows(db, groups)


@router.post("/groups", response_model=GroupOut)
def create_group(body: GroupIn, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> Group:
    group = Group(user_id=user.id, name=body.name.strip(), guidance_prompt=body.guidance_prompt.strip())
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.patch("/groups/{group_id}", response_model=GroupOut)
def patch_group(
    group_id: int,
    body: GroupPatch,
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
) -> Group:
    group = _owned_group(db, user, group_id)
    if body.name is not None:
        group.name = body.name.strip()
    if body.guidance_prompt is not None:
        group.guidance_prompt = body.guidance_prompt.strip()
    if body.archived is not None:
        group.archived = body.archived
    db.commit()
    db.refresh(group)
    return group


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    group = _owned_group(db, user, group_id)
    for topic in db.query(Topic).filter(Topic.group_id == group.id).all():
        _delete_topic_rows(db, topic)
    db.query(AiUsage).filter(AiUsage.group_id == group.id).update({AiUsage.group_id: None}, synchronize_session=False)
    db.delete(group)
    db.commit()
    return {"ok": True}


@router.get("/groups/{group_id}/topics")
def list_topics(group_id: int, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> list[dict]:
    _owned_group(db, user, group_id)
    rows = db.query(Topic).filter(Topic.group_id == group_id, Topic.user_id == user.id).order_by(Topic.id).all()
    topic_ids = [row.id for row in rows]
    counts: dict[int, dict[str, int]] = defaultdict(lambda: {"subtopics": 0, "unanswered": 0})
    if topic_ids:
        for subtopic in db.query(Subtopic).filter(Subtopic.topic_id.in_(topic_ids)).all():
            bucket = counts[subtopic.topic_id]
            bucket["subtopics"] += 1
            if subtopic.attempts == 0:
                bucket["unanswered"] += 1
    payloads = []
    for row in rows:
        item = _topic_out(row)
        item["subtopic_count"] = counts[row.id]["subtopics"]
        item["unanswered_count"] = counts[row.id]["unanswered"]
        payloads.append(item)
    return payloads


@router.post("/groups/{group_id}/topics")
def add_topic(
    group_id: int,
    body: TopicIn,
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
) -> dict:
    _owned_group(db, user, group_id)
    return _insert_topic(db, user, group_id, body.name, body.notes)


@router.post("/groups/{group_id}/topics/import")
def import_topics(
    group_id: int,
    body: TopicImport,
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
) -> dict:
    _owned_group(db, user, group_id)
    created = []
    skipped = []
    for line in body.text.splitlines():
        name = line.strip()
        if not name:
            continue
        exists = db.query(Topic).filter(Topic.group_id == group_id, Topic.name == name).first()
        if exists:
            skipped.append(name)
            continue
        created.append(_insert_topic(db, user, group_id, name, ""))
    return {"created": created, "skipped": skipped}


@router.get("/topics/{topic_id}")
def get_topic(topic_id: int, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    topic = _owned_topic(db, user, topic_id)
    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    views = _subtopic_views(topic.subtopics)
    scored = {item.subtopic.id: item for item in score_subtopics(views, settings.ucb_c)}
    payload = _topic_out(topic)
    payload["subtopics"] = []
    for row in topic.subtopics:
        info = scored[row.id]
        payload["subtopics"].append(
            {
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "importance": row.importance,
                "kind": row.kind,
                "source": row.source,
                "ewma_score": row.ewma_score,
                "attempts": row.attempts,
                "open_misconceptions": row.open_misconceptions,
                "retest_next": row.retest_next,
                "weakness": info.weakness,
                "exploration": info.exploration,
                "ucb": info.ucb,
                "untested": info.untested,
            }
        )
    return payload


@router.patch("/topics/{topic_id}")
def patch_topic(
    topic_id: int,
    body: TopicPatch,
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
) -> dict:
    topic = _owned_topic(db, user, topic_id)
    if body.name is not None:
        topic.name = body.name.strip()
    if body.notes is not None:
        topic.notes = body.notes
    if body.active is not None:
        topic.active = body.active
    db.commit()
    db.refresh(topic)
    return _topic_out(topic)


@router.delete("/topics/{topic_id}")
def delete_topic(topic_id: int, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    topic = _owned_topic(db, user, topic_id)
    _delete_topic_rows(db, topic)
    db.commit()
    return {"ok": True}


@router.post("/topics/{topic_id}/subtopics")
def add_subtopic(
    topic_id: int,
    body: SubtopicIn,
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
) -> dict:
    topic = _owned_topic(db, user, topic_id)
    if db.query(Subtopic).filter(Subtopic.topic_id == topic.id, Subtopic.name == body.name.strip()).first():
        raise HTTPException(status_code=409, detail="That subtopic already exists")
    row = Subtopic(
        topic_id=topic.id,
        user_id=user.id,
        name=body.name.strip(),
        description=body.description.strip(),
        importance=body.importance,
        kind=body.kind,
        source="user",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "name": row.name, "source": row.source}


@router.delete("/topics/{topic_id}/subtopics/{subtopic_id}")
def delete_subtopic(
    topic_id: int,
    subtopic_id: int,
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
) -> dict:
    topic = _owned_topic(db, user, topic_id)
    row = db.get(Subtopic, subtopic_id)
    if row is None or row.topic_id != topic.id or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Subtopic not found")
    active = (
        db.query(StudySession)
        .filter(StudySession.topic_id == topic.id, StudySession.user_id == user.id, StudySession.status == "active")
        .first()
    )
    if active is not None:
        raise HTTPException(status_code=409, detail="Finish the active session before deleting a subtopic")
    db.query(SessionQuestion).filter(SessionQuestion.subtopic_id == row.id).update({SessionQuestion.subtopic_id: None})
    db.query(Misconception).filter(Misconception.subtopic_id == row.id).update({Misconception.subtopic_id: None})
    db.query(AiUsage).filter(AiUsage.subtopic_id == row.id).update({AiUsage.subtopic_id: None})
    db.query(Subtopic).filter(Subtopic.parent_subtopic_id == row.id).update({Subtopic.parent_subtopic_id: None})
    db.flush()
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/groups/{group_id}/preview-next")
def preview_next(group_id: int, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    """Choose the next topic and subtopic plan. Does not start a session or call the AI."""
    group = _owned_group(db, user, group_id)
    active = (
        db.query(StudySession)
        .filter(StudySession.group_id == group.id, StudySession.user_id == user.id, StudySession.status == "active")
        .one_or_none()
    )
    if active is not None:
        return {"outcome": "resume", "session_id": active.id, "topic_id": active.topic_id}

    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    now = datetime.now(timezone.utc)
    topics = db.query(Topic).filter(Topic.group_id == group.id, Topic.user_id == user.id).all()
    candidates = []
    for topic in topics:
        last_done = (
            db.query(StudySession)
            .filter(StudySession.topic_id == topic.id, StudySession.status == "completed")
            .order_by(StudySession.completed_at.desc())
            .first()
        )
        candidates.append(
            TopicCandidate(
                id=topic.id,
                name=topic.name,
                sessions_completed=topic.sessions_completed,
                ewma_score=topic.ewma_score,
                last_session_at=topic.last_session_at,
                interval_days=topic.interval_days,
                completed_today=_completed_today(last_done.completed_at if last_done else None, user.timezone, now),
                active=topic.active,
            )
        )
    selection = select_topic(
        candidates,
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
    plan = plan_session(
        _subtopic_views(topic.subtopics if topic else []),
        question_count=settings.questions_per_session,
        ucb_c=settings.ucb_c,
        max_retests=settings.max_retests_per_session,
        rng=random.SystemRandom(),
    )
    return {
        "outcome": "selected",
        "topic_id": selection.topic_id,
        "topic_name": topic.name if topic else None,
        "pool": selection.pool,
        "probability": selection.probability,
        "plan": [slot.__dict__ for slot in plan],
        "note": "Preview only. The session is not saved, and no question has been generated.",
    }


def _insert_topic(db: Session, user: User, group_id: int, name: str, notes: str) -> dict:
    name = name.strip()
    if db.query(Topic).filter(Topic.group_id == group_id, Topic.name == name).first():
        raise HTTPException(status_code=409, detail="That topic already exists in this group")
    topic = Topic(group_id=group_id, user_id=user.id, name=name, notes=notes.strip())
    db.add(topic)
    db.commit()
    db.refresh(topic)
    from app.services.study import ensure_subtopic_map

    payload = _topic_out(topic)
    payload["map_status"] = ensure_subtopic_map(db, user, topic)
    payload["subtopic_count"] = db.query(Subtopic).filter(Subtopic.topic_id == topic.id).count()
    return payload


def _topic_out(topic: Topic) -> dict:
    return {
        "id": topic.id,
        "group_id": topic.group_id,
        "name": topic.name,
        "notes": topic.notes,
        "status": topic.status,
        "mastery_level": topic.mastery_level,
        "ewma_score": topic.ewma_score,
        "sessions_completed": topic.sessions_completed,
        "active": topic.active,
        "next_review_at": topic.next_review_at,
    }
