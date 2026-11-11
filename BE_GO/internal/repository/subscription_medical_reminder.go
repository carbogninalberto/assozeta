package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var _ domain.SubscriptionReminderStore = (*SubscriptionMedical)(nil)

// BeginSubscriptionReminder opens the medical transaction used by
// subscription/<uid>/medical-certificate/send-email-reminder.
func (r *SubscriptionMedical) BeginSubscriptionReminder(ctx context.Context) (domain.SubscriptionReminderTransaction, error) {
	tx, err := r.BeginSubscriptionMedical(ctx)
	if err != nil {
		return nil, err
	}
	return tx.(*subscriptionMedicalTransaction), nil
}

func (t *subscriptionMedicalTransaction) ReminderRecipient(ctx context.Context, subscription string) (domain.MedicalReminderRecipient, error) {
	var result domain.MedicalReminderRecipient
	err := t.tx.GetContext(ctx, &result, `SELECT s.associate_id::text AS associate, p.first_name AS first, p.last_name AS last, p.email,
 u.email AS useremail, a.denomination, p.sport_association_id::text AS associateassociation
 FROM application_subscription s JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 JOIN bakney_user u ON u.user_id=s.user_id LEFT JOIN application_associate p ON p.associate_id=s.associate_id
 WHERE s.subscription_id=$1`, subscription)
	if err != nil || result.Associate == nil {
		return result, err
	}
	// get_main_tutor: the primary relation, otherwise the first relation by key.
	var relation struct {
		ID      int64   `db:"id"`
		Primary bool    `db:"is_primary"`
		Email   *string `db:"email"`
	}
	err = t.tx.GetContext(ctx, &relation, `SELECT r.id, r.is_primary, p.email FROM application_associatetutorrelation r
 JOIN application_associate p ON p.associate_id=r.tutor_id WHERE r.associate_id=$1 ORDER BY r.is_primary DESC, r.id LIMIT 1`, *result.Associate)
	if errors.Is(err, sql.ErrNoRows) {
		return result, nil
	}
	if err != nil {
		return result, err
	}
	result.Tutor, result.TutorEmail, result.TutorUnpromoted, result.TutorRelation = true, relation.Email, !relation.Primary, relation.ID
	return result, nil
}

// PromoteTutorRelation is get_main_tutor's save of the first relation: auditlog's
// pre_save entry (indexed through the associate's association, else the actor's), then the UPDATE.
func (t *subscriptionMedicalTransaction) PromoteTutorRelation(ctx context.Context, relation int64, association *string,
	audit domain.LoginAuditContext, at time.Time) error {
	key := strconv.FormatInt(relation, 10)
	path := "associate.sport_association"
	if association == nil {
		// The associate has no association: the resolver finds nothing and the entry falls back to the actor's own
		// association (the first row by key), or goes unindexed.
		path = "actor.sport_association"
		var actorAssociation string
		err := t.tx.GetContext(ctx, &actorAssociation, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1::uuid ORDER BY sport_association_id LIMIT 1`, audit.ActorID)
		switch {
		case err == nil:
			association = &actorAssociation
		case !errors.Is(err, sql.ErrNoRows):
			return err
		}
	}
	if err := appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "associatetutorrelation", Key: key,
		Representation: "AssociateTutorRelation object (" + key + ")", Action: 1,
		Changes: map[string][2]string{"is_primary": {"False", "True"}}, Context: audit, AssociationID: association, ResolutionPath: &path, At: at}); err != nil {
		return err
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_associatetutorrelation SET is_primary=true WHERE id=$1`, relation)
	return err
}
