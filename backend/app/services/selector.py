from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class TopicCandidate:
    id: int
    name: str
    sessions_completed: int
    ewma_score: float | None
    last_session_at: datetime | None
    interval_days: float
    completed_today: bool = False
    in_progress: bool = False
    active: bool = True


@dataclass
class Selection:
    outcome: str
    topic_id: int | None = None
    pool: str | None = None
    probability: float = 0
    debug: dict | None = None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _days_since(moment: datetime | None, now: datetime) -> float:
    if moment is None:
        return 10_000
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - moment).total_seconds() / 86400)


def component_scores(topic: TopicCandidate, now: datetime) -> tuple[float, float, float]:
    ewma = 0.0 if topic.ewma_score is None else topic.ewma_score
    error = _clamp(1 - ewma / 100, 0, 1)
    interval = topic.interval_days if topic.interval_days > 0 else 1
    recency = _clamp(_days_since(topic.last_session_at, now) / (2 * interval), 0, 1)
    attempts = 1 / math.sqrt(max(topic.sessions_completed, 1))
    return error, recency, attempts


def _revised_weight(
    topic: TopicCandidate,
    now: datetime,
    w_error: float,
    w_recency: float,
    w_attempts: float,
    floor: float,
) -> float:
    error, recency, attempts = component_scores(topic, now)
    return max(w_error * error + w_recency * recency + w_attempts * attempts, floor)


def selection_probabilities(
    topics: list[TopicCandidate],
    *,
    pending_ratio: float = 0.70,
    w_error: float = 0.50,
    w_recency: float = 0.30,
    w_attempts: float = 0.20,
    floor: float = 0.05,
    now: datetime | None = None,
) -> dict[int, float]:
    """Overall probability of each topic, after the once-per-day filter."""
    now = now or datetime.now(timezone.utc)
    available = [topic for topic in topics if topic.active and not topic.in_progress and not topic.completed_today]
    pending = [topic for topic in available if topic.sessions_completed == 0]
    revised = [topic for topic in available if topic.sessions_completed >= 1]
    probs = {topic.id: 0.0 for topic in topics}
    if not pending and not revised:
        return probs

    if pending and revised:
        pending_mass, revised_mass = pending_ratio, 1 - pending_ratio
    elif pending:
        pending_mass, revised_mass = 1.0, 0.0
    else:
        pending_mass, revised_mass = 0.0, 1.0

    if pending and pending_mass:
        share = pending_mass / len(pending)
        for topic in pending:
            probs[topic.id] = share

    if revised and revised_mass:
        weights = [
            _revised_weight(topic, now, w_error, w_recency, w_attempts, floor) for topic in revised
        ]
        total = sum(weights)
        for topic, weight in zip(revised, weights):
            probs[topic.id] = revised_mass * (weight / total)
    return probs


def select_topic(
    topics: list[TopicCandidate],
    *,
    rng: random.Random,
    pending_ratio: float = 0.70,
    w_error: float = 0.50,
    w_recency: float = 0.30,
    w_attempts: float = 0.20,
    floor: float = 0.05,
    now: datetime | None = None,
) -> Selection:
    now = now or datetime.now(timezone.utc)
    active = [topic for topic in topics if topic.active and not topic.in_progress]
    available = [topic for topic in active if not topic.completed_today]
    if not active:
        return Selection(outcome="no_topics")
    if not available:
        return Selection(outcome="nothing_today")

    pending = [topic for topic in available if topic.sessions_completed == 0]
    revised = [topic for topic in available if topic.sessions_completed >= 1]
    if pending and revised:
        pool_name = "pending" if rng.random() < pending_ratio else "revised"
    elif pending:
        pool_name = "pending"
    else:
        pool_name = "revised"
    pool = pending if pool_name == "pending" else revised

    probs = selection_probabilities(
        topics,
        pending_ratio=pending_ratio,
        w_error=w_error,
        w_recency=w_recency,
        w_attempts=w_attempts,
        floor=floor,
        now=now,
    )
    if pool_name == "pending":
        chosen = rng.choice(pool)
    else:
        chosen = rng.choices(
            pool,
            weights=[_revised_weight(topic, now, w_error, w_recency, w_attempts, floor) for topic in pool],
            k=1,
        )[0]

    debug_rows = []
    for topic in available:
        error, recency, attempts = component_scores(topic, now)
        debug_rows.append(
            {
                "id": topic.id,
                "name": topic.name,
                "pool": "pending" if topic.sessions_completed == 0 else "revised",
                "E": round(error, 4),
                "R": round(recency, 4),
                "A": round(attempts, 4),
                "probability": round(probs[topic.id], 6),
            }
        )
    return Selection(
        outcome="selected",
        topic_id=chosen.id,
        pool="pending" if chosen.sessions_completed == 0 else "revised",
        probability=probs[chosen.id],
        debug={"pool_drawn": pool_name, "candidates": debug_rows},
    )
