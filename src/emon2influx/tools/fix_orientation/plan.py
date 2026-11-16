"""Read-only step: which periods of which households have swapped counters (§13)."""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .store import Correction, Store, day_start
from .votes import Segment, Vote, segments, vote_days


@dataclass
class Period:
    signature: str
    start: str  # UTC, ISO 8601
    stop: str
    first_day: str
    last_day: str
    voted_days: int
    days: int
    agreement: float

    def bounds(self) -> tuple[datetime, datetime]:
        return datetime.fromisoformat(self.start), datetime.fromisoformat(self.stop)


@dataclass
class HouseholdPlan:
    signature: str
    periods: list[Period] = field(default_factory=list)
    review: list[str] = field(default_factory=list)  # segments that need a human look
    skipped: list[str] = field(default_factory=list)  # already corrected


@dataclass
class Plan:
    bucket: str
    until: str
    created: str
    households: list[HouseholdPlan] = field(default_factory=list)

    def periods(self) -> list[Period]:
        return [p for h in self.households for p in h.periods]

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> "Plan":
        raw = json.loads(path.read_text())
        households = [
            HouseholdPlan(
                h["signature"], [Period(**p) for p in h["periods"]], h["review"], h["skipped"]
            )
            for h in raw["households"]
        ]
        return cls(raw["bucket"], raw["until"], raw["created"], households)


def effective_corrections(corrections: list[Correction]) -> list[Correction]:
    """Per period the latest record counts: a restore cancels an earlier correction."""
    latest: dict[tuple[datetime, datetime], Correction] = {}
    for c in sorted(corrections, key=lambda c: c.recorded_at):
        latest[(c.start, c.stop)] = c
    return [c for c in latest.values() if c.kind == "counters_swapped"]


def plan_household(store: Store, signature: str, start: datetime, until: datetime) -> HouseholdPlan:
    plan = HouseholdPlan(signature)
    days = store.daily_stats(signature, start, until)
    if not days:
        return plan
    done = effective_corrections(store.corrections(signature))
    for segment in segments(vote_days(days)):
        if segment.orientation is Vote.SWAPPED and not segment.correct:
            plan.review.append(_describe(segment))
            continue
        if not segment.correct:
            if segment.orientation is Vote.UNCLEAR and segment.days > 0:
                plan.review.append(_describe(segment))
            continue
        period_start = max(start, day_start(segment.first))
        period_stop = min(until, day_start(segment.last + timedelta(days=1)))
        if period_start >= period_stop:
            continue
        if any(c.start < period_stop and period_start < c.stop for c in done):
            plan.skipped.append(f"{segment.first} - {segment.last}: already corrected")
            continue
        plan.periods.append(
            Period(
                signature,
                period_start.isoformat(),
                period_stop.isoformat(),
                segment.first.isoformat(),
                segment.last.isoformat(),
                segment.voted_days,
                segment.days,
                round(segment.agreement, 3),
            )
        )
    return plan


def _describe(segment: Segment) -> str:
    return (
        f"{segment.first} - {segment.last} ({segment.days} days, {segment.orientation}): "
        f"{segment.note}"
    )


def make_plan(store: Store, signatures: list[str], start: datetime, until: datetime) -> Plan:
    plan = Plan(store.bucket, until.isoformat(), datetime.now(UTC).isoformat())
    for signature in signatures:
        try:
            household = plan_household(store, signature, start, until)
        except Exception as error:  # one household must not stop the whole plan
            household = HouseholdPlan(signature, review=[f"not analysed: {str(error)[:120]}"])
        if household.periods or household.review or household.skipped:
            plan.households.append(household)
    return plan
