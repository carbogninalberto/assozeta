package repository

import (
	"context"
	"database/sql"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// CourseEnrolment serves course-subscriptions/add, the course-overview add
// and the installment payment; it reuses the course writes and course
// subscription helpers on one transaction.
type CourseEnrolment struct{ db *sqlx.DB }

func NewCourseEnrolment(db *sqlx.DB) *CourseEnrolment { return &CourseEnrolment{db: db} }

type courseEnrolmentTransaction struct{ *courseWritesTransaction }

func (r *CourseEnrolment) BeginCourseEnrolment(ctx context.Context) (domain.CourseEnrolmentTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &courseEnrolmentTransaction{courseWritesTransaction: &courseWritesTransaction{courseSubscriptionTransaction: &courseSubscriptionTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}}, nil
}

func (t *courseEnrolmentTransaction) Subscriptions() domain.CourseSubscriptionTransaction {
	return t.courseSubscriptionTransaction
}

func (t *courseEnrolmentTransaction) AssociationUser(ctx context.Context, association string) (*string, error) {
	return courseWritesFirst[string](ctx, t.tx, `SELECT user_id FROM application_sportassociation WHERE sport_association_id=$1`, association)
}

func (t *courseEnrolmentTransaction) CoursesCategory(ctx context.Context, association string) (*string, error) {
	preferred, err := courseWritesFirst[sql.NullString](ctx, t.tx, `SELECT u.default_payment_category_courses_id::text FROM application_sportassociation a
 JOIN bakney_user u ON u.user_id=a.user_id WHERE a.sport_association_id=$1`, association)
	if err != nil {
		return nil, err
	}
	if preferred != nil && preferred.Valid {
		return t.CategoryFirst(ctx, preferred.String)
	}
	return t.DefaultPaymentCategory(ctx, &association)
}

func (t *courseEnrolmentTransaction) CategoryFirst(ctx context.Context, id string) (*string, error) {
	return courseWritesFirst[string](ctx, t.tx, `SELECT payment_category_id FROM application_paymentcategory WHERE payment_category_id=$1::uuid`, id)
}

func (t *courseEnrolmentTransaction) Course(ctx context.Context, id string, group *string) (*domain.CourseWriteRow, error) {
	return courseWritesFirst[domain.CourseWriteRow](ctx, t.tx, `SELECT `+courseWritesColumns+` FROM application_course
 WHERE course_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid) ORDER BY course_id LIMIT 1`, id, group)
}

func (t *courseEnrolmentTransaction) CourseRender(ctx context.Context, id string) (*domain.CourseOverviewRow, error) {
	return courseWritesFirst[domain.CourseOverviewRow](ctx, t.tx, `SELECT `+courseSerializerColumns+` FROM application_course c WHERE c.course_id=$1`, id)
}

func (t *courseEnrolmentTransaction) Subscription(ctx context.Context, id string, group *string) (*domain.CourseWriteSubscription, error) {
	return t.LiveSubscription(ctx, id, group)
}

func (t *courseEnrolmentTransaction) LiveRow(ctx context.Context, course, subscription string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_coursesubscription
 WHERE course_id=$1 AND subscription_id=$2 AND NOT deleted)`, course, subscription)
	return exists, err
}

// CreateRow is CourseSubscription.objects.create(...): every column of the
// instance, then the create entry (the stored diff with the in-memory
// overrides), indexed through the course.
func (t *courseEnrolmentTransaction) CreateRow(ctx context.Context, row domain.CourseSubscriptionRow, overrides map[string][2]string,
	representation string, audit domain.CourseSubscriptionAudit) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_coursesubscription (course_subscription_id, course_id, paid, creation_date,
 subscription_id, payment_id, multi_payments, one_fee_payment, archived, type, multiple_quote, billed_frequency, billed_from, billed_until,
 billed_from_day_of_month, membership_active, membership_fee, auto_renewal, deleted) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,$15,
 $16,$17::numeric,$18,$19)`, row.ID, row.CourseID, row.Paid, row.CreationDate, row.SubscriptionID, row.PaymentID, row.MultiPayments,
		row.OneFeePayment, row.Archived, row.Type, row.MultipleQuote, row.BilledFrequency, row.BilledFrom, row.BilledUntil, row.BilledFromDayOfMonth,
		row.MembershipActive, row.MembershipFee, row.AutoRenewal, row.Deleted); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_coursesubscription", "course_subscription_id", row.ID)
	if err != nil {
		return err
	}
	for name, pair := range overrides {
		changes[name] = pair
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscription", "CourseSubscription", row.ID, representation, 0, changes,
		row.CourseAssociationID, "course.sport_association", audit))
}

