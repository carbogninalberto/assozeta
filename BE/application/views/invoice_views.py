"""
@ copyright: Bakney SRL
"""
import base64
import csv
import logging
from datetime import date
from io import StringIO

import datetime

from django.db.models import Q, Prefetch
from django.utils import timezone

from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes

from application.utils.subscriptions_utils import smart_search
from core import settings
from core.middleware import IsAuthenticated

from application.models.balance_sheet_models import CustomAccounts
from application.models.invoices_models import Invoice, InvoiceSuppliers, CustomerInvoice
from application.models.payment_models import Payment, SupplierAndCustomers, PaymentCategory
from application.models.user_models import SportAssociation, User, Associate
from application.permissions import IsTeamsPlanAssociation, IsProPlanAssociation
from application.serializers.invoice_serializers import InvoiceSerializer, InvoiceSuppliersSerializer, \
    CustomerInvoiceSerializer, generate_invoice_xml, generate_invoice_json
from application.serializers.payment_serializers import PaymentSerializer, PaymentEntrySerializer, \
    PaymentInvoiceSerializer
from application.utils.api_utils import is_valid_uuid, KTDatatablePagination, BalanceSheetData
from application.utils.excel_utils import get_excel_base64
from application.printing_tasks import print_document_invoice, print_document_customer_invoice
from application.tasks import export_invoices_to_zip

from core.tasks import send_mail_async

