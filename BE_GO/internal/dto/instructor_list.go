package dto

import (
	"encoding/json"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// InstructorItem is InstructorSerializer (fields '__all__').
type InstructorItem struct {
	ID                       string   `json:"instructor_id"`
	UserID                   string   `json:"user"`
	FirstName                string   `json:"first_name"`
	LastName                 string   `json:"last_name"`
	Email                    *string  `json:"email"`
	BornDate                 *string  `json:"born_date"`
	BornCity                 *string  `json:"born_city"`
	BornProvince             *string  `json:"born_province"`
	TaxCode                  *string  `json:"tax_code"`
	AddressCity              *string  `json:"address_city"`
	Address                  *string  `json:"address"`
	AddressProvince          *string  `json:"address_province"`
	CivicNumber              *string  `json:"civic_number"`
	StudyTitle               *string  `json:"study_title"`
	Role                     *string  `json:"role"`
	Phone                    *string  `json:"phone"`
	Phone2                   *string  `json:"phone_2"`
	Phone3                   *string  `json:"phone_3"`
	Phone4                   *string  `json:"phone_4"`
	Draft                    bool     `json:"draft"`
	AssociatedUserID         *string  `json:"associated_user_id"`
	IsVolunteer              bool     `json:"is_volunteer"`
	StipulatedContractIn     *string  `json:"stipulated_contract_in"`
	DefaultHourlyBilling     string   `json:"default_hourly_billing"`
	DefaultPercentageBilling *string  `json:"default_percentage_billing"`
	Documents                []string `json:"documents"`
}

func isoDate(value *time.Time) *string {
	if value == nil {
		return nil
	}
	text := value.Format("2006-01-02")
	return &text
}

func InstructorItemFrom(row domain.InstructorRow) (InstructorItem, error) {
	item := InstructorItem{ID: row.ID, UserID: row.UserID, FirstName: row.FirstName, LastName: row.LastName, Email: row.Email,
		BornDate: isoDate(row.BornDate), BornCity: row.BornCity, BornProvince: row.BornProvince, TaxCode: row.TaxCode,
		AddressCity: row.AddressCity, Address: row.Address, AddressProvince: row.AddressProvince, CivicNumber: row.CivicNumber,
		StudyTitle: row.StudyTitle, Role: row.Role, Phone: row.Phone, Phone2: row.Phone2, Phone3: row.Phone3, Phone4: row.Phone4,
		Draft: row.Draft, AssociatedUserID: row.AssociatedUserID, IsVolunteer: row.IsVolunteer,
		StipulatedContractIn: isoDate(row.StipulatedContractIn), DefaultHourlyBilling: row.DefaultHourlyBilling,
		DefaultPercentageBilling: row.DefaultPercentageBilling, Documents: []string{}}
	err := json.Unmarshal([]byte(row.Documents), &item.Documents)
	return item, err
}
