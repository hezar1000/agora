import json
import json
import re
import uuid
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from django.contrib.auth.models import User
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from peer_course.models import Course

POLL_HTML_MAX_LENGTH = 100_000
_ALLOWED_POLL_TAGS = {
    "a", "b", "blockquote", "br", "code", "em", "h1", "h2", "h3",
    "h4", "h5", "h6", "i", "img", "li", "ol", "p", "pre", "span",
    "strike", "strong", "sub", "sup", "s", "u", "ul",
}
_DROP_WITH_CONTENT_TAGS = {
    "audio", "button", "embed", "form", "iframe", "input", "link", "math",
    "meta", "object", "script", "source", "style", "svg", "textarea", "video",
}
_SAFE_DATA_IMAGE = re.compile(
    r"^data:image/(?:gif|jpeg|png|webp);base64,[a-z0-9+/=\s]+$", re.IGNORECASE
)


def _safe_url(value, *, image=False):
    """Return a browser-safe URL or ``None`` for executable/unknown schemes."""
    if not isinstance(value, str):
        return None
    value = "".join(ch for ch in value.strip() if ord(ch) >= 32)
    if not value:
        return None
    if image and _SAFE_DATA_IMAGE.fullmatch(value):
        return value
    parsed = urlsplit(value)
    allowed_schemes = {"http", "https"} if image else {"http", "https", "mailto"}
    if parsed.scheme and parsed.scheme.lower() not in allowed_schemes:
        return None
    if not parsed.scheme and value.startswith("//"):
        return None
    return value


def sanitize_poll_html(value):
    """Keep normal Quill formatting while removing stored-XSS primitives."""
    if not value:
        return ""
    value = str(value)[:POLL_HTML_MAX_LENGTH]
    soup = BeautifulSoup(value, "html.parser")

    for tag in list(soup.find_all(True)):
        # Descendants of a tag dropped earlier in this loop (e.g. the MathML
        # inside a Quill/KaTeX formula) are already detached; touching them
        # raises "not part of a tree" and 500s every poll page for the course.
        if tag.decomposed:
            continue
        name = tag.name.lower()
        if name in _DROP_WITH_CONTENT_TAGS:
            tag.decompose()
            continue
        if name not in _ALLOWED_POLL_TAGS:
            tag.unwrap()
            continue

        clean_attrs = {}
        classes = tag.get("class", [])
        ql_classes = [item for item in classes if re.fullmatch(r"ql-[a-z0-9-]+", item)]
        if ql_classes:
            clean_attrs["class"] = ql_classes

        # Quill uses this inert text attribute to rebuild formula spans.  It is
        # safe to retain after capping its size; all executable attributes are
        # still discarded below.
        if name == "span" and "ql-formula" in ql_classes and tag.get("data-value"):
            clean_attrs["data-value"] = str(tag["data-value"])[:2000]

        if name == "a":
            href = _safe_url(tag.get("href"))
            if href:
                clean_attrs["href"] = href
                clean_attrs["rel"] = "noopener noreferrer"
                if tag.get("target") == "_blank":
                    clean_attrs["target"] = "_blank"
        elif name == "img":
            src = _safe_url(tag.get("src"), image=True)
            if src:
                clean_attrs["src"] = src
            if tag.get("alt"):
                clean_attrs["alt"] = str(tag["alt"])[:500]

        tag.attrs = clean_attrs

    return str(soup)


def parse_answer_options(value):
    """Normalize both current JSON strings and legacy list/null values."""
    if isinstance(value, list):
        return [str(option) for option in value]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(option) for option in parsed] if isinstance(parsed, list) else []


