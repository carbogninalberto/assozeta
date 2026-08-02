"""
Copyright: Bakney S.r.l.
Serializers for Audit Log API endpoint.
"""
import json
import logging
import re
from rest_framework import serializers
from auditlog.models import LogEntry

logger = logging.getLogger(__name__)

# UUID regex pattern
UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)


def is_uuid(value):
    """Check if a string is a valid UUID."""
    if not isinstance(value, str):
        return False
    return bool(UUID_PATTERN.match(value))


def resolve_uuid(uuid_str, field_name):
    """
    Resolve a UUID to a human-readable representation based on field name.
    Returns (resolved_value, label) or (None, None) if not resolvable.
    """
    if not is_uuid(uuid_str):
        return None, None

    # Import models lazily to avoid circular imports
    from application.models import (
        CourseSubscription, Associate, Course, Subscription,
        Payment, Instructor, Carnet, MedicalCertificate, Invoice, Module,
        Group, Family, Tags
    )
    from application.models.carnet_models import CarnetSubscription
    from application.models.attendee_models import (
        AttendanceRegistry, AttendanceDay, GlobalCalendarEvents, Reminders
    )
    from application.models.subscriptions_models import SubscriptionMembership
    from application.models.courses_models import (
        CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsSubscription
    )

    # Field name to model mapping
    field_resolvers = {
        'course_subscription_id': (
            CourseSubscription,
            'course_subscription_id',
            lambda obj: f"{obj.subscription.associate.first_name} {obj.subscription.associate.last_name} - {obj.course.title}"
        ),
        'course_subscription': (
            CourseSubscription,
            'course_subscription_id',
            lambda obj: f"{obj.subscription.associate.first_name} {obj.subscription.associate.last_name} - {obj.course.title}"
        ),
        'associate_id': (
            Associate,
            'associate_id',
            lambda obj: f"{obj.first_name} {obj.last_name}"
        ),
        'associate': (
            Associate,
            'associate_id',
            lambda obj: f"{obj.first_name} {obj.last_name}"
        ),
        'course_id': (
            Course,
            'course_id',
            lambda obj: obj.title
        ),
        'course': (
            Course,
            'course_id',
            lambda obj: obj.title
        ),
        'subscription_id': (
            Subscription,
            'subscription_id',
            lambda obj: f"{obj.associate.first_name} {obj.associate.last_name} - {obj.subscription_type or 'Iscrizione'}"
        ),
        'subscription': (
            Subscription,
            'subscription_id',
            lambda obj: f"{obj.associate.first_name} {obj.associate.last_name} - {obj.subscription_type or 'Iscrizione'}"
        ),
        'payment_id': (
            Payment,
            'payment_id',
            lambda obj: f"{obj.description or 'Pagamento'} - €{obj.amount}"
        ),
        'payment': (
            Payment,
            'payment_id',
            lambda obj: f"{obj.description or 'Pagamento'} - €{obj.amount}"
        ),
        'instructor_id': (
            Instructor,
            'instructor_id',
            lambda obj: f"{obj.first_name} {obj.last_name}"
        ),
        'instructor': (
            Instructor,
            'instructor_id',
            lambda obj: f"{obj.first_name} {obj.last_name}"
        ),
        'carnet_id': (
            Carnet,
            'carnet_id',
            lambda obj: obj.title
        ),
        'carnet': (
            Carnet,
            'carnet_id',
            lambda obj: obj.title
        ),
        'carnet_subscription_id': (
            CarnetSubscription,
            'carnet_subscription_id',
            lambda obj: f"{obj.associate.first_name} {obj.associate.last_name} - {obj.carnet.title}"
        ),
        'medical_certificate_id': (
            MedicalCertificate,
            'medical_id',
            lambda obj: f"{obj.associate.first_name} {obj.associate.last_name} - {obj.expiration_date}"
        ),
        'invoice_id': (
            Invoice,
            'invoice_id',
            lambda obj: f"Ricevuta #{obj.invoice_number}"
        ),
        'module_id': (
            Module,
            'module_id',
            lambda obj: obj.title
        ),
        # Attendance related
        'attendance_registry': (
            AttendanceRegistry,
            'attendance_registry_id',
            lambda obj: f"Registro - {obj.course.title}"
        ),
        'attendance_registry_id': (
            AttendanceRegistry,
            'attendance_registry_id',
            lambda obj: f"Registro - {obj.course.title}"
        ),
        'attendance_day': (
            AttendanceDay,
            'attendance_day_id',
            lambda obj: f"{obj.title or 'Giornata'} - {obj.date.strftime('%d/%m/%Y')}"
        ),
        'attendance_day_id': (
            AttendanceDay,
            'attendance_day_id',
            lambda obj: f"{obj.title or 'Giornata'} - {obj.date.strftime('%d/%m/%Y')}"
        ),
        # Groups and Family
        'group_id': (
            Group,
            'group_id',
            lambda obj: obj.name
        ),
        'group': (
            Group,
            'group_id',
            lambda obj: obj.name
        ),
        'family_id': (
            Family,
            'family_id',
            lambda obj: obj.name
        ),
        'family': (
            Family,
            'family_id',
            lambda obj: obj.name
        ),
        # Tags
        'tag_id': (
            Tags,
            'tag_id',
            lambda obj: obj.name
        ),
        'tag': (
            Tags,
            'tag_id',
            lambda obj: obj.name
        ),
        # Subscription membership
        'subscription_membership_id': (
            SubscriptionMembership,
            'subscription_membership_id',
            lambda obj: f"{obj.subscription.associate.first_name} {obj.subscription.associate.last_name} - Tesseramento"
        ),
        'subscription_membership': (
            SubscriptionMembership,
            'subscription_membership_id',
            lambda obj: f"{obj.subscription.associate.first_name} {obj.subscription.associate.last_name} - Tesseramento"
        ),
        # Camps
        'camps_and_retreats_id': (
            CampsAndRetreats,
            'camps_and_retreats_id',
            lambda obj: obj.title
        ),
        'camps_and_retreats': (
            CampsAndRetreats,
            'camps_and_retreats_id',
            lambda obj: obj.title
        ),
        'camps_and_retreats_period_id': (
            CampsAndRetreatsPeriod,
            'camps_and_retreats_period_id',
            lambda obj: f"{obj.camps_and_retreats.title} - {obj.start_date.strftime('%d/%m/%Y')}"
        ),
        'camps_and_retreats_subscription_id': (
            CampsAndRetreatsSubscription,
            'camps_and_retreats_subscription_id',
            lambda obj: f"{obj.associate.first_name} {obj.associate.last_name} - {obj.camps_and_retreats.title}"
        ),
        # Reminders
        'reminders_id': (
            Reminders,
            'reminders_id',
            lambda obj: f"{obj.event_title} - {obj.send_at.strftime('%d/%m/%Y')}"
        ),
        # Global calendar events
        'global_calendar_id': (
            GlobalCalendarEvents,
            'global_calendar_id',
            lambda obj: f"Calendario #{str(obj.global_calendar_id)[:8]}"
        ),
    }

    # Special resolvers for non-FK UUID fields (events stored in JSON)
    special_resolvers = {
        'associated_event': _resolve_event_from_json,
        'event_id': _resolve_event_from_json,
    }

    # Try special resolver first
    if field_name in special_resolvers:
        result = special_resolvers[field_name](uuid_str)
        if result:
            return result, 'Event'

    resolver = field_resolvers.get(field_name)
    if not resolver:
        return None, None

    model_class, pk_field, formatter = resolver

    try:
        obj = model_class.objects.select_related().get(**{pk_field: uuid_str})
        return formatter(obj), model_class.__name__
    except model_class.DoesNotExist:
        return f"[Eliminato: {uuid_str[:8]}...]", model_class.__name__
    except Exception as e:
        logger.debug(f"Error resolving {field_name}={uuid_str}: {e}")
        return None, None


