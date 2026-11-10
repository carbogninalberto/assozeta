package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// --- AssociateSerializer.create's associate_id branch (subscription-renew.md) ---

func (t *subscriptionCreateTransaction) oneAssociate(ctx context.Context, query string, args ...any) (*domain.SubscriptionEditAssociate, error) {
	var row domain.SubscriptionEditAssociate
	err := t.tx.GetContext(ctx, &row, `SELECT `+subscriptionEditColumns+` FROM application_associate WHERE `+query, args...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *subscriptionCreateTransaction) AssociateAny(ctx context.Context, id string) (*domain.SubscriptionEditAssociate, error) {
	return t.oneAssociate(ctx, `associate_id=$1 FOR UPDATE`, id)
}

func (t *subscriptionCreateTransaction) AssociateByTaxCode(ctx context.Context, taxCode, association string) (*domain.SubscriptionEditAssociate, error) {
	return t.oneAssociate(ctx, `UPPER(tax_code::text)=UPPER($1) AND sport_association_id=$2 ORDER BY associate_id LIMIT 1 FOR UPDATE`,
		taxCode, association)
}

func (t *subscriptionCreateTransaction) LiveTaxCodeTaken(ctx context.Context, taxCode, association, except string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_associate WHERE tax_code IS NOT NULL AND UPPER(tax_code)=UPPER($1)
 AND sport_association_id=$2 AND NOT deleted AND associate_id<>$3`, taxCode, association, except)
}

func (t *subscriptionCreateTransaction) SetAssociateDeleted(ctx context.Context, id string, deleted bool) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_associate SET deleted=$2 WHERE associate_id=$1`, id, deleted)
	return err
}

func (t *subscriptionCreateTransaction) NamedAssociations(ctx context.Context, username string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT a.sport_association_id FROM application_sportassociation a
 JOIN bakney_user u ON u.user_id=a.user_id WHERE u.username=$1 AND NOT u.deleted AND NOT a.deleted ORDER BY a.sport_association_id`, username)
	return ids, err
}

func (t *subscriptionCreateTransaction) AssociationCount(ctx context.Context, user string) (int, error) {
	var count int
	err := t.tx.GetContext(ctx, &count, `SELECT COUNT(*) FROM application_sportassociation WHERE user_id=$1 AND NOT deleted`, user)
	return count, err
}

// --- subscription/renew ---

func (t *subscriptionRenewTransaction) RenewAssociationOwner(ctx context.Context, id string) (*domain.Account, error) {
	var owner domain.Account
	err := t.tx.GetContext(ctx, &owner, `SELECT u.user_id, u.role FROM application_sportassociation a
 JOIN bakney_user u ON u.user_id=a.user_id WHERE a.sport_association_id=$1 AND NOT a.deleted`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &owner, nil
}

type SubscriptionRenewals struct{ db *sqlx.DB }

func NewSubscriptionRenewals(db *sqlx.DB) *SubscriptionRenewals { return &SubscriptionRenewals{db: db} }

type subscriptionRenewTransaction struct {
	*subscriptionCreateTransaction
	courses *courseSubscriptionTransaction
}

func (r *SubscriptionRenewals) BeginSubscriptionRenew(ctx context.Context) (domain.SubscriptionRenewTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &subscriptionRenewTransaction{subscriptionCreateTransaction: &subscriptionCreateTransaction{&subscriptionEditTransaction{ownedTransaction: tx}},
		courses: &courseSubscriptionTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

func (t *subscriptionRenewTransaction) RenewalPayments(ctx context.Context, association string, first, last, tax *string, start, end time.Time,
	except, scope *string) ([]string, bool, error) {
	rows := []struct {
		ID       string `db:"payment_id"`
		Invoiced bool   `db:"invoiced"`
	}{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT p.payment_id, p.invoice_id IS NOT NULL AS invoiced FROM application_payment p
 INNER JOIN application_associate a ON a.associate_id=p.associate_id
 WHERE a.first_name IS NOT DISTINCT FROM $2 AND a.last_name IS NOT DISTINCT FROM $3 AND `+iexact("a.tax_code", 4)+`
 AND p.sport_association_id=$1 AND p.subject=1 AND p.creation_date >= $5 AND p.creation_date <= $6 AND NOT p.paid AND NOT p.deleted
 AND ($7::uuid IS NULL OR p.payment_id<>$7::uuid) AND ($8::uuid IS NULL OR p.sport_association_id=$8::uuid)
 ORDER BY p.payment_id FOR UPDATE OF p`, association, first, last, tax, start, end, except, scope); err != nil {
		return nil, false, err
	}
	ids, invoiced := make([]string, 0, len(rows)), false
	for _, row := range rows {
		ids = append(ids, row.ID)
		invoiced = invoiced || row.Invoiced
	}
	return ids, invoiced, nil
}

