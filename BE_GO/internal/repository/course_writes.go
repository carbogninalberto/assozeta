package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/jmoiron/sqlx"
)

// CourseWrites serves course add, update and delete and the course-overview
// update and delete; it reuses the course subscription collector helpers.
type CourseWrites struct{ db *sqlx.DB }

func NewCourseWrites(db *sqlx.DB) *CourseWrites { return &CourseWrites{db: db} }

type courseWritesTransaction struct{ *courseSubscriptionTransaction }

func (r *CourseWrites) BeginCourseWrites(ctx context.Context) (domain.CourseWritesTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &courseWritesTransaction{courseSubscriptionTransaction: &courseSubscriptionTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

func courseWritesFirst[T any](ctx context.Context, tx *sqlx.Tx, query string, args ...any) (*T, error) {
	var row T
	err := tx.GetContext(ctx, &row, query, args...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *courseWritesTransaction) OwnerAssociationFirst(ctx context.Context, owner string) (*string, error) {
	return courseWritesFirst[string](ctx, t.tx, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, owner)
}

const courseWritesColumns = `course_id, sport_association_id, status_flag, course_type, creation_date, title, description, multi_payments,
 fee::text AS fee, events::text AS events, one_fee_payment, one_fee::text AS one_fee, google_calendar_id, google_background_color,
 multiple_quotes::text AS multiple_quotes, start_date, end_date, pinned, billed_duration_is_sport_season, billed_frequency,
 billed_from_subscription_date, billed_from_day_of_month, auto_renewal, deleted, group_id`

func (t *courseWritesTransaction) LiveCourse(ctx context.Context, id string, group *string) (*domain.CourseWriteRow, error) {
	return courseWritesFirst[domain.CourseWriteRow](ctx, t.tx, `SELECT `+courseWritesColumns+` FROM application_course
 WHERE course_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid) ORDER BY course_id LIMIT 1 FOR UPDATE`, id, group)
}

func (t *courseWritesTransaction) UserFullInstallmentsPlan(ctx context.Context, user string) (bool, error) {
	var plan bool
	err := t.tx.GetContext(ctx, &plan, `SELECT full_installments_plan FROM bakney_user WHERE user_id=$1`, user)
	return plan, err
}

// DefaultBankAccount is Payment.get_default_bank_account(association).
func (t *courseWritesTransaction) DefaultBankAccount(ctx context.Context, association *string) (*string, error) {
	// CustomAccounts has no ordering: first() orders by the primary key.
	return courseWritesFirst[string](ctx, t.tx, `SELECT custom_account_id FROM application_customaccounts
 WHERE sport_association_id IS NOT DISTINCT FROM $1::uuid AND NOT editable AND enabled AND account_type=2
 ORDER BY custom_account_id LIMIT 1`, association)
}

func (t *courseWritesTransaction) GroupExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_group WHERE group_id=$1::uuid)`, id)
	return exists, err
}

func (t *courseWritesTransaction) snapshots(ctx context.Context, table, key string, ids []string) ([]*courseSubscriptionSnapshot, error) {
	result := make([]*courseSubscriptionSnapshot, 0, len(ids))
	for _, id := range ids {
		snapshot, err := t.snapshot(ctx, table, key, id)
		if err != nil {
			return nil, err
		}
		if snapshot == nil {
			return nil, domain.ErrCourseWritesServer
		}
		result = append(result, snapshot)
	}
	return result, nil
}

func (t *courseWritesTransaction) keys(ctx context.Context, query string, args ...any) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, query, args...)
	return ids, err
}

// auditDeleted appends post_delete entries in reverse order; rows that no
// longer resolve and carry no association are indexed through the actor.
func (t *courseWritesTransaction) auditDeleted(ctx context.Context, model, name string, snapshots []*courseSubscriptionSnapshot,
	representation func(int) string, audit domain.CourseSubscriptionAudit) error {
	for index := len(snapshots) - 1; index >= 0; index-- {
		text := ""
		if representation != nil {
			text = representation(index)
		}
		if err := appendModelAudit(ctx, t.tx, courseSubscriptionEntry(model, name, snapshots[index].id, text, 2, snapshots[index].changes, nil, "",
			audit)); err != nil {
			return err
		}
	}
	return nil
}

// DeleteCourse is course.delete(): the collector gathers the course
// subscriptions (base manager, key order) and their installments, the
// attendance registries and their days; sorted for deletion they are
// installments, days, course subscriptions, registries, the course. The
// pre_delete signals run in that order (installments: the unpaid payment is
// deleted; course subscriptions: delete_related_course_payment), the
// many-to-many rows go, then each model's rows are deleted and audited in
// reverse key order.
func (t *courseWritesTransaction) DeleteCourse(ctx context.Context, course domain.CourseWriteRow, audit domain.CourseSubscriptionAudit) error {
	rows := []domain.CourseSubscriptionRow{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT `+courseSubscriptionColumns+` FROM application_coursesubscription cs
 JOIN application_course c ON c.course_id=cs.course_id WHERE cs.course_id=$1 ORDER BY cs.course_subscription_id FOR UPDATE OF cs`,
		course.ID); err != nil {
		return err
	}
	keys := make([]string, len(rows))
	for index, row := range rows {
		keys[index] = row.ID
	}
	rowSnapshots, err := t.snapshots(ctx, "application_coursesubscription", "course_subscription_id", keys)
	if err != nil {
		return err
	}
	installmentIDs, err := t.keys(ctx, `SELECT course_subscription_installment_id FROM application_coursesubscriptioninstallment
 WHERE course_subscription_id=ANY($1::uuid[]) ORDER BY course_subscription_installment_id FOR UPDATE`, keys)
	if err != nil {
		return err
	}
	installments, err := t.snapshots(ctx, "application_coursesubscriptioninstallment", "course_subscription_installment_id", installmentIDs)
	if err != nil {
		return err
	}
	registryIDs, err := t.keys(ctx, `SELECT attendance_registry_id FROM application_attendanceregistry WHERE course_id=$1
 ORDER BY attendance_registry_id FOR UPDATE`, course.ID)
	if err != nil {
		return err
	}
	registries, err := t.snapshots(ctx, "application_attendanceregistry", "attendance_registry_id", registryIDs)
	if err != nil {
		return err
	}
	dayIDs, err := t.keys(ctx, `SELECT attendance_day_id FROM application_attendanceday WHERE attendance_registry_id=ANY($1::uuid[])
 ORDER BY attendance_day_id FOR UPDATE`, registryIDs)
	if err != nil {
		return err
	}
	days, err := t.snapshots(ctx, "application_attendanceday", "attendance_day_id", dayIDs)
	if err != nil {
		return err
	}
	courseSnapshot, err := t.snapshot(ctx, "application_course", "course_id", course.ID)
	if err != nil {
		return err
	}
	if courseSnapshot == nil {
		return domain.ErrCourseWritesServer
	}
	// pre_delete: installments (delete_related_payment).
	for _, installment := range installments {
		payment := storedText(installment.stored, "payment_id")
		if payment == nil {
			continue
		}
		current, err := t.snapshot(ctx, "application_payment", "payment_id", *payment)
		if err != nil {
			return err
		}
		if current != nil && current.stored["paid"] != true {
			if err = t.deletePayments(ctx, []*courseSubscriptionSnapshot{current}, true, audit); err != nil {
				return err
			}
		}
	}
	// pre_delete: course subscriptions (delete_related_course_payment).
	for _, row := range rows {
		if row.PaymentID != nil {
			// Payment.objects.get(id=...) raises FieldError, logged.
			continue
		}
		if row.Type == 3 {
			unpaid, err := t.MembershipPaymentsWhere(ctx, row.ID, audit.Group, true, nil, 0)
			if err != nil {
				return err
			}
			payments := []*courseSubscriptionSnapshot{}
			for _, payment := range unpaid {
				snapshot, err := t.snapshot(ctx, "application_payment", "payment_id", payment.ID)
				if err != nil {
					return err
				}
				if snapshot != nil {
					payments = append(payments, snapshot)
				}
			}
			if err = t.deletePayments(ctx, payments, true, audit); err != nil {
				return err
			}
		}
		if err := t.courseSubscriptionTransaction.DeleteInstallmentsOf(ctx, row.ID, audit); err != nil {
			return err
		}
	}
	for _, statement := range []struct {
		query string
		arg   any
	}{
		{`DELETE FROM application_course_tags WHERE course_id=$1`, course.ID},
		{`DELETE FROM application_course_locations WHERE course_id=$1`, course.ID},
		{`DELETE FROM application_coursesubscription_membership_payments WHERE coursesubscription_id=ANY($1::uuid[])`, keys},
		{`DELETE FROM application_carnetsubscription_course_subscription WHERE coursesubscription_id=ANY($1::uuid[])`, keys},
		{`DELETE FROM application_coursesubscriptioninstallment WHERE course_subscription_installment_id=ANY($1::uuid[])`, installmentIDs},
	} {
		if _, err := t.tx.ExecContext(ctx, statement.query, statement.arg); err != nil {
			return err
		}
	}
	if err = t.auditDeleted(ctx, "coursesubscriptioninstallment", "CourseSubscriptionInstallment", installments, nil, audit); err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `DELETE FROM application_attendanceday WHERE attendance_day_id=ANY($1::uuid[])`, dayIDs); err != nil {
		return err
	}
	if err = t.auditDeleted(ctx, "attendanceday", "AttendanceDay", days, nil, audit); err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `DELETE FROM application_coursesubscription WHERE course_subscription_id=ANY($1::uuid[])`, keys); err != nil {
		return err
	}
	if err = t.auditDeleted(ctx, "coursesubscription", "CourseSubscription", rowSnapshots, func(index int) string {
		return dto.CourseSubscriptionRepr(rows[index], dto.CourseSubscriptionMemory{})
	}, audit); err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `DELETE FROM application_attendanceregistry WHERE attendance_registry_id=ANY($1::uuid[])`,
		registryIDs); err != nil {
		return err
	}
	if err = t.auditDeleted(ctx, "attendanceregistry", "AttendanceRegistry", registries, nil, audit); err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `DELETE FROM application_course WHERE course_id=$1`, course.ID); err != nil {
		return err
	}
	association, err := t.liveAssociation(ctx, courseSnapshot.changes)
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("course", "Course", course.ID, "", 2, courseSnapshot.changes, association,
		"changes_json.sport_association", audit))
}

