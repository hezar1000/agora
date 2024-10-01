from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse, resolve

# Test scenario 1: add new course
# Test scenario 2: enter with instructor/ta/student code as well as wrong codes
# Test scenario 3: check can TA see reviews True/False
# Test scenario 4: check enable_independent_pool True/False (views: student dashboard, assign student review)
