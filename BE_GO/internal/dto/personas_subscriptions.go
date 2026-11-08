package dto

import (
	"encoding/json"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func personaJSON(value *json.RawMessage) any {
	if value == nil {
		return nil
	}
	decoder := json.NewDecoder(strings.NewReader(string(*value)))
	decoder.UseNumber()
	var decoded any
	if err := decoder.Decode(&decoded); err != nil {
		return nil
	}
	return personaJSONObject(decoded)
}

// personaJSONObject keeps decoded JSON objects as ordered request objects.
func personaJSONObject(value any) any {
	switch v := value.(type) {
	case map[string]any:
		object := domain.RequestObject{}
		for key, item := range v {
			object = append(object, domain.RequestField{Name: key, Value: personaJSONObject(item)})
		}
		return object
	case []any:
		items := make([]any, len(v))
		for i, item := range v {
			items[i] = personaJSONObject(item)
		}
		return items
	}
	return value
}

// PersonaSubscriptionItem is AssociateSubscriptionSerializer: the declared
// fields, then the model fields (signature_storage_key excluded) and the
// forward relations; associate is BasicAssociateSerializer (second and third
// are the M2M managers' tutors one and two levels down).
func PersonaSubscriptionItem(row domain.PersonaSubscriptionRow, associate domain.PersonaAssociate, second, third []domain.PersonaRelation,
	today time.Time) domain.RequestObject {
	current, year, next := true, "corrente", false
	if row.StartDate != nil && row.EndDate != nil {
		switch {
		case !row.StartDate.After(today) && !today.After(*row.EndDate):
		case row.EndDate.Before(today):
			current, year = false, "precedente"
		default:
			current, year = false, "successivo"
		}
		next = today.Before(*row.StartDate)
	}
	tags := []any{}
	if row.Tags != nil {
		for _, tag := range strings.Split(*row.Tags, ",") {
			tags = append(tags, tag)
		}
	}
	var medical, expiration any
	if row.MedicalID != nil {
		medical = *row.MedicalID
		expiration = personaDate(row.MedicalExpiration)
	}
	return domain.RequestObject{
		{Name: "subscription_id", Value: row.ID}, {Name: "active", Value: current}, {Name: "is_current", Value: current},
		{Name: "is_next_year", Value: next}, {Name: "current_year", Value: year}, {Name: "medical", Value: medical},
		{Name: "medical_expiration_date", Value: expiration}, {Name: "age", Value: personaInt(PersonaAge(associate.BornDate, today))},
		{Name: "renewal_available", Value: !current && !row.FutureRenewal},
		{Name: "signature_present", Value: row.SignatureStorageKey != nil && *row.SignatureStorageKey != "" || row.SignatureURL != nil && *row.SignatureURL != ""},
		{Name: "associate", Value: personaBasicAssociate(associate, second, third)},
		{Name: "custom_data", Value: personaJSON(row.CustomData)}, {Name: "additional_fields", Value: personaJSON(row.AdditionalFields)},
		{Name: "type", Value: personaInt(row.Type)}, {Name: "role", Value: personaInt(row.Role)},
		{Name: "acceptance_date", Value: personaDate(row.AcceptanceDate)}, {Name: "resignation_date", Value: personaDate(row.ResignationDate)},
		{Name: "status_flag", Value: personaInt(row.StatusFlag)}, {Name: "creation_date", Value: DRFUTCDateTime(row.CreationDate)},
		{Name: "start_date", Value: personaDate(row.StartDate)}, {Name: "end_date", Value: personaDate(row.EndDate)},
		{Name: "signature_url", Value: PersonaOptional(row.SignatureURL)}, {Name: "deleted", Value: row.Deleted},
		{Name: "archived", Value: row.Archived}, {Name: "draft", Value: row.Draft}, {Name: "trial", Value: row.Trial},
		{Name: "meta", Value: personaJSON(row.Meta)}, {Name: "subscription_number", Value: PersonaOptional(row.SubscriptionNumber)},
		{Name: "subscription_type", Value: PersonaOptional(row.SubscriptionType)}, {Name: "competitive", Value: row.Competitive},
		{Name: "notes", Value: PersonaOptional(row.Notes)}, {Name: "sport_association", Value: row.AssociationID},
		{Name: "supplier", Value: PersonaOptional(row.SupplierID)}, {Name: "instructor", Value: PersonaOptional(row.InstructorID)},
		{Name: "user", Value: row.UserID}, {Name: "document_pdf", Value: PersonaOptional(row.DocumentID)},
		{Name: "payment", Value: PersonaOptional(row.PaymentID)}, {Name: "group", Value: PersonaOptional(row.GroupID)},
		{Name: "tags", Value: tags},
	}
}
