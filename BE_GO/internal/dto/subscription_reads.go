package dto

import (
	"encoding/json"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func subscriptionReadsDate(value *time.Time) any {
	if value == nil {
		return nil
	}
	return value.Format("2006-01-02")
}

func subscriptionReadsJSON(text *string) any {
	if text == nil {
		return nil
	}
	return json.RawMessage(*text)
}

// SubscriptionReadsFloat is float(Decimal) of a numeric text.
func SubscriptionReadsFloat(text string) any {
	value, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return nil
	}
	return value
}

func subscriptionReadsDay(value time.Time) time.Time {
	return time.Date(value.Year(), value.Month(), value.Day(), 0, 0, 0, 0, time.UTC)
}

func subscriptionReadsDays(later, earlier time.Time) int {
	return int(subscriptionReadsDay(later).Sub(subscriptionReadsDay(earlier)).Hours() / 24)
}

// SubscriptionReadsCurrent is Subscription.is_current against the UTC date.
func SubscriptionReadsCurrent(row domain.SubscriptionReadsRow, now time.Time) bool {
	if row.StartDate == nil || row.EndDate == nil {
		return true
	}
	today := subscriptionReadsDay(now.UTC())
	return !subscriptionReadsDay(*row.StartDate).After(today) && !today.After(subscriptionReadsDay(*row.EndDate))
}

// SubscriptionReadsNextYear is Subscription.is_next_year.
func SubscriptionReadsNextYear(row domain.SubscriptionReadsRow, now time.Time) bool {
	if row.StartDate == nil || row.EndDate == nil {
		return false
	}
	return subscriptionReadsDay(now.UTC()).Before(subscriptionReadsDay(*row.StartDate))
}

// SubscriptionReadsPlainLabel is Subscription.get_plain_medical_label() (the
// associate is present): the age from the UTC date, the certificate against
// today in Europe/Rome.
func SubscriptionReadsPlainLabel(row domain.SubscriptionReadsRow, now time.Time) string {
	today := subscriptionReadsDay(now.UTC())
	rome := subscriptionReadsDay(now.In(courseSubscriptionRome))
	born := row.Associate.BornDate
	age, days := 0, 0
	if born != nil {
		days = subscriptionReadsDays(today, *born)
		age = int(float64(days) / 365.25)
	}
	if age < 6 {
		remaining := 0
		if born != nil {
			remaining = int(6*365.25 - float64(days))
		}
		return "Esente per " + courseSubscriptionDaysText(remaining)
	}
	if row.MedicalID == nil || row.MedicalExpiration == nil {
		missing := max(subscriptionReadsDays(rome, row.CreationDate.In(courseSubscriptionRome)), 0)
		return "Mancante da " + courseSubscriptionDaysText(missing)
	}
	expiration := subscriptionReadsDay(*row.MedicalExpiration)
	if expiration.Before(rome) {
		return "Scaduto da " + courseSubscriptionDaysText(subscriptionReadsDays(rome, expiration))
	}
	return courseSubscriptionDaysText(subscriptionReadsDays(expiration, rome)) + " rimanenti"
}

func subscriptionReadsTags(text string) (any, error) {
	return ParseReferenceJSON([]byte(text))
}

func subscriptionReadsAssociateInfo(row *domain.PersonaAssociate) any {
	if row == nil {
		return nil
	}
	text := PersonaOptional
	return map[string]any{"associate_id": row.ID, "address": text(row.Address), "address_cap": text(row.AddressCap),
		"address_city": text(row.AddressCity), "born_city": text(row.BornCity), "born_date": subscriptionReadsDate(row.BornDate),
		"email": text(row.Email), "first_name": text(row.FirstName), "last_name": text(row.LastName), "is_minor": row.IsMinor, "sex": row.Sex,
		"tax_code": text(row.TaxCode), "phone": text(row.Phone), "phone_2": text(row.Phone2), "phone_3": text(row.Phone3),
		"phone_4": text(row.Phone4), "phone_label": text(row.PhoneLabel), "phone_2_label": text(row.Phone2Label),
		"phone_3_label": text(row.Phone3Label), "phone_4_label": text(row.Phone4Label), "picture_path": text(row.PicturePath)}
}

func subscriptionReadsSignature(row domain.SubscriptionReadsRow) bool {
	return (row.SignatureStorageKey != nil && *row.SignatureStorageKey != "") || (row.SignatureURL != nil && *row.SignatureURL != "")
}

