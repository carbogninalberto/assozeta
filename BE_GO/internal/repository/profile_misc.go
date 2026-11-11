package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

// ProfileMisc reads profile/associates/*, profile/image and stores
// onboarding/update writes.
type ProfileMisc struct{ db *sqlx.DB }

func NewProfileMisc(db *sqlx.DB) *ProfileMisc { return &ProfileMisc{db: db} }

var _ domain.ProfileAssociatesReader = (*ProfileMisc)(nil)
var _ domain.OnboardingStore = (*ProfileMisc)(nil)

type profileMiscQuery struct{ args []any }

func (q *profileMiscQuery) arg(value any) string {
	q.args = append(q.args, value)
	return "$" + strconv.Itoa(len(q.args))
}

// group is the GroupAwareManager filter on alias.
func (q *profileMiscQuery) group(alias string, scope domain.ProfileAssociateScope) string {
	if scope.Group == nil {
		return "true"
	}
	return alias + ".group_id=" + q.arg(*scope.Group) + "::uuid"
}

// members is scoped_queryset's family Associate set (group-aware managers).
func (q *profileMiscQuery) members(scope domain.ProfileAssociateScope) string {
	association, person := q.arg(*scope.Association)+"::uuid", q.arg(scope.Person)+"::uuid"
	return `SELECT m.associate_id FROM application_associate m WHERE m.sport_association_id=` + association +
		` AND NOT m.deleted AND ` + q.group("m", scope) + ` AND (m.user_id=` + person + ` OR m.tax_code IN (
  SELECT ta.tax_code FROM application_subscription ts JOIN application_associate ta ON ta.associate_id=ts.associate_id
  WHERE ts.user_id=` + person + ` AND ts.sport_association_id=` + association + ` AND ` + q.group("ts", scope) + `
   AND ta.tax_code IS NOT NULL AND ta.tax_code<>''))`
}

// subscriptions is scoped_queryset's family Subscription set.
func (q *profileMiscQuery) subscriptions(scope domain.ProfileAssociateScope) string {
	members := q.members(scope)
	return `SELECT vs.subscription_id FROM application_subscription vs WHERE vs.sport_association_id=` + q.arg(*scope.Association) +
		`::uuid AND ` + q.group("vs", scope) + ` AND (vs.user_id=` + q.arg(scope.Person) + `::uuid OR vs.associate_id IN (` + members + `))`
}

func (r *ProfileMisc) CourseAssociation(ctx context.Context, course string, scope domain.ProfileAssociateScope) (bool, *string, *string, error) {
	q := &profileMiscQuery{}
	where := `c.course_id=` + q.arg(course) + `::uuid AND NOT c.deleted AND ` + q.group("c", scope)
	if scope.Association != nil {
		where += ` AND c.sport_association_id=` + q.arg(*scope.Association) + `::uuid`
	}
	var row struct {
		Association *string `db:"sport_association_id"`
		Owner       *string `db:"user_id"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT c.sport_association_id::text AS sport_association_id, sa.user_id::text AS user_id
 FROM application_course c LEFT JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id
 WHERE `+where+` ORDER BY c.course_id LIMIT 1`, q.args...)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil, nil
	}
	if err != nil {
		return false, nil, nil, err
	}
	return true, row.Association, row.Owner, nil
}

func (r *ProfileMisc) Associates(ctx context.Context, users []string, enabledOnly bool, scope domain.ProfileAssociateScope) ([]domain.ProfileAssociate, error) {
	q := &profileMiscQuery{}
	where := `NOT a.deleted AND ` + q.group("a", scope) + ` AND a.user_id=ANY(` + q.arg(users) + `::uuid[])`
	if enabledOnly {
		where += ` AND NOT a.disabled`
	}
	if scope.Association != nil {
		where += ` AND a.sport_association_id=` + q.arg(*scope.Association) + `::uuid`
		if scope.Family {
			where += ` AND a.associate_id IN (` + q.members(scope) + `)`
		}
	}
	rows := []domain.ProfileAssociate{}
	err := r.db.SelectContext(ctx, &rows, `SELECT a.associate_id::text AS associate_id, a.first_name, a.last_name, a.is_minor
 FROM application_associate a WHERE `+where, q.args...)
	return rows, err
}

// subscriptionScope is scoped_queryset(Subscription.objects) on alias s.
func (q *profileMiscQuery) subscriptionScope(scope domain.ProfileAssociateScope) string {
	if scope.Association == nil {
		return ""
	}
	where := ` AND s.sport_association_id=` + q.arg(*scope.Association) + `::uuid`
	if scope.Family {
		where += ` AND s.subscription_id IN (` + q.subscriptions(scope) + `)`
	}
	return where
}

