package domain

import (
	"context"
	"errors"
	"time"
)

// ErrStatisticNotPorted refuses statistic/dashboard widgets the Go port does
// not implement (todaylessons, expiredPayments) and inputs it does not
// characterize; the handler answers with the stable 500 page before any work.
var ErrStatisticNotPorted = errors.New("statistic input is not ported")

// StatisticScope is the request's association, X-Group-Id (nil when absent or
// not a UUID) and, during association-scoped impersonation, that association.
type StatisticScope struct {
	Association   string
	Group         *string
	Impersonation *string
}

type StatisticBalance struct {
	Day   int `db:"balance_sheet_start_day"`
	Month int `db:"balance_sheet_start_month"`
}

// StatisticDaily is one UTC calendar day of a TruncDate group (numeric text or a count).
type StatisticDaily struct {
	Day   time.Time `db:"day"`
	Value string    `db:"value"`
}

type StatisticKeyCount struct {
	Key   int   `db:"key"`
	Count int64 `db:"count"`
}

type StatisticBestCourse struct {
	CourseID      string  `db:"course_id"`
	Title         *string `db:"title"`
	Description   *string `db:"description"`
	Subscriptions int64   `db:"subscriptions"`
}

type StatisticPerson struct {
	AssociateID *string `db:"associate_id"`
	FirstName   *string `db:"first_name"`
	LastName    *string `db:"last_name"`
}

type StatisticCarnet struct {
	CarnetSubscriptionID string    `db:"carnet_subscription_id"`
	CreationDate         time.Time `db:"creation_date"`
	Meta                 string    `db:"meta"`
	SubscriptionID       string    `db:"subscription_id"`
	StatisticPerson
	CarnetID      *string `db:"carnet_id"`
	Title         *string `db:"title"`
	Description   *string `db:"description"`
	LessonsNumber *int64  `db:"lessons_number"`
	Fee           *string `db:"fee"`
	Public        *bool   `db:"public"`
}

type StatisticMedical struct {
	SubscriptionID string    `db:"subscription_id"`
	Expiration     time.Time `db:"expiration_date"`
	StatisticPerson
}

type StatisticApproval struct {
	CreationDate   time.Time `db:"creation_date"`
	SubscriptionID string    `db:"subscription_id"`
	Status         int       `db:"status_flag"`
	StatisticPerson
}

type StatisticSums struct {
	Income   *string `db:"income"`
	Expenses *string `db:"expenses"`
}

type StatisticMonthly struct {
	Month time.Time `db:"month"`
	Value string    `db:"value"`
}

type StatisticCourseGroup struct {
	Title   *string `db:"title"`
	Count   int64   `db:"count"`
	Revenue *string `db:"revenue"`
}

type StatisticAccepted struct {
	HasAssociate bool       `db:"has_associate"`
	Born         *time.Time `db:"born_date"`
	Sex          *string    `db:"sex"`
}

type StatisticPaymentTotals struct {
	Paid          *string `db:"paid"`
	Unpaid        *string `db:"unpaid"`
	Subscriptions *string `db:"subscriptions"`
	Activities    *string `db:"activities"`
	Count         int64   `db:"count"`
}

// StatisticYear is a balance-sheet year [From, To] (To is the last day at 00:00).
type StatisticYear struct{ From, To time.Time }

type StatisticDashboardStore interface {
	ScopedAssociation(ctx context.Context, user string, scope *string) (string, error)
	Balance(ctx context.Context, user string) (StatisticBalance, error)
	SubscriptionStatuses(ctx context.Context, scope StatisticScope, today string) ([]StatisticKeyCount, error)
	DailyPayments(ctx context.Context, scope StatisticScope, from, to time.Time) ([]StatisticDaily, error)
	DailySubscriptions(ctx context.Context, scope StatisticScope, from, to time.Time) ([]StatisticDaily, error)
	BestCourses(ctx context.Context, scope StatisticScope) ([]StatisticBestCourse, error)
	DailyCourseSubscriptions(ctx context.Context, scope StatisticScope, from, to time.Time) ([]StatisticDaily, error)
	CourseSubscriptionCount(ctx context.Context, scope StatisticScope) (int64, error)
	ExpiringCarnets(ctx context.Context, scope StatisticScope, owner string) ([]StatisticCarnet, error)
	Medicals(ctx context.Context, scope StatisticScope, today string, expiring bool, year StatisticYear) ([]StatisticMedical, error)
	Approvals(ctx context.Context, scope StatisticScope, today string) ([]StatisticApproval, error)
	IncomeAndExpenses(ctx context.Context, scope StatisticScope, from, to time.Time) (StatisticSums, error)
	ExpiredPayments(ctx context.Context, scope StatisticScope, from, to time.Time) ([]StatisticExpiredPayment, error)
	ExpiredPaymentSubscriptions(ctx context.Context, scope StatisticScope, associates []string) ([]StatisticSubscriptionSpan, error)
	PaymentLink(ctx context.Context, payment string, group *string) (StatisticPaymentLink, error)
}