from application.utils.payments_utils import generate_invoice_description
from core.settings import APP_HOST

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def invoice_list(request):
    paginator = KTDatatablePagination()
    # pagination_search
    general_search = request.GET.get('query[generalSearch]', None)
    sort_field = request.GET.get('sort[field]', None)
    sort_type = request.GET.get('sort[sort]', None)
    current_year = request.GET.get('query[current_year]', None)
    archived = request.GET.get('query[archived]', False)
    invoice_id = request.GET.get('query[invoice_id]', None)

    if invoice_id:
        invoice = Invoice.objects.filter(invoice_id=invoice_id).first()
        if invoice is not None:
            payment = Payment.objects.filter(invoice=invoice).first()
            if payment is not None:
                return Response({
                    'data': {
                        'invoice': InvoiceSerializer(invoice).data,
                    }
                }, status=status.HTTP_200_OK)
            return Response({
                'data': {
                    'invoice': InvoiceSerializer(invoice).data,
                }
            }, status=status.HTTP_200_OK)
        return Response({'error': 'Invoice not found'}, status=status.HTTP_404_NOT_FOUND)


    logger.info("invoice_list -> init -> user: {}".format(request.user.user_id))
    
    # Use direct relation instead of extra query
    try:
        sport_association = request.user.sportassociation
    except SportAssociation.DoesNotExist:
        sport_association = SportAssociation.objects.get(user=request.user)
    
    # Combine filters and add select_related/prefetch_related
    filters = Q(sport_association=sport_association, archived=archived) & (
        Q(payment__isnull=False) | Q(meta__isnull=False)
    )
    
    invoices = Invoice.objects.filter(filters).select_related(
        'sport_association',
        'sport_association__user',
        'document_pdf',
        'selected_tutor',
        'group'
    ).prefetch_related(
        Prefetch('payment_set',
            queryset=Payment.objects.select_related(
                'invoice', 
                'associate',
                'supplier',
                'instructor',
                'custom_accounts'
            )
        )
    ).order_by('-number', '-creation_date')

    # current year range
    current_date_from, current_date_to = BalanceSheetData.get_range_from_year_and_starting_date(
        date=timezone.now(),
        starting_day=request.user.balance_sheet_start_day,
        starting_month=request.user.balance_sheet_start_month
    )

    logger.info(f"[invoice_list] current_date_from: {current_date_from}, current_date_to: {current_date_to}")

    if current_year:
        if current_year == '1':
            invoices = invoices.filter(
                Q(payment__payment_date__gte=current_date_from) &
                Q(payment__payment_date__lte=current_date_to)
            )

        elif current_year == '0':
            invoices = invoices.filter(
                Q(payment__payment_date__lt=current_date_from) |
                Q(payment__payment_date__gt=current_date_to)
            )

    # filter for general search
    if general_search:
        # check if general_search is a date
        try:
            date = datetime.datetime.strptime(general_search, '%d/%m/%Y').strftime('%Y-%m-%d')
            general_search = date
        except ValueError:
            pass

        try:
            # Parse amount once and reuse
            amount = float(general_search.replace(',', '.'))
            invoices = invoices.filter(
                Q(number__icontains=general_search) |
                Q(membership_fee=amount) |
                Q(activity_fee=amount)
            )
        except (ValueError, TypeError):
            # Use smart_search for non-numeric searches
            invoices = smart_search(invoices, general_search)
    # sort by field
    if sort_field:
        invoices = invoices.order_by(f"{'-' if sort_type == 'desc' else ''}{sort_field}")

    # pagination
    invoices = paginator.paginate_queryset(queryset=invoices, request=request)
    
    # Serialize invoices with prefetched data
    invoices_data = InvoiceSerializer(invoices, many=True).data
    
    # Build payment map from prefetched data (no extra query needed)
    payment_map = {}
    for invoice in invoices:
        # Get first payment from prefetched payment_set
        payments = invoice.payment_set.all()
        if payments:
            payment_map[str(invoice.invoice_id)] = payments[0]
    
    # Collect invoices needing documents for batch processing
    invoices_needing_docs = []
    
    data = {}
    rows_ids = []
    for idx, invoice_data in enumerate(invoices_data):
        # Collect invoices without PDFs for batch processing
        if invoice_data['document_pdf'] is None:
            invoices_needing_docs.append(str(invoice_data['invoice_id']))
        
        data[idx] = invoice_data
        
        # Use payment from map
        payment = payment_map.get(invoice_data['invoice_id'])
        if payment:
            data[idx]['payment'] = PaymentInvoiceSerializer(payment).data
            data[idx]['no_payment'] = False
        else:
            data[idx]['payment'] = invoice_data['meta']
            data[idx]['no_payment'] = True
        
        # Remove meta field
        if 'meta' in data[idx]:
            del data[idx]['meta']
        
        rows_ids.append(invoice_data['invoice_id'])
    
    # Batch process document generation
    auth_token = request.headers.get('authorization')
    for invoice_id in invoices_needing_docs:
        print_document_invoice.delay(invoice_id, auth_token)

    logger.info("invoice_list -> ended -> user: {}".format(request.user.user_id))
    return Response({'data': data, "meta": {
        "total": paginator.page.paginator.count,
        "page": paginator.page.number,
        "pages": paginator.page.paginator.num_pages,
        "perpage": paginator.page.paginator.per_page,
        "rowIds": rows_ids
    }}, status=status.HTTP_200_OK)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def invoice_update(request, uid):
    logger.info("Updating invoice", extra={'user_id': str(request.user.user_id), 'invoice_id': uid})

    is_valid_uuid(uid)

    # get number from body
    data = request.data
    number = None
    cancelled = None
    payment_date = None
    if "number" not in data.keys():
        raise Exception('Number empty')
    else:
        number = data['number']
    if "cancelled" not in data.keys():
        cancelled = False
    else:
        cancelled = data['cancelled']

    if "payment_date" in data.keys():
        try:
            # transform to datetime from string date
            payment_date = datetime.datetime.strptime(data['payment_date'], '%Y-%m-%d')
        except Exception as e:
            payment_date = None

    sport_association = SportAssociation.objects.filter(user=request.user).first()
    if sport_association is not None:
        invoice = Invoice.objects.filter(
            invoice_id=uid,
            sport_association=sport_association,
            archived=False
        ).first()
        if invoice is not None:
            invoice.number = int(number)
            invoice.cancelled = bool(cancelled)
            if 'selected_tutor' in data.keys():
                # get the selected tutor
                selected_tutor = Associate.objects.filter(associate_id=data['selected_tutor']).first()
                if selected_tutor is not None:
                    invoice.selected_tutor = selected_tutor
            else:
                invoice.selected_tutor = None
            payment = Payment.objects.filter(invoice=invoice).first()
            if payment is not None:
                if payment_date:
                    payment.payment_date = payment_date
                payment.save()
                invoice.description = generate_invoice_description(payment, sport_association)
            invoice.save()
            logger.info("Invoice updated, regenerating document", extra={'invoice_id': uid})
            print_document_invoice.apply(args=[str(invoice.invoice_id), request.headers.get('authorization')])
        else:
            logger.error("Invoice not found", extra={'invoice_id': uid})
            raise Exception('Invoice not found')

        logger.info("Invoice updated successfully", extra={'invoice_id': uid})
        return Response({'message': 'Invoice updated', 'invoice': InvoiceSerializer(invoice).data}, status=status.HTTP_200_OK)
    raise Exception('Sport association not found')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def invoice_delete(request, uid):
    logger.info("Deleting invoice", extra={'user_id': str(request.user.user_id), 'invoice_id': uid})

    is_valid_uuid(uid)

    sport_association = SportAssociation.objects.get(user=request.user)
    if sport_association is not None: #\
            #and sport_association.user.temporary_invoice_deletion:
        invoice = Invoice.objects.filter(
            invoice_id=uid,
            sport_association=sport_association,
        ).first()
        # if it exists, and it has been created from less than 7 days
        # \
        # and invoice.creation_date < invoice.creation_date + datetime.timedelta(days=7)
        if invoice is not None:
            payment = Payment.objects.filter(invoice=invoice).first()
            if payment is not None:
                payment.invoice = None
                payment.paid = False #if payment.subject is not Payment.OTHER else True
                payment.save()
            logger.info("Invoice deleted successfully", extra={'invoice_id': uid})
            invoice.delete()

            return Response({'message': 'Invoice deleted'}, status=status.HTTP_200_OK)
        raise Exception('Invoice not found')
    raise Exception('Sport association not found')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def invoice_send(request, uid):
    """
    API endpoint to add a new payment
    """
    logger.info("Sending invoice via email", extra={'user_id': str(request.user.user_id), 'invoice_id': uid})

    is_valid_uuid(uid)

    sport_association = SportAssociation.objects.get(user=request.user)
    invoice = Invoice.objects.filter(invoice_id=uid).first()
    invoice.payment = Payment.objects.filter(invoice=invoice).first()

    if invoice.sport_association.sport_association_id != sport_association.sport_association_id:
        raise PermissionDenied("User not allowed.")

    # Determine name based on email source
    if invoice.payment.associate and invoice.payment.associate.email:
        email = invoice.payment.associate.email
        name = f"{invoice.payment.associate.first_name} {invoice.payment.associate.last_name}"
    elif invoice.payment.user and invoice.payment.user.email:
        email = invoice.payment.user.email
        name = f"{invoice.payment.user.first_name} {invoice.payment.user.last_name}"
    elif invoice.payment.supplier and invoice.payment.supplier.email:
        email = invoice.payment.supplier.email
        name = invoice.payment.supplier.name
    else:
        return Response({"msg": "Nessuna email associata."}, status=status.HTTP_412_PRECONDITION_FAILED)

    if invoice.payment and email is not None:

        email_html = f"""
        Ciao {name},<br>
        ecco la tua ricevuta di {float(invoice.membership_fee) + float(invoice.activity_fee)}€ n. {invoice.number} del {invoice.payment.payment_date.strftime('%d/%m/%Y')}.<br>
        <br>
        {invoice.description}
        <br>
        <br>
        <a href="https://{APP_HOST}/api/document/retrieve/{invoice.invoice_id}?download=true&token={invoice.document_pdf.token}">Scarica la ricevuta</a><br>
        <br>
        se non riesci a scaricare la ricevuta, copia e incolla il seguente link nel tuo browser:<br>
        https://{APP_HOST}/api/document/retrieve/{invoice.invoice_id}?download=true&token={invoice.document_pdf.token}<br>
        <br>
        <br>
        Cordiali saluti,<br>
        {sport_association.denomination}
        """

        send_mail_async.apply_async(
            kwargs={
                "subject": f"{sport_association.denomination} | Ricevuta di pagamento n. {invoice.number} - {invoice.payment.payment_date.strftime('%d/%m/%Y')}",
                "message": email_html,
                "from_email": settings.DEFAULT_TEAM_EMAIL,
                "recipient_list": [email],
                "html_message": email_html,
                "fail_silently": False,
                "sport_association_id": sport_association.sport_association_id,
                "reply_to": [sport_association.user.email],
            }
        )
        logger.info("Invoice email sent successfully", extra={'invoice_id': uid, 'recipient': email})
        return Response({"msg": "email sent."}, status=status.HTTP_200_OK)
    else:
        logger.warning("Invoice email not sent - no payment/email found", extra={'invoice_id': uid})
        return Response({"msg": "email not sent."}, status=status.HTTP_412_PRECONDITION_FAILED)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def invoice_bulk_delete(request):

    data = request.data

    # check that invoice_ids key is present
    if 'invoice_ids' not in data.keys():
        raise Exception("missing required field")

    sport_association = SportAssociation.objects.get(user=request.user)

    # get all invoices
    Invoice.objects.filter(
        sport_association=sport_association,
        invoice_id__in=data['invoice_ids']
    ).delete()

    return Response({'message': 'All Invoices deleted'}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def invoice_bulk_archive(request):

    data = request.data

    # check that invoice_ids key is present
    if 'invoice_ids' not in data.keys():
        raise Exception("missing required field")

    sport_association = SportAssociation.objects.get(user=request.user)

    # get all invoices
    invoices = Invoice.objects.filter(
        sport_association=sport_association,
        invoice_id__in=data['invoice_ids']
    )
    # bulk update
    for invoice in invoices:
        invoice.archived = True

    Invoice.objects.bulk_update(invoices, ['archived'])

    return Response({'message': 'All Invoices archived'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsProPlanAssociation | IsTeamsPlanAssociation])
def invoice_list_archived(request):

    is_athlete = True if User.ATHLETE == request.user.role else False
    if is_athlete:
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    sport_association = SportAssociation.objects.get(user=request.user)
    invoices = Invoice.objects.filter(
        sport_association=sport_association,
        archived=True
    ).select_related('document_pdf').prefetch_related(
        Prefetch('payment_set',
            queryset=Payment.objects.select_related('associate', 'invoice')
        )
    ).order_by('-creation_date')

    data = []
    for invoice in invoices:
        invoice_data = InvoiceSerializer(invoice).data
        payment = invoice.payment_set.first()
        if payment is not None:
            invoice_data['payment'] = PaymentSerializer(payment).data
        data.append(invoice_data)

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def invoice_list_export(request):

    logger.info("invoice_list_export")
    export_mode = request.GET.get('m', 'csv')
    archived = request.GET.get('archived', False)

    if User.ATHLETE == request.user.role:
        raise Exception("Cannot export info for athlete")
    else:
        sport_association = SportAssociation.objects.get(user=request.user)
        invoices = Invoice.objects.filter(
            sport_association=sport_association,
            archived=archived
        ).select_related('document_pdf').prefetch_related(
            Prefetch('payment_set',
                queryset=Payment.objects.select_related('associate')
            )
        ).order_by('creation_date')

        invoices_parsed = [
            {"data": invoice, "payment": invoice.payment_set.first()}
            for invoice in invoices
        ]

    data = {
        "file": ""
    }
    if export_mode == 'csv':
        data['type'] = 'csv'
        data['filename'] = "[{}] {} a {}.csv".format(
            date.today().strftime("%Y-%m-%d"),
            "RICEVUTE",
            sport_association.denomination
        )
        f = StringIO()
        csv.writer(f).writerow([
            'data',
            'nome',
            'cognome',
            'quota associativa',
            'quota attivita',
            'numero',
            'modalità di pagamento',
        ])
        csv.writer(f).writerows(
            [[
                invoice["data"].creation_date.strftime('%Y-%m-%d'),
                invoice["payment"].associate.first_name if invoice["payment"] and invoice['payment'].associate else '',
                invoice["payment"].associate.last_name if invoice["payment"] and invoice['payment'].associate else '',
                invoice["data"].membership_fee,
                invoice["data"].activity_fee,
                invoice["data"].number,
                str("-" if invoice["payment"].type == Payment.DEFAULT else invoice["payment"].type) if invoice["payment"] else '-',
            ] for invoice in invoices_parsed])
        data["file"] = base64.b64encode(f.getvalue().encode())
    elif export_mode == 'xlsx':
        data['type'] = 'xlsx'
        data['filename'] = "[{}] {} a {}.xlsx".format(
            date.today().strftime("%Y-%m-%d"),
            "RICEVUTE",
            sport_association.denomination
        )
        # todo: benchmark this against using multiple arrays and a single for loop
        excel_base64 = get_excel_base64(
            [invoice["data"].creation_date.strftime('%Y-%m-%d') for invoice in invoices_parsed],
            [invoice["payment"].associate.first_name if invoice["payment"] and invoice['payment'].associate else '' for invoice in invoices_parsed],
            [invoice["payment"].associate.last_name if invoice["payment"] and invoice['payment'].associate else '' for invoice in invoices_parsed],
            [invoice["data"].membership_fee for invoice in invoices_parsed],
            [invoice["data"].activity_fee for invoice in invoices_parsed],
            [invoice["data"].number for invoice in invoices_parsed],
            [str("-" if invoice["payment"].type == Payment.DEFAULT else invoice["payment"].type) if invoice["payment"] else '-' for invoice in invoices_parsed],
            columns=[
                'data',
                'nome',
                'cognome',
                'quota associativa',
                'quota attivita',
                'numero',
                'modalità di pagamento',
            ]
        )
        if excel_base64 is None:
            raise Exception("Error while generating excel file")
        else:
            data["file"] = excel_base64
    elif export_mode == 'files':
        # get all the invoices files and return them zipped in a file
        files_to_zip = [str(i.document_pdf.document_id) for i in invoices.filter(
            document_pdf__isnull=False
        ).select_related('document_pdf')]

        # files_to_zip = invoices.filter(
        #     document_pdf__isnull=False
        # ).select_related('document_pdf').values_list('document_pdf__document_id', flat=True)

        logger.info('files to zip: {}'.format(files_to_zip[:5]))

        export_invoices_to_zip.delay(files_to_zip, sport_association.sport_association_id)

        data['message'] = 'Export dei file in corso, controlla la sezione Documenti e Modelli tra qualche minuto.'

        # printing_service = PrintingService()
        #
        # file, filename = printing_service.download_multiple_files(request, files_to_zip)
        #
        # data['file'] = file
        # data['filename'] = filename
        # logger.info('files to zip: {} {}'.format(filename, file))

    return Response({'data': data}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def invoice_suppliers_stats(request):

    sport_association = SportAssociation.objects.get(user=request.user)
    invoices = InvoiceSuppliers.objects.filter(sport_association=sport_association).order_by('-expire_date')

    invoices_total = 0
    invoices_paid = 0

    for invoice in invoices:
        invoices_total += invoice.amount
        if invoice.paid:
            invoices_paid += invoice.amount

    # invoice_expiring in the next 30 days
    invoices_expiring = invoices.filter(
        expire_date__lte=datetime.datetime.now() + datetime.timedelta(days=30),
        paid=False
    )

    # invoice_expiring return date, amount, invoice_identifier
    invoices_expiring = invoices_expiring.values('expire_date', 'amount', 'invoice_identifier', 'supplier__name')

    data = {
        "invoices_total": invoices_total,
        "invoices_paid": invoices_paid,
        "invoices_unpaid": invoices_total - invoices_paid,
        "invoices_expiring": invoices_expiring
    }

    return Response(data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
#@cache_endpoint('invoice_suppliers_list', timeout=60 * 60 * 24 * 7)
def invoice_suppliers_list(request):
    paginator = KTDatatablePagination()
    # pagination_search
    general_search = request.GET.get('query[generalSearch]', None)
    sort_field = request.GET.get('sort[field]', None)
    sort_type = request.GET.get('sort[sort]', None)


    sport_association = SportAssociation.objects.get(user=request.user)
    invoices = InvoiceSuppliers.objects.filter(sport_association=sport_association)\
        .select_related('supplier').order_by('-expire_date')

    # filter for general search
    if general_search:
        # check if general_search is a date
        try:
            date = datetime.datetime.strptime(general_search, '%d/%m/%Y').strftime('%Y-%m-%d')
            general_search = date
        except ValueError:
            pass

        try:
            invoices = invoices.filter(
                Q(amount__icontains=float(general_search.replace(',', '.'))) |
                Q(amount__exact=float(general_search.replace(',', '.')))
            )
        except Exception as e:
            invoices = invoices\
                .filter(
                    Q(notes__icontains=general_search) |
                    Q(invoice_identifier__icontains=general_search) |
                    Q(payment_date__icontains=general_search) |
                    Q(expire_date__icontains=general_search) |
                    Q(supplier__name__icontains=general_search)
                )
    # sort by field
    if sort_field:
        invoices = invoices.order_by(f"{'-' if sort_type == 'desc' else ''}{sort_field}")

    # pagination
    invoices = paginator.paginate_queryset(queryset=invoices, request=request)

    data = {}
    for idx, invoice in enumerate(invoices):
        data[idx] = InvoiceSuppliersSerializer(invoice).data
    return Response({'data': data, "meta": {
        "total": paginator.page.paginator.count,
        "page": paginator.page.number,
        "pages": paginator.page.paginator.num_pages,
        "perpage": paginator.page.paginator.per_page,
        "rowIds": [invoice.invoice_supplier_id for invoice in invoices]
    }}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def invoice_suppliers_add(request):
    data = request.data

    data['sport_association'] = request.user.sport_association.sport_association_id
    invoice = InvoiceSuppliersSerializer(data=data)

    if data['custom_accounts'] is None:
        custom_accounts = CustomAccounts.objects.filter(
            sport_association=request.user.sport_association,
            editable=False,
            enabled=True,
            account_type=CustomAccounts.BANK
        ).first()
    else:
        custom_accounts = CustomAccounts.objects.filter(
            sport_association=request.user.sport_association,
            custom_account_id=data['custom_accounts']
        ).first()

    if custom_accounts is None:
        return Response({'error': 'Nessun conto bancario attivo trovato.'}, status=status.HTTP_400_BAD_REQUEST)

    supplier = SupplierAndCustomers.objects.filter(supplier_id=data['supplier_id']).first()

    if supplier is None:
        return Response({'error': 'Fornitore non trovato.'}, status=status.HTTP_400_BAD_REQUEST)

    # create associated payment
    if data['payment_date'] is None:
        # set payment date to today with also time
        data['payment_date'] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    payment_data = {
        'subject': Payment.OTHER,
        'amount': data['amount'],
        'description': 'Fattura fornitore ({}) generata automaticamente dal sistema in data {}.'.format(
            supplier.name, datetime.datetime.now().strftime("%d/%m/%Y alle %H:%M")
        ),
        'expense': True,
        'paid': data['paid'] if data['paid'] else False,
        'payment_date': data['payment_date'] if data['paid'] else None,
        'creation_date': data['payment_date'],
        'payment_category': PaymentCategory.objects.filter(name__iexact="Pagamento Fornitore").first().payment_category_id,
        'custom_accounts': custom_accounts.custom_account_id,
    }

    # put date in a serializer
    payment = PaymentEntrySerializer(data=payment_data)

    if payment.is_valid(raise_exception=True) and \
            invoice.is_valid(raise_exception=True):
        payment = payment.save()
        payment.user = request.user
        payment.sport_association = request.user.sport_association
        payment.supplier = supplier
        payment.save()
        # invoice
        invoice = invoice.save()
        invoice.supplier = supplier
        invoice.payment = payment
        invoice.save()

    return Response({'message': 'Invoice created'}, status=status.HTTP_200_OK)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def invoice_suppliers_update(request, uid):
    is_valid_uuid(uid)

    # get number from body
    data = request.data

    sport_association = SportAssociation.objects.filter(user=request.user).first()
    if sport_association is not None:
        invoice = InvoiceSuppliers.objects.filter(
            invoice_supplier_id=uid,
            sport_association=sport_association
        ).first()
        if invoice is not None:
            invoice = InvoiceSuppliersSerializer(invoice, data=data, partial=True)
            if invoice.is_valid(raise_exception=True):
                invoice = invoice.save()
                if invoice.payment:
                    invoice.payment.paid = data['paid']
                    invoice.payment.payment_date = timezone.now()
                    invoice.payment.save()
        else:
            raise Exception('Invoice not found')

        return Response({'message': 'Invoice updated'}, status=status.HTTP_200_OK)
    raise Exception('Sport association not found')


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def invoice_suppliers_delete(request, uid):
    is_valid_uuid(uid)

    sport_association = SportAssociation.objects.get(user=request.user)
    if sport_association is not None:
        invoice = InvoiceSuppliers.objects.filter(
            invoice_supplier_id=uid,
            sport_association=sport_association
        ).first()
        # if it exists, and it has been created from less than 24 hours
        if invoice is not None:
            invoice.delete()
            return Response({'message': 'Invoice deleted'}, status=status.HTTP_200_OK)
        raise Exception('Invoice not found')
    raise Exception('Sport association not found')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def invoice_customers_stats(request):

    sport_association = SportAssociation.objects.get(user=request.user)
    invoices = CustomerInvoice.objects.filter(
        sport_association=sport_association).order_by('-payment_expiry_date')

    invoices_total = 0
    invoices_paid = 0

    for invoice in invoices:
        invoices_total += invoice.payment_total_amount
        if invoice.paid:
            invoices_paid += invoice.payment_total_amount

    # invoice_expiring in the next 30 days
    invoices_expiring = invoices.filter(
        payment_expiry_date__lte=datetime.datetime.now() + datetime.timedelta(days=30),
        payment_expiry_date__gte=datetime.datetime.now(),
        paid=False
    )

    # invoice_expiring return date, amount, invoice_identifier
    invoices_expiring = invoices_expiring.values(
        'payment_expiry_date',
        'payment_total_amount',
        'prefix',
        'number',
        'fiscal_year'
    )

    data = {
        "invoices_total": invoices_total,
        "invoices_paid": invoices_paid,
        "invoices_unpaid": invoices_total - invoices_paid,
        "invoices_expiring": invoices_expiring
    }

    return Response(data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
# @cache_endpoint('invoice_customers_list', timeout=60 * 60 * 24 * 7)
def invoice_customers_list(request):

    # TODO: remove this code once we implement a proper system
    invoices = CustomerInvoice.objects.filter(
        pdf__isnull=True,
    )
    for invoice in invoices:
        try:
            # store the xml in the invoice
            invoice.xml = generate_invoice_xml(invoice)
            invoice.save()
        except Exception as e:
            logger.error(f"Error generating XML for invoice {invoice.customer_invoice_id}: {e}")

        print_document_customer_invoice.apply(args=[
            str(invoice.customer_invoice_id),
            request.headers.get('authorization'),
        ])

    # get all invoices
    invoices = CustomerInvoice.objects.filter(
        sport_association=request.user.sport_association
    ).order_by('-creation_date').iterator(chunk_size=100)

    return Response({'data': [CustomerInvoiceSerializer(invoice).data for invoice in invoices]},
                    status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def invoice_customers_add(request):
    # use the CustomerInvoiceSerializer to validate the data
    data = request.data
    data['sport_association'] = request.user.sport_association.sport_association_id
    # elaborate the data
    if ('payment_expiry_date' in data and  data['payment_expiry_date'] is None) \
            or 'payment_expiry_date' not in data:
        # set payment date to today with also time
        data['payment_expiry_date'] = datetime.datetime.now().strftime("%Y-%m-%d")
    if ('transmitting_date' in data and data['transmitting_date'] is None) \
            or 'transmitting_date' not in data:
        # set payment date to today with also time
        data['transmitting_date'] = datetime.datetime.now().strftime("%Y-%m-%d")

    # convert transmitting date from YYYY-MM-DD to DD/MM/YYYY
    data['transmitting_date'] = datetime.datetime.strptime(data['transmitting_date'], '%Y-%m-%d').strftime('%d/%m/%Y')
    # convert payment expiry date from YYYY-MM-DD to DD/MM/YYYY
    data['payment_expiry_date'] = datetime.datetime.strptime(data['payment_expiry_date'], '%Y-%m-%d').strftime('%d/%m/%Y')
    invoice = CustomerInvoiceSerializer(data=data)

    if invoice.is_valid(raise_exception=True):
        invoice = invoice.save()
        invoice.save()

    try:
        # store the xml in the invoice
        invoice.xml = generate_invoice_xml(invoice)
        invoice.json = generate_invoice_json(invoice) # we generate the json for convenience
        invoice.save()
    except Exception as e:
        logger.error(f"Error generating XML for invoice {invoice.customer_invoice_id}: {e}")
        return Response({'error': 'Errore durante la generazione del file XML.'}, status=status.HTTP_200_OK)

    print_document_customer_invoice.apply(args=[
        str(invoice.customer_invoice_id),
        request.headers.get('authorization'),
    ])

    return Response({'message': 'Invoice created'}, status=status.HTTP_200_OK)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def invoice_customers_update(request, uid):
    is_valid_uuid(uid)
    # we only update the paid and transmitted fields
    data = request.data
    sport_association = SportAssociation.objects.filter(user=request.user).first()
    if sport_association is not None:
        invoice = CustomerInvoice.objects.filter(
            customer_invoice_id=uid,
            sport_association=sport_association
        ).first()
        if invoice is not None:
            invoice.paid = data['paid'] if 'paid' in data else invoice.paid
            invoice.transmitted = data['transmitted'] if 'transmitted' in data else invoice.transmitted
            invoice.save()
        else:
            raise Exception('Invoice not found')

        return Response({'message': 'Invoice updated'}, status=status.HTTP_200_OK)
    raise Exception('Sport association not found')


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def invoice_customers_delete(request, uid):
    is_valid_uuid(uid)
    sport_association = SportAssociation.objects.get(user=request.user)
    if sport_association is not None:
        invoice = CustomerInvoice.objects.filter(
            customer_invoice_id=uid,
            sport_association=sport_association
        ).first()
        # if it exists, and it has been created from less than 24 hours
        if invoice is not None:
            invoice.delete()
            return Response({'message': 'Invoice deleted'}, status=status.HTTP_200_OK)
        raise Exception('Invoice not found')
    raise Exception('Sport association not found')
