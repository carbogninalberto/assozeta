package repository

import (
	"context"
	"database/sql"
	"errors"
	"sort"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/jmoiron/sqlx"
)

type CourseSubscriptions struct{ db *sqlx.DB }

func NewCourseSubscriptions(db *sqlx.DB) *CourseSubscriptions { return &CourseSubscriptions{db: db} }

type courseSubscriptionTransaction struct{ *paymentCategoryTransaction }

func (r *CourseSubscriptions) BeginCourseSubscription(ctx context.Context) (domain.CourseSubscriptionTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &courseSubscriptionTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *courseSubscriptionTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

// ActorAssociation is SportAssociation.objects.filter(user=actor).first()
// (ActiveManager), the audit index fallback.
func (t *courseSubscriptionTransaction) ActorAssociation(ctx context.Context, actor string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, actor)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &id, err
}

const courseSubscriptionColumns = `cs.course_subscription_id, cs.course_id, cs.paid, cs.creation_date, cs.subscription_id, cs.payment_id,
 cs.multi_payments, cs.one_fee_payment, cs.archived, cs.type, cs.multiple_quote::text AS multiple_quote, cs.billed_frequency, cs.billed_from,
 cs.billed_until, cs.billed_from_day_of_month, cs.membership_active, cs.membership_fee::text AS membership_fee, cs.auto_renewal, cs.deleted,
 c.sport_association_id AS course_association_id, c.title AS course_title, c.course_type`

// CourseSubscriptions is get_queryset() (course__sport_association joins the
// course table directly: deleted and grouped courses included) with
// scoped_queryset()'s subscription and course boundary.
func (t *courseSubscriptionTransaction) CourseSubscriptions(ctx context.Context, query domain.CourseSubscriptionQuery) ([]domain.CourseSubscriptionRow, error) {
	rows := []domain.CourseSubscriptionRow{}
	statement := `SELECT ` + courseSubscriptionColumns + ` FROM application_coursesubscription cs
 JOIN application_course c ON c.course_id=cs.course_id
 LEFT JOIN application_subscription s ON s.subscription_id=cs.subscription_id
 WHERE c.sport_association_id=$1 AND ($2 OR NOT cs.deleted) AND ($3::uuid IS NULL OR cs.course_id=$3::uuid)
 AND ($4::uuid IS NULL OR cs.subscription_id=$4::uuid) AND ($5::uuid IS NULL OR cs.course_subscription_id=$5::uuid)
 AND ($6::uuid IS NULL OR (s.sport_association_id=$6::uuid AND c.sport_association_id=$6::uuid))
 AND ($7::uuid[] IS NULL OR cs.course_subscription_id=ANY($7::uuid[])) ORDER BY cs.course_subscription_id`
	if query.Lock {
		statement += ` FOR UPDATE OF cs`
	}
	var ids any
	if query.IDs != nil {
		ids = query.IDs
	}
	err := t.tx.SelectContext(ctx, &rows, statement, query.AssociationID, query.IncludeDeleted, query.CourseID, query.SubscriptionID, query.ID, query.Scope,
		ids)
	return rows, err
}

const courseSubscriptionAssociateColumns = `a.associate_id, a.address, a.address_cap, a.address_city, a.born_city, a.born_date, a.email, a.sex,
 a.first_name, a.last_name, a.is_minor, a.sport_association_id, a.tax_code, a.user_id, asa.user_id AS association_user_id, a.phone,
 a.family_id, a.family_role`

