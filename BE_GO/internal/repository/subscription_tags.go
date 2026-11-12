package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SubscriptionTags struct{ db *sqlx.DB }

func NewSubscriptionTags(db *sqlx.DB) *SubscriptionTags { return &SubscriptionTags{db: db} }

type subscriptionTagTransaction struct{ *paymentCategoryTransaction }

func (r *SubscriptionTags) BeginSubscriptionTags(ctx context.Context) (domain.SubscriptionTagTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &subscriptionTagTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *subscriptionTagTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

func (t *subscriptionTagTransaction) OrderedTags(ctx context.Context, association string) ([]domain.SearchProfileTag, error) {
	rows := []domain.SearchProfileTag{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT tag_id, tag_name FROM application_tags WHERE sport_association_id=$1 ORDER BY tag_name`, association)
	return rows, err
}

func (t *subscriptionTagTransaction) TagOf(ctx context.Context, id, association string) (*string, bool, error) {
	var name string
	err := t.tx.GetContext(ctx, &name, `SELECT tag_name FROM application_tags WHERE tag_id=$1 AND sport_association_id=$2
 ORDER BY tag_id LIMIT 1 FOR UPDATE`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	return &name, true, nil
}

func (t *subscriptionTagTransaction) SubscriptionOf(ctx context.Context, id, association string, group *string) (*domain.SubscriptionTagTarget, error) {
	var row struct {
		Created                                          time.Time
		StartDate, EndDate                               sql.NullTime
		StartDay, StartMonth, EndDay, EndMonth, Duration int
		CustomEnd                                        bool
	}
	err := t.tx.GetContext(ctx, &row, `SELECT s.creation_date AS created,s.start_date AS startdate,s.end_date AS enddate,
 u.subscription_start_day AS startday,u.subscription_start_month AS startmonth,
 u.subscription_end_day AS endday,u.subscription_end_month AS endmonth,u.custom_end_date AS customend,u.subscription_duration AS duration
 FROM application_subscription s
 JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 JOIN bakney_user u ON u.user_id=a.user_id
 WHERE s.subscription_id=$1 AND s.sport_association_id=$2 AND ($3::uuid IS NULL OR s.group_id=$3::uuid)
 ORDER BY s.subscription_id LIMIT 1 FOR UPDATE OF s`, id, association, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, domain.ErrSubscriptionTagSubscriptionNotFound
	}
	if err != nil {
		return nil, err
	}
	date := func(d sql.NullTime) *domain.CalendarDate {
		if !d.Valid {
			return nil
		}
		y, m, day := d.Time.Date()
		return &domain.CalendarDate{Year: y, Month: int(m), Day: day}
	}
	return &domain.SubscriptionTagTarget{Created: row.Created.UTC(), Dates: domain.SubscriptionDates{Start: date(row.StartDate), End: date(row.EndDate)},
		Rule: domain.SubscriptionDateRule{Duration: row.Duration, Season: domain.CalendarRule{StartDay: row.StartDay, StartMonth: row.StartMonth,
			CustomEnd: row.CustomEnd, EndDay: row.EndDay, EndMonth: row.EndMonth}}}, nil
}

// SetLink is `tag not in subscription.tags.all()` followed by add() or remove().
func (t *subscriptionTagTransaction) SetLink(ctx context.Context, tag, subscription string, linked bool) (bool, error) {
	query := `INSERT INTO application_subscription_tags (subscription_id,tags_id) SELECT $1,$2
 WHERE NOT EXISTS (SELECT 1 FROM application_subscription_tags WHERE subscription_id=$1 AND tags_id=$2)`
	if !linked {
		query = `DELETE FROM application_subscription_tags WHERE subscription_id=$1 AND tags_id=$2`
	}
	result, err := t.tx.ExecContext(ctx, query, subscription, tag)
	if err != nil {
		return false, err
	}
	count, err := result.RowsAffected()
	return count > 0, err
}

func calendarText(d *domain.CalendarDate) string {
	if d == nil {
		return "None"
	}
	return fmt.Sprintf("%04d-%02d-%02d", d.Year, d.Month, d.Day)
}

func calendarValue(d *domain.CalendarDate) *string {
	if d == nil {
		return nil
	}
	text := calendarText(d)
	return &text
}

// SaveSubscriptionDates is the visible effect of Subscription.save(): every
// other field is written back unchanged, so auditlog records only the dates.
func (t *subscriptionTagTransaction) SaveSubscriptionDates(ctx context.Context, id, association string, old, dates domain.SubscriptionDates,
	audit domain.LoginAuditContext, at time.Time) error {
	changes := map[string][2]string{}
	if before, after := calendarText(old.Start), calendarText(dates.Start); before != after {
		changes["start_date"] = [2]string{before, after}
	}
	if before, after := calendarText(old.End), calendarText(dates.End); before != after {
		changes["end_date"] = [2]string{before, after}
	}
	if len(changes) == 0 {
		return nil
	}
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET start_date=$2::date,end_date=$3::date WHERE subscription_id=$1`,
		id, calendarValue(dates.Start), calendarValue(dates.End)); err != nil {
		return err
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "subscription", Key: id, Representation: "Subscription object (" + id + ")",
		Action: 1, Changes: changes, Context: audit, AssociationID: &association, ResolutionPath: &path, At: at})
}

func subscriptionTagAudit(id string, action int, changes map[string][2]string, association *string, path string, audit domain.LoginAuditContext,
	at time.Time) modelAuditEntry {
	entry := modelAuditEntry{Model: "tags", Key: id, Representation: "Tags object (" + id + ")", Action: action, Changes: changes,
		Context: audit, At: at}
	if association != nil {
		entry.AssociationID, entry.ResolutionPath = association, &path
	}
	return entry
}

func (t *subscriptionTagTransaction) CreateTag(ctx context.Context, id, name, association string, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_tags (tag_id,tag_name,sport_association_id,creation_date) VALUES ($1,$2,$3,$4)`,
		id, name, association, at); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_tags", "tag_id", id)
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, subscriptionTagAudit(id, 0, changes, &association, "direct", audit, at))
}

func (t *subscriptionTagTransaction) RenameTag(ctx context.Context, id, name, association string, changes map[string][2]string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_tags SET tag_name=$2 WHERE tag_id=$1`, id, name); err != nil {
		return err
	}
	if len(changes) == 0 {
		return nil
	}
	return appendModelAudit(ctx, t.tx, subscriptionTagAudit(id, 1, changes, &association, "direct", audit, at))
}

func (t *subscriptionTagTransaction) DeleteTag(ctx context.Context, id string, audit domain.LoginAuditContext, at time.Time) error {
	// The collector deletes the Subscription.tags through rows first.
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_subscription_tags WHERE tags_id=$1`, id); err != nil {
		return err
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_tags", "tag_id", id)
	if err != nil {
		return err
	}
	var association *string
	if value := changes["sport_association"][0]; value != "" && value != "None" {
		association = &value
	}
	return appendModelAudit(ctx, t.tx, subscriptionTagAudit(id, 2, changes, association, "changes_json.sport_association", audit, at))
}
