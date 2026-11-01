package service

import (
	"context"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// auditResolver holds one request's resolver context: the X-Group-Id the
// group-aware managers read (a malformed header is ignored) and the events
// JSON the event resolver scans (identical within a read-only request).
type auditResolver struct {
	ctx   context.Context
	store domain.AuditLogStore
	group *string
	// events[0] are the AttendanceRegistry lists, events[1] the
	// GlobalCalendarEvents lists, each loaded on first use.
	events [2][]any
	loaded [2]bool
}

func (s *AuditLogs) resolver(ctx context.Context, groupHeader string) *auditResolver {
	return &auditResolver{ctx: ctx, store: s.store, group: supplierGroup(groupHeader)}
}

// auditFailed is a resolver formatter's swallowed exception (AttributeError on
// a missing relation or attribute, or a row Django cannot load).
type auditFailed struct{}

type auditFormat func(row domain.AuditObject) (any, error)

func auditText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func auditOr(value *string, fallback string) string {
	if value == nil || *value == "" {
		return fallback
	}
	return *value
}

func auditShort(id *string) string {
	runes := []rune(auditText(id))
	if len(runes) > 8 {
		runes = runes[:8]
	}
	return string(runes)
}

func auditFail(domain.AuditObject) (any, error) { return auditFailed{}, nil }

func auditTitleOf(row domain.AuditObject) (any, error) { return auditOptional(row["title"]), nil }

func auditFullName(row domain.AuditObject) string {
	return auditText(row["first_name"]) + " " + auditText(row["last_name"])
}

// auditDated formats a resolver datetime column; an unloadable year fails.
func auditDated(row domain.AuditObject, name string, format func(date string) string) (any, error) {
	date, ok, err := dto.AuditStoredDate(row[name+"_year"], row[name+"_daymonth"])
	if err != nil || !ok {
		return auditFailed{}, err
	}
	return format(date), nil
}

// auditDescriptions is get_model_description's MODEL_DESCRIPTION_RESOLVERS by
// content type model. Kinds whose formatter always raises (invoice_number,
// associate and name attributes the models lack) describe nothing.
var auditDescriptions = map[string]auditFormat{
	"subscription": func(row domain.AuditObject) (any, error) {
		if row["associate"] == nil {
			return "Iscrizione #" + auditShort(row["id"]), nil
		}
		return "Iscrizione - " + auditFullName(row), nil
	},
	"coursesubscription": func(row domain.AuditObject) (any, error) {
		if row["subscription"] == nil || row["associate"] == nil {
			return "Iscrizione Corso #" + auditShort(row["id"]), nil
		}
		return auditFullName(row) + " - " + auditText(row["title"]), nil
	},
	"course":    func(row domain.AuditObject) (any, error) { return "Corso - " + auditText(row["title"]), nil },
	"associate": func(row domain.AuditObject) (any, error) { return auditFullName(row), nil },
	"payment": func(row domain.AuditObject) (any, error) {
		return "Pagamento - " + auditOr(row["description"], "N/D") + " (€" + auditText(row["amount"]) + ")", nil
	},
	"invoice":    auditFail,
	"instructor": func(row domain.AuditObject) (any, error) { return "Istruttore - " + auditFullName(row), nil },
	"attendanceday": func(row domain.AuditObject) (any, error) {
		return auditDated(row, "date", func(date string) string { return "Presenze - " + auditOr(row["title"], "Giornata") + " (" + date + ")" })
	},
	"attendanceregistry": func(row domain.AuditObject) (any, error) {
		return "Registro Presenze - " + auditText(row["title"]), nil
	},
	"medicalcertificate": func(row domain.AuditObject) (any, error) { return "Certificato #" + auditShort(row["id"]), nil },
	"carnet":             func(row domain.AuditObject) (any, error) { return "Carnet - " + auditText(row["title"]), nil },
	"carnetsubscription": auditFail,
	"subscriptionmembership": func(row domain.AuditObject) (any, error) {
		if row["subscription"] == nil || row["associate"] == nil {
			return "Tesseramento", nil
		}
		return "Tesseramento - " + auditFullName(row), nil
	},
	"group":  func(row domain.AuditObject) (any, error) { return "Gruppo - " + auditText(row["name"]), nil },
	"family": auditFail,
	"tags":   auditFail,
	"user": func(row domain.AuditObject) (any, error) {
		if auditOr(row["first_name"], "") != "" {
			return "Utente - " + auditFullName(row), nil
		}
		return "Utente - " + auditText(row["email"]), nil
	},
	"sportassociation": func(row domain.AuditObject) (any, error) {
		if auditOr(row["denomination"], "") != "" {
			return "Associazione - " + *row["denomination"], nil
		}
		return "Associazione #" + auditShort(row["id"]), nil
	},
	"module":           func(row domain.AuditObject) (any, error) { return "Modulo - " + auditText(row["title"]), nil },
	"campsandretreats": func(row domain.AuditObject) (any, error) { return "Campo - " + auditText(row["title"]), nil },
	"campsandretreatsperiod": func(row domain.AuditObject) (any, error) {
		return auditDated(row, "start", func(date string) string { return "Periodo - " + auditText(row["title"]) + " (" + date + ")" })
	},
	"campsandretreatssubscription": auditFail,
	"reminders":                    func(row domain.AuditObject) (any, error) { return "Promemoria - " + auditText(row["event_title"]), nil },
	"globalcalendarevents":         func(row domain.AuditObject) (any, error) { return "Calendario #" + auditShort(row["id"]), nil },
	"message": func(row domain.AuditObject) (any, error) {
		if auditOr(row["subject"], "") != "" {
			return "Messaggio - " + *row["subject"], nil
		}
		return "Messaggio " + auditText(row["type"]), nil
	},
	"automationworkflow":         func(row domain.AuditObject) (any, error) { return "Automazione - " + auditText(row["name"]), nil },
	"communicationconfiguration": func(domain.AuditObject) (any, error) { return "Configurazione Email", nil },
}

type auditFieldResolver struct {
	kind   string
	format auditFormat
}

func auditSubscriber(suffix func(row domain.AuditObject) string) auditFormat {
	return func(row domain.AuditObject) (any, error) {
		if row["subscription"] == nil || row["associate"] == nil {
			return auditFailed{}, nil
		}
		return auditFullName(row) + " - " + suffix(row), nil
	}
}

// auditFields is resolve_uuid's field_resolvers by field name.
var auditFields = func() map[string]auditFieldResolver {
	courseSubscription := auditFieldResolver{"coursesubscription", auditSubscriber(func(row domain.AuditObject) string { return auditText(row["title"]) })}
	associate := auditFieldResolver{"associate", func(row domain.AuditObject) (any, error) { return auditFullName(row), nil }}
	course := auditFieldResolver{"course", auditTitleOf}
	subscription := auditFieldResolver{"subscription", func(row domain.AuditObject) (any, error) {
		if row["associate"] == nil {
			return auditFailed{}, nil
		}
		return auditFullName(row) + " - " + auditOr(row["subscription_type"], "Iscrizione"), nil
	}}
	payment := auditFieldResolver{"payment", func(row domain.AuditObject) (any, error) {
		return auditOr(row["description"], "Pagamento") + " - €" + auditText(row["amount"]), nil
	}}
	instructor := auditFieldResolver{"instructor", func(row domain.AuditObject) (any, error) { return auditFullName(row), nil }}
	carnet := auditFieldResolver{"carnet", auditTitleOf}
	registry := auditFieldResolver{"attendanceregistry", func(row domain.AuditObject) (any, error) {
		return "Registro - " + auditText(row["title"]), nil
	}}
	day := auditFieldResolver{"attendanceday", func(row domain.AuditObject) (any, error) {
		return auditDated(row, "date", func(date string) string { return auditOr(row["title"], "Giornata") + " - " + date })
	}}
	group := auditFieldResolver{"group", func(row domain.AuditObject) (any, error) { return auditOptional(row["name"]), nil }}
	family := auditFieldResolver{"family", auditFail}
	tag := auditFieldResolver{"tags", auditFail}
	membership := auditFieldResolver{"subscriptionmembership", auditSubscriber(func(domain.AuditObject) string { return "Tesseramento" })}
	camp := auditFieldResolver{"campsandretreats", auditTitleOf}
	return map[string]auditFieldResolver{
		"course_subscription_id": courseSubscription, "course_subscription": courseSubscription,
		"associate_id": associate, "associate": associate, "course_id": course, "course": course,
		"subscription_id": subscription, "subscription": subscription, "payment_id": payment, "payment": payment,
		"instructor_id": instructor, "instructor": instructor, "carnet_id": carnet, "carnet": carnet,
		"carnet_subscription_id": {"carnetsubscription", auditFail}, "medical_certificate_id": {"medicalcertificate", auditFail},
		"invoice_id": {"invoice", auditFail}, "module_id": {"module", auditTitleOf},
		"attendance_registry": registry, "attendance_registry_id": registry, "attendance_day": day, "attendance_day_id": day,
		"group_id": group, "group": group, "family_id": family, "family": family, "tag_id": tag, "tag": tag,
		"subscription_membership_id": membership, "subscription_membership": membership,
		"camps_and_retreats_id": camp, "camps_and_retreats": camp,
		"camps_and_retreats_period_id": {"campsandretreatsperiod", func(row domain.AuditObject) (any, error) {
			return auditDated(row, "start", func(date string) string { return auditText(row["title"]) + " - " + date })
		}},
		"camps_and_retreats_subscription_id": {"campsandretreatssubscription", auditFail},
		"reminders_id": {"reminders", func(row domain.AuditObject) (any, error) {
			return auditDated(row, "send", func(date string) string { return auditText(row["event_title"]) + " - " + date })
		}},
		"global_calendar_id": {"globalcalendarevents", func(row domain.AuditObject) (any, error) {
			return "Calendario #" + auditShort(row["id"]), nil
		}},
	}
}()

// describe is get_object_description.
func (r *auditResolver) describe(row domain.AuditLogRow, verbose string) (any, error) {
	if format, ok := auditDescriptions[row.Model]; ok && row.ObjectPK != "" {
		description, _, err := r.lookup(row.Model, row.ObjectPK, format)
		if err != nil {
			return nil, err
		}
		if text, ok := description.(string); ok && text != "" {
			return text, nil
		}
	}
	if row.ObjectRepr != "" && strings.Contains(row.ObjectRepr, " object (") {
		return verbose + " #" + auditShort(&row.ObjectPK), nil
	}
	if row.ObjectRepr != "" {
		return row.ObjectRepr, nil
	}
	return verbose + " #" + row.ObjectPK, nil
}

// lookup is model_class.objects.select_related().get(pk=value) then the
// formatter: found=false is DoesNotExist, auditFailed{} a swallowed exception.
func (r *auditResolver) lookup(kind, value string, format auditFormat) (any, bool, error) {
	id, err := paymentInfoParseUUID(value)
	if err != nil {
		return auditFailed{}, true, nil // the UUID field's ValidationError
	}
	object, err := r.store.AuditObject(r.ctx, kind, id.String(), r.group)
	if err != nil || object == nil {
		return nil, false, err
	}
	result, err := format(object)
	return result, true, err
}

// ResolveUUID is resolve_uuid for a value is_uuid accepted.
func (r *auditResolver) ResolveUUID(value, field string) (any, error) {
	if field == "associated_event" || field == "event_id" {
		result, err := r.event(value)
		if err != nil || dto.PythonTruthy(result) {
			return result, err
		}
		return nil, nil
	}
	resolver, ok := auditFields[field]
	if !ok {
		return nil, nil
	}
	result, found, err := r.lookup(resolver.kind, value, resolver.format)
	switch {
	case err != nil:
		return nil, err
	case !found:
		return "[Eliminato: " + auditShort(&value) + "...]", nil
	}
	if _, failed := result.(auditFailed); failed {
		return nil, nil
	}
	return result, nil
}

// event is _resolve_event_from_json: the first AttendanceRegistry event, else
// the first GlobalCalendarEvents event, whose event_id equals the value. Each
// queryset loads (json.loads) all of its rows before the scan.
func (r *auditResolver) event(id string) (any, error) {
	for source := range r.events {
		if !r.loaded[source] {
			registries, calendars, err := r.store.EventLists(r.ctx, source == 0, source == 1)
			if err != nil {
				return nil, err
			}
			for _, text := range append(registries, calendars...) {
				value, err := dto.AuditStoredJSON(&text)
				if err != nil {
					return nil, err
				}
				r.events[source] = append(r.events[source], value)
			}
			r.loaded[source] = true
		}
		for _, events := range r.events[source] {
			list, _ := events.([]any)
			if !dto.PythonTruthy(events) {
				continue
			}
			for _, event := range list {
				object, ok := event.(domain.RequestObject)
				if !ok {
					continue
				}
				if eventID, _ := object.Lookup("event_id"); eventID != any(id) {
					continue
				}
				title, present := object.Lookup("title")
				if !present {
					title = "Evento"
				}
				start, present := object.Lookup("start")
				if !present {
					start = ""
				}
				if dto.PythonTruthy(start) {
					moment, err := dto.AuditFromISOFormat(start)
					if err == nil {
						return dto.PythonStr(title) + " - " + moment.DateTime, nil
					}
					if !dto.IsAuditISOValueError(err) {
						return nil, err
					}
				}
				return title, nil
			}
		}
	}
	return nil, nil
}
