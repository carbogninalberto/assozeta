import os
from celery import Celery
from celery.schedules import crontab

# set the default Django settings module for the 'celery' program.
# this is also used in manage.py
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# Fix for macOS + Python 3.13 fork() crashes with SSL/email libraries
# See: https://github.com/celery/celery/issues/7007
os.environ.setdefault('OBJC_DISABLE_INITIALIZE_FORK_SAFETY', 'YES')

app = Celery('core')

# Using a string here means the worker don't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()

# Use solo pool on macOS to avoid fork() issues with Python 3.13
# For production on Linux, you can use 'prefork' or 'gevent'
import platform
if platform.system() == 'Darwin':  # macOS
    app.conf.worker_pool = 'solo'

# Add task annotations for rate limiting
app.conf.task_annotations = {
    'send_reminders': {'rate_limit': '60/m'},
    'clear_expired_subscription_tokens': {'rate_limit': '60/m'},
    'generate_coupon_if_not_exists': {'rate_limit': '4/m'},
    'change_type_of_stripe_payments': {'rate_limit': '30/m'},
    'mark_as_paid_payments_checking_stripe': {'rate_limit': '3/m'},
    'auto_mark_attendance': {'rate_limit': '4/m'},
    'renew_memberships_payments': {'rate_limit': '1/m'},
    'association_quote_assign': {'rate_limit': '1/m'},
}

app.conf.beat_schedule = {
    'renew-memberships-payments': {
        'task': 'renew_memberships_payments',
        'schedule': crontab(hour='0', minute='10'),
        'args': ()
    },
    'clear-expired-subscription-tokens': {
        'task': 'clear_expired_subscription_tokens',
        'schedule': crontab(minute='*'),
        'args': ()
    },
    'reset-communication-daily-email-balance': {
      'task': 'reset_communication_daily_email_balance',
        'schedule': crontab(hour='0', minute='0'),
        'args': ()
    },
    'delete-users-with-request': {
            'task': 'delete_users_with_request',
        'schedule': crontab(hour='7', minute='30'),
        'args': ()
    },
    'association-quote-assign': {
        'task': 'association_quote_assign',
        'schedule': crontab(hour='3', minute='0'),
        'args': ()
    },
    'send-reminders': {
        'task': 'send_reminders',
        'schedule': crontab(minute='*'),
        'args': ()
    },
    'update-age-of-associates': {
        'task': 'update_age_of_associates',
        'schedule': crontab(hour='0', minute='0'),
        'args': ()
    },
    'generate-coupon-if-not-exists': {
        'task': 'generate_coupon_if_not_exists',
        'schedule': crontab(minute='*/15'),
        'args': ()
    },
    'change-type-of-stripe-payments': {
        'task': 'change_type_of_stripe_payments',
        'schedule': crontab(minute='*/45'),
        'args': ()
    },
    'mark-as-paid-payments-checking-stripe': {
        'task': 'mark_as_paid_payments_checking_stripe',
        'schedule': crontab(minute='*/20'),
        'args': ()
    },
    'clear-deleted-subscription': {
        'task': 'clear_deleted_subscription',
        'schedule': crontab(hour='0', minute='15'),
        'args': ()
    },
    'auto-move-users-to-base-plan': {
        'task': 'auto_move_users_to_base_plan',
        'schedule': crontab(hour='0', minute='0'),
        'args': ()
    },
    'nurturing-warm-leads': {
        'task': 'nurturing_warm_leads',
        'schedule': crontab(hour='10', minute='20'),
        'args': ()
    },
    'auto-archive-subscription': {
        'task': 'auto_archive_subscription',
        'schedule': crontab(
            day_of_month='1-4',
            month_of_year='1,6,9',
            hour='1',
            minute='0'
        ),
        'args': ()
    },
    'auto-mark-attendance': {
        'task': 'auto_mark_attendance',
        'schedule': crontab(minute='*/15'),
        'args': ()
    },
    'auto-delete-orphan-medical-certificate': {
        'task': 'auto_delete_orphan_medical_certificate',
        'schedule': crontab(hour='3', minute='0'),
        'args': ()
    },
    'send-expiring-certificate-email': {
        'task': 'send_expiring_certificate_email',
        'schedule': crontab(hour='6', minute='0'),
        'args': ()
    },
    'send-expired-certificate-email': {
        'task': 'send_expired_certificate_email',
        'schedule': crontab(hour='7', minute='0'),
        'args': ()
    },
    'send-user-partial-registration-email': {
        'task': 'send_user_partial_registration_email',
        'schedule': crontab(hour='8', minute='0'),
        'args': ()
    },
    'delete-old-audit-logs': {
        'task': 'delete_old_audit_logs',
        'schedule': crontab(hour='0', minute='0'),
        'args': ()
    },
    'cleanup-old-exports': {
        'task': 'cleanup_old_exports',
        'schedule': crontab(hour='2', minute='30'),  # Run daily at 2:30 AM
        'args': ()
    },
}
# We used CELERY_BROKER_URL in settings.py instead of:
# app.conf.broker_url = ''

# We used CELERY_BEAT_SCHEDULER in settings.py instead of:
# app.conf.beat_scheduler = ''django_celery_beat.schedulers.DatabaseScheduler'
