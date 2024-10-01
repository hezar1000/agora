from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from django.apps import apps

import peer_grade.choices as status_choices


import datetime


class Course(models.Model):
    """Defines a course"""

    displayname = models.CharField(
        "Display Name", db_column="display_name", max_length=128
    )
    """Display name for the course"""

    # users associated with this course (including instructors, students, and TAs)
    # users = models.ManyToManyField(User, through='CourseMember')

    browsable = models.BooleanField(
        "Visible to Students?", db_column="browsable", db_index=True
    )
    archived = models.BooleanField("Archived?", db_column="archived", db_index=True)

    stucode = models.CharField(
        "Student Enroll Code",
        db_column="student_enroll_code",
        max_length=128,
        null=True,
        db_index=True,
    )
    """student enter the auto-generated course code to gain access to the course"""

    tascode = models.CharField(
        "TA Enroll Code",
        db_column="ta_enroll_code",
        max_length=128,
        null=True,
        db_index=True,
    )
    """TAs can enter this code to gain access to the course"""

    instructor_code = models.CharField(
        "Instructor Enroll Code",
        db_column="instructor_enroll_code",
        max_length=128,
        null=True,
        db_index=True,
    )
    """Instructors can enter this code to gain access to the course (not usually used)"""

    total_late_units = models.IntegerField(blank=True, default=6)

    can_tas_see_reviews = models.BooleanField(default=False)
    """
    If False, the TAs can't see student reviews, which includes:
      - Grade, or even access the review page in general except the following cases:
        - An evaluation of the same review submission has been assigned to the TA
        - An appeal request of the corresponding submission has been assigned to the TA
        - A student report has been assigned to the TA corresponding the review

    Earlier, this option also meant that TAs could not see the grader's name
    however this is not the case anymore.
    """

    enable_independent_pool = models.BooleanField(default=True)
    """
    If True, the students will be categorized into supervised/independet pools
    This is regulated using the calibration and evaluations functions and means that
    the independent students' submissions will either not be reviewed by TAs or will
    be spot checked less frequently, hence the name independent/unsupervised
    """
    enable_participation = models.BooleanField(default=False)
    points_upon_participation_in_green_list = models.FloatField("points_upon_participation_in_green_list", db_column='points_upon_participation_in_green_list', null=True, default=10.0)
    points_upon_participation_in_blue_list = models.FloatField("points_upon_participation_in_blue_list", db_column='points_upon_participation_in_blue_list', null=True, default=10.0)
    fraction_of_points_gained_upon_further_participations = models.FloatField("fraction_of_points_gained_upon_further_participations", db_column='fraction_of_points_gained_upon_further_participations', null=True, default=0.1)
    points_upon_participation_in_red_list = models.FloatField("points_upon_participation_in_red_list", db_column='points_upon_participation_in_red_list', null=True, default=0.0)
    points_upon_participation_in_yellow_list = models.FloatField("points_upon_participation_in_yellow_list", db_column='points_upon_participation_in_yellow_list', null=True, default=0.0)


    # late_per_submission = models.IntegerField("Max Late Day per submission", blank=True, default=2)

    # These should not be attributes of the course.  It should be attributes of an assignment instead.
    # spotcheckprob = models.FloatField("Spot Check Probability", db_column='spotCheckProb', default=0.5)   # show it, what factions of reviews are checked by TAs This field type is a guess.
    # highmarkthreshold = models.FloatField("Threshold for High Marks", db_column='highMarkThreshold', default=0.8)   # bias towards marks higher than this threshold
    # highmarkbias = models.FloatField("Bias Towards High Marks", db_column='highMarkBias', default=1.0)   # multiplicative factor of bias towards high marks.
    # calibrationthreshold = models.FloatField("Threshold for Low Calibration Scores", db_column='calibrationThreshold', default=0.75)   # bias towards students whose calibration score is lower than this threshold
    # calibrationbias = models.FloatField("Bias Towards Low Calibration Scores", db_column='calibrationBias', default=1.0)   # multiplicative factor of bias towards low calibration scores.

    # Not used right now
    # authtype = models.CharField(db_column='authType', max_length=128)  # not using thisright now.  multi-level, ldap, pdo.
    # registrationtype = models.CharField(db_column='registrationType', max_length=128) # not using this right now.  used to be either open or closed.

    class Meta:
        db_table = "course"

    def __str__(self):
        return "%s" % (self.displayname)

    def has_ta(self):
        return self.members.filter(role="ta").exists()

    def tas(self):
        return self.members.filter(role="ta")


def display_role(role):
    if role == "ta":
        return "TA"
    return role.capitalize()