class Lecture(models.Model):
    lecture_id = models.AutoField(primary_key=True)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True)
    session = models.ForeignKey(
        "peer_course.CourseSession", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="live_lectures",
    )
    # Snapshot the scheduled deadline at manual start. The grading date can
    # subsequently change without moving the live lecture's clock.
    scheduled_end_time = models.DateTimeField(null=True, blank=True)
    blocked_students = models.ManyToManyField(User, related_name='blocked_students')
    enable_messages = models.BooleanField(default=True)
    #: Six digits that let a guest join this lecture with only a name, when
    #: the course allows it. Made on first use; dead once the lecture ends.
    guest_code = models.CharField(max_length=6, blank=True, default="", db_index=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.end_time is not None:
            from .microphone import end_microphone_lecture
            from peer_course.lecture_guests import remove_guests
            end_microphone_lecture(self.pk)
            remove_guests(self.course_id)

    @classmethod
    def create(cls, course_id):
        """Return the one open lecture, serializing scheduler/manual start races."""
        with transaction.atomic():
            Course.objects.select_for_update().get(pk=course_id)
            lecture = cls.objects.filter(
                course_id=course_id,
                end_time__isnull=True,
            ).order_by("lecture_id").first()
            if lecture is not None:
                return lecture
            try:
                with transaction.atomic():
                    return cls.objects.create(course_id=course_id)
            except IntegrityError:
                return cls.objects.get(course_id=course_id, end_time__isnull=True)

    def endLecture(lecture_id, time):
        lecture = Lecture.objects.get(pk=lecture_id)
        lecture.end_time = time
        lecture.save()
        return lecture

    def currentLecture(course_id):
        lecture = Lecture.objects.filter(
            course_id=course_id, end_time__isnull=True).first()
        return lecture

    def blockStudent(lecture_id, student_id):
        lecture = Lecture.objects.get(pk=lecture_id)
        student = User.objects.get(pk=student_id)
        messages = Message.objects.filter(
            lecture_id=lecture_id, auth_user_id=student_id)

        lecture.blocked_students.add(student)
        lecture.save()

        for message in messages:
            message.blocked = True
            message.save()

        return lecture
    
    def unblockStudent(lecture_id, student_id):
        lecture = Lecture.objects.get(pk=lecture_id)
        student = User.objects.get(pk=student_id)

        lecture.blocked_students.remove(student)
        lecture.save()

        return lecture
    
    def toggleMessages(lecture_id, toggle):
        lecture = Lecture.objects.get(pk=lecture_id)
        lecture.enable_messages = True if toggle == "true" else False
        lecture.save()
        return lecture

    class Meta:
        db_table = "peer_lecture"
        constraints = [
            models.UniqueConstraint(
                fields=("course",),
                condition=models.Q(end_time__isnull=True),
                name="unique_open_lecture_per_course",
            )
        ]


class ClassroomMicrophone(models.Model):
    """One live microphone and one playback device per course, across workers."""

    course = models.OneToOneField(Course, on_delete=models.CASCADE, primary_key=True)
    lecture = models.ForeignKey(Lecture, null=True, on_delete=models.SET_NULL)
    selected_member = models.ForeignKey(
        "peer_course.CourseMember", null=True, on_delete=models.SET_NULL,
    )
    last_speaker = models.ForeignKey(
        "peer_course.CourseMember", null=True, on_delete=models.SET_NULL,
        related_name="last_classroom_microphones",
    )
    generation = models.UUIDField(default=uuid.uuid4)
    transport = models.CharField(max_length=6, default="direct")
    # Once the current output device has needed the relay (campus Wi-Fi often
    # blocks peers), later turns start on it instead of waiting for direct
    # audio to fail again. A different output device tries direct afresh.
    relay_preferred = models.BooleanField(default=False)
    receiver_channel = models.CharField(max_length=255, blank=True, default="")
    receiver_user = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL, related_name="classroom_outputs",
    )
    receiver_expires_at = models.DateTimeField(null=True)
    sender_channel = models.CharField(max_length=255, blank=True, default="")
    sender_expires_at = models.DateTimeField(null=True)


