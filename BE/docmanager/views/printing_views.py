import logging
from datetime import datetime

from bs4 import BeautifulSoup
from django.http.response import HttpResponse
from django.shortcuts import render
from django.utils import translation
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework import status

from rest_framework.decorators import permission_classes, api_view

from application.models.user_models import Associate, SportAssociation, InstructorHours, \
    SportAssociationModuleTemplates, Folder, SportAssociationDocumentsArchive
from application.models.utils import filter_mentions, extract_values
from application.serializers.invoice_serializers import generate_invoice_html
from application.utils.api_utils import is_valid_uuid
from core import settings
from core.middleware import IsAuthenticated
from rest_framework.response import Response

from application.models import User
from application.models.invoices_models import Invoice, CustomerInvoice
from application.models.payment_models import Payment, PaymentCategory
from application.models.subscriptions_models import Subscription, MedicalAppointments, SubscriptionFile, \
    SubscriptionMembership
from application.utils.printing import Puppeteer, PrintingService
import base64

from core.tasks import send_mail_async
from docmanager.models import Document
from docmanager.views.utils import HasDocumentToken, HasBypassAuthorizationHeader, get_pdf_fields

logger = logging.getLogger(__name__)


@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated])
def document_subscription(request, uid):


    logger.info('medical_certificate_document')

    try:
        # template = os.path.join(BASE_DIR, 'templates/document/application/subscription.html')
        logger.info('document_subscription {}'.format(uid))
        subscription = Subscription.objects.get(subscription_id=uid)
        filename = '[{}] Modulo di Iscrizione {}.pdf'.format(
            subscription.sport_association.denomination,
            subscription.associate.get_full_name())
        document = Document.objects.create(filename=filename)
        printing_service = PrintingService()
        headers = {
            'Authorization': request.headers.get('authorization')
        }
        url = 'document/subscription/' + str(uid) + '/view'
        response = printing_service.print_and_store_pdf(request, document=document, url=url, headers=headers)
        if response.status_code == 200:
            subscription.document_pdf = document
            subscription.save()
        return response
    except TypeError as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
