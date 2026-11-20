import re
from datetime import date

from auditlog.models import LogEntry
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from application.audit_context import atomic_audit_mutation

from .models import MessageTemplate

CHANNELS = ('email', 'whatsapp', 'sms')
VARIABLES = ('customer_name', 'association_name', 'date', 'time', 'link')
SAMPLE = dict(zip(VARIABLES, ('Giulia Rossi', 'ASD Aurora', '2026-12-15', '18:30', 'https://example.org/info')))
TOKEN = re.compile(r'\{\{[ \t]*([a-z_]+)[ \t]*\}\}')
LOCALE = re.compile(r'[a-z]{2,3}(?:-[a-z0-9]{2,8})*', re.ASCII)
TEMPLATE_FIELDS = ('channel', 'locale', 'name', 'subject', 'body', 'enabled', 'is_default')


def invalid(field, detail):
    raise ValidationError({field: detail})


def text(data, field, maximum, required=False):
    value = data.get(field, '')
    if (not isinstance(value, str) or len(value) > maximum
            or re.search(r'[\x00\ud800-\udfff]', value) or required and not value.strip()):
        invalid(field, 'Testo mancante o non valido.')
    return value


def locale_code(data):
    locale = text(data, 'locale', 35, required=True).strip().lower()
    if not LOCALE.fullmatch(locale):
        invalid('locale', 'Usa un codice lingua come it o en-gb.')
    return locale


def channel_code(data):
    channel = data.get('channel')
    if channel not in CHANNELS:
        invalid('channel', 'Canale non valido.')
    return channel


def render(value, field, variables):
    rest = TOKEN.sub('', value)
    if '{{' in rest or '}}' in rest or '{{{' in value or '}}}' in value:
        invalid(field, 'Sintassi variabile non valida. Usa {{nome_variabile}}.')

    def replace(match):
        name = match[1]
        if name not in VARIABLES:
            invalid(field, f'Variabile sconosciuta: {name}.')
        if not variables.get(name):
            invalid(name, 'Inserisci un valore per questa variabile.')
        return variables[name]

    return TOKEN.sub(replace, value)


def message_text(data, channel, required=True):
    subject = text(data, 'subject', 100, required=required and channel == 'email')
    if (channel != 'email' and subject) or '\r' in subject or '\n' in subject:
        invalid('subject', 'Oggetto non valido.')
    return {'subject': subject, 'body': text(data, 'body', 10000, required=required)}


def rendered_message(template, variables):
    rendered = {field: render(template[field], field, variables) for field in ('subject', 'body')}
    return message_text(rendered, template['channel'])


def validate_template(data):
    channel = channel_code(data)
    result = {
        **message_text(data, channel),
        'channel': channel,
        'locale': locale_code(data),
        'name': text(data, 'name', 100, required=True).strip(),
    }
    for field, default in (('enabled', True), ('is_default', False)):
        value = data.get(field, default)
        if type(value) is not bool:
            invalid(field, 'Valore booleano richiesto.')
        result[field] = value
    if not result['enabled']:
        result['is_default'] = False
    return result


def message_context(data):
    result = {key: text(data, key, limit).strip() for key, limit in (('date', 10), ('time', 5), ('link', 2048))}
    if result['date']:
        try:
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', result['date']):
                raise ValueError
            date.fromisoformat(result['date'])
        except ValueError:
            invalid('date', 'Data non valida.')
    if result['time'] and not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', result['time']):
        invalid('time', 'Ora non valida.')
    if result['link'] and not re.fullmatch(r'https?://[^/\x00-\x20\x7f?#]+[^\x00-\x20\x7f]*', result['link']):
        invalid('link', 'Usa un link HTTP o HTTPS.')
    return result


def template_data(template):
    return {'id': str(template.pk), **{field: getattr(template, field) for field in TEMPLATE_FIELDS}}


def template_audit_values(template):
    return {field: str(getattr(template, field)) for field in (*TEMPLATE_FIELDS, 'archived')}


def audit_template(template, before, actor):
    after = template_audit_values(template)
    changes = {field: [before.get(field, 'None'), value] for field, value in after.items()
               if before.get(field) != value}
    if changes:
        LogEntry.objects.log_create(
            template, changes=changes, actor=actor, actor_email=actor.email,
            action=LogEntry.Action.UPDATE if before else LogEntry.Action.CREATE,
        )


def update_template(template, actor, at, **values):
    before = template_audit_values(template)
    for field, value in values.items():
        setattr(template, field, value)
    template.updated_by_id = actor.pk
    template.updated_at = at
    template.save(update_fields=[*values, 'updated_by_id', 'updated_at'])
    audit_template(template, before, actor)


def association_templates(association):
    templates = MessageTemplate.objects.filter(sport_association=association)
    if not templates.exists():
        with transaction.atomic():
            type(association).objects.select_for_update().get(pk=association.pk)
            if not templates.exists():
                MessageTemplate.objects.bulk_create([
                    MessageTemplate(
                        sport_association=association, channel=channel, locale='it', name='Messaggio di saluto',
                        subject='Un messaggio da {{association_name}}' if channel == 'email' else '',
                        body='Ciao {{customer_name}}, ti scriviamo da {{association_name}}.',
                        enabled=True, is_default=True,
                    )
                    for channel in CHANNELS
                ])
    return templates.filter(archived=False)


def save_template(association, actor, data, template_id=None):
    with atomic_audit_mutation():
        type(association).objects.select_for_update().get(pk=association.pk)
        templates = association_templates(association)
        current = templates.filter(pk=template_id).first() if template_id else None
        if template_id and current is None:
            return None
        values = validate_template({**(template_data(current) if current else {}), **data})
        rendered_message(values, SAMPLE)
        now = timezone.now()
        if values['is_default']:
            defaults = templates.filter(channel=values['channel'], locale=values['locale'], is_default=True).exclude(
                pk=template_id,
            )
            for previous in defaults:
                update_template(previous, actor, now, is_default=False)
        if current is None:
            current = templates.create(sport_association=association, updated_by_id=actor.pk, updated_at=now, **values)
            audit_template(current, {}, actor)
        else:
            update_template(current, actor, now, **values)
        return current


def archive_template(association, actor, template_id):
    with atomic_audit_mutation():
        type(association).objects.select_for_update().get(pk=association.pk)
        current = MessageTemplate.objects.filter(sport_association=association, archived=False, pk=template_id).first()
        if current is None:
            return False
        update_template(current, actor, timezone.now(), archived=True, enabled=False, is_default=False)
        return True


def choose_template(templates, channel, locale, template_id):
    available = templates.filter(channel=channel, enabled=True)
    if template_id:
        return available.filter(pk=template_id).first()
    defaults = {row.locale: row for row in available.filter(is_default=True)}
    for code in (locale, locale.split('-')[0], 'it'):
        if code in defaults:
            return defaults[code]
    return None
