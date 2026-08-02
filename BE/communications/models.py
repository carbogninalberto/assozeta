from datetime import datetime
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from auditlog.registry import auditlog
import pytz
from django.db import models
from django.db.models import Q
from django.utils import timezone

from application.models.subscriptions_models import Subscription, Tags
from core.celery import app
from core.tasks import send_mail_async
from application.models import SportAssociation
from core import settings
import logging

logger = logging.getLogger(__name__)


class CommunicationConfiguration(models.Model):
    SSL = 'SSL'
    TLS = 'TLS'
    NONE = 'NONE'

    ENCRYPTION_CHOICES = [
        (SSL, 'SSL'),
        (TLS, 'STARTTLS'),
        (NONE, 'No Encryption'),
    ]

    communication_configuration_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE, null=True)
    email_smtp_host = models.CharField(max_length=255, blank=True, null=True)
    email_smtp_port = models.PositiveIntegerField(blank=True, null=True)
    email_smtp_user = models.CharField(max_length=255, blank=True, null=True)
    email_smtp_password = models.CharField(max_length=255, blank=True, null=True)
    email_sender_name = models.CharField(max_length=255, blank=True, null=True)
    email_encryption = models.CharField(
        max_length=10,
        choices=ENCRYPTION_CHOICES,
        default=NONE,
        help_text="Choose the encryption method for the SMTP connection."
    )
    daily_email_limit = models.PositiveIntegerField(default=1000)  # This could be reduced with each email sent
    daily_email_balance = models.PositiveIntegerField(default=0)  # This could be reduced with each email sent
    sms_balance = models.PositiveIntegerField(default=10)  # This could be reduced with each SMS sent

    class Meta:
        indexes = [
            models.Index(fields=['communication_configuration_id', 'sport_association']),
        ]

    def has_smtp_configured(self):
        return all([self.email_smtp_host, self.email_smtp_port, self.email_smtp_user, self.email_smtp_password])

    def verify_smtp(self, send_email=True):
        # Verify that with the current configuration we can connect to the SMTP server
        # and send an email.
        # If the verification is successful, return True, otherwise return False.
        try:
            # log to the SMTP server based on email_encryption
            if self.email_encryption == self.SSL:
                server = smtplib.SMTP_SSL(self.email_smtp_host, self.email_smtp_port)
            else:
                server = smtplib.SMTP(self.email_smtp_host, self.email_smtp_port)
                if self.email_encryption == self.TLS:
                    server.starttls()
            # login to the SMTP server
            server.login(self.email_smtp_user, self.email_smtp_password)

            if not send_email:
                return True, "Ok"

            # send a test email
            message = 'Subject: {}\n\n{}'.format(
                f"Email di test da {settings.WHITELABEL_NAME}, SMTP configurato correttamente.",
                "Se hai ricevuto questa email, significa che hai configurato il server SMTP correttamente."
            ).encode('utf-8').strip()
            server.sendmail(f"{self.email_sender_name or settings.WHITELABEL_NAME} <{self.email_smtp_user}>",
                            self.sport_association.user.email, message)

            # close the connection
            server.quit()
            return True, "Ok"
        except Exception as e:
            print(e)
            return False, e

    def send_email(self, subject, body, recipient_list, html_body=None, reply_to=None):
        # Send an email using the current configuration.
        # If the email is sent successfully, return True, otherwise return False.
        # check if the daily_email_balance is equal to or greater than the daily_email_limit
        if self.daily_email_balance >= self.daily_email_limit:
            return False, "Daily email limit reached"
        try:
            if not self.has_smtp_configured() or not self.verify_smtp(send_email=False)[0]:
                # send an email as bakney
                # set the email_sender_name to sport_association.denomination
                if html_body:
                    send_mail_async.apply_async(
                        kwargs={
                            "subject": subject,
                            "message": body,
                            "from_email": settings.DEFAULT_FROM_EMAIL.replace(settings.WHITELABEL_NAME,
                                                                           self.sport_association.denomination),
                            "recipient_list": recipient_list,
                            "fail_silently": False,
                            "html_message": html_body,
                            "sport_association_id": self.sport_association.sport_association_id,
                            "reply_to": reply_to,
                            "skip_association_check": True,
                        }
                    )
                else:
                    send_mail_async.apply_async(
                        kwargs={
                            "subject": subject,
                            "message": body,
                            "from_email": settings.DEFAULT_FROM_EMAIL.replace(settings.WHITELABEL_NAME,
                                                                           self.sport_association.denomination),
                            "recipient_list": recipient_list,
                            "fail_silently": False,
                            "sport_association_id": self.sport_association.sport_association_id,
                            "reply_to": reply_to,
                            "skip_association_check": True,
                    }
                    )
                return True, "Ok"
            # Create a MIMEText object to represent the email
            if html_body:
                msg = MIMEMultipart('alternative')
            else:
                msg = MIMEMultipart()

            # check if the email_sender_name is set
            if self.email_sender_name:
                msg['From'] = "{} <{}>".format(self.email_sender_name, self.email_smtp_user)
            else:
                msg['From'] = self.email_smtp_user

            msg['To'] = ', '.join(recipient_list)
            msg['Subject'] = subject
            if reply_to:
                if isinstance(reply_to, list):
                    msg['Reply-To'] = ', '.join(reply_to)
                else:
                    msg['Reply-To'] = reply_to
            if html_body:
                msg.attach(MIMEText(html_body, 'html', 'utf-8'))
            else:
                msg.attach(MIMEText(body, 'plain', 'utf-8'))

            # Convert the MIMEText object to a string
            message = msg.as_string()

            # log to the SMTP server based on email_encryption
            if self.email_encryption == self.SSL:
                server = smtplib.SMTP_SSL(self.email_smtp_host, self.email_smtp_port)
            else:
                server = smtplib.SMTP(self.email_smtp_host, self.email_smtp_port)
                if self.email_encryption == self.TLS:
                    server.starttls()

            # login to the SMTP server
            server.login(self.email_smtp_user, self.email_smtp_password)

            # send the email
            server.sendmail(self.email_smtp_user, recipient_list, message)

            # close the connection
            server.quit()
            # reduce the daily_email_balance
            self.daily_email_balance += 1
            self.save()
            return True, "Ok"
        except Exception as e:
            print(e)
            return False, e


