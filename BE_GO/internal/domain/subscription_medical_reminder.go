package domain

import (
	"context"
	"time"
)

// MedicalReminderRecipient is what subscription_medical_certificate_send_email_reminder
// reads after the lookup: the subscription's associate (Associate nil without
// one), its main tutor, the subscription user's email and the association's
// denomination. TutorUnpromoted marks an associate whose tutor relations are
// all non-primary (get_main_tutor would save the first one as primary).
type MedicalReminderRecipient struct {
	Associate          *string
	First, Last, Email *string
	Tutor              bool
	TutorEmail         *string
	TutorUnpromoted    bool
	// TutorRelation is the key of the relation get_main_tutor saves as primary.
	TutorRelation int64
	// AssociateAssociation, AssociateDeleted, AssociateGroup describe the associate row the audit index resolves through.
	AssociateAssociation    *string
	AssociateDeleted        bool
	AssociateGroup          *string
	UserEmail, Denomination *string
}

type SubscriptionReminderTransaction interface {
	SubscriptionMedicalTransaction
	ReminderRecipient(ctx context.Context, subscription string) (MedicalReminderRecipient, error)
	// PromoteTutorRelation is relation.is_primary = True; relation.save() with its update entry.
	PromoteTutorRelation(ctx context.Context, relation int64, association string, audit LoginAuditContext, at time.Time) error
}

type SubscriptionReminderStore interface {
	BeginSubscriptionReminder(context.Context) (SubscriptionReminderTransaction, error)
}
