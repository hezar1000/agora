import arrow
from django import template

register = template.Library()


@register.filter
def relative_date(date):
    return arrow.get(date).humanize().capitalize()


@register.inclusion_tag("peer_home/tags/relative-date.html")
def relative_date_tooltip(date):
    return {"relative": arrow.get(date).humanize().capitalize(), "actual": date}
