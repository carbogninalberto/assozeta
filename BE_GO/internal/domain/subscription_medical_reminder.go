package domain

import "context"

// MedicalReminderRecipient is what subscription_medical_certificate_send_email_reminder
// reads after the lookup: the subscription's associate (Associate nil without
// one), its main tutor, the subscription user's email and the association's
// denomination. TutorUnpromoted marks an associate whose tutor relations are
// all non-primary (get_main_tutor would save the first one as primary).
type MedicalReminderRecipient struct {
	Associate               *string
	First, Last, Email      *string
	Tutor                   bool
	TutorEmail              *string
	TutorUnpromoted         bool
	UserEmail, Denomination *string
}

type SubscriptionReminderTransaction interface {
	SubscriptionMedicalTransaction
	ReminderRecipient(ctx context.Context, subscription string) (MedicalReminderRecipient, error)
}

type SubscriptionReminderStore interface {
	BeginSubscriptionReminder(context.Context) (SubscriptionReminderTransaction, error)
}
