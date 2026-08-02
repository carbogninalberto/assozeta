from django.urls import path

from docmanager.views.document_view import medical_certificate_document, retrieve_document, billing_invoice_document, \
    delete_document
from .views.printing_views import document_subscription, document_subscription_view, document_invoice, \
    document_invoice_view, document_subscription_preview, document_compensation, document_compensation_view, \
    document_medical_appointment, document_medical_appointment_view, document_template, document_template_view, \
    document_einvoice, document_einvoice_view

urlpatterns = [
    path(r'document/medical/certificate/', medical_certificate_document),
    path(r'document/billing-invoice/', billing_invoice_document),
    path(r'document/subscription/<str:uid>', document_subscription),
    path(r'document/subscription/preview/', document_subscription_preview),
    path(r'document/subscription/<str:uid>/view/', document_subscription_view),
    path(r'document/invoice/<str:uid>', document_invoice),
    path(r'document/invoice/<str:uid>/view/', document_invoice_view),
    path(r'document/einvoice/<str:uid>', document_einvoice),
    path(r'document/einvoice/<str:uid>/view/', document_einvoice_view),
    path(r'document/compensation/<str:uid>', document_compensation),
    path(r'document/compensation/<str:uid>/view/', document_compensation_view),
    path(r'document/template/<str:uid>', document_template),
    path(r'document/template/<str:uid>/view/', document_template_view),
    path(r'document/medical-appointment/<str:uid>', document_medical_appointment),
    path(r'document/medical-appointment/<str:uid>/view/', document_medical_appointment_view),
    path(r'document/retrieve/<str:uid>', retrieve_document),
    path(r"document/<str:uid>/delete", delete_document),
]
