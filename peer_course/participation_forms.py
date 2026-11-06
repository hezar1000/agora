"""Editing a course's participation scheme.

Two questions decide every scheme: what earns credit inside one lecture, and
how lectures add up over a term. The form asks those, offers the schemes
already in use as starting points, and reveals the numbers underneath for
anyone who wants them.

Curves are written rather than drawn: ``0 -> 0%, 30 -> 60%`` is what an
instructor would say out loud, and it round-trips to the stored anchors.
"""

from django import forms
from django.forms import ModelForm, modelformset_factory

from . import participation_curves as curves
from . import participation_presets as presets
from .models import MeasureRubric, ParticipationScheme

KEEP = "__keep__"


class ParticipationSchemeForm(ModelForm):
    """The scalars, plus the preset that seeds everything else."""

    preset = forms.ChoiceField(
        required=False,
        label="Start from",
        help_text=(
            "Applying a starting point replaces the curves below. Leave it "
            "alone to keep what is there."
        ),
    )

    class Meta:
        model = ParticipationScheme
        fields = [
            "raise_value",
            "speak_bonus",
            "lecture_cap",
            "lectures_expected",
            "dropped_lectures",
            "bonus_after_speaking",
            "volume_excludes_covered",
        ]
        labels = {
            "raise_value": "A hand raise is worth",
            "speak_bonus": "Being chosen to speak adds",
            "lecture_cap": "Most contributions one lecture can count",
            "lectures_expected": "Lectures in the term",
            "dropped_lectures": "Lowest lectures ignored",
            "bonus_after_speaking": "After a student has spoken",
            "volume_excludes_covered": "Attendance and contributions overlap",
        }
        help_texts = {
            "raise_value": (
                "How much one hand raise counts for. One is the usual answer; "
                "the scale only matters relative to the thresholds below."
            ),
            "lectures_expected": (
                "How many lectures the term is graded against, whether or not "
                "every one of them runs. Empty counts the lectures actually "
                "held, which suits a course that does not know its shape yet."
            ),
            "dropped_lectures": (
                "How many of a student's weakest lectures are set aside, so "
                "that missing a few costs nothing."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        chosen = getattr(self.instance, "preset", "") or ""
        self.fields["preset"].choices = [
            (KEEP, self._keep_label(chosen)),
            *(
                (name, blueprint["label"])
                for name, blueprint in sorted(
                    presets.PRESETS.items(), key=lambda item: item[1]["label"]
                )
            ),
        ]
        self.fields["preset"].initial = KEEP
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                # Bootstrap 3 stretches .form-control to the full width of its
                # column, which turns a checkbox into a full-width grey slab.
                continue
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " form-control").strip()

    def _keep_label(self, chosen):
        """Name the current settings without pretending they are untouched.

        A preset is a starting point, not a mode: an instructor who picks
        Attendance and then moves a threshold is still doing Attendance, edited.
        Saying so keeps the label meaningful and keeps the reset discoverable.
        """
        if not chosen:
            return "Keep the current settings"
        label = presets.PRESETS.get(chosen, {}).get("label", chosen)
        if self.instance.pk and self._differs_from_preset(chosen):
            return f"Keep the current settings ({label}, edited)"
        return f"Keep the current settings ({label})"

    def _differs_from_preset(self, chosen):
        blueprint = presets.PRESETS.get(chosen)
        if not blueprint:
            return False
        for field, expected in blueprint["scheme"].items():
            if getattr(self.instance, field, None) != expected:
                return True
        stored = {
            rubric.measure: rubric for rubric in self.instance.rubrics.all()
        }
        expected_rubrics = {r["measure"]: r for r in blueprint["rubrics"]}
        if set(stored) != set(expected_rubrics):
            return True
        for measure, expected in expected_rubrics.items():
            rubric = stored[measure]
            for field, value in expected.items():
                if field == "measure":
                    continue
                if getattr(rubric, field) != value:
                    return True
        return False

    def clean_dropped_lectures(self):
        dropped = self.cleaned_data.get("dropped_lectures") or 0
        expected = self.data.get(f"{self.prefix}-lectures_expected") or self.data.get(
            "lectures_expected"
        )
        try:
            expected = int(expected)
        except (TypeError, ValueError):
            return dropped
        if expected and dropped >= expected:
            raise forms.ValidationError(
                "Dropping every lecture would leave nothing to grade."
            )
        return dropped


class MeasureRubricForm(ModelForm):
    """One measure's weight and the curve that awards it."""

    absolute_anchors_text = forms.CharField(
        required=False,
        label="Fixed scale",
        help_text=(
            "What a student earns for a given amount, whatever the class does. "
            "Percentages are of this measure's marks, so \u201c0 -> 0%, "
            "28 -> 70%\u201d means nothing for none and 70% of the marks at 28. "
            "Leave empty if only the class-relative curve should apply."
        ),
    )
    relative_anchors_text = forms.CharField(
        required=False,
        label="Curve for the top of the class",
        help_text=(
            "Positions given as class statistics rather than counts, so "
            "\u201cmedian -> 70%, max -> 100%\u201d puts the middle of the "
            "class at 70% of this measure's marks and the highest at 100%. "
            "Below the lowest position here, this curve pays nothing and the "
            "fixed scale decides. Leave empty for no curve."
        ),
    )

    class Meta:
        model = MeasureRubric
        fields = [
            "weight",
            "absolute_interpolation",
            "relative_interpolation",
            "combination",
        ]
        labels = {
            "weight": "Marks this measure is worth",
            "absolute_interpolation": "Between fixed positions, spread marks",
            "relative_interpolation": "Between class positions, spread marks",
            "combination": "When both apply, students get",
        }
        help_texts = {
            "weight": (
                "Relative to the other measures below, not a percentage of "
                "anything. The share each one takes of the participation grade "
                "is worked out from these and shown beside them."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["absolute_anchors_text"].initial = curves.format_anchors(
            self.instance.absolute_anchors
        )
        self.fields["relative_anchors_text"].initial = curves.format_anchors(
            self.instance.relative_anchors
        )
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " form-control").strip()

    def _parse(self, field):
        try:
            return curves.parse_anchors(self.cleaned_data.get(field, ""))
        except curves.CurveError as error:
            # The message names the piece that is wrong, so it belongs on the
            # field the instructor is looking at.
            self.add_error(field, str(error))
            return None

    def clean(self):
        cleaned = super().clean()
        absolute = self._parse("absolute_anchors_text")
        relative = self._parse("relative_anchors_text")
        combination = cleaned.get("combination")

        if absolute is not None:
            self.instance.absolute_anchors = absolute
        if relative is not None:
            self.instance.relative_anchors = relative

        # A measure that carries weight but has no curve silently scores zero
        # for everybody, which looks like a grading bug rather than a setting.
        if (cleaned.get("weight") or 0) > 0 and not (absolute or relative):
            self.add_error(
                "absolute_anchors_text",
                "This measure counts toward the grade but has no curve, so it "
                "would score nothing for everyone.",
            )
        if combination == "absolute" and not absolute:
            self.add_error(
                "absolute_anchors_text",
                "Students get the absolute curve, so it cannot be empty.",
            )
        if combination == "relative" and not relative:
            self.add_error(
                "relative_anchors_text",
                "Students get the class-relative curve, so it cannot be empty.",
            )
        return cleaned


MeasureRubricFormSet = modelformset_factory(
    MeasureRubric, form=MeasureRubricForm, extra=0, can_delete=False
)