// --- update ---------------------------------------------------------------------

func (t *courseWritesTransaction) LinkedLocations(ctx context.Context, course string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT courselocation_id FROM application_course_locations WHERE course_id=$1::uuid
 ORDER BY courselocation_id`, course)
	return ids, err
}

func (t *courseWritesTransaction) LocationLinkable(ctx context.Context, id, association string, linked []string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_courselocation WHERE course_location_id=$1::uuid
 AND (sport_association_id=$2::uuid OR course_location_id=ANY($3::uuid[])))`, id, association, uuidArray(linked))
	return exists, err
}

func (t *courseWritesTransaction) ClearLocations(ctx context.Context, course string) error {
	_, err := t.tx.ExecContext(ctx, `DELETE FROM application_course_locations WHERE course_id=$1`, course)
	return err
}

func (t *courseWritesTransaction) AddLocation(ctx context.Context, course, location string) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_course_locations (course_id, courselocation_id)
 SELECT $1::uuid, $2::uuid WHERE NOT EXISTS (SELECT 1 FROM application_course_locations WHERE course_id=$1::uuid AND courselocation_id=$2::uuid)`,
		course, location)
	return err
}

// courseIndex is the Course entry's index: the stored association (direct),
// otherwise the actor's.
func courseIndex(row domain.CourseWriteRow, audit domain.CourseSubscriptionAudit) (*string, string) {
	if row.AssociationID != nil {
		return row.AssociationID, "direct"
	}
	return nil, ""
}

// SaveCourse is course_update's Course.save(): every assigned column is
// written; auditlog's entry (if any) is indexed through the stored association.
func (t *courseWritesTransaction) SaveCourse(ctx context.Context, save domain.CourseWriteCourseSave, audit domain.CourseSubscriptionAudit) error {
	row := save.Row
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_course SET title=$2, description=$3, one_fee=$4::numeric, fee=$5::numeric,
 multiple_quotes=$6::jsonb, events=$7::jsonb, billed_duration_is_sport_season=$8, billed_frequency=$9, billed_from_subscription_date=$10,
 billed_from_day_of_month=$11, auto_renewal=$12, start_date=$13, end_date=$14 WHERE course_id=$1`, row.ID, row.Title, row.Description, row.OneFee,
		row.Fee, row.MultipleQuotes, row.Events, row.SportSeason, row.BilledFrequency, row.FromSubscription, row.BilledDayOfMonth, row.AutoRenewal,
		row.StartDate, row.EndDate); err != nil {
		return err
	}
	if len(save.Changes) == 0 {
		return nil
	}
	association, path := courseIndex(row, audit)
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("course", "Course", row.ID, "", 1, save.Changes, association, path, audit))
}