func (t *courseSubscriptionTransaction) associates(ctx context.Context, ids []string) (map[string]domain.InconsistencyAssociate, error) {
	rows := []domain.InconsistencyAssociate{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT `+courseSubscriptionAssociateColumns+` FROM application_associate a
 LEFT JOIN application_sportassociation asa ON asa.sport_association_id=a.sport_association_id WHERE a.associate_id=ANY($1::uuid[])`, ids); err != nil {
		return nil, err
	}
	result := make(map[string]domain.InconsistencyAssociate, len(rows))
	for _, row := range rows {
		result[row.ID] = row
	}
	return result, nil
}

// People loads the subscriptions through the forward relation (base
// manager) with their medical certificate and associate.
func (t *courseSubscriptionTransaction) People(ctx context.Context, ids []string) (map[string]domain.CourseSubscriptionPerson, error) {
	rows := []domain.CourseSubscriptionPerson{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT s.subscription_id, s.start_date, s.end_date, s.creation_date, s.medical_id,
 m.expiration_date, m.competitive_medical_certificate, s.associate_id FROM application_subscription s
 LEFT JOIN application_medicalcertificate m ON m.medical_id=s.medical_id WHERE s.subscription_id=ANY($1::uuid[])`, ids); err != nil {
		return nil, err
	}
	associateIDs := []string{}
	for _, row := range rows {
		if row.AssociateID != nil {
			associateIDs = append(associateIDs, *row.AssociateID)
		}
	}
	associates, err := t.associates(ctx, associateIDs)
	if err != nil {
		return nil, err
	}
	result := make(map[string]domain.CourseSubscriptionPerson, len(rows))
	for _, row := range rows {
		if row.AssociateID != nil {
			if associate, ok := associates[*row.AssociateID]; ok {
				row.Associate = &associate
			}
		}
		result[row.ID] = row
	}
	return result, nil
}

// Installments is CourseSubscriptionInstallment.objects (live, group-aware)
// for the rows; paid is payment.paid through the forward relation.
func (t *courseSubscriptionTransaction) Installments(ctx context.Context, rows []string, group *string) ([]domain.CourseSubscriptionInstallmentItem, error) {
	items := []domain.CourseSubscriptionInstallmentItem{}
	err := t.tx.SelectContext(ctx, &items, `SELECT i.course_subscription_installment_id, i.course_subscription_id, i.id, i.amount::text AS amount,
 i.payment_date, COALESCE(p.paid, false) AS paid FROM application_coursesubscriptioninstallment i
 LEFT JOIN application_payment p ON p.payment_id=i.payment_id
 WHERE i.course_subscription_id=ANY($1::uuid[]) AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid)
 ORDER BY i.course_subscription_installment_id`, rows, group)
	return items, err
}

// MembershipPayments renders the rows' membership payments (PaymentManager:
// live, group-aware) with what PaymentSerializer reads.
func (t *courseSubscriptionTransaction) MembershipPayments(ctx context.Context, rows []string, group *string) (map[string][]domain.CourseSubscriptionPayment, error) {
	links := []struct {
		Row     string `db:"coursesubscription_id"`
		Payment string `db:"payment_id"`
	}{}
	if err := t.tx.SelectContext(ctx, &links, `SELECT l.coursesubscription_id, l.payment_id FROM application_coursesubscription_membership_payments l
 JOIN application_payment p ON p.payment_id=l.payment_id
 WHERE l.coursesubscription_id=ANY($1::uuid[]) AND NOT p.deleted AND ($2::uuid IS NULL OR p.group_id=$2::uuid) ORDER BY l.id`, rows, group); err != nil {
		return nil, err
	}
	result := map[string][]domain.CourseSubscriptionPayment{}
	for _, link := range links {
		payment, err := t.payment(ctx, link.Payment, group)
		if err != nil {
			return nil, err
		}
		if payment.Instructor, payment.Invoice, err = paymentNestedRows(ctx, t.tx, payment.InvoicePartyPayment); err != nil {
			return nil, err
		}
		result[link.Row] = append(result[link.Row], payment)
	}
	return result, nil
}

