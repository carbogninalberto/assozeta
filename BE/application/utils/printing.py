import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta
from functools import partial
from io import BytesIO
from typing import Dict
from zipfile import ZipFile, ZIP_DEFLATED

import requests
from django.core.files.temp import NamedTemporaryFile
from django.db.models import Q
from django.http import HttpResponse, FileResponse
from django.utils import timezone
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, A3, A2
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm

from rest_framework import status
from rest_framework.response import Response

from application.models import Payment, CourseSubscription, CourseTags
from application.models.carnet_models import CarnetSubscription
from application.models.invoices_models import Invoice
from application.models.subscriptions_models import Subscription, SubscriptionMembership, Tags
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

from application.utils.api_utils import BalanceSheetData
from application.utils.excel_utils import get_excel_base64
from core.settings import PUPPETEER_PORT, PUPPETEER_HOST, STORAGE_DIR, CURRENT_HOST_PUPPETEER
from docmanager.tasks import save_to_storage
from docmanager.views.utils import fill_pdf_fields

logger = logging.getLogger(__name__)


class PrintingTemplates:
    SUBSCRIPTION = 'document/subscription/{}/view'


class Puppeteer:

    def __init__(self, current_host='host.docker.internal', current_port=8000, type_tag='pdf',
                 print_background='true', margin={'top': '1cm', 'bottom': '1cm'}):
        self.current_host = current_host
        self.current_port = current_port
        self.type_tag = type_tag
        self.print_background = print_background
        self.margin = json.dumps(margin)
        if PUPPETEER_PORT != 80:
            self.service = str('http://{}:{}/?'.format(PUPPETEER_HOST, PUPPETEER_PORT))
            self.fast_service = str('http://{}:{}/render'.format(PUPPETEER_HOST, PUPPETEER_PORT))
        else:
            self.service = str('http://{}/?'.format(PUPPETEER_HOST))
            self.fast_service = str('http://{}/render'.format(PUPPETEER_HOST))

    def pdf_from_url(self, url: str, headers, local=True):
        """
        It accepts a string url to convert into a pdf. It returns a status code: True or False, and a text, the pdf
        in bytes or the exception.
        if local=True => pass an url similar to: document/print
        otherwise pass a complete url.
        """
        logger.debug('[Puppeteer => pdf_from_url(): url={} local={}'.format(url, local))
        try:
            # if local:
            #     url = '{}/{}'.format(CURRENT_HOST_PUPPETEER, url)
            #
            #     # if self.current_port != 80 and self.current_host != 'localhost':
            #     #     url = 'http://{}:{}/{}'.format(self.current_host, self.current_port, url)
            #     # else:
            #     #     url = 'https://{}/{}'.format(self.current_host, url)
            # logger.debug(f'url: {url} service: {self.service}')
            # request_url = f"{self.service}" \
            #               f"url={url}&" \
            #               f"type={self.type_tag}&" \
            #               f"printBackground={self.print_background}&" \
            #               f"margin={self.margin}&" \
            #               f"headers={json.dumps(headers)}"
            #
            # logger.debug(f"request_url: {request_url}")
            # document = requests.get(request_url)

            # get from fast render
            document = FastRender.get_pdf(url, headers)

            return True, document.content
        except Exception as e:
            return False, e



class FastRender:
    render_url = 'http://{}:{}/render'.format(PUPPETEER_HOST, PUPPETEER_PORT)
    headers = {
        'Content-Type': 'application/json',
    }

    @staticmethod
    def get_pdf(url, headers={}):
        logger.info(f"FastRender.get_pdf: url={url}")
        payload = {
          # "apiKey": "",
          "type": "pdf",
          "url": '{}/{}'.format(CURRENT_HOST_PUPPETEER, url),
          "async": False,
          "headers": headers,
          "device": {
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "scale": 1,
            "width": 1920,
            "height": 1080
          },
          "render": {
            "block": {
              "cookies": False,
              "ads": False,
              "trackers": False,
              "banners": False
            },
            "waitTime": 0,
            "timeout": 30000,
            "fullPage": False,
            "triggerLazyAnimations": False,
            "scroll": {
              "position": 0,
              "animate": False,
              "duration": 0,
              "animation": ""
            },
            "waitUntil": "networkidle2"
          },
          "pdf": {
            "scale": 1,
            "displayHeaderFooter": False,
            "headerTemplate": "",
            "footerTemplate": "",
            "printBackground": True,
            "landscape": False,
            "pageRanges": "",
            "format": "A4",
            "margin": {
              "top": "1cm",
              "right": "0px",
              "bottom": "1cm",
              "left": "0px"
            },
            "preferCSSPageSize": False,
            "omitBackground": False,
            "timeout": 30000
          }
        }

        return requests.post(
            FastRender.render_url,
            headers=FastRender.headers,
            verify=False,
            data=json.dumps(payload)
        )

    def render(self, url, config):

        response = requests.post(url, json=config)
        pass


