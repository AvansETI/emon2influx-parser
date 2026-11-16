"""Deciding, day by day, which counter held the import energy (doc/architecture.md §13).

The rule is the p1-meter-app's (apps/api/src/data/orientation.ts): on a complete day, the
counter whose daily increase is closer to the integral of `power_consumed` is the import
counter. For a correction it is applied more strictly: only clear days vote, gaps take the
orientation of their neighbours only when both agree, and short runs are reported, not
corrected.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from statistics import median


class Vote(StrEnum):
    AS_NAMED = "as-named"
    SWAPPED = "swapped"
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class DayStats:
    day: date
    points: int
    import_kwh: float | None  # integral of power_consumed
    delta_consumed: float | None  # daily increase of energy_consumed
    delta_produced: float | None  # daily increase of energy_produced


@dataclass(frozen=True)
class DayVote:
    day: date
    vote: Vote
    reason: str


@dataclass(frozen=True)
class Segment:
    """Consecutive days with one orientation, from `first` to `last` (inclusive)."""

    first: date
    last: date
    orientation: Vote
    voted_days: int  # days that voted for this orientation
    days: int
    agreement: float  # share of voting days inside the segment that agree
    correct: bool  # swapped and clear enough to correct
    note: str


# The app's thresholds.
MIN_COMPLETENESS = 0.9
MIN_IMPORT_KWH = 1.0
# Stricter, for corrections.
MIN_MARGIN_KWH = 0.2  # the closer counter must be at least this much closer
MIN_SEGMENT_VOTES = 3
MIN_AGREEMENT = 0.8
WINDOW_DAYS = 15  # completeness against the median of ±15 days: the send interval changes


def vote_days(days: list[DayStats]) -> list[DayVote]:
    days = sorted(days, key=lambda d: d.day)
    votes = []
    for i, day in enumerate(days):
        window = [
            d.points for d in days[max(0, i - WINDOW_DAYS) : i + WINDOW_DAYS + 1] if d.points > 0
        ]
        expected = median(window) if window else 0
        votes.append(DayVote(day.day, *_vote(day, expected)))
    return votes


def _vote(day: DayStats, expected: float) -> tuple[Vote, str]:
    if day.points < expected * MIN_COMPLETENESS:
        return Vote.UNCLEAR, f"incomplete ({day.points} of ~{expected:.0f} points)"
    if day.import_kwh is None or day.import_kwh < MIN_IMPORT_KWH:
        return Vote.UNCLEAR, "too little use"
    if day.delta_consumed is None or day.delta_produced is None:
        return Vote.UNCLEAR, "a counter is missing"
    error_consumed = abs(day.delta_consumed - day.import_kwh)
    error_produced = abs(day.delta_produced - day.import_kwh)
    if abs(error_consumed - error_produced) < MIN_MARGIN_KWH:
        return Vote.UNCLEAR, "both counters are equally close"
    if error_consumed < error_produced:
        return Vote.AS_NAMED, f"energy_consumed off by {error_consumed:.2f} kWh"
    return Vote.SWAPPED, f"energy_produced off by {error_produced:.2f} kWh"


def segments(votes: list[DayVote]) -> list[Segment]:
    """Fills unclear days from agreeing neighbours, then groups runs of one orientation."""
    labels = _absorb_noise(votes, _fill([v.vote for v in votes]))
    result: list[Segment] = []
    start = 0
    for i in range(1, len(votes) + 1):
        if i == len(votes) or labels[i] != labels[start]:
            result.append(_segment(votes[start:i], labels[start]))
            start = i
    return result


def _fill(labels: list[Vote]) -> list[Vote]:
    filled = list(labels)
    i = 0
    while i < len(filled):
        if filled[i] is not Vote.UNCLEAR:
            i += 1
            continue
        j = i
        while j < len(filled) and filled[j] is Vote.UNCLEAR:
            j += 1
        before = filled[i - 1] if i > 0 else None
        after = filled[j] if j < len(filled) else None
        # A gap at the edge follows its only neighbour; inside, both sides must agree.
        fill = (
            before
            if after is None
            else after
            if before is None
            else (before if before == after else None)
        )
        if fill is not None:
            filled[i:j] = [fill] * (j - i)
        i = j
    return filled


def _runs(labels: list[Vote]) -> list[tuple[int, int]]:
    runs, start = [], 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            runs.append((start, i))
            start = i
    return runs


def _absorb_noise(votes: list[DayVote], labels: list[Vote]) -> list[Vote]:
    """A short run (fewer than MIN_SEGMENT_VOTES clear days) between two neighbours with the
    same orientation is noise: it takes that orientation. Its own votes then lower the
    segment's agreement, which is reported."""
    labels = list(labels)
    changed = True
    while changed:
        changed = False
        runs = _runs(labels)
        for k in range(1, len(runs) - 1):
            start, end = runs[k]
            before, after = labels[runs[k - 1][0]], labels[runs[k + 1][0]]
            own = sum(1 for v in votes[start:end] if v.vote == labels[start])
            if (
                before == after != labels[start]
                and before is not Vote.UNCLEAR
                and (own < MIN_SEGMENT_VOTES)
            ):
                labels[start:end] = [before] * (end - start)
                changed = True
                break
    return labels


def _segment(votes: list[DayVote], label: Vote) -> Segment:
    own = sum(1 for v in votes if v.vote == label)
    other = sum(1 for v in votes if v.vote not in (label, Vote.UNCLEAR))
    agreement = own / (own + other) if own + other else 0.0
    correct = label == Vote.SWAPPED and own >= MIN_SEGMENT_VOTES and agreement >= MIN_AGREEMENT
    if label == Vote.UNCLEAR:
        note = "orientation unknown (no clear days, or a change inside a gap): not corrected"
    elif label == Vote.SWAPPED and not correct:
        note = f"too few clear days ({own}) to correct: review by hand"
    else:
        note = ""
    return Segment(votes[0].day, votes[-1].day, label, own, len(votes), agreement, correct, note)