def _resolve_event_from_json(event_id):
    """
    Resolve an event_id by searching through GlobalCalendarEvents.events JSON arrays.
    Events are stored as JSON arrays in the GlobalCalendarEvents model.
    """
    from application.models.attendee_models import GlobalCalendarEvents, AttendanceRegistry

    # First try to find in AttendanceRegistry.events (more specific)
    registries = AttendanceRegistry.objects.filter(events__isnull=False)
    for registry in registries:
        if registry.events:
            events = registry.events if isinstance(registry.events, list) else []
            for event in events:
                if isinstance(event, dict) and event.get('event_id') == event_id:
                    title = event.get('title', 'Evento')
                    start = event.get('start', '')
                    if start:
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                            return f"{title} - {dt.strftime('%d/%m/%Y %H:%M')}"
                        except (ValueError, TypeError):
                            pass
                    return title

    # Try GlobalCalendarEvents as fallback
    calendars = GlobalCalendarEvents.objects.filter(events__isnull=False)
    for calendar in calendars:
        if calendar.events:
            events = calendar.events if isinstance(calendar.events, list) else []
            for event in events:
                if isinstance(event, dict) and event.get('event_id') == event_id:
                    title = event.get('title', 'Evento')
                    start = event.get('start', '')
                    if start:
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                            return f"{title} - {dt.strftime('%d/%m/%Y %H:%M')}"
                        except (ValueError, TypeError):
                            pass
                    return title

    return None


