import logging
import datetime

from rest_framework import status
from rest_framework.response import Response

from application.models.user_models import EmailLog
from application.permissions import IsProPlanAssociation, IsTeamsPlanAssociation
from application.serializers.user_serializers import EmailLogSerializer
from application.utils.api_utils import is_valid_uuid
from core import settings
from core.settings import APP_HOST
from .models import Message, CommunicationConfiguration, SmsCreditPayment, MessageTransaction, AutomationWorkflow
from .serializers import CommunicationConfigurationSerializer, MessageSerializer, \
    CommunicationConfigurationPatchSerializer, SmsSerializer, SmsCreditPaymentSerializer, PostSerializer, \
    EmailSerializer, MessageTransactionSerializer, AutomationWorkflowSerializer
from core.middleware import IsAuthenticated
from rest_framework.decorators import api_view, permission_classes
from smsapi.client import SmsApiComClient
import stripe


logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def configuration_smtp_info(request):

    configuration = CommunicationConfiguration.objects.filter(
        sport_association=request.user.sport_association
    ).first()

    if not configuration and request.user.is_sport_association():
        configuration = CommunicationConfiguration.objects.create(
            sport_association=request.user.sport_association,
            email_smtp_port=587,
            email_encryption='TLS',
        )
    elif not configuration:
        return Response({'msg': 'info not found.'}, status=status.HTTP_404_NOT_FOUND)

    serializer = CommunicationConfigurationSerializer(configuration)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def configuration_smtp_verify(request):

    configuration = CommunicationConfiguration.objects.filter(
        sport_association=request.user.sport_association
    ).first()

    if not configuration:
        return Response({'msg': 'info not found.'}, status=status.HTTP_404_NOT_FOUND)

    # verify the SMTP settings
    verified, msg = configuration.verify_smtp()

    return Response({'verified': verified, 'msg': str(msg)}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def configuration_history_sms(request):

    # get the sms credit payments for the sport association filtered by only paid
    sms_credit_payments = SmsCreditPayment.objects.filter(
        sport_association=request.user.sport_association,
        paid=True
    ).order_by('-payment_date')

    # create the serializer
    serializer = SmsCreditPaymentSerializer(sms_credit_payments, many=True)

    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def communication_email_logs_list(request):

    # get the sms credit payments for the sport association filtered by only paid
    email_logs = EmailLog.objects.filter(
        sport_association=request.user.sport_association,
        sent_at__gte=datetime.datetime.now() - datetime.timedelta(days=90)
    ).order_by('-sent_at').iterator(chunk_size=100)

    # create the serializer
    serializer = EmailLogSerializer(email_logs, many=True)

    return Response({
        'today_count': EmailLog.objects.filter(
            sport_association=request.user.sport_association,
            sent_at__gte=datetime.datetime.now() - datetime.timedelta(days=1)
        ).count(),
        'total_count': len(serializer.data),
        'results': serializer.data,
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def configuration_send_sms(request):

    logger.info("Sending SMS", extra={'user_id': str(request.user.user_id), 'sport_association_id': str(request.user.sport_association.sport_association_id)})
    data = request.data

    if 'message_id' in data and data['message_id']:
        message = Message.objects.filter(message_id=data['message_id']).first()
        if not message:
            return Response({'msg': 'message not found.'}, status=status.HTTP_404_NOT_FOUND)
        data['message'] = message.message
        serializer = SmsSerializer(data=data)
        if not serializer.is_valid(raise_exception=True):
            return Response({'msg': 'invalid data.'}, status=status.HTTP_400_BAD_REQUEST)
    else:
        serializer = SmsSerializer(data=data)
        if not serializer.is_valid(raise_exception=True):
            return Response({'msg': 'invalid data.'}, status=status.HTTP_400_BAD_REQUEST)
        # create a Message and a MessageTransaction associated
        message = Message.objects.create(
            sport_association=request.user.sport_association,
            type=Message.SMS,
            message=serializer.data['message'],
        )

    configuration = CommunicationConfiguration.objects.filter(
        sport_association=request.user.sport_association
    ).first()
    if not configuration:
        return Response({'msg': 'info not found.'}, status=status.HTTP_404_NOT_FOUND)

    # check balance is enough
    sms_to_send = len(serializer.data['phone_number'].split(','))

    logger.debug("Checking SMS balance", extra={'sms_to_send': sms_to_send, 'current_balance': configuration.sms_balance, 'sport_association_id': str(request.user.sport_association.sport_association_id)})
    if configuration.sms_balance < sms_to_send:
        logger.warning("Insufficient SMS balance", extra={'sms_to_send': sms_to_send, 'current_balance': configuration.sms_balance})
        return Response({'msg': 'not enough sms balance.'}, status=status.HTTP_400_BAD_REQUEST)

    # verify phone_number is not a list of phone_numbers separated by comma
    # if it is, split the phone_numbers and create a MessageTransaction for each one
    if ',' in serializer.data['phone_number']:
        phone_numbers = serializer.data['phone_number'].split(',')
        for phone_number in phone_numbers:
            message_transaction = MessageTransaction.objects.create(
                message=message,
                recipient=phone_number,
            )
    else:
        # associate a MessageTransaction
        message_transaction = MessageTransaction.objects.create(
            message=message,
            recipient=serializer.data['phone_number'],
        )

    # send the sms
    logger.info("Calling SMS API", extra={'recipient_count': sms_to_send, 'message_id': str(message.message_id)})
    client = SmsApiComClient(access_token=settings.SMSAPI_TOKEN)
    send_results = client.sms.send(to=serializer.data['phone_number'], message=serializer.data['message'])

    sms_sent_count = 0
    for result in send_results:
        # decrease the sms balance
        if result.points > 0:
            configuration.sms_balance -= 1
            sms_sent_count += 1
        if result.error:
            logger.error("SMS sending error", extra={'result_id': result.id, 'error': result.error})
    configuration.save()
    logger.info("SMS sent successfully", extra={'sent_count': sms_sent_count, 'new_balance': configuration.sms_balance})
    return Response({'msg': 'sms sent.'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def configuration_send_email(request):

    logger.info("Sending email", extra={'user_id': str(request.user.user_id), 'sport_association_id': str(request.user.sport_association.sport_association_id)})
    data = request.data

    if 'message_id' in data and data['message_id']:
        message = Message.objects.filter(message_id=data['message_id']).first()
        if not message:
            return Response({'msg': 'message not found.'}, status=status.HTTP_404_NOT_FOUND)
        data['message'] = message.message
        data['subject'] = message.subject
        serializer = EmailSerializer(data=data)
        if not serializer.is_valid(raise_exception=True):
            return Response({'msg': 'invalid data.'}, status=status.HTTP_400_BAD_REQUEST)
    else:
        serializer = EmailSerializer(data=data)
        if not serializer.is_valid(raise_exception=True):
            return Response({'msg': 'invalid data.'}, status=status.HTTP_400_BAD_REQUEST)
        # create a Message and a MessageTransaction associated
        message = Message.objects.create(
            sport_association=request.user.sport_association,
            type=Message.EMAIL,
            message=serializer.data['message'],
            subject=serializer.data['subject'],
        )

    # verify email is not a list of emails separated by comma
    # if it is, split the emails and create a MessageTransaction for each one
    emails = serializer.data['email'].split(',')
    if ',' in serializer.data['email']:
        for email in emails:
            message_transaction = MessageTransaction.objects.create(
                message=message,
                recipient=email,
            )
    else:
        # associate a MessageTransaction
        message_transaction = MessageTransaction.objects.create(
            message=message,
            recipient=serializer.data['email'],
        )

    # send the email
    configuration = CommunicationConfiguration.objects.filter(
        sport_association=request.user.sport_association
    ).first()
    logger.info("Calling SMTP to send email", extra={'recipient_count': len(emails), 'subject': serializer.data['subject'], 'message_id': str(message.message_id)})
    result, msg = configuration.send_email(
        subject=serializer.data['subject'],
        body=serializer.data['message'],
        recipient_list=emails,
    )

    if result:
        logger.info("Email sent successfully", extra={'recipient_count': len(emails), 'message_id': str(message.message_id)})
    else:
        logger.error("Email sending failed", extra={'recipient_count': len(emails), 'error': msg, 'message_id': str(message.message_id)})
    return Response({'msg': msg, 'result': result}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def configuration_send_post(request):

    data = request.data
    serializer = PostSerializer(data=data)
    if not serializer.is_valid(raise_exception=True):
        return Response({'msg': 'invalid data.'}, status=status.HTTP_400_BAD_REQUEST)

    # post are on the wall, we need to create only one MessageTransaction
    message = Message.objects.create(
        sport_association=request.user.sport_association,
        type=Message.INSIDE_APP,
        message=serializer.data['message'],
    )

    # associate a MessageTransaction
    message_transaction = MessageTransaction.objects.create(
        message=message,
        recipient=request.user.sport_association.sport_association_id,
    )

    return Response({'msg': 'post sent.'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def configuration_buy_sms(request):

    logger.info("Purchasing SMS credits", extra={'user_id': str(request.user.user_id), 'sport_association_id': str(request.user.sport_association.sport_association_id)})
    data = request.data

    # get amount of sms to buy
    amount = data.get('amount', 0)

    if amount != 100 and amount != 200 and amount != 300:
        logger.warning("Invalid SMS purchase amount", extra={'amount': amount})
        return Response({'msg': 'invalid amount.'}, status=status.HTTP_400_BAD_REQUEST)

    # get the line item based on the amount
    line_item = None
    if amount == 100:
        line_item = settings.SMS_100_LINE_ITEM
    elif amount == 200:
        line_item = settings.SMS_200_LINE_ITEM
    elif amount == 300:
        line_item = settings.SMS_300_LINE_ITEM

    if not line_item:
        return Response({'msg': 'invalid amount.'}, status=status.HTTP_400_BAD_REQUEST)

    logger.info("Creating Stripe checkout session for SMS purchase", extra={'amount': amount, 'line_item': line_item})
    checkout_session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{
            'price': line_item,
            'quantity': 1,
        }],
        mode='payment',
        custom_fields=[
            {
                "key": "taxcode",
                "label": {"type": "custom", "custom": "Codice Fiscale / Partita IVA"},
                "type": "text",
            },
        ],
        success_url=f"https://{APP_HOST}/#/communication/configuration",
        cancel_url=f"https://{APP_HOST}/#/cancel",
        metadata={
            'sms_balance': int(amount),
            'sport_association': request.user.sport_association.sport_association_id,
            'denomination': request.user.sport_association.denomination,
        }
    )

    # get payment intent id
    checkout_session_id = checkout_session.id

    logger.info("Stripe checkout session created", extra={'checkout_session_id': checkout_session_id, 'amount': amount})
    # create the payment in the db
    SmsCreditPayment.objects.create(
        sport_association=request.user.sport_association,
        amount=amount,
        payment_intent_id=checkout_session_id,
    )
    # return with the url to redirect the user
    return Response({'url': checkout_session.url}, status=status.HTTP_200_OK)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def configuration_smtp_update(request):

    configuration = CommunicationConfiguration.objects.filter(
        sport_association=request.user.sport_association
    ).first()

    if not configuration:
        return Response({'msg': 'info not found.'}, status=status.HTTP_404_NOT_FOUND)

    serializer = CommunicationConfigurationPatchSerializer(configuration, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response({'msg': 'info not valid.'}, status=status.HTTP_400_BAD_REQUEST)

    serializer.save()

    return Response({'msg': 'info updated.'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def communication_messages_list(request):

    request.user.is_sport_association()

    messages = Message.objects.filter(
        sport_association=request.user.sport_association
    ).order_by('-created_at')

    serializer = MessageSerializer(messages, many=True)

    return Response(serializer.data, status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def communication_messages_detail(request, message_id):

    request.user.is_sport_association()
    is_valid_uuid(message_id)

    # get the message and its message transactions
    message = Message.objects.filter(
        sport_association=request.user.sport_association,
        message_id=message_id,
    ).first()

    if not message:
        return Response({'msg': 'message not found.'}, status=status.HTTP_404_NOT_FOUND)

    message_transactions = MessageTransaction.objects.filter(
        message=message
    ).order_by('-created_at')

    if not message_transactions:
        return Response({'msg': 'message not found.'}, status=status.HTTP_404_NOT_FOUND)

    serializer = MessageTransactionSerializer(message_transactions, many=True)

    return Response(serializer.data, status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def communication_messages_delete(request, message_id):

    request.user.is_sport_association()
    is_valid_uuid(message_id)

    # get the message and its message transactions
    message = Message.objects.filter(
        sport_association=request.user.sport_association,
        message_id=message_id,
    ).first()
    message.delete()

    return Response({'msg': 'message deleted.'}, status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def communication_messages_add(request):

    request.user.is_sport_association()
    data = request.data
    data['sport_association'] = request.user.sport_association.sport_association_id

    # add the message
    serializer = MessageSerializer(data=data)

    if not serializer.is_valid():
        return Response({'msg': 'message not valid.'}, status=status.HTTP_400_BAD_REQUEST)

    serializer.save(sport_association=request.user.sport_association)

    return Response({'msg': 'message added.'}, status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def communication_workflows_list(request):

    request.user.is_sport_association()

    workflows = AutomationWorkflow.objects.filter(
        sport_association=request.user.sport_association
    ).order_by('-created_at')

    serializer = AutomationWorkflowSerializer(workflows, many=True)

    return Response(serializer.data, status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def communication_workflows_add(request):

    request.user.is_sport_association()
    data = request.data
    data['sport_association'] = request.user.sport_association.sport_association_id

    # add the workflow
    serializer = AutomationWorkflowSerializer(data=data)

    if not serializer.is_valid():
        logger.error(serializer.errors)
        return Response({'msg': 'workflow not valid.'}, status=status.HTTP_400_BAD_REQUEST)

    workflow = serializer.save(sport_association=request.user.sport_association)

    return Response({'msg': 'workflow added.', 'workflow_id': workflow.automation_workflow_id}, status.HTTP_200_OK)



@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def communication_workflows_delete(request, workflow_id):

    request.user.is_sport_association()
    is_valid_uuid(workflow_id)

    # get the workflow and delete it
    workflow = AutomationWorkflow.objects.filter(
        sport_association=request.user.sport_association,
        automation_workflow_id=workflow_id,
    ).first()
    workflow.delete()

    return Response({'msg': 'workflow deleted.'}, status.HTTP_200_OK)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def communication_workflows_update(request, workflow_id):

    request.user.is_sport_association()
    is_valid_uuid(workflow_id)

    # get the workflow and delete it
    workflow = AutomationWorkflow.objects.filter(
        sport_association=request.user.sport_association,
        automation_workflow_id=workflow_id,
    ).first()

    if not workflow:
        return Response({'msg': 'workflow not found.'}, status=status.HTTP_404_NOT_FOUND)

    is_enabled = workflow.enabled

    serializer = AutomationWorkflowSerializer(workflow, data=request.data, partial=True)

    if not serializer.is_valid():
        return Response({'msg': 'workflow not valid.'}, status=status.HTTP_400_BAD_REQUEST)

    serializer.save()
    logger.info(f"workflow updated: {serializer.data} - is_enabled: {is_enabled}")
    if not is_enabled and serializer.data['enabled']:
        logger.info('workflow is enabled, triggering it')
        try:
            # check trigger type
            trigger_type = workflow.automation_tree[0]['value']
            if trigger_type == 'cron' and \
                    'triggered' in workflow.automation_tree[0]['data'] and \
                    workflow.automation_tree[0]['data']['triggered'] is True:
                logger.info('workflow is already triggered by cron')
                # reset the triggered flag
                workflow.automation_tree[0]['data']['triggered'] = False
                workflow.save()
        except Exception as e:
            logger.error(f"error in trigger type: {e}")
        # trigger the workflow
        workflow.trigger()

    return Response({'msg': 'workflow updated.'}, status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def communication_workflows_details(request, workflow_id):

    request.user.is_sport_association()
    is_valid_uuid(workflow_id)

    # get the workflow and delete it
    workflow = AutomationWorkflow.objects.filter(
        sport_association=request.user.sport_association,
        automation_workflow_id=workflow_id,
    ).first()

    if not workflow:
        return Response({'msg': 'workflow not found.'}, status=status.HTTP_404_NOT_FOUND)

    serializer = AutomationWorkflowSerializer(workflow)

    return Response(serializer.data, status.HTTP_200_OK)
