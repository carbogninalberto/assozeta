package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type InstructorLists struct{ db *sqlx.DB }

func NewInstructorLists(db *sqlx.DB) *InstructorLists { return &InstructorLists{db: db} }

var _ domain.InstructorListStore = (*InstructorLists)(nil)

func (r *InstructorLists) Instructors(ctx context.Context, owner string) ([]domain.InstructorRow, error) {
	rows := []domain.InstructorRow{}
	err := r.db.SelectContext(ctx, &rows, `SELECT i.instructor_id, i.user_id, i.first_name, i.last_name, i.email, i.born_date, i.born_city,
 i.born_province, i.tax_code, i.address_city, i.address, i.address_province, i.civic_number, i.study_title, i.role, i.phone, i.phone_2,
 i.phone_3, i.phone_4, i.draft, i.associated_user_id, i.is_volunteer, i.stipulated_contract_in,
 i.default_hourly_billing::text AS default_hourly_billing, i.default_percentage_billing::text AS default_percentage_billing,
 COALESCE((SELECT json_agg(d.document_id) FROM application_instructor_documents d WHERE d.instructor_id=i.instructor_id), '[]'::json)::text AS documents
 FROM application_instructor i WHERE i.user_id=$1`, owner)
	return rows, err
}
