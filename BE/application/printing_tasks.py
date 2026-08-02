from celery import shared_task
import requests

from core.settings import CURRENT_HOST


@shared_task(name="print_document_invoice")
def print_document_invoice(invoice_id, auth_token, send_receipt_email=False):
    try:
        print(f"[PRINTING TASK -> print_document_invoice] starting... {invoice_id}, send_receipt_email={send_receipt_email}")
        headers = {'Authorization': auth_token}
        response = requests.get(f"{CURRENT_HOST}/document/invoice/{invoice_id}?send_receipt_email={send_receipt_email}", headers=headers)
        # Depending on the response, you can take further actions or handle errors
        print(f"[PRINTING TASK -> print_document_invoice] finished!")
    except Exception as e:
        print(f"[PRINTING TASK -> print_document_invoice] error {e}")

@shared_task(name="print_document_customer_invoice")
def print_document_customer_invoice(customer_invoice_id, auth_token):
    try:
        print(f"[PRINTING TASK -> print_document_customer_invoice] starting... {customer_invoice_id}")
        headers = {'Authorization': auth_token}
        response = requests.get(f"{CURRENT_HOST}/document/einvoice/{customer_invoice_id}", headers=headers)
        # Depending on the response, you can take further actions or handle errors
        print(f"[PRINTING TASK -> print_document_customer_invoice] finished!")
    except Exception as e:
        print(f"[PRINTING TASK -> print_document_customer_invoice] error {e}")


@shared_task(name="print_document_compensation")
def print_document_compensation(payment_id, auth_token):
    try:
        print(f"[PRINTING TASK] starting... {payment_id}")
        headers = {'Authorization': auth_token}
        response = requests.get(f"{CURRENT_HOST}/document/compensation/{payment_id}", headers=headers)
        # Depending on the response, you can take further actions or handle errors
        print(f"[PRINTING TASK] finished!")
    except Exception as e:
        print(f"[PRINTING TASK] error {e}")


@shared_task(name="print_document_medical_appointment")
def print_document_medical_appointment(medical_appointment_id, auth_token):
    try:
        print(f"[PRINTING TASK] starting... {medical_appointment_id}")
        headers = {'Authorization': auth_token}
        response = requests.get(f"{CURRENT_HOST}/document/medical-appointment/{medical_appointment_id}", headers=headers)
        # Depending on the response, you can take further actions or handle errors
        print(f"[PRINTING TASK] finished!")
    except Exception as e:
        print(f"[PRINTING TASK] error {e}")