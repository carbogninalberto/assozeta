package domain

import (
	"context"
	"time"
)

// CourseEnrolmentInstallment is the live group-aware installment
// course_installment_make_payment resolves by id, with what the view reads
// through the forward relations (base managers).
type CourseEnrolmentInstallment struct {
	ID                 string    `db:"course_subscription_installment_id"`
	Amount             string    `db:"amount"`
	PaymentDate        time.Time `db:"payment_date"`
	PaymentID          *string   `db:"payment_id"`
	SubscriptionID     *string   `db:"subscription_id"`
	SubscriptionUserID *string   `db:"subscription_user_id"`
	AssociateID        *string   `db:"associate_id"`
	CourseAssociation  *string   `db:"course_association_id"`
	// SubscriptionAssociation is the row's subscription association
	// (scoped_queryset's relation during an impersonation session).
	SubscriptionAssociation *string `db:"subscription_association_id"`
}

// CourseEnrolmentPayment is Payment.objects.create(...) of the enrolment
// paths: the stored columns, the in-memory amount auditlog records (a float
// renders through str()) and whether a second payment.save() follows.
type CourseEnrolmentPayment struct {
	ID            string
	Amount        string
	MemoryAmount  string
	CreationDate  time.Time
	PaymentDate   *time.Time
	Description   string
	CategoryID    *string
	UserID        *string
	AssociateID   *string
	AssociationID *string
	Meta          *string
	Course        *string
	AccountID     *string
	GroupID       *string
	SecondSave    bool
}

// CourseEnrolmentRowSave is CourseSubscription.save() of an instance created
// in this request: auditlog diffs its in-memory values (FloatFee: the float
// membership_fee default) with the stored row; Representation is its
// str(self.__dict__).
type CourseEnrolmentRowSave struct {
	Row            CourseSubscriptionRow
	FloatFee       bool
	Representation string
}

// CourseEnrolmentTransaction is one transaction serving the enrolment routes.
type CourseEnrolmentTransaction interface {
	// Subscriptions exposes the course-subscriptions reads (rendering).
	Subscriptions() CourseSubscriptionTransaction

	OwnerAssociation(ctx context.Context, owner string) (string, error)
	OwnerAssociationFirst(ctx context.Context, owner string) (*string, error)
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	GroupExists(ctx context.Context, id string) (bool, error)
	DefaultPaymentCategory(ctx context.Context) (*string, error)
	DefaultBankAccount(ctx context.Context, association *string) (*string, error)
	// AssociationUser is sport_association.user (base manager).
	AssociationUser(ctx context.Context, association string) (*string, error)
	// CoursesCategory is the association user's default_payment_category_courses
	// (PaymentCategory.objects.filter(pk=...).first()) or, without one, the
	// default category by name.
	CoursesCategory(ctx context.Context, association string) (*string, error)
	// CategoryFirst is PaymentCategory.objects.filter(payment_category_id=id).first().
	CategoryFirst(ctx context.Context, id string) (*string, error)
	UserFullInstallmentsPlan(ctx context.Context, user string) (bool, error)

	// Course is Course.objects.filter(course_id=id): live, and group-aware when group is set.
	Course(ctx context.Context, id string, group *string) (*CourseWriteRow, error)
	// CourseRender is the course row CourseSerializer renders.
	CourseRender(ctx context.Context, id string) (*CourseOverviewRow, error)
	// Subscription is Subscription.objects (group-aware) by id.
	Subscription(ctx context.Context, id string, group *string) (*CourseWriteSubscription, error)
	// LiveRow is CourseSubscription.objects.filter(course=..., subscription=...).first().
	LiveRow(ctx context.Context, course, subscription string) (bool, error)
	// SubscriptionInAssociation is Subscription.objects (group-aware).filter(pk=id,
	// sport_association_id=association).exists().
	SubscriptionInAssociation(ctx context.Context, id string, association, group *string) (bool, error)
	// RowIDLive is the UniqueValidator on the default manager.
	RowIDLive(ctx context.Context, id string) (bool, error)

	CreateRow(ctx context.Context, row CourseSubscriptionRow, overrides map[string][2]string, representation string, audit CourseSubscriptionAudit) error
	SaveRow(ctx context.Context, save CourseEnrolmentRowSave, audit CourseSubscriptionAudit) error
	CreatePayment(ctx context.Context, payment CourseEnrolmentPayment, audit CourseSubscriptionAudit) error
	CreateInstallment(ctx context.Context, installment CourseWriteInstallmentCreate, audit CourseSubscriptionAudit) error
	AddMembershipPayment(ctx context.Context, row, payment string) error

	Installment(ctx context.Context, id string, group *string) (*CourseEnrolmentInstallment, error)
	// LinkInstallment is installment.save() with the new payment.
	LinkInstallment(ctx context.Context, installment CourseEnrolmentInstallment, payment string, audit CourseSubscriptionAudit) error
	// Payment renders a payment through PaymentSerializer's reads.
	Payment(ctx context.Context, id string, group *string) (CourseSubscriptionPayment, error)

	Commit() error
	Rollback() error
}

type CourseEnrolmentStore interface {
	BeginCourseEnrolment(context.Context) (CourseEnrolmentTransaction, error)
}
