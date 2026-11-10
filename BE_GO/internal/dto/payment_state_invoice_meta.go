package dto

import (
	"encoding/json"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PaymentStateInvoiceMeta is the payment_obj payment_cancel and payment_delete
// store in invoice.meta: PaymentSerializer(payment).data values, the nested
// user and associate as str() of their dicts (UserSerializer and
// AssociateSerializer field order; AssociateSerializer's sport_association
// and family relations are uuid.UUID objects, its associate_id and user str).
// PaymentSerializer has no 'subscription' field, so it is always None.
func PaymentStateInvoiceMeta(payment domain.CourseSubscriptionPayment) domain.RequestObject {
	user := "None"
	if row := payment.User; row != nil {
		user = PythonStr(domain.RequestObject{{Name: "first_name", Value: row.FirstName}, {Name: "last_name", Value: row.LastName},
			{Name: "email", Value: row.Email}, {Name: "phone", Value: paymentStateMetaText(row.Phone)}, {Name: "username", Value: row.Username},
			{Name: "role", Value: json.Number(strconv.Itoa(row.Role))}})
	}
	associate := "None"
	if payment.AssociateRow != nil {
		row := InconsistencyAssociateFrom(*payment.AssociateRow)
		associate = PythonStr(domain.RequestObject{{Name: "associate_id", Value: row.ID},
			{Name: "address", Value: paymentStateMetaText(row.Address)}, {Name: "address_cap", Value: paymentStateMetaText(row.AddressCap)},
			{Name: "address_city", Value: paymentStateMetaText(row.AddressCity)}, {Name: "born_city", Value: paymentStateMetaText(row.BornCity)},
			{Name: "born_date", Value: paymentStateMetaText(row.BornDate)}, {Name: "email", Value: paymentStateMetaText(row.Email)},
			{Name: "sex", Value: row.Sex}, {Name: "first_name", Value: paymentStateMetaText(row.FirstName)},
			{Name: "last_name", Value: paymentStateMetaText(row.LastName)}, {Name: "is_minor", Value: row.IsMinor},
			{Name: "sport_association", Value: paymentStateMetaUUID(row.AssociationID)}, {Name: "tax_code", Value: paymentStateMetaText(row.TaxCode)},
			{Name: "user", Value: paymentStateMetaText(row.User)}, {Name: "phone", Value: paymentStateMetaText(row.Phone)},
			{Name: "family", Value: paymentStateMetaUUID(row.Family)}, {Name: "family_role", Value: paymentStateMetaText(row.FamilyRole)}})
	}
	var paymentDate any
	if payment.PaymentDate != nil {
		paymentDate = DRFUTCDateTime(*payment.PaymentDate)
	}
	return domain.RequestObject{{Name: "payment_id", Value: payment.ID}, {Name: "user", Value: user}, {Name: "associate", Value: associate},
		{Name: "subscription", Value: nil}, {Name: "subject", Value: json.Number(strconv.Itoa(payment.Subject))},
		{Name: "amount", Value: payment.Amount}, {Name: "creation_date", Value: DRFUTCDateTime(payment.CreationDate)},
		{Name: "payment_date", Value: paymentDate}}
}

func paymentStateMetaText(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

func paymentStateMetaUUID(value *string) any {
	if value == nil {
		return nil
	}
	return PythonUUID(*value)
}
