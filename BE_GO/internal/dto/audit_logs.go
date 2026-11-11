package dto

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrAuditUnhandled is an exception the Django serializer lets escape
	// (AttributeError, TypeError, ValueError): the stable 500 page.
	ErrAuditUnhandled = errors.New("audit serializer exception")
	// ErrAuditNotPorted refuses inputs whose Python semantics are not ported
	// (see audit-logs.md): also the 500 page, never a guessed body.
	ErrAuditNotPorted = refusal.New("audit serializer input not ported")
)

// AuditResolver is resolve_uuid(value, field) for a value is_uuid accepted:
// nil when Python returns (None, None), else the resolved value.
type AuditResolver interface {
	ResolveUUID(value, field string) (any, error)
}

var auditUUIDPattern = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\n?$`)

// AuditIsUUID is is_uuid: UUID_PATTERN.match, whose $ also matches before a
// final newline.
func AuditIsUUID(value any) bool {
	text, ok := value.(string)
	return ok && auditUUIDPattern.MatchString(text)
}

var auditActionLabels = map[int64]string{0: "Creazione", 1: "Modifica", 2: "Eliminazione"}

func AuditActionLabel(action int64) string {
	if label, ok := auditActionLabels[action]; ok {
		return label
	}
	return "Sconosciuto"
}

// AuditModelVerboseName is MODEL_VERBOSE_NAMES.get(model, title()).
func AuditModelVerboseName(model string) (string, error) {
	if label, ok := auditModelLabels[model]; ok {
		return label, nil
	}
	return auditTitle(strings.ReplaceAll(model, "_", " "))
}

// auditTitle is str.title() for ASCII text; other text is refused because
// Python's full case mappings and cased-character rules are not ported.
func auditTitle(text string) (string, error) {
	if !auditASCII(text) {
		return "", ErrAuditNotPorted
	}
	return PythonTitle(text), nil
}

func auditASCII(text string) bool {
	for i := 0; i < len(text); i++ {
		if text[i] >= utf8.RuneSelf {
			return false
		}
	}
	return true
}

var auditFieldLabels = map[string]string{
	"first_name": "Nome", "last_name": "Cognome", "email": "Email", "phone": "Telefono", "born_date": "Data di nascita",
	"born_city": "Luogo di nascita", "tax_code": "Codice Fiscale", "address": "Indirizzo", "address_city": "Città",
	"address_cap": "CAP", "address_province": "Provincia", "gender": "Genere", "nationality": "Nazionalità",
	"status_flag": "Stato", "start_date": "Data inizio", "end_date": "Data fine", "creation_date": "Data creazione",
	"notes": "Note", "subscription_type": "Tipo iscrizione", "subscription_number": "Numero iscrizione",
	"custom_data": "Dati personalizzati", "additional_fields": "Campi aggiuntivi", "archived": "Archiviato",
	"deleted": "Eliminato", "draft": "Bozza", "trial": "Prova", "competitive": "Agonistico", "role": "Ruolo", "type": "Tipo",
	"title": "Titolo", "description": "Descrizione", "fee": "Quota", "events": "Eventi", "multi_payments": "Pagamenti multipli",
	"one_fee_payment": "Quota unica", "max_participants": "Partecipanti max", "min_age": "Età minima", "max_age": "Età massima",
	"amount": "Importo", "paid": "Pagato", "payment_date": "Data pagamento", "payment_method": "Metodo pagamento", "price": "Prezzo",
	"attendees": "Presenti", "expected_absences": "Assenze previste", "date": "Data", "auto_marked": "Segnato automaticamente",
	"associated_event": "Evento associato", "collaborator_permissions": "Permessi collaboratore",
	"collaborator_role": "Ruolo collaboratore", "connected_user": "Utente collegato", "expiration_date": "Data scadenza",
	"competitive_medical_certificate": "Certificato agonistico", "invoice_number": "Numero ricevuta", "invoice_date": "Data ricevuta",
	"membership_type": "Tipo tesseramento", "membership_number": "Numero tessera", "associate": "Associato", "course": "Corso",
	"subscription": "Iscrizione", "payment": "Pagamento", "instructor": "Istruttore", "group": "Gruppo", "family": "Famiglia",
	"sport_association": "Associazione", "user": "Utente", "attendance_registry": "Registro presenze", "carnet": "Carnet",
	"medical": "Certificato medico", "active": "Attivo", "enabled": "Abilitato", "name": "Nome", "meta": "Metadati",
	"document_pdf": "Documento PDF", "supplier": "Fornitore",
}

func auditFieldLabel(field string) (string, error) {
	if label, ok := auditFieldLabels[field]; ok {
		return label, nil
	}
	return auditTitle(strings.ReplaceAll(field, "_", " "))
}

var auditPermissionLabels = map[string]string{
	"association.courses.read": "Lettura corsi", "association.courses.create": "Creazione corsi",
	"association.courses.update": "Modifica corsi", "association.courses.delete": "Eliminazione corsi",
	"association.courses.attendance.read": "Lettura presenze", "association.courses.attendance.update": "Modifica presenze",
	"association.members.read": "Lettura iscrizioni", "association.members.create": "Creazione iscrizioni",
	"association.members.update": "Modifica iscrizioni", "association.members.delete": "Eliminazione iscrizioni",
	"association.personas.read": "Lettura anagrafiche", "association.personas.create": "Creazione anagrafiche",
	"association.personas.update": "Modifica anagrafiche", "association.personas.delete": "Eliminazione anagrafiche",
	"bookeeping.payments.read": "Lettura pagamenti", "bookeeping.payments.create": "Creazione pagamenti",
	"bookeeping.payments.update": "Modifica pagamenti", "bookeeping.payments.delete": "Eliminazione pagamenti",
	"bookeeping.invoices.read": "Lettura ricevute", "bookeeping.invoices.create": "Creazione ricevute",
}

var auditBooleanFields = map[string]bool{"archived": true, "deleted": true, "draft": true, "trial": true, "paid": true,
	"competitive": true, "auto_marked": true, "multi_payments": true, "one_fee_payment": true, "active": true, "enabled": true}

// AuditJSONLoads is json.loads(text) for the serializer: decoded=false is a
// JSONDecodeError (which every caller catches). Integers over Python's 4300
// digit limit raise ValueError (not caught); NaN/Infinity literals, lone
// surrogate escapes and deep nesting are refused.
func AuditJSONLoads(text string) (any, bool, error) {
	if auditJSONDepth(text) > 500 || auditLoneSurrogate(text) {
		return nil, false, ErrAuditNotPorted
	}
	value, err := parseReferenceJSON([]byte(text), nil, true)
	switch {
	case errors.Is(err, ErrLoginRateJSONValue):
		return nil, false, ErrAuditUnhandled
	case err != nil && (strings.Contains(text, "NaN") || strings.Contains(text, "Infinity")):
		return nil, false, ErrAuditNotPorted
	case err != nil:
		return nil, false, nil
	}
	return value, true, nil
}

// AuditStoredJSON loads a jsonb column the way Django's JSONField does.
func AuditStoredJSON(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	value, ok, err := AuditJSONLoads(*text)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, fmt.Errorf("stored audit JSON is not valid JSON")
	}
	return value, nil
}

func auditJSONDepth(text string) int {
	depth, deepest, quoted, escaped := 0, 0, false, false
	for i := 0; i < len(text); i++ {
		c := text[i]
		switch {
		case escaped:
			escaped = false
		case quoted && c == '\\':
			escaped = true
		case c == '"':
			quoted = !quoted
		case !quoted && (c == '[' || c == '{'):
			depth++
			if depth > deepest {
				deepest = depth
			}
		case !quoted && (c == ']' || c == '}'):
			depth--
		}
	}
	return deepest
}

// auditLoneSurrogate reports a \uD800-\uDFFF escape that is not a high
// surrogate immediately followed by a low surrogate escape.
func auditLoneSurrogate(text string) bool {
	unit := func(i int) (int, bool) {
		if i+6 > len(text) || text[i] != '\\' || text[i+1] != 'u' {
			return 0, false
		}
		value, err := strconv.ParseUint(text[i+2:i+6], 16, 16)
		return int(value), err == nil
	}
	for i := 0; i < len(text); i++ {
		if text[i] != '\\' {
			continue
		}
		if i+1 < len(text) && text[i+1] != 'u' {
			i++
			continue
		}
		value, ok := unit(i)
		if !ok {
			continue
		}
		switch {
		case value >= 0xd800 && value <= 0xdbff:
			low, ok := unit(i + 6)
			if !ok || low < 0xdc00 || low > 0xdfff {
				return true
			}
			i += 11
		case value >= 0xdc00 && value <= 0xdfff:
			return true
		default:
			i += 5
		}
	}
	return false
}

// AuditFiniteJSON reports whether every number in the response is finite:
// DRF's strict JSONRenderer raises on inf.
func AuditFiniteJSON(value any) bool {
	switch v := value.(type) {
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			return true
		}
		number, _ := strconv.ParseFloat(string(v), 64)
		return !math.IsInf(number, 0) && !math.IsNaN(number)
	case domain.RequestObject:
		for _, field := range v {
			if !AuditFiniteJSON(field.Value) {
				return false
			}
		}
	case []any:
		for _, item := range v {
			if !AuditFiniteJSON(item) {
				return false
			}
		}
	}
	return true
}

func auditGet(object domain.RequestObject, key string, fallback any) any {
	if value, ok := object.Lookup(key); ok {
		return value
	}
	return fallback
}

// auditLoadText applies the formatters' "json.loads a str, return it on a
// JSONDecodeError" step; done reports that the str itself is the result.
func auditLoadText(value any) (any, bool, error) {
	text, ok := value.(string)
	if !ok {
		return value, false, nil
	}
	parsed, decoded, err := AuditJSONLoads(text)
	if err != nil || !decoded {
		return text, err == nil, err
	}
	return parsed, false, nil
}

// format_additional_fields; nil is None.
func auditFormatAdditionalFields(value any) (*string, error) {
	if !PythonTruthy(value) {
		return nil, nil
	}
	value, done, err := auditLoadText(value)
	if err != nil || done {
		return auditPtr(value), err
	}
	list, ok := value.([]any)
	if !ok {
		return auditPtr(PythonStr(value)), nil
	}
	parts := []string{}
	for _, field := range list {
		object, ok := field.(domain.RequestObject)
		if !ok {
			continue
		}
		props, ok := auditGet(object, "props", domain.RequestObject{}).(domain.RequestObject)
		if !ok {
			return nil, ErrAuditUnhandled // props.get on a non-dict
		}
		label, fieldValue := auditGet(props, "label", "Campo"), auditGet(props, "value", "")
		if PythonTruthy(fieldValue) {
			parts = append(parts, PythonStr(label)+": "+PythonStr(fieldValue))
		}
	}
	if len(parts) == 0 {
		return nil, nil
	}
	return auditPtr(strings.Join(parts, ", ")), nil
}

// format_attendees (also expected_absences).
func auditFormatAttendees(value any, resolver AuditResolver) (*string, error) {
	if !PythonTruthy(value) {
		return nil, nil
	}
	value, done, err := auditLoadText(value)
	if err != nil || done {
		return auditPtr(value), err
	}
	list, ok := value.([]any)
	if !ok {
		return auditPtr(PythonStr(value)), nil
	}
	if len(list) == 0 {
		return auditPtr("Nessun presente"), nil
	}
	if len(list) <= 3 {
		names := []string{}
		for _, attendee := range list {
			object, ok := attendee.(domain.RequestObject)
			if !ok {
				continue
			}
			id := auditGet(object, "course_subscription_id", nil)
			if !PythonTruthy(id) {
				continue
			}
			var resolved any
			if AuditIsUUID(id) {
				if resolved, err = resolver.ResolveUUID(id.(string), "course_subscription_id"); err != nil {
					return nil, err
				}
			}
			text, isText := resolved.(string)
			if PythonTruthy(resolved) && isText && !strings.HasPrefix(text, "[Eliminato") {
				name, _, _ := strings.Cut(text, " - ")
				names = append(names, name)
			} else {
				names = append(names, "Atleta")
			}
		}
		if len(names) > 0 {
			return auditPtr(strings.Join(names, ", ")), nil
		}
	}
	return auditPtr(fmt.Sprintf("%d presenti", len(list))), nil
}

// format_events.
func auditFormatEvents(value any) (*string, error) {
	if !PythonTruthy(value) {
		return nil, nil
	}
	value, done, err := auditLoadText(value)
	if err != nil || done {
		return auditPtr(value), err
	}
	list, ok := value.([]any)
	if !ok {
		return auditPtr(PythonStr(value)), nil
	}
	if len(list) == 0 {
		return auditPtr("Nessun evento"), nil
	}
	if len(list) <= 2 {
		parts := []any{}
		for _, event := range list {
			object, ok := event.(domain.RequestObject)
			if !ok {
				continue
			}
			title, start := auditGet(object, "title", "Evento"), auditGet(object, "start", "")
			if !PythonTruthy(start) {
				parts = append(parts, title)
				continue
			}
			moment, err := AuditFromISOFormat(start)
			switch {
			case errors.Is(err, errAuditISOValue):
				parts = append(parts, title)
			case err != nil:
				return nil, err
			default:
				parts = append(parts, PythonStr(title)+" ("+moment.Date+")")
			}
		}
		if len(parts) > 0 {
			return auditJoin(parts)
		}
	}
	return auditPtr(fmt.Sprintf("%d eventi", len(list))), nil
}

// ", ".join(items): a non-str item is a TypeError.
func auditJoin(items []any) (*string, error) {
	texts := make([]string, len(items))
	for i, item := range items {
		text, ok := item.(string)
		if !ok {
			return nil, ErrAuditUnhandled
		}
		texts[i] = text
	}
	return auditPtr(strings.Join(texts, ", ")), nil
}

// format_collaborator_permissions (never None).
func auditFormatPermissions(value any) (*string, error) {
	if !PythonTruthy(value) {
		return auditPtr("Nessun permesso"), nil
	}
	value, done, err := auditLoadText(value)
	if err != nil || done {
		return auditPtr(value), err
	}
	list, ok := value.([]any)
	if !ok {
		return auditPtr(PythonStr(value)), nil
	}
	if len(list) == 0 {
		return auditPtr("Nessun permesso"), nil
	}
	if len(list) > 3 {
		return auditPtr(fmt.Sprintf("%d permessi", len(list))), nil
	}
	labels := []string{}
	for _, item := range list {
		text, ok := item.(string)
		if !ok {
			return nil, ErrAuditUnhandled // unhashable (TypeError) or no .split (AttributeError)
		}
		label, known := auditPermissionLabels[text]
		if !known {
			parts := strings.Split(text, ".")
			if label, err = auditCapitalize(parts[len(parts)-1]); err != nil {
				return nil, err
			}
		}
		labels = append(labels, label)
	}
	return auditPtr(strings.Join(labels, ", ")), nil
}

// auditCapitalize is str.capitalize() for ASCII text (others are refused).
func auditCapitalize(text string) (string, error) {
	if !auditASCII(text) {
		return "", ErrAuditNotPorted
	}
	if text == "" {
		return "", nil
	}
	return strings.ToUpper(text[:1]) + strings.ToLower(text[1:]), nil
}

func auditPtr(value any) *string {
	text, ok := value.(string)
	if !ok {
		return nil
	}
	return &text
}

// format_boolean.
func auditFormatBoolean(value any) string {
	if value == nil || value == "None" {
		return "N/D"
	}
	if text, ok := value.(string); ok {
		switch auditPythonLower(text) {
		case "true", "1", "yes":
			return "Sì"
		}
		return "No"
	}
	if PythonTruthy(value) {
		return "Sì"
	}
	return "No"
}

// auditPythonLower is str.lower() where it matters for equality with ASCII
// text: U+0130 lowercases to "i̇" (two code points) in Python.
func auditPythonLower(text string) string {
	return strings.ToLower(strings.ReplaceAll(text, "İ", "i̇"))
}

// IsAuditISOValueError reports fromisoformat's ValueError.
func IsAuditISOValueError(err error) bool { return errors.Is(err, errAuditISOValue) }

// PythonStripText is str.strip().
func PythonStripText(text string) string { return pythonStrip(text) }

// AuditPythonLower is str.lower() for comparisons against ASCII names.
func AuditPythonLower(text string) string { return auditPythonLower(text) }

// AuditEnrich is enrich_value(value, field).
func AuditEnrich(value any, field string, resolver AuditResolver) (any, error) {
	if field != "" {
		var formatted *string
		var err error
		switch field {
		case "additional_fields":
			formatted, err = auditFormatAdditionalFields(value)
		case "attendees", "expected_absences":
			formatted, err = auditFormatAttendees(value, resolver)
		case "events":
			formatted, err = auditFormatEvents(value)
		case "collaborator_permissions":
			formatted, err = auditFormatPermissions(value)
		}
		if err != nil {
			return nil, err
		}
		if formatted != nil {
			return *formatted, nil
		}
		if auditBooleanFields[field] {
			return auditFormatBoolean(value), nil
		}
	}
	switch v := value.(type) {
	case string:
		if strings.HasPrefix(v, "[") || strings.HasPrefix(v, "{") {
			parsed, decoded, err := AuditJSONLoads(v)
			if err != nil {
				return nil, err
			}
			if decoded {
				return AuditEnrich(parsed, field, resolver)
			}
		}
		if AuditIsUUID(v) && field != "" {
			resolved, err := resolver.ResolveUUID(v, field)
			if err != nil {
				return nil, err
			}
			if PythonTruthy(resolved) {
				return resolved, nil
			}
		}
		return v, nil
	case bool:
		return auditFormatBoolean(v), nil
	case []any:
		if len(v) == 0 {
			return "(vuoto)", nil
		}
		items := make([]any, len(v))
		for i, item := range v {
			enriched, err := AuditEnrich(item, field, resolver)
			if err != nil {
				return nil, err
			}
			items[i] = enriched
		}
		return items, nil
	case domain.RequestObject:
		object := make(domain.RequestObject, len(v))
		for i, entry := range v {
			enriched, err := AuditEnrich(entry.Value, entry.Name, resolver)
			if err != nil {
				return nil, err
			}
			object[i] = domain.RequestField{Name: entry.Name, Value: enriched}
		}
		return object, nil
	}
	return value, nil
}

// AuditEnrichChanges is enrich_changes (called only for truthy changes).
func AuditEnrichChanges(changes any, resolver AuditResolver) (any, error) {
	object, ok := changes.(domain.RequestObject)
	if !ok || len(object) == 0 {
		return changes, nil
	}
	enriched := make(domain.RequestObject, len(object))
	for i, entry := range object {
		var value any
		var err error
		if pair, ok := entry.Value.([]any); ok && len(pair) == 2 {
			var old, updated any
			if old, err = AuditEnrich(pair[0], entry.Name, resolver); err != nil {
				return nil, err
			}
			if updated, err = AuditEnrich(pair[1], entry.Name, resolver); err != nil {
				return nil, err
			}
			value = []any{old, updated}
		} else if value, err = AuditEnrich(entry.Value, entry.Name, resolver); err != nil {
			return nil, err
		}
		enriched[i] = domain.RequestField{Name: entry.Name, Value: value}
	}
	return enriched, nil
}

// AuditDisplayChanges is get_display_changes (called only for truthy changes).
func AuditDisplayChanges(changes any, resolver AuditResolver) ([]any, error) {
	object, _ := changes.(domain.RequestObject)
	rows := []any{}
	for _, entry := range object {
		label, err := auditFieldLabel(entry.Name)
		if err != nil {
			return nil, err
		}
		if pair, ok := entry.Value.([]any); ok && len(pair) == 2 {
			old, err := AuditEnrich(pair[0], entry.Name, resolver)
			if err != nil {
				return nil, err
			}
			updated, err := AuditEnrich(pair[1], entry.Name, resolver)
			if err != nil {
				return nil, err
			}
			oldText, newText := auditDisplayValue(old), auditDisplayValue(updated)
			rows = append(rows, domain.RequestObject{{Name: "field", Value: label}, {Name: "field_key", Value: entry.Name},
				{Name: "old", Value: oldText}, {Name: "new", Value: newText},
				{Name: "formatted", Value: label + ": " + oldText + " → " + newText}})
			continue
		}
		enriched, err := AuditEnrich(entry.Value, entry.Name, resolver)
		if err != nil {
			return nil, err
		}
		text := auditDisplayValue(enriched)
		rows = append(rows, domain.RequestObject{{Name: "field", Value: label}, {Name: "field_key", Value: entry.Name},
			{Name: "value", Value: text}, {Name: "formatted", Value: label + ": " + text}})
	}
	return rows, nil
}

// _format_display_value.
func auditDisplayValue(value any) string {
	switch v := value.(type) {
	case nil:
		return "N/D"
	case string:
		switch v {
		case "None":
			return "N/D"
		case "", `""`:
			return "(vuoto)"
		}
		return v
	case bool:
		if v {
			return "Sì"
		}
		return "No"
	case []any:
		if len(v) == 0 {
			return "(vuoto)"
		}
		texts := make([]string, len(v))
		for i, item := range v {
			text, ok := item.(string)
			if !ok {
				return fmt.Sprintf("%d elementi", len(v))
			}
			texts[i] = text
		}
		if len(v) <= 5 {
			return strings.Join(texts, ", ")
		}
		return fmt.Sprintf("%d elementi", len(v))
	case domain.RequestObject:
		label, hasLabel := v.Lookup("label")
		text, hasValue := v.Lookup("value")
		if hasLabel && hasValue {
			return PythonStr(label) + ": " + PythonStr(text)
		}
		return fmt.Sprintf("%d campi", len(v))
	}
	return PythonStr(value)
}

// AuditChangesSummary is get_changes_summary (nil for None).
func AuditChangesSummary(action int64, changes any) *string {
	if !PythonTruthy(changes) {
		return nil
	}
	var text string
	switch action {
	case 0:
		text = "Record creato"
	case 2:
		text = "Record eliminato"
	case 1:
		object, _ := changes.(domain.RequestObject)
		switch {
		case len(object) == 0:
			text = "Nessuna modifica"
		case len(object) <= 3:
			names := make([]string, len(object))
			for i, entry := range object {
				names[i] = entry.Name
			}
			text = "Modificato: " + strings.Join(names, ", ")
		default:
			text = fmt.Sprintf("Modificati %d campi", len(object))
		}
	default:
		return nil
	}
	return &text
}

// AuditMoment is a datetime the serializer formats with strftime.
type AuditMoment struct{ Date, DateTime string }

var errAuditISOValue = errors.New("fromisoformat ValueError")

// AuditFromISOFormat is datetime.fromisoformat(start.replace('Z', '+00:00'))
// for a str start (another type has no .replace: AttributeError), following
// CPython 3.13's parser (basic and extended date forms, ISO week dates, any
// one-character separator, fractions, UTC offsets with seconds). Anything
// Python rejects is errAuditISOValue (ValueError). Years below 1000 are the
// one refusal: strftime('%Y') pads on BSD libc and not on glibc.
func AuditFromISOFormat(value any) (AuditMoment, error) {
	text, ok := value.(string)
	if !ok {
		return AuditMoment{}, ErrAuditUnhandled
	}
	moment, valid := auditParseISO(strings.ReplaceAll(text, "Z", "+00:00"))
	if !valid {
		return AuditMoment{}, errAuditISOValue
	}
	if moment.year < 1000 {
		return AuditMoment{}, ErrAuditNotPorted
	}
	date := fmt.Sprintf("%02d/%02d/%d", moment.day, moment.month, moment.year)
	return AuditMoment{Date: date, DateTime: fmt.Sprintf("%s %02d:%02d", date, moment.hour, moment.minute)}, nil
}

type auditISOMoment struct{ year, month, day, hour, minute int }

func auditAllDigits(text string) bool {
	if text == "" {
		return false
	}
	for i := 0; i < len(text); i++ {
		if text[i] < '0' || text[i] > '9' {
			return false
		}
	}
	return true
}

// auditISOComponent is _read_isoformat_component: exactly n ASCII digits.
func auditISOComponent(text string, n int) (int, bool) {
	if len(text) != n || !auditAllDigits(text) {
		return 0, false
	}
	value, _ := strconv.Atoi(text)
	return value, true
}

func auditISOSlice(text string, from, to int) string {
	if from > len(text) {
		from = len(text)
	}
	if to > len(text) {
		to = len(text)
	}
	return text[from:to]
}

// auditISOSeparator is _find_isoformat_datetime_separator for len(text) >= 7.
func auditISOSeparator(text string) (int, bool) {
	size := len(text)
	if size == 7 {
		return 7, true
	}
	switch {
	case text[4] == '-' && text[5] == 'W':
		if size > 8 && text[8] == '-' {
			if size == 9 {
				return 0, false
			}
			if size > 10 && text[10] >= '0' && text[10] <= '9' {
				return 8, true
			}
			return 10, true
		}
		return 8, true
	case text[4] == '-':
		return 10, true
	case text[4] == 'W':
		index := 7
		for index < size && text[index] >= '0' && text[index] <= '9' {
			index++
		}
		if index < 9 {
			return index, true
		}
		if index%2 == 0 {
			return 7, true
		}
		return 8, true
	}
	return 8, true
}

func auditISOLeap(year int) bool { return year%4 == 0 && (year%100 != 0 || year%400 == 0) }

func auditParseISO(text string) (auditISOMoment, bool) {
	var out auditISOMoment
	if len(text) < 7 {
		return out, false
	}
	split, ok := auditISOSeparator(text)
	if !ok {
		return out, false
	}
	if split > len(text) {
		return out, false
	}
	date, clock := text[:split], auditISOSlice(text, split+1, len(text))
	dateOnly := split >= len(text)
	if len(date) != 7 && len(date) != 8 && len(date) != 10 {
		return out, false
	}
	for i := 0; i < len(text); i++ {
		if text[i] >= 0x80 {
			return out, false
		}
	}
	out, ok = auditParseISODate(date)
	if !ok {
		return out, false
	}
	if dateOnly {
		return out, true
	}
	return auditParseISOTime(clock, out)
}

// auditISOByte reads text[i], with the buffer's terminating NUL past the end.
func auditISOByte(text string, i int) byte {
	if i < len(text) {
		return text[i]
	}
	return 0
}

// auditISODigits is parse_digits: exactly n ASCII digits at pos.
func auditISODigits(text string, pos, n int) (int, int, bool) {
	value := 0
	for i := 0; i < n; i++ {
		c := auditISOByte(text, pos+i)
		if c < '0' || c > '9' {
			return 0, pos, false
		}
		value = value*10 + int(c-'0')
	}
	return value, pos + n, true
}

// auditISOClock is the C parse_hh_mm_ss_ff over text[from:end] (the buffer
// continues past end, which the C reader relies on). more is its return code 1:
// something other than the NUL follows the parsed part.
func auditISOClock(text string, from, end int) (parts [4]int, more, ok bool) {
	pos, hasSep := from, true
	for i := 0; i < 3; i++ {
		value, next, valid := auditISODigits(text, pos, 2)
		if !valid {
			return parts, false, false
		}
		parts[i], pos = value, next
		c := auditISOByte(text, pos)
		pos++
		if i == 0 {
			hasSep = c == ':'
		}
		switch {
		case c == '.' || c == ',':
			i = 3
		case pos >= end:
			return parts, c != 0, true
		case hasSep && c == ':':
			continue
		case !hasSep:
			pos--
		default:
			return parts, false, false
		}
	}
	remains := end - pos
	take := remains
	if take >= 6 {
		take = 6
	}
	if take < 1 {
		return parts, false, false
	}
	value, next, valid := auditISODigits(text, pos, take)
	if !valid {
		return parts, false, false
	}
	for i := take; i < 6; i++ {
		value *= 10
	}
	parts[3], pos = value, next
	for auditISOByte(text, pos) >= '0' && auditISOByte(text, pos) <= '9' {
		pos++
	}
	return parts, auditISOByte(text, pos) != 0, true
}

// auditParseISOTime is the C parse_isoformat_time plus the datetime range checks.
func auditParseISOTime(text string, date auditISOMoment) (auditISOMoment, bool) {
	if len(text) < 2 {
		return date, false
	}
	zone := 0
	for zone < len(text) && text[zone] != 'Z' && text[zone] != '+' && text[zone] != '-' {
		zone++
	}
	parts, more, ok := auditISOClock(text, 0, zone)
	if !ok {
		return date, false
	}
	if zone == len(text) {
		if more {
			return date, false
		}
	} else if text[zone] == 'Z' {
		if zone+1 != len(text) {
			return date, false
		}
	} else {
		tz, tzMore, tzOK := auditISOClock(text, zone+1, len(text))
		if !tzOK || tzMore {
			return date, false
		}
		micro := ((tz[0]*60+tz[1])*60+tz[2])*1000000 + tz[3]
		if micro >= 24*3600*1000000 {
			return date, false
		}
	}
	if parts[0] > 23 || parts[1] > 59 || parts[2] > 59 {
		return date, false
	}
	date.hour, date.minute = parts[0], parts[1]
	return date, true
}

// auditParseISODate is the C parse_isoformat_date over a 7, 8 or 10 byte
// ASCII date: YYYY-MM-DD, YYYYMMDD, YYYY-Www[-D] and YYYYWww[D].
func auditParseISODate(date string) (auditISOMoment, bool) {
	var out auditISOMoment
	if len(date) != 7 && len(date) != 8 && len(date) != 10 {
		return out, false
	}
	year, ok := auditISOComponent(date[:4], 4)
	if !ok {
		return out, false
	}
	hasSep := date[4] == '-'
	pos := 4
	if hasSep {
		pos = 5
	}
	if auditISOSlice(date, pos, pos+1) == "W" {
		pos++
		week, ok := auditISOComponent(auditISOSlice(date, pos, pos+2), 2)
		if !ok {
			return out, false
		}
		pos += 2
		weekday := 1
		if len(date) > pos {
			if (date[pos] == '-') != hasSep {
				return out, false
			}
			if hasSep {
				pos++
			}
			if weekday, ok = auditISOComponent(auditISOSlice(date, pos, pos+1), 1); !ok {
				return out, false
			}
		}
		if year < 1 || year > 9999 || weekday < 1 || weekday > 7 {
			return out, false
		}
		if week < 1 || week > 53 {
			return out, false
		}
		if week == 53 {
			first := (int(time.Date(year, 1, 1, 0, 0, 0, 0, time.UTC).Weekday()) + 6) % 7
			if !(first == 3 || (first == 2 && auditISOLeap(year))) {
				return out, false
			}
		}
		fourth := time.Date(year, 1, 4, 0, 0, 0, 0, time.UTC)
		monday := fourth.AddDate(0, 0, -((int(fourth.Weekday()) + 6) % 7))
		day := monday.AddDate(0, 0, (week-1)*7+weekday-1)
		if day.Year() < 1 || day.Year() > 9999 {
			return out, false
		}
		out.year, out.month, out.day = day.Year(), int(day.Month()), day.Day()
	} else {
		month, ok := auditISOComponent(auditISOSlice(date, pos, pos+2), 2)
		if !ok {
			return out, false
		}
		pos += 2
		if (auditISOSlice(date, pos, pos+1) == "-") != hasSep {
			return out, false
		}
		if hasSep {
			pos++
		}
		day, ok := auditISOComponent(auditISOSlice(date, pos, pos+2), 2)
		if !ok {
			return out, false
		}
		if year < 1 || month < 1 || month > 12 || day < 1 || day > auditDaysIn(year, month) {
			return out, false
		}
		out.year, out.month, out.day = year, month, day
	}
	return out, true
}

func auditDaysIn(year, month int) int {
	return time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day()
}

// AuditStoredDate is strftime('%d/%m/%Y') of a loaded datetime column given
// as its UTC year and 'DD/MM'; ok=false is a row Django cannot load (Python
// datetime years 1..9999), which the resolvers swallow.
func AuditStoredDate(year, dayMonth *string) (string, bool, error) {
	if year == nil || dayMonth == nil {
		return "", false, nil
	}
	value, err := strconv.ParseFloat(*year, 64)
	if err != nil || value < 1 || value > 9999 {
		return "", false, nil
	}
	if value < 1000 {
		return "", false, ErrAuditNotPorted
	}
	return *dayMonth + "/" + strconv.Itoa(int(value)), true, nil
}

// pythonDigit is the value of a Unicode decimal digit (category Nd).
func pythonDigit(r rune) (int, bool) {
	if r >= '0' && r <= '9' {
		return int(r - '0'), true
	}
	if !unicode.IsDigit(r) {
		return 0, false
	}
	for _, entry := range unicode.Digit.R16 {
		if uint32(r) >= uint32(entry.Lo) && uint32(r) <= uint32(entry.Hi) && (uint32(r)-uint32(entry.Lo))%uint32(entry.Stride) == 0 {
			return int(((uint32(r) - uint32(entry.Lo)) / uint32(entry.Stride)) % 10), true
		}
	}
	for _, entry := range unicode.Digit.R32 {
		if uint32(r) >= entry.Lo && uint32(r) <= entry.Hi && (uint32(r)-entry.Lo)%entry.Stride == 0 {
			return int(((uint32(r) - entry.Lo) / entry.Stride) % 10), true
		}
	}
	return 0, false
}

// PythonIntText is int(text) for a str: surrounding whitespace, a sign and
// Unicode decimal digits with single underscores between them.
func PythonIntText(text string) (*big.Int, bool) {
	text = pythonStrip(text)
	negative := false
	if text != "" && (text[0] == '+' || text[0] == '-') {
		negative = text[0] == '-'
		text = text[1:]
	}
	var digits strings.Builder
	previousDigit := false
	for _, r := range text {
		if r == '_' {
			if !previousDigit {
				return nil, false
			}
			previousDigit = false
			continue
		}
		digit, ok := pythonDigit(r)
		if !ok {
			return nil, false
		}
		digits.WriteByte(byte('0' + digit))
		previousDigit = true
	}
	if digits.Len() == 0 || !previousDigit {
		return nil, false
	}
	value, _ := new(big.Int).SetString(digits.String(), 10)
	if negative {
		value.Neg(value)
	}
	return value, true
}

// PythonStrptimeDate is datetime.strptime(text, '%Y-%m-%d') (UTC).
func PythonStrptimeDate(text string) (time.Time, bool) {
	runes := []rune(text)
	at := 0
	digit := func() (int, bool) {
		if at < len(runes) {
			if value, ok := pythonDigit(runes[at]); ok {
				at++
				return value, true
			}
		}
		return 0, false
	}
	year := 0
	for i := 0; i < 4; i++ {
		value, ok := digit()
		if !ok {
			return time.Time{}, false
		}
		year = year*10 + value
	}
	ascii := func(i int, low, high rune) bool { return i < len(runes) && runes[i] >= low && runes[i] <= high }
	if !ascii(at, '-', '-') {
		return time.Time{}, false
	}
	at++
	// %m: 1[0-2]|0[1-9]|[1-9], followed by '-'.
	month := 0
	switch {
	case ascii(at, '1', '1') && ascii(at+1, '0', '2') && ascii(at+2, '-', '-'):
		month, at = 10+int(runes[at+1]-'0'), at+2
	case ascii(at, '0', '0') && ascii(at+1, '1', '9') && ascii(at+2, '-', '-'):
		month, at = int(runes[at+1]-'0'), at+2
	case ascii(at, '1', '9') && ascii(at+1, '-', '-'):
		month, at = int(runes[at]-'0'), at+1
	default:
		return time.Time{}, false
	}
	at++
	// %d: 3[01]|[12]\d|0[1-9]|[1-9]| [1-9]; the first alternative that matches wins.
	day := 0
	if ascii(at, '3', '3') && ascii(at+1, '0', '1') {
		day, at = 30+int(runes[at+1]-'0'), at+2
	} else if ascii(at, '1', '2') && at+1 < len(runes) {
		if value, ok := pythonDigit(runes[at+1]); ok {
			day, at = 10*int(runes[at]-'0')+value, at+2
		}
	}
	if day == 0 {
		switch {
		case ascii(at, '0', '0') && ascii(at+1, '1', '9'):
			day, at = int(runes[at+1]-'0'), at+2
		case ascii(at, '1', '9'):
			day, at = int(runes[at]-'0'), at+1
		case ascii(at, ' ', ' ') && ascii(at+1, '1', '9'):
			day, at = int(runes[at+1]-'0'), at+2
		default:
			return time.Time{}, false
		}
	}
	if at != len(runes) || year < 1 || day > auditDaysIn(year, month) {
		return time.Time{}, false
	}
	return time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC), true
}

// PythonParseDate is django.utils.dateparse.parse_date for a str: date.fromisoformat
// (basic and ISO week forms), then date_re (\d{4}-\d{1,2}-\d{1,2}$ with Unicode
// digits, and $ also before a final newline). It returns the date as YYYY-MM-DD;
// ok=false covers both "not well formatted" (None) and "well formatted but not a
// date" (ValueError): the model field rejects either.
func PythonParseDate(text string) (string, bool) {
	if moment, ok := auditParseISODate(text); ok {
		return fmt.Sprintf("%04d-%02d-%02d", moment.year, moment.month, moment.day), true
	}
	text = strings.TrimSuffix(text, "\n")
	parts := strings.Split(text, "-")
	if len(parts) != 3 {
		return "", false
	}
	var values [3]int
	for index, part := range parts {
		runes := []rune(part)
		if len(runes) == 0 || (index == 0 && len(runes) != 4) || (index > 0 && len(runes) > 2) {
			return "", false
		}
		for _, r := range runes {
			digit, ok := pythonDigit(r)
			if !ok {
				return "", false
			}
			values[index] = values[index]*10 + digit
		}
	}
	if values[0] < 1 || values[1] < 1 || values[1] > 12 || values[2] < 1 || values[2] > auditDaysIn(values[0], values[1]) {
		return "", false
	}
	return fmt.Sprintf("%04d-%02d-%02d", values[0], values[1], values[2]), true
}
