package dto

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// AssociationListBilling is the serializer's hand-built dict, rendered by
// DRF's JSONEncoder (same datetime form as DateTimeField under UTC).
type AssociationListBilling struct {
	AutoRenewal bool    `json:"auto_renewal"`
	RenewalType int     `json:"renewal_type"`
	EndsOn      *string `json:"ends_on"`
	BillingPlan *string `json:"billing_plan"`
}

// AssociationListItem is SportAssociationToolSerializer in declared order.
type AssociationListItem struct {
	DateJoined          string                            `json:"date_joined"`
	UserID              string                            `json:"user_id"`
	Email               string                            `json:"email"`
	Username            string                            `json:"username"`
	FirstName           string                            `json:"first_name"`
	LastName            string                            `json:"last_name"`
	Phone               *string                           `json:"phone"`
	Role                *string                           `json:"role"`
	Size                *string                           `json:"size"`
	Denomination        string                            `json:"denomination"`
	TaxCode             string                            `json:"tax_code"`
	Address             *string                           `json:"address"`
	AddressCap          *string                           `json:"address_cap"`
	AddressCity         *string                           `json:"address_city"`
	Market              *string                           `json:"market"`
	LastLogin           *string                           `json:"last_login"`
	Notes               *string                           `json:"notes"`
	AssociationID       string                            `json:"sport_association_id"`
	BillingSubscription *AssociationListBilling           `json:"billing_subscription"`
	UserOnboarding      *domain.AssociationListOnboarding `json:"user_onboarding"`
}

// DRFUTCDateTime is DRF's output for an aware datetime under TIME_ZONE=UTC:
// isoformat() (six fractional digits when microseconds are nonzero), Z suffix.
func DRFUTCDateTime(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02T15:04:05Z")
	}
	return value.Format("2006-01-02T15:04:05.000000Z")
}

func AssociationListItemFrom(row domain.AssociationListRow) AssociationListItem {
	optionalTime := func(value *time.Time) *string {
		if value == nil {
			return nil
		}
		text := DRFUTCDateTime(*value)
		return &text
	}
	item := AssociationListItem{DateJoined: DRFUTCDateTime(row.DateJoined), UserID: row.UserID, Email: row.Email,
		Username: row.Username, FirstName: row.FirstName, LastName: row.LastName, Phone: row.Phone, Role: row.Role, Size: row.Size,
		Denomination: row.Denomination, TaxCode: row.TaxCode, Address: row.Address, AddressCap: row.AddressCap,
		AddressCity: row.AddressCity, Market: row.Market, LastLogin: optionalTime(row.LastLogin), Notes: row.Notes,
		AssociationID: row.AssociationID}
	// UsersOnboarding.objects.get: none or several rows yield None.
	if row.OnboardingCount == 1 {
		item.UserOnboarding = row.Onboarding
	}
	if row.BillingPresent && row.AutoRenewal != nil && row.RenewalType != nil {
		item.BillingSubscription = &AssociationListBilling{AutoRenewal: *row.AutoRenewal, RenewalType: *row.RenewalType,
			EndsOn: optionalTime(row.EndsOn), BillingPlan: row.BillingPlan}
	}
	return item
}