# =============================================================================
# Field-specific formatters for complex JSON fields
# =============================================================================

def format_additional_fields(value):
    """
    Format additional_fields JSON for display.
    Input: [{"props": {"label": "Numero maglia", "value": "XL"}, "type": "text"}, ...]
    Output: "Numero maglia: XL, Campo2: valore2"
    """
    if not value:
        return None

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value

    if not isinstance(value, list):
        return str(value)

    formatted_parts = []
    for field in value:
        if isinstance(field, dict):
            props = field.get('props', {})
            label = props.get('label', 'Campo')
            field_value = props.get('value', '')
            if field_value:  # Only show fields that have values
                formatted_parts.append(f"{label}: {field_value}")

    return ", ".join(formatted_parts) if formatted_parts else None


def format_attendees(value):
    """
    Format attendees JSON for display.
    Input: [{"course_subscription_id": "uuid"}, ...]
    Output: "Mario Rossi, Luigi Verdi" or "3 presenti"
    """
    if not value:
        return None

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value

    if not isinstance(value, list):
        return str(value)

    count = len(value)
    if count == 0:
        return "Nessun presente"

    # Try to resolve names for up to 3 attendees
    if count <= 3:
        names = []
        for attendee in value:
            if isinstance(attendee, dict):
                cs_id = attendee.get('course_subscription_id')
                if cs_id:
                    resolved, _ = resolve_uuid(cs_id, 'course_subscription_id')
                    if resolved and not resolved.startswith('[Eliminato'):
                        # Extract just the name part (before the course title)
                        name_part = resolved.split(' - ')[0] if ' - ' in resolved else resolved
                        names.append(name_part)
                    else:
                        names.append('Atleta')
        if names:
            return ", ".join(names)

    return f"{count} presenti"


def format_events(value):
    """
    Format events JSON for display.
    Input: [{"event_id": "uuid", "title": "Lezione", "start": "2024-01-15T10:00:00"}, ...]
    Output: "Lezione (15/01/2024), Allenamento (16/01/2024)" or "5 eventi"
    """
    if not value:
        return None

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value

    if not isinstance(value, list):
        return str(value)

    count = len(value)
    if count == 0:
        return "Nessun evento"

    # Show details for up to 2 events
    if count <= 2:
        event_strs = []
        for event in value:
            if isinstance(event, dict):
                title = event.get('title', 'Evento')
                start = event.get('start', '')
                if start:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                        event_strs.append(f"{title} ({dt.strftime('%d/%m/%Y')})")
                    except (ValueError, TypeError):
                        event_strs.append(title)
                else:
                    event_strs.append(title)
        if event_strs:
            return ", ".join(event_strs)

    return f"{count} eventi"


def format_collaborator_permissions(value):
    """
    Format collaborator_permissions for display.
    Input: ["association.courses.read", "bookeeping.payments.read", ...]
    Output: "Lettura corsi, Lettura pagamenti" or "5 permessi"
    """
    if not value:
        return "Nessun permesso"

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value

    if not isinstance(value, list):
        return str(value)

    count = len(value)
    if count == 0:
        return "Nessun permesso"

    # Permission translation map
    PERMISSION_LABELS = {
        'association.courses.read': 'Lettura corsi',
        'association.courses.create': 'Creazione corsi',
        'association.courses.update': 'Modifica corsi',
        'association.courses.delete': 'Eliminazione corsi',
        'association.courses.attendance.read': 'Lettura presenze',
        'association.courses.attendance.update': 'Modifica presenze',
        'association.members.read': 'Lettura iscrizioni',
        'association.members.create': 'Creazione iscrizioni',
        'association.members.update': 'Modifica iscrizioni',
        'association.members.delete': 'Eliminazione iscrizioni',
        'association.personas.read': 'Lettura anagrafiche',
        'association.personas.create': 'Creazione anagrafiche',
        'association.personas.update': 'Modifica anagrafiche',
        'association.personas.delete': 'Eliminazione anagrafiche',
        'bookeeping.payments.read': 'Lettura pagamenti',
        'bookeeping.payments.create': 'Creazione pagamenti',
        'bookeeping.payments.update': 'Modifica pagamenti',
        'bookeeping.payments.delete': 'Eliminazione pagamenti',
        'bookeeping.invoices.read': 'Lettura ricevute',
        'bookeeping.invoices.create': 'Creazione ricevute',
    }

    if count <= 3:
        labels = []
        for p in value[:3]:
            label = PERMISSION_LABELS.get(p)
            if not label:
                # Extract last part and capitalize
                parts = p.split('.')
                label = parts[-1].capitalize() if parts else p
            labels.append(label)
        return ", ".join(labels)

    return f"{count} permessi"


