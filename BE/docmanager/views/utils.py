import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission

from docmanager.models import Document

from typing import Any, Dict, List
from PyPDF2 import PageObject, PdfReader, PdfWriter
from PyPDF2.constants import FieldFlag, PageAttributes as PG, FieldDictionaryAttributes, AnnotationDictionaryAttributes
from PyPDF2.generic import NameObject, TextStringObject, NumberObject, ArrayObject, DictionaryObject

import base64
from io import BytesIO

OPTIONAL_READ_WRITE_FIELD = FieldFlag(0)

def get_bytes_file(writer):
    try:
        with BytesIO() as output_file:
            writer.write(output_file)
            output_file.seek(0)
            return output_file.getvalue()
    except Exception as e:
        # Handle or log the error appropriately
        raise ValueError(f"Failed to encode file: {str(e)}")


class CustomPDFWriter(PdfWriter):

    def update_page_form_field_values_clean(
            self,
            page: PageObject,
            fields: Dict[str, Any],
            flags: FieldFlag = OPTIONAL_READ_WRITE_FIELD,
    ) -> None:
        self.set_need_appearances_writer()
        # Iterate through pages, update field values
        if PG.ANNOTS not in page:
            return
        for j in range(len(page[PG.ANNOTS])):  # type: ignore
            writer_annot = page[PG.ANNOTS][j].get_object()  # type: ignore
            # retrieve parent field values, if present
            writer_parent_annot = {}  # fallback if it's not there
            if PG.PARENT in writer_annot:
                writer_parent_annot = writer_annot[PG.PARENT]
            for field in fields:
                if 'signature' in field:
                    continue
                if writer_annot.get(FieldDictionaryAttributes.T) == field:
                    # remove all the style and bound borders
                    # example of what is in the field:
                    writer_annot.update(
                        {
                            NameObject('/Border'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]),
                            # /DA
                            NameObject('/DA'): TextStringObject("/Helvetica 10 Tf 0 g"),

                        }
                    )
                    # Remove or clear border style
                    if '/BS' in writer_annot:
                        del writer_annot['/BS']

                    # Clear appearance stream
                    if '/AP' in writer_annot:
                        writer_annot[NameObject('/AP')] = DictionaryObject()

                    # Clear mark dictionary
                    if '/MK' in writer_annot:
                        writer_annot[NameObject('/MK')] = DictionaryObject()

                    if writer_annot.get(FieldDictionaryAttributes.FT) == "/Btn":
                        writer_annot.update(
                            {
                                NameObject(
                                    AnnotationDictionaryAttributes.AS
                                ): NameObject(fields[field])
                            }
                        )
                    writer_annot.update(
                        {
                            NameObject(FieldDictionaryAttributes.V): TextStringObject(
                                fields[field]
                            )
                        }
                    )
                    if flags:
                        writer_annot.update(
                            {
                                NameObject(FieldDictionaryAttributes.Ff): NumberObject(
                                    flags
                                )
                            }
                        )
                elif writer_parent_annot.get(FieldDictionaryAttributes.T) == field:
                    # remove border
                    writer_parent_annot.update(
                        {
                            NameObject('/Border'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]),
                            NameObject('/DA'): TextStringObject("/Helvetica 10 Tf 0 g"),
                            NameObject('/AP'): DictionaryObject(),
                            NameObject('/MK'): DictionaryObject(),
                        }
                    )
                    writer_parent_annot.update(
                        {
                            NameObject(FieldDictionaryAttributes.V): TextStringObject(
                                fields[field]
                            )
                        }
                    )


