"""High-precision request routing for the in-process assistant.

Routing is only a UX boundary. Tenant scope, tool allowlists and authorization
remain enforced by the tools that execute an action.
"""
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from application.manuale.answers import (
    is_manual_diagnostic_question,
    is_manual_question,
)


class Intent(str, Enum):
    CAPABILITIES = 'capabilities'
    CONVERSATION = 'conversation'
    MANUAL_FAQ = 'manual_faq'
    MANUAL_DIAGNOSTIC = 'manual_diagnostic'
    ACTION = 'action'
    AMBIGUOUS = 'ambiguous'


@dataclass(frozen=True)
class Route:
    intent: Intent
    query: str
    reason: str


_CAPABILITY_PATTERNS = (
    re.compile(r'\b(?:cosa|che\s+cosa|che)\s+sai\s+fare\b'),
    re.compile(r'\b(?:cosa|che\s+cosa|che)\s+puoi\s+fare\b'),
    re.compile(r'\b(?:cosa|che)\s+posso\s+(?:fare|vedere|esportare|creare|richiedere|ottenere)\b'),
    re.compile(r'\b(?:quali|che)\s+(?:cose|funzioni|capacita|capacità)\s+(?:puoi|sai)\s+(?:fare|gestire)\b'),
    re.compile(r'\b(?:quali|che)\s+(?:report|export|analisi|dati)\s+(?:posso|puoi|si\s+possono)\s+'
               r'(?:creare|generare|esportare|richiedere|ottenere)\b'),
    re.compile(r'\bquali\s+sono\s+le\s+(?:tue\s+)?(?:capacita|capacità|funzioni)\b'),
    re.compile(r'\b(?:come|in\s+cosa)\s+puoi\s+aiutarmi\b'),
    re.compile(r'\b(?:per|a)\s+cosa\s+puoi\s+aiutarmi\b'),
    re.compile(
        r'^(?:(?:per\s+favore|gentilmente)\s+)?(?:aiutami|mi\s+aiuti)'
        r'(?:\s+(?:con|su)\s+(?:cosa|qualcosa))?(?:\s+per\s+favore)?[!.?\s]*$'
    ),
    re.compile(
        r'^(?:mi\s+serve|ho\s+bisogno\s+di|avrei\s+bisogno\s+di)\s+(?:un\s+)?aiuto'
        r'(?:\s+per\s+favore)?[!.?\s]*$'
    ),
    re.compile(r'^come\s+posso\s+(?:essere\s+)?aiutato[?.!\s]*$'),
    re.compile(r'\b(?:chi\s+sei|sei\s+un\s+(?:bot|assistente))\b'),
    re.compile(r'\bwhat\s+can\s+you\s+do\b'),
    re.compile(r'^(?:ciao[,\s]*)?(?:puoi|mi\s+puoi|sai)\s+aiutarmi(?:\s+(?:con|su)\s+cosa)?[?.!\s]*$'),
)

_GREETING = re.compile(
    r'^(?:ciao|salve|buongiorno|buonasera|buon\s+pomeriggio|ehi|hey|hello|hi)'
    r'(?:[\s,!.?-]+(?:come\s+stai|tutto\s+bene|come\s+va))?[!.?\s]*$'
)
_THANKS = re.compile(r'^(?:grazie(?:\s+mille)?|perfetto|ottimo|va\s+bene|ok|capito)[!.?\s]*$')
_FAREWELL = re.compile(r'^(?:arrivederci|a\s+presto|ciao\s+ciao|buona\s+giornata)[!.?\s]*$')

