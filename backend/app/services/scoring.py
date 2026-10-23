from __future__ import annotations


def session_percent(scores: list[int]) -> float:
    if not scores:
        return 0.0
    return sum(scores) / (5 * len(scores)) * 100


def topic_ewma(previous: float | None, session_score: float, alpha: float = 0.5) -> float:
    if previous is None:
        return session_score
    return alpha * session_score + (1 - alpha) * previous


def subtopic_ewma(previous: float | None, score: float, alpha: float = 0.6) -> float:
    if previous is None:
        return float(score)
    return alpha * score + (1 - alpha) * previous


def next_schedule(
    session_score: float,
    *,
    interval_days: float,
    ease: float,
    sessions_completed: int,
) -> tuple[float, float]:
    """Return the next interval in days and the updated ease. `sessions_completed` is the count before this session."""
    ease = 2.5 if ease <= 0 else ease
    interval = interval_days if interval_days > 0 else 1.0
    if sessions_completed <= 0:
        interval = 1.0 if session_score < 70 else 3.0
    elif session_score < 50:
        interval = 1.0
        ease -= 0.20
    elif session_score < 70:
        interval = max(1.0, interval * 1.2)
        ease -= 0.15
    elif session_score < 85:
        interval = interval * ease
    else:
        interval = interval * ease * 1.3
        ease += 0.10
    ease = min(3.0, max(1.3, ease))
    interval = min(120.0, max(1.0, interval))
    return interval, ease


def mastery_level(
    *,
    sessions_completed: int,
    recent_scores: list[float],
    core_ready: bool,
    previous_level: str,
    latest_score: float | None,
) -> str:
    """`recent_scores` is oldest-first and should include the latest session. `core_ready` is true when every core subtopic has been tested with ewma >= 4 and has no open misconception."""
    if sessions_completed <= 0:
        level = "new"
    else:
        last2 = recent_scores[-2:]
        last3 = recent_scores[-3:]
        reviewing = sessions_completed >= 2 and len(last2) >= 2 and sum(last2) / len(last2) >= 70
        mastered = (
            sessions_completed >= 3
            and len(last3) >= 3
            and sum(last3) / len(last3) >= 85
            and min(last3) >= 75
            and core_ready
        )
        if mastered:
            level = "mastered"
        elif reviewing:
            level = "reviewing"
        else:
            level = "learning"

    if latest_score is not None and latest_score < 60:
        if previous_level == "mastered" and level == "mastered":
            level = "reviewing"
        elif previous_level == "reviewing" and level in {"reviewing", "mastered"}:
            level = "learning"
    return level
