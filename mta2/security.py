"""Small response-security middleware kept local to avoid another dependency."""

from django.conf import settings


class ContentSecurityPolicyMiddleware:
    """Apply Agora's compatibility CSP to every Django response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault(
            "Content-Security-Policy", settings.CONTENT_SECURITY_POLICY
        )
        return response
