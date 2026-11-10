package dto

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// SubscriptionReadsAthleteItem is SubscriptionSerializerAthleteOptimizedList
// without the nested associate, the association and the per-request fields
// (carnets, courses, renewal_available) the service adds: the ModelSerializer
// fields (exclude signature_storage_key; user and the other forward relations
// as keys, tags as their keys), is_current and the medical method fields.
func SubscriptionReadsAthleteItem(row domain.SubscriptionReadsRow, now time.Time) (map[string]any, error) {
	tags, err := ParseReferenceJSON([]byte(row.Tags))
	if err != nil {
		return nil, err
	}
	tagIDs := []any{}
	for _, tag := range tags.([]any) {
		value, _ := tag.(domain.RequestObject).Lookup("tag_id")
		tagIDs = append(tagIDs, value)
	}
	item := subscriptionReadsModel(row)
	item["user"] = row.UserID
	item["associate"] = nil
	item["supplier"], item["instructor"] = PersonaOptional(row.SupplierID), PersonaOptional(row.InstructorID)
	item["document_pdf"], item["payment"], item["group"] = PersonaOptional(row.DocumentID), PersonaOptional(row.PaymentID), PersonaOptional(row.GroupID)
	item["tags"] = tagIDs
	item["is_current"] = SubscriptionReadsCurrent(row, now)
	item["medical"], item["medical_token"], item["medical_expiration_date"], item["medical_document"] = nil, nil, nil, nil
	if row.MedicalID != nil {
		item["medical"], item["medical_token"] = *row.MedicalID, PersonaOptional(row.MedicalToken)
		item["medical_expiration_date"], item["medical_document"] = subscriptionReadsDate(row.MedicalExpiration), PersonaOptional(row.MedicalDocumentID)
	}
	item["signature_present"] = subscriptionReadsSignature(row)
	return item, nil
}

// SubscriptionReadsAthleteAssociate is user_serializers.AssociateSerializer;
// user is get_user (the associate's user, else its association owner's).
func SubscriptionReadsAthleteAssociate(row domain.PersonaAssociate, user any) map[string]any {
	return map[string]any{"associate_id": row.ID, "address": PersonaOptional(row.Address), "address_cap": PersonaOptional(row.AddressCap),
		"address_city": PersonaOptional(row.AddressCity), "born_city": PersonaOptional(row.BornCity), "born_date": subscriptionReadsDate(row.BornDate),
		"email": PersonaOptional(row.Email), "sex": row.Sex, "first_name": PersonaOptional(row.FirstName), "last_name": PersonaOptional(row.LastName),
		"is_minor": row.IsMinor, "sport_association": PersonaOptional(row.AssociationID), "tax_code": PersonaOptional(row.TaxCode), "user": user,
		"phone": PersonaOptional(row.Phone), "family": PersonaOptional(row.FamilyID), "family_role": PersonaOptional(row.FamilyRole)}
}
