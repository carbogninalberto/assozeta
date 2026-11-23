from celery import shared_task
import requests

from core.settings import CURRENT_HOST
from core.handoff import _fetch, _running, barrier


def print_request(url, headers):
    active = _running.get()
    if active is None:
        return requests.get(url, headers=headers)
    from celery import current_task

    task_id, attempt, _ = active
    effect_key = f'{task_id}:{current_task.request.retries}:print'
    outcome, = _fetch("SELECT public.assozeta_handoff_effect_intent(%s,%s,'print')", (attempt, effect_key))
    if outcome == 'confirmed':
        return None
    if outcome != 'recorded':
        raise RuntimeError('Print effect needs reconciliation')
    try:
        response = requests.get(url, headers=headers)
    except (requests.exceptions.InvalidHeader, requests.exceptions.MissingSchema, requests.exceptions.InvalidURL):
        _fetch("""DELETE FROM public.assozeta_handoff_effects
            WHERE effect_key=%s AND attempt_id=%s AND state='intent' RETURNING 1""", (effect_key, attempt))
        raise
    barrier('effect:accepted', task_id)
    _fetch('SELECT public.assozeta_handoff_effect_confirm(%s,NULL)', (effect_key,))
    return response


@shared_task(name="print_document_invoice")
def print_document_invoice(invoice_id, auth_token, send_receipt_email=False):
    try:
        print(f"[PRINTING TASK -> print_document_invoice] starting... {invoice_id}, send_receipt_email={send_receipt_email}")
        headers = {'Authorization': auth_token}
        print_request(f"{CURRENT_HOST}/document/invoice/{invoice_id}?send_receipt_email={send_receipt_email}", headers)
        # Depending on the response, you can take further actions or handle errors
        print(f"[PRINTING TASK -> print_document_invoice] finished!")
    except requests.RequestException as e:
        print(f"[PRINTING TASK -> print_document_invoice] error {e}")

@shared_task(name="print_document_customer_invoice")
def print_document_customer_invoice(customer_invoice_id, auth_token):
    try:
        print(f"[PRINTING TASK -> print_document_customer_invoice] starting... {customer_invoice_id}")
        headers = {'Authorization': auth_token}
        print_request(f"{CURRENT_HOST}/document/einvoice/{customer_invoice_id}", headers)
        # Depending on the response, you can take further actions or handle errors
        print(f"[PRINTING TASK -> print_document_customer_invoice] finished!")
    except requests.RequestException as e:
        print(f"[PRINTING TASK -> print_document_customer_invoice] error {e}")


@shared_task(name="print_document_compensation")
def print_document_compensation(payment_id, auth_token):
    try:
        print(f"[PRINTING TASK] starting... {payment_id}")
        headers = {'Authorization': auth_token}
        print_request(f"{CURRENT_HOST}/document/compensation/{payment_id}", headers)
        # Depending on the response, you can take further actions or handle errors
        print(f"[PRINTING TASK] finished!")
    except requests.RequestException as e:
        print(f"[PRINTING TASK] error {e}")


@shared_task(name="print_document_medical_appointment")
def print_document_medical_appointment(medical_appointment_id, auth_token):
    try:
        print(f"[PRINTING TASK] starting... {medical_appointment_id}")
        headers = {'Authorization': auth_token}
        print_request(f"{CURRENT_HOST}/document/medical-appointment/{medical_appointment_id}", headers)
        # Depending on the response, you can take further actions or handle errors
        print(f"[PRINTING TASK] finished!")
    except requests.RequestException as e:
        print(f"[PRINTING TASK] error {e}")
