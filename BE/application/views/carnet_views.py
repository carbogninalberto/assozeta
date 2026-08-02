"""
@ copyright: Bakney srl
"""
import logging

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from core.middleware import IsAuthenticated

from application.models import Carnet
from application.models.carnet_models import CarnetSubscription
from application.models.courses_models import CourseSubscription, CourseSubscriptionInstallment, Course
from application.models.payment_models import Payment
from application.models.subscriptions_models import Subscription
from application.models.user_models import SportAssociation, User
from application.serializers.carnet_serializers import CarnetListSerializer, CarnetAddSerializer, \
    CarnetListInfoSerializer, CarnetSubscriptionSerializer, CarnetUpdateSerializer
from application.serializers.courses_serializers import CourseSimpleSerializer
from application.utils.api_utils import is_valid_uuid

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def carnet_list(request):


    serialized_carnets = []
    if request.user.role == User.ASSOCIATION:
        sport_association = SportAssociation.objects.get(user=request.user)
        carnets = Carnet.objects.filter(sport_association=sport_association)
        serialized_carnets = CarnetListSerializer(carnets, many=True)
    elif request.user.role == User.ATHLETE:
        carnets = CarnetSubscription.objects.filter(Q(user_id=request.user) | Q(subscription__associate__user=request.user)).order_by('-creation_date')
        serialized_carnets = CarnetSubscriptionSerializer(carnets, many=True)

    return Response({'data': serialized_carnets.data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def carnet_add(request):
    """
    Add a new carnet to the association
    """

    if request.user.role != User.ASSOCIATION:
        return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    logger.info("Adding new carnet", extra={'user_id': str(request.user.user_id)})
    data = request.data

    # check data validity with CarnetAddSerializer
    serializer = CarnetAddSerializer(data=data)
    if not serializer.is_valid():
        logger.warning("Carnet validation failed", extra={'errors': str(serializer.errors)})
        return Response({'msg': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    # save carnet
    carnet = serializer.save()
    carnet.sport_association = SportAssociation.objects.get(user=request.user)
    carnet.save()

    # add subscriptions to carnet
    for subscription in data['subscriptions']:
        if Subscription.objects.filter(subscription_id=subscription).exists():
            carnet_assign(request._request, carnet.carnet_id, subscription)

    logger.info("Carnet added successfully", extra={'carnet_id': str(carnet.carnet_id), 'title': carnet.title})
    return Response({'msg': 'Carnet added successfully.'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def carnet_subscription_list(request):
    """
    Set carnet subscription to disabled
    """

    # get subscription_id from query params (if present)
    subscription_id = request.query_params.get('subscription_id', None)

    if request.user.role != User.ASSOCIATION:
        if subscription_id not in [str(x) for x in Subscription.objects.filter(user=request.user).values_list(
                'subscription_id', flat=True)]:
            return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
        else:
            carnet_subscriptions = CarnetSubscription.objects.filter(
                subscription__subscription_id=subscription_id)

    if subscription_id:
        # get subscription
        subscription = Subscription.objects.get(subscription_id=subscription_id)
        carnet_subscriptions = CarnetSubscription.objects.filter(
            Q(subscription__subscription_id=subscription_id) | Q(subscription__associate=subscription.associate))

    # now serialize the data by extracting:
    # - carnet info
    # - course info (if present)
    # - lessons info
    data = []

    for carnet_subscription in carnet_subscriptions:
        courses = []
        for course_sub in carnet_subscription.course_subscription.all():
            courses.append({
                "course_id": course_sub.course.course_id,
                "course_title": course_sub.course.title,
                "course_subscription_id": course_sub.course_subscription_id
            })
        data.append({
            'carnet_subscription_id': carnet_subscription.carnet_subscription_id,
            'carnet_id': carnet_subscription.carnet_id.carnet_id,
            'title': carnet_subscription.carnet_id.title,
            'creation_date': carnet_subscription.creation_date,
            'subscription_id': carnet_subscription.subscription.subscription_id,
            'disabled': carnet_subscription.disabled,
            'payment': {
                'payment_id': carnet_subscription.payment.payment_id if carnet_subscription.payment else None,
                'amount': "{:.2f}".format(
                    carnet_subscription.payment.amount \
                        if carnet_subscription.payment \
                        else carnet_subscription.carnet_id.fee
                ),
                'creation_date': carnet_subscription.payment.creation_date if carnet_subscription.payment else None,
            },
            'course': courses,
            'lessons_counter': carnet_subscription.meta['lessons_counter'],
            'lessons_left': carnet_subscription.meta['lessons_left'],
        })
    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def carnet_subscription_disable(request, uid):
    """
    Set carnet subscription to disabled
    """

    if request.user.role != User.ASSOCIATION:
        return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    if not uid:
        return Response({'msg': 'Missing carnet subscription uid.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        carnet = CarnetSubscription.objects.get(
            carnet_subscription_id=uid,
            subscription__sport_association__user=request.user
        )
        carnet.disabled = True
        carnet.save()
        return Response({'msg': 'Carnet subscription disabled.'}, status=status.HTTP_200_OK)
    except CarnetSubscription.DoesNotExist:
        return Response({'msg': 'Carnet subscription not found.'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def carnet_subscription_enable(request, uid):
    """
    Set carnet subscription to enabled
    """

    if request.user.role != User.ASSOCIATION:
        return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    if not uid:
        return Response({'msg': 'Missing carnet subscription uid.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        carnet = CarnetSubscription.objects.get(
            carnet_subscription_id=uid,
            subscription__sport_association__user=request.user
        )
        carnet.disabled = False
        carnet.save()
        return Response({'msg': 'Carnet subscription enabled.'}, status=status.HTTP_200_OK)
    except CarnetSubscription.DoesNotExist:
        return Response({'msg': 'Carnet subscription not found.'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def carnet_subscription_update(request, uid):
    """
    Set carnet subscription to enabled
    """

    if request.user.role != User.ASSOCIATION:
        return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    if not uid:
        return Response({'msg': 'Missing carnet subscription uid.'}, status=status.HTTP_400_BAD_REQUEST)

    if 'lessons_left' not in request.data.keys():
        return Response({'msg': 'Missing lessons left.'}, status=status.HTTP_400_BAD_REQUEST)

    is_valid_uuid(uid)

    try:
        carnet = CarnetSubscription.objects.get(
            carnet_subscription_id=uid,
            subscription__sport_association__user_id=request.user.user_id
        )

        # check lessons_left is < than the lessons_number
        if int(request.data['lessons_left']) > carnet.meta['lessons_counter'] or \
                int(request.data['lessons_left']) < 0:
            return Response({'msg': 'Lessons left cannot be > than lessons_number.'}, status=status.HTTP_400_BAD_REQUEST)

        carnet.meta['lessons_left'] = int(request.data['lessons_left'])
        carnet.save()
        return Response({'msg': 'Carnet subscription updated.'}, status=status.HTTP_200_OK)
    except CarnetSubscription.DoesNotExist:
        return Response({'msg': 'Carnet subscription not found.'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def carnet_subscription_topup(request, uid):
    """
    Create a new carnet with the same subscription
    """

    if not uid:
        return Response({'msg': 'Missing carnet subscription uid.'}, status=status.HTTP_400_BAD_REQUEST)

    is_valid_uuid(uid)

    logger.info("Topping up carnet", extra={'user_id': str(request.user.user_id), 'carnet_subscription_id': uid})
    try:
        if request.user.is_sport_association(raise_exception=False):
            old_carnet = CarnetSubscription.objects.get(
                carnet_subscription_id=uid,
                subscription__sport_association__user_id=request.user.user_id
            )
        else:
            # if user athlete let's see if he has the right to top up the carnet
            old_carnet = CarnetSubscription.objects.get(
                carnet_subscription_id=uid
            )
            if old_carnet.subscription.user != request.user:
                logger.warning("Unauthorized carnet topup attempt", extra={'user_id': str(request.user.user_id), 'carnet_subscription_id': uid})
                return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

        logger.debug("Creating payment for carnet topup", extra={'amount': str(old_carnet.carnet_id.fee), 'carnet_id': str(old_carnet.carnet_id.carnet_id)})
        # create a payment for the carnet
        payment = Payment.objects.create(
            user=old_carnet.subscription.user,
            associate=old_carnet.subscription.associate,
            amount=old_carnet.carnet_id.fee,
            subject=Payment.COURSE,
            creation_date=timezone.now(),
            sport_association=old_carnet.carnet_id.sport_association,
        )
        payment.save()

        carnet = CarnetSubscription.objects.create(
            carnet_id=old_carnet.carnet_id,
            user_id=old_carnet.user_id,
            subscription=old_carnet.subscription,
            payment=payment,
            meta={
                'lessons_counter': old_carnet.carnet_id.lessons_number,
                'lessons_left': old_carnet.carnet_id.lessons_number,
                'lessons_registry': []
            }
        )

        for course_subscription in old_carnet.course_subscription.all():
            carnet.course_subscription.add(course_subscription)

        carnet.save()
        logger.info("Carnet topped up successfully", extra={'carnet_subscription_id': str(carnet.carnet_subscription_id), 'payment_id': str(payment.payment_id)})
        return Response({'msg': 'Carnet topped up.'}, status=status.HTTP_200_OK)
    except CarnetSubscription.DoesNotExist as e:
        logger.error("Carnet not found for topup", extra={'carnet_subscription_id': uid}, exc_info=True)
        return Response({'msg': 'Error in topping up carnet.'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def carnet_subscription_delete(request, uid, uid_course):
    """
    Set carnet subscription delete
    """

    if request.user.role != User.ASSOCIATION:
        return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    if not uid:
        return Response({'msg': 'Missing carnet subscription uid.'}, status=status.HTTP_400_BAD_REQUEST)

    if not uid_course:
        return Response({'msg': 'Missing course subscription uid.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        carnet_subscription = CarnetSubscription.objects.get(
            carnet_subscription_id=uid
        )
        # remove course_subscription
        carnet_subscription.course_subscription.remove(uid_course)
        # save carnet_subscription
        carnet_subscription.save()

        return Response({'msg': 'Carnet subscription deleted.'}, status=status.HTTP_200_OK)
    except CarnetSubscription.DoesNotExist:
        return Response({'msg': 'Carnet subscription not found.'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def carnet_update(request, uid):
    """
    Update a carnet
    """

    if request.user.role != User.ASSOCIATION:
        return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    if not uid:
        return Response({'msg': 'Missing carnet uid.'}, status=status.HTTP_400_BAD_REQUEST)

    data = request.data
    serializer = CarnetUpdateSerializer(data=data)

    if not serializer.is_valid():
        return Response({'msg': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    try:
        carnet = Carnet.objects.get(carnet_id=uid)
        # update carnet
        carnet.title = serializer.validated_data['title']
        carnet.description = serializer.validated_data['description']
        carnet.fee = serializer.validated_data['fee']
        carnet.lessons_number = serializer.validated_data['lessons_number']
        carnet.public = serializer.validated_data['public']
        carnet.save()
        return Response({'msg': 'Carnet updated successfully.'}, status=status.HTTP_200_OK)
    except Carnet.DoesNotExist:
        return Response({'msg': 'Carnet not found.'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def carnet_delete(request, uid):
    """
    Delete a carnet
    """

    if request.user.role != User.ASSOCIATION:
        return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    if not uid:
        return Response({'msg': 'Missing carnet uid.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        carnet = Carnet.objects.get(carnet_id=uid)
        carnet.delete()
        return Response({'msg': 'Carnet deleted successfully.'}, status=status.HTTP_200_OK)
    except Carnet.DoesNotExist:
        return Response({'msg': 'Carnet not found.'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def carnet_info(request, uid):
    """
    Info about a carnet
    """

    if request.user.role != User.ASSOCIATION:
        return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    if not uid:
        return Response({'msg': 'Missing carnet uid.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        carnet = Carnet.objects.get(carnet_id=uid)
        serialized_carnet = CarnetListInfoSerializer(carnet)
        data = serialized_carnet.data
        data['subscriptions'] = []
        # loop through carnetsubscriptions and add the user info
        carnet_subscriptions = CarnetSubscription.objects.filter(carnet_id=carnet).order_by('-creation_date')
        for sub in carnet_subscriptions:
            sub_data = CarnetSubscriptionSerializer(sub).data
            sub_data['courses'] = []
            course_subscriptions = CourseSubscription.objects.filter(
                subscription=sub.subscription
            )
            for course_sub in course_subscriptions:
                sub_data['courses'].append(CourseSimpleSerializer(course_sub.course).data)
            data['subscriptions'].append(sub_data)

        return Response({'data': data}, status=status.HTTP_200_OK)
    except Carnet.DoesNotExist:
        return Response({'msg': 'Carnet not found.'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def carnet_replace(request, uid, uid_subscription):
    """
    Add a new carnet to the association
    """

    if request.user.role != User.ASSOCIATION:
        return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    # get sport_association
    sport_association = SportAssociation.objects.get(user=request.user)

    # read request data and get course_subscription
    data = request.data
    # get course_subscription_id and check if it exists, if not keep it None
    course_subscription = None
    if 'course_subscription_id' in data:
        course_subscription = CourseSubscription.objects.filter(
            course_subscription_id=data['course_subscription_id'],
            course__sport_association=sport_association,
        ).first()

    if course_subscription:
        # delete all installments related to the subscription
        installments = CourseSubscriptionInstallment.objects.filter(
            course_subscription=course_subscription,
            paid=False,
        )
        if installments:
            for installment in installments:
                if installment.payment is not None:
                    installment.payment.delete()
                installment.delete()
        # delete unpaid payment otherwise they become orphan payments
        payment = Payment.objects.filter(
            payment_id=course_subscription.payment_id,
            paid=False,
        ).first()
        if payment is not None:
            course_subscription.payment.delete()

    carnet_assign(request._request, uid, uid_subscription)

    return Response({'msg': 'Carnet added successfully.'}, status=status.HTTP_200_OK)


@api_view(['DELETE'])
def carnet_unassign(request, uid, uid_subscription):
    """
    Remove a carnet from a subscription and delete the
    unpaid payments
    """

    if request.user.role != User.ASSOCIATION:
        return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    # get sport_association

    # get carnet_subscription
    carnet_subscription = CarnetSubscription.objects.filter(
        carnet_subscription_id=uid_subscription,
    ).first()

    if carnet_subscription:
        # delete the unpaid payment
        if carnet_subscription.payment is not None and \
                not carnet_subscription.payment.paid:
            carnet_subscription.payment.delete()
            carnet_subscription.payment = None
        # delete the carnet_subscription
        carnet_subscription.delete()
        return Response({'msg': 'Carnet removed successfully.'}, status=status.HTTP_200_OK)
    else:
        return Response({'msg': 'Carnet not found.'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def carnet_assign(request, uid, uid_subscription):
    """
    Add a new carnet to a subscription
    """

    # get subscription by uid
    subscription = Subscription.objects.filter(subscription_id=uid_subscription).first()
    if not subscription:
        return Response({'msg': 'Subscription not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.user.role != User.ASSOCIATION and subscription.user != request.user:
        return Response({'msg': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    if not uid:
        return Response({'msg': 'Missing carnet uid.'}, status=status.HTTP_400_BAD_REQUEST)

    if not uid_subscription:
        return Response({'msg': 'Missing subscription uid.'}, status=status.HTTP_400_BAD_REQUEST)

    # get sport association by user if user is an association
    if request.user.role == User.ASSOCIATION:
        sport_association = SportAssociation.objects.filter(user=request.user).first()
    else:
        sport_association = subscription.sport_association
    if not sport_association:
        return Response({'msg': 'Sport association not found.'}, status=status.HTTP_404_NOT_FOUND)


    # get carnet by uid for the association
    carnet = Carnet.objects.filter(carnet_id=uid, sport_association=sport_association).first()
    if not carnet:
        return Response({'msg': 'Carnet not found.'}, status=status.HTTP_404_NOT_FOUND)

    # read request data and get course_subscription
    data = request.data
    # get course_subscription_id and check if it exists, if not keep it None
    course_subscription = None
    if 'course_subscription_id' in data:
        course_subscription = CourseSubscription.objects.filter(
            course_subscription_id=data['course_subscription_id']
        ).first()

    if 'carnet_subscription_id' in data:
        carnet_subscription = CarnetSubscription.objects.filter(
            carnet_subscription_id=data['carnet_subscription_id']
        ).first()
        if course_subscription is None and 'course_id' in data:
            course = Course.objects.filter(course_id=data['course_id']).first()
            course_subscription = CourseSubscription.objects.filter(
                course=course,
                subscription=subscription,
            ).first()

        if course_subscription is None:
            return Response({'msg': 'Course subscription not found.'}, status=status.HTTP_404_NOT_FOUND)

        carnet_subscription.course_subscription.add(course_subscription)
        carnet_subscription.save()
        return Response({
            'msg': 'Carnet assigned successfully.',
            'course_subscription_id': course_subscription.course_subscription_id if course_subscription is not None else None
        }, status=status.HTTP_200_OK)

    # create a payment for the carnet
    payment = Payment.objects.create(
        user=subscription.user,
        associate=subscription.associate,
        amount=carnet.fee,
        subject=Payment.COURSE,
        creation_date=timezone.now(),
        sport_association=sport_association,
    )
    payment.save()

    # assign carnet to subscription
    carnet_subscription = CarnetSubscription.objects.create(
        carnet_id=carnet,
        user_id=subscription.user,
        subscription=subscription,
        payment=payment,
        meta={
            'lessons_counter': carnet.lessons_number,
            'lessons_left': carnet.lessons_number,
            'lessons_registry': []
        }
    )

    if course_subscription is not None and course_subscription not in carnet_subscription.course_subscription.all():
        carnet_subscription.course_subscription.add(course_subscription)

    carnet_subscription.save()

    return Response({
        'msg': 'Carnet assigned successfully.',
        'course_subscription_id': course_subscription.course_subscription_id if course_subscription is not None else None
    }, status=status.HTTP_200_OK)