func (t *courseWritesTransaction) TitlePayments(ctx context.Context, title, association string, group *string) ([]domain.CourseWriteTitlePayment, error) {
	pattern := "%" + strings.NewReplacer(`\`, `\\`, "%", `\%`, "_", `\_`).Replace(title) + "%"
	rows := []domain.CourseWriteTitlePayment{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT payment_id, meta::text AS meta, description FROM application_payment
 WHERE NOT deleted AND sport_association_id=$3::uuid AND ($2::uuid IS NULL OR group_id=$2::uuid)
 AND (UPPER(meta::text) LIKE UPPER($1) OR UPPER(description::text) LIKE UPPER($1))
 ORDER BY payment_id FOR UPDATE`, pattern, group, association)
	return rows, err
}

func (t *courseWritesTransaction) RewritePayment(ctx context.Context, id string, meta *string, description *string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET meta=$2::jsonb, description=$3 WHERE payment_id=$1`, id, meta, description)
	return err
}

// --- add --------------------------------------------------------------------------

func (t *courseWritesTransaction) CreateCourse(ctx context.Context, row domain.CourseWriteRow, overrides map[string][2]string,
	audit domain.CourseSubscriptionAudit) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_course (course_id, sport_association_id, status_flag, course_type, creation_date,
 title, description, multi_payments, fee, events, one_fee_payment, one_fee, google_calendar_id, google_background_color, multiple_quotes,
 start_date, end_date, pinned, billed_duration_is_sport_season, billed_frequency, billed_from_subscription_date, billed_from_day_of_month,
 auto_renewal, deleted, group_id) VALUES ($1,NULL,$2,$3,$4,$5,$6,$7,$8::numeric,$9::jsonb,$10,$11::numeric,NULL,$12,$13::jsonb,NULL,NULL,false,
 $14,$15,$16,$17,$18,false,$19)`, row.ID, row.StatusFlag, row.CourseType, row.CreationDate, row.Title, row.Description, row.MultiPayments,
		row.Fee, row.Events, row.OneFeePayment, row.OneFee, row.Color, row.MultipleQuotes, row.SportSeason, row.BilledFrequency,
		row.FromSubscription, row.BilledDayOfMonth, row.AutoRenewal, row.GroupID); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_course", "course_id", row.ID)
	if err != nil {
		return err
	}
	for name, pair := range overrides {
		changes[name] = pair
	}
	// Course.objects.get resolves the course without an association: the actor's.
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("course", "Course", row.ID, "", 0, changes, nil, "", audit))
}

