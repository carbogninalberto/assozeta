import base64
import io
import logging
import random
import re
import uuid

import bcrypt
import requests
from PIL import ImageDraw, Image, ImageFont
from codicefiscale import codicefiscale
from django.contrib.auth.hashers import make_password
from rest_framework.exceptions import ValidationError
from datetime import datetime, date
from django.utils import timezone
from rest_framework.pagination import PageNumberPagination
import secrets
import string


from core.settings import CRM_API_TOKEN
from django.db.models import Q
from functools import reduce
import operator
from django.db.models.fields import CharField, TextField, IntegerField, FloatField, DecimalField
from django.db.models.fields.related import ForeignKey, ManyToManyField, OneToOneField


logger = logging.getLogger(__name__)

class SubscriptionMaps:
    MAP = {
        "nome": None,
        "cognome": None,
        "sesso": None,
        "codice fiscale": None,
        "data di nascita": None,
        "città di nascita": None,
        "indirizzo": None,
        "città di residenza": None,
        "cap": None,
        "email": None,
        "telefono": None,
        "nome tutore": None,
        "cognome tutore": None,
        "sesso tutore": None,
        "codice fiscale tutore": None,
        "data di nascita tutore": None,
        "città di nascita tutore": None,
        "città di residenza tutore": None,
        "indirizzo tutore": None,
        "cap tutore": None,
        "email tutore": None,
        "telefono tutore": None,
        "scadenza certificato medico": None,
        "data tesseramento": None,
        "data scadenza tesseramento": None,
        "numero tessera": None,
    }
    KEY_TO_FIELD_ASSOCIATE = {
        "nome": "first_name",
        "cognome": "last_name",
        "sesso": "sex",
        "codice fiscale": "tax_code",
        "data di nascita": "born_date",
        "città di nascita": "born_city",
        "indirizzo": "address",
        "cap": "address_cap",
        "email": "email",
        "città di residenza": "address_city",
        "telefono": "phone",
        "scadenza certificato medico": "certificate_expiring_date",
        "numero tessera": "subscription_number",
        "tipo tessera": "subscription_type",
        "data iscrizione": "start_date",
        "data scadenza iscrizione": "end_date",
        "data tesseramento": "membership_start_date",
        "data scadenza tesseramento": "membership_end_date",
    }

    MANDATORY_FIELDS = {
        "first_name": True,
        "last_name": True,
        "sex": True,
        "tax_code": True,
        "born_date": True,
        "born_city": True,
        "address": True,
        "address_cap": True,
        "address_city": True,
        "email": False,
        "phone": False,
    }

    STATUS_DICT = {
        1: "non firmata",
        2: "in attesa",
        3: "rifiutata",
        4: "accettata",
        5: "archiviata"
    }

    @classmethod
    def get_mandatory_fields(cls):
        return [key for key in filter(lambda x: cls.MANDATORY_FIELDS[x], cls.MANDATORY_FIELDS.keys())]


