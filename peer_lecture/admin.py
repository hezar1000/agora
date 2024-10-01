from django.contrib import admin
from .models import *

# Register your models here.
admin.site.register(Lecture)
admin.site.register(Poll)
admin.site.register(PollResult)
admin.site.register(Message)