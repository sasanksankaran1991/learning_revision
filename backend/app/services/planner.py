from __future__ import annotations

import math
import random
from dataclasses import dataclass

QUESTION_TYPES = ("explain", "apply", "compare", "derive", "debug_claim", "edge_case")
MATH_TYPES = ("derive", "apply", "explain", "debug_claim", "compare", "edge_case")


@dataclass
class SubtopicView:
    id: int
    name: str
    importance: str
    kind: str
    ewma_score: float | None
    attempts: int
    open_misconceptions: int
    retest_next: bool = False


@dataclass
class ScoredSubtopic:
    subtopic: SubtopicView
    weakness: float | None
    exploration: float | None
    ucb: float | None
    untested: bool


@dataclass
class PlannedSlot:
    subtopic_id: int
    name: str
    is_retest: bool
    ucb: float | None
    untested: bool
    weakness: float | None
    qtype: str


def weakness(subtopic: SubtopicView) -> float:
    ewma = 0.0 if subtopic.ewma_score is None else subtopic.ewma_score
    bonus = 0.15 if subtopic.open_misconceptions else 0.0
    return min(1.0, max(0.0, 1 - ewma / 5 + bonus))


def score_subtopics(subtopics: list[SubtopicView], ucb_c: float) -> list[ScoredSubtopic]:
    total_attempts = sum(max(item.attempts, 0) for item in subtopics)
    scored: list[ScoredSubtopic] = []
    for subtopic in subtopics:
        if subtopic.attempts <= 0:
            scored.append(ScoredSubtopic(subtopic, None, None, None, True))
            continue
        q = weakness(subtopic)
        bonus = 0.0
        if total_attempts > 1:
            bonus = ucb_c * math.sqrt(math.log(total_attempts) / subtopic.attempts)
        scored.append(ScoredSubtopic(subtopic, q, bonus, q + bonus, False))
    return scored


def _rank(scored: list[ScoredSubtopic], rng: random.Random) -> list[ScoredSubtopic]:
    untested_core = [item for item in scored if item.untested and item.subtopic.importance == "core"]
    untested_other = [item for item in scored if item.untested and item.subtopic.importance != "core"]
    tested = [item for item in scored if not item.untested]
    rng.shuffle(untested_core)
    rng.shuffle(untested_other)
    tested.sort(key=lambda item: item.ucb or 0, reverse=True)
    return untested_core + untested_other + tested


def _qtype(kind: str, previous: str | None) -> str:
    options = MATH_TYPES if kind == "math" else QUESTION_TYPES
    for option in options:
        if option != previous:
            return option
    return options[0]


def plan_session(
    subtopics: list[SubtopicView],
    *,
    question_count: int,
    ucb_c: float = 1.414,
    max_retests: int = 2,
    rng: random.Random | None = None,
) -> list[PlannedSlot]:
    rng = rng or random.Random()
    if question_count < 1 or not subtopics:
        return []

    scored = {item.subtopic.id: item for item in score_subtopics(subtopics, ucb_c)}
    retests = [item for item in subtopics if item.retest_next]
    retests.sort(key=lambda item: item.ewma_score if item.ewma_score is not None else -1)

    slots: list[tuple[SubtopicView, bool]] = []
    used: set[int] = set()
    for subtopic in retests[:max_retests]:
        slots.append((subtopic, True))
        used.add(subtopic.id)

    remaining = [item.subtopic for item in _rank(list(scored.values()), rng) if item.subtopic.id not in used]
    cursor = 0
    while len(slots) < question_count and remaining:
        slots.append((remaining[cursor % len(remaining)], False))
        cursor += 1
        if cursor == len(remaining) and len(slots) < question_count:
            # Every distinct subtopic is already placed; further slots repeat from the top.
            pass
        if cursor > question_count + len(remaining):
            break

    planned: list[PlannedSlot] = []
    previous: str | None = None
    for subtopic, is_retest in slots[:question_count]:
        info = scored[subtopic.id]
        qtype = _qtype(subtopic.kind, previous)
        previous = qtype
        planned.append(
            PlannedSlot(
                subtopic_id=subtopic.id,
                name=subtopic.name,
                is_retest=is_retest,
                ucb=None if is_retest else info.ucb,
                untested=info.untested,
                weakness=info.weakness,
                qtype=qtype,
            )
        )
    return planned