// subscriptionReadsModel are the ModelSerializer fields both list serializers share.
func subscriptionReadsModel(row domain.SubscriptionReadsRow) map[string]any {
	return map[string]any{"subscription_id": row.ID, "custom_data": subscriptionReadsJSON(row.CustomData),
		"additional_fields": subscriptionReadsJSON(row.AdditionalFields), "type": row.Type, "role": row.Role,
		"acceptance_date": subscriptionReadsDate(row.AcceptanceDate), "resignation_date": subscriptionReadsDate(row.ResignationDate),
		"status_flag": row.StatusFlag, "creation_date": DRFUTCDateTime(row.CreationDate), "start_date": subscriptionReadsDate(row.StartDate),
		"end_date": subscriptionReadsDate(row.EndDate), "signature_url": PersonaOptional(row.SignatureURL), "archived": row.Archived,
		"draft": row.Draft, "trial": row.Trial, "meta": subscriptionReadsJSON(row.Meta), "subscription_number": PersonaOptional(row.Number),
		"subscription_type": PersonaOptional(row.SubscriptionType), "deleted": row.Deleted, "competitive": row.Competitive,
		"notes": PersonaOptional(row.Notes), "sport_association": row.AssociationID}
}

func subscriptionReadsPaymentsInfo(payments domain.SubscriptionReadsPayments) (bool, map[string]any) {
	return payments.Unpaid == 0, map[string]any{"total": SubscriptionReadsFloat(payments.Total), "to_be_paid": SubscriptionReadsFloat(payments.ToPay)}
}

// SubscriptionReadsFastItem is SubscriptionFastOptimizedSerializer (renewal from the annotation).
func SubscriptionReadsFastItem(row domain.SubscriptionReadsRow, payments domain.SubscriptionReadsPayments, now time.Time) (map[string]any, error) {
	tags, err := subscriptionReadsTags(row.Tags)
	if err != nil {
		return nil, err
	}
	item := subscriptionReadsModel(row)
	current := SubscriptionReadsCurrent(row, now)
	item["user"] = map[string]any{"user_id": row.UserID, "username": row.UserUsername, "email": PersonaOptional(row.UserEmail),
		"first_name": PersonaOptional(row.UserFirstName), "last_name": PersonaOptional(row.UserLastName), "role": row.UserRole}
	item["associate"] = subscriptionReadsAssociateInfo(row.Associate)
	var payment any
	if row.PaymentID != nil && row.PaymentAmount != nil {
		var date any
		if row.PaymentDate != nil {
			date = DRFUTCDateTime(*row.PaymentDate)
		}
		payment = map[string]any{"payment_id": *row.PaymentID, "amount": SubscriptionReadsFloat(*row.PaymentAmount), "paid": *row.PaymentPaid,
			"payment_date": date}
	}
	item["payment"] = payment
	item["tags"] = tags
	item["all_payments_paid"], item["payments_info"] = subscriptionReadsPaymentsInfo(payments)
	var age, label any
	if row.Associate != nil {
		age = PersonaAge(row.Associate.BornDate, subscriptionReadsDay(now.UTC()))
		label = SubscriptionReadsPlainLabel(row, now)
	}
	item["age"], item["plain_medical_label"] = age, label
	item["current_year"], item["next_years"] = current, SubscriptionReadsNextYear(row, now)
	item["renewal_available"] = !current && !row.FutureRenewal
	item["medical"], item["medical_document"], item["medical_token"], item["medical_expiration_date"] = PersonaOptional(row.MedicalID), nil, nil, nil
	if row.MedicalID != nil {
		item["medical_document"], item["medical_token"] = PersonaOptional(row.MedicalDocumentID), PersonaOptional(row.MedicalToken)
		item["medical_expiration_date"] = subscriptionReadsDate(row.MedicalExpiration)
	}
	item["document"], item["document_token"] = PersonaOptional(row.DocumentID), PersonaOptional(row.DocumentToken)
	item["signature_present"] = subscriptionReadsSignature(row)
	return item, nil
}

