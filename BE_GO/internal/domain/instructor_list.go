package domain

import (
	"context"
	"time"
)

// InstructorRow is one Instructor with the columns InstructorSerializer
// renders; Documents is the JSON array of linked document keys.
type InstructorRow struct {
	ID                       string     `db:"instructor_id"`
	UserID                   string     `db:"user_id"`
	FirstName                string     `db:"first_name"`
	LastName                 string     `db:"last_name"`
	Email                    *string    `db:"email"`
	BornDate                 *time.Time `db:"born_date"`
	BornCity                 *string    `db:"born_city"`
	BornProvince             *string    `db:"born_province"`
	TaxCode                  *string    `db:"tax_code"`
	AddressCity              *string    `db:"address_city"`
	Address                  *string    `db:"address"`
	AddressProvince          *string    `db:"address_province"`
	CivicNumber              *string    `db:"civic_number"`
	StudyTitle               *string    `db:"study_title"`
	Role                     *string    `db:"role"`
	Phone                    *string    `db:"phone"`
	Phone2                   *string    `db:"phone_2"`
	Phone3                   *string    `db:"phone_3"`
	Phone4                   *string    `db:"phone_4"`
	Draft                    bool       `db:"draft"`
	AssociatedUserID         *string    `db:"associated_user_id"`
	IsVolunteer              bool       `db:"is_volunteer"`
	StipulatedContractIn     *time.Time `db:"stipulated_contract_in"`
	DefaultHourlyBilling     string     `db:"default_hourly_billing"`
	DefaultPercentageBilling *string    `db:"default_percentage_billing"`
	Documents                string     `db:"documents"`
}

type InstructorListStore interface {
	// Instructors is Instructor.objects.filter(user=owner), unordered.
	Instructors(ctx context.Context, owner string) ([]InstructorRow, error)
}