// SetCourseAssociation is the association save; auditlog writes before the
// UPDATE, so the entry resolves through its changes.
func (t *courseWritesTransaction) SetCourseAssociation(ctx context.Context, course, association string, changes map[string][2]string,
	audit domain.CourseSubscriptionAudit) error {
	// The resolver tries the old value, then the new one.
	resolved, err := t.liveAssociation(ctx, map[string][2]string{"sport_association": {changes["sport_association"][1], ""}})
	if err != nil {
		return err
	}
	if err = appendModelAudit(ctx, t.tx, courseSubscriptionEntry("course", "Course", course, "", 1, changes, resolved,
		"changes_json.sport_association", audit)); err != nil {
		return err
	}
	_, err = t.tx.ExecContext(ctx, `UPDATE application_course SET sport_association_id=$2 WHERE course_id=$1`, course, association)
	return err
}

// LiveSubscription is Subscription.objects (group-aware) by key.
func (t *courseWritesTransaction) LiveSubscription(ctx context.Context, id string, group *string) (*domain.CourseWriteSubscription, error) {
	return courseWritesFirst[domain.CourseWriteSubscription](ctx, t.tx, `SELECT subscription_id, sport_association_id, user_id, associate_id
 FROM application_subscription WHERE subscription_id=$1 AND ($2::uuid IS NULL OR group_id=$2::uuid)`, id, group)
}

// courseWritesFloatFee is CourseSubscription's in-memory float membership_fee default.
func courseWritesFloatFee(text string) string {
	return strings.Replace(text, "'membership_fee': Decimal('0.00')", "'membership_fee': 0.0", 1)
}

