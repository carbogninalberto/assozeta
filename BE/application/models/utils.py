import logging
from bs4 import BeautifulSoup
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

DEFAULT_ADDITIONAL_SECTIONS = [
    {
        "show_to_members": True,
        "show_to_both": True,
        "show_to_athletes": True,
        "name": "INFORMATIVA AI SENSI DELL'EX ART.13 D.LGS. 196/2003",
        "text": """Gentile sig./sig.ra, <br>
                ai sensi dell'ex art.13 d. lgs. 196/2003 (di seguito T.U.), ed in relazione ai dati personali di cui
                codesto ente entrerà in possesso,
                La informiamo di quanto segue: <br>

                <b> 1. FINALITA' DEL TRATTAMENTO DEI DATI.</b><br>
                Il trattamento è finalizzato unicamente per la realizzazione delle finalità istituzionali promosse
                dall'ente medesimo nei limiti delle disposizioni statuarie previste e in conformità a quanto stabilito
                dal D.lgs. n. 196/2003. <br>

                <b>2. MODALITA' DEL TRATTAMENTO DEI DATI.</b><br>
                a) Il trattamento e realizzato per mezzo delle operazioni o complesso di operazioni indicate all'art. 4
                comma 1 lett. a) T.U. : raccolta, registrazione, organizzazione, conservazione, consultazione,
                e distribuzione dei dati. <br>

                b) Le operazioni possono essere svolte con o senza l'ausilio di strumenti elettronici o comunque
                automatizzati. <br>

                c) Il trattamento è svolto dal titolare e/o dagli incaricati del trattamento. <br>

                <b>3. CONFERIMENTO DEI DATI.</b><br>
                Il conferimento di dati personali comuni e/o sensibili è strettamente necessario ai fini dello
                svolgimento delle attività di cui al punto 1.<br>

                <b>4. RIFIUTO DI CONFERIMENTO DI DATI.</b><br>
                L'eventuale rifiuto da parte dell'interessato di conferire dati personali nel caso di cui al punto 3
                comporta l'impossibilità di adempiere
                alle attività di cui punto 1.<br>

                <b>5. COMUNICAZIONE DEI DATI.</b><br>
                I dati personali possono venire a conoscenza degli incaricati del trattamento e possono essere
                comunicati per le finalità di cui al punto 1 a collaboratori esterni e, in genere, a tutti quei soggetti
                cui la comunicazione sia necessaria per il corretto adempimento delle finalità
                indicate nel punto 1.<br>

                <b>6. DIFFUSIONE DEI DATI.</b><br>
                I dati personali non sono soggetti a diffusione di nessun genere. <br>

                <b>7. TITOLARE DEL TRATTAMENTO.</b><br>
                Titolare del trattamento è <b>l'associazione</b>.""",
    },
    {
        "show_to_members": True,
        "show_to_both": True,
        "show_to_athletes": True,
        "name": "CONSENSO PER IL TRATTAMENTO DEI DATI NON SENSIBILI AD USO ISTITUZIONALE",
        "text": """Il/La sottoscritto/a, acquisite le informazioni di cui all'ex articolo 13 del D.lgs. n. 196/2003, ai sensi
                dell'art 23 del predetto decreto, presta
                il proprio consenso all'intero trattamento dei propri dati personali e in particolare: <br>
                <b> · </b> Per l'utilizzo degli stessi per il perseguimento degli scopi statuari per il fine di
                ricevere comunicazioni cartacee o elettroniche
                (newsletter/email) con informazioni in merito all'attività dell'Associazione. <br>
                <b> · </b> Affinché i dati riguardanti l'iscrizione siano comunicati agli enti cui l'associazione
                collabora e da questi trattati nella misura
                necessaria all'adempimento di obblighi previsti dalla legge e dalle norme statuarie. Sono consapevole
                che, in mancanza del mio
                consenso l'ente sportivo non potrà dar luogo
                ai servizi citati. <br>"""
    },
    {
        "show_to_members": True,
        "show_to_both": True,
        "show_to_athletes": True,
        "name": "SCRITTURA PRIVATA DI RICHIESTA CONSENSO",
        "text": """L'ASSOCIAZIONE CHIEDE ALL'ASSOCIATO E/O AL TUTORE,
                SE MINORENNE, L'AUTORIZZAZIONE ALL'USO DI FOTOGRAFIE E RIPRESE DEL SAGGIO O DI OGNI ALTRA OCCASIONE
                CHE RITRAGGONO IL/LA FIGLIO/A MINORE PER SCOPI PUBBLICITARI, COMPRESA LA PUBBLICAZIONE SUL SITO
                DELL'ASSOCIAZIONE.
                <br>"""
    }

]


