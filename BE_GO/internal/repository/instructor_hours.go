package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type InstructorHours struct{ db *sqlx.DB }

func NewInstructorHours(db *sqlx.DB) *InstructorHours { return &InstructorHours{db: db} }

var _ domain.InstructorHoursStore = (*InstructorHours)(nil)

type instructorHoursTransaction struct{ *ownedTransaction }

func (r *InstructorHours) BeginInstructorHours(ctx context.Context) (domain.InstructorHoursTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &instructorHoursTransaction{ownedTransaction: tx}, nil
}

func (r *InstructorHours) OwnedInstructorExists(ctx context.Context, id, owner string) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_instructor WHERE instructor_id=$1 AND user_id=$2)`, id, owner)
	return found, err
}

// instructorOwnerAssociation is user_resolver(instructor.user):
// SportAssociation.objects.filter(user=owner).first().
const instructorOwnerAssociation = `SELECT sa.sport_association_id FROM application_sportassociation sa
 JOIN application_instructor i ON i.user_id=sa.user_id WHERE i.instructor_id=$1::uuid AND NOT sa.deleted
 ORDER BY sa.sport_association_id LIMIT 1`

func (t *instructorHoursTransaction) InstructorAssociation(ctx context.Context, id string) (bool, *string, error) {
	var found bool
	if err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_instructor WHERE instructor_id=$1)`, id); err != nil || !found {
		return false, nil, err
	}
	association, err := t.ownerAssociation(ctx, id)
	return true, association, err
}

func (t *instructorHoursTransaction) ownerAssociation(ctx context.Context, instructor string) (*string, error) {
	var association string
	err := t.tx.GetContext(ctx, &association, instructorOwnerAssociation, instructor)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &association, nil
}

const instructorHourColumns = `h.instructor_hours_id, h.instructor_id, h.creation_date, h.date::text AS date, h.compensation_type,
 h.hours::text AS hours, h.hourly_billing::text AS hourly_billing, h.percentage_billing::text AS percentage_billing,
 h.courses::text AS courses, h.period, h.amount::text AS amount, h.paid, h.payment_id, h.notes,
 h.calculation_data::text AS calculation_data, h.document_id`

