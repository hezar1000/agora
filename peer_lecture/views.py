from datetime import datetime, timedelta
import json

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.template import loader
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from peer_course.base import CourseBase
from peer_course.models import Course
from peer_course import hand_events
from peer_course.models import HandRaise
from peer_course.models import CourseParticipation
from peer_course.models import CourseMember, LEARNER_ROLES
from peer_home.wrappers import render
from peer_course.models import CourseSession
from peer_course.sessions import active_session_for_course, scheduled_session_to_adopt, session_bounds
from peer_course.base import CoursePermissions
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import pytz
from django.db import IntegrityError, transaction

from .models import Lecture, Message, Poll, PollResult, parse_answer_options

VALID_POLL_TYPES = {choice for choice, _label in Poll._meta.get_field("poll_type").choices}
MAX_POLL_OPTIONS = 50
MAX_OPTION_LENGTH = 500
MAX_TEXT_RESPONSE_CHARS = 5_000
MAX_MESSAGE_LENGTH = 2_000
MAX_POLL_IMPORT_BYTES = 2 * 1024 * 1024
MAX_POLL_IMPORT_COUNT = 500


def _selected_course(request, *, staff=False, instructor=False):
    """Resolve the selected course and enforce active membership for every live view."""
    course_id = request.session.get("course_id")
    if not course_id:
        raise PermissionDenied("Select a course before using this page.")
    course = get_object_or_404(Course, pk=course_id)
    course_member = CourseMember.objects.filter(
        course=course,
        user=request.user,
        active=True,
    ).first()
    if not request.user.is_superuser and course_member is None:
        raise PermissionDenied("You are not an active member of this course.")
    if instructor and not (
        request.user.is_superuser
        or (course_member is not None and course_member.is_instructor())
    ):
        raise PermissionDenied("This action is only available to course instructors.")
    if staff and not (
        request.user.is_superuser
        or (course_member is not None and course_member.role in {"instructor", "ta"})
    ):
        raise PermissionDenied("This action is only available to course staff.")
    return course, course_member


def _course_timezone(course):
    try:
        return pytz.timezone(course.course_timezone)
    except (pytz.UnknownTimeZoneError, AttributeError, TypeError):
        return pytz.UTC


def _poll_for_course(course_id, poll_id, **filters):
    return get_object_or_404(Poll, pk=poll_id, course_id=course_id, **filters)


def _student_may_view_poll(poll, *, allow_active=False):
    """Past results follow the owning template's visibility; active polls stay usable."""
    if poll.end_time is None and poll.start_time is not None:
        return bool(
            allow_active
            and poll.lecture_id
            and poll.lecture.end_time is None
        )
    template = poll.duplicate_of or poll
    return bool(poll.end_time and template.is_visible_to_students and not template.archived)


def _broadcast(course_id, sender_user_id, key, value=None):
    event = {
        "type": "send_message",
        "key": key,
        "send_auth_id": sender_user_id,
    }
    if value is not None:
        event["value"] = value
    async_to_sync(get_channel_layer().group_send)(f"course_{course_id}", event)


def _set_lecture_running(course, auth_id, start):
    """Start (``start=True``) or end the course's lecture.

    The request says which one the instructor meant. A page that missed a
    broadcast can show "Start Lecture" while a lecture is running; when the
    button toggled, pressing it ended that lecture. A request that matches
    the current state now changes nothing and reports the actual state.
    Rapid repeat requests are serialized on the course row.
    """
    with transaction.atomic():
        course = Course.objects.select_for_update().get(pk=course.pk)
        lecture = Lecture.objects.select_for_update().filter(
            course=course,
            end_time__isnull=True,
        ).order_by("lecture_id").first()
        now_local = timezone.localtime(timezone.now(), _course_timezone(course))

        if start == (lecture is not None):
            return JsonResponse({
                "begin": lecture is not None,
                "lecture_id": lecture.pk if lecture is not None else None,
            })

        if lecture is not None:
            course.manual_override = False
            course.manually_started = False
            course.save(update_fields=["manual_override", "manually_started"])
            lecture.end_time = now_local
            lecture.save(update_fields=["end_time"])
            Poll.objects.filter(
                lecture=lecture,
                end_time__isnull=True,
            ).update(end_time=now_local)

            if lecture.session_id is not None:
                open_session = course.sessions.select_for_update().filter(
                    pk=lecture.session_id, ended=False,
                ).first()
            else:
                open_session = active_session_for_course(
                    course,
                    at=now_local,
                    for_update=True,
                    manual_active=True,
                )
            if open_session is not None:
                open_session.ended = True
                open_session.actual_end_time = now_local.time().replace(tzinfo=None)
                open_session.save(update_fields=["ended", "actual_end_time"])
            began = False
            event_key = "end-lecture"
        else:
            lecture = Lecture.objects.create(course=course)
            course.manual_override = True
            course.manually_started = True
            course.save(update_fields=["manual_override", "manually_started"])

            scheduled = scheduled_session_to_adopt(course, now_local)
            if scheduled is not None:
                if scheduled.ended:
                    # Stopped and started again before its scheduled end:
                    # the same lecture continues in the same session.
                    scheduled.ended = False
                    scheduled.actual_end_time = None
                    scheduled.save(update_fields=["ended", "actual_end_time"])
                lecture.session = scheduled
                lecture.scheduled_end_time = session_bounds(scheduled)[1]
            else:
                scheduled_end = now_local + course.default_session_duration
                lecture.session = CourseSession.objects.create(
                    course=course,
                    session_date=now_local.date(),
                    start_time_pst=now_local.time().replace(tzinfo=None),
                    end_time_pst=scheduled_end.time().replace(tzinfo=None),
                )
            lecture.save(update_fields=["session", "scheduled_end_time"])
            began = True
            event_key = "begin-lecture"

    _broadcast(course.pk, auth_id, event_key)
    return JsonResponse({"begin": began, "lecture_id": lecture.pk})