class BalanceSheetData:
    SOLAR_YEAR = 1
    SPORT_YEAR_SEP_AUG = 2
    SPORT_YEAR_JUN_MAY = 3

    @staticmethod
    def get_range_from_year_and_social_period(date, social_period):
        if social_period == BalanceSheetData.SPORT_YEAR_JUN_MAY:
            if date.month < 6:
                return timezone.localtime(timezone.make_aware(datetime(date.year - 1, 6, 1))), \
                       timezone.localtime(timezone.make_aware(datetime(date.year, 5, 31)))
            else:
                return timezone.localtime(timezone.make_aware(datetime(date.year, 6, 1))), \
                       timezone.localtime(timezone.make_aware(datetime(date.year + 1, 5, 31)))
        elif social_period == BalanceSheetData.SPORT_YEAR_SEP_AUG:
            if date.month < 9:
                return timezone.localtime(timezone.make_aware(datetime(date.year - 1, 9, 1))), \
                       timezone.localtime(timezone.make_aware(datetime(date.year, 8, 31)))
            else:
                return timezone.localtime(timezone.make_aware(datetime(date.year, 9, 1))), \
                       timezone.localtime(timezone.make_aware(datetime(date.year + 1, 8, 31)))
        elif social_period == BalanceSheetData.SOLAR_YEAR:
            return timezone.localtime(timezone.make_aware(datetime(date.year, 1, 1))), \
                   timezone.localtime(timezone.make_aware(datetime(date.year, 12, 31)))
        else:
            raise ValidationError("Invalid social period")

    @staticmethod
    def get_range_from_year_and_starting_date(date, starting_day, starting_month, user=None):
        '''
        Get the range of the balance year from the starting date and the starting month,
        considering the date which is today. The starting day is the initial day of the
        balance year, and the starting month is the initial month of the balance year.
        The last day of the balance year is the day before the starting day of the next
        balance year.
        '''
        # Input validation
        if not 1 <= starting_month <= 12:
            raise ValueError("Month must be between 1 and 12")
        if not 1 <= starting_day <= 31:
            raise ValueError("Day must be between 1 and 31")

        # Validate the date combination
        try:
            datetime(date.year, starting_month, starting_day)
        except ValueError as e:
            raise ValueError(f"Invalid date combination: {e}")

        # Calculate the start_date for the current year
        current_year_start = datetime(date.year, starting_month, starting_day)
        current_year_start = timezone.make_aware(current_year_start)

        # Calculate the start_date for the previous year
        previous_year_start = datetime(date.year - 1, starting_month, starting_day)
        previous_year_start = timezone.make_aware(previous_year_start)

        # check if is type date or datetime
        if isinstance(date, datetime):
            # make date timezone-aware if it is not
            if not timezone.is_aware(date):
                date = timezone.make_aware(date)
        else:
            date = timezone.make_aware(datetime(date.year, date.month, date.day))

        # Determine which fiscal year the date falls into
        start_date = current_year_start if date >= current_year_start else previous_year_start

        if user and user.custom_end_date and \
                user.subscription_end_month and \
                user.subscription_end_day:
            # Calculate the end_date based on the user's custom end date
            end_date = timezone.make_aware(
                datetime(start_date.year, user.subscription_end_month, user.subscription_end_day))
            if end_date < start_date:
                end_date = timezone.make_aware(
                    datetime(start_date.year + 1, user.subscription_end_month, user.subscription_end_day))
        else:
            # Calculate the end_date as one day before the next year's start date
            end_date = datetime(start_date.year + 1, starting_month, starting_day) - timezone.timedelta(days=1)
            end_date = timezone.make_aware(end_date)

        return timezone.localtime(start_date), timezone.localtime(end_date)

    @staticmethod
    def get_range_from_year_and_social_period_in_months(social_period):
        if social_period == BalanceSheetData.SPORT_YEAR_JUN_MAY:
            return ['Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic', 'Gen', 'Feb', 'Mar', 'Apr', 'Mag'], \
                   [6, 7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5]
        elif social_period == BalanceSheetData.SPORT_YEAR_SEP_AUG:
            return ['Set', 'Ott', 'Nov', 'Dic', 'Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago'], \
                   [9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8]
        elif social_period == BalanceSheetData.SOLAR_YEAR:
            return ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic'], \
                   [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        else:
            raise ValidationError("Invalid social period")

    @staticmethod
    def get_range_from_year_and_starting_date_in_months(starting_month):
        months = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']
        return months[starting_month - 1:] + months[:starting_month - 1], \
                  [starting_month + i if starting_month + i <= 12 else starting_month + i - 12 for i in range(12)]

    @staticmethod
    def get_balance_sheet_default_data():
        return {
            "incoming": {
                "generalIncome": [],
            },
            "outgoing": {
                "generalExpenses": [],
            },
            "cash": 0,
            "bank": 0,
            "other": 0,
            "draft": True,
            "version": 1,
        }

    @staticmethod
    def find_category_index(category_list, category_id):
        """Find the index of a category in a list by its ID."""
        for i, item in enumerate(category_list):
            if item['id'] == str(category_id):
                return i
        return -1



