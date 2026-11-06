"""Inline SVG for the student participation page.

Drawn on the server as plain SVG rather than through a charting library. The
page must work with scripting off, the library on this codebase is a
known-vulnerable jQuery, and three small pictures do not justify a dependency.

Every chart carries a title and description, so a screen reader gets the same
information the picture conveys rather than "image".
"""

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

WIDTH = 520
HEIGHT = 170
PAD_LEFT = 44
PAD_BOTTOM = 28
PAD_TOP = 12
PAD_RIGHT = 12


def _scale(value, low, high, size, start):
    if high == low:
        return start + size / 2
    return start + (value - low) / (high - low) * size


@register.simple_tag
def participation_curve(points, mine, median, label="Your participation grade"):
    """The grade curve, with the student and the class median marked."""
    if not points:
        return ""
    xs = [x for x, _ in points]
    low, high = min(xs), max(xs)
    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM

    path = " ".join(
        f"{'M' if index == 0 else 'L'}"
        f"{_scale(x, low, high, plot_w, PAD_LEFT):.1f},"
        f"{HEIGHT - PAD_BOTTOM - y * plot_h:.1f}"
        for index, (x, y) in enumerate(points)
    )
    my_x = _scale(min(max(mine, low), high), low, high, plot_w, PAD_LEFT)
    median_x = _scale(min(max(median, low), high), low, high, plot_w, PAD_LEFT)
    my_y = HEIGHT - PAD_BOTTOM - _grade_at(points, mine) * plot_h

    description = (
        f"You have {mine:g} contributions; the class median is {median:g}."
    )
    return mark_safe(
        f'''<svg viewBox="0 0 {WIDTH} {HEIGHT}" class="participation-chart"
     role="img" aria-labelledby="curve-title curve-desc">
  <title id="curve-title">{escape(label)}</title>
  <desc id="curve-desc">{escape(description)}</desc>
  <line x1="{PAD_LEFT}" y1="{HEIGHT - PAD_BOTTOM}" x2="{WIDTH - PAD_RIGHT}"
        y2="{HEIGHT - PAD_BOTTOM}" stroke="currentColor" stroke-opacity="0.3"/>
  <line x1="{PAD_LEFT}" y1="{PAD_TOP}" x2="{PAD_LEFT}" y2="{HEIGHT - PAD_BOTTOM}"
        stroke="currentColor" stroke-opacity="0.3"/>
  <text x="4" y="{PAD_TOP + 8}" font-size="11" fill="currentColor">100%</text>
  <text x="10" y="{HEIGHT - PAD_BOTTOM}" font-size="11" fill="currentColor">0</text>
  <path d="{path}" fill="none" stroke="currentColor" stroke-width="2"/>
  <line x1="{median_x:.1f}" y1="{PAD_TOP}" x2="{median_x:.1f}"
        y2="{HEIGHT - PAD_BOTTOM}" stroke="currentColor" stroke-dasharray="4 3"
        stroke-opacity="0.55"/>
  <text x="{median_x:.1f}" y="{PAD_TOP - 2}" font-size="11" text-anchor="middle"
        fill="currentColor" fill-opacity="0.7">median</text>
  <circle cx="{my_x:.1f}" cy="{my_y:.1f}" r="5" fill="currentColor"/>
  <text x="{my_x:.1f}" y="{HEIGHT - PAD_BOTTOM + 16}" font-size="11"
        text-anchor="middle" fill="currentColor">you ({mine:g})</text>
</svg>'''
    )


def _grade_at(points, value):
    best = 0.0
    for x, y in points:
        if x <= value:
            best = y
    return best