_ACTION_START = re.compile(
    r'^(?:(?:per\s+favore|gentilmente)[,\s]+)?'
    r'(?:(?:puoi|potresti|mi\s+puoi|mi\s+potresti|sai|riesci\s+a)\s+)?'
    r'(?:fammi|farmi|fai|dammi|darmi|crea|creami|crearmi|genera|generami|generarmi|prepara|preparami|prepararmi|'
    r'esporta|esportami|esportarmi|salva|salvami|salvarmi|scarica|scaricami|scaricarmi|'
    r'invia|inviami|inviarmi|manda|mandami|mandarmi|mostra|mostrami|mostrarmi|fornisci|forniscimi|'
    r'dimmi|dirmi|mi\s+dici|mi\s+mostri|elenca|elencami|calcola|calcolami|aggrega|riepiloga|visualizza|'
    r'analizza|confronta|conta|trova|cerca|rifai|prosegui|procedi|fallo|crealo|esportalo|general[oi])\b'
)
_MI_ACTION_START = re.compile(
    r'^(?:mi|ci)\s+(?:fai|dai|crei|generi|prepari|esporti|salvi|scarichi|invii|mandi|'
    r'mostri|dici|fornisci)\b'
)
_ASSISTED_ACTION_START = re.compile(
    r'^(?:aiutami|mi\s+aiuti|mi\s+serve\s+aiuto|ho\s+bisogno\s+di\s+aiuto)\s+(?:a|per)\s+'
    r'(?:creare|generare|preparare|esportare|salvare|scaricare|inviare|mandare|mostrare|'
    r'elencare|calcolare|trovare|cercare|analizzare|confrontare)\b'
)
_POLITE_INFINITIVE_ACTION_START = re.compile(
    r'^(?:puoi|potresti|mi\s+puoi|mi\s+potresti)\s+'
    r'(?:creare|generare|preparare|esportare|salvare|scaricare|inviare|mandare|mostrare|'
    r'elencare|calcolare|trovare|cercare|analizzare|confrontare)\b'
)
_OUTPUT_ACTION_START = re.compile(
    r'^(?:(?:per\s+favore|gentilmente)[,\s]+)?'
    r'(?:(?:puoi|potresti|mi\s+puoi|mi\s+potresti|sai|riesci\s+a)\s+)?'
    r'(?:fammi|farmi|fai|dammi|darmi|crea|creami|crearmi|genera|generami|generarmi|'
    r'prepara|preparami|prepararmi|esporta|esportami|esportarmi|salva|salvami|salvarmi|'
    r'scarica|scaricami|scaricarmi|invia|inviami|inviarmi|manda|mandami|mandarmi|'
    r'fornisci|forniscimi|fallo|crealo|esportalo|general[oi])\b'
)
_OUTPUT_MI_ACTION_START = re.compile(
    r'^(?:mi|ci)\s+(?:fai|dai|crei|generi|prepari|esporti|salvi|scarichi|invii|mandi|fornisci)\b'
)
_OUTPUT_ASSISTED_ACTION_START = re.compile(
    r'^(?:aiutami|mi\s+aiuti|mi\s+serve\s+aiuto|ho\s+bisogno\s+di\s+aiuto)\s+(?:a|per)\s+'
    r'(?:creare|generare|preparare|esportare|salvare|scaricare|inviare|mandare)\b'
)
_OUTPUT_POLITE_INFINITIVE_START = re.compile(
    r'^(?:puoi|potresti|mi\s+puoi|mi\s+potresti)\s+'
    r'(?:creare|generare|preparare|esportare|salvare|scaricare|inviare|mandare)\b'
)
_DATA_NOUNS = (
    r'soc(?:io|i)|pagament[oi]|iscrizion[ei]|cors[oi]|certificat[oi]|ricevut[ae]|'
    r'fattur[ae]|presenz[ae]|carnet|bilanc(?:io|i)|report|export|esportazion[ei]|'
    r'comunicazion[ei]|modul[oi]|tag|registro'
)
_QUERY_START = re.compile(
    r'^(?:(?:per\s+favore|gentilmente)[,\s]+)?'
    r'(?:(?:puoi|potresti|mi\s+puoi|mi\s+potresti|sai|riesci\s+a)\s+)?'
    r'(?:(?:dirmi|dire|spiegarmi)\s+)?'
    r'(?:quanti|quante|quanto|quanta|chi|quando|quali|ci\s+sono|'
    r'qual\s+(?:e|è)\s+(?:il|la|lo|i|gli|le)|'
    r'cosa\s+(?:ho|abbiamo|risulta|sono)|che\s+(?:cosa\s+)?(?:ho|abbiamo|risulta|sono)|'
    r'fammi\s+vedere|voglio\s+vedere|vorrei\s+vedere|voglio\s+sapere|vorrei\s+sapere|'
    r'ho\s+bisogno|mi\s+serve)\b'
)
_REPORT_ACTION = re.compile(
    r'\b(?:fai|farmi|crea|creare|creami|crearmi|genera|generare|generami|generarmi|prepara|preparare|preparami|prepararmi|'
    r'esporta|esportare|esportami|esportarmi|'
    r'salva|salvami|scarica|scaricami|posso\s+avere|vorrei|voglio|mi\s+serve|'
    r'ho\s+bisogno\s+di)\b.{0,80}\b(?:report|export|esportazione|pdf|xlsx|csv|file)\b'
    r'|\b(?:report|export|esportazione)\b.{0,50}\b(?:salvat[oi]|pdf|xlsx|csv)\b'
)
_DATA_REQUEST = re.compile(
    r'^(?:voglio|vorrei|mi\s+serve|mi\s+servono|ho\s+bisogno\s+di|avrei\s+bisogno\s+di)\b.{0,80}'
    r'\b(?:' + _DATA_NOUNS + r')\b'
)
_DATA_EXPLANATION_START = re.compile(
    r'^(?:mi\s+spieghi|spiegami)\s+'
    r'(?:(?:il|lo|la|i|gli|le)\s+(?:totale|saldo|importo|elenco|lista|dati|'
    r'andamento|riepilogo|numero)\b|(?:quanti|quante|quanto|quanta|quali|quando|chi)\b|'
    r'(?:' + _DATA_NOUNS + r')\b)'
)
_FOLLOW_UP_PARAMETER = re.compile(
    r'^(?:(?:s[iì]|certo|ok|va\s+bene|perfetto|procedi|fallo|esportalo|crealo|generalo|'
    r'usa|scegli|seleziona|aggiungi|togli|includi|escludi|solo|in|nel|per|del|della|dei|delle|degli|'
    r'dal|dall|tra|fino|quello|quella)\b.*|'
    r'pdf|excel|xlsx|csv|gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|'
    r'settembre|ottobre|novembre|dicembre|quest[ao]\s+(?:mese|anno)|'
    r'(?:tutti|tutte|tutto)\b.*|\d{4})[!.?\s]*$'
)


