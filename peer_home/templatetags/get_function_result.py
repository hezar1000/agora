from django import template

register = template.Library()


@register.simple_tag
def get_function_result(obj, func_name, param):
    return getattr(obj, func_name)(param)
