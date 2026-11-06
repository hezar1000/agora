from django.contrib import admin
from .models import *

# Register your models here.


class CourseAdmin(admin.ModelAdmin):
    pass


admin.site.register(Course, CourseAdmin)


class CourseMemberAdmin(admin.ModelAdmin):
    pass

class CourseSessionAdmin(admin.ModelAdmin):
    pass

admin.site.register(CourseSession, CourseSessionAdmin)

admin.site.register(CourseMember, CourseMemberAdmin)

class CourseMemberParticipation(admin.ModelAdmin):
    pass

admin.site.register(CourseParticipation, CourseMemberParticipation)