def _start_poll_for_course(
    course,
    lecture_id,
    saved_id,
    title,
    poll_data,
    answer,
    options,
    dont_save_answer,
    poll_type,
    enforce_one_answer,
    min_word_count,
    max_word_count,
):
    """Create exactly one running instance, serializing double-clicks per course."""
    with transaction.atomic():
        course = Course.objects.select_for_update().get(pk=course.pk)
        lecture = get_object_or_404(
            Lecture.objects.select_for_update(),
            pk=lecture_id,
            course=course,
            end_time__isnull=True,
        )
        if Poll.objects.filter(
            lecture=lecture,
            start_time__isnull=False,
            end_time__isnull=True,
        ).exists():
            raise IntegrityError("A poll is already running for this lecture.")

        if saved_id:
            source_poll = get_object_or_404(
                Poll,
                pk=saved_id,
                course=course,
                duplicate_of__isnull=True,
                archived=False,
            )
        else:
            source_poll = Poll.objects.create(
                course=course,
                title=title,
                answer=answer,
                answer_options=json.dumps(options),
                dont_save_answer=dont_save_answer,
                poll_type=poll_type,
                enforce_one_answer=enforce_one_answer,
                min_word_count=min_word_count,
                max_word_count=max_word_count,
            )
            Poll.savePollText(source_poll.pk, poll_data)
            source_poll.refresh_from_db()

        running_poll = Poll.objects.create(
            course=course,
            lecture=lecture,
            duplicate_of=source_poll,
            title=source_poll.title,
            answer=source_poll.answer,
            answer_options=json.dumps(parse_answer_options(source_poll.answer_options)),
            dont_save_answer=source_poll.dont_save_answer,
            poll_type=source_poll.poll_type,
            enforce_one_answer=source_poll.enforce_one_answer,
            min_word_count=source_poll.min_word_count,
            max_word_count=source_poll.max_word_count,
            poll_data=source_poll.safe_poll_data or None,
            start_time=timezone.now(),
        )
    return lecture, running_poll



def get_poll_input(request):
    saved_poll_id = request.POST.get('saved-poll-id')
    poll_title = (request.POST.get('poll-title') or "Untitled Poll").strip()[:200]
    poll_data = request.POST.get('poll-data')
    poll_type = request.POST.get('pollMode', 'iclicker') # Get the specific mode
    if poll_type not in VALID_POLL_TYPES:
        raise ValueError("Unsupported poll type.")
    poll_enforcement = request.POST.get('enforce-one-answer') == 'on' 
    no_answers = request.POST.get('options-anonymity','false') == 'true'
    
    # Get min/max word counts for text polls
    min_word_count = 1
    max_word_count = 50
    if poll_type == 'text':
        try:
            min_word_count = max(1, int(request.POST.get('text-min-words', 1)))
        except (ValueError, TypeError):
            min_word_count = 1
        try:
            max_word_count = min(1000, max(1, int(request.POST.get('text-max-words', 50))))
        except (ValueError, TypeError):
            max_word_count = 50
        # Ensure max >= min
        if max_word_count < min_word_count:
            max_word_count = min_word_count
    
    if poll_type == 'list':
        answer = request.POST.get('target-list')
        answer_options = request.POST.getlist('list-option')
    elif poll_type == 'text':
        # Text polls don't have predefined answer options or correct answers
        answer = []
        answer_options = []
    else:
        answer = request.POST.getlist('answer')
        answer_options = [opt for opt in request.POST.getlist('answer-option') if opt != 'None']

    answer_options = [str(option).strip()[:MAX_OPTION_LENGTH] for option in answer_options]
    answer_options = [option for option in answer_options if option]
    if len(answer_options) > MAX_POLL_OPTIONS:
        raise ValueError(f"A poll may have at most {MAX_POLL_OPTIONS} options.")
    if poll_type == "list" and (
        len(answer_options) != 4 or len(set(answer_options)) != 4
    ):
        raise ValueError("Participation-list polls require four distinct list names.")

    return saved_poll_id, poll_title, poll_data, answer, answer_options, no_answers, poll_type, poll_enforcement, min_word_count, max_word_count

def get_course_and_user_data(request):
        course, coursemember = _selected_course(request)
        return int(request.user.pk), int(course.pk), coursemember