func (t *courseSubscriptionTransaction) payment(ctx context.Context, id string, group *string) (domain.CourseSubscriptionPayment, error) {
	var row domain.CourseSubscriptionPayment
	if err := t.tx.GetContext(ctx, &row.InvoicePartyPayment, `SELECT `+invoicePartyPaymentColumns+` FROM application_payment p
 LEFT JOIN application_customaccounts a ON a.custom_account_id=p.custom_accounts_id
 LEFT JOIN application_paymentcategory c ON c.payment_category_id=p.payment_category_id WHERE p.payment_id=$1`, id); err != nil {
		return row, err
	}
	payment := &row.InvoicePartyPayment
	if payment.UserID != nil {
		var user domain.InvoicePartyUser
		if err := t.tx.GetContext(ctx, &user, `SELECT first_name, last_name, email, phone, username, role FROM bakney_user WHERE user_id=$1`,
			*payment.UserID); err != nil {
			return row, err
		}
		payment.User = &user
	}
	if payment.SupplierID != nil {
		var supplier domain.Supplier
		err := t.tx.GetContext(ctx, &supplier, `SELECT `+supplierColumns+` FROM application_supplierandcustomers WHERE supplier_id=$1`, *payment.SupplierID)
		if err != nil && !errors.Is(err, sql.ErrNoRows) {
			return row, err
		}
		if err == nil {
			payment.Supplier = &supplier
		}
	}
	payment.Attachments = []domain.InvoicePartyDocument{}
	if err := t.tx.SelectContext(ctx, &payment.Attachments, `SELECT d.document_id, d.creation_date, d.filepath, d.filename,
 d.file_size_bytes, d.token::text AS token FROM application_payment_attachments l JOIN docmanager_document d ON d.document_id=l.document_id
 WHERE l.payment_id=$1 ORDER BY l.id`, id); err != nil {
		return row, err
	}
	if payment.AssociateID != nil {
		associates, err := t.associates(ctx, []string{*payment.AssociateID})
		if err != nil {
			return row, err
		}
		if associate, ok := associates[*payment.AssociateID]; ok {
			row.AssociateRow = &associate
		}
	}
	subscription, err := t.paymentSubscription(ctx, *payment, group)
	if err != nil {
		return row, err
	}
	payment.Subscription = subscription
	return row, nil
}

// single returns the one value of a .get(): nil for DoesNotExist, an error
// for MultipleObjectsReturned.
func single(values []*string) (*string, bool, error) {
	switch len(values) {
	case 0:
		return nil, false, nil
	case 1:
		return values[0], true, nil
	}
	return nil, false, domain.ErrCourseSubscriptionServer
}

// paymentSubscription is PaymentSerializer.get_subscription_id.
func (t *courseSubscriptionTransaction) paymentSubscription(ctx context.Context, payment domain.InvoicePartyPayment, group *string) (*string, error) {
	lookup := func(query string, args ...any) (*string, bool, error) {
		values := []*string{}
		if err := t.tx.SelectContext(ctx, &values, query+` LIMIT 2`, args...); err != nil {
			return nil, false, err
		}
		return single(values)
	}
	// A related subscription that is None raises AttributeError.
	required := func(value *string, found bool, err error) (*string, bool, error) {
		if err == nil && found && value == nil {
			return nil, false, domain.ErrCourseSubscriptionServer
		}
		return value, found, err
	}
	switch payment.Subject {
	case 1:
		value, _, err := lookup(`SELECT subscription_id::text FROM application_subscription
 WHERE payment_id=$1 AND ($2::uuid IS NULL OR group_id=$2::uuid)`, payment.ID, group)
		return value, err
	case 2:
		value, found, err := required(lookup(`SELECT cs.subscription_id::text FROM application_coursesubscriptioninstallment i
 JOIN application_coursesubscription cs ON cs.course_subscription_id=i.course_subscription_id
 WHERE i.payment_id=$1 AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid)`, payment.ID, group))
		if err != nil || found {
			return value, err
		}
		value, found, err = required(lookup(`SELECT subscription_id::text FROM application_coursesubscription
 WHERE payment_id=$1 AND NOT deleted`, payment.ID))
		if err != nil || found {
			return value, err
		}
		value, _, err = required(lookup(`SELECT subscription_id::text FROM application_carnetsubscription WHERE payment_id=$1`, payment.ID))
		return value, err
	case 0:
		values := []string{}
		if err := t.tx.SelectContext(ctx, &values, `SELECT subscription_id FROM application_subscription
 WHERE associate_id IS NOT DISTINCT FROM $1::uuid AND NOT archived AND ($2::uuid IS NULL OR group_id=$2::uuid)
 ORDER BY creation_date LIMIT 1`, payment.AssociateID, group); err != nil {
			return nil, err
		}
		if len(values) == 0 {
			return nil, nil
		}
		return &values[0], nil
	}
	return nil, nil
}

// Carnets is CarnetSubscription.objects.filter(course_subscription=obj,
// disabled=False).select_related('carnet_id').
func (t *courseSubscriptionTransaction) Carnets(ctx context.Context, rows []string) ([]domain.CourseSubscriptionCarnet, error) {
	items := []domain.CourseSubscriptionCarnet{}
	err := t.tx.SelectContext(ctx, &items, `SELECT l.coursesubscription_id, h.carnet_subscription_id, c.carnet_id, c.title, c.lessons_number,
 h.meta::text AS meta, h.creation_date FROM application_carnetsubscription_course_subscription l
 JOIN application_carnetsubscription h ON h.carnet_subscription_id=l.carnetsubscription_id
 LEFT JOIN application_carnet c ON c.carnet_id=h.carnet_id_id
 WHERE l.coursesubscription_id=ANY($1::uuid[]) AND NOT h.disabled ORDER BY l.id`, rows)
	return items, err
}

