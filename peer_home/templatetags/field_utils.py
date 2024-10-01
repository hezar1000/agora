from django import template

register = template.Library()


@register.inclusion_tag("peer_home/tags/non-field-errors.html")
def non_field_errors(form):
    """
    Django template tag that takes care of boilerplate for displaying form's non-field errors
    
    :param form: a django form to display
    :type form: django.forms.Form
    """
    return {"errors": form.non_field_errors}


@register.inclusion_tag("peer_home/tags/form-default-fields.html")
def form_default_fields(form):
    """
    Django template tag that takes care of boilerplate for displaying forms

    Includes:
      - CSRF token
      - `non_field_errors`
      - `form.hidden_fields`
      - `form.visible_fields`
    
    :param form: a django form to display
    :type form: django.forms.Form
    """
    return {"form": form}
