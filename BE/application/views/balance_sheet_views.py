"""
@ copyright: Bakney SRL
"""
from datetime import datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes

from application.utils.balance_sheet_utils import generate_balance_sheet
from application.utils.printing import export_balance_sheet
from core.middleware import IsAuthenticated
from rest_framework.response import Response
from application.models import BalanceSheet
from application.models.balance_sheet_models import CustomAccounts, CustomAccountsTransfer
from application.models.user_models import SportAssociation, User

from application.permissions import IsProPlanAssociation, IsTeamsPlanAssociation
from application.serializers.auth_serializers import SportAssociationSerializer
from application.serializers.balance_sheet import CustomAccountSerializer, CustomAccountTransferSerializer, \
    CustomAccountTransferInfoSerializer
from application.utils.api_utils import BalanceSheetData
import logging

logger = logging.getLogger(__name__)


@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def balance_sheet(request):
    user = request.user
    logger.info("Balance sheet operation", extra={'user_id': str(user.user_id), 'method': request.method})

    if user.role != User.ASSOCIATION:
        logger.warning("Unauthorized balance sheet access - not association", extra={'user_id': str(user.user_id), 'role': user.role})
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    try:
        sport_association = user.sportassociation
    except SportAssociation.DoesNotExist:
        logger.error("Sport association not found for user", extra={'user_id': str(user.user_id)})
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'GET' or (request.method == 'POST' and request.data.get('format', None) is not None):
        # get query param Export with values excel or pdf
        export = request.data.get('format', None)
        logger.debug("Retrieving balance sheet", extra={'user_id': str(user.user_id), 'export_format': export})
        # get currentDate from query params
        current_date = request.query_params.get('currentDate', datetime.now())
        if isinstance(current_date, str):
            current_date = datetime.strptime(current_date, '%Y-%m-%d')
        elif isinstance(current_date, datetime):
            # remove time from datetime
            current_date = datetime.strptime(current_date.strftime('%Y-%m-%d'), '%Y-%m-%d')

        # get date range from current date
        date_from, date_to = BalanceSheetData.get_range_from_year_and_starting_date(
            date=current_date,
            starting_day=user.balance_sheet_start_day,
            starting_month=user.balance_sheet_start_month
        )

        # calculate previous year
        last_date_from, _ = BalanceSheetData.get_range_from_year_and_starting_date(
            date=datetime.strptime(datetime.now().strftime('%Y-%m-%d'), '%Y-%m-%d'),
            starting_day=user.balance_sheet_start_day,
            starting_month=user.balance_sheet_start_month
        )

        available_years = [
            {
                'year': last_date_from.year - 5,
                'start_date': last_date_from.replace(year=last_date_from.year - 5)
            },
            {
                'year': last_date_from.year - 4,
                'start_date': last_date_from.replace(year=last_date_from.year - 4)
            },
            {
                'year': last_date_from.year - 3,
                'start_date': last_date_from.replace(year=last_date_from.year - 3)
            },
            {
                'year': last_date_from.year - 2,
                'start_date': last_date_from.replace(year=last_date_from.year - 2)
            },
            {
                'year': last_date_from.year - 1,
                'start_date': last_date_from.replace(year=last_date_from.year - 1)
            },
            {
                'year': last_date_from.year,
                'start_date': last_date_from
            }
        ]

        balance_sheet_obj = BalanceSheet.objects.filter(
            year=date_from.year,
            sport_association=sport_association) \
            .order_by('-creation_date').first()

        logger.debug("Checking existing balance sheet", extra={
            'user_id': str(user.user_id),
            'year': date_from.year,
            'exists': balance_sheet_obj is not None,
            'status': balance_sheet_obj.status_flag if balance_sheet_obj else None
        })

        if balance_sheet_obj is None:
            default_bs = BalanceSheetData.get_balance_sheet_default_data()
        else:
            default_bs = balance_sheet_obj.data
            if balance_sheet_obj.status_flag == BalanceSheet.APPROVED:
                logger.info("Returning approved balance sheet", extra={
                    'user_id': str(user.user_id),
                    'year': balance_sheet_obj.year
                })
                return Response({'data': {
                    'available_years': available_years,
                    'balance_sheet': {
                        'data': balance_sheet_obj.data,
                        'creation_date': balance_sheet_obj.creation_date,
                        'year': balance_sheet_obj.year,
                        'draft': False,
                    },
                    'sport_association': SportAssociationSerializer(sport_association).data
                }}, status=status.HTTP_200_OK)

        try:
            logger.info("Generating balance sheet", extra={
                'user_id': str(user.user_id),
                'sport_association_id': str(sport_association.sport_association_id),
                'date_from': str(date_from),
                'date_to': str(date_to)
            })
            default_bs = generate_balance_sheet(sport_association, date_from, date_to, default_bs)
        except Exception as e:
            # if issues getting new view, fallback to old view
            logger.error("Error generating balance sheet", extra={
                'user_id': str(user.user_id),
                'sport_association_id': str(sport_association.sport_association_id),
                'error': str(e)
            }, exc_info=True)

        # update bank, cash, other and total
        logger.debug("Calculating account balances", extra={'user_id': str(user.user_id)})
        default_bs['bank'] = 0
        for acc in CustomAccounts.objects.filter(
            sport_association=sport_association,
            account_type=CustomAccounts.BANK,
        ):
            default_bs['bank'] += float(CustomAccountSerializer(acc).get_current_balance_from(
                date_from=datetime(1970, 1, 1),  # from the beginning
                date_to=date_to,
            ))
        default_bs['cash'] = 0
        for acc in CustomAccounts.objects.filter(
            sport_association=sport_association,
            account_type=CustomAccounts.CASH,
        ):
            default_bs['cash'] += float(CustomAccountSerializer(acc).get_current_balance_from(
                date_from=datetime(1970, 1, 1),  # from the beginning
                date_to=date_to,
            ))
        default_bs['other'] = 0
        for acc in CustomAccounts.objects.filter(
            sport_association=sport_association,
            account_type=CustomAccounts.OTHER,
        ):
            default_bs['other'] += float(CustomAccountSerializer(acc).get_current_balance_from(
                date_from=datetime(1970, 1, 1),  # from the beginning
                date_to=date_to,
            ))
        default_bs['total'] = default_bs['bank'] + default_bs['cash'] + default_bs['other']

        logger.info("Account balances calculated", extra={
            'user_id': str(user.user_id),
            'bank': default_bs['bank'],
            'cash': default_bs['cash'],
            'other': default_bs['other'],
            'total': default_bs['total']
        })

        if balance_sheet_obj is None:
            logger.info("Creating new balance sheet", extra={
                'user_id': str(user.user_id),
                'year': date_from.year
            })
            balance_sheet_obj = BalanceSheet.objects.create(
                sport_association=sport_association,
                data=default_bs,
                year=date_from.year,
            )
        else:
            logger.debug("Updating existing balance sheet", extra={
                'user_id': str(user.user_id),
                'year': date_from.year
            })
            balance_sheet_obj.year = date_from.year
            balance_sheet_obj.data = default_bs

        balance_sheet_obj.save()

        if export is not None and export in ['excel', 'pdf']:
            logger.info("Exporting balance sheet", extra={
                'user_id': str(user.user_id),
                'format': export,
                'year': balance_sheet_obj.year
            })
            # call export_balance_sheet() function
            data = export_balance_sheet(balance_sheet_obj, export)
            return Response(data, status=status.HTTP_200_OK)

        logger.info("Balance sheet retrieved successfully", extra={
            'user_id': str(user.user_id),
            'year': balance_sheet_obj.year,
            'is_draft': balance_sheet_obj.status_flag == BalanceSheet.DRAFT
        })
        return Response({'data': {
            'available_years': available_years,
            'balance_sheet': {
                'data': balance_sheet_obj.data,
                'creation_date': balance_sheet_obj.creation_date,
                'year': balance_sheet_obj.year,
                'draft': True if balance_sheet_obj.status_flag == BalanceSheet.DRAFT else False,
            },
            'sport_association': SportAssociationSerializer(sport_association).data
        }}, status=status.HTTP_200_OK)
    elif request.method == 'POST':
        logger.info("Updating balance sheet", extra={'user_id': str(user.user_id)})
        data = request.data
        if data is None or len(data.keys()) != 1 or 'balance_sheet' not in data.keys():
            logger.warning("Invalid balance sheet update data", extra={
                'user_id': str(user.user_id),
                'keys': list(data.keys()) if data else None
            })
            return Response({'msg': 'Invalid data.'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            is_draft = data['balance_sheet']['draft']
            balance_sheet_obj = BalanceSheet.objects.filter(sport_association=sport_association)\
                .order_by('-creation_date').first()
            if balance_sheet_obj is None:
                logger.warning("Balance sheet not found for update", extra={'user_id': str(user.user_id)})
                return Response({'msg': 'No balance sheet found.'}, status=status.HTTP_404_NOT_FOUND)
            else:
                logger.info("Saving balance sheet", extra={
                    'user_id': str(user.user_id),
                    'year': data['balance_sheet']['year'],
                    'is_draft': is_draft
                })
                balance_sheet_obj.data = data['balance_sheet']
                balance_sheet_obj.status_flag = BalanceSheet.DRAFT if is_draft else BalanceSheet.APPROVED
                balance_sheet_obj.year = data['balance_sheet']['year']
                balance_sheet_obj.save()
                logger.info("Balance sheet saved successfully", extra={
                    'user_id': str(user.user_id),
                    'year': balance_sheet_obj.year,
                    'status': balance_sheet_obj.status_flag
                })
                return Response({'data': {
                    'balance_sheet': {
                        'data': balance_sheet_obj.data,
                        'creation_date': balance_sheet_obj.creation_date,
                        'year': balance_sheet_obj.year,
                        'draft': is_draft,
                    },
                    'sport_association': SportAssociationSerializer(sport_association).data
                }}, status=status.HTTP_200_OK)
    elif request.method == 'DELETE':
        logger.info("Deleting balance sheet", extra={'user_id': str(user.user_id)})
        current_date = request.query_params.get('currentDate', datetime.now())
        if isinstance(current_date, str):
            current_date = datetime.strptime(current_date, '%Y-%m-%d')
        elif isinstance(current_date, datetime):
            # remove time from datetime
            current_date = datetime.strptime(current_date.strftime('%Y-%m-%d'), '%Y-%m-%d')

        # get date range from current date
        date_from, date_to = BalanceSheetData.get_range_from_year_and_starting_date(
            date=current_date,
            starting_day=user.balance_sheet_start_day,
            starting_month=user.balance_sheet_start_month
        )

        balance_sheet_obj = BalanceSheet.objects.filter(
            year=date_from.year,
            sport_association=sport_association) \
            .order_by('-creation_date').first()

        if balance_sheet_obj is None:
            logger.warning("Balance sheet not found for deletion", extra={
                'user_id': str(user.user_id),
                'year': date_from.year
            })
            return Response({'msg': 'No balance sheet found.'}, status=status.HTTP_200_OK)

        logger.info("Resetting balance sheet to defaults", extra={
            'user_id': str(user.user_id),
            'year': date_from.year
        })
        default_bs = BalanceSheetData.get_balance_sheet_default_data()
        balance_sheet_obj.data = default_bs
        balance_sheet_obj.save()
        logger.info("Balance sheet refreshed successfully", extra={
            'user_id': str(user.user_id),
            'year': balance_sheet_obj.year
        })
        return Response({'msg': 'Balance sheet refreshed.'}, status=status.HTTP_200_OK)
    else:
        return Response(status=status.HTTP_401_UNAUTHORIZED)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def balance_sheet_archived(request):
    user = request.user
    logger.info("Retrieving archived balance sheets", extra={'user_id': str(user.user_id)})

    sport_association = SportAssociation.objects.get(user=user)
    if user.role != User.ASSOCIATION:
        logger.warning("Unauthorized archived balance sheet access", extra={
            'user_id': str(user.user_id),
            'role': user.role
        })
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    if sport_association is None:
        logger.error("Sport association not found", extra={'user_id': str(user.user_id)})
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    balance_sheet_objs = BalanceSheet.objects.filter(sport_association=sport_association, archived=False) \
        .order_by('-creation_date')
    data = {
        "balance_sheets": [],
        'sport_association': SportAssociationSerializer(sport_association).data
    }
    for balance_sheet_obj in balance_sheet_objs:
        data["balance_sheets"].append({
            'data': balance_sheet_obj.data,
            'creation_date': balance_sheet_obj.creation_date,
            'year': balance_sheet_obj.year,
            'draft': True if balance_sheet_obj.status_flag == BalanceSheet.DRAFT else False,
        })

    logger.info("Archived balance sheets retrieved", extra={
        'user_id': str(user.user_id),
        'count': len(data['balance_sheets'])
    })
    return Response(data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
# @cache_endpoint('custom_accounts_list', timeout=60 * 60 * 24 * 7)
def balance_sheet_accounts_list(request):
    get_related = request.query_params.get('related', 'true')

    user = request.user
    logger.info("Retrieving custom accounts list", extra={
        'user_id': str(user.user_id),
        'get_related': get_related
    })

    if user.role != User.ASSOCIATION:
        logger.warning("Unauthorized custom accounts access", extra={
            'user_id': str(user.user_id),
            'role': user.role
        })
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=user)
    if sport_association is None:
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    # get custom accounts
    custom_accounts = CustomAccounts.objects.filter(sport_association=sport_association)

    if get_related == 'true':
        data = {
            'data': CustomAccountSerializer(custom_accounts.order_by('editable'), many=True).data,
        }
    else:
        data = {'data': []}
        for acc in custom_accounts:
            data['data'].append({
                'custom_account_id': acc.custom_account_id,
                'enabled': acc.enabled,
                'name': acc.name,
                'initial_balance': acc.initial_balance,
                'account_type': acc.account_type,
                'account_code': acc.account_code,
                'sport_association_id': acc.sport_association_id,
                'editable': acc.editable,
            })

    logger.info("Custom accounts retrieved", extra={
        'user_id': str(user.user_id),
        'count': len(data['data'])
    })
    return Response(data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def balance_sheet_accounts_add(request):
    user = request.user
    logger.info("Creating custom account", extra={'user_id': str(user.user_id)})

    if user.role != User.ASSOCIATION:
        logger.warning("Unauthorized custom account creation", extra={
            'user_id': str(user.user_id),
            'role': user.role
        })
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=user)
    if sport_association is None:
        logger.error("Sport association not found", extra={'user_id': str(user.user_id)})
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    data = request.data
    data['sport_association'] = sport_association.sport_association_id
    serializer = CustomAccountSerializer(data=data)
    if serializer.is_valid(raise_exception=True):
        serializer.save(sport_association=sport_association)
        logger.info("Custom account created successfully", extra={
            'user_id': str(user.user_id),
            'account_id': str(serializer.data['custom_account_id']),
            'account_name': serializer.data.get('name')
        })
        return Response(serializer.data, status=status.HTTP_200_OK)
    else:
        logger.warning("Custom account validation failed", extra={
            'user_id': str(user.user_id),
            'errors': serializer.errors
        })
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def balance_sheet_accounts_update(request, uid):
    user = request.user
    logger.info("Updating custom account", extra={'user_id': str(user.user_id), 'account_id': uid})

    if user.role != User.ASSOCIATION:
        logger.warning("Unauthorized custom account update", extra={
            'user_id': str(user.user_id),
            'role': user.role
        })
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=user)
    if sport_association is None:
        logger.error("Sport association not found", extra={'user_id': str(user.user_id)})
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    data = request.data
    data['sport_association'] = sport_association.sport_association_id
    custom_account = CustomAccounts.objects.get(custom_account_id=uid)

    # if not custom_account.editable:
    #     return Response({'msg': 'This account is not editable.'}, status=status.HTTP_403_FORBIDDEN)

    serializer = CustomAccountSerializer(custom_account, data=data, partial=True)
    if serializer.is_valid(raise_exception=True):
        serializer.save()
        logger.info("Custom account updated successfully", extra={
            'user_id': str(user.user_id),
            'account_id': uid,
            'account_name': serializer.data.get('name')
        })
        return Response(serializer.data, status=status.HTTP_200_OK)
    else:
        logger.warning("Custom account update validation failed", extra={
            'user_id': str(user.user_id),
            'account_id': uid,
            'errors': serializer.errors
        })
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def balance_sheet_accounts_delete(request, uid):
    user = request.user
    logger.info("Deleting custom account", extra={'user_id': str(user.user_id), 'account_id': uid})

    if user.role != User.ASSOCIATION:
        logger.warning("Unauthorized custom account deletion", extra={
            'user_id': str(user.user_id),
            'role': user.role
        })
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=user)
    if sport_association is None:
        logger.error("Sport association not found", extra={'user_id': str(user.user_id)})
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    custom_account = CustomAccounts.objects.get(custom_account_id=uid)

    if not custom_account.editable:
        logger.warning("Attempted to delete non-editable account", extra={
            'user_id': str(user.user_id),
            'account_id': uid
        })
        return Response({'msg': 'This account is not deletable.'}, status=status.HTTP_403_FORBIDDEN)

    logger.info("Custom account deleted successfully", extra={
        'user_id': str(user.user_id),
        'account_id': uid,
        'account_name': custom_account.name
    })
    custom_account.delete()
    return Response({'msg': 'bank accounts deleted'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def balance_sheet_accounts_transfer_list(request):
    user = request.user
    logger.info("Retrieving account transfers", extra={'user_id': str(user.user_id)})

    if user.role != User.ASSOCIATION:
        logger.warning("Unauthorized account transfers access", extra={
            'user_id': str(user.user_id),
            'role': user.role
        })
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=user)
    if sport_association is None:
        logger.error("Sport association not found", extra={'user_id': str(user.user_id)})
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    # get custom accounts
    custom_accounts_transfers = CustomAccountsTransfer.objects.filter(sport_association=sport_association).iterator(chunk_size=100)
    data = {
        'data': CustomAccountTransferInfoSerializer(custom_accounts_transfers, many=True).data,
    }

    logger.info("Account transfers retrieved", extra={
        'user_id': str(user.user_id),
        'count': len(data['data'])
    })
    return Response(data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def balance_sheet_accounts_transfer_add(request):
    user = request.user
    logger.info("Creating account transfer", extra={'user_id': str(user.user_id)})

    if user.role != User.ASSOCIATION:
        logger.warning("Unauthorized account transfer creation", extra={
            'user_id': str(user.user_id),
            'role': user.role
        })
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=user)
    if sport_association is None:
        logger.error("Sport association not found", extra={'user_id': str(user.user_id)})
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    data = request.data
    data['sport_association'] = sport_association.sport_association_id
    serializer = CustomAccountTransferSerializer(data=data)
    if serializer.is_valid(raise_exception=True):
        serializer.save(sport_association=sport_association)
        logger.info("Account transfer created successfully", extra={
            'user_id': str(user.user_id),
            'transfer_id': str(serializer.data['custom_account_transfer_id']),
            'amount': serializer.data.get('amount')
        })
        return Response(serializer.data, status=status.HTTP_200_OK)
    else:
        logger.warning("Account transfer validation failed", extra={
            'user_id': str(user.user_id),
            'errors': serializer.errors
        })
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def balance_sheet_accounts_transfer_update(request, uid):
    user = request.user
    logger.info("Updating account transfer", extra={'user_id': str(user.user_id), 'transfer_id': uid})

    if user.role != User.ASSOCIATION:
        logger.warning("Unauthorized account transfer update", extra={
            'user_id': str(user.user_id),
            'role': user.role
        })
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=user)
    if sport_association is None:
        logger.error("Sport association not found", extra={'user_id': str(user.user_id)})
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    data = request.data
    data['sport_association'] = sport_association.sport_association_id
    custom_account_transfer = CustomAccountsTransfer.objects.get(custom_account_transfer_id=uid)
    serializer = CustomAccountTransferSerializer(custom_account_transfer, data=data, partial=True)
    if serializer.is_valid(raise_exception=True):
        serializer.save()
        logger.info("Account transfer updated successfully", extra={
            'user_id': str(user.user_id),
            'transfer_id': uid,
            'amount': serializer.data.get('amount')
        })
        return Response(serializer.data, status=status.HTTP_200_OK)
    else:
        logger.warning("Account transfer update validation failed", extra={
            'user_id': str(user.user_id),
            'transfer_id': uid,
            'errors': serializer.errors
        })
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def balance_sheet_accounts_transfer_delete(request, uid):
    user = request.user
    logger.info("Deleting account transfer", extra={'user_id': str(user.user_id), 'transfer_id': uid})

    if user.role != User.ASSOCIATION:
        logger.warning("Unauthorized account transfer deletion", extra={
            'user_id': str(user.user_id),
            'role': user.role
        })
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    sport_association = SportAssociation.objects.get(user=user)
    if sport_association is None:
        logger.error("Sport association not found", extra={'user_id': str(user.user_id)})
        return Response({'msg': 'Info not available.'}, status=status.HTTP_403_FORBIDDEN)

    custom_account_transfer = CustomAccountsTransfer.objects.get(custom_account_transfer_id=uid)
    logger.info("Account transfer deleted successfully", extra={
        'user_id': str(user.user_id),
        'transfer_id': uid
    })
    custom_account_transfer.delete()
    return Response({'msg': 'bank accounts deleted'}, status=status.HTTP_200_OK)