func (t *instructorHoursTransaction) OwnedHour(ctx context.Context, owner, instructor, id string) (*domain.InstructorHour, error) {
	var row domain.InstructorHour
	err := t.tx.GetContext(ctx, &row, `SELECT `+instructorHourColumns+` FROM application_instructorhours h
 WHERE h.instructor_hours_id=$3 AND h.instructor_id=(SELECT instructor_id FROM application_instructor
  WHERE instructor_id=$2 AND user_id=$1 ORDER BY instructor_id LIMIT 1) FOR UPDATE`, owner, instructor, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// instructorHoursWhere is the list's queryset; icontains is Django's
// UPPER(column::text) LIKE UPPER(pattern) with the pattern escaped.
func instructorHoursWhere(query domain.InstructorHoursQuery) (string, []any) {
	where := ` FROM application_instructorhours h JOIN application_instructor i ON i.instructor_id=h.instructor_id
 WHERE i.user_id=$1 AND h.instructor_id=$2`
	args := []any{query.Owner, query.Instructor}
	if query.From != nil {
		args = append(args, *query.From, *query.To)
		where += ` AND h.date BETWEEN $3::date AND $4::date`
	}
	if query.Search != nil {
		args = append(args, "%"+strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(*query.Search)+"%")
		n := "$" + strconv.Itoa(len(args))
		where += ` AND (UPPER(h.date::text) LIKE UPPER(` + n + `) OR UPPER(h.hours::text) LIKE UPPER(` + n + `)
 OR UPPER(h.amount::text) LIKE UPPER(` + n + `) OR UPPER(h.notes::text) LIKE UPPER(` + n + `))`
	}
	return where, args
}

func (t *instructorHoursTransaction) CountHours(ctx context.Context, query domain.InstructorHoursQuery) (int64, error) {
	where, args := instructorHoursWhere(query)
	var count int64
	err := t.tx.GetContext(ctx, &count, `SELECT count(*)`+where, args...)
	return count, err
}

func (t *instructorHoursTransaction) Hours(ctx context.Context, query domain.InstructorHoursQuery, limit, offset int64) ([]domain.InstructorHourListed, error) {
	where, args := instructorHoursWhere(query)
	rows := []domain.InstructorHourListed{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+instructorHourColumns+`, d.token::text AS document_token, p.paid AS payment_paid
 FROM (SELECT h.instructor_hours_id`+where+` ORDER BY h.date DESC, h.creation_date DESC LIMIT `+strconv.FormatInt(limit, 10)+` OFFSET `+strconv.FormatInt(offset, 10)+`) page
 JOIN application_instructorhours h ON h.instructor_hours_id=page.instructor_hours_id
 LEFT JOIN docmanager_document d ON d.document_id=h.document_id LEFT JOIN application_payment p ON p.payment_id=h.payment_id
 ORDER BY h.date DESC, h.creation_date DESC`, args...)
	return rows, err
}

func (t *instructorHoursTransaction) HourIDTaken(ctx context.Context, id string, exclude *string) (bool, error) {
	var taken bool
	err := t.tx.GetContext(ctx, &taken, `SELECT EXISTS(SELECT 1 FROM application_instructorhours
 WHERE instructor_hours_id=$1 AND ($2::uuid IS NULL OR instructor_hours_id<>$2::uuid))`, id, exclude)
	return taken, err
}

func (t *instructorHoursTransaction) VisiblePayment(ctx context.Context, id string, group *string) (*domain.InstructorHoursPayment, error) {
	var row domain.InstructorHoursPayment
	err := t.tx.GetContext(ctx, &row, `SELECT payment_id, sport_association_id FROM application_payment
 WHERE payment_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)`, id, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *instructorHoursTransaction) DocumentExists(ctx context.Context, id string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM docmanager_document WHERE document_id=$1)`, id)
	return found, err
}

func (t *instructorHoursTransaction) FirstAssociation(ctx context.Context, user string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

func instructorHourArgs(row domain.InstructorHour) []any {
	return []any{row.ID, row.InstructorID, row.CreationDate, row.Date, row.CompensationType, row.Hours, row.HourlyBilling,
		row.PercentageBilling, row.Courses, row.Period, row.Amount, row.Paid, row.PaymentID, row.Notes, row.CalculationData, row.DocumentID}
}

func (t *instructorHoursTransaction) InsertHour(ctx context.Context, row domain.InstructorHour) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_instructorhours (instructor_hours_id, instructor_id, creation_date, date,
 compensation_type, hours, hourly_billing, percentage_billing, courses, period, amount, paid, payment_id, notes, calculation_data, document_id)
 VALUES ($1,$2,$3,$4::date,$5,$6::numeric,$7::numeric,$8::numeric,$9::jsonb,$10,$11::numeric,$12,$13,$14,$15::jsonb,$16)`,
		instructorHourArgs(row)...)
	return err
}

func (t *instructorHoursTransaction) UpdateHour(ctx context.Context, row domain.InstructorHour) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_instructorhours SET instructor_id=$2, creation_date=$3, date=$4::date,
 compensation_type=$5, hours=$6::numeric, hourly_billing=$7::numeric, percentage_billing=$8::numeric, courses=$9::jsonb, period=$10,
 amount=$11::numeric, paid=$12, payment_id=$13, notes=$14, calculation_data=$15::jsonb, document_id=$16 WHERE instructor_hours_id=$1`,
		instructorHourArgs(row)...)
	return err
}

func (t *instructorHoursTransaction) MarkPaid(ctx context.Context, id string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_instructorhours SET paid=true WHERE instructor_hours_id=$1`, id)
	return err
}

func (t *instructorHoursTransaction) CreatedChanges(ctx context.Context, id string) (map[string][2]string, error) {
	return createdModelChanges(ctx, t.tx, "application_instructorhours", "instructor_hours_id", id)
}

func (t *instructorHoursTransaction) DeletedChanges(ctx context.Context, id string) (map[string][2]string, error) {
	changes, _, err := storedModelChanges(ctx, t.tx, "application_instructorhours", "instructor_hours_id", id, false, true)
	return changes, err
}

func (t *instructorHoursTransaction) DeleteHour(ctx context.Context, id string) error {
	_, err := t.tx.ExecContext(ctx, `DELETE FROM application_instructorhours WHERE instructor_hours_id=$1`, id)
	return err
}

type documentReference struct {
	Table  string `db:"table_name"`
	Column string `db:"column_name"`
}

func (t *instructorHoursTransaction) documentReferences(ctx context.Context) ([]documentReference, error) {
	references := []documentReference{}
	err := t.tx.SelectContext(ctx, &references, `SELECT c.conrelid::regclass::text AS table_name, a.attname AS column_name
 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1]
 WHERE c.contype='f' AND c.confrelid='docmanager_document'::regclass AND array_length(c.conkey, 1)=1`)
	return references, err
}

func (t *instructorHoursTransaction) DocumentDeletable(ctx context.Context, id string) (bool, error) {
	references, err := t.documentReferences(ctx)
	if err != nil {
		return false, err
	}
	for _, ref := range references {
		if documentSetNull[ref.Table+"."+ref.Column] {
			continue
		}
		var referenced bool
		if err := t.tx.GetContext(ctx, &referenced, `SELECT EXISTS(SELECT 1 FROM `+ref.Table+` WHERE `+ref.Column+`=$1)`, id); err != nil {
			return false, err
		}
		if referenced {
			return false, nil
		}
	}
	return true, nil
}

// instructorHoursDocumentReverse are Document's reverse foreign keys that
// declare a related_name; auditlog renders their empty manager on delete.
var instructorHoursDocumentReverse = map[string]string{"customer_invoice_pdf": "application.CustomerInvoice.None"}

