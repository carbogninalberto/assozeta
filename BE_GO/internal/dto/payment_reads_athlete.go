package dto

import (
	"encoding/json"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PaymentAthleteOptional renders a nullable text column.
func PaymentAthleteOptional(value *string) any { return paymentReadsOptional(value) }

// paymentAthleteMeta is PaymentCompressedSerializer.get_meta.
func paymentAthleteMeta(row domain.PaymentAthleteRow, meta domain.PaymentAthleteMeta) map[string]any {
	installment := map[string]any{"id": nil, "payment_date": nil}
	subscription := map[string]any{"subscription_id": nil, "start_date": nil, "end_date": nil}
	data := map[string]any{"type": "", "is_installment": false, "installment": installment, "conjunction": "",
		"description": paymentReadsOptional(row.Description), "notes": paymentReadsOptional(row.Notes), "name": nil,
		"subscription": subscription}
	if meta.Kind == domain.PaymentMetaNone {
		return data
	}
	data["name"] = paymentReadsOptional(meta.Name)
	data["subscription"] = map[string]any{"subscription_id": meta.SubscriptionID, "start_date": paymentReadsOptional(meta.Start),
		"end_date": paymentReadsOptional(meta.End)}
	switch meta.Kind {
	case domain.PaymentMetaInstallment:
		data["type"], data["conjunction"], data["is_installment"] = "rata", " del corso ", true
		if meta.Number != nil {
			installment["id"] = *meta.Number
		}
		if meta.Date != nil {
			// DRF's JSON encoder renders the datetime as isoformat() with 'Z'.
			installment["payment_date"] = DRFUTCDateTime(*meta.Date)
		}
	case domain.PaymentMetaSubscription:
		data["type"], data["conjunction"] = "Iscrizione", " all'associazione "
	case domain.PaymentMetaCarnet:
		data["type"], data["conjunction"] = "Assegnazione carnet", " con nome "
	case domain.PaymentMetaCourse:
		data["type"], data["conjunction"] = "Iscrizione ", " al corso "
	case domain.PaymentMetaCamp:
		data["type"], data["conjunction"] = "Iscrizione ", " al campo "
	}
	return data
}

// PaymentAthleteItem is PaymentCompressedSerializer(payment).data.
func PaymentAthleteItem(row domain.PaymentAthleteRow, meta domain.PaymentAthleteMeta) map[string]any {
	var associate any
	if row.AssociateID != nil {
		associate = map[string]any{"associate_id": *row.AssociateID, "first_name": paymentReadsOptional(row.AssociateName),
			"last_name": paymentReadsOptional(row.AssociateLast)}
	}
	return map[string]any{"payment_id": row.ID, "type": row.Type, "subject": row.Subject, "amount": row.Amount, "associate": associate,
		"description": paymentReadsOptional(row.Description), "paid": row.Paid, "creation_date": DRFUTCDateTime(row.CreationDate),
		"payment_date": invoicePartyTime(row.PaymentDate), "payment_category": paymentReadsOptional(row.CategoryID),
		"sport_association": paymentReadsOptional(row.AssociationID), "notes": paymentReadsOptional(row.Notes),
		"course": invoicePartyJSON(row.Course), "meta": paymentAthleteMeta(row, meta)}
}

// PaymentAthleteOneSolution is payment_list's search of the course's events
// for the one-fee installment: an event whose int(id) is 0 and whose
// payment_date is the installment's date as d/m/Y. ok is false where Python
// raises (events that are not a list of objects with those keys, a value
// int() rejects, or an installment date with microseconds, which the view's
// strptime of str(datetime) rejects).
func PaymentAthleteOneSolution(events *string, due time.Time) (found, ok bool) {
	if events == nil {
		return false, false // iterating None
	}
	decoder := json.NewDecoder(strings.NewReader(*events))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return false, false
	}
	var items []any
	switch typed := value.(type) {
	case []any:
		items = typed
	case map[string]any:
		// Iterating a dict yields its keys: any key fails on inst['id'].
		return false, len(typed) == 0
	case string:
		return false, typed == ""
	default:
		return false, false
	}
	due = due.UTC()
	date := due.Format("02/01/2006")
	for _, item := range items {
		if due.Nanosecond()/1000 != 0 || due.Year() < 1000 {
			return false, false
		}
		event, isObject := item.(map[string]any)
		if !isObject {
			return false, false
		}
		id, present := event["id"]
		if !present {
			return false, false
		}
		number, result := PythonInt(id)
		if result != PythonIntOK {
			return false, false
		}
		if number.Sign() != 0 {
			continue
		}
		day, present := event["payment_date"]
		if !present {
			return false, false
		}
		if text, isText := day.(string); isText && text == date {
			return true, true
		}
	}
	return false, true
}