@register.simple_tag
def participation_histogram(buckets, mine, label="How the class compares"):
    """The shape of the class, with the student's own bucket highlighted.

    No names, no ranks, no ordering of individuals -- only how many people sit
    where. Enough to read your own grade, nothing about anyone else's.
    """
    if not buckets:
        return ""
    tallest = max(count for _, _, count in buckets) or 1
    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM
    bar_w = plot_w / len(buckets)

    bars = []
    for index, (low, high, count) in enumerate(buckets):
        height = (count / tallest) * plot_h
        x = PAD_LEFT + index * bar_w
        y = HEIGHT - PAD_BOTTOM - height
        is_mine = low <= mine <= high
        opacity = "1" if is_mine else "0.35"
        bars.append(
            f'<rect x="{x + 1:.1f}" y="{y:.1f}" width="{max(bar_w - 2, 1):.1f}" '
            f'height="{height:.1f}" fill="currentColor" fill-opacity="{opacity}">'
            f"<title>{count} student{'' if count == 1 else 's'} with "
            f"{low:g}{'' if low == high else f'–{high:g}'} contributions</title>"
            f"</rect>"
        )
    mine_bucket = next(
        (count for low, high, count in buckets if low <= mine <= high), 0
    )
    description = (
        f"You are among {mine_bucket} student"
        f"{'' if mine_bucket == 1 else 's'} with a similar number of "
        "contributions."
    )
    return mark_safe(
        f'''<svg viewBox="0 0 {WIDTH} {HEIGHT}" class="participation-chart"
     role="img" aria-labelledby="hist-title hist-desc">
  <title id="hist-title">{escape(label)}</title>
  <desc id="hist-desc">{escape(description)}</desc>
  <line x1="{PAD_LEFT}" y1="{HEIGHT - PAD_BOTTOM}" x2="{WIDTH - PAD_RIGHT}"
        y2="{HEIGHT - PAD_BOTTOM}" stroke="currentColor" stroke-opacity="0.3"/>
  {''.join(bars)}
  <text x="{PAD_LEFT}" y="{HEIGHT - 6}" font-size="11" fill="currentColor">
    fewer contributions
  </text>
  <text x="{WIDTH - PAD_RIGHT}" y="{HEIGHT - 6}" font-size="11"
        text-anchor="end" fill="currentColor">more</text>
</svg>'''
    )


@register.simple_tag
def lecture_strip(lectures, dropped=0):
    """One cell per lecture, filled where the student took part."""
    if not lectures:
        return ""
    size, gap = 20, 4
    width = len(lectures) * (size + gap)
    missed = sum(1 for lecture in lectures if not lecture["took_part"])
    remaining = max(dropped - missed, 0)
    description = (
        f"You took part in {len(lectures) - missed} of {len(lectures)} "
        f"lectures. {remaining} dropped lecture"
        f"{'' if remaining == 1 else 's'} remaining."
    )

    cells = []
    for index, lecture in enumerate(lectures):
        x = index * (size + gap)
        took_part = lecture["took_part"]
        cells.append(
            f'<rect x="{x}" y="0" width="{size}" height="{size}" rx="3" '
            f'fill="currentColor" fill-opacity="{"0.85" if took_part else "0.12"}" '
            f'stroke="currentColor" stroke-opacity="0.35">'
            f"<title>Lecture {index + 1}: "
            f"{'took part' if took_part else 'did not take part'}</title>"
            f"</rect>"
        )
    return mark_safe(
        f'''<svg viewBox="0 0 {width} {size}" width="{min(width, 520)}"
     class="participation-strip" role="img"
     aria-labelledby="strip-title strip-desc">
  <title id="strip-title">Lectures you took part in</title>
  <desc id="strip-desc">{escape(description)}</desc>
  {''.join(cells)}
</svg>'''
    )


