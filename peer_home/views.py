from django.http import HttpResponseRedirect, HttpResponse
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db.models import Sum
from django.http import JsonResponse
import pytz
from django.utils import timezone
from django.db import transaction
import numpy as np
from django.shortcuts import redirect, get_object_or_404
from django.utils.http import url_has_allowed_host_and_scheme
from django.db.models import Count, Q

from peer_home.wrappers import render
from peer_course.models import Course, CourseMember, CourseParticipation
from peer_course.models import AttendanceCheck, AttendanceRecord, HandRaise
from peer_course import attendance
from peer_course import hand_events, lecture_guests
from peer_course import participation_rules
from peer_course.decorators import chosen_course_required
from django.views.decorators.http import require_POST
from peer_course.base import CourseBase, CoursePermissions
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from peer_course.models import CourseSession, LEARNER_ROLES
from peer_course.sessions import (
    active_session_for_course,
    course_timezone,
    latest_unended_session,
    session_bounds,
)
from peer_lecture.models import Lecture, Poll
from peer_lecture.microphone import select_microphone_student

from datetime import datetime, timedelta


class HomeViews:
    @staticmethod
    @login_required
    def sound_only(request, course_id):
        course = get_object_or_404(Course, pk=course_id, archived=False, classroom_microphone=True)
        CoursePermissions.require_instructor(request.user, course.pk)
        return render(request, "classroom-sound.html", {
            "course": course, "is_instructor": True, "is_student": False,
            "sound_only": True, "user_role": "instructor",
        })

    @staticmethod
    def render(request):
        "Render the homepage"
        render_dict = {"logged_in": request.user.is_authenticated}
        if request.user.is_authenticated:
            return HomeViews.agora_dash(request)
        return HttpResponseRedirect(reverse(settings.LOGIN_URL))
    
    @staticmethod
    def is_session_active(course, current_date, current_time, current_day):
        """Read active state without creating, ending, or updating any row."""
        tz = course_timezone(course)
        local_now = tz.localize(datetime.combine(current_date, current_time))
        manual_active = bool(course.manual_override and Lecture.currentLecture(course.id))
        return active_session_for_course(
            course,
            at=local_now,
            manual_active=manual_active,
        ) is not None

    @staticmethod
    def maintain_scheduled_sessions(course, current_date, current_time, current_day):
        """Apply scheduled-template transitions from the single maintenance owner."""
        # ``ended`` means a lecture has happened.  Older cleanup selected the
        # highest-id unended row, so a schedule entered for the whole term
        # could mark December ended while September's lecture was closing.
        # The state is impossible and safe to repair before resolving today.
        course.sessions.filter(
            is_template=False,
            ended=True,
            session_date__gt=current_date,
        ).update(ended=False, actual_end_time=None)

        if not course.auto_start:
            return HomeViews.is_session_active(
                course,
                current_date,
                current_time,
                current_day,
            )

        template_sessions = course.sessions.filter(is_template=True)
        
        # First check if any template session allows this day
        day_allowed = False
        for tmpl in template_sessions:
            if tmpl.active_days and current_day in tmpl.active_days:
                day_allowed = True
                break
        
        for tmpl in template_sessions if day_allowed else []:
            if tmpl.cancel_next_session:
                if tmpl.end_time_pst and current_time < tmpl.end_time_pst:
                    continue
                else:
                    tmpl.cancel_next_session = False
                    tmpl.save()
            if tmpl.session_date and current_date < tmpl.session_date:
                continue
            if tmpl.session_end_date and current_date > tmpl.session_end_date:
                continue

            if tmpl.active_days and current_day not in tmpl.active_days:
                continue

            if tmpl.start_time_pst and tmpl.end_time_pst:
                should_spawn = False
                if tmpl.start_time_pst <= tmpl.end_time_pst:
                    if tmpl.start_time_pst <= current_time <= tmpl.end_time_pst:
                        should_spawn = True
                else:
                    if current_time >= tmpl.start_time_pst or current_time <= tmpl.end_time_pst:
                        should_spawn = True

                if should_spawn:
                    st = tmpl.start_time_pst.replace(microsecond=0)
                    et = tmpl.end_time_pst.replace(microsecond=0)
                    existing = course.sessions.filter(
                        is_template=False,
                        session_date=current_date,
                        start_time_pst=st
                    )
                    if not existing.exists():
                        HomeViews._spawn_if_missing(course, tmpl, current_date)
        
        return HomeViews.is_session_active(
            course,
            current_date,
            current_time,
            current_day,
        )


    @staticmethod
    def manage_lecture(request, course, session_active, current_date):
        if (
            request is None
            or not hasattr(request, "user")
            or not request.user.is_authenticated
        ):
            auth_id = "system"
        else:
            auth_id = request.user.pk
        with transaction.atomic():
            course = Course.objects.select_for_update().get(pk=course.pk)
            current_lecture = Lecture.currentLecture(course.id)
            if course.manual_override:
                if current_lecture:
                    open_session = active_session_for_course(
                        course,
                        for_update=True,
                        manual_active=True,
                    )
                    tz = course_timezone(course)
                    now_local = timezone.localtime(timezone.now(), tz)
                    # An early start runs until the scheduled class ends,
                    # not until a duration measured from the audio test.
                    # Standalone/legacy manual lectures keep their original
                    # clock, independently of an edited grading date.
                    scheduled_end = current_lecture.scheduled_end_time or (
                        current_lecture.start_time + (
                            course.default_session_duration or timedelta(hours=1)
                        )
                    )
                    if now_local >= scheduled_end:
                        Lecture.endLecture(current_lecture.pk, now_local)
                        hand_events.clear_student_hands(course)
                        if open_session and not open_session.ended:
                            open_session.ended = True
                            open_session.actual_end_time = now_local.time().replace(tzinfo=None)
                            open_session.save(update_fields=["ended", "actual_end_time"])
                        course.manual_override = False
                        course.manually_started = False
                        course.save(update_fields=["manual_override", "manually_started"])
                        return None
                return current_lecture

            if session_active and not current_lecture:
                # A lecture always starts with an empty student queue.  This
                # also repairs a hand left open by an older release.
                hand_events.clear_student_hands(course)
                lecture = Lecture.create(course.id)
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f"course_{course.id}",
                    {
                        "type": "send_message",
                        "key": "begin-lecture",            
                        "send_auth_id": auth_id,
                    }
                )
                return lecture

            elif not session_active and current_lecture:
                open_session = latest_unended_session(course, for_update=True)
                if open_session:
                    tz = course_timezone(course)
                    bounds = session_bounds(open_session, tz)
                    scheduled_end = bounds[1] if bounds else timezone.now()
                    open_session.ended = True
                    open_session.actual_end_time = timezone.localtime(
                        scheduled_end,
                        tz,
                    ).time().replace(tzinfo=None)
                    open_session.save(update_fields=["ended", "actual_end_time"])
                else:
                    scheduled_end = timezone.now()
                Lecture.endLecture(current_lecture.pk, scheduled_end)
                hand_events.clear_student_hands(course)
                
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f"course_{course.id}",
                    {
                        "type": "send_message",
                        "key": "end-lecture",
                        "send_auth_id": "system"
                    }
                )
                return None

            else:
                return current_lecture
            
    @staticmethod
    def current_total(participant, session):
        """
        Return the number of *cap‑relevant* points the participant already has
        in this session:   hand‑raise points  +  bonus points
        (speaking points are ignored).
        """
        qs = CourseParticipation.objects.filter(
            participant=participant,
            session=session,
            count_in_calculations=True
        )
        bonus = qs.aggregate(b=Sum("bonus_points"))["b"] or 0
        total_pp = qs.aggregate(t=Sum("participation_points_gained"))["t"] or 0
        num_spoken     = qs.filter(spoke_upon_participation=True).count()
        speaking_pts   = num_spoken * participation_rules.for_course(session.course).speak_bonus

        hand_points = max(0, total_pp - speaking_pts) 

        return hand_points + bonus


            
    @staticmethod
    def _spawn_if_missing(course, tmpl, current_date):

        st = tmpl.start_time_pst.replace(microsecond=0)
        et = tmpl.end_time_pst.replace(microsecond=0)

        with transaction.atomic():
            session, created = CourseSession.objects.get_or_create(
                course=course,
                is_template=False,
                session_date=current_date,
                start_time_pst=st,
                end_time_pst=et,
            )
            
    @staticmethod
    @transaction.atomic
    def spawn_session(course):
        """
        Creates a new CourseSession for 'now' (in the course's timezone),
        using the course's default_session_duration as the end time offset.

        This sets:
          - session_date = today's date
          - start_time_pst = current local time
          - end_time_pst = (start_time + default_session_duration)
        """
        tz = pytz.timezone(course.course_timezone)
        start_time_local = timezone.localtime(timezone.now(), tz)

        default_duration = course.default_session_duration or timedelta(hours=1)
        end_time_local = start_time_local + default_duration

        new_session = CourseSession.objects.create(
            course=course,
            session_date=start_time_local.date(),
            start_time_pst=start_time_local.time(),        
            end_time_pst=end_time_local.time(),            
            is_template=False,
        )
        return new_session
        


    @staticmethod
    def get_next_template_session(course):
        """
        Returns the next template session that is either:
        - scheduled for the future or today (sess.session_date >= current_date), OR
        - has already passed session_date but is "recurring" on today's weekday (active_days).
        """        
        course_tz = course_timezone(course)
        now = timezone.localtime(timezone.now(), course_tz)
        current_date = now.date()
        
        weekday_map = {0: 'mon', 1: 'tue', 2: 'wed', 3: 'thu', 4: 'fri', 5: 'sat', 6: 'sun'}
        current_day = weekday_map[now.weekday()]
        
        candidate_sessions = []
        template_sessions = course.sessions.filter(is_template=True)
        
        for sess in template_sessions:
            if sess.active_days and current_day not in sess.active_days:
                continue
                
            if sess.session_date and sess.session_end_date:
                if sess.session_date <= current_date < sess.session_end_date:
                    candidate_sessions.append(sess)
            else:
                if sess.active_days and current_day in sess.active_days:
                    candidate_sessions.append(sess)                

        next_session = None
        min_time_diff = timedelta.max
        
        for sess in candidate_sessions:
            session_day = current_date  
            session_dt = datetime.combine(session_day, sess.start_time_pst)
            session_dt = course_tz.localize(session_dt)
            
            if session_dt >= now:
                diff = session_dt - now
                if diff < min_time_diff:
                    min_time_diff = diff
                    next_session = sess
        return next_session


    @staticmethod
    @chosen_course_required
    def agora_dash(request):
        render_dict = dict()
        try:
            cid = request.session["course_id"]
            course = Course._default_manager.get(id=cid)
        except Course.DoesNotExist:
            request.session.pop("course_id", None)
            return redirect("course:list")
        CoursePermissions.require_course_member(request.user, course.id)
        request.session["course_id"] = course.id
        request.session['course_role'] = course.id

        client_ip = request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR')
        render_dict['show_agora'] = client_ip not in settings.ALLOWED_IPS

        current_lecture = Lecture.currentLecture(course.id)
        render_dict["lecture"] = current_lecture

        next_url = request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return HttpResponseRedirect(next_url)
        
        course_tz = course_timezone(course)
        now_in_course_tz = timezone.localtime(timezone.now(), course_tz)
        current_time = now_in_course_tz.time()
        current_date = now_in_course_tz.date()

        weekday_map = {0: 'mon', 1: 'tue', 2: 'wed', 3: 'thu', 4: 'fri', 5: 'sat', 6: 'sun'}
        current_day = weekday_map[now_in_course_tz.weekday()]

        session_active = HomeViews.is_session_active(course, current_date, current_time, current_day)
        render_dict["session_active"] = session_active

        lecture = Lecture.currentLecture(course.id)
        render_dict["lecture"] = lecture

        render_dict["course"] = course
        coursemember = CourseBase.get_course_member(request.user, course.id)

        # Looking at a demonstration class through one of its students' eyes.
        # The console puts this in an iframe beside the instructor's own view,
        # so trying Agora out takes one browser rather than two browsers, two
        # accounts and a colleague.
        #
        # Only ever a sandbox, and only ever for the instructor who owns it:
        # a course that is not a sandbox, a requester who is not its
        # instructor, or a member of some other course is ignored outright.
        acting_as = request.GET.get("as")
        if acting_as and course.is_sandbox and coursemember is not None \
                and coursemember.is_instructor():
            understudy = CourseMember.objects.filter(
                pk=acting_as, course=course, role="student", active=True
            ).select_related("user").first()
            if understudy is not None:
                coursemember = understudy
                render_dict["acting_as"] = understudy
        
        # A superuser previewing a student would otherwise still be handed the
        # staff view, because `is_superuser` short-circuits the role.
        previewing = bool(render_dict.get("acting_as"))
        render_dict['is_student'] = coursemember.role in LEARNER_ROLES
        render_dict['is_ta'] = coursemember.role == 'ta'
        render_dict['is_instructor'] = (not previewing) and (
            coursemember.is_instructor() or request.user.is_superuser
        )
        render_dict["coursemember"] = coursemember
        if course.lecture_guests and lecture is not None and render_dict['is_instructor']:
            render_dict["guest_code"] = lecture_guests.code_for(lecture)
            render_dict["guest_count"] = lecture_guests.guest_count(course.id)
            render_dict["guest_join_url"] = request.build_absolute_uri(
                reverse("course:sandbox_join")
            )
        # Every list is labelled by name as well as colour; see CourseQueue.
        render_dict["queue_labels"] = course.queue_labels()
        # What this lecture, on its own, has earned. The same shape the status
        # endpoint sends, so the first paint and every refresh agree.
        open_session = active_session_for_course(
            course,
            manual_active=bool(course.manual_override and current_lecture),
        )
        points_so_far = bonus_so_far = 0
        # A superuser who is not enrolled is handed an unsaved CourseMember by
        # get_course_member, so it has no pk and Django refuses to filter a
        # relation on it. They have no participation of their own either, so
        # zero is both safe and correct.
        if open_session is not None and coursemember.pk:
            for participation in CourseParticipation.objects.filter(
                participant=coursemember,
                session=open_session,
                count_in_calculations=True,
            ):
                points_so_far += participation.participation_points_gained or 0
                bonus_so_far += participation.bonus_points or 0
        render_dict["lecture_status"] = participation_rules.lecture_status(
            course, coursemember, open_session, points_so_far, bonus_so_far
        )
        return render(request, "agora.html", render_dict)
        

    @staticmethod
    def random_student_helper(request, lid):
        return HomeViews._select_student(request, lid)

    @staticmethod
    def _selection_payload(
        course,
        picked,
        lid,
        *,
        selection_version,
        duplicate=False,
    ):
        color_name = {1: "green", 2: "blue", 3: "red", 4: "yellow"}[lid]
        profile = getattr(picked.user, "profile", None)
        protected_photo = ""
        if profile is not None and getattr(profile, "photo", None):
            protected_photo = reverse(
                "account:protected_photo",
                kwargs={"uid": picked.user_id},
            )
        return {
            # An account with no name would otherwise be announced to the room
            # as a blank space. CWL supplies names, but nothing guarantees it.
            # The name they asked to be called, not the registrar's: this is
            # read aloud to the room.
            # A guest is marked so nobody hunts for them on the class list.
            "student": (picked.get_user_fullname() or picked.user.username)
            + (" (guest)" if picked.role == "guest" else ""),
            "student_id": picked.user.username,
            "color": course.get_color_config_dict().get(color_name, ""),
            "list_id": lid,
            "student_photo": protected_photo,
            "selection_version": selection_version,
            "duplicate": duplicate,
        }

    @staticmethod
    def _selection_weights(course, pool, spoken_ids=frozenset()):
        """How likely each raised hand is to be chosen.

        Two effects multiply. The first is the term-long one below: students
        who have spoken more across the course weigh less. The second is
        within this class, and is `course.spoken_weight` -- the share of a
        chance a student who has already spoken today keeps, next to one who
        has not.

        At spoken_weight 0 the product is zero for anyone who has spoken, so
        they cannot be chosen while an unspoken hand is up; that is the hard
        exclusion this used to do by filtering the pool, and it remains the
        default. At 0.5 they are half as likely. At 1 today is ignored. The
        dial is continuous, so a course can sit anywhere between.
        """
        if not pool:
            return np.array([])
        bias_exponent = course.weight_exponent
        if np.isclose(bias_exponent, 0):
            return HomeViews._apply_within_class(
                course, pool, spoken_ids, np.ones(len(pool))
            )
        counts = CourseParticipation.objects.filter(
            participant__in=pool,
            spoke_upon_participation=True,
            count_in_calculations=True,
        ).values("participant").annotate(total=Count("id"))
        # The floor of 2 exists because log2(1) is 0 and log2(0) undefined, so
        # it guards the division below. Its side effect is that having spoken
        # nought, once or twice all weigh the same -- the first two turns are
        # free -- and only the third begins to downweight.
        #
        # Reviewed and kept deliberately on 2026-09-04. Measured on CPSC 430
        # (median 7 turns, most active 13) the curve still spreads selection
        # 3.7-fold across a term, and CPSC 320 runs weight_exponent 2.0 where
        # eight turns weigh 11% of none. The early grace is acceptable against
        # that. `1 / log2(n + 2)` would remove the flat spot if it ever stops
        # being acceptable; do not "fix" it without asking, it is not an
        # oversight.
        spoken = {item["participant"]: max(item["total"], 2) for item in counts}
        weights = np.array([
            (1.0 / np.log2(spoken.get(member.id, 2))) ** bias_exponent
            for member in pool
        ])
        return HomeViews._apply_within_class(course, pool, spoken_ids, weights)

    @staticmethod
    def _apply_within_class(course, pool, spoken_ids, weights):
        """Scale by today, then normalise."""
        share = float(getattr(course, "spoken_weight", 0.0) or 0.0)
        today = np.array([
            share if member.pk in spoken_ids else 1.0 for member in pool
        ])
        scaled = weights * today
        # Everyone raising has already spoken (or the share is zero and they
        # all have). Nobody can be held back against nobody, so fall back to
        # the term-long weights over the whole pool -- which is what the old
        # `pool = unspoken or raisers` did in exactly this case.
        if scaled.sum() <= 0:
            scaled = weights
        if scaled.sum() <= 0:
            return np.ones(len(pool)) / len(pool)
        return scaled / scaled.sum()

    @staticmethod
    def _select_student(request, lid, preferred_member_id=None):
        try:
            lid = int(lid)
        except (TypeError, ValueError):
            return JsonResponse(
                {"student": "Invalid list id.", "student_id": "None"},
                status=400,
            )
        hand_field = {
            1: "hand_up",
            2: "hand_up_list_2",
            3: "hand_up_list_3",
            4: "hand_up_list_4",
        }.get(lid)
        if hand_field is None:
            return JsonResponse(
                {"student": "Invalid list id.", "student_id": "None"},
                status=400,
            )

        course_id = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, course_id)
        channel_layer = get_channel_layer()
        created_selection = False

        with transaction.atomic():
            # One course lock serializes selections across workers and browser tabs.
            course = Course.objects.select_for_update().get(pk=course_id)
            open_lecture = Lecture.currentLecture(course.pk)
            open_session = active_session_for_course(
                course,
                for_update=True,
                manual_active=bool(course.manual_override and open_lecture),
            )
            if open_session is None:
                return JsonResponse({
                    "student": "No active session. Start lecture first.",
                    "student_id": "None",
                })
            # Calling on someone credits every hand that is up, which during
            # a roll call is the whole room.
            if attendance.is_open(course):
                return JsonResponse(
                    {
                        "student": "Attendance is being taken. Close it before calling on anyone.",
                        "student_id": "None",
                    },
                    status=409,
                )

            # The last committed selection is a durable optimistic-concurrency
            # token. Two tabs that observed the same version can submit freely: the
            # first request advances it and every replay returns that committed row.
            current_selection = CourseParticipation.objects.select_related(
                "participant__user__profile"
            ).filter(
                session=open_session,
                spoke_upon_participation=True,
                real_participation=True,
                count_in_calculations=True,
                participation_list__in=(1, 2, 3, 4),
            ).order_by("-pk").first()
            current_version = current_selection.pk if current_selection else 0
            submitted_version = request.POST.get("selection_version")
            if submitted_version is None:
                return JsonResponse(
                    {
                        "student": "Refresh the dashboard before selecting a student.",
                        "student_id": "None",
                        "selection_version": current_version,
                    },
                    status=400,
                )
            try:
                expected_version = int(submitted_version)
            except (TypeError, ValueError):
                return JsonResponse(
                    {"student": "Invalid selection version.", "student_id": "None"},
                    status=400,
                )
            if expected_version < 0:
                return JsonResponse(
                    {"student": "Invalid selection version.", "student_id": "None"},
                    status=400,
                )

            if expected_version != current_version and current_selection is not None:
                payload = HomeViews._selection_payload(
                    course,
                    current_selection.participant,
                    current_selection.participation_list,
                    selection_version=current_version,
                    duplicate=True,
                )
            elif expected_version != current_version:
                return JsonResponse(
                    {
                        "student": "Selection state changed. Please try again.",
                        "student_id": "None",
                        "selection_version": current_version,
                    },
                    status=409,
                )
            else:
                raisers = list(
                    # Lock the course members only. Profile is a reverse
                    # one-to-one, so select_related joins it as a LEFT OUTER
                    # JOIN, and PostgreSQL refuses FOR UPDATE against the
                    # nullable side of one.
                    CourseMember.objects.select_for_update(of=("self",)).select_related(
                        "user__profile"
                    ).filter(
                        course=course,
                        role__in=LEARNER_ROLES,
                        active=True,
                        **{hand_field: True},
                    )
                )
                if preferred_member_id is not None:
                    raisers = [member for member in raisers if member.pk == preferred_member_id]
                if not raisers:
                    return JsonResponse({
                        "student": f"No available student in the {hand_field} list.",
                        "student_id": "None",
                        "selection_version": current_version,
                    })

                spoken_ids = set(CourseParticipation.objects.filter(
                    participant__in=raisers,
                    session=open_session,
                    spoke_upon_participation=True,
                    count_in_calculations=True,
                ).values_list("participant_id", flat=True))
                pool = raisers
                if preferred_member_id is not None:
                    picked = pool[0]
                elif not course.list_counts_for_points(lid):
                    # A queue that earns nothing -- students wanting to ask a
                    # question rather than answer one -- is a plain draw. The
                    # spoken-today and term-long biases exist to spread
                    # participation credit around, and there is none here.
                    picked = np.random.choice(pool)
                else:
                    picked = np.random.choice(
                        pool,
                        p=HomeViews._selection_weights(course, pool, spoken_ids),
                    )

                # Conditional claiming is a second line of defence on databases where
                # row-level locking semantics differ. Only a still-raised member wins.
                claimed = CourseMember.objects.filter(
                    pk=picked.pk,
                    **{hand_field: True},
                ).update(**{hand_field: False})
                if claimed != 1:
                    return JsonResponse(
                        {"student": "Selection already completed.", "student_id": "None"},
                        status=409,
                    )
                setattr(picked, hand_field, False)
                # The claim above is a conditional UPDATE, which bypasses the
                # model hook that records hand events, so record it here.
                hand_events.record_lowered(
                    picked, lid, HandRaise.Resolution.SPOKE
                )

                scoring = course.list_counts_for_points(lid)
                # One source of truth: the tally on screen is the grade being
                # earned, not a parallel currency that happens to look like it.
                rules = participation_rules.for_course(course)
                speaking_points = rules.speak_bonus if scoring else 0
                hand_points = rules.raise_value if scoring else 0
                maximum = rules.lecture_cap
                running = HomeViews.current_total(picked, open_session)
                new_points = speaking_points + hand_points
                if maximum is not None:
                    new_points = max(0, min(new_points, maximum - running))

                selection = CourseParticipation.objects.create(
                    participant=picked,
                    session=open_session,
                    time_participated=timezone.now(),
                    participation_list=lid,
                    participation_points_gained=new_points,
                    spoke_upon_participation=True,
                    real_participation=True,
                    # Follows the list, as every other value here does. A
                    # speaker on an excluded list is recorded but not counted,
                    # which is what the deployed instance does and what the
                    # non-speaker rows below already did.
                    count_in_calculations=scoring,
                )

                if rules.bonus_after_speaking and scoring:
                    previous_ids = CourseParticipation.objects.filter(
                        session=open_session,
                        spoke_upon_participation=True,
                        count_in_calculations=True,
                    ).exclude(participant=picked).values_list(
                        "participant_id", flat=True
                    ).distinct()
                    for participant_id in previous_ids:
                        last = CourseParticipation.objects.select_for_update().filter(
                            participant_id=participant_id,
                            session=open_session,
                            spoke_upon_participation=True,
                            count_in_calculations=True,
                        ).order_by("-time_participated", "-pk").first()
                        running = HomeViews.current_total(last.participant, open_session)
                        if maximum is None or running < maximum:
                            last.bonus_points += 1 if maximum is None else min(1, maximum - running)
                            last.save(update_fields=["bonus_points"])

                others = CourseMember.objects.select_for_update().filter(
                    course=course,
                    role="student",
                    active=True,
                    **{hand_field: True},
                )
                if rules.bonus_after_speaking:
                    # A student who has spoken is paid for the rest of the
                    # lecture through the bonus above, whether or not their
                    # hand is up, so a raised hand must not pay them twice.
                    # Without that rule -- AI 100 counts every raise all term
                    # -- their later raises count like anyone else's. Until
                    # 2026-09-16 they were excluded either way, and a student
                    # whose hand was up through eight calls was credited three.
                    selected_ids = CourseParticipation.objects.filter(
                        session=open_session,
                        spoke_upon_participation=True,
                        count_in_calculations=True,
                    ).values_list("participant_id", flat=True)
                    others = others.exclude(pk__in=selected_ids)
                for other in others:
                    participation, _created = CourseParticipation.objects.get_or_create(
                        participant=other,
                        session=open_session,
                        participation_list=lid,
                        spoke_upon_participation=False,
                        count_in_calculations=scoring,
                        defaults={
                            "time_participated": timezone.now(),
                            "participation_points_gained": 0,
                            "real_participation": False,
                            "bonus_points": 0,
                        },
                    )
                    if scoring:
                        running = HomeViews.current_total(other, open_session)
                        if maximum is None or running < maximum:
                            participation.participation_points_gained += (
                                hand_points if maximum is None else min(hand_points, maximum - running)
                            )
                            participation.save(update_fields=["participation_points_gained"])

                payload = HomeViews._selection_payload(
                    course,
                    picked,
                    lid,
                    selection_version=selection.pk,
                )
                created_selection = True
                select_microphone_student(course.pk, picked, open_lecture)

        if created_selection:
            async_to_sync(channel_layer.group_send)(
                f"course_{course.pk}",
                {
                    "type": "send_message",
                    "key": "next-student",
                    "send_auth_id": request.user.pk,
                    "value": payload["student_id"],
                },
            )
            async_to_sync(channel_layer.group_send)(
                f"course_{course.pk}",
                {
                    "type": "send_message",
                    "key": "count-hands-up",
                    "send_auth_id": request.user.pk,
                },
            )
        return JsonResponse(payload)

    @staticmethod
    @login_required
    @chosen_course_required
    def polls(request):
        context = {}
        try:
            cid = request.session["course_id"]
            course = Course.objects.get(id=cid)
        except (Course.DoesNotExist, KeyError):
            request.session.pop("course_id", None)
            return redirect('course:list')
        
        coursemember = CourseBase.get_course_member(request.user, course.id)
        context['course'] = course
        context['coursemember'] = coursemember
        context['is_student'] = coursemember.role in LEARNER_ROLES
        context['is_ta'] = coursemember.role == 'ta'
        context['is_instructor'] = coursemember.is_instructor() or request.user.is_superuser

        return render(request, 'polls.html', context)




    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def random_student(request):
        return HomeViews.random_student_helper(request, 1)

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def random_student_blue(request):
        return HomeViews.random_student_helper(request, 2)


    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def random_student_red(request):
        return HomeViews.random_student_helper(request, 3)

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def random_student_yellow(request):
        return HomeViews.random_student_helper(request, 4)


    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def choose_next(request, uid, lid):
        try:
            member_id = int(uid)
        except (TypeError, ValueError):
            return JsonResponse(
                {"student": "Invalid student id.", "student_id": "None"},
                status=400,
            )
        return HomeViews._select_student(request, lid, preferred_member_id=member_id)



    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    # This is green btw, it was "all" from before but should get updated. 
    def clear_all(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            coursemember.time_spoken = timezone.now()
            coursemember.save()

            students = CourseMember.objects.filter(course = course, hand_up= True, 
                role__in=LEARNER_ROLES, active= True)

            with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
                for student in students:
                    student.hand_up = False
                    student.save()

            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'clear-hands',
                    'send_auth_id': request.user.pk
                })

        return HttpResponse('hands were cleared out.')

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def clear_all_lists(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)

        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            coursemember.time_spoken = timezone.now()
            coursemember.save()
            
            students = CourseMember.objects.filter(course = course, role__in=LEARNER_ROLES, hand_up= True, active= True) | CourseMember.objects.filter(course = course, role__in=LEARNER_ROLES, hand_up_list_2= True, active= True) | CourseMember.objects.filter(course = course, role__in=LEARNER_ROLES, hand_up_list_3= True, active= True) | CourseMember.objects.filter(course = course, role__in=LEARNER_ROLES, hand_up_list_4= True, active= True)
            with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
                for student in students:
                    student.hand_up= False
                    student.hand_up_list_2= False
                    student.hand_up_list_3= False
                    student.hand_up_list_4= False
                    student.save()

            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'clear-hands',
                    'send_auth_id': request.user.pk
                })

        return HttpResponse('hands were cleared out.')

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def clear_all_blue(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            coursemember.time_spoken = timezone.now()
            coursemember.save()
            
            students = CourseMember.objects.filter(course = course, hand_up_list_2= True, 
                role__in=LEARNER_ROLES, active= True)

            with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
                for student in students:
                    student.hand_up_list_2 = False
                    student.save()

            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'clear-hands',
                    'send_auth_id': request.user.pk
                })

        return HttpResponse('Blue hands were cleared out.')

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def clear_all_red(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            coursemember.time_spoken = timezone.now()
            coursemember.save()
            
            students = CourseMember.objects.filter(course = course, hand_up_list_3= True, 
                role__in=LEARNER_ROLES, active= True)

            with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
                for student in students:
                    student.hand_up_list_3 = False
                    student.save()

            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'clear-hands',
                    'send_auth_id': request.user.pk
                })

        return HttpResponse('Red hands were cleared out.')


    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def clear_all_yellow(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            coursemember.time_spoken = timezone.now()
            coursemember.save()
            
            students = CourseMember.objects.filter(course = course, hand_up_list_4= True, 
                role__in=LEARNER_ROLES, active= True)

            with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
                for student in students:
                    student.hand_up_list_4 = False
                    student.save()

            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'clear-hands',
                    'send_auth_id': request.user.pk
                })

        return HttpResponse('Yellow hands were cleared out.')



    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def enable(request):
        return HomeViews.enable_list_helper(request,1)

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def enable_blue(request):
        return HomeViews.enable_list_helper(request,2)

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def enable_red(request):
        return HomeViews.enable_list_helper(request,3)

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def enable_yellow(request):
        return HomeViews.enable_list_helper(request,4)

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def enable_all(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)

        coursemember.hand_up = True
        coursemember.hand_up_list_2 = True
        coursemember.hand_up_list_3 = True
        coursemember.hand_up_list_4 = True
        coursemember.save()
        
        send_message = request.GET.get('send_message') == "true"

        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            # Update all instructors (consistent with individual enable functions)
            all_instructors = CourseMember.objects.filter(course=course, role="instructor")
            all_instructors.update(hand_up=True, hand_up_list_2=True, hand_up_list_3=True, hand_up_list_4=True)
            
            # For superusers, also ensure their own coursemember record is updated
            if request.user.is_superuser and coursemember and not coursemember.is_instructor():
                coursemember.hand_up = True
                coursemember.hand_up_list_2 = True
                coursemember.hand_up_list_3 = True
                coursemember.hand_up_list_4 = True
                coursemember.save()
            
            if send_message:
                async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'enable-hand',
                        'send_auth_id': request.user.pk
                    })

        return HttpResponse('Hand up was enabled in all lists.')
    
    def enable_list_helper(request, lid):
        channel_layer = get_channel_layer()

        render_dict = dict()
        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        send_message = request.GET.get('send_message') == "true"

        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            if lid == 1:
                # Update all instructors (consistent with disable)
                all_instructors = CourseMember.objects.filter(course=course, role="instructor")
                all_instructors.update(hand_up=True)
                
                coursemember.hand_up = True
                coursemember.time_spoken = timezone.now()
                coursemember.save()
                color = 'Green'
                
                # For superusers, also ensure their own coursemember record is updated
                if request.user.is_superuser and coursemember and not coursemember.is_instructor():
                    coursemember.hand_up = True
                    coursemember.save()

                if send_message:
                    async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'enable-hand',
                        'send_auth_id': request.user.pk
                    })

                    async_to_sync(channel_layer.group_send)(
                        f"course_{course.id}",
                        {
                            "type": "send_message",
                            "key": "count-hands-up",
                            "send_auth_id": request.user.pk
                        }
                    )

            if lid == 2:
                # Update all instructors (consistent with disable)
                all_instructors = CourseMember.objects.filter(course=course, role="instructor")
                all_instructors.update(hand_up_list_2=True)
                
                coursemember.hand_up_list_2 = True
                coursemember.time_spoken = timezone.now()
                coursemember.save()
                color = 'Blue'
                
                # For superusers, also ensure their own coursemember record is updated
                if request.user.is_superuser and coursemember and not coursemember.is_instructor():
                    coursemember.hand_up_list_2 = True
                    coursemember.save()

                if send_message:
                    async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'enable-hand',
                        'send_auth_id': request.user.pk
                    })

                    async_to_sync(channel_layer.group_send)(
                        f"course_{course.id}",
                        {
                            "type": "send_message",
                            "key": "count-hands-up",
                            "send_auth_id": request.user.pk
                        }
                    )

            if lid == 3:
                # Update all instructors (consistent with disable)
                all_instructors = CourseMember.objects.filter(course=course, role="instructor")
                all_instructors.update(hand_up_list_3=True)
                
                coursemember.hand_up_list_3 = True
                coursemember.time_spoken = timezone.now()
                coursemember.save()
                color = 'Red'
                
                # For superusers, also ensure their own coursemember record is updated
                if request.user.is_superuser and coursemember and not coursemember.is_instructor():
                    coursemember.hand_up_list_3 = True
                    coursemember.save()

                if send_message:
                    async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'enable-hand',
                        'send_auth_id': request.user.pk
                    })

                    async_to_sync(channel_layer.group_send)(
                        f"course_{course.id}",
                        {
                            "type": "send_message",
                            "key": "count-hands-up",
                            "send_auth_id": request.user.pk
                        }
                    )


            if lid == 4:
                # Update all instructors (consistent with disable)
                all_instructors = CourseMember.objects.filter(course=course, role="instructor")
                all_instructors.update(hand_up_list_4=True)
                
                coursemember.hand_up_list_4 = True
                coursemember.time_spoken = timezone.now()
                coursemember.save()
                color = 'Yellow'
                
                # For superusers, also ensure their own coursemember record is updated
                if request.user.is_superuser and coursemember and not coursemember.is_instructor():
                    coursemember.hand_up_list_4 = True
                    coursemember.save()

                if send_message:
                    async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'enable-hand',
                        'send_auth_id': request.user.pk
                    })

                    async_to_sync(channel_layer.group_send)(
                        f"course_{course.id}",
                        {
                            "type": "send_message",
                            "key": "count-hands-up",
                            "send_auth_id": request.user.pk
                        }
                    )

  
        if coursemember.role in LEARNER_ROLES:
            # A student already in another queue who clicks this one wants this
            # one. Refusing left them to work out that they had to lower a hand
            # first, in the middle of a lecture, and the refusal did not even
            # consult the setting that was supposed to permit it: a course with
            # multiple queues turned on was refused all the same.
            elsewhere = [
                (other, field)
                for other, field in hand_events.HAND_FIELDS.items()
                if other != lid and getattr(coursemember, field, False)
            ]
            if elsewhere and not course.allow_multiple_queues:
                with hand_events.resolving_as(HandRaise.Resolution.MOVED):
                    for _other, field in elsewhere:
                        setattr(coursemember, field, False)
                    coursemember.save(
                        update_fields=[field for _other, field in elsewhere]
                    )
                elsewhere = []
            if elsewhere and not course.allow_multiple_queues:
                render_dict['hand'] = '<span style="color: red">  You can only raise your hand in one list at the time. </span>'
            else:
                instructors = CourseMember.objects.filter(course = course, role= 'instructor')
                
                if instructors.exists():
                    ref_time = instructors[0].time_spoken
                    for instructor in instructors:
                        if ref_time < instructor.time_spoken:
                            ref_time = instructor.time_spoken
                else:
                    ref_time = timezone.now()
            
                if lid ==1:
                    if instructors.exists():
                        for instructor in instructors:
                            if instructor.hand_up == True:
                                coursemember.hand_up= True
                                if coursemember.time_spoken < ref_time:
                                    coursemember.time_spoken = timezone.now()
                                coursemember.save()
                                render_dict['hand'] = 'Your hand is up in the Green list. '
                            else:
                                render_dict['hand'] = 'The Green list is disabled by the instructor for now.'

                        async_to_sync(channel_layer.group_send)(
                        f'course_{course.id}',
                        {
                            'type': 'send_message',
                            'key': 'update-hand-status',
                            'send_auth_id': request.user.pk
                        })

                        async_to_sync(channel_layer.group_send)(
                        f"course_{course.id}",
                        {
                            "type": "send_message",
                            "key": "count-hands-up",
                            "send_auth_id": request.user.pk
                        }
                    )

                                
                if lid ==2:
                    if instructors.exists():
                        for instructor in instructors:
                            if instructor.hand_up_list_2 == True:
                                coursemember.hand_up_list_2 = True
                                if coursemember.time_spoken < ref_time:
                                    coursemember.time_spoken = timezone.now()
                                coursemember.save()
                                render_dict['hand'] = 'Your hand is up in the Blue list. '
                            else:
                                render_dict['hand'] = 'The Blue list is disabled by the instructor for now.'

                        async_to_sync(channel_layer.group_send)(
                        f'course_{course.id}',
                        {
                            'type': 'send_message',
                            'key': 'update-hand-status',
                            'send_auth_id': request.user.pk
                        })

                        async_to_sync(channel_layer.group_send)(
                        f"course_{course.id}",
                        {
                            "type": "send_message",
                            "key": "count-hands-up",
                            "send_auth_id": request.user.pk
                        }
                    )

                            
                if lid ==3: 
                    if instructors.exists():
                        for instructor in instructors:
                            if instructor.hand_up_list_3 == True:
                                coursemember.hand_up_list_3 = True
                                if coursemember.time_spoken < ref_time:
                                    coursemember.time_spoken = timezone.now()
                                coursemember.save()
                                render_dict['hand'] = 'Your hand is up in the Red list. '
                            else:
                                render_dict['hand'] = 'The Red list is disabled by the instructor for now.'

                        async_to_sync(channel_layer.group_send)(
                        f'course_{course.id}',
                        {
                            'type': 'send_message',
                            'key': 'update-hand-status',
                            'send_auth_id': request.user.pk
                        })

                        async_to_sync(channel_layer.group_send)(
                        f"course_{course.id}",
                        {
                            "type": "send_message",
                            "key": "count-hands-up",
                            "send_auth_id": request.user.pk
                        }
                    )

                            
                if lid ==4: 
                    if instructors.exists():
                        for instructor in instructors:
                            if instructor.hand_up_list_4 == True:
                                coursemember.hand_up_list_4 = True
                                if coursemember.time_spoken < ref_time:
                                    coursemember.time_spoken = timezone.now()
                                coursemember.save()
                                render_dict['hand'] = 'Your hand is up in the Yellow list. '
                            else:
                                render_dict['hand'] = 'The Yellow list is disabled by the instructor for now.'

                        async_to_sync(channel_layer.group_send)(
                        f'course_{course.id}',
                        {
                            'type': 'send_message',
                            'key': 'update-hand-status',
                            'send_auth_id': request.user.pk
                        })

                        async_to_sync(channel_layer.group_send)(
                        f"course_{course.id}",
                        {
                            "type": "send_message",
                            "key": "count-hands-up",
                            "send_auth_id": request.user.pk
                        }
                    )
                            
        return JsonResponse(render_dict)

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def disable(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        coursemember.hand_up = False
        coursemember.save()
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role in LEARNER_ROLES:
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'update-hand-status',
                    'send_auth_id': request.user.pk
                })

        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            all_instructors = CourseMember.objects.filter(course=course, role="instructor")
            all_instructors.update(hand_up=False)
            
            students = CourseMember.objects.filter(course = course, hand_up= True, 
                role__in=LEARNER_ROLES, active= True)
            
            with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
                for student in students:
                    student.hand_up= False
                    student.save()

            if send_message:
                async_to_sync(channel_layer.group_send)(
                    f'course_{course.id}',
                    {
                        'type': 'send_message',
                        'key': 'disable-hand',
                        'send_auth_id': request.user.pk
                    })

        return HttpResponse('hand up was disabled.')

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def disable_blue(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        coursemember.hand_up_list_2 = False
        coursemember.save()
        
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role in LEARNER_ROLES:
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'update-hand-status',
                    'send_auth_id': request.user.pk
                })

        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            all_instructors = CourseMember.objects.filter(course=course, role="instructor")
            all_instructors.update(hand_up_list_2=False)
            
            # For superusers, also ensure their own coursemember record is updated
            if request.user.is_superuser and coursemember and not coursemember.is_instructor():
                coursemember.hand_up_list_2 = False
                coursemember.save()
            
            students = CourseMember.objects.filter(course = course, hand_up_list_2= True, 
                role__in=LEARNER_ROLES, active= True)
            with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
                for student in students:
                    student.hand_up_list_2= False
                    student.save()

            if send_message:
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                        'type': 'send_message',
                        'key': 'disable-hand',
                        'send_auth_id': request.user.pk
                })

        return HttpResponse('hand up was disabled for the Blue list.')

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def disable_red(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        coursemember.hand_up_list_3 = False
        coursemember.save()
        
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role in LEARNER_ROLES:
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'update-hand-status',
                    'send_auth_id': request.user.pk
                })

        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            all_instructors = CourseMember.objects.filter(course=course, role="instructor")
            all_instructors.update(hand_up_list_3=False)
            
            # For superusers, also ensure their own coursemember record is updated
            if request.user.is_superuser and coursemember and not coursemember.is_instructor():
                coursemember.hand_up_list_3 = False
                coursemember.save()
            
            students = CourseMember.objects.filter(course = course, hand_up_list_3= True, 
                role__in=LEARNER_ROLES, active= True)
            with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
                for student in students:
                    student.hand_up_list_3= False
                    student.save()

            if send_message:
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                        'type': 'send_message',
                        'key': 'disable-hand',
                        'send_auth_id': request.user.pk
                })

        return HttpResponse('hand up was disabled for the Red list.')

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def disable_yellow(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)
        
        coursemember.hand_up_list_4 = False
        coursemember.save()
        
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role in LEARNER_ROLES:
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'update-hand-status',
                    'send_auth_id': request.user.pk
                })

        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            all_instructors = CourseMember.objects.filter(course=course, role="instructor")
            all_instructors.update(hand_up_list_4=False)
            
            # For superusers, also ensure their own coursemember record is updated
            if request.user.is_superuser and coursemember and not coursemember.is_instructor():
                coursemember.hand_up_list_4 = False
                coursemember.save()
            
            students = CourseMember.objects.filter(course = course, hand_up_list_4= True, 
                role__in=LEARNER_ROLES, active= True)
            with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
                for student in students:
                    student.hand_up_list_4= False
                    student.save()

            if send_message:
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                        'type': 'send_message',
                        'key': 'disable-hand',
                        'send_auth_id': request.user.pk
                })

        return HttpResponse('hand up was disabled for the Yellow list.')


    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def disable_all(request):
        channel_layer = get_channel_layer()

        cid = request.session["course_id"]
        CoursePermissions.require_course_member(request.user, cid)

        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)

        coursemember.hand_up = False
        coursemember.hand_up_list_2 = False
        coursemember.hand_up_list_3 = False
        coursemember.hand_up_list_4 = False
        coursemember.save()
        
        send_message = request.GET.get('send_message') == "true"

        if coursemember.role in LEARNER_ROLES:
            async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'update-hand-status',
                    'send_auth_id': request.user.pk
                })

        if request.user.is_superuser or (coursemember and coursemember.is_instructor()):
            # Update all instructors (consistent with individual disable functions)
            all_instructors = CourseMember.objects.filter(course=course, role="instructor")
            all_instructors.update(hand_up=False, hand_up_list_2=False, hand_up_list_3=False, hand_up_list_4=False)
            
            # For superusers, also ensure their own coursemember record is updated
            if request.user.is_superuser and coursemember and not coursemember.is_instructor():
                coursemember.hand_up = False
                coursemember.hand_up_list_2 = False
                coursemember.hand_up_list_3 = False
                coursemember.hand_up_list_4 = False
                coursemember.save()
            
            students = CourseMember.objects.filter(course = course, role__in=LEARNER_ROLES, hand_up= True, active= True) | CourseMember.objects.filter(course = course, role__in=LEARNER_ROLES, hand_up_list_2= True, active= True) | CourseMember.objects.filter(course = course, role__in=LEARNER_ROLES, hand_up_list_3= True, active= True) | CourseMember.objects.filter(course = course, role__in=LEARNER_ROLES, hand_up_list_4= True, active= True)
            with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
                for student in students:
                    student.hand_up= False
                    student.hand_up_list_2= False
                    student.hand_up_list_3= False
                    student.hand_up_list_4= False
                    student.save()

            if send_message:
                async_to_sync(channel_layer.group_send)(
                f'course_{course.id}',
                {
                    'type': 'send_message',
                    'key': 'disable-hand',
                    'send_auth_id': request.user.pk
                })

        return HttpResponse('Hand up was disabled in all lists.')


    @staticmethod
    @login_required
    @chosen_course_required
    def check_status(request):
        render_dict = dict()
        cid = request.session["course_id"]
        course = Course._default_manager.get(id=cid)
        course_tz = course_timezone(course)
        now_in_course_tz = timezone.localtime(timezone.now(), course_tz)
        current_date = now_in_course_tz.date()
        current_time = now_in_course_tz.time()
        weekday_map = {0: 'mon', 1: 'tue', 2: 'wed', 3: 'thu', 4: 'fri', 5: 'sat', 6: 'sun'}
        current_day = weekday_map[now_in_course_tz.weekday()]
        CoursePermissions.require_course_member(request.user, cid)
        coursemember = CourseBase.get_course_member(request.user, cid)
        
        # For instructors/superusers, check their own hand status (consistent with count_hands_up)
        # For students, check if any instructor has the hand enabled
        is_staff = request.user.is_superuser or (coursemember and coursemember.role in ['instructor', 'ta'])
        
        if is_staff:
            # Instructor/Superuser: use their own hand status
            green_enabled = bool(coursemember.hand_up)
            render_dict['disbale_green'] = not green_enabled
            if not green_enabled:
                render_dict['status_green'] = 'Your hand is down in the Green list.'
            else:
                render_dict['status_green'] = 'Your hand is up in the Green list. '
            
            blue_enabled = bool(coursemember.hand_up_list_2)
            render_dict['disbale_blue'] = not blue_enabled
            if not blue_enabled:
                render_dict['status_blue'] = 'Your hand is down in the Blue list.'
            else:
                render_dict['status_blue'] = 'Your hand is up in the Blue list. '
            
            red_enabled = bool(coursemember.hand_up_list_3)
            render_dict['disbale_red'] = not red_enabled
            if not red_enabled:
                render_dict['status_red'] = 'Your hand is down in the Red list.'
            else:
                render_dict['status_red'] = 'Your hand is up in the Red list. '
            
            yellow_enabled = bool(coursemember.hand_up_list_4)
            render_dict['disbale_yellow'] = not yellow_enabled
            if not yellow_enabled:
                render_dict['status_yellow'] = 'Your hand is down in the Yellow list.'
            else:
                render_dict['status_yellow'] = 'Your hand is up in the Yellow list. '
        else:
            # Student: a list is open when any instructor has that hand up.
            # One query for all four lists; this view is fetched by every
            # student's page during a lecture, so its query count is load.
            raised = list(
                CourseMember.objects.filter(course=course, role='instructor')
                .values_list('hand_up', 'hand_up_list_2', 'hand_up_list_3', 'hand_up_list_4')
            )
            lists = (
                ('green', 'Green', 0, coursemember.hand_up),
                ('blue', 'Blue', 1, coursemember.hand_up_list_2),
                ('red', 'Red', 2, coursemember.hand_up_list_3),
                ('yellow', 'Yellow', 3, coursemember.hand_up_list_4),
            )
            for key, label, column, own_hand_up in lists:
                if not any(flags[column] for flags in raised):
                    render_dict[f'disbale_{key}'] = True
                else:
                    render_dict[f'disbale_{key}'] = False
                    if not own_hand_up:
                        render_dict[f'status_{key}'] = f'Your hand is down in the {label} list.'
                    else:
                        render_dict[f'status_{key}'] = f'Your hand is up in the {label} list. '
        current_lecture = Lecture.currentLecture(course.id)
        if current_lecture:
            active_poll = Poll.objects.filter(lecture=current_lecture, end_time__isnull=True).order_by('-poll_id').first()
        else:
            active_poll = None

        if active_poll:
            render_dict['poll_active'] = True
            # This src tells the student client to load the active poll.
            render_dict['poll_src'] = '/polling/student/?start-poll=true'
        else:
            render_dict['poll_active'] = False
            render_dict['poll_src'] = None

        # is_session_active() is exactly "active_session_for_course() found a
        # session" evaluated at this same instant, so resolve the session once
        # and answer both questions from it.
        open_session = active_session_for_course(
            course,
            manual_active=bool(course.manual_override and current_lecture),
        )
        session_active = open_session is not None
        render_dict["session_active"] = session_active
        render_dict["lecture_running"] = current_lecture is not None
        # Whether a roll call is open (never which list: see
        # peer_course.attendance), and whether this student answered one
        # already closed in this lecture. One query, as this is polled by
        # every phone in the room.
        roll_calls = Q(closed_at__isnull=True)
        if open_session is not None and coursemember.pk:
            roll_calls |= Q(
                closed_at__isnull=False,
                session=open_session,
                records__member=coursemember,
                records__status__in=AttendanceRecord.ATTENDED,
            )
        closed_at = list(
            AttendanceCheck.objects.filter(roll_calls, course=course)
            .values_list("closed_at", flat=True)[:10]
        )
        render_dict["attendance_open"] = any(c is None for c in closed_at)
        answered_roll_call = any(c is not None for c in closed_at)
        if open_session:
            participations_total = list(CourseParticipation.objects.filter(
                participant=coursemember,
                count_in_calculations=True,
                session=open_session
            ))
        else:
            participations_total = []
        spoke_today = any(p.spoke_upon_participation for p in participations_total)
        # What a student who has already spoken should be told depends on two
        # settings, and the lecture screen shows it in eight places. Decide it
        # once, here, and let the screen render whatever it is given -- so a
        # course that turns phantom points off does not keep promising them,
        # and one that can call a student twice says so.
        notes = []
        if spoke_today and participation_rules.for_course(course).bonus_after_speaking:
            notes.append("you will collect phantom points automatically")
        if spoke_today and (course.spoken_weight or 0) > 0:
            notes.append("you may still be called again, so stay ready")
        main_total = sum(p.participation_points_gained for p in participations_total)
        bonus_total = sum(p.bonus_points for p in participations_total)
        render_dict["total_points_today"] = main_total
        render_dict["total_bonus_points"] = bonus_total
        # What this lecture, on its own, has earned. Shaped by what the course
        # grades, so an attendance course is not shown a points total and a
        # points course is not shown a tick.
        render_dict["lecture_status"] = participation_rules.lecture_status(
            course, coursemember, open_session, main_total, bonus_total,
            answered_roll_call=answered_roll_call,
        )
        render_dict["spoken_note"] = (
            "You have already spoken in this session, so " + " and ".join(notes)
            if notes
            else ""
        )
        return JsonResponse(render_dict)

    @staticmethod
    @login_required
    @chosen_course_required
    def count_hands_up(request):
        channel_layer = get_channel_layer()
        render_dict = dict()
        cid = request.session["course_id"]
        course = Course._default_manager.get(id=cid)

        open_session = active_session_for_course(
            course,
            manual_active=bool(course.manual_override and Lecture.currentLecture(course.pk)),
        )

        coursemember = CourseBase.get_course_member(request.user, course.id)
        render_dict['green_enabled'] = bool(coursemember.hand_up)
        render_dict['blue_enabled']  = bool(coursemember.hand_up_list_2)
        render_dict['red_enabled']   = bool(coursemember.hand_up_list_3)
        render_dict['yellow_enabled']= bool(coursemember.hand_up_list_4)
        selection_versions = {str(list_id): 0 for list_id in range(1, 5)}
        if open_session:
            for participation in CourseParticipation.objects.filter(
                participant__course=course,
                session=open_session,
                spoke_upon_participation=True,
                real_participation=True,
                count_in_calculations=True,
                participation_list__in=(1, 2, 3, 4),
            ).order_by("-time_participated", "-pk"):
                key = str(participation.participation_list)
                if selection_versions[key] == 0:
                    selection_versions[key] = participation.pk
        render_dict["selection_versions"] = selection_versions
        render_dict["selection_version"] = max(selection_versions.values(), default=0)

        # During a roll call a student must not see where the room's hands
        # went: from outside the lecture hall, that is the answer.
        hidden_for_attendance = (
            not request.user.is_superuser
            and not coursemember.is_staff()
            and attendance.is_open(course)
        )
        if hidden_for_attendance or not coursemember.is_instructor() and not (
            coursemember.hand_up or 
            coursemember.hand_up_list_2 or 
            coursemember.hand_up_list_3 or 
            coursemember.hand_up_list_4
        ):
            render_dict['count'] = 'Feature disabled.'
            render_dict['count_spoken'] = 'Feature disabled.'
            render_dict['count_total'] = [0, 0, 0, 0]
            render_dict['count_total_spoken'] = [0, 0, 0, 0]

            render_dict['list_of_unspoken'] = []
            render_dict['blue_list_of_unspoken'] = []
            render_dict['red_list_of_unspoken'] = []
            render_dict['yellow_list_of_unspoken'] = []
            render_dict['list_of_spoken'] = []
            render_dict['blue_list_of_spoken'] = []
            render_dict['red_list_of_spoken'] = []
            render_dict['yellow_list_of_spoken'] = []
            return JsonResponse(render_dict)

        all_students = CourseMember.objects.filter(
            course=course, role__in=LEARNER_ROLES, active=True
        ).select_related('user', 'user__profile')
        students_green  = all_students.filter(hand_up=True).order_by('time_spoken')
        students_blue   = all_students.filter(hand_up_list_2=True).order_by('time_spoken')
        students_red    = all_students.filter(hand_up_list_3=True).order_by('time_spoken')
        students_yellow = all_students.filter(hand_up_list_4=True).order_by('time_spoken')

        if open_session:
            spoken_participations = CourseParticipation.objects.filter(
                participant__course=course,
                session=open_session,
                spoke_upon_participation=True,
                count_in_calculations=True
            ).select_related(
                "participant__user", "participant__user__profile"
            ).order_by('-time_participated')
        else:
            spoken_participations = CourseParticipation.objects.none()

        green_spoken_ids = set(
            spoken_participations.filter(participation_list=1)
            .values_list('participant', flat=True)
        )
        blue_spoken_ids = set(
            spoken_participations.filter(participation_list=2)
            .values_list('participant', flat=True)
        )
        red_spoken_ids = set(
            spoken_participations.filter(participation_list=3)
            .values_list('participant', flat=True)
        )
        yellow_spoken_ids = set(
            spoken_participations.filter(participation_list=4)
            .values_list('participant', flat=True)
        )

        green_unspoken = [s for s in students_green  if s.id not in green_spoken_ids]
        green_spoken   = [s for s in students_green  if s.id in green_spoken_ids]

        blue_unspoken  = [s for s in students_blue   if s.id not in blue_spoken_ids]
        blue_spoken    = [s for s in students_blue   if s.id in blue_spoken_ids]

        red_unspoken   = [s for s in students_red    if s.id not in red_spoken_ids]
        red_spoken     = [s for s in students_red    if s.id in red_spoken_ids]

        yellow_unspoken = [s for s in students_yellow if s.id not in yellow_spoken_ids]
        yellow_spoken   = [s for s in students_yellow if s.id in yellow_spoken_ids]

        list_of_unspoken = [
            [*x.display_name_parts(), x.id] for x in green_unspoken
        ]
        list_of_spoken = [
            [*x.display_name_parts(), x.id] for x in green_spoken
        ]

        blue_list_of_unspoken = [
            [*x.display_name_parts(), x.id] for x in blue_unspoken
        ]
        blue_list_of_spoken = [
            [*x.display_name_parts(), x.id] for x in blue_spoken
        ]

        red_list_of_unspoken = [
            [*x.display_name_parts(), x.id] for x in red_unspoken
        ]
        red_list_of_spoken = [
            [*x.display_name_parts(), x.id] for x in red_spoken
        ]

        yellow_list_of_unspoken = [
            [*x.display_name_parts(), x.id] for x in yellow_unspoken
        ]
        yellow_list_of_spoken = [
            [*x.display_name_parts(), x.id] for x in yellow_spoken
        ]

        green_unsp_count = len(green_unspoken)
        green_sp_count   = len(green_spoken)

        blue_unsp_count  = len(blue_unspoken)
        blue_sp_count    = len(blue_spoken)

        red_unsp_count   = len(red_unspoken)
        red_sp_count     = len(red_spoken)

        yellow_unsp_count = len(yellow_unspoken)
        yellow_sp_count   = len(yellow_spoken)

        render_dict['count'] = (
            'Unspoken: Green: ' + str(green_unsp_count) +
            ', Blue: ' + str(blue_unsp_count) +
            ', Red: ' + str(red_unsp_count) +
            # These strings are assigned with textContent, so markup in them
            # is shown literally: the lecture screen displayed "Yellow: 0</br>"
            # to every instructor. A newline with white-space: pre-line does
            # what the tag was reaching for.
            ', Yellow: ' + str(yellow_unsp_count) + '\n' +
            'Spoken: Green: ' + str(green_sp_count) +
            ', Blue: ' + str(blue_sp_count) +
            ', Red: ' + str(red_sp_count) +
            ', Yellow: ' + str(yellow_sp_count)
        )
        render_dict['count_spoken'] = (
            'Green: ' + str(green_sp_count) +
            ', Blue: ' + str(blue_sp_count) +
            ', Red: ' + str(red_sp_count) +
            ', Yellow: ' + str(yellow_sp_count)
        )

        render_dict['count_total'] = [
            green_unsp_count + green_sp_count,
            blue_unsp_count + blue_sp_count,
            red_unsp_count + red_sp_count,
            yellow_unsp_count + yellow_sp_count
        ]
        render_dict['count_total_spoken'] = [
            green_sp_count,
            blue_sp_count,
            red_sp_count,
            yellow_sp_count
        ]

        render_dict['list_of_unspoken'] = list_of_unspoken
        render_dict['list_of_spoken']   = list_of_spoken

        render_dict['blue_list_of_unspoken'] = blue_list_of_unspoken
        render_dict['blue_list_of_spoken']   = blue_list_of_spoken

        render_dict['red_list_of_unspoken']  = red_list_of_unspoken
        render_dict['red_list_of_spoken']    = red_list_of_spoken

        render_dict['yellow_list_of_unspoken'] = yellow_list_of_unspoken
        render_dict['yellow_list_of_spoken']   = yellow_list_of_spoken

        return JsonResponse(render_dict)



    @staticmethod
    @login_required
    @chosen_course_required
    def count_already_spoken(request):
        render_dict = {}
        cid = request.session["course_id"]
        course = Course._default_manager.get(id=cid)
        list_of_already_spoken = []
        coursemember = CourseBase.get_course_member(request.user, course.id)
        if coursemember.role in LEARNER_ROLES and not (
            coursemember.hand_up or
            coursemember.hand_up_list_2 or
            coursemember.hand_up_list_3 or
            coursemember.hand_up_list_4
        ):
            return JsonResponse({
                "count_spoken": "Feature disabled.",
                "list_of_already_spoken": [],
                "color_totals": {"Green": 0, "Blue": 0, "Red": 0, "Yellow": 0}
            })
            
        else:
            open_session = active_session_for_course(
                course,
                manual_active=bool(course.manual_override and Lecture.currentLecture(course.pk)),
            )
            
            if open_session:
                spoken_participations = CourseParticipation.objects.filter(
                    participant__course=course,
                    session=open_session,
                    spoke_upon_participation=True,
                    count_in_calculations=True
                ).select_related(
                    "participant__user", "participant__user__profile"
                ).order_by("-time_participated")
            else:
                spoken_participations = CourseParticipation.objects.none()

            counts = {}  
            color_totals = {"Green": 0, "Blue": 0, "Red": 0, "Yellow": 0}
            for participation in spoken_participations:
                if participation.participation_list == 1:
                    color_label = "Green"
                elif participation.participation_list == 2:
                    color_label = "Blue"
                elif participation.participation_list == 3:
                    color_label = "Red"
                elif participation.participation_list == 4:
                    color_label = "Yellow"
                else:
                    color_label = "Unknown"

                color_totals[color_label] += 1
            for participation in spoken_participations:
                key = (participation.participant.id, participation.participation_list)
                counts[key] = counts.get(key, 0) + 1
                if participation.participation_list == 1:
                    color_label = "Green"
                elif participation.participation_list == 2:
                    color_label = "Blue"
                elif participation.participation_list == 3:
                    color_label = "Red"
                elif participation.participation_list == 4:
                    color_label = "Yellow"
                else:
                    color_label = "Unknown"
                list_of_already_spoken.append([
                    *participation.participant.display_name_parts(),
                    color_label,
                    str(counts[key])
                ])
            render_dict["list_of_already_spoken"] = list_of_already_spoken
            render_dict["color_totals"] = color_totals

        return JsonResponse(render_dict)



    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def undo(request):
        channel_layer = get_channel_layer()
        render_dict = {}
        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        coursemember = CourseBase.get_course_member(request.user, course.id)

        open_session = active_session_for_course(
            course,
            manual_active=bool(course.manual_override and Lecture.currentLecture(course.pk)),
        )

        if coursemember.is_instructor():
            if open_session:
                participations = CourseParticipation.objects.filter(
                    participant__course=course,
                    session=open_session,
                    count_in_calculations=True
                ).order_by('-time_participated')
                if participations.exists():
                    time_of_interest = participations.first().time_participated
                    participations_to_remove = CourseParticipation.objects.filter(
                        participant__course=course,
                        session=open_session,
                        time_participated=time_of_interest,
                        count_in_calculations=True
                    )
                    for p in participations_to_remove:
                        p.count_in_calculations = False
                        p.save()

                    send_message = request.GET.get('send_message') == "true"
                    if send_message:
                        async_to_sync(channel_layer.group_send)(
                            f'course_{course.id}',
                            {
                                'type': 'send_message',
                                'key': 'undo-last-call',
                                'send_auth_id': request.user.pk
                            }
                        )

        if open_session:
            spoken_participations = CourseParticipation.objects.filter(
                participant__course=course,
                session=open_session,
                spoke_upon_participation=True,
                count_in_calculations=True
            ).select_related(
                "participant__user", "participant__user__profile"
            ).order_by('-time_participated')
        else:
            spoken_participations = CourseParticipation.objects.none()

        already_spoken = []
        list_of_already_spoken = []
        green_already_spoken = []
        blue_already_spoken = []
        red_already_spoken = []
        yellow_already_spoken = []

        for participation in spoken_participations:
            if participation.participant not in already_spoken:
                already_spoken.append(participation.participant)
                color_str = (
                    'Green' if participation.participation_list == 1 else
                    'Blue' if participation.participation_list == 2 else
                    'Red' if participation.participation_list == 3 else 'Yellow'
                )
                list_of_already_spoken.append([
                    *participation.participant.display_name_parts(),
                    color_str,
                    '-'
                ])
            if participation.participation_list == 1 and participation.participant not in green_already_spoken:
                green_already_spoken.append(participation.participant)
            if participation.participation_list == 2 and participation.participant not in blue_already_spoken:
                blue_already_spoken.append(participation.participant)
            if participation.participation_list == 3 and participation.participant not in red_already_spoken:
                red_already_spoken.append(participation.participant)
            if participation.participation_list == 4 and participation.participant not in yellow_already_spoken:
                yellow_already_spoken.append(participation.participant)

        green_already_spoken_count = len(green_already_spoken)
        blue_already_spoken_count = len(blue_already_spoken)
        red_already_spoken_count = len(red_already_spoken)
        yellow_already_spoken_count = len(yellow_already_spoken)

        render_dict['count_spoken'] = (
            'Count: ' +
            'Green: ' + str(green_already_spoken_count) +
            ', Blue: ' + str(blue_already_spoken_count) +
            ', Red: ' + str(red_already_spoken_count) +
            ', Yellow: ' + str(yellow_already_spoken_count)
        )
        render_dict['list_of_already_spoken'] = list_of_already_spoken
        return JsonResponse(render_dict)


    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def reset_class_participation(request):
        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)
        course = Course._default_manager.get(id=cid)
        students = CourseMember.objects.filter(course = course, 
            role='student', active= True)
        with hand_events.resolving_as(HandRaise.Resolution.CLEARED):
            for student in students:
                student.spoken = False
                student.first_hand_up= True
                student.save()
        return HttpResponse('Every student was cleared for class participation.')

    @staticmethod
    @login_required
    @chosen_course_required
    @require_POST
    def reset_participation_points(request):
        cid = request.session["course_id"]
        CoursePermissions.require_instructor(request.user, cid)

        course = Course._default_manager.get(id=cid)
        students = CourseMember.objects.filter(course = course, 
            role='student', active= True)
        for student in students:
            student.participation_points = 0
            student.save()
        return HttpResponse('Every student was cleared for weekly participation points.')
