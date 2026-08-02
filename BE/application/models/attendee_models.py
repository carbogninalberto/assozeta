"""
Copyright: Bakney S.R.L.
"""
import uuid

from auditlog.registry import auditlog
from django.db import models
from django.utils import timezone
from application.models.courses_models import Course


class AttendanceRegistry(models.Model):
    """
    This model contains a registry information
    The period field allow to regenerate, verify and validate the attendance_day
    rows for the registry.
    """
    DRAFT = 1
    PUBLISHED = 2

    STATUS = (
        (DRAFT, 'draft'),
        (PUBLISHED, 'published')
    )

    attendance_registry_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    creation_date = models.DateTimeField(default=timezone.now)
    events = models.JSONField(null=True)
    status = models.PositiveSmallIntegerField(choices=STATUS, default=1)

    class Meta:
        indexes = [
            models.Index(fields=['attendance_registry_id', 'course']),
        ]


class AttendanceDay(models.Model):
    """
    This model contains the row defined by the registry with the attendee for each
    defined row.
    """
    attendance_day_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    attendance_registry = models.ForeignKey(AttendanceRegistry, on_delete=models.CASCADE)
    title = models.CharField(max_length=125, null=True)
    date = models.DateTimeField(null=False)
    attendees = models.JSONField(null=True)
    # automark sets these attendees as absent
    expected_absences = models.JSONField(null=True)
    auto_marked = models.BooleanField(default=False)
    associated_event = models.UUIDField(null=True)

    class Meta:
        indexes = [
            models.Index(fields=['attendance_day_id', 'attendance_registry']),
        ]



class GlobalCalendarEvents(models.Model):
    """
    Global calendar events for sport associations
    """
    global_calendar_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey('SportAssociation', on_delete=models.CASCADE, null=True)
    events = models.JSONField(null=True)

    class Meta:
        indexes = [
            models.Index(fields=['global_calendar_id', 'sport_association']),
        ]


class Reminders(models.Model):
    """
    Event reminders for users and instructors
    """
    reminders_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    event_id = models.UUIDField(default=uuid.uuid4, null=True)
    event_title = models.CharField(max_length=2000, null=False)
    event_description = models.CharField(max_length=2000, null=True)
    event_course_title = models.CharField(max_length=2000, null=True)
    event_reminder_text = models.CharField(max_length=2000, null=True)
    send_at = models.DateTimeField(null=False)
    user = models.ForeignKey('User', on_delete=models.CASCADE, null=False)
    instructor = models.ForeignKey('Instructor', on_delete=models.CASCADE, null=True)
    completed = models.BooleanField(default=False)
    sport_association = models.ForeignKey('SportAssociation', on_delete=models.CASCADE, null=True)


auditlog.register(AttendanceRegistry)
auditlog.register(AttendanceDay)
auditlog.register(GlobalCalendarEvents)
auditlog.register(Reminders)