def fill_pdf_fields(pdf_path, replace_dict: Dict, output_base64=True, output_path="output.pdf"):
    # Create reader and writer objects
    # TODO: add strict mode
    tmp_pdf_path = pdf_path
    try:
        import fitz
        import os
        from django.utils import timezone
        output_file = os.path.abspath(f"/tmp/{timezone.now().isoformat()}-file.pdf")

        # Insert signature
        # Get base64 image data
        image_data = replace_dict['sport_association.president_signature']
        if ';base64,' in image_data:
            image_data = image_data.split(';base64,')[1]
        decoded_image = base64.b64decode(image_data)

        # save img to /tmp/{timezone.now().isoformat()}-signature.png and assign to signature_file
        signature_file = os.path.abspath(f"/tmp/{timezone.now().isoformat()}-signature.png")

        # Save the decoded image to the file
        with open(signature_file, 'wb') as f:
            f.write(decoded_image)

        image_rectangle = fitz.Rect(400, 20, 520, 1300)

        # retrieve the first page of the PDF
        file_handle = fitz.open(pdf_path)
        first_page = file_handle[0]

        # add the image
        first_page.insert_image(image_rectangle, filename=signature_file)


        try:
            # Insert stamp
            stamp_image_data = replace_dict['sport_association.stamp']
            if ';base64,' in stamp_image_data:
                stamp_image_data = stamp_image_data.split(';base64,')[1]
            decoded_stamp_image = base64.b64decode(stamp_image_data)
            stamp_file = os.path.abspath(f"/tmp/{timezone.now().isoformat()}-stamp.png")
            with open(stamp_file, 'wb') as f:
                f.write(decoded_stamp_image)
            stamp_rectangle = fitz.Rect(100, 20, 260, 1300)
            # add the stamp image
            first_page.insert_image(stamp_rectangle, filename=stamp_file)
        except KeyError:
            print("No stamp image found in replace_dict.")
        except Exception as e:
            print(f"Failed to insert stamp: {str(e)}")


        file_handle.save(output_file)

        tmp_pdf_path = output_file

    except Exception as e:
        print(f"Failed to use fitz: {str(e)}")

    reader = PdfReader(tmp_pdf_path)
    writer = CustomPDFWriter()

    # Add all pages to writer
    for page in reader.pages:
        writer.add_page(page)

    # Get the form fields
    fields = reader.get_fields()  # Changed from get_form_text_fields()

    # Make sure the PDF is updateable
    writer.set_need_appearances_writer()  # This is important!

    # Fill each field
    field_dict = {}
    for field_name in fields:
        # Replace '-' with '.' in field names, if present
        field_dict[field_name] = replace_dict.get(str(field_name).replace('-', '.'), "")

    # Update all fields at once
    for page in writer.pages:
        writer.update_page_form_field_values_clean(
            page, field_dict
        )
    #     ,
    #             FieldFlag.READ_ONLY
    #         )

    if not output_base64:
        # Save the pdf to output file
        with open(output_path, "wb") as output_file:
            writer.write(output_file)
        print(f"\nPDF saved to {output_path}")
        return output_path

    # Return bytes

    return get_bytes_file(writer)


def get_pdf_fields(pdf_path) -> List[Dict[str, str]]:
    # Create a PDF reader object
    reader = PdfReader(pdf_path)

    # Get form fields from the PDF
    fields = reader.get_fields()  # Changed from get_form_text_fields()

    all_fields = []

    if fields:
        for field_name, field_data in fields.items():
            value = field_data.get('/V', '')  # Get actual field value
            print(f"{field_name}: {value}")
            all_fields.append({field_name: value})
    else:
        print("No fillable fields found in the PDF.")

    return all_fields


class HasDocumentToken(BasePermission):
    def has_permission(self, request, view):
        # check if token exists
        token = request.query_params.get('token', None)
        if token is None:
            return False
        # check if token is valid
        try:
            Document.objects.get(token=token)
            return True
        except Document.DoesNotExist:
            return False


class HasBypassAuthorizationHeader(BasePermission): # pragma: no cover
    def has_permission(self, request, view):
        # check if bypass header exists
        token = request.headers.get('Authorization', None)
        return token is not None and hmac.compare_digest(token, settings.DOCUMENT_BYPASS_TOKEN)