def format_boolean(value):
    """Format boolean for Italian display."""
    if value is None or value == 'None':
        return "N/D"
    if isinstance(value, str):
        return "Sì" if value.lower() in ('true', '1', 'yes') else "No"
    return "Sì" if value else "No"


# Registry of field formatters
FIELD_FORMATTERS = {
    'additional_fields': format_additional_fields,
    'attendees': format_attendees,
    'expected_absences': format_attendees,  # Same format as attendees
    'events': format_events,
    'collaborator_permissions': format_collaborator_permissions,
}

# Boolean fields that should be formatted with Sì/No
BOOLEAN_FIELDS = {
    'archived', 'deleted', 'draft', 'trial', 'paid', 'competitive',
    'auto_marked', 'multi_payments', 'one_fee_payment', 'active', 'enabled'
}


# =============================================================================
# Model description resolvers for human-readable object descriptions
# =============================================================================

def get_model_description(content_type, object_pk):
    """
    Resolve a model instance to a human-readable description.
    Returns a formatted string or None if not resolvable.
    """
    if not content_type or not object_pk:
        return None

    model_name = content_type.model

    # Import models lazily to avoid circular imports
    from application.models import (
        CourseSubscription, Associate, Course, Subscription,
        Payment, Instructor, Carnet, MedicalCertificate, Invoice, Module,
        Group, Family, Tags, User
    )
    from application.models.carnet_models import CarnetSubscription
    from application.models.attendee_models import (
        AttendanceRegistry, AttendanceDay, GlobalCalendarEvents, Reminders
    )
    from application.models.subscriptions_models import SubscriptionMembership
    from application.models.courses_models import (
        CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsSubscription
    )
    from application.models.user_models import SportAssociation
    from communications.models import (
        Message, AutomationWorkflow, CommunicationConfiguration, SmsCreditPayment
    )

    # Model-specific description resolvers
    MODEL_DESCRIPTION_RESOLVERS = {
        'subscription': (
            Subscription,
            'subscription_id',
            lambda obj: f"Iscrizione - {obj.associate.first_name} {obj.associate.last_name}" if obj.associate else f"Iscrizione #{str(obj.subscription_id)[:8]}"
        ),
        'coursesubscription': (
            CourseSubscription,
            'course_subscription_id',
            lambda obj: f"{obj.subscription.associate.first_name} {obj.subscription.associate.last_name} - {obj.course.title}" if obj.subscription and obj.subscription.associate else f"Iscrizione Corso #{str(obj.course_subscription_id)[:8]}"
        ),
        'course': (
            Course,
            'course_id',
            lambda obj: f"Corso - {obj.title}"
        ),
        'associate': (
            Associate,
            'associate_id',
            lambda obj: f"{obj.first_name} {obj.last_name}"
        ),
        'payment': (
            Payment,
            'payment_id',
            lambda obj: f"Pagamento - {obj.description or 'N/D'} (€{obj.amount})"
        ),
        'invoice': (
            Invoice,
            'invoice_id',
            lambda obj: f"Ricevuta #{obj.invoice_number}"
        ),
        'instructor': (
            Instructor,
            'instructor_id',
            lambda obj: f"Istruttore - {obj.first_name} {obj.last_name}"
        ),
        'attendanceday': (
            AttendanceDay,
            'attendance_day_id',
            lambda obj: f"Presenze - {obj.title or 'Giornata'} ({obj.date.strftime('%d/%m/%Y')})"
        ),
        'attendanceregistry': (
            AttendanceRegistry,
            'attendance_registry_id',
            lambda obj: f"Registro Presenze - {obj.course.title}"
        ),
        'medicalcertificate': (
            MedicalCertificate,
            'medical_id',
            lambda obj: f"Certificato - {obj.associate.first_name} {obj.associate.last_name}" if hasattr(obj, 'associate') and obj.associate else f"Certificato #{str(obj.medical_id)[:8]}"
        ),
        'carnet': (
            Carnet,
            'carnet_id',
            lambda obj: f"Carnet - {obj.title}"
        ),
        'carnetsubscription': (
            CarnetSubscription,
            'carnet_subscription_id',
            lambda obj: f"Carnet - {obj.associate.first_name} {obj.associate.last_name}" if obj.associate else f"Carnet #{str(obj.carnet_subscription_id)[:8]}"
        ),
        'subscriptionmembership': (
            SubscriptionMembership,
            'subscription_membership_id',
            lambda obj: f"Tesseramento - {obj.subscription.associate.first_name} {obj.subscription.associate.last_name}" if obj.subscription and obj.subscription.associate else "Tesseramento"
        ),
        'group': (
            Group,
            'group_id',
            lambda obj: f"Gruppo - {obj.name}"
        ),
        'family': (
            Family,
            'family_id',
            lambda obj: f"Famiglia - {obj.name}" if obj.name else f"Famiglia #{str(obj.family_id)[:8]}"
        ),
        'tags': (
            Tags,
            'tag_id',
            lambda obj: f"Tag - {obj.name}"
        ),
        'user': (
            User,
            'user_id',
            lambda obj: f"Utente - {obj.first_name} {obj.last_name}" if obj.first_name else f"Utente - {obj.email}"
        ),
        'sportassociation': (
            SportAssociation,
            'sport_association_id',
            lambda obj: f"Associazione - {obj.denomination}" if obj.denomination else f"Associazione #{str(obj.sport_association_id)[:8]}"
        ),
        'module': (
            Module,
            'module_id',
            lambda obj: f"Modulo - {obj.title}"
        ),
        'campsandretreats': (
            CampsAndRetreats,
            'camps_and_retreats_id',
            lambda obj: f"Campo - {obj.title}"
        ),
        'campsandretreatsperiod': (
            CampsAndRetreatsPeriod,
            'camps_and_retreats_period_id',
            lambda obj: f"Periodo - {obj.camps_and_retreats.title} ({obj.start_date.strftime('%d/%m/%Y')})"
        ),
        'campsandretreatssubscription': (
            CampsAndRetreatsSubscription,
            'camps_and_retreats_subscription_id',
            lambda obj: f"Iscrizione Campo - {obj.associate.first_name} {obj.associate.last_name}" if obj.associate else "Iscrizione Campo"
        ),
        'reminders': (
            Reminders,
            'reminders_id',
            lambda obj: f"Promemoria - {obj.event_title}"
        ),
        'globalcalendarevents': (
            GlobalCalendarEvents,
            'global_calendar_id',
            lambda obj: f"Calendario #{str(obj.global_calendar_id)[:8]}"
        ),
        'message': (
            Message,
            'message_id',
            lambda obj: f"Messaggio - {obj.subject or obj.type}" if obj.subject else f"Messaggio {obj.type}"
        ),
        'automationworkflow': (
            AutomationWorkflow,
            'automation_workflow_id',
            lambda obj: f"Automazione - {obj.name}"
        ),
        'communicationconfiguration': (
            CommunicationConfiguration,
            'communication_configuration_id',
            lambda obj: f"Configurazione Email/SMS"
        ),
        'smscreditpayment': (
            SmsCreditPayment,
            'sms_credit_payment_id',
            lambda obj: f"Acquisto {obj.amount} crediti SMS"
        ),
    }

    resolver = MODEL_DESCRIPTION_RESOLVERS.get(model_name)
    if not resolver:
        return None

    model_class, pk_field, formatter = resolver

    try:
        obj = model_class.objects.select_related().get(**{pk_field: object_pk})
        return formatter(obj)
    except Exception as e:
        logger.debug(f"Could not resolve description for {model_name}={object_pk}: {e}")
        return None