def get_nested_attr(obj, attr_path):
    """
    Recursively get nested attributes from an object
    """
    if not obj or not attr_path:
        return ""

    attrs = attr_path.split('.')
    current_obj = obj

    for attr in attrs:
        if current_obj is None:
            return ""
        if isinstance(current_obj, dict):
            current_obj = current_obj.get(attr)
        else:
            current_obj = getattr(current_obj, attr, None)

    return str(current_obj) if current_obj is not None else ""

def filter_mentions(html_content: str, context_objects: Dict[str, Any] = None) -> str:
    """
    Filter HTML content by replacing mention spans with their corresponding Django object values.

    Args:
        html_content (str): The HTML content containing mention spans
        context_objects (Dict[str, Any]): Dictionary of objects containing the values
            Example: {
                'sport_association': sport_association_obj,
                'team': team_obj,
                'player': player_obj
            }

    Returns:
        str: Filtered HTML content with mentions replaced by actual values
    """
    if not html_content:
        return ""

    if context_objects is None:
        context_objects = {}

    # Parse HTML content
    soup = BeautifulSoup(html_content, 'html.parser')

    # Find all mention spans
    mention_spans = soup.find_all('span', {
        'class': 'mention',
        'data-type': 'mention'
    })

    logger.info(f"Found {len(mention_spans)} mention spans in the HTML content")

    for span in mention_spans:
        key = span.get('key', '')
        logger.info(f"Found mention span with key {key}")
        if not key:
            continue

        # Split the key into object and attribute
        parts = key.split('.')
        logger.info(f"Split mention key into parts: {parts}")
        if len(parts) < 2:
            continue

        # if more than 2 parts, group the first parts and leave the last part as attribute
        if len(parts) > 2:
            obj_name = parts[0]
            attr_name = ".".join(parts[1:])
            print("TEST", obj_name, attr_name)
        else:
            obj_name, attr_name = parts

        # Get the value from the context objects
        replacement_value = ""
        if obj_name in context_objects:
            obj = context_objects[obj_name]
            # if obj is a dictionary, get the value from the dictionary
            if obj and isinstance(obj, dict):
                replacement_value = obj.get(attr_name, "")
                print("TEST replacement_value", replacement_value)
            # if obj is an object, get the value from the object attribute
            elif obj and hasattr(obj, attr_name):
                logger.info(f"Replacing mention {key} {attr_name} with value {getattr(obj, attr_name)}")
                replacement_value = str(getattr(obj, attr_name))
                if str(replacement_value) == 'None':
                    replacement_value = ''
            else:
                logger.info(f"Object {obj_name} not found in the context objects")
                replacement_value = get_nested_attr(obj, attr_name)
                if replacement_value == 'None':
                    replacement_value = ''

        replacement_value = str(replacement_value).strip()
        try:
            if replacement_value and len(replacement_value) == 10 and replacement_value[4] == '-' and replacement_value[7] == '-':
                replacement_value= replacement_value[8:10] + '/' + replacement_value[5:7] + '/' + replacement_value[0:4]
        except Exception as e:
            logger.info(f"Error converting date: {e}")
            pass

        # Replace the span with the value
        span.replace_with(replacement_value)

    return str(soup)


def extract_values(keys: List[str], context_objects: Dict[str, Any] = None) -> Dict[str, str]:
    """
    Extract values from context objects based on a list of dot-notation keys.

    Args:
        keys (List[str]): List of keys in dot notation (e.g. ['sport_association.user.email'])
        context_objects (Dict[str, Any]): Dictionary of objects containing the values
            Example: {
                'sport_association': sport_association_obj,
                'team': team_obj
            }

    Returns:
        Dict[str, str]: Dictionary mapping keys to their values
        Example: {'sport_association.user.email': 'user@example.com'}
    """
    try:
        result = {}
        if not keys or not context_objects:
            return result

        for key in keys:
            parts = key.split('.')
            if len(parts) < 2:
                continue

            # Get root object name and attribute path
            obj_name = parts[0]
            attr_path = '.'.join(parts[1:])

            # Skip if object not in context
            if obj_name not in context_objects:
                continue

            obj = context_objects[obj_name]
            value = ""

            # Handle dictionary objects
            if isinstance(obj, dict):
                value = obj.get(attr_path, "")
            # Handle object attributes
            elif hasattr(obj, attr_path):
                value = str(getattr(obj, attr_path))
            # Handle nested attributes
            else:
                # Assuming get_nested_attr is available
                value = get_nested_attr(obj, attr_path)

            # Store non-None values
            if value and str(value) != 'None':
                result[key] = value

        return result
    except Exception as e:
        raise ValueError(f"Failed to extract values: {str(e)}")

def get_default_additional_sections():
    return DEFAULT_ADDITIONAL_SECTIONS

def get_default_configuration():
    return {
        'mandatory_phone': False,
        'mandatory_email': False,
        'mandatory_signature': False,
    }

def get_default_enabled_for():
    return ["associate", "associate-membership", "membership"]

def get_default_stripe_methods():
    return ["card", "sepa_debit"]
