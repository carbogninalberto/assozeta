import uuid

from auditlog.registry import auditlog
from django.db import models
from django.utils import timezone

from application.models.payment_models import Payment
from application.models.user_models import SportAssociation, User
from docmanager.models import Document


class Module(models.Model):
    """
    This model contains information about a Module
    """

    module_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    custom_link = models.CharField(max_length=255, null=True, unique=True)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)
    creation_date = models.DateTimeField(default=timezone.now)
    title = models.CharField(max_length=255, null=True)
    # require approval
    require_approval = models.BooleanField(default=False)
    # availability
    start_date = models.DateField(null=True)
    end_date = models.DateField(null=True)
    always_active = models.BooleanField(default=False)
    # limitation on subscribers
    max_responses = models.PositiveIntegerField(null=True)
    # queue mode
    queue_mode = models.BooleanField(default=False)
    # only logged users
    only_users = models.BooleanField(default=False)
    # payment_info
    payment_required = models.BooleanField(default=False)
    payment_data = models.JSONField(null=False, default=dict)
    response_message = models.CharField(max_length=1024, null=True)
    # allow attachments upload
    allow_attachments = models.BooleanField(default=False)
    # form_data
    elements = models.JSONField(null=False, default=dict)


    @property
    def enabled(self):
        if self.always_active:
            return True
        if self.start_date and self.end_date:
            return self.start_date <= timezone.now().date() <= self.end_date
        return False

    @property
    def current_responses(self):
        responses = ModuleResponses.objects.filter(module=self, approved=True)
        return responses.count()

    @property
    def is_max_responses_reached(self):
        return self.max_responses and self.current_responses >= self.max_responses

    class Meta:
        indexes = [
            models.Index(fields=['module_id', 'sport_association']),
        ]


class ModuleResponses(models.Model):

    module_response_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    module = models.ForeignKey(Module, on_delete=models.CASCADE)
    creation_date = models.DateTimeField(default=timezone.now)
    progressive_response_number = models.PositiveIntegerField(null=True)
    queue_position = models.PositiveIntegerField(null=True)
    response = models.JSONField(null=False, default=dict)
    approved = models.BooleanField(default=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True)
    attachments = models.ManyToManyField(Document, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['module_response_id', 'module']),
        ]


auditlog.register(Module)
auditlog.register(ModuleResponses)