# @permission_classes([IsAuthenticated])
@xframe_options_exempt
def document_subscription_preview(request):

    # get user from query params sport_association_id
    sport_association_id = request.GET.get('sport_association_id', None)
    print = request.GET.get('print', None)

    if print is not None:
        full_url = f"{request.path}?sport_association_id={sport_association_id}"
        # # remove the print query param
        # res = FastRender.get_pdf(url=full_url)
        # # return the pdf
        # return HttpResponse(res, content_type='application/pdf')
        puppeteer = Puppeteer()
        rendered_status, rendered_pdf = puppeteer.pdf_from_url(url=full_url, headers={})
        # make sure the pdf is rendered
        if not rendered_status:
            return Response(data={
                "error": "PDF not rendered"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        # make the file a string in base64 for json response, it is a requests content of a pdf
        pdf_base64 = base64.b64encode(rendered_pdf).decode('utf-8')
        # return the base64 pdf
        return Response(data={
            "file": pdf_base64,
        }, status=status.HTTP_200_OK)


    if sport_association_id is not None:
        sport_association = SportAssociation.objects.get(sport_association_id=sport_association_id)
        user = sport_association.user
    else:
        user = request.user

    # create a dummy subscription object empty to pass to the template
    subscription = Subscription(
        creation_date=None,
        type=None,
        role=None
    )
    subscription.sport_association = user.sport_association
    subscription.associate = Associate(
        first_name='____________________________',
        last_name='____________________________',
        tax_code='____________________________',
        born_city='____________________________',
        born_date='____________________________',
        address='____________________________',
        address_cap='______________',
        address_city='____________________________',
        sex='[ ] M [ ] F [ ] Altro',
        phone='____________________________',
        email='____________________________',
        is_minor=True,
    )
    tutor = Associate(
        first_name='____________________________',
        last_name='____________________________',
        tax_code='____________________________',
        born_city='____________________________',
        born_date='____________________________',
        address='____________________________',
        address_cap='______________',
        address_city='____________________________',
        sex='[ ] M [ ] F [ ] Altro',
        phone='____________________________',
        email='____________________________',
    )

    # Mock the tutors relationship by patching get_main_tutor
    subscription.associate.get_main_tutor = lambda: tutor

    # if additional fields are present in the user object, add them to the subscription object
    if sport_association.additional_fields is not None:
        subscription.additional_fields = sport_association.additional_fields

    payment_data = None

    footer = sport_association.invoice_footer
    header = sport_association.document_header

    # need to filter_mentions the header
    header = filter_mentions(header, context_objects={
        'sport_association': sport_association,
        'subscription': subscription,
        'other': {
            'today': datetime.now().strftime('%d/%m/%Y')
        }
    })

    # filter_mentions the footer
    footer = filter_mentions(footer, context_objects={
        'sport_association': sport_association,
        'subscription': subscription,
        'other': {
            'today': datetime.now().strftime('%d/%m/%Y')
        }
    })

    # assure footer is a string
    if footer is None:
        footer = ''

    subscription_template = subscription.sport_association.get_subscription_template()

    return render(request, f'document/application/{subscription_template}', {
        "uid": None,
        "subscription": subscription,
        "payment_data": payment_data,
        "show_regulation_to": [1, 2, 3],
        "show_demand_to": [1, 2, 3],
        "is_preview": True,
        "header": header,
        "footer": footer
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def document_subscription_view(request, uid):


    logger.info('medical_certificate_document')

    logger.info('document_subscription_view {}'.format(uid))
    subscription = Subscription.objects.get(subscription_id=uid)
    payment_data = None
    if subscription.payment and subscription.payment.meta:
        try:
            payment_data = subscription.payment.meta
        except Exception as e:
            logger.error(e)

    sections = subscription.sport_association.additional_sections
    for section in sections:
        logger.info('section: {}...'.format(str(section)[:10]))
        section['show_to'] = []
        if 'show_to_members' in section and section['show_to_members']:
            section['show_to'].append(1)
        if 'show_to_both' in section and section['show_to_both']:
            section['show_to'].append(2)
        if 'show_to_athletes' in section and section['show_to_athletes']:
            section['show_to'].append(3)


    logger.info('sections: {}'.format(len(sections)))

    # define show_regulation_to
    show_regulation_to = []
    if subscription.sport_association.show_regulation_to_members:
        show_regulation_to.append(1)
    if subscription.sport_association.show_regulation_to_both:
        show_regulation_to.append(2)
    if subscription.sport_association.show_regulation_to_athletes:
        show_regulation_to.append(3)

    # define show_demand_to
    show_demand_to = []
    if subscription.sport_association.show_demand_to_members:
        show_demand_to.append(1)
    if subscription.sport_association.show_demand_to_both:
        show_demand_to.append(2)
    if subscription.sport_association.show_demand_to_athletes:
        show_demand_to.append(3)

    footer = subscription.sport_association.invoice_footer
    header = subscription.sport_association.document_header

    # need to filter_mentions the header
    header = filter_mentions(header, context_objects={
        'sport_association': subscription.sport_association,
        'subscription': subscription,
        'other': {
            'today': datetime.now().strftime('%d/%m/%Y')
        }
    })

    # filter_mentions the footer
    footer = filter_mentions(footer, context_objects={
        'sport_association': subscription.sport_association,
        'subscription': subscription,
        'other': {
            'today': datetime.now().strftime('%d/%m/%Y')
        }
    })

    # assure footer is a string
    if footer is None:
        footer = ''

    subscription_template = subscription.sport_association.get_subscription_template()
    return render(request, f'document/application/{subscription_template}', {
        "uid": uid,
        "subscription": subscription,
        "payment_data": payment_data,
        "show_regulation_to": show_regulation_to,
        "show_demand_to": show_demand_to,
        "is_preview": False,
        "header": header,
        "footer": footer
    })


@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated|HasDocumentToken|HasBypassAuthorizationHeader])
def document_invoice(request, uid):


    logger.info('medical_certificate_document')

    # get send_receipt_email from query params both for GET and POST boolean
    send_receipt_email = request.GET.get('send_receipt_email', False) or request.POST.get('send_receipt_email', False)
    # assure it is a boolean
    send_receipt_email = send_receipt_email in ['true', 'True', '1']

    try:
        logger.info('document_invoice {}'.format(uid))
        try:
            invoice = Invoice.objects.get(invoice_id=uid)
            invoice.payment = Payment.objects.get(invoice=invoice)
        except Invoice.DoesNotExist:
            return Response({'exception': 'Invoice not found'}, status=status.HTTP_404_NOT_FOUND)
        filename = '[{}] Ricevuta {}.pdf'.format(
            invoice.sport_association.denomination,
            invoice.payment.payment_date.date())
        document = Document.objects.create(filename=filename)
        printing_service = PrintingService()
        headers = {
            'Authorization': request.headers.get('authorization')
         }
        url = 'document/invoice/' + str(uid) + '/view'
        response = printing_service.print_and_store_pdf(request, document=document, url=url, headers=headers)
        if response.status_code == 200:
            invoice.document_pdf = document
            invoice.save()
            # SEND EMAIL TO ASSOCIATE and USER if available
            p = Payment.objects.get(invoice=invoice)
            subject = f"Ricevuta di pagamento {invoice.payment.payment_date.date().strftime('%d/%m/%y')}"
            message = f"Gentile {p.associate.get_full_name()},\n" \
                      f"abbiamo il piacere di inviarle la ricevuta di pagamento di {str(invoice.payment.amount).replace(',', ' ').replace('.', ',')} € del {invoice.payment.payment_date.date().strftime('%d/%m/%y')}.\n\n" \
                      f"Ecco il link per scaricare la ricevuta:\n {settings.APP_URL}/api/document/retrieve/{document.document_id}?download=false&token={invoice.document_pdf.token}\n\n" \
                      f"Cordiali saluti,\n" \
                      f"{invoice.sport_association.denomination}"

            if p.associate.email:
                if send_receipt_email is True:
                    send_mail_async.apply_async(
                        kwargs={
                            "subject": subject,
                            "message": message,
                            "from_email": settings.DEFAULT_TEAM_EMAIL,
                            "reply_to": [settings.DEFAULT_SUPPORT_EMAIL],
                            "recipient_list": [p.associate.email],
                            "html_message": None,
                            "fail_silently": False
                        }
                    )
        return response
    except TypeError as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated|HasBypassAuthorizationHeader])
