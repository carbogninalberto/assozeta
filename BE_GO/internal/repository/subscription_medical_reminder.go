package repository

import (
	"context"
	"database/sql"
	"errors"

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
 u.email AS useremail, a.denomination
 FROM application_subscription s JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 JOIN bakney_user u ON u.user_id=s.user_id LEFT JOIN application_associate p ON p.associate_id=s.associate_id
 WHERE s.subscription_id=$1`, subscription)
	if err != nil || result.Associate == nil {
		return result, err
	}
	// get_main_tutor: the primary relation, otherwise the first relation by key.
	var relation struct {
		Primary bool    `db:"is_primary"`
		Email   *string `db:"email"`
	}
	err = t.tx.GetContext(ctx, &relation, `SELECT r.is_primary, p.email FROM application_associatetutorrelation r
 JOIN application_associate p ON p.associate_id=r.tutor_id WHERE r.associate_id=$1 ORDER BY r.is_primary DESC, r.id LIMIT 1`, *result.Associate)
	if errors.Is(err, sql.ErrNoRows) {
		return result, nil
	}
	if err != nil {
		return result, err
	}
	result.Tutor, result.TutorEmail, result.TutorUnpromoted = true, relation.Email, !relation.Primary
	return result, nil
}