class PollViews:
    @staticmethod
    @login_required
    @require_GET
    def get_all_instances(request, cid, saved_poll_id):
        course, _course_member = _selected_course(request, staff=True)
        if course.pk != cid:
            raise PermissionDenied("The requested course is not selected.")
        saved_poll = _poll_for_course(
            course.pk,
            saved_poll_id,
            duplicate_of__isnull=True,
        )
        instances = Poll.objects.filter(
            duplicate_of=saved_poll,
            course=course,
            end_time__isnull=False
        ).order_by('-start_time')
        results_list = []
        course_tz = _course_timezone(course)
        for instance in instances:
            if instance.start_time is None:
                continue
            local_start_time = timezone.localtime(instance.start_time, course_tz)
            results_list.append({
                "id": instance.pk,
                "date": local_start_time.strftime("%b %d, %Y, %I:%M %p") 
            })
        return JsonResponse({"instances": results_list})
    
    @staticmethod
    @login_required
    @require_GET
    def get_all_instances_student(request, cid, saved_poll_id):
        course, _course_member = _selected_course(request)
        if course.pk != cid:
            raise PermissionDenied("The requested course is not selected.")
        saved_poll = _poll_for_course(
            course.pk,
            saved_poll_id,
            duplicate_of__isnull=True,
            archived=False,
            is_visible_to_students=True,
        )

        instances = Poll.objects.filter(
            duplicate_of=saved_poll,
            course=course,
            end_time__isnull=False
        ).order_by('-start_time')

        results_list = []
        course_tz = _course_timezone(course)
        for instance in instances:
            if instance.start_time is None:
                continue
            local_start_time = timezone.localtime(instance.start_time, course_tz)
            results_list.append({
                "id": instance.pk,
                "date": local_start_time.strftime("%b %d, %Y, %I:%M %p")
            })
        return JsonResponse({"instances": results_list})
    

    @staticmethod
    @login_required
    @require_GET
    def get_my_answer_json(request, poll_id: int):
        """
        Returns the current user's participation status and recorded answer(s) for a given poll instance.
        Only accessible to enrolled members (student/TA/instructor) of the course that owns the poll.
        """
        course, course_member = _selected_course(request)
        poll = _poll_for_course(course.pk, poll_id)
        is_staff = request.user.is_superuser or (
            course_member is not None and course_member.role in {"instructor", "ta"}
        )
        if not is_staff and not _student_may_view_poll(poll, allow_active=True):
            raise PermissionDenied("These poll results are not visible to students.")

        pr = PollResult.objects.filter(poll_id=poll_id, auth_user_id=request.user.id).first()

        participated = pr is not None
        answer_payload = None

        # If we're configured to save only participation, don't reveal the stored answer
        if participated and not poll.dont_save_answer:
            # pr.answer is stored as a list (e.g., ["A"]) or possibly string in old data
            ans = pr.answer
            if ans is None:
                answer_payload = []
            elif isinstance(ans, (list, tuple)):
                answer_payload = list(ans)
            else:
                # coerce single string → list
                answer_payload = [str(ans)]

        return JsonResponse({
            "participated": participated,
            "answer": answer_payload,   # list or None
            "dont_save_answer": poll.dont_save_answer,
        })
    
    @staticmethod
    @login_required
    @require_GET
    def download_all_polls_json(request):
        course, _course_member = _selected_course(request, instructor=True)
        polls = Poll.objects.filter(
            course=course,
            duplicate_of__isnull=True,
            archived=False,
        )
        polls_list = []
        for poll in polls:
            polls_list.append({
                "id": poll.pk,
                "title": poll.title,
                "answer": poll.answer,
                "dont_save_answer": poll.dont_save_answer,
                "poll_data": poll.poll_data,
                "answer_options": poll.answer_options,
                "poll_type": poll.poll_type,
                "is_visible_to_students": poll.is_visible_to_students,
                "enforce_one_answer": poll.enforce_one_answer,
            })
        return JsonResponse({"polls": polls_list}, safe=False)
    
    @staticmethod
    @login_required
    @require_POST
    def upload_all_polls_json(request):
        """
        SIMPLE UPLOAD:
        Uploads a JSON file containing polls.
        This logic just ADDS all polls from the file.
        It does NOT delete or check for duplicates.
        """
        course, _course_member = _selected_course(request, instructor=True)
        try:
            json_file = request.FILES.get('polls_file')
            if not json_file:
                return JsonResponse({"status": "error", "error": "No file provided"}, status=400)
            if json_file.size > MAX_POLL_IMPORT_BYTES:
                return JsonResponse(
                    {"status": "error", "error": "Poll file is too large."},
                    status=400,
                )

            data = json.load(json_file)
            polls_list = data.get('polls')

            if not isinstance(polls_list, list):
                 return JsonResponse({"status": "error", "error": "Invalid JSON format: 'polls' key not found or is not a list"}, status=400)
            if len(polls_list) > MAX_POLL_IMPORT_COUNT:
                return JsonResponse(
                    {"status": "error", "error": "Too many polls in one upload."},
                    status=400,
                )

            created_count = 0

            with transaction.atomic():
                for poll_data in polls_list:
                    if not isinstance(poll_data, dict):
                        raise ValueError("Every poll must be a JSON object.")
                    poll_title = str(poll_data.get('title') or 'Untitled Poll').strip()[:200]
                    answer = poll_data.get('answer', [])
                    if not isinstance(answer, list):
                        answer = [str(answer)] if answer not in (None, "") else []
                    no_answers = poll_data.get('dont_save_answer') is True
                    poll_type = poll_data.get('poll_type', 'iclicker')
                    if poll_type not in VALID_POLL_TYPES:
                        raise ValueError(f"Unsupported poll type: {poll_type}")
                    poll_enforcement = poll_data.get('enforce_one_answer') is True
                    poll_text = poll_data.get('poll_data')
                    answer_opts_str = poll_data.get('answer_options')
                    is_visible = poll_data.get('is_visible_to_students') is True

                    answer_options_list = parse_answer_options(answer_opts_str)
                    answer_options_list = [
                        option.strip()[:MAX_OPTION_LENGTH]
                        for option in answer_options_list[:MAX_POLL_OPTIONS]
                        if option.strip()
                    ]
                    if poll_type == "list" and (
                        len(answer_options_list) != 4
                        or len(set(answer_options_list)) != 4
                    ):
                        raise ValueError(
                            "Participation-list polls require four distinct list names."
                        )

                    new_poll = Poll(
                        title=poll_title,
                        course=course,
                        answer=answer,
                        dont_save_answer=no_answers,
                        poll_type=poll_type,
                        enforce_one_answer=poll_enforcement,
                        is_visible_to_students=is_visible
                    )
                    
                    new_poll.save()

                    Poll.saveAnswerOptions(new_poll.pk, answer_options_list)
                    if poll_text:
                        Poll.savePollText(new_poll.pk, poll_text)
                    
                    created_count += 1
            
            return JsonResponse({
                "status": "success", 
                "message": f"Upload complete. Created {created_count} new polls."
            })

        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"status": "error", "error": "Invalid JSON file"}, status=400)
        except (TypeError, ValueError) as exc:
            return JsonResponse({"status": "error", "error": str(exc)}, status=400)
        except Exception:
            return JsonResponse(
                {"status": "error", "error": "The poll file could not be imported."},
                status=500,
            )
        

    @staticmethod
    @login_required
    @require_GET
    def get_poll_results_json(request, poll_id):
        # For API calls, determine if user is instructor/student
        # If student, only show their own text responses
        course, course_member = _selected_course(request)
        poll = _poll_for_course(course.pk, poll_id)
        course_id = course.pk
        # If user is instructor/TA, show all responses (pass None)
        # If user is student, only show their own (pass request.user)
        if request.user.is_superuser or (
            course_member is not None and course_member.role in {"instructor", "ta"}
        ):
            user = None  # Show all responses
        else:
            if not _student_may_view_poll(poll):
                raise PermissionDenied("These poll results are not visible to students.")
            user = request.user  # Show only student's own text response
        results_dict = Poll.getResults(poll_id, user=user, course_timezone=course.course_timezone)
        return JsonResponse(results_dict, safe=False)

    @staticmethod
    @login_required
    @require_GET
    def get_poll_results_with_count_json(request, poll_id):
        """
        THIS IS THE NEW FUNCTION.
        It returns the combined data structure with the participant count.
        """
        # For API calls, determine if user is instructor/student
        # If student, only show their own text responses
        course, course_member = _selected_course(request)
        poll = _poll_for_course(course.pk, poll_id)
        course_id = course.pk
        # If user is instructor/TA, show all responses (pass None)
        # If user is student, only show their own (pass request.user)
        if request.user.is_superuser or (
            course_member is not None and course_member.role in {"instructor", "ta"}
        ):
            user = None
        else:
            if not _student_may_view_poll(poll):
                raise PermissionDenied("These poll results are not visible to students.")
            user = request.user
        results_dict = Poll.getResults(poll_id, user=user, course_timezone=course.course_timezone)
        participant_count = PollResult.objects.filter(poll=poll).count()
        
        response_data = {
            "counts": results_dict,
            "participant_count": participant_count
        }
        
        return JsonResponse(response_data, safe=False)
    @staticmethod
    @login_required
    def instructor(request):
        course, course_member = _selected_course(request, staff=True)
        auth_id = request.user.pk
        course_id = course.pk
        
        data = {
            "auth_id": auth_id,
            "course_id": course_id,
            "course_member": course_member,
            "MEDIA_URL": settings.MEDIA_URL,
        }

        lecture = Lecture.objects.filter(course_id=course_id, end_time__isnull=True).first()

        if lecture:
            data["lecture"] = lecture
            poll = Poll.objects.filter(lecture_id=lecture.pk, end_time__isnull=True).first()

            if poll: data["poll"] = poll
                    
        if request.method == 'POST' and 'toggle-lecture' in request.POST:
            action = request.POST['toggle-lecture']
            if action not in ('start', 'end'):
                return HttpResponseBadRequest(
                    "toggle-lecture must be 'start' or 'end'; reload the page."
                )
            return _set_lecture_running(course, auth_id, start=action == 'start')

        if request.method == 'POST':
            if 'enable-polling' in request.POST:
                polls = Poll.getSavedPolls(course_id)
                if len(polls) > 0:
                    data['saved_polls'] = polls
                    data['view_saved_polls'] = True
                else:
                    data['create_poll'] = True

                return render(request, 'instructor-poll.html', data)
            
            elif 'move-hands' in request.POST:
                try:
                    poll_id = int(request.POST['move-hands'])
                except (KeyError, TypeError, ValueError):
                    return JsonResponse(
                        {"status": "error", "message": "Invalid poll."},
                        status=400,
                    )

                with transaction.atomic():
                    locked_course = Course.objects.select_for_update().get(pk=course_id)
                    poll = get_object_or_404(
                        Poll.objects.select_for_update(),
                        pk=poll_id,
                        course=locked_course,
                    )
                    if poll.poll_type != "list":
                        return JsonResponse(
                            {
                                "status": "error",
                                "message": "Only participation-list poll results can move hands.",
                            },
                            status=400,
                        )
                    if poll.end_time is None:
                        return JsonResponse(
                            {"status": "error", "message": "Stop the poll before moving hands."},
                            status=409,
                        )
                    options = parse_answer_options(poll.answer_options)
                    if len(options) != 4 or len(set(options)) != 4:
                        return JsonResponse(
                            {
                                "status": "error",
                                "message": "This poll does not contain four distinct participation lists.",
                            },
                            status=400,
                        )
                    if poll.hands_moved_at is not None:
                        return JsonResponse(
                            {
                                "status": "success",
                                "message": "Hands were already moved for this poll.",
                                "already_moved": True,
                            }
                        )

                    open_session = active_session_for_course(
                        locked_course,
                        for_update=True,
                        manual_active=bool(
                            locked_course.manual_override
                            and Lecture.currentLecture(locked_course.pk)
                        ),
                    )
                    if open_session is None:
                        return JsonResponse(
                            {"status": "error", "message": "No active session."},
                            status=409,
                        )

                    try:
                        config = json.loads(locked_course.color_config or "{}")
                        if not isinstance(config, dict):
                            config = {}
                    except (TypeError, json.JSONDecodeError):
                        config = {}
                    for color in ("green", "blue", "red", "yellow"):
                        config[f"hidden_{color}"] = False
                    locked_course.color_config = json.dumps(config)
                    locked_course.save(update_fields=["color_config"])

                    colors = ("green", "blue", "red", "yellow")
                    color_map = dict(zip(options, colors))
                    hand_fields = {
                        "green": "hand_up",
                        "blue": "hand_up_list_2",
                        "red": "hand_up_list_3",
                        "yellow": "hand_up_list_4",
                    }
                    list_ids = {"green": 1, "blue": 2, "red": 3, "yellow": 4}
                    moved_count = 0
                    for result in PollResult.objects.filter(poll=poll).select_related("auth_user"):
                        choice = result.answer[0] if isinstance(result.answer, list) and result.answer else None
                        color = color_map.get(choice)
                        if color is None:
                            continue
                        member = CourseMember.objects.select_for_update().filter(
                            course=locked_course,
                            user=result.auth_user,
                            active=True,
                        ).first()
                        if member is None:
                            continue
                        # A poll answer moves a hand between lists, which is
                        # neither the student lowering it nor the instructor
                        # clearing it.
                        with hand_events.resolving_as(HandRaise.Resolution.MOVED):
                            for field in hand_fields.values():
                                setattr(member, field, False)
                            setattr(member, hand_fields[color], True)
                            member.save(update_fields=list(hand_fields.values()))
                        CourseParticipation.objects.create(
                            participant=member,
                            session=open_session,
                            time_participated=timezone.now(),
                            participation_list=list_ids[color],
                        )
                        moved_count += 1

                    poll.hands_moved_at = timezone.now()
                    poll.save(update_fields=["hands_moved_at"])

                for color in ('green','blue','red','yellow'):
                    _broadcast(course_id, auth_id, "enable-hand", color)
                _broadcast(course_id, auth_id, "update-hand-status")
                return JsonResponse({
                    "status": "success",
                    "message": "Hands moved into lists.",
                    "moved_count": moved_count,
                })


            elif 'save-poll' in request.POST:
                try:
                    saved_poll_id, poll_title, poll_data, answer, answer_options, no_answers, poll_type, poll_enforcement, min_word_count, max_word_count = get_poll_input(request)
                except ValueError as exc:
                    return JsonResponse({"error": str(exc)}, status=400)

                if not saved_poll_id:
                    poll = Poll.create(answer, no_answers, poll_title, course_id, poll_type=poll_type, enforce_one_answer=poll_enforcement, min_word_count=min_word_count, max_word_count=max_word_count)
                    poll = Poll.saveAnswerOptions(poll.pk, answer_options)
                    poll = Poll.savePollText(poll.pk, poll_data)
                else:
                    poll = _poll_for_course(
                        course_id,
                        saved_poll_id,
                        duplicate_of__isnull=True,
                        archived=False,
                    )
                    poll.title = poll_title
                    poll.answer = answer
                    poll.dont_save_answer = no_answers
                    poll.poll_type   = poll_type
                    poll.enforce_one_answer = poll_enforcement
                    if poll_type == 'text':
                        poll.min_word_count = min_word_count
                        poll.max_word_count = max_word_count
                    poll.save()
                    poll = Poll.saveAnswerOptions(poll.pk, answer_options)
                    poll = Poll.savePollText(poll.pk, poll_data)

                return render(request, 'instructor-poll.html', data)

            elif 'view-saved-polls' in request.POST:
                data['saved_polls'] = Poll.getSavedPolls(course_id)
                data['view_saved_polls'] = True
                return render(request, 'instructor-poll.html', data)
            
            elif 'toggle-visibility' in request.POST:
                saved_poll_id = request.POST.get('saved-poll-id')
                poll = _poll_for_course(
                    course_id,
                    saved_poll_id,
                    duplicate_of__isnull=True,
                    archived=False,
                )
                poll.is_visible_to_students = not poll.is_visible_to_students
                poll.save(update_fields=['is_visible_to_students'])
                data['saved_polls'] = Poll.getSavedPolls(course_id)
                data['view_saved_polls'] = True
                return render(request, 'instructor-poll.html', data)
            
            elif 'view-create-poll' in request.POST:
                data['create_poll'] = True
                return render(request, 'instructor-poll.html', data)
            
            elif 'start-poll' in request.POST:
                try:
                    saved_id, title, poll_data, answer, options, no_answers, p_type, poll_enforcement, min_word_count, max_word_count = get_poll_input(request)
                    lecture_id = int(request.POST.get('id'))
                except (TypeError, ValueError) as exc:
                    return JsonResponse({"error": str(exc) or "Invalid lecture."}, status=400)
                try:
                    active_lecture, running_poll = _start_poll_for_course(
                        course,
                        lecture_id,
                        saved_id,
                        title,
                        poll_data,
                        answer,
                        options,
                        no_answers,
                        p_type,
                        poll_enforcement,
                        min_word_count,
                        max_word_count,
                    )
                except IntegrityError:
                    return JsonResponse(
                        {"error": "A poll is already running for this lecture."},
                        status=409,
                    )

                _broadcast(course_id, auth_id, "start-poll", running_poll.pk)

                data.update({
                    "lecture":        active_lecture,
                    "poll":           running_poll,
                    "answer_options": parse_answer_options(running_poll.answer_options),
                    "poll_data":      running_poll.safe_poll_data,
                    "poll_type":      running_poll.poll_type,
                    "enforce_one_answer": running_poll.enforce_one_answer,
                })

                return render(request, 'instructor-poll.html', data)

            elif 'delete-poll' in request.POST:
                saved_poll_id = request.POST.get('saved-poll-id')
                poll = _poll_for_course(
                    course_id,
                    saved_poll_id,
                    duplicate_of__isnull=True,
                    archived=False,
                )
                poll.archived = True
                poll.save(update_fields=["archived"])
                data['saved_polls'] = Poll.getSavedPolls(course_id)
                data['view_saved_polls'] = True
                return render(request, 'instructor-poll.html', data)
            
            elif 'cancel' in request.POST:
                data['create_poll'] = False
                data['view_saved_polls'] = False
                data['edit_poll'] = None
                data['poll'] = None
                data['results'] = None
                return render(request, 'instructor-poll.html', data)
            
            elif 'edit-poll' in request.POST:
                saved_poll_id = request.POST.get('saved-poll-id')
                poll = _poll_for_course(
                    course_id,
                    saved_poll_id,
                    duplicate_of__isnull=True,
                    archived=False,
                )
                data['edit_poll'] = {
                    "poll_id": poll.pk,
                    "poll_data": poll.safe_poll_data,
                    "title": poll.title,
                    "answer_options": parse_answer_options(poll.answer_options),
                    "answer": poll.answer,
                    "dont_save_answer": poll.dont_save_answer,
                    "poll_type":        poll.poll_type,
                    "enforce_one_answer": poll.enforce_one_answer,
                    "min_word_count": poll.min_word_count if poll.poll_type == 'text' else 1,
                    "max_word_count": poll.max_word_count if poll.poll_type == 'text' else 50,
                }
                data['create_poll'] = True
                
                return render(request, 'instructor-poll.html', data)
            
            elif 'clone-poll' in request.POST:
                saved_poll_id = request.POST.get('saved-poll-id')
                poll = _poll_for_course(
                    course_id,
                    saved_poll_id,
                    duplicate_of__isnull=True,
                    archived=False,
                )

                newPoll = Poll.create(poll.answer, poll.dont_save_answer, poll.title + " (copy)", course_id, poll_type=poll.poll_type, enforce_one_answer=poll.enforce_one_answer, min_word_count=poll.min_word_count, max_word_count=poll.max_word_count)
                newPoll = Poll.saveAnswerOptions(newPoll.pk, parse_answer_options(poll.answer_options))
                newPoll = Poll.savePollText(newPoll.pk, poll.poll_data)

                data['edit_poll'] = {
                    "poll_id": newPoll.pk,
                    "poll_data": newPoll.safe_poll_data,
                    "title": newPoll.title,
                    "answer_options": parse_answer_options(newPoll.answer_options),
                    "answer": newPoll.answer,
                    "poll_type": newPoll.poll_type,
                    "dont_save_answer": newPoll.dont_save_answer,
                    "enforce_one_answer": newPoll.enforce_one_answer,
                    "min_word_count": newPoll.min_word_count if newPoll.poll_type == 'text' else 1,
                    "max_word_count": newPoll.max_word_count if newPoll.poll_type == 'text' else 50,
                }
                
                data['create_poll'] = True
                
                return render(request, 'instructor-poll.html', data)

            elif 'stop-poll' in request.POST:
                lecture = Lecture.objects.filter(
                    course_id=course_id,
                    end_time__isnull=True,
                ).first()

                if data.get("poll"):
                    poll = data["poll"]
                    _broadcast(course_id, auth_id, "stop-poll", poll.pk)
                    poll = Poll.endPoll(poll.pk, timezone.now())

                    results = Poll.getResults(poll.pk, user=None, course_timezone=course.course_timezone)
                    data["results"] = results
                    data["poll_type"] = poll.poll_type
                    data["poll"] = None

                return render(request, 'instructor-poll.html', data)
        
        elif 'stop-poll' in request.GET:
            last_poll = Poll.lastPoll(lecture.pk) if lecture else None
            if last_poll:
                results = Poll.getResults(last_poll.pk, user=None, course_timezone=course.course_timezone)
                data["results"] = results
                data["poll_type"] = last_poll.poll_type

            return render(request, 'instructor-poll.html', data)

        elif 'update-results' in request.GET:
            poll_id = request.GET.get('update-results')
            poll = _poll_for_course(course_id, poll_id)
            
            # Instructor view - show all responses (pass None)
            results_dict = Poll.getResults(poll_id, user=None, course_timezone=course.course_timezone)
            
            # New logic to count unique participants
            participant_count = PollResult.objects.filter(poll=poll).count()
            
            # Combine them into a single response
            response_data = {
                "counts": results_dict,
                "participant_count": participant_count
            }
            
            return JsonResponse(response_data, safe=False)

        else:
            return render(request, 'instructor-poll.html', data)
        
    
    
    @staticmethod
    @login_required
    def student(request):
        course, course_member = _selected_course(request)
        auth_id = request.user.pk
        course_id = course.pk

        # Treat calls from Agora (which use ?start-poll=true / ?stop-poll=true) as "embedded"
        embedded = any(key in request.GET for key in ("start-poll", "stop-poll", "embed"))

        data = {
            "auth_id": auth_id,
            "course_id": course_id,
            "course_member": course_member,
            "MEDIA_URL": settings.MEDIA_URL,
            "is_student": True,
            "embedded": embedded,
            # Only show the live poll UI when explicitly launched by Agora with ?start-poll=true
            "show_live_poll": ("start-poll" in request.GET),
        }

        lecture = Lecture.objects.filter(course_id=course_id, end_time__isnull=True).first()
        if lecture:
            data["lecture"] = lecture
            poll = Poll.objects.filter(
                lecture=lecture,
                course=course,
                start_time__isnull=False,
                end_time__isnull=True,
            ).first()
            if poll:
                data["poll"] = poll

        if request.method == 'POST':
            auth_user_id = request.user.pk
            if 'poll_id' in request.POST and 'view-saved-polls' not in request.POST:
                try:
                    poll_id = int(request.POST.get('poll_id'))
                except (TypeError, ValueError):
                    return JsonResponse({'error': 'Invalid poll.'}, status=400)
                if lecture is None:
                    return JsonResponse({'error': 'There is no active lecture.'}, status=409)
                poll = _poll_for_course(
                    course_id,
                    poll_id,
                    lecture=lecture,
                    start_time__isnull=False,
                    end_time__isnull=True,
                )
                
                # Handle text polls with word count validation
                if poll.poll_type == 'text':
                    text_answer = request.POST.get('poll-answer', '').strip()
                    if len(text_answer) > MAX_TEXT_RESPONSE_CHARS:
                        return JsonResponse({
                            'error': f'Your response exceeds {MAX_TEXT_RESPONSE_CHARS} characters.'
                        }, status=400)
                    
                    # Validate word count using poll's min/max
                    if text_answer:
                        words = text_answer.split()
                        word_count = len([w for w in words if w.strip()])
                        min_words = poll.min_word_count if hasattr(poll, 'min_word_count') else 1
                        max_words = poll.max_word_count if hasattr(poll, 'max_word_count') else 50
                        
                        if word_count < min_words:
                            return JsonResponse({
                                'error': f'Your response must be at least {min_words} word{"s" if min_words > 1 else ""}. Please add more content.',
                                'word_count': word_count
                            }, status=400)
                        
                        if word_count > max_words:
                            return JsonResponse({
                                'error': f'Your response exceeds {max_words} words. Please shorten it.',
                                'word_count': word_count
                            }, status=400)
                        
                        # Store as single-item list
                        answer = [text_answer]
                    else:
                        answer = []
                else:
                    answer = list(dict.fromkeys(request.POST.getlist('poll-answer')))
                    valid_options = set(parse_answer_options(poll.answer_options))
                    if any(option not in valid_options for option in answer):
                        return JsonResponse({'error': 'Invalid poll answer.'}, status=400)
                    if (poll.enforce_one_answer or poll.poll_type == 'list') and len(answer) > 1:
                        return JsonResponse(
                            {'error': 'This poll accepts only one answer.'},
                            status=400,
                        )
                
                # If the answer list is empty, the student has withdrawn their vote.
                if not answer:
                    PollResult.objects.filter(
                        poll=poll,
                        auth_user=request.user,
                    ).delete()
                else:
                    stored_answer = [] if poll.dont_save_answer else answer
                    try:
                        PollResult.objects.update_or_create(
                            poll=poll,
                            auth_user=request.user,
                            defaults={"answer": stored_answer, "time": timezone.now()},
                        )
                    except IntegrityError:
                        PollResult.objects.filter(
                            poll=poll,
                            auth_user=request.user,
                        ).update(answer=stored_answer, time=timezone.now())

                # Broadcast that the poll results have been updated
                _broadcast(course_id, auth_id, "poll-updated", poll_id)
                return render(request, 'student-poll.html', data)
            
            
            
            
            elif 'view-saved-polls' in request.POST:
                data['saved_polls'] = Poll.getSavedPolls(course_id, is_student=True)
                data['view_saved_polls'] = True
                return render(request, 'student-poll.html', data)

        else:
            if 'stop-poll' in request.GET and lecture:
                last_poll = Poll.lastPoll(lecture.pk)
                if last_poll:
                    # Get course timezone
                    # Student view - only show their own text response (pass request.user)
                    data["results"] = Poll.getResults(last_poll.pk, user=request.user, course_timezone=course.course_timezone)
                    data["poll_type"] = last_poll.poll_type

        return render(request, 'student-poll.html', data)
        