def document_invoice_view(request, uid):


    logger.info('medical_certificate_document')

    logger.info('document_invoice_view {}'.format(uid))
    invoice = Invoice.objects.get(invoice_id=uid)
    payment = Payment.objects.get(invoice=invoice)
    total_amount = invoice.activity_fee + invoice.membership_fee
    user = User.objects.filter(user_id=invoice.sport_association.user_id).first()
    enumerate_invoices = user.enumerate_invoices
    footer = invoice.sport_association.invoice_footer
    header = invoice.sport_association.document_header

    # need to filter_mentions the header
    header = filter_mentions(header, context_objects={
        'sport_association': invoice.sport_association,
        'invoice': invoice,
        'other': {
            'today': datetime.now().strftime('%d/%m/%Y')
        }
    })

    # filter_mentions the footer
    footer = filter_mentions(footer, context_objects={
        'sport_association': invoice.sport_association,
        'invoice': invoice,
        'other': {
            'today': datetime.now().strftime('%d/%m/%Y')
        }
    })

    # assure footer is a string
    if footer is None:
        footer = ''

    payment_method = payment.get_type_in_lang(lang='it')

    # check if invoice subject is OTHER
    if payment.subject == Payment.OTHER:
        invoice.description = f"Ricevuta di pagamento con causale: <b>{payment.payment_category.name}</b> " + \
                              f"dell'associazione <b>{invoice.sport_association.denomination}</b>."

    stamp_free = False
    if payment.subject == Payment.COURSE or \
        payment.subject == Payment.SUBSCRIPTION or \
        (payment.payment_category is not None and payment.payment_category.tax_deductible):
        stamp_free = True

    # check if payment.course and payment cagtegory is empty and set default value
    if payment.payment_category is None and payment.subject == Payment.COURSE:
        # create in-memory payment category with the course / carnet name
        payment.payment_category = PaymentCategory(name=payment.get_course_carnet_name())


    payment_categories = None

    # if there are meta_payment_categories then:
    if payment.meta_payment_categories and len(payment.meta_payment_categories) > 0:
        # generate a dict of {name: amount} for the meta_payment_categories including the activity fee
        # get category name for the activity fee
        payment_categories = {}
        if payment.payment_category and payment.subject != Payment.SUBSCRIPTION:
            name = payment.payment_category.name.lower()
            # sum decimal and float
            to_subtract = sum([meta_payment_category['amount'] for meta_payment_category in payment.meta_payment_categories])
            # convert to decimal the to_subtract
            amount = float(invoice.activity_fee) - float(to_subtract)
            payment_categories = {name: amount}

        # go through the meta_payment_categories and extract the name of the payment categories
        for meta_payment_category in payment.meta_payment_categories:
            payment_category = PaymentCategory.objects.get(
                payment_category_id=meta_payment_category['payment_category_id'])
            # check if name is already in the dict, if so number it like (1), (2), (3) etc.
            if payment_category.name.lower() in payment_categories:
                i = 1
                while True:
                    if f"{payment_category.name.lower()} ({i})" not in payment_categories:
                        payment_categories[f"{payment_category.name.lower()} ({i})"] = meta_payment_category['amount']
                        break
                    i += 1
            else:
                payment_categories[payment_category.name.lower()] = meta_payment_category['amount']
            # add check for stamp_free if category is tax_deductible
            stamp_free = stamp_free and payment_category.tax_deductible

    # check if payment is cancelled
    body_style = ""
    if invoice.cancelled:
        body_style = "opacity:0.7;text-decoration: line-through;"
        footer += "<br><br><b>RICEVUTA ANNULLATA (non è più valida)</b>"

    if invoice.selected_tutor is None:
        try:
            payment.associate.tutor = payment.associate.main_tutor
            if payment.associate.tutor is not None:
                payment.associate.is_minor = True
        except Exception as e:
            logger.error(e)
    else:
        payment.associate.tutor = invoice.selected_tutor
        payment.associate.is_minor = True

    # check if the payment is a subscription payment
    sub = Subscription.objects.filter(payment=payment).first()
    sub_memebership = SubscriptionMembership.objects.filter(subscription=sub).first()

    sub_period = sub.get_period() if sub else ""
    sub_membership_period = sub_memebership.get_period() if sub_memebership else ""

    logger.info('sub_period: {}'.format(sub_period))
    logger.info('sub_membership_period: {} - {}'.format(sub_membership_period, sub_memebership))

    # get sub meta
    sub_meta = sub.meta if sub else None

    got_plan = False
    got_plan_name = '-'
    got_membership_plan = False
    got_membership_plan_name = '-'

    try:
        if sub_meta is not None:
            # extract the plan_id or plan_name from the meta
            if 'plan_id' in sub_meta:
                got_plan = True
                plan_id = sub_meta.get('plan_id', None)
            if 'membership_plan_id' in sub_meta:
                got_membership_plan = True
                membership_plan_id = sub_meta.get('membership_plan_id', None)
            if 'plan_name' in sub_meta:
                got_plan_name = sub_meta.get('plan_name', '-')
            if 'membership_plan_name' in sub_meta:
                got_membership_plan_name = sub_meta.get('membership_plan_name', '-')

            # get plan id if it is not None or ''
            if plan_id is not None and plan_id != '' and \
                    sub.sport_association.subscription_fee_plans and \
                    got_plan_name == '-':
                plans = sub.sport_association.subscription_fee_plans
                for plan in plans:
                    if plan['id'] == plan_id:
                        got_plan_name = plan['name']
                        break
            if membership_plan_id is not None and \
                    membership_plan_id != '' and \
                    got_membership_plan_name == '-':
                plans = sub.sport_association.membership_fee_plans
                for plan in plans:
                    if plan['id'] == membership_plan_id:
                        got_membership_plan_name = plan['name']
                        break
    except Exception as e:
        logger.error(e)

    logger.info('got_plan_name: {}'.format(got_plan_name))
    logger.info('got_membership_plan_name: {}'.format(got_membership_plan_name))
    logger.info('got_plan: {}'.format(got_plan))
    logger.info('got_membership_plan: {}'.format(got_membership_plan))

    invoice_template = invoice.sport_association.get_invoice_template()
    extra_text_invoices = invoice.sport_association.extra_text_invoices
    if extra_text_invoices:
        # Remove HTML tags and check if any content remains
        cleaned_text = BeautifulSoup(extra_text_invoices, "html.parser").get_text().strip()
        if not cleaned_text:
            extra_text_invoices = None

    if payment is not None:
        if payment.payment_category is not None:
            # check if need to hide the payment category
            if invoice.sport_association.user.hide_category_name is True:
                payment.payment_category.name = ''


    default_payment_category = invoice.sport_association.user.default_payment_category
    hide_category_name = invoice.sport_association.user.hide_category_name

    if (default_payment_category is not None and payment.payment_category.deleted is False) or \
            (payment.payment_category is not None and not hide_category_name):
        override_label = True
    else:
        override_label = False

    # assure that is_multiple is found in sub.custom_data
    if sub and sub.custom_data and 'is_multiple' not in sub.custom_data:
        sub.custom_data['is_multiple'] = False
    elif sub and sub.custom_data and 'is_multiple' in sub.custom_data:
        sub.custom_data['is_multiple'] = bool(sub.custom_data['is_multiple'])

    return render(
        request,
        f'document/application/{invoice_template}',
        {
            "uid": uid,
            "extra_text_invoices": extra_text_invoices,
            "total_amount": total_amount,
            "body_style": body_style,
            "payment": payment,
            'course_name': payment.get_course_carnet_name(),
            "enumerate_invoices": enumerate_invoices,
            "payment_categories": payment_categories,
            "custom_data": sub.custom_data if sub else None,
            "stamp_free": stamp_free,
            "invoice": invoice,
            "payment_method": payment_method,
            "sub": sub,
            "sub_memebership": sub_memebership,
            "sub_period": sub_period,
            "sub_membership_period": sub_membership_period,
            "override_label": override_label,
            "got_plan_name": got_plan_name,
            "got_membership_plan_name": got_membership_plan_name,
            "got_plan": got_plan,
            "got_membership_plan": got_membership_plan,
            "header": header,
            "footer": footer
        }
    )



