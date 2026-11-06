"""Keeping a demonstration class's guests inside it.

Joining with a six-digit code and a name logs in an account nobody vouched
for. That is the point -- a room full of people cannot each make a CWL account
to watch a demo -- but it means the two doors out of a course have to be shut,
because everything else about Agora's permissions rests on membership and a
guest is a member of exactly one course.

The two doors:

* enrolling, which is the one action that turns a non-member into a member of
  a course they were not invited to, and
* creating a course, which OPEN_COURSE_CREATION leaves available to any signed
  in account so that a new instructor is not deadlocked out of their first one.

Everything else already refuses them: `get_course_member` returns None for a
course they are not in, and every course-scoped view asks it first.
"""


def is_guest(user):
    """Whether this account exists only to attend a demonstration class."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return user.memberships.filter(is_sandbox_guest=True).exists()


def guest_course_id(user):
    """The one course a guest belongs to, or None."""
    membership = user.memberships.filter(is_sandbox_guest=True).first()
    return membership.course_id if membership else None


REFUSAL = (
    "You joined a demonstration class with a code, so this account can only "
    "be used in that class. Sign in with your CWL to use Agora properly."
)