class PrintingService:
    puppeteer = Puppeteer()
    fastrender = FastRender()

    def fill_pdf_and_store(self, request, document, template, fields: Dict):
        try:
            storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()), str(document.document_id))
            file = os.path.join(storing_path, document.filename)
            file_bytes = fill_pdf_fields(pdf_path=template, replace_dict=fields)
            # save the file
            default_storage.save(file, ContentFile(file_bytes))

            with default_storage.open(file, 'rb') as pdf:
                # building the response
                response = HttpResponse(pdf, content_type='application/pdf')
                content = "inline; filename={}".format(document.filename)
                download = request.GET.get("download")
                if download:
                    content = "attachment; filename={}".format(document.filename)
                response['Content-Disposition'] = content
                return response
        except Exception as e:
            return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def print_and_store_pdf(self, request, document, url, headers):
        try:
            storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()), str(document.document_id))
            file = os.path.join(storing_path, document.filename)

            # Create the storing path if not present
            # with default_storage not required, automatically created
            # if not exists(storing_path):
            #     os.makedirs(storing_path)

            puppeeter = Puppeteer()
            # puppeeter = FastRender()
            rendered_status, rendered_pdf = puppeeter.pdf_from_url(url, headers)

            if rendered_status:

                #default_storage.save(file, ContentFile(rendered_pdf))

                # Convert PDF binary to base64 string for serialization
                pdf_base64 = base64.b64encode(rendered_pdf).decode('utf-8')

                # Queue the storage task with serializable data
                save_to_storage.delay(file, pdf_base64)

                # with default_storage.open(file, 'wb+') as pdf:
                #     pdf.write(rendered_pdf)

                # Return directly from memory instead of re-downloading
                response = HttpResponse(rendered_pdf, content_type='application/pdf')
                disposition = "attachment" if request.GET.get("download") else "inline"
                response['Content-Disposition'] = f'{disposition}; filename={document.filename}'
                return response


                # with default_storage.open(file, 'rb') as pdf:
                #     # building the response
                #     response = HttpResponse(pdf, content_type='application/pdf')
                #     content = "inline; filename={}".format(document.filename)
                #     download = request.GET.get("download")
                #     if download:
                #         content = "attachment; filename={}".format(document.filename)
                #     response['Content-Disposition'] = content
                #     return response
                # raise Exception
            else:
                # rendered_pdf contains the error when rendered_status is False
                raise Exception(f"PDF rendering failed: {rendered_pdf}")

        except Exception as e:
            return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def download_file(self, request, document, token):
        try:
            if document.filepath:
                # retrieve from filepath
                file = os.path.join(STORAGE_DIR, str(document.filepath))
            else:
                storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()),
                                            str(document.document_id))
                file = os.path.join(storing_path, document.filename)

            if not default_storage.exists(file):
                header_auth_token = request.headers.get('authorization', None)
                if header_auth_token is None and token is not None:
                    header_auth_token = 'Bearer {}'.format(token)
                subscription_documents = Subscription.objects.filter(document_pdf=document)
                invoice_documents = Invoice.objects.filter(document_pdf=document)
                headers = {
                    'Authorization': header_auth_token
                }
                if len(subscription_documents) == 1:
                    url = 'document/subscription/' + str(subscription_documents.first().subscription_id) + '/view'
                elif len(invoice_documents) == 1:
                    url = 'document/invoice/' + str(invoice_documents.first().invoice_id) + '/view'
                else:
                    raise Exception("File not found")
                self.print_and_store_pdf(request, document, url, headers)

            # open the file in read bytes mode
            with default_storage.open(file, 'rb') as pdf:
                content_type = 'application/pdf'
                # check if document is an image
                if document.filename.endswith('.jpg') or document.filename.endswith('.jpeg'):
                    content_type = 'image/jpeg'
                elif document.filename.endswith('.png'):
                    content_type = 'image/png'
                # building the response
                response = HttpResponse(pdf, content_type=content_type)
                content = "inline; filename={}".format(document.filename.replace(',', ' '))
                download = request.GET.get("download")
                if download:
                    content = "attachment; filename={}".format(document.filename.replace(',', ' '))
                response['Content-Disposition'] = content
                return response
            raise Exception

        except Exception as e:
            return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def download_multiple_files(self, documents, export_base64=True, export_binary=False, save_path=None):
        try:
            # Use a temporary file instead of BytesIO for large files
            with NamedTemporaryFile(delete=False, suffix='.zip') as temp_zip:
                with ZipFile(temp_zip.name, 'w', compression=ZIP_DEFLATED) as zip_file:
                    for idx, document in enumerate(documents):
                        try:
                            # Determine file path
                            if document.filepath:
                                file = os.path.join(STORAGE_DIR, str(document.filepath))
                            else:
                                storing_path = os.path.join(
                                    STORAGE_DIR,
                                    str(document.creation_date.timestamp()),
                                    str(document.document_id)
                                )
                                file = os.path.join(storing_path, document.filename)

                            # Stream the file directly to zip without loading entire content into memory
                            zip_name = f"{idx + 1}_{document.filename}"
                            with default_storage.open(file, 'rb') as doc_file:
                                # Use copyfileobj to stream in chunks
                                zip_file.writestr(zip_name, doc_file.read())

                        except Exception as e:
                            logger.error(f"Error processing document {document.document_id}: {str(e)}")
                            continue

                # Handle different export types
                if save_path:
                    if export_binary:
                        default_storage.save(save_path, ContentFile(temp_zip.read()))

                elif export_base64:
                    # Stream base64 encoding for response
                    with open(temp_zip.name, 'rb') as f:
                        return base64_encode_file_to_string(f), 'documents.zip'

                elif export_binary:
                    with open(temp_zip.name, 'rb') as f:
                        return f.read(), 'documents.zip'

                # Default HTTP response
                response = FileResponse(
                    open(temp_zip.name, 'rb'),
                    content_type='application/zip',
                    filename='documents.zip'
                )
                return response

        except Exception as e:
            logger.error(f"Error creating zip file: {str(e)}")
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        finally:
            # Clean up temporary file
            if 'temp_zip' in locals():
                os.unlink(temp_zip.name)

    def view_file(self, request, document, token):
        try:
            if document.filepath:
                file_path = os.path.join(STORAGE_DIR, str(document.filepath))
            else:
                storing_path = os.path.join(STORAGE_DIR, str(document.creation_date.timestamp()),
                                            str(document.document_id))
                file_path = os.path.join(storing_path, document.filename)

            logger.info(f"view_file: file_path={file_path}")

            if not default_storage.exists(file_path):
                header_auth_token = request.headers.get('authorization', None)
                if header_auth_token is None and token is not None:
                    header_auth_token = 'Bearer {}'.format(token)
                subscription_documents = Subscription.objects.filter(document_pdf=document)
                invoice_documents = Invoice.objects.filter(document_pdf=document)
                headers = {
                    'Authorization': header_auth_token
                }
                if len(subscription_documents) == 1:
                    url = 'document/subscription/' + str(subscription_documents.first().subscription_id) + '/view'
                elif len(invoice_documents) == 1:
                    url = 'document/invoice/' + str(invoice_documents.first().invoice_id) + '/view'
                else:
                    raise Exception("File not found")
                self.print_and_store_pdf(request, document, url, headers)

            # Open the file in read bytes mode
            with default_storage.open(file_path, 'rb') as file:
                content_type = 'application/pdf'
                # Check if document is an image
                if document.filename.endswith(('.jpg', '.jpeg')):
                    content_type = 'image/jpeg'
                elif document.filename.endswith('.png'):
                    content_type = 'image/png'
                # Build the response
                response = HttpResponse(file, content_type=content_type)
                return response

        except Exception as e:
            return Response({'exception': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# SECTION FOR MISC PRINTING DOCUMENTS
def generate_xlsx_from_data(rows, columns):
    excel_base64 = get_excel_base64(
        *rows,
        columns=columns
    )
    if excel_base64 is None:
        raise Exception("Error while generating excel file")
    return excel_base64


def header_footer(canvas, doc, title, isA3=False, landscape=True):
    canvas.saveState()
    # Use actual page size from doc instead of hardcoded A3/A2
    page_width, page_height = doc.pagesize
    x = page_width / 2.0
    # Header
    canvas.setFont('Helvetica-Bold', 9)
    canvas.drawCentredString(x, page_height - 1 * cm, title)
    # Footer
    canvas.setFont('Helvetica', 9)
    page_number_text = "Pagina %d" % doc.page
    canvas.drawCentredString(x, 1 * cm, page_number_text)
    canvas.restoreState()


def generate_pdf_from_data(rows, columns, title='', scale=3.5, align=None, header=None, isA3=False, landscape=True, additional_data=None):
    buffer = BytesIO()

    # check if align is an array of strings
    if align is not None and isinstance(align, list):
        if len(align) != len(columns):
            raise Exception("Align must have the same length as columns")
        for i in range(len(align)):
            if align[i] not in ['LEFT', 'CENTER', 'RIGHT']:
                raise Exception("Align must be one of ['LEFT', 'CENTER', 'RIGHT']")

    # Check that all rows have the same number of elements as columns
    assert all(len(row) == len(columns) for row in rows), "Row lengths do not match columns."

    style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.white),
        ('TEXTCOLOR',(0,0),(-1,0),colors.black),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('BACKGROUND',(0,1),(-1,-1),colors.white),
        ('BOX', (0,0), (-1,-1), 1, colors.black),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
    ])

    # Add column-specific alignment if provided
    if align is not None:
        for col_idx, alignment in enumerate(align):
            # Convert the alignment string to ReportLab format
            reportlab_alignment = alignment.upper()
            style.add('ALIGN', (col_idx, 0), (col_idx, -1), reportlab_alignment)


    data = [columns] + rows
    table = Table(data)
    table.setStyle(style)

    def get_text_width(text, font_size=11):
        # This is a simplified example - you'd need to implement actual font metrics
        return len(str(text)) * (font_size / 12)  # rough approximation

    try:
        # Calculate the necessary widths for each column dynamically
        colWidths = [max(get_text_width(str(word)) for word in column) / scale
                     for column in zip(*data)]
        colWidths = [max(width, 1) * cm for width in colWidths]  # Ensure minimum width
        table._argW = colWidths
    except IndexError:
        raise Exception("Error calculating column widths. Check that all rows have the same number of elements.")

    # Auto-select page size: pick the smallest that fits the table width
    margins = 30 + 30  # leftMargin + rightMargin
    total_table_width = sum(colWidths)
    page_sizes = [A4, A3, A2]  # smallest to largest
    selected_size = A2  # fallback to largest
    for ps in page_sizes:
        available_width = (ps[1] if landscape else ps[0]) - margins
        if total_table_width <= available_width:
            selected_size = ps
            break

    # Allow caller to force A3 minimum (e.g. balance sheet)
    if isA3 and selected_size == A4:
        selected_size = A3

    orientation = (selected_size[1], selected_size[0]) if landscape else (selected_size[0], selected_size[1])
    doc = SimpleDocTemplate(buffer, pagesize=orientation, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)


    header_table = None

    # set header if available as string
    if header is not None:
        # Extract the base64 image data
        img_data = None
        img_match = re.search(r'src="data:image/jpeg;base64,([^"]+)"', header)
        if img_match:
            img_data = img_match.group(1)

        # Extract text components
        title_match = re.search(r'<h1[^>]*>(.*?)</h1>', header)
        title_text = title_match.group(1) if title_match else "Associazione Sportiva"

        subtitle_match = re.search(r'<h2[^>]*>(.*?)</h2>', header)
        subtitle_text = subtitle_match.group(1) if subtitle_match else "Bilancio"

        approve_match = re.search(r'<h3[^>]*>(.*?)</h3>', header)
        approve_text = approve_match.group(1) if approve_match else "Approvato il __/__/____"

        # Create a 3-column table to mimic the flex layout
        # Column 1: Logo
        # Column 2: Title and subtitle
        # Column 3: Date

        # Create the logo cell
        if img_data:
            try:
                img_stream = BytesIO(base64.b64decode(img_data))
                img = Image(img_stream)
                img.drawHeight = 100
                img.drawWidth = img.drawHeight * (img.imageWidth / img.imageHeight)
                logo_cell = img
            except Exception:
                # Fallback if image processing fails
                styles = getSampleStyleSheet()
                logo_cell = Paragraph("[LOGO]", styles['Normal'])
        else:
            styles = getSampleStyleSheet()
            logo_cell = Paragraph("[LOGO]", styles['Normal'])

        # Create title and subtitle cell
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'Title',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=20,
            leading=24,
            spaceBefore=30,
            spaceAfter=10,
        )

        subtitle_style = ParagraphStyle(
            'Subtitle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=16,
            leading=20,
            spaceAfter=10,
        )

        approve_style = ParagraphStyle(
            'Subtitle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=16,
            leading=20,
        )

        title_para = Paragraph(title_text, title_style)
        subtitle_para = Paragraph(subtitle_text, subtitle_style)
        approve_para = Paragraph(approve_text, approve_style)


        # Create the table
        table_data = [["", logo_cell, [title_para, subtitle_para, approve_para], ""]]
        col_widths = [A2[1] / 4,A2[1] / 8,(A2[1] / 8) * 3,A2[1] / 4]  # Adjust column widths

        header_table = Table(table_data, colWidths=col_widths)
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, 0), 20),
            ('ALIGN', (0, 0), (0, 0), 'RIGHT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('ALIGN', (2, 0), (2, 0), 'LEFT'),
            ('ALIGN', (3, 0), (3, 0), 'LEFT'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 20),
        ]))


    if header_table:
        elements = [header_table, table]
    else:
        elements = [table]

    # add data if is not None
    if additional_data is not None:
        for key in additional_data.keys():
            styles = getSampleStyleSheet()
            data_style = ParagraphStyle(
                'Subtitle',
                parent=styles['Normal'],
                fontName='Helvetica',
                alignment=TA_CENTER,
                fontSize=11,
                leading=11,
                spaceBefore=10,
            )
            data_paragraph = Paragraph(additional_data[key], data_style)
            elements.append(data_paragraph)

    on_every_page = partial(header_footer, title=title, isA3=isA3, landscape=landscape)
    doc.build(elements, onFirstPage=on_every_page, onLaterPages=on_every_page)

    pdf_data = buffer.getvalue()
    buffer.close()
    pdf_base64 = base64.b64encode(pdf_data).decode('utf-8')
    return pdf_base64