@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated|HasDocumentToken|HasBypassAuthorizationHeader])
def document_einvoice(request, uid):

    try:
        logger.info('document_invoice {}'.format(uid))
        try:
            customer_invoice = CustomerInvoice.objects.get(customer_invoice_id=uid)
        except Invoice.DoesNotExist:
            return Response({'exception': 'Invoice not found'}, status=status.HTTP_404_NOT_FOUND)
        filename = f"[{customer_invoice.sport_association.denomination}] Fattura {customer_invoice.prefix} {customer_invoice.number}/{customer_invoice.fiscal_year}.pdf"
        document = Document.objects.create(filename=filename)
        printing_service = PrintingService()
        headers = {
            'Authorization': request.headers.get('authorization')
         }
        url = 'document/einvoice/' + str(uid) + '/view'
        response = printing_service.print_and_store_pdf(request, document=document, url=url, headers=headers)
        if response.status_code == 200:
            customer_invoice.pdf = document
            customer_invoice.save()
        return response
    except TypeError as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated|HasBypassAuthorizationHeader])
def document_einvoice_view(request, uid):
    invoice = CustomerInvoice.objects.get(pk=uid)
    einvoice = generate_invoice_html(invoice)
    return HttpResponse(einvoice, content_type='text/html; charset=utf-8')

