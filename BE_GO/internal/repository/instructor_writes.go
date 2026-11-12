package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type InstructorWrites struct{ db *sqlx.DB }

func NewInstructorWrites(db *sqlx.DB) *InstructorWrites { return &InstructorWrites{db: db} }

var _ domain.InstructorWritesStore = (*InstructorWrites)(nil)

type instructorWritesTransaction struct{ *ownedTransaction }

func (r *InstructorWrites) BeginInstructorWrites(ctx context.Context) (domain.InstructorWritesTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &instructorWritesTransaction{ownedTransaction: tx}, nil
}

const instructorFirstAssociation = `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`

const instructorAssociations = `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 2`

func instructorOptional(rows []string) *string {
	if len(rows) == 0 {
		return nil
	}
	return &rows[0]
}

func (r *InstructorWrites) FirstAssociation(ctx context.Context, user string) (*string, error) {
	rows := []string{}
	err := r.db.SelectContext(ctx, &rows, instructorFirstAssociation, user)
	return instructorOptional(rows), err
}

func (r *InstructorWrites) Associations(ctx context.Context, user string) ([]string, error) {
	rows := []string{}
	err := r.db.SelectContext(ctx, &rows, instructorAssociations, user)
	return rows, err
}

const instructorLessonColumns = `SELECT instructor_id, user_id, first_name, last_name FROM application_instructor`

func instructorLessonFirst(rows []domain.InstructorLessonRow) *domain.InstructorLessonRow {
	if len(rows) == 0 {
		return nil
	}
	return &rows[0]
}

func (r *InstructorWrites) LessonInstructor(ctx context.Context, id string) (*domain.InstructorLessonRow, error) {
	rows := []domain.InstructorLessonRow{}
	err := r.db.SelectContext(ctx, &rows, instructorLessonColumns+` WHERE instructor_id=$1`, id)
	return instructorLessonFirst(rows), err
}

func (r *InstructorWrites) RequestingInstructor(ctx context.Context, acting string, owner *string) (*domain.InstructorLessonRow, error) {
	rows := []domain.InstructorLessonRow{}
	err := r.db.SelectContext(ctx, &rows, instructorLessonColumns+` WHERE associated_user_id=$1 AND ($2::uuid IS NULL OR user_id=$2::uuid)
 ORDER BY instructor_id LIMIT 1`, acting, owner)
	return instructorLessonFirst(rows), err
}

func (r *InstructorWrites) AssociationInstructors(ctx context.Context, association string) ([]domain.InstructorLessonRow, error) {
	rows := []domain.InstructorLessonRow{}
	err := r.db.SelectContext(ctx, &rows, instructorLessonColumns+` WHERE NOT draft AND user_id IN
 (SELECT user_id FROM application_sportassociation WHERE sport_association_id=$1 AND NOT deleted)`, association)
	return rows, err
}

func (r *InstructorWrites) PublishedRegistries(ctx context.Context, association string) ([]domain.InstructorLessonRegistry, error) {
	rows := []domain.InstructorLessonRegistry{}
	err := r.db.SelectContext(ctx, &rows, `SELECT c.course_id, c.title, r.events::text AS events FROM application_attendanceregistry r
 JOIN application_course c ON c.course_id=r.course_id WHERE c.sport_association_id=$1 AND r.status=2`, association)
	return rows, err
}

func (t *instructorWritesTransaction) FirstAssociation(ctx context.Context, user string) (*string, error) {
	rows := []string{}
	err := t.tx.SelectContext(ctx, &rows, instructorFirstAssociation, user)
	return instructorOptional(rows), err
}

func (t *instructorWritesTransaction) Associations(ctx context.Context, user string) ([]string, error) {
	rows := []string{}
	err := t.tx.SelectContext(ctx, &rows, instructorAssociations, user)
	return rows, err
}

func (t *instructorWritesTransaction) LiveAssociation(ctx context.Context, id string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1::uuid
 AND NOT deleted)`, id)
	return found, err
}

const instructorRecordColumns = `instructor_id, user_id, first_name, last_name, email, born_date::text AS born_date, born_city, born_province,
 tax_code, address_city, address, address_province, civic_number, study_title, role, phone, phone_2, phone_3, phone_4, draft,
 associated_user_id, is_volunteer, stipulated_contract_in::text AS stipulated_contract_in,
 default_hourly_billing::text AS default_hourly_billing, default_percentage_billing::text AS default_percentage_billing`

func (t *instructorWritesTransaction) OwnedInstructor(ctx context.Context, id, owner string) (*domain.InstructorRecord, error) {
	var row domain.InstructorRecord
	err := t.tx.GetContext(ctx, &row, `SELECT `+instructorRecordColumns+` FROM application_instructor
 WHERE instructor_id=$1 AND user_id=$2 ORDER BY instructor_id LIMIT 1 FOR UPDATE`, id, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *instructorWritesTransaction) DocumentExists(ctx context.Context, id string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM docmanager_document WHERE document_id=$1)`, id)
	return found, err
}