def enrich_value(value, field_name=None):
    """
    Recursively enrich a value by:
    1. Applying field-specific formatters for complex JSON fields
    2. Resolving UUIDs to human-readable names
    3. Formatting booleans with Italian Sì/No

    Returns the enriched value for display.
    """
    # Check for field-specific formatter first
    if field_name and field_name in FIELD_FORMATTERS:
        formatted = FIELD_FORMATTERS[field_name](value)
        if formatted is not None:
            return formatted

    # Handle boolean fields
    if field_name and field_name in BOOLEAN_FIELDS:
        return format_boolean(value)

    if isinstance(value, str):
        # Try to parse as JSON first (for stringified arrays/objects)
        if value.startswith('[') or value.startswith('{'):
            try:
                parsed = json.loads(value)
                return enrich_value(parsed, field_name)
            except json.JSONDecodeError:
                pass

        # Try to resolve as UUID
        if is_uuid(value) and field_name:
            resolved, _ = resolve_uuid(value, field_name)
            if resolved:
                return resolved
        return value

    elif isinstance(value, bool):
        return format_boolean(value)

    elif isinstance(value, list):
        # For lists without specific formatter, try to format nicely
        if len(value) == 0:
            return "(vuoto)"
        return [enrich_value(item, field_name) for item in value]

    elif isinstance(value, dict):
        enriched = {}
        for k, v in value.items():
            enriched[k] = enrich_value(v, k)
        return enriched

    return value