func (r *ProfileMisc) CourseSubscribed(ctx context.Context, course string, associates []string, scope domain.ProfileAssociateScope) ([]string, error) {
	q := &profileMiscQuery{}
	where := `cs.course_id=` + q.arg(course) + `::uuid AND NOT cs.deleted AND s.associate_id=ANY(` + q.arg(associates) + `::uuid[])`
	if scope.Association != nil {
		// scoped_queryset(CourseSubscription.objects): the subscription's and
		// the course's association, then the family subscriptions.
		association := q.arg(*scope.Association) + "::uuid"
		where += ` AND s.sport_association_id=` + association + ` AND c.sport_association_id=` + association
		if scope.Family {
			where += ` AND cs.subscription_id IN (` + q.subscriptions(scope) + `)`
		}
	}
	ids := []string{}
	err := r.db.SelectContext(ctx, &ids, `SELECT DISTINCT s.associate_id::text FROM application_coursesubscription cs
 JOIN application_subscription s ON s.subscription_id=cs.subscription_id JOIN application_course c ON c.course_id=cs.course_id
 WHERE `+where, q.args...)
	return ids, err
}

func (r *ProfileMisc) UserSubscriptions(ctx context.Context, user, association string, associates []string, scope domain.ProfileAssociateScope) ([]domain.ProfileAssociateSubscription, error) {
	q := &profileMiscQuery{}
	where := q.group("s", scope) + ` AND s.user_id=` + q.arg(user) + `::uuid AND s.associate_id=ANY(` + q.arg(associates) +
		`::uuid[]) AND s.sport_association_id=` + q.arg(association) + `::uuid`
	where += q.subscriptionScope(scope)
	rows := []domain.ProfileAssociateSubscription{}
	err := r.db.SelectContext(ctx, &rows, `SELECT s.subscription_id::text AS subscription_id, s.associate_id::text AS associate_id
 FROM application_subscription s WHERE `+where, q.args...)
	return rows, err
}

func (r *ProfileMisc) SeasonSubscriptions(ctx context.Context, association string, associates []string, from, to time.Time, scope domain.ProfileAssociateScope) ([]domain.ProfileAssociateSubscription, error) {
	q := &profileMiscQuery{}
	where := q.group("s", scope) + ` AND s.sport_association_id=` + q.arg(association) + `::uuid AND s.associate_id=ANY(` +
		q.arg(associates) + `::uuid[]) AND s.creation_date>=` + q.arg(from) + ` AND s.creation_date<=` + q.arg(to)
	where += q.subscriptionScope(scope)
	rows := []domain.ProfileAssociateSubscription{}
	err := r.db.SelectContext(ctx, &rows, `SELECT DISTINCT ON (s.associate_id) s.subscription_id::text AS subscription_id,
 s.associate_id::text AS associate_id FROM application_subscription s WHERE `+where+` ORDER BY s.associate_id, s.subscription_id`, q.args...)
	return rows, err
}

func (r *ProfileMisc) BalanceStart(ctx context.Context, user string) (int, int, error) {
	var row struct {
		Day   int `db:"balance_sheet_start_day"`
		Month int `db:"balance_sheet_start_month"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT balance_sheet_start_day, balance_sheet_start_month FROM bakney_user WHERE user_id=$1`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, 0, domain.ErrAccountNotFound
	}
	return row.Day, row.Month, err
}

func (r *ProfileMisc) Avatar(ctx context.Context, user string) (bool, *string, error) {
	var avatar *string
	err := r.db.GetContext(ctx, &avatar, `SELECT avatar_image FROM bakney_user WHERE user_id=$1::uuid AND NOT deleted`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	return err == nil, avatar, err
}

// --- onboarding/update -------------------------------------------------------------------------

type onboardingTransaction struct{ *ownedTransaction }

func (r *ProfileMisc) BeginOnboarding(ctx context.Context) (domain.OnboardingTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &onboardingTransaction{ownedTransaction: tx}, nil
}

func (t *onboardingTransaction) Association(ctx context.Context, owner string) (*domain.OnboardingAssociation, error) {
	var row domain.OnboardingAssociation
	err := t.tx.GetContext(ctx, &row, `SELECT sport_association_id::text AS sport_association_id, deleted, denomination, tax_code, logo,
 sport, subscription_fee::text AS subscription_fee, membership_fee::text AS membership_fee
 FROM application_sportassociation WHERE user_id=$1 FOR UPDATE`, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *onboardingTransaction) SaveAssociation(ctx context.Context, v domain.OnboardingAssociation) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_sportassociation SET denomination=$2, tax_code=$3, logo=$4, sport=$5,
 subscription_fee=$6::numeric, membership_fee=$7::numeric WHERE sport_association_id=$1`,
		v.ID, v.Denomination, v.TaxCode, v.Logo, v.Sport, v.SubscriptionFee, v.MembershipFee)
	return err
}

func (t *onboardingTransaction) FreshConfiguration(ctx context.Context, association string) (*domain.OnboardingConfiguration, error) {
	var row domain.OnboardingConfiguration
	err := t.tx.GetContext(ctx, &row, `SELECT c.id, c.onboarding_completed_at, a.user_id::text AS owner_id
 FROM instance_instanceconfiguration c JOIN application_sportassociation a ON a.sport_association_id=c.primary_association_id
 WHERE c.self_hosted AND c.setup_provenance='fresh' AND c.primary_association_id=$1 ORDER BY c.id LIMIT 1 FOR UPDATE OF c`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *onboardingTransaction) CompleteConfiguration(ctx context.Context, id int64, at time.Time) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE instance_instanceconfiguration SET onboarding_completed_at=$2, updated_at=$3 WHERE id=$1`,
		id, at, time.Now().UTC())
	return err
}

