package dto

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// InconsistencyAssociate is AssociateSerializer in declared field order.
type InconsistencyAssociate struct {
	ID            string  `json:"associate_id"`
	Address       *string `json:"address"`
	AddressCap    *string `json:"address_cap"`
	AddressCity   *string `json:"address_city"`
	BornCity      *string `json:"born_city"`
	BornDate      *string `json:"born_date"`
	Email         *string `json:"email"`
	Sex           string  `json:"sex"`
	FirstName     *string `json:"first_name"`
	LastName      *string `json:"last_name"`
	IsMinor       bool    `json:"is_minor"`
	AssociationID *string `json:"sport_association"`
	TaxCode       *string `json:"tax_code"`
	User          *string `json:"user"`
	Phone         *string `json:"phone"`
	Family        *string `json:"family"`
	FamilyRole    *string `json:"family_role"`
}

type InconsistencyReport struct {
	Inconsistencies bool                     `json:"inconsistencies"`
	MissingTutors   []InconsistencyAssociate `json:"missing_tutors"`
}

func InconsistencyAssociateFrom(row domain.InconsistencyAssociate) InconsistencyAssociate {
	var born *string
	if row.BornDate != nil {
		text := row.BornDate.Format("2006-01-02")
		born = &text
	}
	// get_user: the associate's user, else its association owner.
	user := row.UserID
	if user == nil {
		user = row.OwnerUserID
	}
	return InconsistencyAssociate{ID: row.ID, Address: row.Address, AddressCap: row.AddressCap, AddressCity: row.AddressCity,
		BornCity: row.BornCity, BornDate: born, Email: row.Email, Sex: row.Sex, FirstName: row.FirstName, LastName: row.LastName,
		IsMinor: row.IsMinor, AssociationID: row.AssociationID, TaxCode: row.TaxCode, User: user, Phone: row.Phone,
		Family: row.FamilyID, FamilyRole: row.FamilyRole}
}

// RelativeYears is dateutil relativedelta(now, born).years for dates: the
// anniversary clamps to the month's last day (29 February on 28 February).
func RelativeYears(now, born time.Time) int {
	years := now.Year() - born.Year()
	anniversary := func(year int) time.Time {
		last := time.Date(year, born.Month()+1, 0, 0, 0, 0, 0, time.UTC).Day()
		return time.Date(year, born.Month(), min(born.Day(), last), 0, 0, 0, 0, time.UTC)
	}
	if anniversary(now.Year()).After(time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)) {
		years--
	}
	return years
}
