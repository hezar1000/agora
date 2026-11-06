"""Look up a queue's name by its stored position.

A Django template cannot index a dict with a variable, and the settings page
has to pair each stored choice ("1".."4") with the name that course gave it.
"""

from django import template

register = template.Library()


@register.filter
def queue_name(labels, position):
    """The course's own name for a queue, falling back to its position.

    ``position`` arrives from a bound checkbox as ``choice.data.value``, which
    for a MultiSelectField is a string. It has also been seen as an int, so
    both are tried before giving up.
    """
    if not labels or position in (None, ""):
        return f"Queue {position}"
    try:
        numbered = labels.get(int(position))
    except (TypeError, ValueError):
        numbered = None
    return numbered or labels.get(str(position)) or f"Queue {position}"