class Poll(models.Model):
    poll_id = models.AutoField(primary_key=True)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE, null=True)
    title = models.CharField(max_length=200)
    duplicate_of = models.ForeignKey('self', on_delete=models.RESTRICT, null=True, blank=True)
    archived = models.BooleanField(default=False, db_index=True)
    hands_moved_at = models.DateTimeField(null=True, blank=True)
    is_visible_to_students = models.BooleanField(default=True, help_text="If false, this poll will not appear in the student's 'View Past Poll Results' list.")
    start_time = models.DateTimeField(null=True)
    end_time = models.DateTimeField(null=True)
    poll_data = models.TextField(null=True)
    answer = models.JSONField(null=True, blank=True, default=list)
    answer_options = models.TextField(null=True)
    dont_save_answer = models.BooleanField()
    enforce_one_answer = models.BooleanField(default=False, help_text="When enabled, participants will only be able to select one answer from the list.")
    min_word_count = models.IntegerField(default=1, help_text="Minimum word count for text polls (minimum value is 1).")
    max_word_count = models.IntegerField(default=50, help_text="Maximum word count for text polls.")
    poll_type = models.CharField(
        max_length=10, # Increased length for new values
        choices=[
            ('iclicker', 'iClicker'),
            ('standard', 'Standard Likert'),
            ('numerical', 'Numerical'),
            ('list', 'Participation List'), # 'list' type is preserved
            ('text', 'Text Response') # Text poll type for open-ended responses
        ],
        default='iclicker',
        help_text="The type of poll, which determines the default answer options.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("lecture",),
                condition=models.Q(
                    start_time__isnull=False,
                    end_time__isnull=True,
                ),
                name="unique_open_poll_per_lecture",
            )
        ]

    def create(answer, dont_save_answer, title, course_id, poll_type='iclicker', enforce_one_answer=False, min_word_count=1, max_word_count=50):
        poll = Poll(answer=answer, dont_save_answer=dont_save_answer, title=title, course_id=course_id, poll_type=poll_type, enforce_one_answer=enforce_one_answer, min_word_count=min_word_count, max_word_count=max_word_count)
        poll.save()
        return poll
    
    def setDuplicate(poll_id, duplicate_id):
        poll = Poll.objects.get(pk=poll_id)
        poll.duplicate_of_id = duplicate_id
        poll.save()
        return poll

    def savePollText(poll_id, poll_data):
        poll = Poll.objects.get(pk=poll_id)
        cleaned = sanitize_poll_html(poll_data)
        poll.poll_data = cleaned or None
        poll.save()
        return poll

    def saveAnswerOptions(poll_id, answer_options):
        poll = Poll.objects.get(pk=poll_id)
        poll.answer_options = json.dumps(answer_options)
        poll.save()
        return poll

    def startPoll(poll_id, lecture_id, start_time):
        poll = Poll.objects.get(pk=poll_id)
        poll.start_time = start_time
        poll.lecture_id = lecture_id
        poll.save()
        return poll
    
    def getAnswerOptions(poll_id):
        poll = Poll.objects.get(pk=poll_id)

        return parse_answer_options(poll.answer_options)

    def endPoll(poll_id, end_time):
        poll = Poll.objects.get(pk=poll_id)
        poll.end_time = end_time
        poll.save()
        return poll

    def lastPoll(lecture_id):
        poll = Poll.objects.filter(
            lecture_id=lecture_id).order_by('-end_time').first()
        return poll
    
    def getSavedPolls(course_id, is_student = False):
        polls_orig = Poll.objects.filter(
            duplicate_of__isnull=True,
            course_id=course_id,
            archived=False,
        ).order_by('title')
        if is_student:
            polls_orig = polls_orig.filter(is_visible_to_students=True)
        annotated_polls = []
        for poll in polls_orig:
            most_recent_instance = Poll.objects.filter(
                duplicate_of=poll.pk, 
                start_time__isnull=False
            ).order_by('-start_time').first()
            poll.most_recent_start_time = most_recent_instance.start_time if most_recent_instance else None
            poll.answer_options = parse_answer_options(poll.answer_options)
            
            annotated_polls.append(poll)
        
        return annotated_polls

    def getResults(poll_id, user=None, course_timezone=None):
        """
        Get poll results. For text polls:
        - If user is provided (student viewing), only return their own response
        - If user is None (instructor viewing), return all responses
        - course_timezone: timezone string (e.g., 'America/Vancouver') to convert times
        """
        from django.utils import timezone as django_timezone
        import pytz
        
        poll = Poll.objects.get(pk=poll_id)
        dont_save_ans = poll.dont_save_answer
        if dont_save_ans:
            return None
        else:
            all_submissions = PollResult.objects.filter(poll_id=poll_id)
            correct_answers = poll.answer if isinstance(poll.answer, list) else []

            # Handle text polls differently - return individual responses
            if poll.poll_type == 'text':
                responses = []
                # If user is provided (student), only show their own response
                if user:
                    all_submissions = all_submissions.filter(auth_user=user)
                
                # Convert timezone if provided
                tz = None
                if course_timezone:
                    try:
                        tz = pytz.timezone(course_timezone)
                    except:
                        pass  # Invalid timezone, use UTC
                
                for submission in all_submissions:
                    student_answers = submission.answer
                    if isinstance(student_answers, list) and len(student_answers) > 0:
                        # Get user info
                        submission_user = submission.auth_user
                        
                        # Convert time to course timezone if available
                        time_str = None
                        if submission.time:
                            if tz:
                                local_time = django_timezone.localtime(submission.time, tz)
                                time_str = local_time.strftime("%Y-%m-%d %H:%M:%S")
                            else:
                                time_str = submission.time.isoformat()
                        
                        responses.append({
                            "user_id": submission_user.id,
                            "user_name": f"{submission_user.first_name} {submission_user.last_name}".strip() or submission_user.username,
                            "answer": student_answers[0] if student_answers else "",
                            "time": time_str
                        })
                return {"responses": responses, "answer": correct_answers, "poll_id": poll.pk, "poll_type": poll.poll_type}
            
            # Handle other poll types (aggregate counts)
            answer_options = parse_answer_options(poll.answer_options)
            results = {}
            for option in answer_options:
                results[option] = 0
            for submission in all_submissions:
                student_answers = submission.answer
                if isinstance(student_answers, list):
                    for single_answer in student_answers:
                        if single_answer in results:
                            results[single_answer] += 1
            return {"results": results, "answer": correct_answers, "poll_id": poll.pk, "poll_type": poll.poll_type }

    @property
    def safe_poll_data(self):
        """Sanitize legacy rows on every render as well as newly saved prompts."""
        return sanitize_poll_html(self.poll_data)
        
