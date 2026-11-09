package service

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

// ErrMedicalReminderNotPorted refuses an associate without an email whose
// tutor relations are all non-primary: get_main_tutor would save the first
// relation as primary (an audited write) before the e-mail is queued.
var ErrMedicalReminderNotPorted = errors.New("medical reminder tutor promotion not ported")

// MedicalReminderSettings are the core.settings values the reminder renders.
type MedicalReminderSettings struct {
	AppURL, WhitelabelName string
	IsWhitelabel           bool
}

const medicalReminderTemplate = "email/account/email_subscription_medical_certificate_expiring.html"

type SubscriptionMedicalReminders struct {
	store     domain.SubscriptionReminderStore
	publisher celery.Publisher
	settings  MedicalReminderSettings
}

func NewSubscriptionMedicalReminders(store domain.SubscriptionReminderStore, publisher celery.Publisher,
	settings MedicalReminderSettings) *SubscriptionMedicalReminders {
	return &SubscriptionMedicalReminders{store: store, publisher: publisher, settings: settings}
}

func (s *SubscriptionMedicalReminders) ParseUUID(raw string) (string, error) {
	return messageReadsParseUUID(raw)
}

// Send follows subscription_medical_certificate_send_email_reminder after
// is_valid_uuid: the _medical_certificate_targets lookup, the e-mail data and
// recipient, then send_email_template.delay(...). Nothing is written.
func (s *SubscriptionMedicalReminders) Send(ctx context.Context, identity domain.ResolvedIdentity, id, group string) error {
	tx, err := s.store.BeginSubscriptionReminder(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	subscription, err := tx.Subscription(ctx, id, medicalScope(identity, group))
	if err != nil {
		return err
	}
	if subscription == nil {
		return domain.ErrSubscriptionMedicalNotFound
	}
	if subscription.Certificate == nil {
		return ErrSubscriptionMedicalFailed // TypeError("medical certificate not present!")
	}
	recipient, err := tx.ReminderRecipient(ctx, subscription.ID)
	if err != nil {
		return err
	}
	expiration := subscription.Certificate.Expiration
	if recipient.Associate == nil || expiration == nil {
		return ErrSubscriptionMedicalFailed // attribute access on None
	}
	var email *string
	switch {
	case recipient.Email != nil:
		email = recipient.Email
	case recipient.Tutor && recipient.TutorUnpromoted:
		return ErrMedicalReminderNotPorted
	case recipient.Tutor && recipient.TutorEmail != nil:
		email = recipient.TutorEmail
	default:
		email = recipient.UserEmail
	}
	if email == nil {
		return ErrSubscriptionMedicalFailed // TypeError("email not present!")
	}
	parsed, err := time.Parse("2006-01-02", *expiration)
	if err != nil {
		return err
	}
	if err = tx.Rollback(); err != nil {
		return err
	}
	denomination := "None"
	if recipient.Denomination != nil {
		denomination = *recipient.Denomination
	}
	data := celery.Dict{
		{Key: "athlete_first_name", Value: optionalValue(recipient.First)},
		{Key: "athlete_last_name", Value: optionalValue(recipient.Last)},
		{Key: "sport_association", Value: celery.Dict{{Key: "denomination", Value: optionalValue(recipient.Denomination)}}},
		{Key: "certificate_expiring_date", Value: parsed.Format("02/01/2006")},
		{Key: "app_host", Value: s.settings.AppURL},
		{Key: "settings", Value: celery.Dict{{Key: "WHITELABEL_NAME", Value: s.settings.WhitelabelName},
			{Key: "IS_WHITELABEL", Value: s.settings.IsWhitelabel}}},
	}
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), subscriptionUpdatePublishTimeout)
	defer cancel()
	_, err = s.publisher.Publish(publish, celery.Task{Name: "send_email_template", Kwargs: celery.Dict{
		{Key: "recipient_list", Value: []any{*email}},
		{Key: "subject", Value: fmt.Sprintf("[%s] Ricordati di rinnovare il tuo certificato", denomination)},
		{Key: "template", Value: medicalReminderTemplate},
		{Key: "data", Value: data},
		{Key: "sport_association_id", Value: uuid.MustParse(strings.TrimSpace(subscription.Association))},
	}})
	return err
}
