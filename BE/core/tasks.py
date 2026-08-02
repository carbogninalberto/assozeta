import logging

from celery.exceptions import MaxRetriesExceededError
from celery import shared_task
from application.models.user_models import EmailLog
from django.core.mail import EmailMultiAlternatives, get_connection
from application.models import SportAssociation


logger = logging.getLogger(__name__)


def custom_send_mail(
    subject,
    message,
    from_email,
    recipient_list,
    fail_silently=False,
    auth_user=None,
    auth_password=None,
    connection=None,
    html_message=None,
    reply_to=None,
    skip_association_check=True
):
    """
    Custom wrapper for sending a single message to a recipient list with support for reply_to.
    """
    connection = connection or get_connection(
        username=auth_user,
        password=auth_password,
        fail_silently=fail_silently,
    )
    mail = EmailMultiAlternatives(
        subject, message, from_email, recipient_list, connection=connection, reply_to=reply_to
    )
    if html_message:
        mail.attach_alternative(html_message, "text/html")

    return mail.send()


@shared_task(bind=True, max_retries=3)
def send_mail_async(self, subject, message, from_email, recipient_list, sport_association_id=None, **kwargs):
    logger.info("Starting send_mail_async task", extra={'task_name': 'send_mail_async', 'recipient_count': len(recipient_list), 'sport_association_id': str(sport_association_id) if sport_association_id else None})
    try:
        # Database operations
        # with transaction.atomic():
        # Update or create EmailLog record
        sport_association = None

        result = 'Failed'
        sent = False
        if sport_association_id:
            sport_association = SportAssociation.objects.filter(pk=sport_association_id).first()
            logger.debug("Sport association found", extra={'sport_association_id': str(sport_association_id), 'denomination': sport_association.denomination if sport_association else None})
            print(f'Sport association: {sport_association}')
            if sport_association is not None and \
                    ('skip_association_check' not in kwargs or not kwargs['skip_association_check']):
                from communications.models import CommunicationConfiguration
                config = CommunicationConfiguration.objects.filter(sport_association=sport_association).first()
                print(f'Config: {config}')
                if config is not None:
                    config.send_email(
                        subject=subject,
                        body=message,
                        recipient_list=recipient_list,
                        html_body=kwargs.get('html_message', None),
                        reply_to=kwargs.get('reply_to', None)
                    )
                    sent = True
                    result = 'Sent'

        # Send the email
        if not sent:
            custom_send_mail(subject, message, from_email, recipient_list, **kwargs)
            result = 'Sent'
            logger.info("Email sent successfully", extra={'recipient': recipient_list[0], 'subject': subject})

        EmailLog.objects.create(
            recipient=recipient_list[0],  # Assuming a single recipient
            subject=subject,
            result=result,
            sport_association=sport_association
        )

        # Update or create SentEmails record
        # today = timezone.now().date()
        # sent_emails, created = SentEmails.objects.get_or_create(date=today)
        # sent_emails.number_of_emails += 1
        # sent_emails.save()

        logger.info("Completed send_mail_async task", extra={'task_name': 'send_mail_async', 'result': result})
        print(f'Sent successful')
        return
    except Exception as exc:
        # Retry with exponential backoff
        logger.error("Failed to send email, retrying", extra={'retry_count': self.request.retries, 'delay': self.default_retry_delay}, exc_info=True)
        print(f'Failed to send email, retrying in {self.default_retry_delay} seconds')
        try:
            # Exponential backoff factor is 2 raised to the power of number of retries
            self.retry(exc=exc, countdown=int(self.default_retry_delay * (2 ** self.request.retries)))
        except MaxRetriesExceededError as e:
            result = f'Failed after maximum retries - {str(e)}'
            logger.error("Max retries exceeded for email", extra={'recipient': recipient_list[0], 'subject': subject}, exc_info=True)
            EmailLog.objects.create(
                recipient=recipient_list[0],
                subject=subject,
                result=result,
                sport_association=sport_association
            )
            print(result)
            # Handle the max retry exceeded condition