func (t *subscriptionRenewTransaction) DeletePayments(ctx context.Context, ids []string, audit domain.CourseSubscriptionAudit) error {
	snapshots := make([]*courseSubscriptionSnapshot, 0, len(ids))
	for _, id := range ids {
		snapshot, err := t.courses.snapshot(ctx, "application_payment", "payment_id", id)
		if err != nil {
			return err
		}
		if snapshot != nil {
			snapshots = append(snapshots, snapshot)
		}
	}
	if len(snapshots) == 0 {
		return nil
	}
	return t.courses.deletePayments(ctx, snapshots, true, audit)
}

func (t *subscriptionRenewTransaction) UserByEmail(ctx context.Context, email *string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT user_id FROM bakney_user WHERE NOT deleted AND email IS NOT DISTINCT FROM $1
 ORDER BY user_id LIMIT 1`, email)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

func (t *subscriptionRenewTransaction) SetSubscriptionUser(ctx context.Context, subscription, user string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET user_id=$2 WHERE subscription_id=$1`, subscription, user)
	return err
}

func (t *subscriptionRenewTransaction) AddTags(ctx context.Context, subscription string, ids []string) error {
	if len(ids) == 0 {
		return nil
	}
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_subscription_tags (subscription_id, tags_id)
 SELECT $1, tag_id FROM application_tags WHERE tag_id=ANY($2::uuid[])
 AND sport_association_id=(SELECT sport_association_id FROM application_subscription WHERE subscription_id=$1) ORDER BY tag_id`, subscription, ids)
	return err
}

// --- subscription/associates-draft/list/approve ---

type SubscriptionDraftApprovals struct{ db *sqlx.DB }

func NewSubscriptionDraftApprovals(db *sqlx.DB) *SubscriptionDraftApprovals {
	return &SubscriptionDraftApprovals{db: db}
}

type subscriptionDraftApproveTransaction struct{ *subscriptionCreateTransaction }

func (r *SubscriptionDraftApprovals) BeginSubscriptionDraftApprove(ctx context.Context) (domain.SubscriptionDraftApproveTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &subscriptionDraftApproveTransaction{&subscriptionCreateTransaction{&subscriptionEditTransaction{ownedTransaction: tx}}}, nil
}

func (t *subscriptionDraftApproveTransaction) Savepoint(ctx context.Context) error {
	_, err := t.tx.ExecContext(ctx, `SAVEPOINT draft_approve`)
	return err
}

func (t *subscriptionDraftApproveTransaction) RollbackSavepoint(ctx context.Context) error {
	_, err := t.tx.ExecContext(ctx, `ROLLBACK TO SAVEPOINT draft_approve`)
	return err
}

func (t *subscriptionDraftApproveTransaction) ReleaseSavepoint(ctx context.Context) error {
	_, err := t.tx.ExecContext(ctx, `RELEASE SAVEPOINT draft_approve`)
	return err
}

// draftFilter is the view's filter: the association, then the listed keys
// (IN with one parameter per key, as Django writes it).
func draftFilter(association string, all bool, ids []string) (string, []any) {
	args := []any{association}
	where := `sport_association_id=$1`
	if !all {
		if len(ids) == 0 {
			return where + ` AND false`, args
		}
		holders := make([]string, len(ids))
		for index, id := range ids {
			args = append(args, id)
			holders[index] = fmt.Sprintf("$%d::uuid", index+2)
		}
		where += ` AND associate_import_draft_id IN (` + strings.Join(holders, ", ") + `)`
	}
	return where, args
}

func (t *subscriptionDraftApproveTransaction) DraftCount(ctx context.Context, association string, all bool, ids []string) (int, error) {
	where, args := draftFilter(association, all, ids)
	var count int
	err := t.tx.GetContext(ctx, &count, `SELECT COUNT(*) FROM application_associateimportdraft WHERE `+where, args...)
	return count, err
}

func (t *subscriptionDraftApproveTransaction) ValidDrafts(ctx context.Context, association string, all bool, ids []string) ([]domain.SubscriptionDraftApproveRow, error) {
	where, args := draftFilter(association, all, ids)
	rows := []domain.SubscriptionDraftApproveRow{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT associate_import_draft_id, data::text AS data FROM application_associateimportdraft
 WHERE `+where+` AND valid`, args...)
	return rows, err
}

func importStatusEntry(id, association, path string, action int, changes map[string][2]string, audit domain.LoginAuditContext,
	at time.Time) modelAuditEntry {
	return modelAuditEntry{Model: "associateimportdraftstatus", Key: id, Representation: "AssociateImportDraftStatus object (" + id + ")",
		Action: action, Changes: changes, Context: audit, AssociationID: &association, ResolutionPath: &path, At: at}
}