// CreateCourseSubscription is CourseSubscription.objects.create(course=...,
// subscription=..., multi_payments=...): the float membership_fee default
// is what auditlog records.
func (t *courseWritesTransaction) CreateCourseSubscription(ctx context.Context, row domain.CourseSubscriptionRow, audit domain.CourseSubscriptionAudit) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_coursesubscription (course_subscription_id, course_id, paid, creation_date,
 subscription_id, payment_id, multi_payments, one_fee_payment, archived, type, multiple_quote, billed_frequency, billed_from, billed_until,
 billed_from_day_of_month, membership_active, membership_fee, auto_renewal, deleted) VALUES ($1,$2,false,$3,$4,NULL,$5,false,false,1,NULL,1,NULL,
 NULL,1,true,0.00,false,false)`, row.ID, row.CourseID, row.CreationDate, row.SubscriptionID, row.MultiPayments); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_coursesubscription", "course_subscription_id", row.ID)
	if err != nil {
		return err
	}
	changes["membership_fee"] = [2]string{"None", "0.0"}
	fee := "0.00"
	row.MembershipFee = &fee
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscription", "CourseSubscription", row.ID,
		courseWritesFloatFee(dto.CourseSubscriptionRepr(row, dto.CourseSubscriptionMemory{})), 0, changes, row.CourseAssociationID,
		"course.sport_association", audit))
}

// CreatePayment is Payment.objects.create(...) (one create entry, indexed
// directly) followed by payment.save(), which records the in-memory amount
// when it renders differently from the stored one.
func (t *courseWritesTransaction) CreatePayment(ctx context.Context, payment domain.CourseWritePayment, audit domain.CourseSubscriptionAudit) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_payment (payment_id, type, subject, amount, creation_date, payment_date, paid,
 expense, description, payment_category_id, user_id, associate_id, sport_association_id, archived, meta, custom_accounts_id, deleted, group_id)
 VALUES ($1,'default',2,$2::numeric,$3,NULL,false,false,$4,$5,$6,$7,$8,false,$9::jsonb,$10,false,$11)`, payment.ID, payment.Amount,
		payment.CreationDate, payment.Description, payment.CategoryID, payment.UserID, payment.AssociateID, payment.AssociationID, payment.Meta,
		payment.AccountID, payment.GroupID); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_payment", "payment_id", payment.ID)
	if err != nil {
		return err
	}
	stored := changes["amount"][1]
	changes["amount"] = [2]string{"None", payment.MemoryAmount}
	if err = appendModelAudit(ctx, t.tx, courseSubscriptionEntry("payment", "Payment", payment.ID, "", 0, changes, payment.AssociationID, "direct",
		audit)); err != nil {
		return err
	}
	if stored == payment.MemoryAmount {
		return nil
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("payment", "Payment", payment.ID, "", 1,
		map[string][2]string{"amount": {stored, payment.MemoryAmount}}, payment.AssociationID, "direct", audit))
}

