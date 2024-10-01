from django import template

register = template.Library()


@register.inclusion_tag("peer_home/tags/pagination.html")
def pagination(page, page_range, total_count, link, extra_link_args=""):
    return {
        "page": page,
        "page_range": page_range,
        "total_count": total_count,
        "link": link,
        "extra_link_args": extra_link_args,
    }
