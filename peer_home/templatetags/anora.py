# Source: Anora from github
import re

from django import template

register = template.Library()

CONSONANT_SOUND = re.compile(r"""one(![ir])""", re.IGNORECASE | re.VERBOSE)
VOWEL_SOUND = re.compile(
    r"""[aeio]|u([aeiou]|[^n][^aeiou]|ni[^dmnl]|nil[^l])|h(ier|onest|onou?r|ors\b|our(!i))|[fhlmnrsx]\b""",
    re.IGNORECASE | re.VERBOSE,
)


@register.filter
def anora(text):
    anora = "an" if not CONSONANT_SOUND.match(text) and VOWEL_SOUND.match(text) else "a"
    return anora + " " + text