class ApiMessages:  # pragma: no cover
    PASSWORD_NOT_VALID = "EC000"


def is_valid_uuid(uid):
    # check UUID
    try:
        uuid.UUID(uid)
        return True
    except Exception as e:
        raise ValidationError(e)


def days_between(d1, d2, absolute=False):
    # d1 = datetime.strptime(str(d1), "%Y-%m-%d")
    # d2 = datetime.strptime(str(d2), "%Y-%m-%d")
    if absolute:
        return abs((d2 - d1).days)
    else:
        return (d2 - d1).days


def check_phone_number(param):
    # regex that validates phone number allowing spaces and dashes between digits
    regex = re.compile(r'^\+?\d{7,15}$')
    try:
        if not regex.match(param):
            raise ValidationError("Phone number is not valid.")
    except Exception as e:
        raise ValidationError(f"Cannot Phone number: {e}")
    return param


def check_email(param):
    from django.core.validators import validate_email as django_validate_email
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        django_validate_email(param)
    except DjangoValidationError:
        raise ValidationError("Email is not valid.")
    return param


def check_date(param):
    # regex that validates date
    regex = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    try:
        if not regex.match(param):
            raise ValidationError("Date is not valid.")
    except Exception as e:
        logger.error(f"Could not validate date: {e}")
        param = None
    return param


def check_tax_code(param):
    regex = re.compile(r'^[a-zA-Z]{6}[0-9]{2}[abcdehlmprstABCDEHLMPRST]{1}[0-9]{2}([a-zA-Z]{1}[0-9]{3})[a-zA-Z]{1}$')
    try:
        if not regex.match(param):
            raise ValidationError("Tax code is not valid.")
    except Exception as e:
        raise ValidationError(f"Cannot check tax code: {e}")
    return param


def generate_readable_unique_string(length=8):
    # Define a custom string that excludes potentially confusing characters
    alphabet = string.ascii_letters + string.digits
    alphabet = alphabet.replace('l', '').replace('L', '').replace('I', '').replace('i', '').replace('1', '').replace('0', '').replace('O', '').replace('o', '')

    # Generate a unique string
    return ''.join(secrets.choice(alphabet) for _ in range(length)).upper()


class KTDatatablePagination(PageNumberPagination):
    page_size = 10
    page_query_param = 'pagination[page]'
    page_size_query_param = 'pagination[perpage]'


class ColorPalette:
    colors = [
        'fc-event-primary fc-event-light-primary',
        'fc-event-secondary fc-event-light-secondary',
        'fc-event-warning fc-event-light-warning',
        'fc-event-danger fc-event-light-danger',
        'fc-event-info fc-event-light-info',
        'fc-event-success fc-event-light-success',
        'fc-event-dark fc-event-light-dark',
    ]


def generate_image_with_text(text):

    image_size = (600, 100)
    background_color = "white"
    font_color = "black"

    # Create a new image with white background
    img = Image.new("RGB", image_size, background_color)

    # Initialize the drawing context with the image as background
    draw = ImageDraw.Draw(img)

    # Load a font that supports Unicode
    try:
        # Try to use a Unicode-compatible font if available
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except OSError:
        # Fallback to default if DejaVuSans is not available
        font = ImageFont.load_default()

    # Process multiline text
    text_width = 0
    text_height = 5
    for line in text.split("\n"):
        # Use getbbox() instead of textlength for better Unicode support
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        text_width = w if w > text_width else text_width

        # Calculate the starting position
        position = (5, text_height)

        # Add text to image
        draw.text(position, line, fill=font_color, font=font)
        text_height += 15  # Increased line spacing for better readability

    # Resize the image to fit the text plus margins
    img = img.crop((0, 0, text_width + 30, text_height + 10))

    return img