@register.simple_tag
def lecture_grade_bars(entries, label="Your grade in each lecture"):
    """One bar per lecture against the median, for a per-lecture scheme.

    Sixteen lectures would be sixteen curves and nobody reads sixteen charts.
    Each lecture reduces to the grade it awarded; the dashed line is what
    taking part normally earned, which under this rubric is steady enough to
    read a bar against.
    """
    if not entries:
        return ""
    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM
    bar_w = plot_w / len(entries)

    bars = []
    for index, entry in enumerate(entries):
        grade = entry["grade"]
        height = grade * plot_h
        x = PAD_LEFT + index * bar_w
        y = HEIGHT - PAD_BOTTOM - height
        # A dropped lecture is drawn faintly rather than hidden: the student
        # took part in it, it simply is not counted.
        opacity = "0.85" if entry["counted"] else "0.25"
        state = "counted" if entry["counted"] else "dropped, so it costs nothing"
        bars.append(
            f'<rect x="{x + 1:.1f}" y="{y:.1f}" width="{max(bar_w - 2, 1):.1f}" '
            f'height="{max(height, 1):.1f}" fill="currentColor" '
            f'fill-opacity="{opacity}">'
            f"<title>Lecture {index + 1}: {grade * 100:.0f}% "
            f"from {entry['contributions']:g} contributions ({state})</title>"
            f"</rect>"
        )

    medians = [entry["median_grade"] for entry in entries]
    typical = sum(medians) / len(medians)
    median_y = HEIGHT - PAD_BOTTOM - typical * plot_h
    dropped = sum(1 for entry in entries if not entry["counted"])
    counted = [entry["grade"] for entry in entries if entry["counted"]]
    average = (sum(counted) / len(counted) * 100) if counted else 0
    description = (
        f"Across {len(entries)} lectures your counted grades average "
        f"{average:.0f} percent, against a typical {typical * 100:.0f} percent "
        f"for taking part."
        + (f" The {dropped} lowest are dropped." if dropped else "")
    )
    return mark_safe(
        f'''<svg viewBox="0 0 {WIDTH} {HEIGHT}" class="participation-chart"
     role="img" aria-labelledby="bars-title bars-desc">
  <title id="bars-title">{escape(label)}</title>
  <desc id="bars-desc">{escape(description)}</desc>
  <line x1="{PAD_LEFT}" y1="{HEIGHT - PAD_BOTTOM}" x2="{WIDTH - PAD_RIGHT}"
        y2="{HEIGHT - PAD_BOTTOM}" stroke="currentColor" stroke-opacity="0.3"/>
  <text x="4" y="{PAD_TOP + 8}" font-size="11" fill="currentColor">100%</text>
  {''.join(bars)}
  <line x1="{PAD_LEFT}" y1="{median_y:.1f}" x2="{WIDTH - PAD_RIGHT}"
        y2="{median_y:.1f}" stroke="currentColor" stroke-dasharray="4 3"
        stroke-opacity="0.7"/>
  <text x="{WIDTH - PAD_RIGHT}" y="{median_y - 4:.1f}" font-size="11"
        text-anchor="end" fill="currentColor" fill-opacity="0.75">
    typical ({typical * 100:.0f}%)
  </text>
  <text x="{PAD_LEFT}" y="{HEIGHT - 6}" font-size="11" fill="currentColor">
    first lecture
  </text>
  <text x="{WIDTH - PAD_RIGHT}" y="{HEIGHT - 6}" font-size="11"
        text-anchor="end" fill="currentColor">most recent</text>
</svg>'''
    )


# --- the student's record, drawn -------------------------------------------
#
# One hue, thin bars anchored to the baseline, the count written on each bar
# and nothing else on the chart. The earlier charts on this page drew grades
# and class curves in `currentColor`, which on a dark theme became a black
# slab across the screen, and which in the second week of term showed a
# provisional grade nobody should read. These draw counts, which are real.

INK = "#2f6fb0"          # the one series colour
INK_SOFT = "#c9d8ea"     # the unfilled part of a meter
GUIDE = "#8a8a8a"        # median tick and axis, recessive
TEXT = "#333333"
TEXT_MUTED = "#6b6b6b"


CHART_H = 120
CHART_PAD_TOP = 14
CHART_PAD_BOTTOM = 30
CHART_PAD_LEFT = 8
CHART_PAD_RIGHT = 8
SLOT = 30
BAR = 18