class SmsCreditPayment(models.Model):
    sms_credit_payment_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    amount = models.PositiveIntegerField()
    payment_date = models.DateTimeField(auto_now_add=True)
    payment_intent_id = models.CharField(max_length=255, blank=True, null=True)
    paid = models.BooleanField(default=False)


class Message(models.Model):
    # Constants for communication types
    EMAIL = 'EMAIL'
    SMS = 'SMS'
    INSIDE_APP = 'INSIDE_APP'
    COMMUNICATION_TYPES = [
        (EMAIL, 'Email'),
        (SMS, 'SMS'),
        (INSIDE_APP, 'Inside App Message'),
    ]

    message_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    type = models.CharField(max_length=50, choices=COMMUNICATION_TYPES)
    message = models.TextField()
    subject = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)


class MessageTransaction(models.Model):
    message_transaction_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    message = models.ForeignKey(Message, on_delete=models.CASCADE)
    recipient = models.CharField(max_length=255)
    sent_on = models.DateTimeField(auto_now_add=True)
    delivered_on = models.DateTimeField(blank=True, null=True)
    opened_on = models.DateTimeField(blank=True, null=True)
    clicked_on = models.DateTimeField(blank=True, null=True)
    bounced_on = models.DateTimeField(blank=True, null=True)
    bounced_reason = models.CharField(max_length=255, blank=True, null=True)
    bounced_type = models.CharField(max_length=255, blank=True, null=True)
    bounced_status = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['message_transaction_id', 'message']),
        ]


