package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// MiscRoutes serves communications/send/post and subscription/<uid>/card.
type MiscRoutes struct{ db *sqlx.DB }

func NewMiscRoutes(db *sqlx.DB) *MiscRoutes { return &MiscRoutes{db: db} }

var _ domain.MiscRoutesStore = (*MiscRoutes)(nil)

type miscRoutesTransaction struct{ *subscriptionMiscTransaction }

func (r *MiscRoutes) BeginMiscRoutes(ctx context.Context) (domain.MiscRoutesTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &miscRoutesTransaction{subscriptionMiscTransaction: &subscriptionMiscTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

// OwnerAssociation is SportAssociation.objects.get(user=...) (ActiveManager).
func (t *miscRoutesTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

// CreatePost is Message.objects.create (audited, indexed directly) and
// MessageTransaction.objects.create (not audited) of configuration_send_post.
func (t *miscRoutesTransaction) CreatePost(ctx context.Context, messageID, deliveryID, association, message string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO communications_message (message_id, sport_association_id, type, message, subject, created_at)
 VALUES ($1, $2, 'INSIDE_APP', $3, NULL, $4)`, messageID, association, message, at); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "communications_message", "message_id", messageID)
	if err != nil {
		return err
	}
	path := "direct"
	if err = appendModelAuditForApp(ctx, t.tx, "communications", modelAuditEntry{Model: "message", Key: messageID,
		Representation: "Message object (" + messageID + ")", Action: 0, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at}); err != nil {
		return err
	}
	_, err = t.tx.ExecContext(ctx, `INSERT INTO communications_messagetransaction (message_transaction_id, message_id, recipient, sent_on,
 delivered_on, opened_on, clicked_on, bounced_on, bounced_reason, bounced_type, bounced_status)
 VALUES ($1, $2, $3, $4, NULL, NULL, NULL, NULL, NULL, NULL, NULL)`, deliveryID, messageID, association, at)
	return err
}

// CardToken is SubscriptionToken.objects.filter(token=..., subscription_id=...).first().
func (t *miscRoutesTransaction) CardToken(ctx context.Context, token, subscription string) (*domain.SubscriptionCardToken, error) {
	var row domain.SubscriptionCardToken
	err := t.tx.GetContext(ctx, &row, `SELECT subscription_token_id::text AS subscription_token_id, expiration_date
 FROM application_subscriptiontoken WHERE token=$1::uuid AND subscription_id=$2::uuid ORDER BY subscription_token_id LIMIT 1`, token, subscription)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// DeleteCardToken is subscription_token.delete(): the auditlog delete is
// indexed through the actor's association (the row and its changes no longer
// resolve one), or not at all for anonymous requests.
func (t *miscRoutesTransaction) DeleteCardToken(ctx context.Context, id string, actorAssociation *string, audit domain.LoginAuditContext,
	at time.Time) error {
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_subscriptiontoken", "subscription_token_id", id)
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "subscriptiontoken", Key: id,
		Representation: "SubscriptionToken object (" + id + ")", Action: 2, Changes: changes, Context: audit,
		AssociationID: actorAssociation, ResolutionPath: associateDraftActorPath(actorAssociation), At: at})
}

// CardMember is the scoped, group-aware Subscription.objects row (deleted rows
// included) with its associate, association and the association's user.
func (t *miscRoutesTransaction) CardMember(ctx context.Context, subscription string, group, scope *string) (*domain.SubscriptionCardMember, error) {
	var row domain.SubscriptionCardMember
	err := t.tx.GetContext(ctx, &row, `SELECT s.subscription_number, s.subscription_type, s.end_date, s.associate_id IS NOT NULL AS has_associate,
 a.first_name, a.last_name, s.sport_association_id::text AS sport_association_id, sa.denomination, u.avatar_image
 FROM application_subscription s LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id
 JOIN bakney_user u ON u.user_id=sa.user_id
 WHERE s.subscription_id=$1::uuid AND ($2::uuid IS NULL OR s.group_id=$2::uuid) AND ($3::uuid IS NULL OR s.sport_association_id=$3::uuid)
 ORDER BY s.subscription_id LIMIT 1`, subscription, group, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// CardConfiguration is the association's first membership card configuration.
func (t *miscRoutesTransaction) CardConfiguration(ctx context.Context, association string) (*domain.SubscriptionCardConfiguration, error) {
	var row domain.SubscriptionCardConfiguration
	err := t.tx.GetContext(ctx, &row, `SELECT emit_only_on_approval, customized_template::text AS customized_template
 FROM application_sportassociationmembershipcardconfiguration WHERE sport_association_id=$1::uuid
 ORDER BY sport_association_membership_card_configuration_id LIMIT 1`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// CreateCardConfiguration saves the default configuration (audited, indexed directly).
func (t *miscRoutesTransaction) CreateCardConfiguration(ctx context.Context, id, association string, audit domain.LoginAuditContext,
	at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_sportassociationmembershipcardconfiguration
 (sport_association_membership_card_configuration_id, sport_association_id, emit_only_on_approval, customized_template)
 VALUES ($1, $2, false, NULL)`, id, association); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_sportassociationmembershipcardconfiguration",
		"sport_association_membership_card_configuration_id", id)
	if err != nil {
		return err
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "sportassociationmembershipcardconfiguration", Key: id,
		Representation: "SportAssociationMembershipCardConfiguration object (" + id + ")", Action: 0, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at})
}

// ScopedSubscription is the scoped, group-aware subscription of the association.
func (t *miscRoutesTransaction) ScopedSubscription(ctx context.Context, subscription, association string, group, scope *string) (*string, error) {
	return t.first(ctx, `SELECT subscription_id::text FROM application_subscription WHERE subscription_id=$1::uuid AND sport_association_id=$2::uuid
 AND ($3::uuid IS NULL OR group_id=$3::uuid) AND ($4::uuid IS NULL OR sport_association_id=$4::uuid) ORDER BY subscription_id LIMIT 1`,
		subscription, association, group, scope)
}

// CreateCardToken is SubscriptionToken.objects.create (audited, indexed through
// the subscription's association).
func (t *miscRoutesTransaction) CreateCardToken(ctx context.Context, id, subscription, association, token string, created, expires time.Time,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_subscriptiontoken
 (subscription_token_id, subscription_id, token, creation_date, expiration_date) VALUES ($1, $2, $3, $4, $5)`,
		id, subscription, token, created, expires); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_subscriptiontoken", "subscription_token_id", id)
	if err != nil {
		return err
	}
	path := "subscription.sport_association"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "subscriptiontoken", Key: id,
		Representation: "SubscriptionToken object (" + id + ")", Action: 0, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at})
}