// SubscriptionReadsArchivedItem is SubscriptionSerializer (renewal_available,
// a read-only field without attribute, is skipped: the view adds its own).
func SubscriptionReadsArchivedItem(row domain.SubscriptionReadsRow, payments domain.SubscriptionReadsPayments, now time.Time) (map[string]any, error) {
	tags, err := subscriptionReadsTags(row.Tags)
	if err != nil {
		return nil, err
	}
	item := subscriptionReadsModel(row)
	item["supplier"], item["instructor"] = PersonaOptional(row.SupplierID), PersonaOptional(row.InstructorID)
	item["document_pdf"], item["payment"], item["group"] = PersonaOptional(row.DocumentID), PersonaOptional(row.PaymentID), PersonaOptional(row.GroupID)
	item["user"] = SubscriptionReadsUser(row)
	item["associate"] = subscriptionReadsAssociateInfo(row.Associate)
	item["tags"] = tags
	item["all_payments_paid"], item["payments_info"] = subscriptionReadsPaymentsInfo(payments)
	var age any
	if row.Associate != nil {
		age = PersonaAge(row.Associate.BornDate, subscriptionReadsDay(now.UTC()))
	}
	item["age"] = age
	item["current_year"], item["next_years"] = SubscriptionReadsCurrent(row, now), SubscriptionReadsNextYear(row, now)
	item["medical"], item["medical_token"], item["medical_expiration_date"] = nil, nil, nil
	if row.MedicalID != nil {
		item["medical"], item["medical_token"] = PersonaOptional(row.MedicalDocumentID), PersonaOptional(row.MedicalToken)
		item["medical_expiration_date"] = subscriptionReadsDate(row.MedicalExpiration)
	}
	item["document"], item["document_token"] = PersonaOptional(row.DocumentID), PersonaOptional(row.DocumentToken)
	item["signature_present"] = subscriptionReadsSignature(row)
	return item, nil
}

// SubscriptionReadsUser is UserSerializer.
func SubscriptionReadsUser(row domain.SubscriptionReadsRow) map[string]any {
	return map[string]any{"first_name": PersonaOptional(row.UserFirstName), "last_name": PersonaOptional(row.UserLastName),
		"email": PersonaOptional(row.UserEmail), "phone": PersonaOptional(row.UserPhone), "username": row.UserUsername, "role": row.UserRole}
}

// SubscriptionReadsBasicItem is SubscriptionFastBasicSerializer.
func SubscriptionReadsBasicItem(row domain.SubscriptionReadsRow, now time.Time) map[string]any {
	var associate any
	if row.Associate != nil {
		associate = map[string]any{"associate_id": row.Associate.ID, "first_name": PersonaOptional(row.Associate.FirstName),
			"last_name": PersonaOptional(row.Associate.LastName), "tax_code": PersonaOptional(row.Associate.TaxCode),
			"family": PersonaOptional(row.Associate.FamilyID), "family_role": PersonaOptional(row.Associate.FamilyRole)}
	}
	return map[string]any{"subscription_id": row.ID, "associate": associate, "current_year": SubscriptionReadsCurrent(row, now)}
}

// SubscriptionReadsSeason is Subscription.season.
func SubscriptionReadsSeason(row domain.SubscriptionReadsRow) any {
	if row.StartDate == nil || row.EndDate == nil {
		return nil
	}
	if row.StartDate.Year() == row.EndDate.Year() {
		return strconv.Itoa(row.StartDate.Year())
	}
	return strconv.Itoa(row.StartDate.Year()) + "/" + strconv.Itoa(row.EndDate.Year())
}

// SubscriptionReadsInfo is SubscriptionInfoSerializer without the nested associate, card and files.
func SubscriptionReadsInfo(row domain.SubscriptionReadsRow, tagIDs []any, now time.Time) map[string]any {
	item := subscriptionReadsModel(row)
	item["supplier"], item["instructor"] = PersonaOptional(row.SupplierID), PersonaOptional(row.InstructorID)
	item["document_pdf"], item["payment"], item["group"] = PersonaOptional(row.DocumentID), PersonaOptional(row.PaymentID), PersonaOptional(row.GroupID)
	item["medical"], item["tags"] = PersonaOptional(row.MedicalID), tagIDs
	item["user"] = SubscriptionReadsUser(row)
	item["medical_expiration_date"], item["medical_document"], item["medical_token"] = nil, nil, nil
	if row.MedicalID != nil {
		item["medical_expiration_date"] = subscriptionReadsDate(row.MedicalExpiration)
		item["medical_document"], item["medical_token"] = PersonaOptional(row.MedicalDocumentID), PersonaOptional(row.MedicalToken)
		// BooleanField without allow_null: absent when there is no certificate.
		item["competitive_medical_certificate"] = row.MedicalCompetitive != nil && *row.MedicalCompetitive
	}
	item["is_current"], item["is_next_year"] = SubscriptionReadsCurrent(row, now), SubscriptionReadsNextYear(row, now)
	item["plain_medical_label"] = SubscriptionReadsPlainLabel(row, now)
	item["signature_present"] = subscriptionReadsSignature(row)
	return item
}
