"""Attribute income deposits to the month they actually belong to.

Bi-weekly pay doesn't line up with calendar months: 26 cheques a year against
12 months. So a cheque landing on the 1st or 2nd of a month is usually the
*previous* month's final cheque — the pay period it covers ended before the
month did. Counting it in the calendar month it happens to land in inflates
that month's detected income, and since every 50/30/20 target is a percentage
of detected income, it inflates all three targets and makes "left to spend"
wrong in both months.

Concretely, for a July with deposits on the 1st, 15th and 30th: the 1st is
June's second cheque, and July's income is the 15th + 30th.

Only applied when the deposits actually look bi-weekly. Someone paid monthly,
or semi-monthly on the 1st and 15th, must keep plain calendar attribution —
shifting *their* 1st-of-month cheque backwards would be flatly wrong, and
that's a far more common pay schedule than bi-weekly.
"""

from __future__ import annotations

import statistics
from datetime import date as date_type
from enum import StrEnum
from itertools import pairwise

# Bi-weekly is every 14 days; allow a couple of days of slack for weekends and
# bank holidays shifting a deposit earlier or later.
BIWEEKLY_MIN_GAP_DAYS = 12
BIWEEKLY_MAX_GAP_DAYS = 16

# A deposit on or before this day-of-month is treated as the previous month's
# final cheque. Covers the 1st through the 4th: a cheque due on the 1st can slip
# a few days when it falls on a weekend, and 4 is still comfortably clear of a
# genuine mid-month payday.
GRACE_DAYS = 4

# Below this many deposits there isn't enough history to call the cadence, and
# guessing wrong is worse than leaving the calendar attribution alone.
MIN_DEPOSITS_TO_DETECT = 4

# Only the most recent N deposits are considered, so the drift ratio below
# means the same thing whether the caller passes six months of history or six
# years (there are only 31 possible days-of-month to spread across).
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
# would shift a genuine 1st-of-month cheque into the previous month, leaving
# one month with three cheques and the next with one.
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


def _previous_month(d: date_type) -> tuple[int, int]:
    if d.month == 1:
        return d.year - 1, 12
    return d.year, d.month - 1


def attributed_month(d: date_type, cadence: Cadence) -> tuple[int, int]:
    """The ``(year, month)`` a deposit on ``d`` should be counted in."""
    if cadence is Cadence.BIWEEKLY and d.day <= GRACE_DAYS:
        return _previous_month(d)
    return d.year, d.month