@register.simple_tag
def lecture_chart(rows, mode="plain"):
    """One bar per lecture, drawn at content size, with the rubric's overlays.

    ``mode`` is what the grading scheme reads from each lecture:

    ``per_lecture``  your count against that lecture's median (tick) and
                     maximum (top of the faint column), the settled grade
                     under the bar; dropped lectures faded once drops mean
                     something.
    ``split``        the first counted raise drawn as attendance, the rest
                     as participation (AI 100).
    ``plain``        the count alone.

    Bars carry ``data-`` attributes; a few lines of script on the page turn
    them into a hover tooltip and a click that scrolls to the lecture's row.
    """
    if not rows:
        return ""
    plot_h = CHART_H - CHART_PAD_TOP - CHART_PAD_BOTTOM
    width = CHART_PAD_LEFT + CHART_PAD_RIGHT + SLOT * len(rows)
    baseline = CHART_H - CHART_PAD_BOTTOM
    def height(row):
        # Split, the bar is attendance plus what came after it. Normally the
        # first raise was the attendance, so this is the raises; where the roll
        # call paid for it, it is one more than the raises, and a student who
        # answered the roll call and never raised a hand still has a bar.
        if mode == "split":
            part = row.get("attendance_part", 0)
            return part + row.get("additional", row["raised"] + row["bonus"] - part)
        return row["raised"] + row["bonus"]

    top = max(
        [height(row) for row in rows]
        + ([row.get("maximum", 0) for row in rows] if mode == "per_lecture" else [])
        + [1]
    )
    scale = plot_h / top

    def bar_path(x, y, h, w, radius=3.0):
        if h <= 0:
            return ""
        radius = min(radius, w / 2, h)
        return (
            f"M{x:.1f},{y + h:.1f} V{y + radius:.1f} Q{x:.1f},{y:.1f} {x + radius:.1f},{y:.1f} "
            f"H{x + w - radius:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + radius:.1f} "
            f"V{y + h:.1f} Z"
        )

    marks = []
    for index, row in enumerate(rows):
        raises = row["raised"] + row["bonus"]
        value = height(row)
        x = CHART_PAD_LEFT + index * SLOT + (SLOT - BAR) / 2
        session = row.get("session")
        name = session.display_name if session is not None else f"Lecture {row['number']}"
        when = session.session_date.strftime("%-d/%-m") if session is not None and session.session_date else ""
        faded = row.get("faded")
        opacity = ' fill-opacity="0.35"' if faded else ""
        tip = f"{name}: {raises:g} hand raise{'' if raises == 1 else 's'} counted"
        if mode == "per_lecture":
            if row.get("percent") is not None:
                tip += f" · graded {row['percent']}%"
            tip += f" · class median {row.get('median', 0):g}, top {row.get('maximum', 0):g}"
            if faded:
                tip += " · among your dropped lectures"
        elif mode == "split" and value:
            via = " (roll call)" if row.get("roll_call") else ""
            tip += f" · attendance{via} + {value - row.get('attendance_part', 0):g} additional"
        attrs = (
            f'data-tip="{escape(tip)}" data-row="lecture-row-{row["session_id"]}" '
            f'class="lecture-bar"'
        )
        if mode == "per_lecture" and row.get("maximum"):
            # The lecture's range, faint, so the bar reads against its class.
            col_h = row["maximum"] * scale
            marks.append(
                f'<rect x="{x - 2:.1f}" y="{baseline - col_h:.1f}" width="{BAR + 4}" '
                f'height="{col_h:.1f}" rx="3" fill="{INK_SOFT}" fill-opacity="0.55"/>'
            )
        if value > 0:
            if mode == "split" and row.get("attendance_part"):
                att_h = row["attendance_part"] * scale
                rest_h = (value - row["attendance_part"]) * scale
                marks.append(
                    f'<rect x="{x:.1f}" y="{baseline - att_h:.1f}" width="{BAR}" '
                    f'height="{max(att_h - (1 if rest_h else 0), 0):.1f}" fill="{INK_SOFT}"{opacity} {attrs}>'
                    f"<title>{escape(tip)}</title></rect>"
                )
                if rest_h > 0:
                    marks.append(
                        f'<path d="{bar_path(x, baseline - att_h - rest_h, rest_h - 1, BAR)}" '
                        f'fill="{INK}"{opacity} {attrs}><title>{escape(tip)}</title></path>'
                    )
            else:
                marks.append(
                    f'<path d="{bar_path(x, baseline - value * scale, value * scale, BAR)}" '
                    f'fill="{INK}"{opacity} {attrs}><title>{escape(tip)}</title></path>'
                )
            marks.append(
                f'<text x="{x + BAR / 2:.1f}" y="{baseline - value * scale - 4:.1f}" '
                f'font-size="10" text-anchor="middle" fill="{TEXT}"{opacity}>{value:g}</text>'
            )
        else:
            marks.append(
                f'<rect x="{x:.1f}" y="{baseline - 2:.1f}" width="{BAR}" height="2" '
                f'fill="{GUIDE}" fill-opacity="0.5" {attrs}><title>{escape(tip)}</title></rect>'
            )
        if mode == "per_lecture" and row.get("median"):
            median_y = baseline - row["median"] * scale
            marks.append(
                f'<line x1="{x - 4:.1f}" x2="{x + BAR + 4:.1f}" y1="{median_y:.1f}" '
                f'y2="{median_y:.1f}" stroke="{GUIDE}" stroke-width="1.5"/>'
            )
        marks.append(
            f'<text x="{x + BAR / 2:.1f}" y="{baseline + 11:.1f}" font-size="9" '
            f'text-anchor="middle" fill="{TEXT_MUTED}">{escape(when)}</text>'
        )
        if mode == "per_lecture" and row.get("percent") is not None:
            marks.append(
                f'<text x="{x + BAR / 2:.1f}" y="{baseline + 23:.1f}" font-size="10" '
                f'text-anchor="middle" fill="{TEXT}"{opacity}>{row["percent"]}%</text>'
            )

    total = sum(row["raised"] + row["bonus"] for row in rows)
    description = (
        f"{total:g} hand raises counted across {len(rows)} lecture"
        f"{'' if len(rows) == 1 else 's'}."
    )
    return mark_safe(
        f'''<svg viewBox="0 0 {width:.0f} {CHART_H}" width="{width:.0f}" height="{CHART_H}"
     class="participation-chart raise-bars lecture-chart"
     role="img" aria-labelledby="raises-title raises-desc">
  <title id="raises-title">Hand raises counted, lecture by lecture</title>
  <desc id="raises-desc">{escape(description)}</desc>
  <line x1="{CHART_PAD_LEFT}" y1="{baseline}" x2="{width - CHART_PAD_RIGHT:.0f}" y2="{baseline}"
        stroke="{GUIDE}" stroke-opacity="0.5"/>
  {''.join(marks)}
</svg>'''
    )