class MessageViews:
    @staticmethod
    @login_required
    def student(request):
        course, course_member = _selected_course(request)
        auth_id = request.user.pk
        course_id = course.pk
        
        data = {
            "auth_id": auth_id,
            "course_id": course_id,
            "course_member": course_member,
            "MEDIA_URL": settings.MEDIA_URL,
        }

        lecture = Lecture.objects.filter(course_id=course_id, end_time__isnull=True).first()

        if lecture:
            data["lecture"] = lecture
            
            messages = Message.getMessages(lecture.pk, True)
            data["messages"] = messages

            blocked = lecture.blocked_students.filter(pk=auth_id).first()
            if blocked:
                data["blocked"] = True
        
        if request.method == 'POST':
            if 'message' in request.POST:
                message_text = (request.POST.get('message') or '').strip()
                if not message_text:
                    return JsonResponse({'success': False, 'error': 'Message is empty.'}, status=400)
                if len(message_text) > MAX_MESSAGE_LENGTH:
                    return JsonResponse(
                        {'success': False, 'error': 'Message is too long.'},
                        status=400,
                    )
                if lecture is None:
                    return JsonResponse(
                        {'success': False, 'error': 'Lecture not found.'},
                        status=409,
                    )
                if not lecture.enable_messages:
                    return JsonResponse(
                        {'success': False, 'error': 'The message board is disabled.'},
                        status=403,
                    )
                if lecture.blocked_students.filter(pk=auth_id).exists():
                    return JsonResponse(
                        {'success': False, 'error': 'You are blocked from this message board.'},
                        status=403,
                    )
                message = Message.objects.create(
                    lecture=lecture,
                    auth_user=request.user,
                    message=message_text,
                )
                _broadcast(course_id, auth_id, "message", message.pk)
                return JsonResponse({'success': True})
        
        return render(request, 'student-message.html', data)
                
    @staticmethod
    @login_required
    def instructor(request):
        course, course_member = _selected_course(request, staff=True)
        auth_id = request.user.pk
        course_id = course.pk
        
        data = {
            "auth_id": auth_id,
            "course_id": course_id,
            "course_member": course_member,
            "MEDIA_URL": settings.MEDIA_URL,
        }

        lecture = Lecture.objects.filter(course_id=course_id, end_time__isnull=True).first()

        if lecture:
            data["lecture"] = lecture
            
            messages = Message.getMessages(lecture.pk, False)
            data["messages"] = messages
            
        if request.method == 'POST':
            if "reply-message" in request.POST:
                reply = (request.POST.get('reply-message') or '').strip()
                message_id = request.POST.get('message-id')
                if not reply or len(reply) > MAX_MESSAGE_LENGTH:
                    return JsonResponse({'success': False, 'error': 'Invalid reply.'}, status=400)
                message = get_object_or_404(
                    Message,
                    pk=message_id,
                    lecture__course_id=course_id,
                )
                message.reply_message = reply
                message.save(update_fields=["reply_message"])
                _broadcast(course_id, auth_id, "reply-message", message.pk)
                return JsonResponse({'success': True})
                
            elif "block-user" in request.POST:
                student_id = request.POST.get('block-user')
                if lecture is None:
                    return JsonResponse({'success': False, 'error': 'Lecture not found.'}, status=409)
                student_member = get_object_or_404(
                    CourseMember,
                    course=course,
                    user_id=student_id,
                    role__in=LEARNER_ROLES,
                    active=True,
                )
                lecture.blocked_students.add(student_member.user)
                Message.objects.filter(
                    lecture=lecture,
                    auth_user=student_member.user,
                ).update(blocked=True)
                _broadcast(course_id, auth_id, "block-user", student_member.user_id)
                return JsonResponse({'success': True})
                
            elif "unblock-user" in request.POST:
                student_id = request.POST.get('unblock-user')
                if lecture is None:
                    return JsonResponse({'success': False, 'error': 'Lecture not found.'}, status=409)
                student_member = get_object_or_404(
                    CourseMember,
                    course=course,
                    user_id=student_id,
                    role__in=LEARNER_ROLES,
                    active=True,
                )
                lecture.blocked_students.remove(student_member.user)
                _broadcast(course_id, auth_id, "unblock-user", student_member.user_id)
                return JsonResponse({'success': True})
                
            elif 'broadcast-message' in request.POST:
                message_id = request.POST.get('broadcast-message')
                message = get_object_or_404(
                    Message,
                    pk=message_id,
                    lecture__course_id=course_id,
                )
                message.broadcast = True
                message.save(update_fields=["broadcast"])
                _broadcast(course_id, auth_id, "broadcast-message", message.pk)
                return JsonResponse({'success': True})
                
            elif "hide-message" in request.POST:
                message_id = request.POST.get('hide-message')
                message = get_object_or_404(
                    Message,
                    pk=message_id,
                    lecture__course_id=course_id,
                )
                message.hidden = True
                message.save(update_fields=["hidden"])
                _broadcast(course_id, auth_id, "hide-message", message.pk)
                return render(request, 'instructor-message.html', data)

            elif 'toggle-message-board' in request.POST:
                if lecture is None:
                    return JsonResponse({'success': False, 'error': 'Lecture not found.'}, status=409)
                toggle = request.POST.get('toggle-message-board')
                if toggle not in {"true", "false"}:
                    return JsonResponse({'success': False, 'error': 'Invalid setting.'}, status=400)
                lecture.enable_messages = toggle == "true"
                lecture.save(update_fields=["enable_messages"])
                _broadcast(course_id, auth_id, "update-message-visibility", lecture.pk)
                return JsonResponse({'success': True})
        # testing this
        elif 'get-messages' in request.GET:
            if lecture is None:
                return JsonResponse(
                    {'success': False, 'error': 'Lecture not found.'},
                    status=409,
                )
            try:
                message_id = int(request.GET.get('message-id', ''))
            except (TypeError, ValueError):
                return JsonResponse(
                    {'success': False, 'error': 'A valid message id is required.'},
                    status=400,
                )
            # The broadcast carries the created row's primary key. Resolve that
            # exact row within both the selected course and its current lecture;
            # never substitute whichever message happens to be newest.
            get_object_or_404(
                Message,
                pk=message_id,
                lecture=lecture,
                lecture__course=course,
            )
            message = Message.getMessages(lecture.pk, False).filter(
                message_id=message_id,
            ).first()
            template = loader.get_template('message-bubble.html')
            rendered_html = template.render({"message": message})
            return JsonResponse({
                'html_content': rendered_html,
                'message_id': message_id,
            })
        
        elif 'get-blocked-list' in request.GET:
            if lecture is None:
                return JsonResponse({'blocked_students': []})
            # Preferred names, as everywhere else the instructor sees a student.
            block_list = []
            for blocked in lecture.blocked_students.select_related("profile"):
                profile = getattr(blocked, "profile", None)
                block_list.append({
                    "id": blocked.id,
                    "first_name": (
                        getattr(profile, "preferred_first_name", "") or blocked.first_name
                    ),
                    "last_name": (
                        getattr(profile, "preferred_last_name", "") or blocked.last_name
                    ),
                })

            response_data = {
                'blocked_students': block_list,
            }

            return JsonResponse(response_data)

        return render(request, 'instructor-message.html', data)