func (t *courseWritesTransaction) CreateInstallment(ctx context.Context, installment domain.CourseWriteInstallmentCreate,
	audit domain.CourseSubscriptionAudit) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_coursesubscriptioninstallment (course_subscription_installment_id,
 course_subscription_id, id, amount, paid, payment_date, payment_id, archived, group_id, deleted) VALUES ($1,$2,$3,$4::numeric,false,$5,$6,false,$7,false)`,
		installment.ID, installment.CourseSubscription, installment.Number, installment.Amount, installment.PaymentDate, installment.PaymentID,
		installment.GroupID); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_coursesubscriptioninstallment", "course_subscription_installment_id", installment.ID)
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscriptioninstallment", "CourseSubscriptionInstallment", installment.ID, "",
		0, changes, installment.AssociationID, "course_subscription.course.sport_association", audit))
}

// --- course-overview ----------------------------------------------------------------

func (t *courseWritesTransaction) LiveCourseSubscription(ctx context.Context, course string, subscription *string) (*domain.CourseSubscriptionRow, error) {
	return courseWritesFirst[domain.CourseSubscriptionRow](ctx, t.tx, `SELECT `+courseSubscriptionColumns+` FROM application_coursesubscription cs
 JOIN application_course c ON c.course_id=cs.course_id WHERE cs.course_id=$1 AND cs.subscription_id IS NOT DISTINCT FROM $2::uuid AND NOT cs.deleted
 ORDER BY cs.course_subscription_id LIMIT 1 FOR UPDATE OF cs`, course, subscription)
}

func (t *courseWritesTransaction) installment(ctx context.Context, id string) (domain.CourseWriteInstallment, error) {
	snapshot, err := t.snapshot(ctx, "application_coursesubscriptioninstallment", "course_subscription_installment_id", id)
	if err != nil {
		return domain.CourseWriteInstallment{}, err
	}
	if snapshot == nil {
		return domain.CourseWriteInstallment{}, domain.ErrCourseWritesServer
	}
	return domain.CourseWriteInstallment{ID: id, PaymentID: storedText(snapshot.stored, "payment_id"), Changes: snapshot.changes}, nil
}

func (t *courseWritesTransaction) LiveInstallment(ctx context.Context, row, id string, group *string) (*domain.CourseWriteInstallment, error) {
	ids, err := t.keys(ctx, `SELECT course_subscription_installment_id FROM application_coursesubscriptioninstallment
 WHERE course_subscription_id=$1 AND course_subscription_installment_id=$2::uuid AND NOT deleted AND ($3::uuid IS NULL OR group_id=$3::uuid)
 FOR UPDATE`, row, id, group)
	if err != nil || len(ids) == 0 {
		return nil, err
	}
	installment, err := t.installment(ctx, ids[0])
	return &installment, err
}

func (t *courseWritesTransaction) Installments(ctx context.Context, row string, group *string, payable bool) ([]domain.CourseWriteInstallment, error) {
	query := `SELECT i.course_subscription_installment_id FROM application_coursesubscriptioninstallment i
 WHERE i.course_subscription_id=$1 AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid) AND NOT i.paid
 ORDER BY i.course_subscription_installment_id FOR UPDATE`
	if payable {
		query = `SELECT i.course_subscription_installment_id FROM application_coursesubscriptioninstallment i
 LEFT JOIN application_payment p ON p.payment_id=i.payment_id
 WHERE i.course_subscription_id=$1 AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid) AND (p.paid=false OR i.payment_id IS NULL)
 ORDER BY i.course_subscription_installment_id FOR UPDATE OF i`
	}
	ids, err := t.keys(ctx, query, row, group)
	if err != nil {
		return nil, err
	}
	result := make([]domain.CourseWriteInstallment, 0, len(ids))
	for _, id := range ids {
		installment, err := t.installment(ctx, id)
		if err != nil {
			return nil, err
		}
		result = append(result, installment)
	}
	return result, nil
}

func (t *courseWritesTransaction) PaymentPaid(ctx context.Context, id string) (bool, bool, error) {
	paid, err := courseWritesFirst[bool](ctx, t.tx, `SELECT paid FROM application_payment WHERE payment_id=$1 FOR UPDATE`, id)
	if err != nil || paid == nil {
		return false, false, err
	}
	return true, *paid, nil
}

func (t *courseWritesTransaction) DeletePayment(ctx context.Context, id string, audit domain.CourseSubscriptionAudit) error {
	snapshot, err := t.snapshot(ctx, "application_payment", "payment_id", id)
	if err != nil || snapshot == nil {
		return err
	}
	return t.deletePayments(ctx, []*courseSubscriptionSnapshot{snapshot}, true, audit)
}

func (t *courseWritesTransaction) DeleteInstallment(ctx context.Context, installment domain.CourseWriteInstallment, overrides map[string][2]string,
	audit domain.CourseSubscriptionAudit) error {
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_coursesubscriptioninstallment WHERE course_subscription_installment_id=$1`,
		installment.ID); err != nil {
		return err
	}
	changes := make(map[string][2]string, len(installment.Changes))
	for name, pair := range installment.Changes {
		changes[name] = pair
	}
	for name, pair := range overrides {
		changes[name] = pair
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscriptioninstallment", "CourseSubscriptionInstallment", installment.ID, "",
		2, changes, nil, "", audit))
}