@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated | HasDocumentToken])
def document_compensation(request, uid):
    try:
        try:
            payment = Payment.objects.get(payment_id=uid)
        except Payment.DoesNotExist:
            return Response({'exception': 'Compensation not found'}, status=status.HTTP_404_NOT_FOUND)
        filename = '[{}] Compenso per {} del {}.pdf'.format(
            payment.sport_association.denomination,
            payment.instructor.first_name + ' ' + payment.instructor.last_name,
            payment.payment_date.date())
        document = Document.objects.create(filename=filename)
        printing_service = PrintingService()
        headers = {
            'Authorization': request.headers.get('authorization')
         }
        url = 'document/compensation/' + str(uid) + '/view'
        response = printing_service.print_and_store_pdf(request, document=document, url=url, headers=headers)
        if response.status_code == 200:
            # get all instructor hours with the same payment
            instructor_hours = InstructorHours.objects.filter(payment=payment).iterator(chunk_size=100)
            for instructor_hour in instructor_hours:
                instructor_hour.document = document
                instructor_hour.save()
        return response
    except TypeError as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def document_compensation_view(request, uid):
    payment = Payment.objects.get(payment_id=uid)

    user_language = 'it'
    translation.activate(user_language)

    from_date, to_date = None, None

    # get hours for the payment
    instructor_hours = InstructorHours.objects.filter(payment=payment).iterator(chunk_size=100)

    dates = []

    for instructor_hour in instructor_hours:
        if instructor_hour.compensation_type == 'hourly':
            # convert utc date to rome timezone
            dates.append(instructor_hour.date)
        else:
            # check period
            splitted_dates = instructor_hour.period.split(' al ')

            # convert string to date
            from_date = datetime.strptime(splitted_dates[0], '%d/%m/%Y').date()
            to_date = datetime.strptime(splitted_dates[1], '%d/%m/%Y').date()
            dates.append(from_date)
            dates.append(to_date)

    from_date = min(dates) if dates else from_date
    to_date = max(dates) if dates else to_date

    return render(
        request,
        'document/application/compensation.html',
        {
            "payment": payment,
            "from_date": from_date,
            "to_date": to_date,
        }
    )

