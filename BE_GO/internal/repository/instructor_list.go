package repository

import (
	"context"
	"time"

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

var _ domain.InstructorInfoStore = (*InstructorLists)(nil)

const instructorColumns = `SELECT i.instructor_id, i.user_id, i.first_name, i.last_name, i.email, i.born_date, i.born_city,
 i.born_province, i.tax_code, i.address_city, i.address, i.address_province, i.civic_number, i.study_title, i.role, i.phone, i.phone_2,
 i.phone_3, i.phone_4, i.draft, i.associated_user_id, i.is_volunteer, i.stipulated_contract_in,
 i.default_hourly_billing::text AS default_hourly_billing, i.default_percentage_billing::text AS default_percentage_billing,
 COALESCE((SELECT json_agg(d.document_id) FROM application_instructor_documents d WHERE d.instructor_id=i.instructor_id), '[]'::json)::text AS documents
 FROM application_instructor i`

func (r *InstructorLists) Instructor(ctx context.Context, id string) (*domain.InstructorRow, error) {
	rows := []domain.InstructorRow{}
	if err := r.db.SelectContext(ctx, &rows, instructorColumns+` WHERE i.instructor_id=$1`, id); err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

func (r *InstructorLists) FirstAssociation(ctx context.Context, user string) (*string, error) {
	rows := []string{}
	err := r.db.SelectContext(ctx, &rows, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, user)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

func (r *InstructorLists) OwnInstructor(ctx context.Context, owner, acting string) (*string, error) {
	rows := []string{}
	err := r.db.SelectContext(ctx, &rows, `SELECT instructor_id FROM application_instructor
 WHERE user_id=$1 AND associated_user_id=$2 ORDER BY instructor_id LIMIT 1`, owner, acting)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

func (r *InstructorLists) Hours(ctx context.Context, instructor string, from, to time.Time) ([]domain.InstructorHourRow, error) {
	rows := []domain.InstructorHourRow{}
	err := r.db.SelectContext(ctx, &rows, `SELECT hours::text AS hours, amount::text AS amount, paid FROM application_instructorhours
 WHERE instructor_id=$1 AND date>=$2::date AND date<=$3::date`, instructor, from.Format("2006-01-02"), to.Format("2006-01-02"))
	return rows, err
}