// Course is the course field's queryset (Course.objects.all() evaluated at
// import: live, no group filter).
func (t *courseSubscriptionTransaction) Course(ctx context.Context, id string) (*domain.CourseSubscriptionCourse, error) {
	var row domain.CourseSubscriptionCourse
	err := t.tx.GetContext(ctx, &row, `SELECT course_id, sport_association_id, course_type, title FROM application_course
 WHERE course_id=$1 AND NOT deleted`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

// SubscriptionVisible is Subscription.objects (group-aware).filter(pk=...,
// sport_association_id=...).exists().
func (t *courseSubscriptionTransaction) SubscriptionVisible(ctx context.Context, id, association string, group *string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_subscription
 WHERE subscription_id=$1 AND sport_association_id IS NOT DISTINCT FROM $2::uuid AND ($3::uuid IS NULL OR group_id=$3::uuid))`, id, association, group)
	return exists, err
}

// CourseSubscriptionIDTaken is the UniqueValidator on the default manager
// (live rows), excluding the instance.
func (t *courseSubscriptionTransaction) CourseSubscriptionIDTaken(ctx context.Context, id, exclude string) (bool, error) {
	var taken bool
	err := t.tx.GetContext(ctx, &taken, `SELECT EXISTS(SELECT 1 FROM application_coursesubscription
 WHERE course_subscription_id=$1 AND course_subscription_id<>$2 AND NOT deleted)`, id, exclude)
	return taken, err
}

func (t *courseSubscriptionTransaction) MembershipPaymentsWhere(ctx context.Context, row string, group *string, unpaid bool, billedUntil *string,
	limit int) ([]domain.CourseSubscriptionMembershipPayment, error) {
	payments := []domain.CourseSubscriptionMembershipPayment{}
	query := `SELECT p.payment_id, p.paid, p.meta::text AS meta FROM application_payment p
 JOIN application_coursesubscription_membership_payments l ON l.payment_id=p.payment_id
 WHERE l.coursesubscription_id=$1 AND NOT p.deleted AND ($2::uuid IS NULL OR p.group_id=$2::uuid) AND (NOT $3 OR NOT p.paid)
 AND ($4::text IS NULL OR (p.meta -> 'billed_until')=to_jsonb($4::text)) ORDER BY l.id`
	if limit > 0 {
		query += ` LIMIT ` + strconv.Itoa(limit) + ` FOR UPDATE OF p`
	}
	err := t.tx.SelectContext(ctx, &payments, query, row, group, unpaid, billedUntil)
	return payments, err
}

// MembershipPaymentOverlaps is the exclude(pk=...).filter(meta__billed_from__lt=
// end, meta__billed_until__gt=start).exists() jsonb comparison.
func (t *courseSubscriptionTransaction) MembershipPaymentOverlaps(ctx context.Context, row, exclude string, group *string, end, start string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_payment p
 JOIN application_coursesubscription_membership_payments l ON l.payment_id=p.payment_id
 WHERE l.coursesubscription_id=$1 AND p.payment_id<>$2 AND NOT p.deleted AND ($3::uuid IS NULL OR p.group_id=$3::uuid)
 AND (p.meta -> 'billed_from')<to_jsonb($4::text) AND (p.meta -> 'billed_until')>to_jsonb($5::text))`, row, exclude, group, end, start)
	return exists, err
}

// DefaultPaymentCategory is PaymentCategory.objects.filter(name__iexact=
// 'entrate e proventi da attività tipiche').first() (by key).
func (t *courseSubscriptionTransaction) DefaultPaymentCategory(ctx context.Context) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT payment_category_id FROM application_paymentcategory
 WHERE UPPER(name::text)=UPPER('entrate e proventi da attività tipiche') ORDER BY payment_category_id LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &id, err
}