def compress_base64(data_uri, max_size=(200, 200), max_disk_space=1024*1024):
    try:
        # Decode the Base64 string to bytes
        header, data_b64 = data_uri.split(',', 1)
        data = base64.b64decode(data_b64)
        img = Image.open(io.BytesIO(data))

        # Calculate the new size maintaining the aspect ratio
        ratio = min(max_size[0] / img.size[0], max_size[1] / img.size[1])
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        logger.info(f"New size: {new_size} - Original size: {img.size} - Max disk space: {max_disk_space} - Data size: {len(data)}")
        # if new sizes are the same as the original ones, return the original data_uri
        if new_size == img.size and len(data) <= max_disk_space:
            return data_uri

        # print(f"Original size: {len(data)}")

        # Check if the image has an alpha channel (RGBA)
        if img.mode == 'RGBA':
            # Create a blank RGB image with a white background
            rgb_img = Image.new("RGB", img.size, (255, 255, 255))
            # Paste the RGBA image onto the RGB background
            rgb_img.paste(img, mask=img.split()[3])  # 3 is the alpha channel
            img = rgb_img

        # Resize the image, compress only if the size is greater than max_disk_space
        if len(data) > max_disk_space:
            resized_img = img.resize(new_size, Image.Resampling.LANCZOS)
        else:
            resized_img = img.resize(img.size)

        # Save the resized image to a bytes buffer
        buffered = io.BytesIO()
        resized_img.save(buffered, format="JPEG")

        # Re-encode to Base64
        compressed_b64 = base64.b64encode(buffered.getvalue())
        decoded_b64 = compressed_b64.decode()
        # prefix appropriate header based on resized image format, that is JPEG
        data_uri = f"data:image/jpeg;base64,{decoded_b64}"


        # print(f"Compressed size: {len(decoded_b64)}")

        return data_uri
    except Exception as e:
        print(f"Error compressing base64: {e}")
        return data_uri


def extract_sex_from_italian_fiscal_code(fiscal_code):
    try:
        # In Italian fiscal codes, the 10th-12th characters encode the birth date.
        # For males, this is the day of the month. For females, it's the day of the month plus 40.
        encoded_day = int(fiscal_code[9:11])
        if encoded_day > 31:
            return 'F'  # Female
        else:
            return 'M'  # Male
    except Exception as e:
        print(f"Error in getting sex from fiscal code: {e}")
        return None  # In case of an error or invalid fiscal code


def get_data_from_italian_fiscal_code(fiscal_code):
    try:
        result = None
        try:
            result = codicefiscale.decode(fiscal_code)
        except ValueError as e:
            res = str(e)
            # given this kind of error: Error getting data from fiscal code: [codicefiscale] wrong CIN (Control Internal Number): expected 'Y', found 'L'
            # extract the correct CIN (the one expected) and replace the last character of the fiscal code with it
            if "wrong CIN" in res:
                cin = res.split("expected ")[1].split(",")[0].replace("'", "")
                fiscal_code = fiscal_code[:-1] + cin
                result = codicefiscale.decode(fiscal_code)
        return result
    except Exception as e:
        print(f"Error getting data from fiscal code: {e}")
        return None