class CourseMember(models.Model):
    """Each student/TA/instructor needs a corresponds to a CourseMember object"""

    course = models.ForeignKey(Course, related_name="members", on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name="memberships", on_delete=models.CASCADE)

    role = models.CharField(
        "User Type", db_column="role", max_length=128, db_index=True
    )

    is_independent = models.BooleanField(
        "is_independent?", db_column="is_independent", default=False, db_index=True
    )

    time_is_independent_changed= models.DateTimeField(default=timezone.now)
    """
    Determines whether the student is supervised/independent in this course
    (is False for staff members)
    """

    active = models.BooleanField(default=True, blank=True, db_index=True)
    """
    Not deleting members from DB, since it automatically removes all of their submissions/reviews
    (active == False) indicates that the member has been removed from the course
    """

    qualified = models.BooleanField(default=True, blank=True)
    """If False, the student cannot submit assignments except for qualification assignments"""
    upper_confidence_bound = models.FloatField("Upper confidence bound", db_column='upperconfidencebound', null=True, default=1.0)
    markingload = models.FloatField("Marking Load", db_column='markingload', null=True, default=0.0)
    lower_confidence_bound = models.FloatField("Lower confidence bound", db_column='lowerconfidencebound', null=True, default=0.0)

    # This is more relevant when we think about marking.
    # Enable this later.
    # markingload = models.FloatField("Marking Load", db_column='markingload', null=True, default=1.0)
    
    hand_up = models.BooleanField(
        "hand_up", db_column="hand_up", default=False, db_index=True
    )
    hand_up_list_2 = models.BooleanField(
        "hand_up_list_2", db_column="hand_up_list_2", default=False, db_index=True
    )
    hand_up_list_3 = models.BooleanField(
        "hand_up_list_3", db_column="hand_up_list_3", default=False, db_index=True
    )
    hand_up_list_4 = models.BooleanField(
        "hand_up_list_4", db_column="hand_up_list_4", default=False, db_index=True
    )
    spoken = models.BooleanField(
        "spoken", db_column="spoken", default=False, db_index=True
    )
    time_spoken = models.DateTimeField("time_spoken", db_column="time_spoken", default=timezone.now)

    participation_points = models.IntegerField(
        "participation_points", db_column='participation_points', 
        null=True, default=0)

    regular_points = models.IntegerField(
        "regular_points", db_column='regular_points', 
        null=True, default=0)

    first_hand_up = models.BooleanField(
        "first_hand_up", db_column="first_hand_up", default=True, db_index=True
    )


    class Meta:
        db_table = "course_member"
        unique_together = ("course", "user")

    def __str__(self):
        return "CourseMember %s in %s as %s" % (
            self.get_user_fullname(),
            self.course,
            self.role,
        )

    def display(self):
        return "%s (%s in %s)" % (
            self.get_user_fullname(),
            self.display_role(),
            self.course.displayname,
        )

    def is_TA(self):
        return self.role == "ta"

    def is_instructor(self):
        return self.role == "instructor"

    def is_staff(self):
        return self.is_TA() or self.is_instructor()

    def display_role(self):
        return display_role(self.role)

    def get_user_fullname(self):
        return (
            self.user.first_name.capitalize() + " " + self.user.last_name.capitalize()
        )

    def get_user_id(self):
        # FIXME: if we change how we store student ID
        return self.user.username

    # def update_late_units(self, submission, ):




class CourseParticipation(models.Model):

    participant = models.ForeignKey(
        CourseMember, related_name="participant", on_delete=models.CASCADE
    )

    time_participated = models.DateTimeField("time_participated", db_column="time_participated", default=timezone.now)

    participation_list = models.IntegerField(
        "participation_list", db_column='participation_list', null=True, default=0
    )
    
    participation_points_gained = models.IntegerField(
        "participation_points_gained", db_column='participation_points_gained', null=True, default=0
    )
    spoke_upon_participation = models.BooleanField(
        "spoke_upon_participation", db_column="spoke_upon_participation", default=False, db_index=True
    )

    count_in_calculations = models.BooleanField(
        "count_in_calculations", db_column="count_in_calculations", default=True, db_index=True
    )
    real_participation = models.BooleanField(
        "real_participation", db_column="real_participation", default=True, db_index=True
    )

# class CourseConfiguration(models.Model):
#     courseid = models.IntegerField("Course ID", db_column='courseID', primary_key=True, blank=True, null=False)

#     # Assigning spot checks
#     spotcheckprob = models.FloatField("Spot Check Probability", db_column='spotCheckProb', default=0.5)   # show it, what factions of reviews are checked by TAs This field type is a guess.
#     highmarkthreshold = models.FloatField("Threshold for High Marks", db_column='highMarkThreshold', default=0.8)   # bias towards marks higher than this threshold
#     highmarkbias = models.FloatField("Bias Towards High Marks", db_column='highMarkBias', default=1.0)   # multiplicative factor of bias towards high marks.
#     calibrationthreshold = models.FloatField("Threshold for Low Calibration Scores", db_column='calibrationThreshold', default=0.75)   # bias towards students whose calibration score is lower than this threshold
#     calibrationbias = models.FloatField("Bias Towards Low Calibration Scores", db_column='calibrationBias', default=1.0)   # multiplicative factor of bias towards low calibration scores.

# Number of peer reviews
# numreviews = models.IntegerField("Number of Reviews", db_column='numReviews', default=3)  # number of reviews to assign to each student

# used in "assign reviews"
# windowsize = models.IntegerField("Window Size", db_column='windowSize', default=4)  # used to assign reviews
# numcovertcalibrations = models.IntegerField(db_column='numCovertCalibrations')
# exhaustedcondition = models.TextField(db_column='exhaustedCondition')

# used in promotion or demotion from independent status
# scorewindowsize = models.IntegerField(db_column='scoreWindowSize', default=4)  # Number of prev assignments used to calculate score for promotion.
# scorethreshold = models.FloatField(db_column='scoreThreshold', default=0.8)   # Score threshold used for promotion to independent review status
# disqualifywindowsize = models.IntegerField(db_column='disqualifyWindowSize', default=4) # Number of prev assignments used to calculate score for demotion.
# disqualifythreshold = models.FloatField(db_column='disqualifyThreshold', default=0.8)   # Score threshold used for demotion to supervised review status

# used in auto grade and assign marks
# minreviews = models.IntegerField("Minimum Number of Reviews", db_column='minReviews')  # minimum # of reviews for autograde

# These fields exist because the assign review algorithm is not guaranteed to succeed.
# Should take these out.
# scorenoise = models.FloatField("Score Nois", db_column='scoreNoise')
# maxattempts = models.IntegerField("Maximum Number of Attempts", db_column='maxAttempts')

# class Meta:
#     db_table = 'course_configuration'