func (t *courseSubscriptionTransaction) PaymentState(ctx context.Context, payment string) (domain.CourseSubscriptionPaymentState, error) {
	var row domain.CourseSubscriptionPaymentState
	err := t.tx.GetContext(ctx, &row, `SELECT amount::text AS amount, creation_date, payment_date, description, meta::text AS meta,
 payment_category_id, sport_association_id FROM application_payment WHERE payment_id=$1`, payment)
	return row, err
}

func (t *courseSubscriptionTransaction) SharedPayment(ctx context.Context, row domain.CourseSubscriptionRow, group *string) (bool, error) {
	if row.PaymentID == nil {
		return false, nil
	}
	var shared bool
	err := t.tx.GetContext(ctx, &shared, `SELECT EXISTS(SELECT 1 FROM application_coursesubscriptioninstallment i
 JOIN application_payment p ON p.payment_id=i.payment_id
 WHERE i.course_subscription_id=$1 AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid) AND i.payment_id=$3 AND NOT p.paid)`,
		row.ID, group, *row.PaymentID)
	return shared, err
}

func courseSubscriptionEntry(model, name, key, representation string, action int, changes map[string][2]string, association *string, path string,
	audit domain.CourseSubscriptionAudit) modelAuditEntry {
	entry := modelAuditEntry{Model: model, Key: key, Representation: representation, Action: action, Changes: changes, Context: audit.Context,
		At: audit.At}
	if representation == "" {
		entry.Representation = name + " object (" + key + ")"
	}
	if association != nil {
		entry.AssociationID, entry.ResolutionPath = association, &path
	} else if audit.ActorAssociation != nil {
		fallback := "actor.sport_association"
		entry.AssociationID, entry.ResolutionPath = audit.ActorAssociation, &fallback
	}
	return entry
}

// SaveCourseSubscription is Model.save(): every column is written; the entry
// (if any) carries the in-memory repr.
func (t *courseSubscriptionTransaction) SaveCourseSubscription(ctx context.Context, save domain.CourseSubscriptionSave, audit domain.CourseSubscriptionAudit) error {
	row := save.Row
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_coursesubscription SET course_id=$2, paid=$3, creation_date=$4, subscription_id=$5,
 payment_id=$6, multi_payments=$7, one_fee_payment=$8, archived=$9, type=$10, multiple_quote=$11::jsonb, billed_frequency=$12, billed_from=$13,
 billed_until=$14, billed_from_day_of_month=$15, membership_active=$16, membership_fee=$17::numeric, auto_renewal=$18, deleted=$19
 WHERE course_subscription_id=$1`, row.ID, row.CourseID, row.Paid, row.CreationDate, row.SubscriptionID, row.PaymentID, row.MultiPayments,
		row.OneFeePayment, row.Archived, row.Type, row.MultipleQuote, row.BilledFrequency, row.BilledFrom, row.BilledUntil, row.BilledFromDayOfMonth,
		row.MembershipActive, row.MembershipFee, row.AutoRenewal, row.Deleted); err != nil {
		return err
	}
	changes := save.Changes
	if save.Unseen {
		var err error
		if changes, err = createdModelChanges(ctx, t.tx, "application_coursesubscription", "course_subscription_id", row.ID); err != nil {
			return err
		}
	}
	if len(changes) == 0 {
		return nil
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscription", "CourseSubscription", row.ID, save.Representation, 1,
		changes, save.AssociationID, save.ResolutionPath, audit))
}

// SavePayment is Payment.save() of the matched membership payment, indexed
// directly to its association.
func (t *courseSubscriptionTransaction) SavePayment(ctx context.Context, save domain.CourseSubscriptionPaymentSave, audit domain.CourseSubscriptionAudit) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET amount=$2::numeric, creation_date=COALESCE($3::timestamptz, creation_date),
 payment_date=COALESCE($4::timestamptz, payment_date), description=$5, meta=$6::jsonb, payment_category_id=$7 WHERE payment_id=$1`, save.ID, save.Amount, save.CreationDate, save.PaymentDate, save.Description, save.Meta, save.CategoryID); err != nil {
		return err
	}
	if len(save.Changes) == 0 {
		return nil
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("payment", "Payment", save.ID, "", 1, save.Changes, save.AssociationID, "direct", audit))
}

// --- deletes ------------------------------------------------------------------

type courseSubscriptionSnapshot struct {
	id      string
	changes map[string][2]string
	stored  map[string]any
}

