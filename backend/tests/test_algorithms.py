from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.services.planner import SubtopicView, plan_session
from app.services.scoring import mastery_level, next_schedule, session_percent, subtopic_ewma, topic_ewma
from app.services.selector import TopicCandidate, select_topic, selection_probabilities


def _pending(count: int) -> list[TopicCandidate]:
    return [TopicCandidate(id=i, name=f"p{i}", sessions_completed=0, ewma_score=None, last_session_at=None, interval_days=1) for i in range(count)]


def _revised(topic_id: int, ewma: float, days_ago: float, sessions: int, interval: float = 3) -> TopicCandidate:
    return TopicCandidate(
        id=topic_id,
        name=f"r{topic_id}",
        sessions_completed=sessions,
        ewma_score=ewma,
        last_session_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        interval_days=interval,
    )


def test_nothing_today_when_every_topic_was_done():
    topics = _pending(2)
    for topic in topics:
        topic.completed_today = True
    result = select_topic(topics, rng=random.Random(1))
    assert result.outcome == "nothing_today"


def test_pending_only_when_nothing_has_been_revised():
    result = select_topic(_pending(4), rng=random.Random(1))
    assert result.outcome == "selected"
    assert result.pool == "pending"


def test_pending_share_is_about_seventy_percent():
    topics = _pending(10) + [_revised(100, 80, 1, 3)]
    draws = 8000
    pending_hits = 0
    rng = random.Random(7)
    for _ in range(draws):
        result = select_topic(topics, rng=rng)
        if result.pool == "pending":
            pending_hits += 1
    ratio = pending_hits / draws
    assert 0.67 <= ratio <= 0.73


def test_each_pending_topic_has_equal_probability():
    topics = _pending(10) + [_revised(100, 50, 2, 2)]
    probs = selection_probabilities(topics)
    pending_probs = [probs[i] for i in range(10)]
    assert all(abs(item - 0.07) < 1e-9 for item in pending_probs)
    assert abs(sum(probs.values()) - 1) < 1e-9


def test_weaker_revised_topic_is_more_likely():
    topics = [
        _revised(1, 95, 0.2, 8, interval=14),
        _revised(2, 40, 10, 1, interval=1),
    ]
    probs = selection_probabilities(topics)
    assert probs[2] > probs[1]


def test_ucb_asks_untested_core_before_a_strong_subtopic():
    subtopics = [
        SubtopicView(1, "known", "core", "concept", ewma_score=5, attempts=6, open_misconceptions=0),
        SubtopicView(2, "new", "core", "concept", ewma_score=None, attempts=0, open_misconceptions=0),
        SubtopicView(3, "extra", "supporting", "concept", ewma_score=None, attempts=0, open_misconceptions=0),
    ]
    plan = plan_session(subtopics, question_count=2, rng=random.Random(1))
    assert plan[0].subtopic_id == 2
    assert plan[0].untested


def test_retest_is_reserved_before_ucb():
    subtopics = [
        SubtopicView(1, "gap", "supporting", "concept", ewma_score=1, attempts=4, open_misconceptions=1, retest_next=True),
        SubtopicView(2, "fresh", "core", "concept", ewma_score=None, attempts=0, open_misconceptions=0),
    ]
    plan = plan_session(subtopics, question_count=2, max_retests=2, rng=random.Random(1))
    assert plan[0].is_retest
    assert plan[0].subtopic_id == 1
    assert plan[1].subtopic_id == 2


def test_session_score_is_percent_of_five_point_rubric():
    assert session_percent([5, 5, 5, 5, 0]) == 80
    assert session_percent([4, 4, 4, 4, 4]) == 80


def test_schedule_and_mastery():
    interval, ease = next_schedule(90, interval_days=3, ease=2.5, sessions_completed=2)
    assert interval > 3
    assert ease > 2.5
    first_interval, _ = next_schedule(80, interval_days=1, ease=2.5, sessions_completed=0)
    assert first_interval == 3
    level = mastery_level(
        sessions_completed=3,
        recent_scores=[90, 88, 86],
        core_ready=True,
        previous_level="reviewing",
        latest_score=86,
    )
    assert level == "mastered"
    demoted = mastery_level(
        sessions_completed=3,
        recent_scores=[90, 90, 50],
        core_ready=True,
        previous_level="mastered",
        latest_score=50,
    )
    assert demoted != "mastered"
    assert topic_ewma(None, 80) == 80
    assert subtopic_ewma(None, 4) == 4
