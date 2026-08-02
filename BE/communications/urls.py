from django.urls import path

from communications.views import configuration_smtp_update, configuration_smtp_info, configuration_smtp_verify, \
    configuration_send_sms, configuration_buy_sms, configuration_history_sms, configuration_send_email, \
    configuration_send_post, communication_messages_list, communication_messages_detail, communication_messages_delete, \
    communication_messages_add, communication_workflows_list, communication_workflows_delete, \
    communication_workflows_update, communication_workflows_add, communication_workflows_details, \
    communication_email_logs_list

urlpatterns = [
    path(r'communications/settings/smtp/update', configuration_smtp_update),
    path(r'communications/settings/smtp/info', configuration_smtp_info),
    path(r'communications/settings/smtp/verify', configuration_smtp_verify),
    path(r'communications/send/sms', configuration_send_sms),
    path(r'communications/send/email', configuration_send_email),
    path(r'communications/send/post', configuration_send_post),
    path(r'communications/buy/sms', configuration_buy_sms),
    path(r'communications/history/sms', configuration_history_sms),
    path(r'communications/messages/add', communication_messages_add),
    path(r'communications/messages/list', communication_messages_list),
    path(r'communications/messages/<str:message_id>/detail', communication_messages_detail),
    path(r'communications/messages/<str:message_id>/delete', communication_messages_delete),
    path(r'communications/workflows/list', communication_workflows_list),
    path(r'communications/workflows/add', communication_workflows_add),
    path(r'communications/workflows/<str:workflow_id>/delete', communication_workflows_delete),
    path(r'communications/workflows/<str:workflow_id>/update', communication_workflows_update),
    path(r'communications/workflows/<str:workflow_id>/details', communication_workflows_details),
    path(r'communications/email-logs/list', communication_email_logs_list),
]