func (t *courseSubscriptionTransaction) snapshot(ctx context.Context, table, key, id string) (*courseSubscriptionSnapshot, error) {
	changes, stored, err := storedModelChanges(ctx, t.tx, table, key, id, false, true)
	if errors.Is(err, ErrModelDeleteMissing) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &courseSubscriptionSnapshot{id: id, changes: changes, stored: stored}, nil
}

func storedText(stored map[string]any, column string) *string {
	if text, ok := stored[column].(string); ok {
		return &text
	}
	return nil
}

// liveAssociation is the changes_json resolution: the association must be
// found through SportAssociation.objects (ActiveManager).
func (t *courseSubscriptionTransaction) liveAssociation(ctx context.Context, changes map[string][2]string) (*string, error) {
	value, ok := changes["sport_association"]
	if !ok || value[0] == "" || value[0] == "None" {
		return nil, nil
	}
	var exists bool
	if err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_sportassociation
 WHERE sport_association_id=$1::uuid AND NOT deleted)`, value[0]); err != nil {
		return nil, err
	}
	if !exists {
		return nil, nil
	}
	association := value[0]
	return &association, nil
}

// deletePayments is the collector for payments (a single payment.delete() or
// a payment QuerySet.delete()): instances loaded first; with the Payment
// pre_delete signal connected, each payment's live group-aware installments
// are deleted (their own pre_delete may delete the payment, the Payment
// signal disconnected); then SET_NULL references and through rows are
// cleared and the payments deleted, post_delete audited in reverse key order
// (also for payments already gone).
func (t *courseSubscriptionTransaction) deletePayments(ctx context.Context, payments []*courseSubscriptionSnapshot, signal bool,
	audit domain.CourseSubscriptionAudit) error {
	sort.Slice(payments, func(i, j int) bool { return payments[i].id < payments[j].id })
	if signal {
		for _, payment := range payments {
			installments := []string{}
			if err := t.tx.SelectContext(ctx, &installments, `SELECT course_subscription_installment_id FROM application_coursesubscriptioninstallment
 WHERE payment_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid) ORDER BY course_subscription_installment_id FOR UPDATE`,
				payment.id, audit.Group); err != nil {
				return err
			}
			if err := t.deleteInstallments(ctx, installments, false, audit); err != nil {
				return err
			}
		}
	}
	for _, payment := range payments {
		if err := clearPaymentReferences(ctx, t.tx, payment.id); err != nil {
			return err
		}
	}
	for _, payment := range payments {
		if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_payment WHERE payment_id=$1`, payment.id); err != nil {
			return err
		}
	}
	for index := len(payments) - 1; index >= 0; index-- {
		association, err := t.liveAssociation(ctx, payments[index].changes)
		if err != nil {
			return err
		}
		if err = appendModelAudit(ctx, t.tx, courseSubscriptionEntry("payment", "Payment", payments[index].id, "", 2, payments[index].changes,
			association, "changes_json.sport_association", audit)); err != nil {
			return err
		}
	}
	return nil
}

// deleteInstallments is the collector for installments (ids in key order):
// each pre_delete (delete_related_payment) deletes the installment's unpaid
// payment fetched fresh; then the rows are deleted and post_delete audited in
// reverse order from the instances as loaded.
func (t *courseSubscriptionTransaction) deleteInstallments(ctx context.Context, ids []string, paymentSignal bool, audit domain.CourseSubscriptionAudit) error {
	snapshots := make([]*courseSubscriptionSnapshot, 0, len(ids))
	for _, id := range ids {
		snapshot, err := t.snapshot(ctx, "application_coursesubscriptioninstallment", "course_subscription_installment_id", id)
		if err != nil {
			return err
		}
		if snapshot != nil {
			snapshots = append(snapshots, snapshot)
		}
	}
	for _, installment := range snapshots {
		payment := storedText(installment.stored, "payment_id")
		if payment == nil {
			continue
		}
		current, err := t.snapshot(ctx, "application_payment", "payment_id", *payment)
		if err != nil {
			return err
		}
		if current == nil || current.stored["paid"] == true {
			continue
		}
		if err = t.deletePayments(ctx, []*courseSubscriptionSnapshot{current}, paymentSignal, audit); err != nil {
			return err
		}
	}
	for _, installment := range snapshots {
		if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_coursesubscriptioninstallment WHERE course_subscription_installment_id=$1`,
			installment.id); err != nil {
			return err
		}
	}
	for index := len(snapshots) - 1; index >= 0; index-- {
		if err := appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscriptioninstallment", "CourseSubscriptionInstallment",
			snapshots[index].id, "", 2, snapshots[index].changes, nil, "", audit)); err != nil {
			return err
		}
	}
	return nil
}

func (t *courseSubscriptionTransaction) liveInstallmentsOf(ctx context.Context, row string, group *string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT course_subscription_installment_id FROM application_coursesubscriptioninstallment
 WHERE course_subscription_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid) ORDER BY course_subscription_installment_id FOR UPDATE`,
		row, group)
	return ids, err
}

