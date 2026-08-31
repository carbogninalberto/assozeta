import logging
import re
from bs4 import BeautifulSoup
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

LEGACY_MENTION_ALIASES = {
    'nome': 'associate.first_name',
    'cognome': 'associate.last_name',
    'codicefiscale': 'associate.tax_code',
    'cf': 'associate.tax_code',
    'datadinascita': 'associate.born_date',
    'datanascita': 'associate.born_date',
    'cittadinascita': 'associate.born_city',
    'indirizzo': 'associate.address',
    'citta': 'associate.address_city',
    'cap': 'associate.address_cap',
    'nazionalita': 'associate.nationality',
    'email': 'associate.email',
    'telefono': 'associate.phone',
    'note': 'associate.notes',
    'nometutore': 'main_tutor.first_name',
    'cognometutore': 'main_tutor.last_name',
    'codicefiscaletutore': 'main_tutor.tax_code',
    'emailtutore': 'main_tutor.email',
    'telefonotutore': 'main_tutor.phone',
    'numeroricevuta': 'invoice.number',
    'dataricevuta': 'invoice.creation_date',
    'importoattivita': 'invoice.activity_fee',
    'importoiscrizione': 'invoice.membership_fee',
    'importototale': 'invoice.total_amount',
    'dataodierna': 'other.today',
    'listacorsi': 'other.courses_list',
}

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


def _format_mention_value(value):
    value = '' if value is None else str(value).strip()
    if len(value) == 10 and value[4] == '-' and value[7] == '-':
        return f'{value[8:10]}/{value[5:7]}/{value[0:4]}'
    return value


def _resolve_mention(key, context_objects):
    obj_name, separator, attr_name = key.partition('.')
    if not separator or obj_name not in context_objects:
        return ''
    return _format_mention_value(get_nested_attr(context_objects[obj_name], attr_name))

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

        # Replace the span with the value
        span.replace_with(_resolve_mention(key, context_objects))

    alias_pattern = re.compile(
        r'(?<![\w@])@(' + '|'.join(
            sorted(map(re.escape, LEGACY_MENTION_ALIASES), key=len, reverse=True)
        ) + r')\b',
        flags=re.IGNORECASE,
    )
    for text_node in list(soup.find_all(string=True)):
        parent = text_node.parent
        if parent is None or parent.name in {'script', 'style'}:
            continue
        if parent.name == 'span' and 'mention' in (parent.get('class') or []):
            continue

        replaced = alias_pattern.sub(
            lambda match: _resolve_mention(
                LEGACY_MENTION_ALIASES[match.group(1).lower()],
                context_objects,
            ),
            str(text_node),
        )
        if replaced != str(text_node):
            text_node.replace_with(replaced)

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