def enrich_changes(changes):
    """
    Enrich the changes dict by resolving UUIDs to human-readable names.
    Input format: {'field': [old_value, new_value]}
    """
    if not changes or not isinstance(changes, dict):
        return changes

    enriched = {}
    for field_name, values in changes.items():
        if isinstance(values, list) and len(values) == 2:
            old_val, new_val = values
            enriched[field_name] = [
                enrich_value(old_val, field_name),
                enrich_value(new_val, field_name)
            ]
        else:
            enriched[field_name] = enrich_value(values, field_name)

    return enriched


class AuditLogEntrySerializer(serializers.ModelSerializer):
    """
    Full serializer for LogEntry with human-readable descriptions (Italian).
    Used for detailed view and list view (default).
    """
    action_label = serializers.SerializerMethodField()
    model_name = serializers.SerializerMethodField()
    model_verbose_name = serializers.SerializerMethodField()
    actor_name = serializers.SerializerMethodField()
    actor_email = serializers.SerializerMethodField()
    changes_summary = serializers.SerializerMethodField()
    object_description = serializers.SerializerMethodField()
    enriched_changes = serializers.SerializerMethodField()
    display_changes = serializers.SerializerMethodField()

    class Meta:
        model = LogEntry
        fields = (
            'id',
            'action',
            'action_label',
            'timestamp',
            'object_pk',
            'object_repr',
            'object_description',
            'model_name',
            'model_verbose_name',
            'actor_name',
            'actor_email',
            'remote_addr',
            'changes',
            'enriched_changes',
            'display_changes',
            'changes_summary',
            'additional_data',
        )

    # Italian labels for actions
    ACTION_LABELS = {
        0: 'Creazione',
        1: 'Modifica',
        2: 'Eliminazione',
    }

    # Italian verbose names for models
    MODEL_VERBOSE_NAMES = {
        'payment': 'Pagamento',
        'subscription': 'Iscrizione',
        'course': 'Corso',
        'coursesubscription': 'Iscrizione Corso',
        'coursesubscriptioninstallment': 'Rata Corso',
        'associate': 'Associato',
        'invoice': 'Ricevuta',
        'invoicesuppliers': 'Fattura Fornitore',
        'invoicerows': 'Riga Fattura',
        'customerinvoice': 'Fattura Cliente',
        'instructor': 'Istruttore',
        'instructorhours': 'Ore Istruttore',
        'module': 'Modulo',
        'moduleresponses': 'Risposta Modulo',
        'carnet': 'Carnet',
        'carnetsubscription': 'Iscrizione Carnet',
        'medicalcertificate': 'Certificato Medico',
        'medicalappointments': 'Appuntamento Medico',
        'sportassociation': 'Associazione Sportiva',
        'supplierandcustomers': 'Fornitore/Cliente',
        'balancesheet': 'Bilancio',
        'customaccounts': 'Conto Personalizzato',
        'customaccountstransfer': 'Trasferimento Conto',
        'tags': 'Tag',
        'coursetags': 'Tag Corso',
        'courselocation': 'Sede Corso',
        'subscriptionmembership': 'Tesseramento',
        'signature': 'Firma',
        'attendanceregistry': 'Registro Presenze',
        'attendanceday': 'Giornata Presenze',
        'globalcalendarevents': 'Evento Calendario',
        'reminders': 'Promemoria',
        'group': 'Gruppo',
        'family': 'Famiglia',
        'campsandretreats': 'Campo/Ritiro',
        'campsandretreatsperiod': 'Periodo Campo',
        'campsandretreatssubscription': 'Iscrizione Campo',
        'paymentcategory': 'Categoria Pagamento',
        'vatmanagement': 'Gestione IVA',
        'nurturingemailsplan': 'Piano Email Nurturing',
        'nurturingemails': 'Email Nurturing',
        'billingplan': 'Piano Abbonamento',
        'billingsubscription': 'Abbonamento Billing',
        'billingpayment': 'Pagamento Billing',
        'user': 'Utente',
        'accesstoken': 'Token Accesso',
        'message': 'Messaggio',
        'automationworkflow': 'Automazione',
        'messagetransaction': 'Invio Messaggio',
        'communicationconfiguration': 'Configurazione Comunicazioni',
        'smscreditpayment': 'Acquisto Crediti SMS',
    }

    # Italian labels for field names
    FIELD_LABELS = {
        # Personal info
        'first_name': 'Nome',
        'last_name': 'Cognome',
        'email': 'Email',
        'phone': 'Telefono',
        'born_date': 'Data di nascita',
        'born_city': 'Luogo di nascita',
        'tax_code': 'Codice Fiscale',
        'address': 'Indirizzo',
        'address_city': 'Città',
        'address_cap': 'CAP',
        'address_province': 'Provincia',
        'gender': 'Genere',
        'nationality': 'Nazionalità',
        # Subscription/Course fields
        'status_flag': 'Stato',
        'start_date': 'Data inizio',
        'end_date': 'Data fine',
        'creation_date': 'Data creazione',
        'notes': 'Note',
        'subscription_type': 'Tipo iscrizione',
        'subscription_number': 'Numero iscrizione',
        'custom_data': 'Dati personalizzati',
        'additional_fields': 'Campi aggiuntivi',
        'archived': 'Archiviato',
        'deleted': 'Eliminato',
        'draft': 'Bozza',
        'trial': 'Prova',
        'competitive': 'Agonistico',
        'role': 'Ruolo',
        'type': 'Tipo',
        # Course fields
        'title': 'Titolo',
        'description': 'Descrizione',
        'fee': 'Quota',
        'events': 'Eventi',
        'multi_payments': 'Pagamenti multipli',
        'one_fee_payment': 'Quota unica',
        'max_participants': 'Partecipanti max',
        'min_age': 'Età minima',
        'max_age': 'Età massima',
        # Payment fields
        'amount': 'Importo',
        'paid': 'Pagato',
        'payment_date': 'Data pagamento',
        'payment_method': 'Metodo pagamento',
        'price': 'Prezzo',
        # Attendance fields
        'attendees': 'Presenti',
        'expected_absences': 'Assenze previste',
        'date': 'Data',
        'auto_marked': 'Segnato automaticamente',
        'associated_event': 'Evento associato',
        # User/Collaborator fields
        'collaborator_permissions': 'Permessi collaboratore',
        'collaborator_role': 'Ruolo collaboratore',
        'connected_user': 'Utente collegato',
        # Medical certificate
        'expiration_date': 'Data scadenza',
        'competitive_medical_certificate': 'Certificato agonistico',
        # Invoice fields
        'invoice_number': 'Numero ricevuta',
        'invoice_date': 'Data ricevuta',
        # Membership
        'membership_type': 'Tipo tesseramento',
        'membership_number': 'Numero tessera',
        # Relations
        'associate': 'Associato',
        'course': 'Corso',
        'subscription': 'Iscrizione',
        'payment': 'Pagamento',
        'instructor': 'Istruttore',
        'group': 'Gruppo',
        'family': 'Famiglia',
        'sport_association': 'Associazione',
        'user': 'Utente',
        'attendance_registry': 'Registro presenze',
        'carnet': 'Carnet',
        'medical': 'Certificato medico',
        # Generic
        'active': 'Attivo',
        'enabled': 'Abilitato',
        'name': 'Nome',
        'meta': 'Metadati',
        'document_pdf': 'Documento PDF',
        'supplier': 'Fornitore',
    }

    def get_action_label(self, obj):
        return self.ACTION_LABELS.get(obj.action, 'Sconosciuto')

    def get_model_name(self, obj):
        return obj.content_type.model if obj.content_type else None

    def get_model_verbose_name(self, obj):
        if not obj.content_type:
            return None
        model_name = obj.content_type.model
        return self.MODEL_VERBOSE_NAMES.get(model_name, model_name.replace('_', ' ').title())

    def get_actor_name(self, obj):
        if obj.actor:
            name = f"{obj.actor.first_name} {obj.actor.last_name}".strip()
            return name if name else obj.actor.email
        return None

    def get_actor_email(self, obj):
        if obj.actor:
            return obj.actor.email
        return obj.actor_email

    def get_object_description(self, obj):
        """Generate a human-readable description of the affected object."""
        # First try to resolve a proper description from the actual object
        description = get_model_description(obj.content_type, obj.object_pk)
        if description:
            return description

        # Fallback: if object_repr looks like "Model object (uuid)", use Italian name
        if obj.object_repr and ' object (' in obj.object_repr:
            model_name = self.get_model_verbose_name(obj) or 'Record'
            return f"{model_name} #{str(obj.object_pk)[:8]}"

        # Last resort: use object_repr as-is or formatted model name
        if obj.object_repr:
            return obj.object_repr
        model_name = self.get_model_verbose_name(obj) or 'Record'
        return f"{model_name} #{obj.object_pk}"

    def get_changes_summary(self, obj):
        """Generate a human-readable summary of changes."""
        if not obj.changes:
            return None

        if obj.action == 0:  # CREATE
            return "Record creato"
        elif obj.action == 2:  # DELETE
            return "Record eliminato"
        elif obj.action == 1:  # UPDATE
            changes = obj.changes if isinstance(obj.changes, dict) else {}
            changed_fields = list(changes.keys())
            if len(changed_fields) == 0:
                return "Nessuna modifica"
            elif len(changed_fields) <= 3:
                return f"Modificato: {', '.join(changed_fields)}"
            return f"Modificati {len(changed_fields)} campi"
        return None

    def get_enriched_changes(self, obj):
        """
        Return changes with UUIDs resolved to human-readable names.
        Example: course_subscription_id: 'uuid' -> 'Mario Rossi - Corso Nuoto'
        """
        if not obj.changes:
            return None
        return enrich_changes(obj.changes)

    def get_display_changes(self, obj):
        """
        Return changes formatted for direct frontend display.
        Format: [{"field": "Campo", "field_key": "field_name", "old": "...", "new": "...", "formatted": "..."}, ...]
        """
        if not obj.changes:
            return None

        changes = obj.changes if isinstance(obj.changes, dict) else {}

        display_list = []
        for field_name, values in changes.items():
            # Get Italian label for field
            field_label = self.FIELD_LABELS.get(field_name, field_name.replace('_', ' ').title())

            if isinstance(values, list) and len(values) == 2:
                old_val, new_val = values

                # Enrich both values
                old_display = enrich_value(old_val, field_name)
                new_display = enrich_value(new_val, field_name)

                # Format for display
                old_str = self._format_display_value(old_display)
                new_str = self._format_display_value(new_display)

                display_list.append({
                    'field': field_label,
                    'field_key': field_name,
                    'old': old_str,
                    'new': new_str,
                    'formatted': f"{field_label}: {old_str} → {new_str}"
                })
            else:
                # Single value (for creation records, show all fields)
                enriched = enrich_value(values, field_name)
                value_str = self._format_display_value(enriched)

                display_list.append({
                    'field': field_label,
                    'field_key': field_name,
                    'value': value_str,
                    'formatted': f"{field_label}: {value_str}"
                })

        return display_list

    def _format_display_value(self, value):
        """
        Convert any value to a display-friendly string.
        """
        if value is None or value == 'None':
            return "N/D"
        if value == "" or value == '""':
            return "(vuoto)"
        if isinstance(value, bool):
            return "Sì" if value else "No"
        if isinstance(value, list):
            if len(value) == 0:
                return "(vuoto)"
            # Join simple string lists
            if all(isinstance(v, str) for v in value):
                if len(value) <= 5:
                    return ", ".join(str(v) for v in value)
                return f"{len(value)} elementi"
            return f"{len(value)} elementi"
        if isinstance(value, dict):
            # For dicts, try to extract meaningful info
            if 'label' in value and 'value' in value:
                return f"{value['label']}: {value['value']}"
            # Otherwise just count keys
            return f"{len(value)} campi"
        return str(value)


class AuditLogListMinimalSerializer(serializers.ModelSerializer):
    """
    Minimal serializer for list view when minimal=true is passed.
    Optimized for performance with fewer fields.
    """
    action_label = serializers.SerializerMethodField()
    model_verbose_name = serializers.SerializerMethodField()
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = LogEntry
        fields = (
            'id',
            'action',
            'action_label',
            'timestamp',
            'object_pk',
            'object_repr',
            'model_verbose_name',
            'actor_name',
        )

    def get_action_label(self, obj):
        return AuditLogEntrySerializer.ACTION_LABELS.get(obj.action, 'Sconosciuto')

    def get_model_verbose_name(self, obj):
        if not obj.content_type:
            return None
        return AuditLogEntrySerializer.MODEL_VERBOSE_NAMES.get(
            obj.content_type.model,
            obj.content_type.model.replace('_', ' ').title()
        )

    def get_actor_name(self, obj):
        if obj.actor:
            name = f"{obj.actor.first_name} {obj.actor.last_name}".strip()
            return name if name else obj.actor.email
        return None