def basic_print_data_parser(sport_association, data):
    if sport_association is None:
        return None
    if data is None:
        return None

    if data['current_year'] not in [0, 1]:
        raise Exception('Current year not valid')

    if data['format'] not in ['pdf', 'excel']:
        raise Exception('Format not valid')

    current_year = True if data['current_year'] == 1 else False
    file_format = data['format']  # 'pdf' or 'excel'

    # get current balancesheet start date end date based on the year
    current_date = datetime.now()

    current_date_from, current_date_to = BalanceSheetData.get_range_from_year_and_starting_date(
        date=current_date,
        starting_day=sport_association.user.balance_sheet_start_day,
        starting_month=sport_association.user.balance_sheet_start_month
    )
    return current_date_from, current_date_to, current_year, file_format


def _get_phone_columns(associate):
    """Return (phone, tutor_phone, contact_type) for reports."""
    phone = associate.phone or '-'
    tutor_phone = '-'
    contact_type = 'Socio'
    if associate.is_minor_now:
        tutor = associate.main_tutor
        if tutor and tutor.phone:
            tutor_phone = tutor.phone
            contact_type = 'Tutore'
    return phone, tutor_phone, contact_type


def filter_subscriptions(sport_association, data, medical=False, only_members=False):
    subscriptions = Subscription.objects.filter(
        sport_association=sport_association,
        archived=False
    )

    if only_members is True:
        subscriptions = subscriptions.filter(type__in=[Subscription.MEMBER_ONLY, Subscription.ASSOCIATE_AND_MEMBER])

    if medical:
        subscriptions = subscriptions.filter(medical__isnull=False)

    if 'year' in data['filters']:
        today = timezone.now()
        if data['filters']['year'] == 'pre-iscrizioni':
            subscriptions = subscriptions.filter(
                Q(start_date__gt=today)
            )
        elif data['filters']['year'] == 'corrente':
            subscriptions = subscriptions.filter(
                Q(start_date__lte=today) &
                Q(end_date__gte=today)
            )
        elif data['filters']['year'] == 'precedenti':
            subscriptions = subscriptions.filter(
                Q(end_date__lt=today)
            )

    # if 'period' in data['filters']:
    #     period_start = datetime.strptime(data['filters']['period']['from_date'], '%d/%m/%Y').date()
    #     period_end = datetime.strptime(data['filters']['period']['to_date'], '%d/%m/%Y').date()
    #
    #     subscriptions = subscriptions.filter(
    #         end_date__gte=period_start,  # Subscription ends on or after period starts
    #         start_date__lte=period_end  # Subscription starts on or before period ends
    #     )
    if 'status' in data['filters']:
        subscriptions = subscriptions.filter(status_flag__in=data['filters']['status'])

    subscriptions = subscriptions.select_related('medical', 'associate').prefetch_related('associate__tutor_relations__tutor')
    return subscriptions