const onboardingUserColumns = `user_id::text AS user_id, first_name, last_name, balance_sheet_year, balance_sheet_start_day,
 balance_sheet_start_month, subscription_start_day, subscription_start_month, lead_sport_association_role,
 lead_sport_association_size, lead_sport_market_channel, phone`

func (t *onboardingTransaction) User(ctx context.Context, id string) (domain.OnboardingUser, error) {
	var row domain.OnboardingUser
	err := t.tx.GetContext(ctx, &row, `SELECT `+onboardingUserColumns+` FROM bakney_user WHERE user_id=$1 FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrAccountNotFound
	}
	return row, err
}

func (t *onboardingTransaction) SaveUser(ctx context.Context, v domain.OnboardingUser) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET balance_sheet_year=$2, balance_sheet_start_day=$3, balance_sheet_start_month=$4,
 subscription_start_day=$5, subscription_start_month=$6, lead_sport_association_role=$7, lead_sport_association_size=$8,
 lead_sport_market_channel=$9, phone=$10 WHERE user_id=$1`, v.ID, v.BalanceSheetYear, v.BalanceSheetStartDay, v.BalanceSheetStartMonth,
		v.SubscriptionStartDay, v.SubscriptionStartMonth, v.Role, v.Size, v.Channel, v.Phone)
	return err
}

// RefusedUserSave sends the UPDATE with literal values, as psycopg2 interpolates them, inside a savepoint and
// answers str(error): the message, then DETAIL when the server sent one.
func (t *onboardingTransaction) RefusedUserSave(ctx context.Context, user string, assignments []domain.OnboardingLiteral) (string, error) {
	const savepoint = "onboarding_user_literals"
	if _, err := t.tx.ExecContext(ctx, `SAVEPOINT `+savepoint); err != nil {
		return "", err
	}
	parts := make([]string, len(assignments))
	for i, item := range assignments {
		parts[i] = item.Column + "=" + item.Literal
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET `+strings.Join(parts, ", ")+` WHERE user_id=$1`, user)
	var refused *pgconn.PgError
	if !errors.As(err, &refused) {
		return "", err
	}
	if _, err = t.tx.ExecContext(ctx, `ROLLBACK TO SAVEPOINT `+savepoint); err != nil {
		return "", err
	}
	text := refused.Message + "\n"
	if refused.Detail != "" {
		text = refused.Message + "\nDETAIL:  " + refused.Detail + "\n"
	}
	return text, nil
}

func (t *onboardingTransaction) Flags(ctx context.Context, user string) ([]domain.OnboardingFlags, error) {
	rows, err := t.tx.QueryxContext(ctx, `SELECT users_onboarding_id::text AS users_onboarding_id, `+strings.Join(onboardingFlagColumns, ",")+`
 FROM application_usersonboarding WHERE user_id=$1 LIMIT 21 FOR UPDATE`, user)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []domain.OnboardingFlags
	for rows.Next() {
		values, err := rows.SliceScan()
		if err != nil {
			return nil, err
		}
		row := domain.OnboardingFlags{ID: values[0].(string), Flags: map[string]bool{}}
		for i, name := range onboardingFlagColumns {
			flag, ok := values[i+1].(bool)
			if !ok {
				return nil, errors.New("onboarding flag is not a boolean")
			}
			row.Flags[name] = flag
		}
		result = append(result, row)
	}
	return result, rows.Err()
}

var onboardingFlagColumns = []string{"show", "create_membership", "view_membership", "approve_payment", "download_invoice",
	"view_collaborators", "view_settings"}

func (t *onboardingTransaction) SaveFlags(ctx context.Context, v domain.OnboardingFlags) error {
	args := []any{v.ID}
	sets := make([]string, 0, len(onboardingFlagColumns))
	for _, name := range onboardingFlagColumns {
		args = append(args, v.Flags[name])
		sets = append(sets, name+"=$"+strconv.Itoa(len(args)))
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_usersonboarding SET `+strings.Join(sets, ",")+` WHERE users_onboarding_id=$1`, args...)
	return err
}

func (t *onboardingTransaction) AssociationOf(ctx context.Context, user string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

func (t *onboardingTransaction) AppendAudit(ctx context.Context, event domain.ProfileMiscAudit) error {
	var path *string
	if event.AssociationID != nil {
		path = &event.ResolutionPath
	}
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: event.Model, Key: event.Key, Representation: event.Representation,
		Action: 1, Changes: event.Changes, Context: event.Context, AssociationID: event.AssociationID, ResolutionPath: path, At: event.At})
}
