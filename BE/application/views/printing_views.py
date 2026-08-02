"""
@ copyright: Bakney SRL
"""
import logging

from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

from application.utils.printing import expiring_medical_certificates, expired_medical_certificates, \
    empty_medical_certificates, exempt_medical_certificates, expiring_subscriptions, expiring_memberships, \
    all_subscriptions, not_paid_quotes_subscriptions, not_paid_courses_subscriptions, expired_payments, subscriptions_with_all_payments
from core.middleware import IsAuthenticated
from application.models.user_models import User


logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def printing_generate(request):
    logger.info("Generating print/report", extra={
        'user_id': str(request.user.user_id),
        'print_type': request.data.get('type'),
        'format': request.data.get('format')
    })

    if request.user.role == User.ATHLETE:
        logger.warning("Unauthorized print generation - athlete role", extra={
            'user_id': str(request.user.user_id),
            'role': request.user.role
        })
        # not allowed
        return Response(status=status.HTTP_403_FORBIDDEN)

    if 'type' not in request.data.keys() or \
            'format' not in request.data.keys():

        missing_parameters = []
        if 'type' not in request.data.keys():
            missing_parameters.append('type')
        if 'format' not in request.data.keys():
            missing_parameters.append('format')
        missing_parameters_text = ', '.join(missing_parameters)
        logger.warning("Missing required parameters for print generation", extra={
            'user_id': str(request.user.user_id),
            'missing_parameters': missing_parameters
        })
        return Response({
            'msg': f"Mancano alcuni parametri obbligatori:{missing_parameters_text}"
        }, status=status.HTTP_400_BAD_REQUEST)

    try:
        print_type = request.data['type']
        logger.debug("Processing print generation", extra={
            'user_id': str(request.user.user_id),
            'sport_association_id': str(request.user.sport_association.sport_association_id),
            'type': print_type
        })

        if print_type == 'expiring_medical_certificates':
            file, filename = expiring_medical_certificates(sport_association=request.user.sport_association, data=request.data)
            logger.info("Expiring medical certificates report generated", extra={
                'user_id': str(request.user.user_id),
                'report_filename': filename
            })
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)

        elif print_type == 'expired_medical_certificates':
            file, filename = expired_medical_certificates(sport_association=request.user.sport_association, data=request.data)
            logger.info("Expired medical certificates report generated", extra={
                'user_id': str(request.user.user_id),
                'report_filename': filename
            })
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)

        elif print_type == 'empty_medical_certificates':
            file, filename = empty_medical_certificates(sport_association=request.user.sport_association, data=request.data)
            logger.info("Empty medical certificates report generated", extra={
                'user_id': str(request.user.user_id),
                'report_filename': filename
            })
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)

        elif print_type == 'exempt_medical_certificates':
            file, filename = exempt_medical_certificates(sport_association=request.user.sport_association, data=request.data)
            logger.info("Exempt medical certificates report generated", extra={
                'user_id': str(request.user.user_id),
                'report_filename': filename
            })
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)

        elif print_type == 'expiring_subscriptions':
            file, filename = expiring_subscriptions(sport_association=request.user.sport_association, data=request.data)
            logger.info("Expiring subscriptions report generated", extra={
                'user_id': str(request.user.user_id),
                'report_filename': filename
            })
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)

        elif print_type == 'expiring_memberships':
            file, filename = expiring_memberships(sport_association=request.user.sport_association, data=request.data)
            logger.info("Expiring memberships report generated", extra={
                'user_id': str(request.user.user_id),
                'report_filename': filename
            })
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)

        elif print_type == 'all_subscriptions':
            file, filename = all_subscriptions(sport_association=request.user.sport_association, data=request.data)
            logger.info("All subscriptions report generated", extra={
                'user_id': str(request.user.user_id),
                'report_filename': filename
            })
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)

        elif print_type == 'not_paid_quotes_subscriptions':
            file, filename = not_paid_quotes_subscriptions(sport_association=request.user.sport_association, data=request.data)
            logger.info("Not paid quotes subscriptions report generated", extra={
                'user_id': str(request.user.user_id),
                'report_filename': filename
            })
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)

        elif print_type == 'not_paid_courses_subscriptions':
            file, filename = not_paid_courses_subscriptions(sport_association=request.user.sport_association, data=request.data)
            logger.info("Not paid courses subscriptions report generated", extra={
                'user_id': str(request.user.user_id),
                'report_filename': filename
            })
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)

        elif print_type == 'expired_payments':
            file, filename = expired_payments(sport_association=request.user.sport_association, data=request.data)
            logger.info("Expired payments report generated", extra={
                'user_id': str(request.user.user_id),
                'report_filename': filename
            })
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)


        elif print_type == 'subscriptions_with_all_payments':
            file, filename = subscriptions_with_all_payments(sport_association=request.user.sport_association, data=request.data)
            logger.info("Subscriptions with all payments report generated", extra={
                'user_id': str(request.user.user_id),
                'report_filename': filename
            })
            return Response({
                "file": file,
                "filename": filename
            }, status=status.HTTP_200_OK)

        logger.warning("Invalid print type requested", extra={
            'user_id': str(request.user.user_id),
            'print_type': print_type
        })
        return Response({
            'msg': 'Tipo stampa non valido',
        }, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        logger.error("Error generating print/report", extra={
            'user_id': str(request.user.user_id),
            'print_type': request.data.get('type'),
            'error': str(e)
        }, exc_info=True)
        return Response({
            'msg': 'Errore nella generazione della stampa.',
        }, status=status.HTTP_400_BAD_REQUEST)