@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated | HasDocumentToken])
def document_template(request, uid):
    try:
        try:
            template = SportAssociationModuleTemplates.objects.get(
                pk=uid,
                sport_association=request.user.sport_association
            )
        except SportAssociationModuleTemplates.DoesNotExist:
            return Response({'exception': 'Template not found'}, status=status.HTTP_404_NOT_FOUND)
        filename = '[{}] {} del {}.pdf'.format(
            template.sport_association.denomination,
            str(template.name).replace(',', ' ').replace('.', ' '),
            datetime.now().strftime('%d/%m/%Y')
        )
        document = Document.objects.create(filename=filename)

        query = '?'
        # check if the request.data includes additionalData
        if 'additionalData' in request.data:
            additional_data = request.data.get('additionalData', None)
            # check if there is "parent" key
            if 'parent' in additional_data:
                parent = additional_data.get('parent', None)
                try:
                    folder = Folder.objects.get(id=parent)
                    SportAssociationDocumentsArchive.objects.create(
                        sport_association=request.user.sport_association,
                        folder=folder,
                        document=document
                    )
                except Exception as e:
                    pass
            # filter_mentions
            if 'subscription' in additional_data:
                query += f'subscription_id={additional_data["subscription"]}&'
                try:
                    subscription = Subscription.objects.get(subscription_id=additional_data["subscription"])
                    SubscriptionFile.objects.create(document=document, subscription=subscription)
                except Exception as e:
                    pass

        printing_service = PrintingService()
        headers = {
            'Authorization': request.headers.get('authorization')
         }
        url = 'document/template/' + str(uid) + '/view' + query
        response = printing_service.print_and_store_pdf(request, document=document, url=url, headers=headers)


        return Response({"msg": "Generato con successo."}, status=status.HTTP_200_OK)
    except TypeError as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated|HasBypassAuthorizationHeader])
def document_template_view(request, uid):
    template = SportAssociationModuleTemplates.objects.get(pk=uid)
    sport_association = template.sport_association

    # get query params
    subscription_id = request.GET.get('subscription_id', None)

    context_objects = {
        'sport_association': sport_association,
        'other': {
            'today': datetime.now().strftime('%d/%m/%Y')
        }
    }

    # get subscription
    courses_list = []
    try:
        subscription = Subscription.objects.get(subscription_id=subscription_id)
        courses_list = subscription.get_courses_list()
        context_objects['associate'] = subscription.associate
        context_objects['main_tutor'] = subscription.associate.get_main_tutor()
        context_objects['other']['courses_list'] = courses_list
    except Exception as e:
        pass

    footer = sport_association.invoice_footer if not template.custom_footer_header else template.custom_footer
    header = sport_association.document_header if not template.custom_footer_header else template.custom_header
    if footer is None:
        footer = ''

    if header is None:
        header = ''

    if template.header:
        # need to filter_mentions the header
        header = filter_mentions(header, context_objects={
            'sport_association': sport_association,
            'other': {
                'today': datetime.now().strftime('%d/%m/%Y'),
                'courses_list': courses_list
            }
        })

    if template.footer:
        # filter_mentions the footer
        footer = filter_mentions(footer, context_objects={
            'sport_association': sport_association,
            'other': {
                'today': datetime.now().strftime('%d/%m/%Y'),
                'courses_list': courses_list
            }
        })

    template_content = filter_mentions(template.template, context_objects=context_objects)

    return render(
        request,
        'document/application/model.html',
        {
            "template": template_content,
            "header": header if template.header else '',
            "footer": footer if template.footer else ''
        }
    )