def expiring_medical_certificates(sport_association=None, data=None):
    try:
        file_format = data['format'] if 'format' in data else 'pdf'
        subscriptions = filter_subscriptions(sport_association, data, medical=True, only_members=True)

        today = datetime.date(datetime.today())


        # get all the subscriptions with medical certificate expiring in 30 days or less
        subscriptions = [subscription for subscription in subscriptions if subscription.medical is not None and
                         subscription.medical.expiration_date is not None and
                         (subscription.medical.expiration_date - today).days <= 30 and \
                         (subscription.medical.expiration_date - today).days >= 0]

        subscriptions = sorted(subscriptions, key=lambda x: (x.medical.expiration_date - today).days)

        # cols and rows
        phone_data = [_get_phone_columns(s.associate) for s in subscriptions]
        columns = [
            'Nome',
            'Cognome',
            'Email',
            'Telefono',
            'Tel. Tutore',
            'Contatto',
            'Data di nascita',
            'età',
            'Scadenza certificato medico',
            'Giorni rimanenti',
        ]
        rows = [
            [s.associate.first_name for s in subscriptions],
            [s.associate.last_name for s in subscriptions],
            [s.associate.email or '-' for s in subscriptions],
            [d[0] for d in phone_data],
            [d[1] for d in phone_data],
            [d[2] for d in phone_data],
            [s.associate.born_date.strftime('%d/%m/%Y') for s in subscriptions],
            [s.associate.calculate_age() for s in subscriptions],
            [s.medical.expiration_date.strftime('%d/%m/%Y') for s in subscriptions],
            [(s.medical.expiration_date - today).days for s in subscriptions],
        ]

        if file_format == 'pdf':
            logger.debug(f"[expiring_medical_certificates] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                "Certificati medici in scadenza (entro 30 giorni)",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"Certificati medici in scadenza (entro 30 giorni dal {today.strftime('%d/%m/%Y')})"
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[expiring_medical_certificates] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                "Certificati medici in scadenza (entro 30 giorni)",
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[expiring_medical_certificates] error: {str(e)}")
        raise Exception('Error in parsing data')


def expired_medical_certificates(sport_association=None, data=None):
    try:

        file_format = data['format'] if 'format' in data else 'pdf'
        subscriptions = filter_subscriptions(sport_association, data, medical=True, only_members=True)

        today = datetime.date(datetime.today())

        # get all the subscriptions with medical certificate expired
        subscriptions = [subscription for subscription in subscriptions if subscription.medical is not None and
                         subscription.medical.expiration_date is not None and
                         (subscription.medical.expiration_date - today).days < 0]

        subscriptions = sorted(subscriptions, key=lambda x: (x.medical.expiration_date - today).days)

        # cols and rows
        phone_data = [_get_phone_columns(s.associate) for s in subscriptions]
        columns = [
            'Nome',
            'Cognome',
            'Email',
            'Telefono',
            'Tel. Tutore',
            'Contatto',
            'Data di nascita',
            'età',
            'Scadenza certificato medico',
            'Giorni rimanenti',
        ]
        rows = [
            [s.associate.first_name for s in subscriptions],
            [s.associate.last_name for s in subscriptions],
            [s.associate.email or '-' for s in subscriptions],
            [d[0] for d in phone_data],
            [d[1] for d in phone_data],
            [d[2] for d in phone_data],
            [s.associate.born_date.strftime('%d/%m/%Y') for s in subscriptions],
            [s.associate.calculate_age() for s in subscriptions],
            [s.medical.expiration_date.strftime('%d/%m/%Y') for s in subscriptions],
            [(s.medical.expiration_date - today).days for s in subscriptions],
        ]

        if file_format == 'pdf':
            logger.debug(f"[expired_medical_certificates] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                "Certificati medici scaduti",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"Certificati medici scaduti (giorno stampa {today.strftime('%d/%m/%Y')})"
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[expired_medical_certificates] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                "Certificati medici scaduti",
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[expired_medical_certificates] error: {str(e)}")
        raise Exception('Error in parsing data')


def empty_medical_certificates(sport_association=None, data=None):
    try:

        file_format = data['format'] if 'format' in data else 'pdf'
        subscriptions = filter_subscriptions(sport_association, data, only_members=True)

        today = datetime.date(datetime.today())

        # get all the subscriptions without medical certificate and age > 6 (exempt from medical certificate)
        subscriptions = [subscription for subscription in subscriptions if subscription.medical is None and
                         subscription.associate.calculate_age() >= 6]

        # cols and rows
        phone_data = [_get_phone_columns(s.associate) for s in subscriptions]
        columns = [
            'Nome',
            'Cognome',
            'Email',
            'Telefono',
            'Tel. Tutore',
            'Contatto',
            'Data di nascita',
            'età',
        ]
        rows = [
            [s.associate.first_name for s in subscriptions],
            [s.associate.last_name for s in subscriptions],
            [s.associate.email or '-' for s in subscriptions],
            [d[0] for d in phone_data],
            [d[1] for d in phone_data],
            [d[2] for d in phone_data],
            [s.associate.born_date.strftime('%d/%m/%Y') for s in subscriptions],
            [s.associate.calculate_age() for s in subscriptions],
        ]

        if file_format == 'pdf':
            logger.debug(f"[empty_medical_certificates] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                "Certificati medici non presenti (esclusi soggetti esenti)",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"Certificati medici non presenti esclusi soggetti esenti (giorno stampa {today.strftime('%d/%m/%Y')})"
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[empty_medical_certificates] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                "Certificati medici non presenti (esclusi soggetti esenti)",
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[empty_medical_certificates] error: {str(e)}")
        raise Exception('Error in parsing data')


def exempt_medical_certificates(sport_association=None, data=None):
    try:
        file_format = data['format'] if 'format' in data else 'pdf'
        subscriptions = filter_subscriptions(sport_association, data, only_members=True)

        today = datetime.date(datetime.today())

        # get all the subscriptions exempt from medical certificates
        subscriptions = [subscription for subscription in subscriptions if subscription.medical is None and
                         subscription.associate.calculate_age() <= 6]

        # cols and rows
        phone_data = [_get_phone_columns(s.associate) for s in subscriptions]
        columns = [
            'Nome',
            'Cognome',
            'Email',
            'Telefono',
            'Tel. Tutore',
            'Contatto',
            'Data di nascita',
            'età',
            'Scadenza certificato medico',
            'Giorni rimanenti',
        ]
        rows = [
            [s.associate.first_name for s in subscriptions],
            [s.associate.last_name for s in subscriptions],
            [s.associate.email or '-' for s in subscriptions],
            [d[0] for d in phone_data],
            [d[1] for d in phone_data],
            [d[2] for d in phone_data],
            [s.associate.born_date.strftime('%d/%m/%Y') for s in subscriptions],
            [s.associate.calculate_age() for s in subscriptions],
            [s.medical.expiration_date.strftime('%d/%m/%Y') if s.medical else '-' for s in subscriptions],
            [(s.medical.expiration_date - today).days if s.medical else '-' for s in subscriptions],
        ]

        if file_format == 'pdf':
            logger.debug(f"[exempt_medical_certificates] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                "Atleti esenti dai certificati medici",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"Atleti esenti dai certificati medici (giorno stampa {today.strftime('%d/%m/%Y')})"
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[exempt_medical_certificates] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                "Atleti esenti dai certificati medici",
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[exempt_medical_certificates] error: {str(e)}")
        raise Exception('Error in parsing data')


def expiring_subscriptions(sport_association=None, data=None):
    try:
        file_format = data['format'] if 'format' in data else 'pdf'
        subscriptions = filter_subscriptions(sport_association, data)

        today = datetime.date(datetime.today())

        # get all the subscriptions that are expiring in 30 days
        subscriptions = [
            subscription for subscription in subscriptions if
            (
                subscription.end_date is not None and
                30 >= (subscription.end_date - today).days >= 0
            )
        ]

        # cols and rows
        phone_data = [_get_phone_columns(s.associate) for s in subscriptions]
        columns = [
            'Nome',
            'Cognome',
            'Email',
            'Telefono',
            'Tel. Tutore',
            'Contatto',
            'Data di nascita',
            'età',
            'Giorni rimanenti',
        ]
        rows = [
            [s.associate.first_name for s in subscriptions],
            [s.associate.last_name for s in subscriptions],
            [s.associate.email or '-' for s in subscriptions],
            [d[0] for d in phone_data],
            [d[1] for d in phone_data],
            [d[2] for d in phone_data],
            [s.associate.born_date.strftime('%d/%m/%Y') for s in subscriptions],
            [s.associate.calculate_age() for s in subscriptions],
            [
                (s.end_date - today).days
                if s.end_date is not None
                else 'n.d.'
                for s in subscriptions
            ],
        ]

        if file_format == 'pdf':
            logger.debug(f"[expiring_subscriptions] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                "Iscrizioni in scadenza (entro 30gg)",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"Iscrizioni in scadenza entro 30gg (giorno stampa {today.strftime('%d/%m/%Y')})"
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[expiring_subscriptions] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                "Iscrizioni in scadenza (entro 30gg)",
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[expiring_subscriptions] error: {str(e)}")
        raise Exception('Error in parsing data')


def expiring_memberships(sport_association=None, data=None):
    try:
        file_format = data['format'] if 'format' in data else 'pdf'
        subscriptions = filter_subscriptions(sport_association, data)

        today = datetime.date(datetime.today())

        # get all the subscription_memberships expiring in 30 days or are expired
        associates = subscriptions.values_list('associate', flat=True)

        subscription_memberships = SubscriptionMembership.objects.filter(
            associate__in=associates,
            end_date__isnull=False,
            end_date__lte=today + timedelta(days=30)
        ).select_related('associate').prefetch_related('associate__tutor_relations__tutor')

        # cols and rows
        phone_data = [_get_phone_columns(s.associate) for s in subscription_memberships]
        columns = [
            'Tesseramento',
            'Nome',
            'Cognome',
            'Email',
            'Telefono',
            'Tel. Tutore',
            'Contatto',
            'Data di nascita',
            'età',
            'Giorni rimanenti',
        ]
        rows = [
            [s.membership_type for s in subscription_memberships],
            [s.associate.first_name for s in subscription_memberships],
            [s.associate.last_name for s in subscription_memberships],
            [s.associate.email or '-' for s in subscription_memberships],
            [d[0] for d in phone_data],
            [d[1] for d in phone_data],
            [d[2] for d in phone_data],
            [s.associate.born_date.strftime('%d/%m/%Y') for s in subscription_memberships],
            [s.associate.calculate_age() for s in subscription_memberships],
            [
                (s.end_date - today).days
                if s.end_date is not None
                else '-'
                for s in subscription_memberships
            ],
        ]

        if file_format == 'pdf':
            logger.debug(f"[expiring_memberships] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                "Tesseramenti in scadenza (entro 30gg o scaduti)",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"Tesseramenti in scadenza entro 30gg o scaduti (giorno stampa {today.strftime('%d/%m/%Y')})"
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[expiring_memberships] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                "Tesseramenti in scadenza (entro 30gg o scaduti)",
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[expiring_memberships] error: {str(e)}")
        raise Exception('Error in parsing data')


def all_subscriptions(sport_association=None, data=None):
    try:
        file_format = data['format'] if 'format' in data else 'pdf'
        subscriptions = filter_subscriptions(sport_association, data)

        today = datetime.date(datetime.today())

        # get all the subscriptions
        subscriptions = [
            subscription for subscription in subscriptions
        ]

        # cols and rows
        phone_data = [_get_phone_columns(s.associate) for s in subscriptions]
        columns = [
            'Nome',
            'Cognome',
            'Email',
            'Telefono',
            'Tel. Tutore',
            'Contatto',
            'Data di nascita',
            'età',
            'Nome tutore',
            'Cognome tutore',
        ]
        rows = [
            [s.associate.first_name for s in subscriptions],
            [s.associate.last_name for s in subscriptions],
            [s.associate.email or '-' for s in subscriptions],
            [d[0] for d in phone_data],
            [d[1] for d in phone_data],
            [d[2] for d in phone_data],
            [s.associate.born_date.strftime('%d/%m/%Y') for s in subscriptions],
            [s.associate.calculate_age() for s in subscriptions],
            [s.associate.main_tutor.first_name if s.associate.main_tutor else '-' for s in subscriptions],
            [s.associate.main_tutor.last_name if s.associate.main_tutor else '-' for s in subscriptions],
        ]

        if file_format == 'pdf':
            logger.debug(f"[all_subscriptions] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                f"Iscrizioni",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            title = f"Iscrizioni"
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=title
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[all_subscriptions] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                f"Iscrizioni",
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[all_subscriptions] error: {str(e)}")
        raise Exception('Error in parsing data')


def not_paid_quotes_subscriptions(sport_association=None, data=None):
    try:
        file_format = data['format'] if 'format' in data else 'pdf'
        subscriptions = filter_subscriptions(sport_association, data)

        today = datetime.date(datetime.today())

        # get the payments of type subscription
        payments = Payment.objects.filter(
            subject=Payment.SUBSCRIPTION,
            sport_association=sport_association,
            paid=False,
            associate__in=[s.associate for s in subscriptions],
            creation_date__lt=datetime.today()
        )

        if 'period' in data['filters']:
            period_start = datetime.strptime(data['filters']['period']['from_date'], '%d/%m/%Y').date()
            period_end = datetime.strptime(data['filters']['period']['to_date'], '%d/%m/%Y').date()

            payments = payments.filter(
                creation_date__gte=period_start,  # Subscription ends on or after period starts
                creation_date__lte=period_end  # Subscription starts on or before period ends
            )

        payments = payments.select_related('associate')

        # cols and rows
        phone_data = [_get_phone_columns(p.associate) for p in payments]
        columns = [
            'Nome',
            'Cognome',
            'Email',
            'Telefono',
            'Tel. Tutore',
            'Contatto',
            'quota (€)',
            'data',
            'ritardo pagamento (gg)'
        ]
        rows = [
            [p.associate.first_name for p in payments],
            [p.associate.last_name for p in payments],
            [p.associate.email or '-' for p in payments],
            [d[0] for d in phone_data],
            [d[1] for d in phone_data],
            [d[2] for d in phone_data],
            [str(p.amount).replace('.', ',') for p in payments],
            [p.payment_date.strftime('%d/%m/%Y') if p.payment_date else p.creation_date.strftime('%d/%m/%Y') for p in payments],
            [(today - p.payment_date.date()).days if p.payment_date else (today - p.creation_date.date()).days for p in payments]
        ]

        if file_format == 'pdf':
            logger.debug(f"[not_paid_quotes_subscriptions] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                f"Quote associative non pagate",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"Quote associative non pagate (giorno stampa {today.strftime('%d/%m/%Y')})"
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[not_paid_quotes_subscriptions] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                f"Quote associative non pagate",
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[not_paid_quotes_subscriptions] error: {str(e)}")
        raise Exception('Error in parsing data')


def not_paid_courses_subscriptions(sport_association=None, data=None):
    try:
        file_format = data['format'] if 'format' in data else 'pdf'
        subscriptions = filter_subscriptions(sport_association, data)

        today = datetime.date(datetime.today())

        # get the payments of type subscription
        payments = Payment.objects.filter(
            subject=Payment.COURSE,
            sport_association=sport_association,
            paid=False,
            associate__in=[s.associate for s in subscriptions],
            amount__gt=0,
            creation_date__lt=datetime.today()
        )

        if 'period' in data['filters']:
            period_start = datetime.strptime(data['filters']['period']['from_date'], '%d/%m/%Y').date()
            period_end = datetime.strptime(data['filters']['period']['to_date'], '%d/%m/%Y').date()

            payments = payments.filter(
                creation_date__gte=period_start,  # Subscription ends on or after period starts
                creation_date__lte=period_end  # Subscription starts on or before period ends
            )

        payments = payments.select_related('associate')

        # cols and rows
        phone_data = [_get_phone_columns(p.associate) for p in payments]
        columns = [
            'Nome',
            'Cognome',
            'Email',
            'Telefono',
            'Tel. Tutore',
            'Contatto',
            'quota (€)',
            'corso/carnet',
            'data',
            'ritardo pagamento (gg)'
        ]
        rows = [
            [p.associate.first_name for p in payments],
            [p.associate.last_name for p in payments],
            [p.associate.email or '-' for p in payments],
            [d[0] for d in phone_data],
            [d[1] for d in phone_data],
            [d[2] for d in phone_data],
            [str(p.amount).replace('.', ',') for p in payments],
            [p.get_course_carnet_name() for p in payments],
            [p.payment_date.strftime('%d/%m/%Y') if p.payment_date else p.creation_date.strftime('%d/%m/%Y') for p in payments],
            [(today - p.payment_date.date()).days if p.payment_date else (today - p.creation_date.date()).days for p in payments]
        ]

        if file_format == 'pdf':
            logger.debug(f"[not_paid_courses_subscriptions] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                f"Quote/Carnet corso non pagate",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"Quote corso/carnet non pagate (giorno stampa {today.strftime('%d/%m/%Y')})",
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[not_paid_courses_subscriptions] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                f"Quote/Carnet corso non pagate",
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[not_paid_courses_subscriptions] error: {str(e)}")
        raise Exception('Error in parsing data')


def current_view_subscriptions(sport_association=None, data=None, subscriptions=[], title="Iscrizioni filtrate"):
    try:
        if data['format'] not in ['pdf', 'excel']:
            raise Exception('Format not valid')

        file_format = data['format']  # 'pdf' or 'excel'

        today = datetime.date(datetime.today())

        subscriptions = subscriptions.select_related(
            'associate',  # For associate.get_full_name() and associate.email
            'user'  # For user.username
        ).prefetch_related(
            'tags'  # For tags.all()
        )

        # cols and rows
        columns = [
            'Tesserato',
            'Email',
            'Stato',
            'Anno fiscale',
            'Certificato',
            'Data creazione',
            'Utente',
            'Tag'
        ]
        # subscription.tags is a ManyToManyField and we want to print the tags.tag_name
        rows = [
            [s.associate.get_full_name() for s in subscriptions],
            [s.associate.email for s in subscriptions],
            [s.get_status_lang() for s in subscriptions],
            [s.current_year() for s in subscriptions],
            [s.creation_date.strftime('%d/%m/%Y') for s in subscriptions],
            ['presente' if s.medical else '-' for s in subscriptions],
            [s.user.username for s in subscriptions],
            [', '.join([tag.tag_name for tag in s.tags.all()]) if s.tags else '-' for s in subscriptions],
        ]

        if file_format == 'pdf':
            logger.debug(f"[current_view_subscriptions] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                "Iscrizioni filtrate",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"{title} (giorno stampa {today.strftime('%d/%m/%Y')})",
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[current_view_subscriptions] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                title,
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[current_view_subscriptions] error: {str(e)}")
        raise Exception('Error in parsing data')


def current_view_course(sport_association=None, data=None, courses=[], title="Corsi filtrati"):
    try:
        if data['format'] not in ['pdf', 'excel']:
            raise Exception('Format not valid')

        file_format = data['format']  # 'pdf' or 'excel'

        today = datetime.date(datetime.today())

        courses = courses.prefetch_related(
            'tags'  # For tags.all()
        )

        athlete_courses = []
        for c in courses:
            athlete_courses.append(CourseSubscription.objects.filter(course_id=c.course_id).count())

        tags = CourseTags.objects.filter(
            sport_association=sport_association
        )

        # cols and rows
        columns = [
            'Nome',
            'Tipologia',
            'Visibilità',
            'Data inizio',
            'Data fine',
            'Data creazione',
            'In evidenza',
            'Strutture',
            'Iscritti',
            'Tag'
        ]
        # subscription.tags is a ManyToManyField and we want to print the tags.tag_name
        rows = [
            [c.title for c in courses],
            [c.get_course_type_label() for c in courses],
            [c.get_status_label() for c in courses],
            [c.start_date.strftime('%d/%m/%Y') if c.start_date else '-' for c in courses],
            [c.end_date.strftime('%d/%m/%Y') if c.end_date else '-' for c in courses],
            [c.creation_date.strftime('%d/%m/%Y') for c in courses],
            ["In evidenza" if c.pinned is True else '-' for c in courses],
            [', '.join([str(l.title) for l in c.locations.all()]) if c.locations else '-' for c in courses],
            [int(athlete_courses[i]) for i in range(len(athlete_courses))],
            [', '.join([tag.tag_name for tag in c.tags.all()]) if c.tags else '-' for c in courses],
        ]

        if file_format == 'pdf':
            logger.debug(f"[current_view_course] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                "Corsi filtrati",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"{title} (giorno stampa {today.strftime('%d/%m/%Y')})",
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[current_view_course] excel")
            columns += [tag.tag_name for tag in tags]
            rows += [['assegnato' if tag in c.tags.all() else '' for c in courses] for tag in tags]
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                title,
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[current_view_course] error: {str(e)}")
        raise Exception('Error in parsing data')



def current_view_course_subscription(sport_association=None, data=None, coursesubscriptions=[], title="Iscrizioni corsi filtrate"):
    try:
        if data['format'] not in ['pdf', 'excel']:
            raise Exception('Format not valid')

        file_format = data['format']  # 'pdf' or 'excel'

        today = datetime.date(datetime.today())

        coursesubscriptions = coursesubscriptions.select_related(
            'subscription',
            'subscription__associate',
            'course'  # For course.title
        )

        tags = Tags.objects.filter(
            sport_association=sport_association
        )

        # Build carnet lookup for efficiency (course_subscription_id -> list of carnet info)
        coursesubscription_ids = [s.course_subscription_id for s in coursesubscriptions]
        carnet_subs = CarnetSubscription.objects.filter(
            course_subscription__in=coursesubscription_ids,
            disabled=False
        ).select_related('carnet_id').prefetch_related('course_subscription')

        # Build mapping: course_subscription_id -> carnet display strings
        carnet_lookup = {}
        for cs in carnet_subs:
            for course_sub in cs.course_subscription.all():
                if course_sub.course_subscription_id in coursesubscription_ids:
                    if course_sub.course_subscription_id not in carnet_lookup:
                        carnet_lookup[course_sub.course_subscription_id] = []
                    if cs.carnet_id:
                        # Build display string: "Title (date) - remaining/total"
                        title = cs.carnet_id.title or 'Carnet'
                        date_str = cs.creation_date.strftime('%d/%m/%Y') if cs.creation_date else ''
                        lessons_total = cs.carnet_id.lessons_number or 0
                        lessons_left = cs.meta.get('lessons_left', lessons_total) if cs.meta else lessons_total
                        lessons_str = f"{lessons_left}/{lessons_total}" if lessons_total else ''

                        # Format: "Title (01/01/2024) - 8/10" (remaining/total)
                        display = title
                        if date_str:
                            display += f" ({date_str})"
                        if lessons_str:
                            display += f" - {lessons_str}"

                        carnet_lookup[course_sub.course_subscription_id].append(display)

        # Check if any carnets exist to decide whether to show the column
        has_carnets = len(carnet_lookup) > 0

        # cols and rows
        columns = [
            'Cognome',
            'Nome',
            'Nato il',
            'Email',
            'Telefono',
            'Corso',
            'Data creazione',
            'Tags',
        ]

        # Add Carnet column only if there are carnets
        if has_carnets:
            columns.append('Carnet')

        # subscription.tags is a ManyToManyField and we want to print the tags.tag_name
        rows = [
            [s.subscription.associate.last_name for s in coursesubscriptions],
            [s.subscription.associate.first_name for s in coursesubscriptions],
            [s.subscription.associate.born_date.strftime('%d/%m/%Y') for s in coursesubscriptions],
            [s.subscription.associate.email for s in coursesubscriptions],
            [s.subscription.associate.phone for s in coursesubscriptions],
            [s.course.title for s in coursesubscriptions],
            [s.creation_date.strftime('%d/%m/%Y') for s in coursesubscriptions],
            [', '.join([tag.tag_name for tag in s.subscription.tags.all()]) if s.subscription.tags else '-' for s in coursesubscriptions],
        ]

        # Add Carnet row only if there are carnets
        if has_carnets:
            rows.append([
                ', '.join(carnet_lookup.get(s.course_subscription_id, [])) or '-'
                for s in coursesubscriptions
            ])

        if file_format == 'pdf':
            logger.debug(f"[current_view_coursesubscription] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                "Iscrizioni corsi filtrate",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"{title} (giorno stampa {today.strftime('%d/%m/%Y')})",
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[current_view_coursesubscription] excel")
            columns += [tag.tag_name for tag in tags]
            rows += [['assegnato' if tag in s.subscription.tags.all() else '' for s in coursesubscriptions] for tag in tags]
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                title,
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename

        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[current_view_coursesubscription] error: {str(e)}")
        raise Exception('Error in parsing data')


def expired_payments(sport_association=None, data=None):
    try:

        file_format = data['format'] if 'format' in data else 'pdf'
        subscriptions = filter_subscriptions(sport_association, data)

        payments = Payment.objects.filter(
            paid=False,
            archived=False,
            associate__isnull=False,
            sport_association=sport_association,
            expense=False,
            amount__gt=0,
            associate__in=[s.associate for s in subscriptions],
            creation_date__lte = datetime.today()
        )

        if 'period' in data['filters']:
            period_start = datetime.strptime(data['filters']['period']['from_date'], '%d/%m/%Y').date()
            period_end = datetime.strptime(data['filters']['period']['to_date'], '%d/%m/%Y').date()

            payments = payments.filter(
                creation_date__gte=period_start,  # Subscription ends on or after period starts
                creation_date__lte=period_end  # Subscription starts on or before period ends
            )

            payments = payments.select_related('associate')


        payments.order_by('-creation_date', '-payment_date')
        today = datetime.date(datetime.today())

        payments = sorted(payments, key=lambda x: (today - x.payment_date.date()).days if x.payment_date else (today - x.creation_date.date()).days)


        # cols and rows
        phone_data = [_get_phone_columns(p.associate) for p in payments]
        columns = [
            'Nome',
            'Cognome',
            'Email',
            'Telefono',
            'Tel. Tutore',
            'Contatto',
            'quota (€)',
            'attività',
            'data',
            'ritardo pagamento (gg)'
        ]
        rows = [
            [p.associate.first_name for p in payments],
            [p.associate.last_name for p in payments],
            [p.associate.email or '-' for p in payments],
            [d[0] for d in phone_data],
            [d[1] for d in phone_data],
            [d[2] for d in phone_data],
            [str(p.amount).replace('.', ',') for p in payments],
            [p.get_course_carnet_name() for p in payments],
            [p.payment_date.strftime('%d/%m/%Y') if p.payment_date else p.creation_date.strftime('%d/%m/%Y') for p in payments],
            [(today - p.payment_date.date()).days if p.payment_date else (today - p.creation_date.date()).days for p in payments]
        ]

        if file_format == 'pdf':
            logger.debug(f"[expired_payments] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                f"Pagamenti scaduti",
                sport_association.denomination
            )
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"Pagamenti scaduti (giorno stampa {today.strftime('%d/%m/%Y')})",
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[expired_payments] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                f"Pagamenti scaduti",
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename
        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.debug(f"[expired_payments] error: {str(e)}")
        raise Exception('Error in parsing data')

def subscriptions_with_all_payments(sport_association=None, data=None):
    """
    Export subscriptions with all payments associated to the subscription.associate
    Shows one row per payment, grouped by subscription
    """
    try:
        file_format = data['format'] if 'format' in data else 'pdf'
        subscriptions = filter_subscriptions(sport_association, data)

        today = datetime.date(datetime.today())

        # Build a list of (subscription, payment) tuples
        subscription_payment_pairs = []

        for subscription in subscriptions:
            # Get all payments for this subscription's associate
            payments = Payment.objects.filter(
                associate=subscription.associate,
                sport_association=sport_association,
                archived=False,
                expense=False
            ).select_related('invoice').order_by('-creation_date')

            # Apply period filter if provided
            if 'period' in data.get('filters', {}):
                period_start = datetime.strptime(data['filters']['period']['from_date'], '%d/%m/%Y').date()
                period_end = datetime.strptime(data['filters']['period']['to_date'], '%d/%m/%Y').date()

                payments = payments.filter(
                    creation_date__gte=period_start,
                    creation_date__lte=period_end
                )

            # Create a pair for each payment
            for payment in payments:
                subscription_payment_pairs.append((subscription, payment))

        # If no payments found, show subscriptions without payments
        if not subscription_payment_pairs:
            subscription_payment_pairs = [(s, None) for s in subscriptions]

        # Define columns
        phone_data = [_get_phone_columns(pair[0].associate) for pair in subscription_payment_pairs]
        columns = [
            'Cognome',
            'Nome',
            'Data nascita',
            'Email',
            'Telefono',
            'Tel. Tutore',
            'Contatto',
            'Tipo iscrizione',
            'Oggetto pagamento',
            'Descrizione',
            'Importo (€)',
            'Stato',
            'Data creazione',
            'Data pagamento',
            'N. Ricevuta',
        ]

        # Build rows
        rows = [
            # Cognome
            [pair[0].associate.last_name for pair in subscription_payment_pairs],
            # Nome
            [pair[0].associate.first_name for pair in subscription_payment_pairs],
            # Data nascita
            [pair[0].associate.born_date.strftime('%d/%m/%Y') if pair[0].associate.born_date else '-' for pair in subscription_payment_pairs],
            # Email
            [pair[0].associate.email or '-' for pair in subscription_payment_pairs],
            # Telefono
            [d[0] for d in phone_data],
            # Tel. Tutore
            [d[1] for d in phone_data],
            # Contatto
            [d[2] for d in phone_data],
            # Tipo iscrizione
            [pair[0].get_type_display() for pair in subscription_payment_pairs],
            # Oggetto pagamento
            [pair[1].get_subject_display() if pair[1] else '-' for pair in subscription_payment_pairs],
            # Descrizione
            [pair[1].description if pair[1] and pair[1].description else '-' for pair in subscription_payment_pairs],
            # Importo
            [f"{pair[1].amount:.2f}" if pair[1] else '-' for pair in subscription_payment_pairs],
            # Stato
            ['Pagato' if pair[1] and pair[1].paid else 'Non pagato' if pair[1] else '-' for pair in subscription_payment_pairs],
            # Data creazione
            [pair[1].creation_date.strftime('%d/%m/%Y') if pair[1] else '-' for pair in subscription_payment_pairs],
            # Data pagamento
            [pair[1].payment_date.strftime('%d/%m/%Y') if pair[1] and pair[1].payment_date else '-' for pair in subscription_payment_pairs],
            # N. Ricevuta
            [str(pair[1].invoice.number) if pair[1] and pair[1].invoice else '-' for pair in subscription_payment_pairs],
        ]

        if file_format == 'pdf':
            logger.debug(f"[subscriptions_with_all_payments] pdf")
            filename = "[{}] {} - {}.pdf".format(
                today.strftime('%d/%m/%Y'),
                "Iscrizioni con tutti i pagamenti",
                sport_association.denomination
            )

            # Transpose rows for PDF
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"Iscrizioni con tutti i pagamenti (stampa {today.strftime('%d/%m/%Y')})",
            )

            return file, filename

        elif file_format == 'excel':
            logger.debug(f"[subscriptions_with_all_payments] excel")
            filename = "[{}] {} - {}.xlsx".format(
                today.strftime('%d/%m/%Y'),
                "Iscrizioni con tutti i pagamenti",
                sport_association.denomination
            )
            file = generate_xlsx_from_data(rows, columns)

            return file, filename

        else:
            raise Exception('Format not valid')

    except Exception as e:
        logger.error(f"[subscriptions_with_all_payments] error: {str(e)}")
        raise Exception('Error in parsing data')

def export_balance_sheet(balance_sheet, file_format):
    try:
        today = datetime.date(datetime.today())

        # Prepare the data structure for export
        columns = [
            'Descrizione',
            'Istituzionale (€)',
            'Commerciale (€)',
            'Totale (€)'
        ]

        rows = []
        descriptions = []
        institutional = []
        commercial = []
        totals = []

        # Add incoming section
        descriptions.append('A) ENTRATE')
        institutional.append('')
        commercial.append('')
        totals.append('')

        # Add institutional income
        text = 'Entrate Generali'
        descriptions.append(text)
        institutional.append('')
        commercial.append('')
        totals.append('')

        institutional_total = 0
        commercial_total = 0

        key = 'generalIncome'
        for income in balance_sheet.data['incoming'][key]:
            inst_amount = float(income['institutional'])
            comm_amount = float(income['commercial'])
            total = inst_amount + comm_amount

            descriptions.append(income['description'])
            institutional.append(f"{inst_amount:.2f}".replace(".", ","))
            commercial.append(f"{comm_amount:.2f}".replace(".", ","))
            totals.append(f"{total:.2f}".replace(".", ","))

            institutional_total += inst_amount
            commercial_total += comm_amount

        descriptions.append('Totale entrate')
        institutional.append(f"{institutional_total:.2f}".replace(".", ","))
        commercial.append(f"{commercial_total:.2f}".replace(".", ","))
        totals.append(f"{(institutional_total + commercial_total):.2f}".replace(".", ","))

        # Add empty row as separator
        descriptions.append('')
        institutional.append('')
        commercial.append('')
        totals.append('')

        # Add outgoing section
        descriptions.append('B) USCITE')
        institutional.append('')
        commercial.append('')
        totals.append('')

        reimb_inst_total = 0
        reimb_comm_total = 0

        # Add general expenses
        descriptions.append('Spese generali')
        institutional.append('')
        commercial.append('')
        totals.append('')

        exp_inst_total = 0
        exp_comm_total = 0


        for expense in balance_sheet.data['outgoing']['generalExpenses']:
            inst_amount = float(expense['institutional'])
            comm_amount = float(expense['commercial'])
            total = inst_amount + comm_amount

            descriptions.append(expense['description'])
            institutional.append(f"{inst_amount:.2f}".replace(".", ","))
            commercial.append(f"{comm_amount:.2f}".replace(".", ","))
            totals.append(f"{total:.2f}".replace(".", ","))

            exp_inst_total += inst_amount
            exp_comm_total += comm_amount

        total_outgoing_inst = reimb_inst_total + exp_inst_total
        total_outgoing_comm = reimb_comm_total + exp_comm_total

        descriptions.append('Totale uscite')
        institutional.append(f"{total_outgoing_inst:.2f}".replace(".", ","))
        commercial.append(f"{total_outgoing_comm:.2f}".replace(".", ","))
        totals.append(f"{(total_outgoing_inst + total_outgoing_comm):.2f}".replace(".", ","))

        # Add balance of total incoming & total expenses
        descriptions.append('')
        institutional.append('')
        commercial.append('')
        totals.append('')

        descriptions.append('')
        institutional.append('ENTRATE')
        commercial.append('USCITE')
        totals.append('BILANCIO FINALE')

        descriptions.append('C) RENDICONTO DELLA GESTIONE')
        institutional.append(f"{(institutional_total + commercial_total):.2f}".replace(".", ","))
        commercial.append(f"-{(total_outgoing_inst + total_outgoing_comm):.2f}".replace(".", ","))
        totals.append(f"{(institutional_total + commercial_total - total_outgoing_inst - total_outgoing_comm):.2f}".replace(".", ","))

        # Add final balance
        descriptions.append('')
        institutional.append('')
        commercial.append('')
        totals.append('')

        descriptions.append('D) LIQUIDITÀ')
        institutional.append('')
        commercial.append('')
        totals.append('')

        descriptions.append('Banca')
        institutional.append('')
        commercial.append('')
        totals.append(f"{balance_sheet.data['bank']:.2f}".replace(".", ","))

        descriptions.append('Cassa')
        institutional.append('')
        commercial.append('')
        totals.append(f"{balance_sheet.data['cash']:.2f}".replace(".", ","))

        descriptions.append('Altro')
        institutional.append('')
        commercial.append('')
        totals.append(f"{balance_sheet.data['other']:.2f}".replace(".", ","))

        descriptions.append('Totale')
        institutional.append('')
        commercial.append('')
        totals.append(f"{balance_sheet.data['total']:.2f}".replace(".", ","))

        # For PDF, we need the data in rows where each row is a list of values
        rows = [descriptions, institutional, commercial, totals]

        filename = "[{}] Bilancio {} - {}.{}".format(
            today.strftime('%d/%m/%Y'),
            balance_sheet.year,
            balance_sheet.sport_association.denomination,
            'pdf' if file_format == 'pdf' else 'xlsx'
        )

        custom_header = f"""
        <div style="text-align: center;display:flex;justify-content: center;width: 200px;margin:auto; margin-bottom: 20px;">
            <div style="text-align: center;">
                <img src="{balance_sheet.sport_association.logo}" style="height: 1000px;"/>
            </div>
            <div style="text-align: center;">
                <h1 style="font-size: 20px;">{balance_sheet.sport_association.denomination}</h1>
                <h2 style="font-size: 16px;">{balance_sheet.sport_association.address_city or ''}, {balance_sheet.sport_association.address_cap or ''}, {balance_sheet.sport_association.address or ''}</h2>
                <h3 style="font-size: 16px;">Approvato il ____/____/______</h3>
            </div>
        </div>
        """

        # convert date to day before
        balance_at_date = "{}/{}/{}".format(
            balance_sheet.sport_association.user.balance_sheet_start_day,
            balance_sheet.sport_association.user.balance_sheet_start_month,
            balance_sheet.year
        )
        # convert to actual date
        balance_at_date = datetime.strptime(balance_at_date, '%d/%m/%Y').date()
        # substract 1 day
        balance_at_date = balance_at_date.replace(year=balance_at_date.year + 1) - timedelta(days=1)

        if file_format == 'pdf':
            # Transpose the data for PDF generation
            transposed_rows = list(map(list, zip(*rows)))
            file = generate_pdf_from_data(
                transposed_rows,
                columns,
                title=f"Bilancio {balance_sheet.year} al {balance_at_date.strftime('%d/%m/%Y')}",
                align=['LEFT', 'RIGHT', 'RIGHT', 'RIGHT'],
                header=custom_header,
                isA3=True,
                landscape=False,
                additional_data={
                    'balance_at_date': f"I saldi sono riportati al {balance_at_date.strftime('%d/%m/%Y')}."
                }
            )
        elif file_format == 'excel':
            file = generate_xlsx_from_data(rows, columns)
        else:
            raise Exception('Format not valid')

        return {
            "file": file,
            "filename": filename
        }

    except Exception as e:
        logger.error(f"[export_balance_sheet] error: {str(e)}")
        raise Exception('Error in exporting balance sheet')


# Helper function for streaming base64 encoding
def base64_encode_file_to_string(input_file, chunk_size=8192):
    """Stream base64 encoding of a file to string"""
    chunks = []
    encoder = base64.encodebytes
    while True:
        chunk = input_file.read(chunk_size)
        if not chunk:
            break
        chunks.append(encoder(chunk).decode('utf-8'))
    return ''.join(chunks)
