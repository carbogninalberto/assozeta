package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// SubscriptionTransfers persists subscription/<uid>/transfer. The view is
// not atomic: every statement and save() commits on its own.
type SubscriptionTransfers struct {
	db       *sqlx.DB
	accounts *OAuthAccounts
}

func NewSubscriptionTransfers(db *sqlx.DB) *SubscriptionTransfers {
	return &SubscriptionTransfers{db: db, accounts: NewOAuthAccounts(db)}
}

var _ domain.SubscriptionTransferStore = (*SubscriptionTransfers)(nil)

func (r *SubscriptionTransfers) begin(ctx context.Context) (*ownedTransaction, error) {
	return beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
}

// Subscription is scoped_queryset(request, Subscription.objects).get(...):
// the group-aware manager, the impersonation association when one is set.
func (r *SubscriptionTransfers) Subscription(ctx context.Context, id string, scope, group *string) (*domain.SubscriptionTransferRow, error) {
	var row domain.SubscriptionTransferRow
	err := r.db.GetContext(ctx, &row, `SELECT s.subscription_id, s.sport_association_id, s.user_id, s.associate_id,
 (s.start_date IS NOT NULL AND s.end_date IS NOT NULL) AS dates_set, a.denomination
 FROM application_subscription s JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 WHERE s.subscription_id=$1 AND ($2::uuid IS NULL OR s.sport_association_id=$2::uuid) AND ($3::uuid IS NULL OR s.group_id=$3::uuid)`,
		id, scope, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (r *SubscriptionTransfers) Pending(ctx context.Context, subscription string, notExpiredAt *time.Time) (bool, error) {
	var count int
	err := r.db.GetContext(ctx, &count, `SELECT COUNT(*) FROM application_subscriptiontransfer
 WHERE subscription_id=$1 AND status=1 AND ($2::timestamptz IS NULL OR NOT expires_at < $2::timestamptz)`, subscription, notExpiredAt)
	return count > 0, err
}

// ExpireTransfers is SubscriptionTransfer.objects.filter(expires_at__lt=now).update(status=EXPIRED) (no audit).
func (r *SubscriptionTransfers) ExpireTransfers(ctx context.Context, at time.Time) error {
	_, err := r.db.ExecContext(ctx, `UPDATE application_subscriptiontransfer SET status=4 WHERE expires_at < $1`, at)
	return err
}

// Recipient is User.objects.filter(user_id=id).exclude(user_id=requester).first().
func (r *SubscriptionTransfers) Recipient(ctx context.Context, id, requester string) (*string, error) {
	var found string
	err := r.db.GetContext(ctx, &found, `SELECT user_id FROM bakney_user WHERE user_id=$1 AND NOT deleted AND user_id<>$2
 ORDER BY user_id LIMIT 1`, id, requester)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &found, nil
}

const subscriptionTransferPersonColumns = `associate_id, sport_association_id, first_name, last_name, user_id`

func (r *SubscriptionTransfers) Associate(ctx context.Context, id string) (*domain.SubscriptionTransferPerson, error) {
	var row domain.SubscriptionTransferPerson
	err := r.db.GetContext(ctx, &row, `SELECT `+subscriptionTransferPersonColumns+` FROM application_associate WHERE associate_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// MainTutor reads get_main_tutor's relation: the first primary one, else the first one.
func (r *SubscriptionTransfers) MainTutor(ctx context.Context, associate string) (*domain.SubscriptionTransferTutor, error) {
	for _, primaryOnly := range []bool{true, false} {
		var row domain.SubscriptionTransferTutor
		err := r.db.GetContext(ctx, &row, `SELECT r.id, r.is_primary, t.associate_id AS "tutor.associate_id",
 t.sport_association_id AS "tutor.sport_association_id", t.first_name AS "tutor.first_name", t.last_name AS "tutor.last_name",
 t.user_id AS "tutor.user_id"
 FROM application_associatetutorrelation r JOIN application_associate t ON t.associate_id=r.tutor_id
 WHERE r.associate_id=$1 AND (NOT $2 OR r.is_primary) ORDER BY r.id LIMIT 1`, associate, primaryOnly)
		if errors.Is(err, sql.ErrNoRows) {
			continue
		}
		if err != nil {
			return nil, err
		}
		return &row, nil
	}
	return nil, nil
}

// MakePrimary is tutor_relation.is_primary = True; save(): its update entry,
// indexed through the relation's associate.
func (r *SubscriptionTransfers) MakePrimary(ctx context.Context, tutor domain.SubscriptionTransferTutor, association string,
	audit domain.LoginAuditContext, at time.Time) error {
	tx, err := r.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var primary bool
	if err = tx.tx.GetContext(ctx, &primary, `SELECT is_primary FROM application_associatetutorrelation WHERE id=$1 FOR UPDATE`,
		tutor.RelationID); err != nil {
		return err
	}
	if _, err = tx.tx.ExecContext(ctx, `UPDATE application_associatetutorrelation SET is_primary=TRUE WHERE id=$1`, tutor.RelationID); err != nil {
		return err
	}
	if !primary {
		key := strconv.FormatInt(tutor.RelationID, 10)
		path := "associate.sport_association"
		id := tutor.RelationID
		if err = appendModelAudit(ctx, tx.tx, modelAuditEntry{Model: "associatetutorrelation", Key: key,
			Representation: "AssociateTutorRelation object (" + key + ")", Action: 1,
			Changes: map[string][2]string{"is_primary": {"False", "True"}}, Context: audit, AssociationID: &association,
			ResolutionPath: &path, At: at, ObjectID: &id}); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// OwnerAssociation is request.user.sport_association for an owner:
// SportAssociation.objects.get(user=...) (none or several: an error).
func (r *SubscriptionTransfers) OwnerAssociation(ctx context.Context, user string) (*domain.SubscriptionTransferAssociation, error) {
	var rows []domain.SubscriptionTransferAssociation
	if err := r.db.SelectContext(ctx, &rows, `SELECT sport_association_id, denomination FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted LIMIT 2`, user); err != nil {
		return nil, err
	}
	if len(rows) != 1 {
		return nil, domain.ErrAssociationNotFound
	}
	return &rows[0], nil
}

func (r *SubscriptionTransfers) InstanceConfig(ctx context.Context) (domain.EmailRenderInstanceConfig, error) {
	return r.accounts.InstanceConfig(ctx)
}

// CreateUser is User.objects.create_user(...) with its create entry.
func (r *SubscriptionTransfers) CreateUser(ctx context.Context, user domain.OAuthNewUser, audit domain.LoginAuditContext) error {
	return r.accounts.CreateUser(ctx, user, audit)
}

// CreateTransfer is SubscriptionTransferCreateSerializer.save(): the row
// (status PENDING) and its create entry, indexed through the subscription.
func (r *SubscriptionTransfers) CreateTransfer(ctx context.Context, transfer domain.SubscriptionTransferCreated, audit domain.LoginAuditContext) error {
	tx, err := r.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.tx.ExecContext(ctx, `INSERT INTO application_subscriptiontransfer
 (subscription_transfer_id, subscription_id, created_at, expires_at, requester_id, recipient_id, token, status)
 VALUES ($1,$2,$3,$4,$5,$6,$7,1)`, transfer.ID, transfer.SubscriptionID, transfer.Created, transfer.Expires, transfer.Requester,
		transfer.Recipient, transfer.Token); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, tx.tx, "application_subscriptiontransfer", "subscription_transfer_id", transfer.ID)
	if err != nil {
		return err
	}
	association := transfer.AssociationID
	path := "subscription.sport_association"
	if err = appendModelAudit(ctx, tx.tx, modelAuditEntry{Model: "subscriptiontransfer", Key: transfer.ID,
		Representation: "SubscriptionTransfer object (" + transfer.ID + ")", Action: 0, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: transfer.Created}); err != nil {
		return err
	}
	return tx.Commit()
}

func (r *SubscriptionTransfers) SubscriptionSaveSeason(ctx context.Context, subscription string) (domain.SubscriptionSaveSeason, error) {
	return modelHooksSubscriptionSeason(ctx, r.db, subscription)
}

// SaveSubscriptionUser is subscription.user = recipient; subscription.save()
// (the dates are set: Subscription.save() changes nothing else).
func (r *SubscriptionTransfers) SaveSubscriptionUser(ctx context.Context, row domain.SubscriptionTransferRow, user string,
	audit domain.LoginAuditContext, at time.Time) error {
	return r.saveUser(ctx, "application_subscription", "subscription_id", "subscription", "Subscription", row.ID, row.AssociationID, user,
		audit, at)
}

// SaveAssociateUser is associate.user = recipient; associate.save().
func (r *SubscriptionTransfers) SaveAssociateUser(ctx context.Context, person domain.SubscriptionTransferPerson, user string,
	audit domain.LoginAuditContext, at time.Time) error {
	return r.saveUser(ctx, "application_associate", "associate_id", "associate", "Associate", person.ID, person.AssociationID, user, audit, at)
}

// saveUser writes user_id and auditlog's update entry when the stored value
// changes, indexed directly to the row's association.
func (r *SubscriptionTransfers) saveUser(ctx context.Context, table, key, model, name, id, association, user string,
	audit domain.LoginAuditContext, at time.Time) error {
	tx, err := r.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var old *string
	if err = tx.tx.GetContext(ctx, &old, `SELECT user_id FROM `+table+` WHERE `+key+`=$1 FOR UPDATE`, id); err != nil {
		return err
	}
	if _, err = tx.tx.ExecContext(ctx, `UPDATE `+table+` SET user_id=$2 WHERE `+key+`=$1`, id, user); err != nil {
		return err
	}
	if old == nil || *old != user {
		previous := "None"
		if old != nil {
			previous = *old
		}
		path := "direct"
		if err = appendModelAudit(ctx, tx.tx, modelAuditEntry{Model: model, Key: id, Representation: name + " object (" + id + ")", Action: 1,
			Changes: map[string][2]string{"user": {previous, user}}, Context: audit, AssociationID: &association, ResolutionPath: &path,
			At: at}); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// AcceptTransfer is transfer.status = ACCEPTED; transfer.expires_at = now(); transfer.save().
func (r *SubscriptionTransfers) AcceptTransfer(ctx context.Context, transfer domain.SubscriptionTransferCreated, at time.Time,
	audit domain.LoginAuditContext) error {
	tx, err := r.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var stored struct {
		Expires time.Time `db:"expires_at"`
		Status  int       `db:"status"`
	}
	if err = tx.tx.GetContext(ctx, &stored, `SELECT expires_at, status FROM application_subscriptiontransfer
 WHERE subscription_transfer_id=$1 FOR UPDATE`, transfer.ID); err != nil {
		return err
	}
	if _, err = tx.tx.ExecContext(ctx, `UPDATE application_subscriptiontransfer SET status=2, expires_at=$2 WHERE subscription_transfer_id=$1`,
		transfer.ID, at); err != nil {
		return err
	}
	changes := map[string][2]string{}
	if !stored.Expires.Equal(at) {
		changes["expires_at"] = [2]string{pythonNaiveUTC(stored.Expires), pythonNaiveUTC(at)}
	}
	if stored.Status != 2 {
		changes["status"] = [2]string{strconv.Itoa(stored.Status), "2"}
	}
	if len(changes) > 0 {
		association := transfer.AssociationID
		path := "subscription.sport_association"
		if err = appendModelAudit(ctx, tx.tx, modelAuditEntry{Model: "subscriptiontransfer", Key: transfer.ID,
			Representation: "SubscriptionTransfer object (" + transfer.ID + ")", Action: 1, Changes: changes, Context: audit,
			AssociationID: &association, ResolutionPath: &path, At: at}); err != nil {
			return err
		}
	}
	return tx.Commit()
}