class AutomationWorkflow(models.Model):
    automation_workflow_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    sport_association = models.ForeignKey(SportAssociation, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    automation_tree = models.JSONField(null=False, default=list)
    enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['automation_workflow_id', 'sport_association']),
        ]

    def trigger(self, trigger_type=None, subscription=None, data=None):
        if not self.enabled:
            return False, "Automation workflow is not enabled"

        # if 'triggered' in self.automation_tree[0]['data'] and self.automation_tree[0]['data']['triggered']:
        #    return False, "Workflow already triggered"

        # read the automation_tree and trigger the automation workflow
        trigger_node = self.automation_tree[0]['value']
        logger.info(f"trigger_node: {trigger_node} - trigger_type: {trigger_type}")
        if trigger_type is None and trigger_node == 'cron':
            time_str = self.automation_tree[0]['data']['time']  # e.g. 2023-10-08T11:30
            # convert time_str to utc from Rome timezone
            # time = datetime.fromisoformat(time_str).astimezone(pytz.utc)
            rome_tz = pytz.timezone('Europe/Rome')
            time = rome_tz.localize(datetime.fromisoformat(time_str))
            logger.info(f"time: {time}")
            # current timezone is Rome and the time_str is in Rome timezone
            # time = datetime.fromisoformat(time_str)#.replace(tzinfo=pytz.timezone('Europe/Rome'))
            # convert time into seconds from now, remove the timezone offset
            now_rome = datetime.now().astimezone(rome_tz)
            logger.info(f"time now: {now_rome}")

            # Get the time difference as a timedelta object
            time_diff = time - now_rome
            logger.info(f"time difference: {time_diff}")

            # Convert the timedelta object to seconds
            time_secs = time_diff.total_seconds()
            logger.info(f"time secs: {time_secs}")

            if time_secs < 0:
                return False, "Time is in the past"

            app.send_task(
                'check_workflows_trigger_cron',
                args=[self.automation_workflow_id],
                countdown=time_secs
            )

        elif trigger_type == trigger_node:
            # if the trigger_type is cron, we schedule the full automation workflow based on targets
            if trigger_node == 'cron':
                target = self.automation_tree[0]['data']['target']

                today = timezone.now().date()
                # check if target is 'all' or a tag
                subscriptions = Subscription.objects.filter(
                    sport_association=self.sport_association,
                    start_date__lte=today,
                    end_date__gte=today
                )

                if target != 'all':
                    # check the target
                    if target == 'approved':
                        subscriptions = subscriptions.filter(
                            status_flag=Subscription.ACCEPTED,
                            archived=False
                        )
                    elif target == 'rejected':
                        subscriptions = subscriptions.filter(
                            status_flag=Subscription.REJECTED,
                            archived=False
                        )
                    elif target == 'not_signed':
                        subscriptions = subscriptions.filter(
                            status_flag=Subscription.NOT_SIGNED,
                            archived=False
                        )
                    elif target == 'pending':
                        subscriptions = subscriptions.filter(
                            status_flag=Subscription.PENDING,
                            archived=False
                        )
                    elif target == 'archived':
                        subscriptions = subscriptions.filter(archived=True)
                    else:
                        # get tag name from target id
                        tag = Tags.objects.filter(
                            tag_id=target
                        ).first()
                        if tag:
                            subscriptions = subscriptions.filter(
                                Q(tags__tag_id=tag.tag_id) |
                                Q(tags__tag_name__icontains=tag.tag_name)
                            )
                if len(self.automation_tree) > 1:
                    logger.info(f"subscriptions: {subscriptions}")
                    for subscription in subscriptions:
                        # self.schedule_automation_tree(subscription=subscription)
                        # schedule workflow_block_execute
                        app.send_task(
                            'workflow_block_execute',
                            args=[self.automation_workflow_id, subscription.subscription_id, 1],
                        )
            elif len(self.automation_tree) > 1:
                # self.schedule_automation_tree(subscription=subscription)
                # schedule workflow_block_execute
                app.send_task(
                    'workflow_block_execute',
                    args=[self.automation_workflow_id, subscription.subscription_id, 1],
                )


# Register models with auditlog
auditlog.register(CommunicationConfiguration)
auditlog.register(SmsCreditPayment)
auditlog.register(Message)
auditlog.register(AutomationWorkflow)