func instructorRecordArgs(row domain.InstructorRecord) []any {
	return []any{row.ID, row.UserID, row.FirstName, row.LastName, row.Email, row.BornDate, row.BornCity, row.BornProvince, row.TaxCode,
		row.AddressCity, row.Address, row.AddressProvince, row.CivicNumber, row.StudyTitle, row.Role, row.Phone, row.Phone2, row.Phone3,
		row.Phone4, row.Draft, row.AssociatedUserID, row.IsVolunteer, row.StipulatedContractIn, row.DefaultHourlyBilling,
		row.DefaultPercentageBilling}
}

func (t *instructorWritesTransaction) InsertInstructor(ctx context.Context, row domain.InstructorRecord) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_instructor (instructor_id, user_id, first_name, last_name, email, born_date,
 born_city, born_province, tax_code, address_city, address, address_province, civic_number, study_title, role, phone, phone_2, phone_3,
 phone_4, draft, associated_user_id, is_volunteer, stipulated_contract_in, default_hourly_billing, default_percentage_billing)
 VALUES ($1,$2,$3,$4,$5,$6::date,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21::uuid,$22,$23::date,$24::numeric,$25::numeric)`,
		instructorRecordArgs(row)...)
	return err
}

func (t *instructorWritesTransaction) UpdateInstructor(ctx context.Context, row domain.InstructorRecord) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_instructor SET user_id=$2, first_name=$3, last_name=$4, email=$5, born_date=$6::date,
 born_city=$7, born_province=$8, tax_code=$9, address_city=$10, address=$11, address_province=$12, civic_number=$13, study_title=$14,
 role=$15, phone=$16, phone_2=$17, phone_3=$18, phone_4=$19, draft=$20, associated_user_id=$21::uuid, is_volunteer=$22,
 stipulated_contract_in=$23::date, default_hourly_billing=$24::numeric, default_percentage_billing=$25::numeric WHERE instructor_id=$1`,
		instructorRecordArgs(row)...)
	return err
}

// SetDocuments is instructor.documents.set(documents): links not listed go,
// missing ones are added once.
func (t *instructorWritesTransaction) SetDocuments(ctx context.Context, instructor string, documents []string) error {
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_instructor_documents WHERE instructor_id=$1 AND NOT document_id=ANY($2::uuid[])`,
		instructor, uuidArray(documents)); err != nil {
		return err
	}
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_instructor_documents (instructor_id, document_id)
 SELECT DISTINCT $1::uuid, d FROM unnest($2::uuid[]) d WHERE NOT EXISTS (SELECT 1 FROM application_instructor_documents
 WHERE instructor_id=$1::uuid AND document_id=d)`, instructor, uuidArray(documents))
	return err
}

var instructorWritesTables = map[string][2]string{
	"Instructor":      {"application_instructor", "instructor_id"},
	"InstructorHours": {"application_instructorhours", "instructor_hours_id"},
	"Reminders":       {"application_reminders", "reminders_id"},
	"Payment":         {"application_payment", "payment_id"},
}

func (t *instructorWritesTransaction) CreatedChanges(ctx context.Context, model, id string) (map[string][2]string, error) {
	table := instructorWritesTables[model]
	return createdModelChanges(ctx, t.tx, table[0], table[1], id)
}

func (t *instructorWritesTransaction) AppendAudit(ctx context.Context, event domain.InstructorWritesAudit, audit domain.LoginAuditContext,
	at time.Time) error {
	entry := modelAuditEntry{Model: lowerASCII(event.Model), Key: event.Key, Representation: event.Model + " object (" + event.Key + ")",
		Action: event.Action, Changes: event.Changes, Context: audit, At: at}
	if event.AssociationID != nil {
		path := event.Path
		entry.AssociationID, entry.ResolutionPath = event.AssociationID, &path
	}
	return appendModelAudit(ctx, t.tx, entry)
}

func (t *instructorWritesTransaction) keys(ctx context.Context, query string, args ...any) ([]string, error) {
	rows := []string{}
	err := t.tx.SelectContext(ctx, &rows, query, args...)
	return rows, err
}