@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated | HasDocumentToken])
def document_medical_appointment(request, uid):
    try:
        appointment = MedicalAppointments.objects.filter(
            medical_appointments_id=uid
        ).select_related('subscription', 'subscription__sport_association', 'subscription__associate').first()
        filename = '[{}] {} del {}.pdf'.format(
            appointment.subscription.sport_association.denomination,
            MedicalAppointments.REGIONS[appointment.region],
            appointment.date)
        document = Document.objects.create(filename=filename)
        printing_service = PrintingService()
        headers = {
            'Authorization': request.headers.get('authorization')
         }
        url = 'document/medical-appointment/' + str(uid) + '/view'

        if appointment.region in ['agonistica', 'non-agonistica']:
            response = printing_service.print_and_store_pdf(request, document=document, url=url, headers=headers)
        else:
            # template
            template = appointment.get_template()
            # get variables to fill
            fields = get_pdf_fields(template)

            logger.info(f"fields: {fields}")

            mapped_fields = extract_values(
                keys=[list(field.keys())[0].replace('-', '.') for field in fields],
                context_objects={
                    'sport_association': appointment.subscription.sport_association,
                    'associate': appointment.subscription.associate,
                    'main_tutor': appointment.subscription.associate.main_tutor,
                    'medical_appointment': appointment,
                    'other': {
                        'today': datetime.now().strftime('%d/%m/%Y'),
                        'today_datetime': appointment.creation_date.strftime('%d/%m/%Y %H:%M'),
                        'address': appointment.address,
                    }
                }
            )
            # set all fields to upper case and prepend 2 whitespaces
            mapped_fields = {k: f"  {v.upper() if 'base64' not in v else v}" for k, v in mapped_fields.items()}

            # get data formatted in YYYY-MM-DD in the mapped_fields values and convert to DD/MM/YYYY

            for key, value in mapped_fields.items():
                logger.info(f"Key: {key} Value: {value}")
                value = str(value).strip()
                try:
                    if value and len(value) == 10 and value[4] == '-' and value[7] == '-':
                        mapped_fields[key] = value[8:10] + '/' + value[5:7] + '/' + value[0:4]
                        logger.info(f"Converted date {value} to {mapped_fields[key]}")
                except Exception as e:
                    logger.info(f"Error converting date: {e}")
                    pass

            response = printing_service.fill_pdf_and_store(
                request,
                document=document,
                template=template,
                fields=mapped_fields
            )
        if response.status_code == 200:
            appointment.document = document
            appointment.save()
        return response
    except TypeError as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
# @permission_classes([IsAuthenticated])
def document_medical_appointment_view(request, uid):
    is_valid_uuid(uid)

    appointment = MedicalAppointments.objects.filter(
        medical_appointments_id=uid
    ).select_related('subscription', 'subscription__sport_association', 'subscription__associate').first()

    # check medical appointment is of type "agonistica" or "non-agonistica"
    if appointment.region not in ['agonistica', 'non-agonistica']:
        return Response(
            {'error': 'This kind of medical appointment is not supported in this endpoint'},
            status=status.HTTP_303_SEE_OTHER
        )

    user_language = 'it'
    translation.activate(user_language)

    return render(
        request,
        f"document/medical-appointments/{appointment.region}.html",
        {
            "medical_appointment": appointment,
            "sport_association": appointment.subscription.sport_association,
            "associate": appointment.subscription.associate,
            "meta": appointment.meta
        }
    )