func (t *subscriptionDraftApproveTransaction) DeleteApprovingStatuses(ctx context.Context, association string, audit domain.LoginAuditContext,
	at time.Time) error {
	ids := []string{}
	if err := t.tx.SelectContext(ctx, &ids, `SELECT associate_import_draft_status_id FROM application_associateimportdraftstatus
 WHERE sport_association_id=$1 AND status_type=2 ORDER BY associate_import_draft_status_id FOR UPDATE`, association); err != nil {
		return err
	}
	deleted := make([]map[string][2]string, len(ids))
	for index, id := range ids {
		changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_associateimportdraftstatus", "associate_import_draft_status_id", id)
		if err != nil {
			return err
		}
		deleted[index] = changes
	}
	// The collector sends post_delete in reverse key order; the rows are gone, so the index comes from the changes.
	for index := len(ids) - 1; index >= 0; index-- {
		if err := appendModelAudit(ctx, t.tx, importStatusEntry(ids[index], association, "changes_json.sport_association", 2, deleted[index],
			audit, at)); err != nil {
			return err
		}
	}
	return nil
}

func (t *subscriptionDraftApproveTransaction) CreateApprovingStatus(ctx context.Context, association string, audit domain.LoginAuditContext,
	at time.Time) error {
	id := uuid.NewString()
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_associateimportdraftstatus (associate_import_draft_status_id,
 sport_association_id, doing, status_type, creation_date) VALUES ($1,$2,true,2,$3)`, id, association, at); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_associateimportdraftstatus", "associate_import_draft_status_id", id)
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, importStatusEntry(id, association, "direct", 0, changes, audit, at))
}

func (t *subscriptionDraftApproveTransaction) DeleteDraft(ctx context.Context, id, dataRepr string, audit domain.LoginAuditContext, at time.Time) error {
	changes, stored, err := deleteModelWithAudit(ctx, t.tx, "application_associateimportdraft", "associate_import_draft_id", id)
	if err != nil {
		return err
	}
	changes["data"] = [2]string{dataRepr, "None"}
	entry := modelAuditEntry{Model: "associateimportdraft", Key: id, Representation: "AssociateImportDraft object (" + id + ")", Action: 2,
		Changes: changes, Context: audit, At: at}
	if association, ok := stored["sport_association_id"].(string); ok {
		var live bool
		if err = t.tx.GetContext(ctx, &live, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1
 AND NOT deleted)`, association); err != nil {
			return err
		}
		if live {
			path := "changes_json.sport_association"
			entry.AssociationID, entry.ResolutionPath = &association, &path
		}
	}
	return appendModelAudit(ctx, t.tx, entry)
}

// ValidateBroadcast is SubscriptionNotifications.ValidateBroadcast in this transaction.
func (t *subscriptionDraftApproveTransaction) ValidateBroadcast(ctx context.Context, user, name string, audit domain.LoginAuditContext,
	at time.Time) (bool, error) {
	var broadcast string
	err := t.tx.GetContext(ctx, &broadcast, `SELECT broadcast_id FROM notifications_notificationsbroadcast WHERE broadcast_name=$1
 ORDER BY broadcast_id DESC LIMIT 1`, name)
	if errors.Is(err, sql.ErrNoRows) {
		broadcast = uuid.NewString()
		if _, err = t.tx.ExecContext(ctx, `INSERT INTO notifications_notificationsbroadcast (broadcast_id, broadcast_name) VALUES ($1,$2)`,
			broadcast, name); err != nil {
			return false, err
		}
		if err = notificationAudit(ctx, t.tx, "notificationsbroadcast", "NotificationsBroadcast", "notifications_notificationsbroadcast",
			"broadcast_id", broadcast, audit, at); err != nil {
			return false, err
		}
	} else if err != nil {
		return false, err
	}
	var count int
	if err = t.tx.GetContext(ctx, &count, `SELECT COUNT(*) FROM notifications_usernotificationsbroadcast WHERE broadcast_id=$1 AND user_id=$2`,
		broadcast, user); err != nil {
		return false, err
	}
	if count != 0 {
		return false, nil
	}
	id := uuid.NewString()
	if _, err = t.tx.ExecContext(ctx, `INSERT INTO notifications_usernotificationsbroadcast (user_broadcast_id, user_id, broadcast_id)
 VALUES ($1,$2,$3)`, id, user, broadcast); err != nil {
		return false, err
	}
	return true, notificationAudit(ctx, t.tx, "usernotificationsbroadcast", "UserNotificationsBroadcast", "notifications_usernotificationsbroadcast",
		"user_broadcast_id", id, audit, at)
}

var (
	_ domain.SubscriptionRenewStore        = (*SubscriptionRenewals)(nil)
	_ domain.SubscriptionDraftApproveStore = (*SubscriptionDraftApprovals)(nil)
)