@register.simple_tag
def progress_meter(value, target, label="Progress"):
    """A thin meter: ``value`` of ``target``, with both numbers written."""
    if not target or target <= 0:
        return ""
    width, height, radius = 260, 10, 5
    filled = max(0.0, min(1.0, float(value) / float(target))) * width
    description = f"{value:g} of {target:g}."
    return mark_safe(
        f'''<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}"
     class="participation-meter" role="img" aria-labelledby="meter-title meter-desc">
  <title id="meter-title">{escape(label)}</title>
  <desc id="meter-desc">{escape(description)}</desc>
  <rect x="0" y="0" width="{width}" height="{height}" rx="{radius}" fill="{INK_SOFT}"/>
  <rect x="0" y="0" width="{filled:.1f}" height="{height}" rx="{radius}" fill="{INK}"/>
</svg>'''
    )


@register.simple_tag
def class_distribution(buckets, mine, median=None, maximum=None, label="Where you stand in the class"):
    """How many students sit at each count, with the student's own bucket dark.

    No names and no ranks: enough to see whether you are in the crowded middle
    or a thin tail, nothing about anyone else. The dashed line is the class
    median where the rubric uses one.
    """
    if not buckets:
        return ""
    slot = 30.0
    width = CHART_PAD_LEFT + CHART_PAD_RIGHT + slot * len(buckets)
    plot_w = width - CHART_PAD_LEFT - CHART_PAD_RIGHT
    PAD_LEFT_ = CHART_PAD_LEFT
    plot_h = CHART_H - CHART_PAD_TOP - CHART_PAD_BOTTOM
    baseline = CHART_H - CHART_PAD_BOTTOM
    tallest = max(count for _, _, count in buckets) or 1
    bar_w = 18.0
    low, high = buckets[0][0], buckets[-1][1]

    def x_of(value):
        if high == low:
            return PAD_LEFT_ + plot_w / 2
        return PAD_LEFT_ + (value - low) / (high - low) * (plot_w - slot) + slot / 2

    marks = []
    mine_count = 0
    for index, (lo, hi, count) in enumerate(buckets):
        is_mine = lo <= mine <= hi
        if is_mine:
            mine_count = count
        x = PAD_LEFT_ + index * slot + (slot - bar_w) / 2
        height = (count / tallest) * plot_h
        y = baseline - height
        title = (
            f"{count} student{'' if count == 1 else 's'} with "
            f"{lo:g}{'' if lo == hi else f'–{hi:g}'}"
            + (" (you)" if is_mine else "")
        )
        if count:
            radius = min(4.0, bar_w / 2, height)
            marks.append(
                f'<path d="M{x:.1f},{baseline:.1f} V{y + radius:.1f} '
                f'Q{x:.1f},{y:.1f} {x + radius:.1f},{y:.1f} '
                f'H{x + bar_w - radius:.1f} Q{x + bar_w:.1f},{y:.1f} '
                f'{x + bar_w:.1f},{y + radius:.1f} V{baseline:.1f} Z" '
                f'fill="{INK if is_mine else INK_SOFT}" class="lecture-bar" '
                f'data-tip="{escape(title)}"><title>{escape(title)}</title></path>'
            )
        if is_mine:
            # Inside the bar, in white, when it reaches the top of the plot.
            inside = count and y - 5 < CHART_PAD_TOP + 10
            marks.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{(y + 14) if inside else (y - 5):.1f}" '
                f'font-size="11" text-anchor="middle" '
                f'fill="{"#ffffff" if inside else TEXT}">you</text>'
            )
        tick = f"{lo:g}" if lo == hi else f"{lo:g}–{hi:g}"
        marks.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{baseline + 11:.1f}" font-size="9" '
            f'text-anchor="middle" fill="{TEXT_MUTED}">{escape(tick)}</text>'
        )
    for value, name in ((median, "median"), (maximum, "top")):
        if value is None:
            continue
        mx = x_of(float(value))
        marks.append(
            f'<line x1="{mx:.1f}" x2="{mx:.1f}" y1="{CHART_PAD_TOP}" y2="{baseline}" '
            f'stroke="{GUIDE}" stroke-width="1.5" stroke-dasharray="3 2"/>'
            f'<text x="{mx:.1f}" y="{baseline + 22}" font-size="9" text-anchor="middle" '
            f'fill="{TEXT_MUTED}">{name}</text>'
        )
    students = sum(count for _, _, count in buckets)
    description = (
        f"{students} students. You are among {mine_count} with a similar count"
        + (f"; the class median is {float(median):g}." if median is not None else ".")
    )
    return mark_safe(
        f'''<svg viewBox="0 0 {width:.0f} {CHART_H}" width="{width:.0f}" height="{CHART_H}"
     class="participation-chart class-distribution"
     role="img" aria-labelledby="dist-title dist-desc">
  <title id="dist-title">{escape(label)}</title>
  <desc id="dist-desc">{escape(description)}</desc>
  <line x1="{PAD_LEFT_}" y1="{baseline}" x2="{width - CHART_PAD_RIGHT:.0f}" y2="{baseline}"
        stroke="{GUIDE}" stroke-opacity="0.5"/>
  {''.join(marks)}
</svg>'''
    )
