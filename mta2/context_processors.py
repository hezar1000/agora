from django.conf import settings

def global_settings(request):
    """
    Expose immutable deployment-level product capabilities to templates.
    """
    return {
        "AGORA_MODE": settings.AGORA_MODE,
        "SIGNUP_ENABLED": settings.SIGNUP_ENABLED,
        "GUEST_SESSIONS_ENABLED": settings.GUEST_SESSIONS_ENABLED,
        "OPEN_COURSE_CREATION": settings.OPEN_COURSE_CREATION,
    }
