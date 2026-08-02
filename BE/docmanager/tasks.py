import logging
import base64

from celery import shared_task
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


@shared_task(name='save_to_storage')
def save_to_storage(file_path, pdf_base64):
    logger.info("Starting save_to_storage task", extra={'task_name': 'save_to_storage', 'file_path': file_path})
    try:
        # Convert base64 back to binary
        content = base64.b64decode(pdf_base64)
        default_storage.save(file_path, ContentFile(content))
        logger.info("Completed save_to_storage task", extra={'task_name': 'save_to_storage', 'file_path': file_path})
    except Exception as e:
        logger.error("Error in save_to_storage task", extra={'task_name': 'save_to_storage', 'file_path': file_path}, exc_info=True)
        raise

@shared_task(name='save_binary_to_storage')
def save_binary_to_storage(file_path, binary_data):
    logger.info("Starting save_binary_to_storage task", extra={'task_name': 'save_binary_to_storage', 'file_path': file_path})
    try:
        default_storage.save(file_path, ContentFile(binary_data))
        logger.info("Completed save_binary_to_storage task", extra={'task_name': 'save_binary_to_storage', 'file_path': file_path})
    except Exception as e:
        logger.error("Error in save_binary_to_storage task", extra={'task_name': 'save_binary_to_storage', 'file_path': file_path}, exc_info=True)
        raise