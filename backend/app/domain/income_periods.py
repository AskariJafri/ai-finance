"""Attribute income deposits to the month they were *scheduled* to be paid in.

Bi-weekly pay doesn't line up with calendar months: 26 cheques a year against
12 months. On top of that, banks move a deposit *earlier* when the payday falls
on a weekend or a bank holiday — so a cheque due Saturday August 1st lands on
Friday July 31st. Counting it in July inflates July's detected income, and
since every 50/30/20 target is a percentage of detected income, it inflates all
three targets and makes "left to spend" wrong in both months.

The fix is to learn the schedule rather than guess from the day-of-month. A
bi-weekly schedule is an arithmetic sequence with a period of exactly 14 days,
so it is fully described by one number — its phase. Fit that phase to the
deposits actually observed and every deposit maps back to the *nominal* payday
it was paid against; the deposit is then counted in the month that payday falls
in. That is what "the first pay of the month, then two weeks later, and so on,
and anything before that belongs to the previous month" means once it's made
precise: the month boundary is drawn at the schedule's first payday of the
month, not at whatever date the money happened to clear.

So for a schedule whose nominal paydays are July 17th and 31st, a deposit that
clears on July 30th is July's. For a schedule whose nominal paydays are July
18th and August 1st, a deposit clearing July 31st is *August's*.

Only applied when the deposits actually look bi-weekly. Someone paid monthly,
or semi-monthly on the 1st and 15th, keeps plain calendar attribution —
inventing a 14-day schedule for *them* would be flatly wrong, and those are far
more common pay schedules than bi-weekly.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta
from enum import StrEnum
from itertools import pairwise

# Bi-weekly is every 14 days; allow a couple of days of slack for weekends and
# bank holidays shifting a deposit earlier or later.
BIWEEKLY_MIN_GAP_DAYS = 12
BIWEEKLY_MAX_GAP_DAYS = 16

# The exact period of a bi-weekly schedule. Attribution leans on this being
# exact — it is what makes a single phase enough to pin down every payday.
BIWEEKLY_PERIOD_DAYS = 14

# Furthest a deposit can sit from its nominal payday. Half the period, because
# past that point the deposit is closer to the *next* payday and belongs to
# that one instead. Also the widest a caller's date range has to be padded by
# for attribution to be able to pull a deposit into the range.
MAX_SHIFT_DAYS = BIWEEKLY_PERIOD_DAYS // 2

# Below this many deposits there isn't enough history to call the cadence, and
# guessing wrong is worse than leaving the calendar attribution alone.
MIN_DEPOSITS_TO_DETECT = 4

# Only the most recent N deposits are considered, so the drift ratio below
# means the same thing whether the caller passes six months of history or six
# years (there are only 31 possible days-of-month to spread across). It also
# keeps the phase fit anchored to the schedule in force *now*, so a job change
# that moves payday converges in a couple of months instead of being averaged
# against a year of the old schedule.
DETECTION_WINDOW = 15

# Fraction of deposits that must land on a *distinct* day-of-month.
#
# This is what separates bi-weekly from semi-monthly, and the median gap alone
# cannot: someone paid on the 1st and the 15th has gaps of 14, 17, 14, 17…
# whose median is 14, sitting squarely in the bi-weekly range. The structural
# difference is that a semi-monthly schedule is *anchored* to fixed days of the
# month (so day-of-month repeats every month), while a true bi-weekly schedule
# drifts backwards ~2 days a month and rarely repeats one.
#
# Getting this wrong is not symmetric: misreading semi-monthly as bi-weekly
# fits a 14-day schedule to pay that has none, and then attribution scatters
# cheques into neighbouring months for no reason.
MIN_DAY_OF_MONTH_DRIFT = 0.6


class Cadence(StrEnum):
    BIWEEKLY = "biweekly"
    # Monthly, semi-monthly, irregular, or simply not enough history — all get
    # plain calendar-month attribution, so they don't need to be told apart.
    OTHER = "other"


def detect_cadence(dates: list[date_type]) -> Cadence:
    """Infer the pay cadence from the dates money came in on.

    Two signals must agree, because either alone gives a false positive on a
    common schedule:

    * the *median* gap is ~14 days — median rather than mean so one missed or
      extra deposit (a bonus, a reimbursement) can't drag the answer across
      the threshold; and
    * the day-of-month actually drifts — see ``MIN_DAY_OF_MONTH_DRIFT``, which
      is what rules out semi-monthly pay.

    Anything unrecognised returns ``OTHER`` and keeps plain calendar
    attribution, which is the safe default.
    """
    unique = sorted(set(dates))[-DETECTION_WINDOW:]
    if len(unique) < MIN_DEPOSITS_TO_DETECT:
        return Cadence.OTHER

    gaps = [(b - a).days for a, b in pairwise(unique)]
    if not gaps:
        return Cadence.OTHER

    median_gap = statistics.median(gaps)
    if not BIWEEKLY_MIN_GAP_DAYS <= median_gap <= BIWEEKLY_MAX_GAP_DAYS:
        return Cadence.OTHER

    distinct_days = len({d.day for d in unique})
    if distinct_days / len(unique) < MIN_DAY_OF_MONTH_DRIFT:
        return Cadence.OTHER

    return Cadence.BIWEEKLY


@dataclass(frozen=True)
class PaySchedule:
    """A pay schedule learned from one user's deposits.

    ``phase`` is the residue mod 14 that every nominal payday shares, which is
    what pins the schedule's infinite sequence of paydays onto the calendar.
    It is ``None`` for non-bi-weekly cadences, which keep calendar attribution.
    """

    cadence: Cadence
    phase: int | None = None

    @property
    def is_biweekly(self) -> bool:
        return self.cadence is Cadence.BIWEEKLY and self.phase is not None


def _offset_to_payday(d: date_type, phase: int) -> int:
    """Signed days from ``d`` to the nearest payday of the schedule ``phase``.

    Positive means the deposit landed *early* — its payday is still ahead of
    it, the weekend/holiday case that motivates this module. Negative means it
    posted late.
    """
    ahead = (phase - d.toordinal()) % BIWEEKLY_PERIOD_DAYS
    return ahead - BIWEEKLY_PERIOD_DAYS if ahead > MAX_SHIFT_DAYS else ahead


def fit_schedule(dates: list[date_type]) -> PaySchedule:
    """Learn the pay schedule from the dates money came in on.

    Picks the phase whose paydays sit closest to the deposits actually
    observed, scored by total absolute distance. Absolute rather than squared
    distance so a single outlier — a bonus, a reimbursement, a cheque held up a
    week — moves the fit by at most its own weight instead of dominating it.

    Only 14 phases exist, so this searches all of them; nothing cleverer is
    needed and an exhaustive search cannot settle into a local minimum.
    """
    cadence = detect_cadence(dates)
    if cadence is not Cadence.BIWEEKLY:
        return PaySchedule(cadence)

    recent = sorted(set(dates))[-DETECTION_WINDOW:]
    best_phase = min(
        range(BIWEEKLY_PERIOD_DAYS),
        key=lambda phase: sum(abs(_offset_to_payday(d, phase)) for d in recent),
    )
    return PaySchedule(cadence, best_phase)


def nominal_payday(d: date_type, schedule: PaySchedule) -> date_type:
    """The scheduled payday that a deposit landing on ``d`` was paid against."""
    if schedule.phase is None:
        return d
    return d + timedelta(days=_offset_to_payday(d, schedule.phase))


def attributed_month(d: date_type, schedule: PaySchedule) -> tuple[int, int]:
    """The ``(year, month)`` a deposit on ``d`` should be counted in."""
    payday = nominal_payday(d, schedule)
    return payday.year, payday.month
