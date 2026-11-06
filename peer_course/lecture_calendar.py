"""The term as a calendar, so a schedule can be read rather than reconstructed.

A course's lectures were listed as a table, newest first, next to a separate
"weekly pattern" section written in the language of the database. Between them
an instructor could not answer the two questions they actually have: which days
am I teaching, and where are the gaps. Seventeen rows in reverse order do not
show that 30 September is a holiday sitting inside a Wednesday pattern.

The grid answers both, and names the one case that needs an action: a day the
pattern covers where no lecture exists yet.
"""

import calendar
from datetime import date, timedelta

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
#: Column headings in the grid, where space is tight.
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
#: Names for prose. Pluralising the abbreviations gives "Weddays".
FULL_DAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)

#: What a day is, in the order the legend should read.
LECTURE = "lecture"
NOT_GRADED = "not-graded"
NO_LECTURE = "no-lecture"
PLANNED = "planned"
BLANK = "blank"


def _pattern_days(templates):
    """``{"wed", "fri"}`` and the window each pattern runs for."""
    windows = []
    for template in templates:
        days = template.active_days or ""
        if not isinstance(days, str):
            days = ",".join(days)
        chosen = {part.strip() for part in days.split(",") if part.strip()}
        if not chosen:
            continue
        windows.append((chosen, template.session_date, template.session_end_date))
    return windows


def _pattern_covers(windows, day):
    key = DAY_KEYS[day.weekday()]
    for chosen, start, end in windows:
        if key not in chosen:
            continue
        if start and day < start:
            continue
        if end and day > end:
            continue
        return True
    return False


def _month_range(first, last):
    cursor = date(first.year, first.month, 1)
    while cursor <= last:
        yield cursor
        cursor = date(
            cursor.year + (cursor.month == 12),
            1 if cursor.month == 12 else cursor.month + 1,
            1,
        )


def build(course, today=None):
    """Month grids covering the term, plus a count of each kind of day.

    Returns ``{"months": [...], "counts": {...}, "unplanned": [...]}``.
    ``unplanned`` are days the weekly pattern covers where no lecture exists,
    which is the only state on the page that asks the instructor to do
    something.
    """
    today = today or date.today()
    lectures = list(
        course.sessions.filter(is_template=False)
        .order_by("session_date", "start_time_pst")
    )
    templates = list(course.sessions.filter(is_template=True))
    windows = _pattern_days(templates)

    by_date = {}
    for lecture in lectures:
        if lecture.session_date:
            by_date.setdefault(lecture.session_date, lecture)

    dated = [lecture.session_date for lecture in lectures if lecture.session_date]
    starts = [start for _, start, _ in windows if start]
    ends = [end for _, _, end in windows if end]
    candidates = dated + starts + ends
    if not candidates:
        return {"months": [], "counts": {}, "unplanned": []}
    first, last = min(candidates), max(candidates)

    counts = {LECTURE: 0, NOT_GRADED: 0, NO_LECTURE: 0, PLANNED: 0}
    unplanned = []
    months = []
    for month_start in _month_range(first, last):
        weeks = []
        month_days = calendar.Calendar(firstweekday=0).monthdatescalendar(
            month_start.year, month_start.month
        )
        interesting = False
        for week in month_days:
            row = []
            for day in week:
                lecture = by_date.get(day)
                outside = day.month != month_start.month
                if lecture is not None and not outside:
                    if lecture.cancelled:
                        kind = NO_LECTURE
                    elif not lecture.counts_for_participation:
                        kind = NOT_GRADED
                    else:
                        kind = LECTURE
                    counts[kind] += 1
                    interesting = True
                elif not outside and _pattern_covers(windows, day):
                    kind = PLANNED
                    counts[PLANNED] += 1
                    unplanned.append(day)
                    interesting = True
                else:
                    kind = BLANK
                row.append({
                    "date": day,
                    "outside": outside,
                    "kind": BLANK if outside else kind,
                    "lecture": None if outside else lecture,
                    "past": day < today,
                    "today": day == today,
                })
            weeks.append(row)
        if interesting:
            months.append({"start": month_start, "weeks": weeks})

    return {
        "months": months,
        "counts": counts,
        "unplanned": unplanned,
        "weekday_names": WEEKDAY_NAMES,
    }


def pattern_sentences(templates):
    """Each weekly pattern as a sentence rather than six labelled inputs."""
    sentences = []
    for template in templates:
        days = template.active_days or ""
        if not isinstance(days, str):
            days = ",".join(days)
        chosen = [part.strip() for part in days.split(",") if part.strip()]
        named = [
            FULL_DAY_NAMES[DAY_KEYS.index(key)] for key in chosen if key in DAY_KEYS
        ]
        if not named:
            when = "no days chosen yet"
        elif len(named) == 1:
            when = f"{named[0]}s"
        else:
            when = ", ".join(f"{name}s" for name in named[:-1])
            when += f" and {named[-1]}s"

        times = ""
        if template.start_time_pst and template.end_time_pst:
            times = (
                f", {template.start_time_pst:%H:%M}–{template.end_time_pst:%H:%M}"
            )
        window = ""
        if template.session_date and template.session_end_date:
            window = (
                f", from {template.session_date:%-d %b} to "
                f"{template.session_end_date:%-d %b %Y}"
            )
        elif template.session_date:
            window = f", from {template.session_date:%-d %b %Y}"
        sentences.append({
            "template": template,
            "text": f"{when.capitalize()}{times}{window}",
            "cancel_next": template.cancel_next_session,
        })
    return sentences