func (t *instructorHoursTransaction) DeleteDocument(ctx context.Context, id string) (map[string][2]string, error) {
	references, err := t.documentReferences(ctx)
	if err != nil {
		return nil, err
	}
	for _, ref := range references {
		if !documentSetNull[ref.Table+"."+ref.Column] {
			continue
		}
		if _, err := t.tx.ExecContext(ctx, `UPDATE `+ref.Table+` SET `+ref.Column+`=NULL WHERE `+ref.Column+`=$1`, id); err != nil {
			return nil, err
		}
	}
	changes, _, err := deleteModelWithAuditReverse(ctx, t.tx, "docmanager_document", "document_id", id, instructorHoursDocumentReverse)
	return changes, err
}

func (t *instructorHoursTransaction) AppendAudit(ctx context.Context, event domain.InstructorHoursAudit, association *string,
	audit domain.LoginAuditContext, at time.Time) error {
	entry := modelAuditEntry{Model: lowerASCII(event.Model), Key: event.Key, Representation: event.Model + " object (" + event.Key + ")",
		Action: event.Action, Changes: event.Changes, Context: audit, At: at}
	if event.Instructor != nil {
		// instructor_hours_resolver: the instructor's owner's association.
		owned, err := t.ownerAssociation(ctx, *event.Instructor)
		if err != nil {
			return err
		}
		if owned != nil {
			path := "instructor.user.sport_association"
			entry.AssociationID, entry.ResolutionPath = owned, &path
			return appendModelAuditForApp(ctx, t.tx, "application", entry)
		}
	}
	if association != nil {
		path := "actor.sport_association"
		entry.AssociationID, entry.ResolutionPath = association, &path
	}
	app := "application"
	if event.Model == "Document" {
		app = "docmanager"
	}
	return appendModelAuditForApp(ctx, t.tx, app, entry)
}

func (r *InstructorHours) Courses(ctx context.Context, ids []string, owner string, group *string) ([]domain.InstructorHoursCourse, error) {
	rows := []domain.InstructorHoursCourse{}
	err := r.db.SelectContext(ctx, &rows, `SELECT course_id, title, course_type, multi_payments FROM application_course
 WHERE course_id=ANY($1::uuid[]) AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)
 AND sport_association_id IN (SELECT sport_association_id FROM application_sportassociation WHERE user_id=$3::uuid)
 ORDER BY course_id`, ids, group, owner)
	return rows, err
}

func (r *InstructorHours) CourseSubscriptions(ctx context.Context, course string) ([]domain.InstructorHoursSubscription, error) {
	rows := []domain.InstructorHoursSubscription{}
	err := r.db.SelectContext(ctx, &rows, `SELECT cs.course_subscription_id, a.associate_id IS NOT NULL AS has_associate, a.first_name, a.last_name,
 cs.payment_id, p.paid AS payment_paid, p.amount::text AS payment_amount, p.payment_date
 FROM application_coursesubscription cs LEFT JOIN application_subscription s ON s.subscription_id=cs.subscription_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id LEFT JOIN application_payment p ON p.payment_id=cs.payment_id
 WHERE cs.course_id=$1 AND NOT cs.deleted ORDER BY cs.course_subscription_id`, course)
	return rows, err
}

func (r *InstructorHours) MembershipPayments(ctx context.Context, subscription string, from, to time.Time, group *string) ([]domain.InstructorHoursPaid, error) {
	rows := []domain.InstructorHoursPaid{}
	err := r.db.SelectContext(ctx, &rows, `SELECT NULL::text AS course_subscription_installment_id, p.amount::text AS amount, p.payment_date
 FROM application_coursesubscription_membership_payments m JOIN application_payment p ON p.payment_id=m.payment_id
 WHERE m.coursesubscription_id=$1 AND NOT p.deleted AND ($4::uuid IS NULL OR p.group_id=$4::uuid) AND p.paid
 AND p.payment_date BETWEEN $2 AND $3 ORDER BY m.id`, subscription, from, to, group)
	return rows, err
}

func (r *InstructorHours) InstallmentPayments(ctx context.Context, subscription string, from, to time.Time, group *string) ([]domain.InstructorHoursPaid, error) {
	rows := []domain.InstructorHoursPaid{}
	err := r.db.SelectContext(ctx, &rows, `SELECT i.course_subscription_installment_id::text AS course_subscription_installment_id,
 p.amount::text AS amount, p.payment_date
 FROM application_coursesubscriptioninstallment i JOIN application_payment p ON p.payment_id=i.payment_id
 WHERE i.course_subscription_id=$1 AND NOT i.deleted AND ($4::uuid IS NULL OR i.group_id=$4::uuid) AND p.paid
 AND p.payment_date BETWEEN $2 AND $3 ORDER BY i.course_subscription_installment_id`, subscription, from, to, group)
	return rows, err
}
