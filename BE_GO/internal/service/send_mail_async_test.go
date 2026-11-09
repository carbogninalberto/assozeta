package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/google/uuid"
)

type fakeSendMailStore struct {
	association, configuration, instance bool
}

func (f fakeSendMailStore) ActiveAssociation(context.Context, uuid.UUID) (bool, error) {
	return f.association, nil
}
func (f fakeSendMailStore) HasCommunicationConfiguration(context.Context, uuid.UUID) (bool, error) {
	return f.configuration, nil
}
func (f fakeSendMailStore) InstanceEmail(context.Context) (bool, error) { return f.instance, nil }
func (f fakeSendMailStore) InsertEmailLog(context.Context, string, string, string, *uuid.UUID) error {
	return nil
}

// TestSendMailAsyncPlan: the inputs the port runs and the ones it hands to
// the Django worker before any effect.
func TestSendMailAsyncPlan(t *testing.T) {
	id := uuid.MustParse("6f1c1e9e-6a59-4b0e-9a54-3f1f0a7c2d11")
	base := func(extra ...celery.Item) celery.Dict {
		return append(celery.Dict{{Key: "subject", Value: "Oggetto"}, {Key: "message", Value: "Testo"},
			{Key: "from_email", Value: "Brand <noreply@example.test>"}, {Key: "recipient_list", Value: []any{"a@example.test"}}}, extra...)
	}
	cases := []struct {
		name    string
		args    []any
		kwargs  celery.Dict
		store   fakeSendMailStore
		email   SystemEmail
		handoff bool
	}{
		{name: "plain", kwargs: base()},
		{name: "positional", args: []any{"Oggetto", nil, "noreply@example.test", []any{"a@example.test"}, id}, kwargs: celery.Dict{},
			store: fakeSendMailStore{association: true}},
		{name: "association with configuration", kwargs: base(celery.Item{Key: "sport_association_id", Value: id}),
			store: fakeSendMailStore{association: true, configuration: true}, handoff: true},
		{name: "skip check false still checks", kwargs: base(celery.Item{Key: "sport_association_id", Value: id},
			celery.Item{Key: "skip_association_check", Value: false}), store: fakeSendMailStore{association: true, configuration: true}, handoff: true},
		{name: "skip check", kwargs: base(celery.Item{Key: "sport_association_id", Value: id},
			celery.Item{Key: "skip_association_check", Value: true}), store: fakeSendMailStore{association: true, configuration: true}},
		{name: "deleted association", kwargs: base(celery.Item{Key: "sport_association_id", Value: id.String()}),
			store: fakeSendMailStore{configuration: true}},
		{name: "instance email", kwargs: base(), store: fakeSendMailStore{instance: true}, handoff: true},
		{name: "fail silently", kwargs: base(celery.Item{Key: "fail_silently", Value: true}), handoff: true},
		{name: "unknown keyword", kwargs: base(celery.Item{Key: "connection", Value: nil}), handoff: true},
		{name: "reply_to string", kwargs: base(celery.Item{Key: "reply_to", Value: "r@example.test"}), handoff: true},
		{name: "utf-8 recipient", kwargs: base()[:3].Set("recipient_list", []any{"çağla@example.test"}), handoff: true},
		{name: "empty recipients", kwargs: base()[:3].Set("recipient_list", []any{}), handoff: true},
		{name: "duplicate argument", args: []any{"Oggetto"}, kwargs: base(), handoff: true},
		{name: "malformed association", kwargs: base(celery.Item{Key: "sport_association_id", Value: "x"}), handoff: true},
		{name: "invalid port", kwargs: base(), email: SystemEmail{Host: "smtp.example.test", PortInvalid: true}, handoff: true},
	}
	for _, c := range cases {
		email := c.email
		if email.Host == "" {
			email = SystemEmail{Host: "smtp.example.test", Port: 465}
		}
		task := NewSendMailAsync(c.store, email, time.Now).Task()
		request := &celeryworker.Request{Message: &celery.Message{Task: SendMailAsyncTask, Args: c.args, Kwargs: c.kwargs}}
		err := task.Plan(context.Background(), request)
		if handoff := errors.Is(err, celeryworker.ErrHandoff); handoff != c.handoff || (!handoff && err != nil) {
			t.Errorf("%s: %v", c.name, err)
		}
		if !c.handoff && request.State == nil {
			t.Errorf("%s: no plan state", c.name)
		}
	}
	if task := NewSendMailAsync(nil, SystemEmail{}, time.Now).Task(); task.MaxRetries != 3 {
		t.Fatal("max_retries")
	}
	request := &celeryworker.Request{Message: &celery.Message{Retries: 2}}
	var retry *celeryworker.Retry
	if !errors.As(sendMailAsyncRetry(request, errSendMailAsyncDatabase), &retry) || retry.Countdown != 720 {
		t.Fatal("countdown is 180 * 2 ** retries")
	}
}