def export_customer_to_crm(sport_association):
    try:
        headers = {
            'authtoken': CRM_API_TOKEN,
        }
        # we create the x-www-form-urlencoded data
        data = {
            'company': sport_association.denomination,
            'default_language': 'it',
            'city': sport_association.address_city,
            'country': 'Italy',
            'address': sport_association.address,
            'zip': sport_association.address_cap,
            'vat': sport_association.tax_code,
            'default_currency': 'EUR',
        }

        # make post request to https://team.bakney.com/api/customers
        requests.post('https://team.bakney.com/api/customers', headers=headers, data=data)

        # make request to search for the customer
        response = requests.get(
            f'https://team.bakney.com/api/customers/search/{sport_association.denomination.replace(" ", "_")}',
            headers=headers)

        # we get the response
        response = response.json()
        user_id = response[0]['userid']

        # we save the user_id
        sport_association.crm_user_id = user_id

        # we then create the contact
        # generate random password of 8 characters
        password = ''.join(
            [random.choice('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(8)])
        data = {
            'customer_id': user_id,
            'firstname': sport_association.user.first_name,
            'lastname': sport_association.user.last_name,
            'email': sport_association.user.email,
            'password': password
        }

        # make post request to https://team.bakney.com/api/contacts
        requests.post('https://team.bakney.com/api/contacts', headers=headers, data=data)

        # we then search the contact
        response = requests.get(f'https://team.bakney.com/api/contacts/search/{sport_association.user.email}',
                                headers=headers)
        response = response.json()

        # we save the contact_id
        contact_id = response[0]['id']
        sport_association.crm_contact_id = contact_id
        sport_association.save()
    except Exception as e:
        print(f"Error exporting customer to crm: {e}")
        pass


REMINDER_UNITS_MAP = {
    'minutes': 60,
    'hours': 3600,
    'days': 86400,
}

REMINDER_UNITS_MAP_TEXT = {
    'minutes': 'minuti',
    'hours': 'ore',
    'days': 'giorni',
}


def get_seconds_from_reminder_units(amount, unit):
    return int(amount) * REMINDER_UNITS_MAP[unit]


def migrate_bcrypt_to_django(password: str, bcrypt_hash: str) -> str:
    """
    Verifies BCrypt hash and converts to Django's PBKDF2 if valid
    Returns new PBKDF2 hash if valid, None if invalid
    """
    try:
        is_valid = bcrypt.checkpw(
            password.encode('utf-8'),
            bcrypt_hash.encode('utf-8')
        )
        if is_valid:
            return make_password(password)
        return None
    except ValueError:
        return None

def parse_date(date_str: str) -> date:
    """
    Parse a date string in either DD/MM/YYYY or YYYY-MM-DD format.
    Raises ValueError for invalid formats.
    """
    try:
        if '/' in date_str:
            return datetime.strptime(date_str, '%d/%m/%Y').date()
        else:
            return datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError(f"Date must be in DD/MM/YYYY or YYYY-MM-DD format: {date_str}")


def filter_by_all_fields(queryset, search_term):
    """Filter queryset by all fields using the provided search term."""
    # Get all field objects from the model
    fields = queryset.model._meta.fields

    # Create a list to hold all the Q objects
    queries = []

    # Try to convert the search term to float for numeric fields
    try:
        numeric_search = float(search_term.replace(',', '.'))
        numeric_search_valid = True
    except (ValueError, TypeError):
        numeric_search_valid = False

    # Try to convert to int for integer fields
    try:
        int_search = int(search_term)
        int_search_valid = True
    except (ValueError, TypeError):
        int_search_valid = False

    # Build the queries for each field based on field type
    for field in fields:
        field_type = field.__class__
        field_name = field.name

        # Skip fields that shouldn't be searched
        if isinstance(field, (ForeignKey, ManyToManyField, OneToOneField)):
            # For foreign keys, search the related model's string representation
            # Example: field_name__name__icontains if the related model has a name field
            continue  # Skip ForeignKeys in basic implementation, add related field lookups as needed

        # Text fields
        elif isinstance(field, (CharField, TextField)):
            queries.append(Q(**{f"{field_name}__icontains": search_term}))

        # Integer fields
        elif isinstance(field, IntegerField) and int_search_valid:
            queries.append(Q(**{f"{field_name}__exact": int_search}))

        # Float/Decimal fields
        elif isinstance(field, (FloatField, DecimalField)) and numeric_search_valid:
            queries.append(Q(**{f"{field_name}__exact": numeric_search}))

    # Combine all queries with OR operator
    if queries:
        return queryset.filter(reduce(operator.or_, queries))
    return queryset  # Return the original queryset if no valid fields