class PollResult(models.Model):
    result_id = models.AutoField(primary_key=True)
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE)
    auth_user = models.ForeignKey(User, on_delete=models.CASCADE)
    time = models.DateTimeField(auto_now_add=True)
    answer = models.JSONField(null=True, blank=True, default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("poll", "auth_user"),
                name="unique_poll_result_per_user",
            )
        ]

    def create(poll_id, auth_user_id, answer):
        result = PollResult(poll_id=poll_id, auth_user_id=auth_user_id)
        poll = Poll.objects.get(pk=poll_id)
        dont_save_ans = poll.dont_save_answer

        if dont_save_ans:
            result.answer = []
        else:
            result.answer = answer

        result.save()
        return result

    def updateResult(poll_id, auth_user_id, time, answer):
        result = PollResult.objects.get(poll_id=poll_id, auth_user_id=auth_user_id)
        result.time = time

        poll = Poll.objects.get(pk=poll_id)
        dont_save_ans = poll.dont_save_answer

        if dont_save_ans:
            result.answer = []
        else:
            result.answer = answer

        result.save()
        return result


class Message(models.Model):
    message_id = models.AutoField(primary_key=True)
    lecture = models.ForeignKey(Lecture, on_delete=models.CASCADE)
    auth_user = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.TextField()
    time = models.DateTimeField(auto_now_add=True)
    reply_message = models.TextField(null=True)
    blocked = models.BooleanField(default=False)
    hidden = models.BooleanField(default=False)
    broadcast = models.BooleanField(default=False)

    def create(lecture_id, auth_user_id, message):
        lecture = Lecture.objects.get(pk=lecture_id)
        if lecture.blocked_students.filter(pk=auth_user_id).exists():
            return None

        message = Message(lecture_id=lecture_id,
                          auth_user_id=auth_user_id, message=message)
        message.save()
        return message

    def reply(message_id, reply_message):
        message = Message.objects.get(pk=message_id)
        message.reply_message = reply_message
        message.save()
        return message
    
    def hideMessage(message_id):
        message = Message.objects.get(pk=message_id)
        message.hidden = True
        message.save()
        return message
    
    def broadcastMessage(message_id):
        message = Message.objects.get(pk=message_id)
        message.broadcast = True
        message.save()
        return message

    def getMessages(lecture_id, is_student):
        messages = Message.objects.filter(lecture_id=lecture_id).order_by('time')
        messages = messages.values(
            'message_id', 'lecture_id', 'auth_user_id', 'message', 'time',
            'reply_message', 'blocked', 'auth_user__first_name',
            'auth_user__last_name', 'hidden', 'broadcast',
            'auth_user__profile__preferred_first_name',
            'auth_user__profile__preferred_last_name',
        )

        if is_student:
            messages = messages.exclude(blocked=True)

        return messages