func (t *courseSubscriptionTransaction) DeleteInstallmentsOf(ctx context.Context, row string, audit domain.CourseSubscriptionAudit) error {
	ids, err := t.liveInstallmentsOf(ctx, row, audit.Group)
	if err != nil {
		return err
	}
	return t.deleteInstallments(ctx, ids, true, audit)
}

// BulkDelete is QuerySet.delete() of the rows (key order): the collector
// loads them and their installments (base manager); pre_delete runs for the
// installments, then for the rows (delete_related_course_payment: a row with
// a payment fails on Payment.objects.get(id=...) and does nothing; otherwise
// a membership deletes its unpaid membership payments, then the live
// group-aware installments are deleted); the through rows go, then the
// installments and rows are deleted and audited in reverse key order.
func (t *courseSubscriptionTransaction) BulkDelete(ctx context.Context, rows []domain.CourseSubscriptionRow, audit domain.CourseSubscriptionAudit) error {
	if len(rows) == 0 {
		return nil
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].ID < rows[j].ID })
	keys := make([]string, len(rows))
	snapshots := make([]*courseSubscriptionSnapshot, len(rows))
	for index, row := range rows {
		keys[index] = row.ID
		snapshot, err := t.snapshot(ctx, "application_coursesubscription", "course_subscription_id", row.ID)
		if err != nil {
			return err
		}
		if snapshot == nil {
			return domain.ErrCourseSubscriptionServer
		}
		snapshots[index] = snapshot
	}
	installmentIDs := []string{}
	if err := t.tx.SelectContext(ctx, &installmentIDs, `SELECT course_subscription_installment_id FROM application_coursesubscriptioninstallment
 WHERE course_subscription_id=ANY($1::uuid[]) ORDER BY course_subscription_installment_id FOR UPDATE`, keys); err != nil {
		return err
	}
	installments := make([]*courseSubscriptionSnapshot, 0, len(installmentIDs))
	for _, id := range installmentIDs {
		snapshot, err := t.snapshot(ctx, "application_coursesubscriptioninstallment", "course_subscription_installment_id", id)
		if err != nil {
			return err
		}
		installments = append(installments, snapshot)
	}
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
	for _, row := range rows {
		if row.PaymentID != nil {
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
		if err := t.DeleteInstallmentsOf(ctx, row.ID, audit); err != nil {
			return err
		}
	}
	for _, statement := range []string{
		`DELETE FROM application_coursesubscription_membership_payments WHERE coursesubscription_id=ANY($1::uuid[])`,
		`DELETE FROM application_carnetsubscription_course_subscription WHERE coursesubscription_id=ANY($1::uuid[])`,
		`DELETE FROM application_coursesubscriptioninstallment WHERE course_subscription_id=ANY($1::uuid[])`,
		`DELETE FROM application_coursesubscription WHERE course_subscription_id=ANY($1::uuid[])`,
	} {
		if _, err := t.tx.ExecContext(ctx, statement, keys); err != nil {
			return err
		}
	}
	for index := len(installments) - 1; index >= 0; index-- {
		if err := appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscriptioninstallment", "CourseSubscriptionInstallment",
			installments[index].id, "", 2, installments[index].changes, nil, "", audit)); err != nil {
			return err
		}
	}
	for index := len(rows) - 1; index >= 0; index-- {
		if err := appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscription", "CourseSubscription", rows[index].ID,
			dto.CourseSubscriptionRepr(rows[index], dto.CourseSubscriptionMemory{}), 2, snapshots[index].changes, nil, "", audit)); err != nil {
			return err
		}
	}
	return nil
}