// DeleteInstructor is instructor.delete(): the collector gathers the hours
// and reminders (key order), deletes the document links, sets the payments'
// and subscriptions' instructor to NULL, then deletes hours, reminders and
// the instructor, each model's post_delete entries in reverse key order.
func (t *instructorWritesTransaction) DeleteInstructor(ctx context.Context, id string) ([]domain.InstructorWritesAudit, error) {
	type batch struct {
		model string
		ids   []string
	}
	hours, err := t.keys(ctx, `SELECT instructor_hours_id FROM application_instructorhours WHERE instructor_id=$1
 ORDER BY instructor_hours_id FOR UPDATE`, id)
	if err != nil {
		return nil, err
	}
	reminders, err := t.keys(ctx, `SELECT reminders_id FROM application_reminders WHERE instructor_id=$1 ORDER BY reminders_id FOR UPDATE`, id)
	if err != nil {
		return nil, err
	}
	batches := []batch{{"InstructorHours", hours}, {"Reminders", reminders}, {"Instructor", []string{id}}}
	snapshots := map[string]map[string][2]string{}
	for _, b := range batches {
		table := instructorWritesTables[b.model]
		for _, key := range b.ids {
			changes, _, err := storedModelChanges(ctx, t.tx, table[0], table[1], key, false, true)
			if err != nil {
				return nil, err
			}
			snapshots[key] = changes
		}
	}
	for _, statement := range []string{
		`DELETE FROM application_instructor_documents WHERE instructor_id=$1`,
		`UPDATE application_payment SET instructor_id=NULL WHERE instructor_id=$1`,
		`UPDATE application_subscription SET instructor_id=NULL WHERE instructor_id=$1`,
	} {
		if _, err := t.tx.ExecContext(ctx, statement, id); err != nil {
			return nil, err
		}
	}
	events := []domain.InstructorWritesAudit{}
	for _, b := range batches {
		table := instructorWritesTables[b.model]
		if _, err := t.tx.ExecContext(ctx, `DELETE FROM `+table[0]+` WHERE `+table[1]+`=ANY($1::uuid[])`, uuidArray(b.ids)); err != nil {
			return nil, err
		}
		for index := len(b.ids) - 1; index >= 0; index-- {
			events = append(events, domain.InstructorWritesAudit{Model: b.model, Key: b.ids[index], Action: 2, Changes: snapshots[b.ids[index]]})
		}
	}
	return events, nil
}

func (t *instructorWritesTransaction) SelectableHours(ctx context.Context, instructor string, ids []string) ([]domain.InstructorHour, error) {
	rows := []domain.InstructorHour{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+instructorHourColumns+` FROM application_instructorhours h
 WHERE h.instructor_id=$1 AND h.instructor_hours_id=ANY($2::uuid[]) AND h.payment_id IS NULL AND NOT h.paid ORDER BY h.ctid FOR UPDATE`,
		instructor, uuidArray(ids))
	return rows, err
}

func (t *instructorWritesTransaction) CompensationCategory(ctx context.Context) (*string, error) {
	rows := []string{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT payment_category_id FROM application_paymentcategory
 WHERE name='Compensi e Rimborsi Spese' ORDER BY payment_category_id LIMIT 1`)
	return instructorOptional(rows), err
}

func (t *instructorWritesTransaction) DefaultBankAccount(ctx context.Context, association string) (*string, error) {
	rows := []string{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT custom_account_id FROM application_customaccounts
 WHERE sport_association_id=$1 AND NOT editable AND enabled AND account_type=2 ORDER BY custom_account_id LIMIT 1`, association)
	return instructorOptional(rows), err
}

func (t *instructorWritesTransaction) GroupExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_group WHERE group_id=$1::uuid)`, id)
	return exists, err
}

func (t *instructorWritesTransaction) InsertPayment(ctx context.Context, p domain.InstructorCompensationPayment) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_payment (payment_id, type, subject, amount, creation_date, payment_date, paid,
 expense, description, payment_category_id, user_id, instructor_id, sport_association_id, archived, custom_accounts_id, deleted, group_id)
 VALUES ($1,'default',0,$2::numeric,$3,$4,false,true,$5,$6,$7,$8,$9,false,$10,false,$11)`, p.ID, p.Amount, p.CreationDate, p.PaymentDate,
		p.Description, p.CategoryID, p.UserID, p.InstructorID, p.AssociationID, p.AccountID, p.GroupID)
	return err
}

func (t *instructorWritesTransaction) LinkHour(ctx context.Context, hour, payment string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_instructorhours SET payment_id=$2 WHERE instructor_hours_id=$1`, hour, payment)
	return err
}

func (t *instructorWritesTransaction) SetPaymentNotes(ctx context.Context, payment, notes string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET notes=$2 WHERE payment_id=$1`, payment, notes)
	return err
}