// SaveRow is Model.save() of the in-memory instance: auditlog's entry for
// the fields whose str() differs from the stored row, then the UPDATE.
func (t *courseEnrolmentTransaction) SaveRow(ctx context.Context, save domain.CourseEnrolmentRowSave, audit domain.CourseSubscriptionAudit) error {
	row := save.Row
	stored, err := courseWritesFirst[domain.CourseSubscriptionRow](ctx, t.tx, `SELECT `+courseSubscriptionColumns+`
 FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
 WHERE cs.course_subscription_id=$1 FOR UPDATE OF cs`, row.ID)
	if err != nil {
		return err
	}
	if stored == nil {
		return domain.ErrCourseSubscriptionServer
	}
	before, after := courseSubscriptionAuditValues(*stored, false), courseSubscriptionAuditValues(row, save.FloatFee)
	changes := map[string][2]string{}
	for name, value := range after {
		if before[name] != value {
			changes[name] = [2]string{before[name], value}
		}
	}
	if len(changes) > 0 {
		if err = appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscription", "CourseSubscription", row.ID, save.Representation, 1,
			changes, stored.CourseAssociationID, "course.sport_association", audit)); err != nil {
			return err
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

// CreatePayment is Payment.objects.create(...): the create entry (indexed
// directly) records the in-memory amount; a following payment.save() records
// it again when it renders differently from the stored one.
func (t *courseEnrolmentTransaction) CreatePayment(ctx context.Context, payment domain.CourseEnrolmentPayment, audit domain.CourseSubscriptionAudit) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_payment (payment_id, type, subject, amount, creation_date, payment_date, paid,
 expense, description, payment_category_id, user_id, associate_id, sport_association_id, archived, meta, custom_accounts_id, deleted, group_id, course)
 VALUES ($1,'default',2,$2::numeric,$3,$4,false,false,$5,$6,$7,$8,$9,false,$10::jsonb,$11,false,$12,$13::jsonb)`, payment.ID, payment.Amount,
		payment.CreationDate, payment.PaymentDate, payment.Description, payment.CategoryID, payment.UserID, payment.AssociateID, payment.AssociationID,
		payment.Meta, payment.AccountID, payment.GroupID, payment.Course); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_payment", "payment_id", payment.ID)
	if err != nil {
		return err
	}
	stored := changes["amount"][1]
	changes["amount"] = [2]string{"None", payment.MemoryAmount}
	second := courseWritesGroupResave(audit)
	if audit.GroupRaw != "" {
		changes["group"] = [2]string{"None", audit.GroupRaw}
	}
	if err = appendModelAudit(ctx, t.tx, courseSubscriptionEntry("payment", "Payment", payment.ID, "", 0, changes, payment.AssociationID, "direct",
		audit)); err != nil {
		return err
	}
	if !payment.SecondSave {
		return nil
	}
	if stored != payment.MemoryAmount {
		second["amount"] = [2]string{stored, payment.MemoryAmount}
	}
	if len(second) == 0 {
		return nil
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("payment", "Payment", payment.ID, "", 1,
		second, payment.AssociationID, "direct", audit))
}

func (t *courseEnrolmentTransaction) AddMembershipPayment(ctx context.Context, row, payment string) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_coursesubscription_membership_payments (coursesubscription_id, payment_id)
 VALUES ($1,$2) ON CONFLICT DO NOTHING`, row, payment)
	return err
}

// Installment is CourseSubscriptionInstallment.objects (live, group-aware)
// .filter(pk=id).first() with its course subscription, course and
// subscription read through the forward relations.
func (t *courseEnrolmentTransaction) Installment(ctx context.Context, id string, group *string) (*domain.CourseEnrolmentInstallment, error) {
	return courseWritesFirst[domain.CourseEnrolmentInstallment](ctx, t.tx, `SELECT i.course_subscription_installment_id, i.amount::text AS amount,
 i.payment_date, i.payment_id, cs.subscription_id, s.user_id AS subscription_user_id, s.associate_id, c.sport_association_id AS course_association_id,
 s.sport_association_id AS subscription_association_id
 FROM application_coursesubscriptioninstallment i JOIN application_coursesubscription cs ON cs.course_subscription_id=i.course_subscription_id
 JOIN application_course c ON c.course_id=cs.course_id LEFT JOIN application_subscription s ON s.subscription_id=cs.subscription_id
 WHERE i.course_subscription_installment_id=$1 AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid)
 ORDER BY i.course_subscription_installment_id LIMIT 1 FOR UPDATE OF i`, id, group)
}

func (t *courseEnrolmentTransaction) LinkInstallment(ctx context.Context, installment domain.CourseEnrolmentInstallment, payment string,
	audit domain.CourseSubscriptionAudit) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_coursesubscriptioninstallment SET payment_id=$2 WHERE course_subscription_installment_id=$1`,
		installment.ID, payment); err != nil {
		return err
	}
	old := "None"
	if installment.PaymentID != nil {
		old = *installment.PaymentID
	}
	if old == payment {
		return nil
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscriptioninstallment", "CourseSubscriptionInstallment", installment.ID, "",
		1, map[string][2]string{"payment": {old, payment}}, installment.CourseAssociation, "course_subscription.course.sport_association", audit))
}

func (t *courseEnrolmentTransaction) Payment(ctx context.Context, id string, group *string) (domain.CourseSubscriptionPayment, error) {
	return t.payment(ctx, id, group)
}

func (t *courseEnrolmentTransaction) SubscriptionInAssociation(ctx context.Context, id string, association, group *string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_subscription
 WHERE subscription_id=$1 AND sport_association_id IS NOT DISTINCT FROM $2::uuid AND ($3::uuid IS NULL OR group_id=$3::uuid))`, id, association, group)
	return exists, err
}