// StatisticExpiredPayment is a payment of the expiredPayments widget with what
// get_course_carnet_name reads and its associate (select_related).
type StatisticExpiredPayment struct {
	PaymentID    string     `db:"payment_id"`
	Amount       string     `db:"amount"`
	PaymentDate  *time.Time `db:"payment_date"`
	CreationDate time.Time  `db:"creation_date"`
	Subject      int        `db:"subject"`
	Meta         *string    `db:"meta"`
	Course       *string    `db:"course"`
	Description  *string    `db:"description"`
	HasCategory  bool       `db:"has_category"`
	CategoryName *string    `db:"category_name"`
	AssociateID  string     `db:"associate_id"`
	FirstName    *string    `db:"first_name"`
	LastName     *string    `db:"last_name"`
}

// StatisticSubscriptionSpan is one row of the expiredPayments subscription
// query (one per matching payment of the associate).
type StatisticSubscriptionSpan struct {
	SubscriptionID string    `db:"subscription_id"`
	AssociateID    string    `db:"associate_id"`
	Start          time.Time `db:"start_date"`
	End            time.Time `db:"end_date"`
}

// StatisticPaymentLink is get_course_carnet_name's linked row: Slot 0 course
// subscription, 1 installment, 2 carnet subscription; -1 none.
type StatisticPaymentLink struct {
	Slot          int
	Title         *string
	MissingCarnet bool
}

type StatisticReportStore interface {
	ScopedAssociation(ctx context.Context, user string, scope *string) (string, error)
	Balance(ctx context.Context, user string) (StatisticBalance, error)
	SubscriptionMonths(ctx context.Context, scope StatisticScope, year StatisticYear, accepted bool) ([]StatisticMonthly, error)
	ActiveCourseCount(ctx context.Context, scope StatisticScope, year StatisticYear) (int64, error)
	CourseGroups(ctx context.Context, scope StatisticScope, year StatisticYear) ([]StatisticCourseGroup, error)
	StatusCounts(ctx context.Context, scope StatisticScope) ([]StatisticKeyCount, error)
	PaymentMonths(ctx context.Context, scope StatisticScope, year StatisticYear) ([]StatisticMonthly, error)
	PaymentTotals(ctx context.Context, scope StatisticScope, year StatisticYear) (StatisticPaymentTotals, error)
	Accepted(ctx context.Context, scope StatisticScope) ([]StatisticAccepted, error)
	InvoiceCount(ctx context.Context, scope StatisticScope, year StatisticYear) (int64, error)
}

type StatisticAssociationCard struct {
	ID               string  `db:"sport_association_id"`
	Denomination     string  `db:"denomination"`
	ReviewURL        *string `db:"review_url"`
	ReviewURLEnabled bool    `db:"review_url_enabled"`
	UserID           string  `db:"user_id"`
	AvatarImage      *string `db:"avatar_image"`
	FirstName        string  `db:"first_name"`
	LastName         string  `db:"last_name"`
	Username         string  `db:"username"`
}

type StatisticCommunication struct {
	Message string    `db:"message"`
	SentOn  time.Time `db:"sent_on"`
}

type StatisticAthleteSubscription struct {
	SubscriptionID string     `db:"subscription_id"`
	Start          *time.Time `db:"start_date"`
	End            *time.Time `db:"end_date"`
	Archived       bool       `db:"archived"`
	HasAssociate   bool       `db:"has_associate"`
	StatisticPerson
}

type StatisticAthleteCourse struct {
	CourseSubscriptionID string  `db:"course_subscription_id"`
	CourseID             string  `db:"course_id"`
	CourseTitle          *string `db:"course_title"`
}

type StatisticRegistry struct {
	ID     string  `db:"attendance_registry_id"`
	Events *string `db:"events"`
}

type StatisticAttendanceDay struct {
	ID               string  `db:"attendance_day_id"`
	Attendees        *string `db:"attendees"`
	ExpectedAbsences *string `db:"expected_absences"`
}

// StatisticAthleteScope narrows querysets like scoped_queryset for an
// association-scoped athlete session (Association nil otherwise).
type StatisticAthleteScope struct {
	Association *string
	Person      string
	Group       *string
}

type StatisticAthleteStore interface {
	PrimaryAssociation(ctx context.Context) (*string, bool, error)
	AssociationCard(ctx context.Context, association string) (StatisticAssociationCard, error)
	AthleteSubscriptions(ctx context.Context, scope StatisticAthleteScope, user, association string) ([]StatisticAthleteSubscription, error)
	Communications(ctx context.Context, association string, since time.Time) ([]StatisticCommunication, error)
	SubscriptionCourses(ctx context.Context, scope StatisticAthleteScope, subscription string) ([]StatisticAthleteCourse, error)
	CourseRegistries(ctx context.Context, scope StatisticAthleteScope, course string) ([]StatisticRegistry, error)
	FirstAttendanceDay(ctx context.Context, scope StatisticAthleteScope, registry string, day string) (*StatisticAttendanceDay, error)
}