// courseSubscriptionAuditValues are auditlog's str() renderings of the
// fields of an instance (floatFee: the float membership_fee default).
func courseSubscriptionAuditValues(row domain.CourseSubscriptionRow, floatFee bool) map[string]string {
	text := func(value *string) string {
		if value == nil {
			return "None"
		}
		return *value
	}
	flag := func(value bool) string { return map[bool]string{true: "True", false: "False"}[value] }
	quote := "null"
	if row.MultipleQuote != nil {
		if parsed, err := dto.ParseReferenceJSON([]byte(*row.MultipleQuote)); err == nil {
			quote = dto.CourseWritesAuditJSON(parsed)
		}
	}
	fee := text(row.MembershipFee)
	if floatFee {
		fee = "0.0"
	}
	values := map[string]string{
		"course_subscription_id": row.ID, "course": row.CourseID, "paid": flag(row.Paid), "creation_date": dto.CourseWritesNaiveUTC(row.CreationDate),
		"subscription": text(row.SubscriptionID), "payment": text(row.PaymentID), "multi_payments": flag(row.MultiPayments),
		"one_fee_payment": flag(row.OneFeePayment), "archived": flag(row.Archived), "type": strconv.Itoa(row.Type), "multiple_quote": quote,
		"billed_frequency": strconv.Itoa(row.BilledFrequency), "billed_from": "None", "billed_until": "None",
		"billed_from_day_of_month": strconv.Itoa(row.BilledFromDayOfMonth), "membership_active": flag(row.MembershipActive), "membership_fee": fee,
		"auto_renewal": flag(row.AutoRenewal), "deleted": flag(row.Deleted),
	}
	if row.BilledFrom != nil {
		values["billed_from"] = dto.CourseWritesNaiveUTC(*row.BilledFrom)
	}
	if row.BilledUntil != nil {
		values["billed_until"] = dto.CourseWritesNaiveUTC(*row.BilledUntil)
	}
	return values
}

// SaveCourseSubscription is Model.save() of an in-memory instance: auditlog
// diffs it with the live row (written before the UPDATE, indexed through the
// course); a payment deleted meanwhile fails the deferred foreign key after
// the entry was written.
func (t *courseWritesTransaction) SaveCourseSubscription(ctx context.Context, save domain.CourseWriteSubscriptionSave,
	audit domain.CourseSubscriptionAudit) error {
	row := save.Row
	stored, err := courseWritesFirst[domain.CourseSubscriptionRow](ctx, t.tx, `SELECT `+courseSubscriptionColumns+`
 FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
 WHERE cs.course_subscription_id=$1 AND NOT cs.deleted FOR UPDATE OF cs`, row.ID)
	if err != nil {
		return err
	}
	if stored != nil {
		before, after := courseSubscriptionAuditValues(*stored, false), courseSubscriptionAuditValues(row, save.FloatFee)
		changes := map[string][2]string{}
		for name, value := range after {
			if before[name] != value {
				changes[name] = [2]string{before[name], value}
			}
		}
		if len(changes) > 0 {
			representation := dto.CourseSubscriptionRepr(row, dto.CourseSubscriptionMemory{})
			if save.FloatFee {
				representation = courseWritesFloatFee(representation)
			}
			if err = appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscription", "CourseSubscription", row.ID, representation, 1,
				changes, stored.CourseAssociationID, "course.sport_association", audit)); err != nil {
				return err
			}
		}
	}
	if row.PaymentID != nil {
		exists, _, err := t.PaymentPaid(ctx, *row.PaymentID)
		if err != nil {
			return err
		}
		if !exists {
			return domain.ErrCourseWritesStalePayment
		}
	}
	_, err = t.tx.ExecContext(ctx, `UPDATE application_coursesubscription SET course_id=$2, paid=$3, creation_date=$4, subscription_id=$5,
 payment_id=$6, multi_payments=$7, one_fee_payment=$8, archived=$9, type=$10, multiple_quote=$11::jsonb, billed_frequency=$12, billed_from=$13,
 billed_until=$14, billed_from_day_of_month=$15, membership_active=$16, membership_fee=$17::numeric, auto_renewal=$18, deleted=$19
 WHERE course_subscription_id=$1`, row.ID, row.CourseID, row.Paid, row.CreationDate, row.SubscriptionID, row.PaymentID, row.MultiPayments,
		row.OneFeePayment, row.Archived, row.Type, row.MultipleQuote, row.BilledFrequency, row.BilledFrom, row.BilledUntil, row.BilledFromDayOfMonth,
		row.MembershipActive, row.MembershipFee, row.AutoRenewal, row.Deleted)
	return err
}