def normalize_message(message: str) -> str:
    """Normalize case, Unicode and whitespace without changing the user's query."""
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', message).strip())


def _normalized_for_matching(message: str) -> str:
    return normalize_message(message).casefold()


def is_capability_question(message: str) -> bool:
    normalized = _normalized_for_matching(message)
    return any(pattern.search(normalized) for pattern in _CAPABILITY_PATTERNS)


def is_conversation_message(message: str) -> bool:
    normalized = _normalized_for_matching(message)
    return bool(_GREETING.fullmatch(normalized) or _THANKS.fullmatch(normalized)
                or _FAREWELL.fullmatch(normalized))


def is_action_request(message: str) -> bool:
    normalized = _normalized_for_matching(message)
    return bool(_ACTION_START.search(normalized) or _MI_ACTION_START.search(normalized)
                or _ASSISTED_ACTION_START.search(normalized)
                or _POLITE_INFINITIVE_ACTION_START.search(normalized)
                or _QUERY_START.search(normalized) or _DATA_REQUEST.search(normalized)
                or _DATA_EXPLANATION_START.search(normalized)
                or _REPORT_ACTION.search(normalized))


def is_output_action_request(message: str) -> bool:
    normalized = _normalized_for_matching(message)
    return bool(_OUTPUT_ACTION_START.search(normalized)
                or _OUTPUT_MI_ACTION_START.search(normalized)
                or _OUTPUT_ASSISTED_ACTION_START.search(normalized)
                or _OUTPUT_POLITE_INFINITIVE_START.search(normalized))


def is_action_follow_up(message: str) -> bool:
    return bool(_FOLLOW_UP_PARAMETER.fullmatch(_normalized_for_matching(message)))


def route_message(message: str, *, has_pending_action: bool = False) -> Route:
    """Return one explicit route while keeping ambiguous input recoverable."""
    query = normalize_message(message)
    if is_manual_diagnostic_question(query):
        return Route(Intent.MANUAL_DIAGNOSTIC, query, 'manual_diagnostic')
    if is_capability_question(query):
        return Route(Intent.CAPABILITIES, query, 'capabilities')
    if is_output_action_request(query):
        return Route(Intent.ACTION, query, 'action')
    if is_manual_question(query):
        return Route(Intent.MANUAL_FAQ, query, 'manual_faq')
    if has_pending_action and is_action_follow_up(query):
        return Route(Intent.ACTION, query, 'action_follow_up')
    if is_action_request(query):
        return Route(Intent.ACTION, query, 'action')
    if is_conversation_message(query):
        return Route(Intent.CONVERSATION, query, 'conversation')
    return Route(Intent.AMBIGUOUS, query, 'ambiguous')


def capability_answer(*, ai_enabled: bool, manual_available: bool,
                      saved_reports_enabled: bool = False) -> str:
    """Describe only capabilities that can actually run in this session."""
    if ai_enabled:
        abilities = [
            "cercare e riepilogare i dati dell'associazione",
            'rispondere a domande sui dati',
            'preparare export e report',
        ]
        if saved_reports_enabled:
            abilities.append('consultare i report salvati')
        if manual_available:
            abilities.append('consultare le istruzioni verificate del manuale')
        return ('In base alle autorizzazioni del tuo account, posso '
                f"{', '.join(abilities[:-1])} e {abilities[-1]}.")
    if manual_available:
        return ('Posso cercare istruzioni verificate nel manuale di Assozeta. '
                "Le analisi dei dati, gli export e la creazione di report richiedono l'attivazione dell'AI.")
    return 'In questa configurazione non sono disponibili la ricerca nel manuale né le funzioni AI.'


def manual_only_action_answer(*, manual_available: bool) -> str:
    if manual_available:
        return ("Non posso eseguire analisi dei dati, export o report perché l'AI non è configurata. "
                'Posso cercare le istruzioni verificate nel manuale: indica la funzione che vuoi usare.')
    return "Non posso eseguire questa operazione perché l'AI non è configurata in questa istanza."


def ambiguous_manual_answer() -> str:
    return ('Non sono riuscito a collegare la richiesta a un’istruzione verificata. '
            'Indica la funzione del gestionale e cosa vuoi ottenere; '
            "per analisi ed export dei dati serve l'AI configurata.")
