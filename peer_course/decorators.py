from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.contrib import messages
from django.urls import reverse
from django.http import HttpResponseRedirect

# from .models import Assignment

# TODO (Farzad): I don't like this ...
def chosen_course_required(function):
    def wrap(request, *args, **kwargs):
        # entry = Entry._default_manager.get(pk=kwargs['entry_id'])
        if "course_id" in request.session:
            return function(request, *args, **kwargs)
        else:
            # messages.warning(request, 'Please select a course to continue')
            return HttpResponseRedirect(
                "%s?next=%s" % (reverse("course:list"), request.path)
            )
            # raise PermissionDenied

    wrap.__doc__ = function.__doc__
    wrap.__name__ = function.__name__
    return wrap
