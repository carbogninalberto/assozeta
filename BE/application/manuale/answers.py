"""Deterministic Italian answers over retrieved evidence and owner diagnostics."""
import re

# These are assistant-directed control instructions, not application steps.
# This narrow check is defense in depth, not a general prompt-injection detector.
ASSISTANT_INSTRUCTION = re.compile(
    r'\b(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|system|developer)\s+'
    r'(?:instructions?|messages?|prompts?)\b'
    r'|\b(?:ignora|ignorare|dimentica)\s+(?:tutte\s+)?(?:le\s+)?(?:istruzioni|indicazioni|regole)\s+'
    r'(?:precedenti|di\s+sistema)\b'
    r'|(?:^|\n)\s*(?:system|developer|assistant|sistema|assistente)\s*:', re.I)


def _reader_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _reader_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in ('text', 'markdown', 'title', 'alt', 'content', 'cards'):
                yield from _reader_strings(item)


def manual_evidence_problem(result):
    """Reject declared conflicts, contradictory identities and control prose.

    Different section IDs may legitimately explain the same operation with
    different words. This does not attempt semantic contradiction detection.
    """
    hits = result.get('results', [])
    if result.get('status') in ('conflicting', 'conflict') or any(
            hit.get('status') in ('conflicting', 'conflict') for hit in hits):
        return 'conflicting'
    if hits and (result.get('status', 'verified') != 'verified'
            or any(hit.get('status', 'verified') != 'verified' for hit in hits)):
        return 'unverified'
    seen = {}
    for hit in hits:
        identifier = hit.get('id')
        previous = seen.get(identifier) if identifier else None
        if previous is not None and (previous.get('text') != hit.get('text')
                or (previous.get('content_sha256') and hit.get('content_sha256')
                    and previous['content_sha256'] != hit['content_sha256'])
                or (previous.get('reader') is not None and hit.get('reader') is not None
                    and previous['reader'] != hit['reader'])):
            return 'conflicting'
        if identifier:
            seen[identifier] = hit
        prose = [hit.get('text', ''), hit.get('title', ''), *_reader_strings(hit.get('reader', []))]
        if any(ASSISTANT_INSTRUCTION.search(text) for text in prose):
            return 'irrelevant_instructions'
    return None

def is_manual_question(message):
    """Route explicit how-to requests; ordinary data questions keep their agent path."""
    message = message.lower().strip()
    return bool(re.search(r'\b(manuale|istruzioni|tutorial|guida per)\b', message)
                or re.search(r'\b(come|dove)\s+(?:posso\s+|devo\s+|si\s+|faccio\s+a\s+)?'
                             r'(creare|creo|crea|aggiungere|aggiungo|aggiunge|assegnare|assegno|assegna|modificare|modifico|modifica|configurare|configuro|configura|'
                             r'importare|importo|esportare|esporto|registrare|registro|abilitare|abilito|eliminare|elimino|'
                             r'incassare|incasso|incassa|approvare|approvo|approva|scaricare|scarico|scarica|'
                             r'gestire|gestisco|trovare|trovo|cercare|cerco|cerca|svuotare|svuoto|svuota|cancellare|cancello|'
                             r'archiviare|archivio|archivia|ripristinare|ripristino|ripristina|'
                             r'correggere|correggo|corregge|copiare|copio|copia|controllare|controllo|controlla|'
                             r'cambiare|cambio|cambia|personalizzare|personalizzo|personalizza|ridimensionare|ridimensiono|ridimensiona|'
                             r'impostare|imposto|imposta|segnare|segno|segna|rimuovere|rimuovo|rimuove|'
                             r'generare|genero|genera|stampare|stampo|caricare|carico|invitare|invito|salvare|salvo|inviare|invio|invia)\b', message))


def is_manual_diagnostic_question(message):
    message = message.lower()
    return bool(re.search(r'\b(manuale|documentazione)\b', message)
                and re.search(r'\b(lacune|copertura|diagnostica|sezioni non verificate|fonti obsolete)\b', message))


def manual_diagnostic_answer(result):
    if result.get('status') != 'diagnostic':
        return result.get('message') or 'La diagnostica del manuale non è disponibile.'
    summary = result['summary']
    verified = summary['verified_sections']
    if result['coverage_status'] == 'complete_inventory':
        text = (f"Manuale: {summary['navigation_pages']} pagine inventariate, "
                f"{verified} sezioni verificate su {summary['sections']}.")
    else:
        text = f"Manuale: {verified} sezioni verificate. L'inventario delle lacune è parziale."
    if result['snapshot_status'] == 'stale':
        text += '\nIl pacchetto riguarda una versione diversa o sorgenti che sono cambiate. Deve essere verificato nuovamente.'
    if result['gaps']:
        labels = {'pending': 'da verificare', 'stale': 'da aggiornare', 'unsupported': 'non supportata',
                  'conflicting': 'indicazioni in conflitto',
                  'needs_external_verification': 'richiede verifiche esterne'}
        pages = {page['path']: page for page in result['pages']}
        examples = []
        for gap in result['gaps'][:10]:
            page = pages.get(gap['page'], {})
            section = next((item for item in page.get('sections', []) if item['id'] == gap['section']), {})
            examples.append(f"- {page.get('title', gap['page'])} — {section.get('title', gap['section'])}: {labels.get(gap['status'], gap['status'])}")
        text += '\n\nSezioni che richiedono attenzione:\n' + '\n'.join(examples)
        if len(result['gaps']) > len(examples):
            text += f"\nAltre {len(result['gaps']) - len(examples)} sezioni sono elencate nella diagnostica completa."
    return text


def selected_manual_section(result):
    """The rich and text responses must make the same ambiguity decision."""
    if manual_evidence_problem(result):
        return None
    hits = result.get('results', [])
    if not hits:
        return None
    first = hits[0]
    same_operation = bool(first.get('intent') and len(hits) > 1 and first['intent'] == hits[1].get('intent'))
    if len(hits) > 1 and not same_operation and first['page'] != hits[1]['page'] and first.get('score', 0) - hits[1].get('score', 0) < .025:
        return None
    return first


def manual_section_attachment(result):
    """Project verified public retrieval, never model-written steps or images."""
    section = selected_manual_section(result)
    if result.get('status') != 'verified' or not section or section.get('status') != 'verified':
        return None
    if not section.get('reader') or not section.get('embedded_url'):
        return None
    return {key: section[key] for key in ('id', 'page', 'title', 'page_title', 'reader', 'screenshots', 'embedded_url')}


def grounded_answer(result):
    """Extractive generation keeps cited procedural content supported by its chunk.

    No model can emit an unverified step on this explicit documentation route.
    Other conversational requests can use the same tools inside the agent loop.
    """
    problem = manual_evidence_problem(result)
    if problem == 'conflicting':
        return 'Le indicazioni recuperate sono in conflitto. Non posso indicare una procedura verificata finché il manuale non viene corretto.'
    if problem:
        return 'Non ho trovato istruzioni verificate utilizzabili per questa domanda.'
    hits = result.get('results', [])
    if not hits:
        return result.get('message') or 'Non ho trovato istruzioni verificate. Quale operazione desideri eseguire?'
    first = selected_manual_section(result)
    if first is None:
        options = '\n'.join(f'- [{hit["title"]}]({hit.get("embedded_url") or hit["url"]})' for hit in hits[:3])
        return 'Quale di queste operazioni desideri approfondire?\n\n' + options
    return f'{first["text"]}\n\n[Leggi nel manuale]({first["url"]})'
