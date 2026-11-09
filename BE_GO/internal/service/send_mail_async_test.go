package service

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type fakeSendMailStore struct {
	association, configuration, instance bool
	// instanceSettings replaces the default instance email settings JSON.
	instanceSettings string
}

func (f fakeSendMailStore) ActiveAssociation(context.Context, uuid.UUID) (bool, error) {
	return f.association, nil
}
func (f fakeSendMailStore) FirstCommunicationConfiguration(_ context.Context, association uuid.UUID) (*domain.CommunicationConfiguration, error) {
	if !f.configuration {
		return nil, nil
	}
	return &domain.CommunicationConfiguration{ID: uuid.New(), AssociationID: &association, Encryption: "NONE", DailyLimit: 1000}, nil
}
func (f fakeSendMailStore) AssociationDenomination(context.Context, uuid.UUID) (*string, bool, error) {
	name := "ASD Uno"
	return &name, true, nil
}
func (f fakeSendMailStore) SaveCommunicationConfiguration(context.Context, domain.CommunicationConfiguration, time.Time) error {
	return nil
}
func (f fakeSendMailStore) InstanceBrand(context.Context) (domain.EmailRenderInstanceConfig, error) {
	return domain.EmailRenderInstanceConfig{}, nil
}
func (f fakeSendMailStore) InstanceEmailSettings(context.Context) (*domain.ConsumerEmailInstance, error) {
	if !f.instance {
		return nil, nil
	}
	settings := f.instanceSettings
	if settings == "" {
		settings = `{"host": "smtp.example.test", "port": 587, "username": "u", "security": "tls", "from_email": "i@example.test", "sender_name": "Istanza"}`
	}
	return &domain.ConsumerEmailInstance{SelfHosted: true, EmailSettings: settings}, nil
}
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
		{name: "association with configuration (no publisher)", kwargs: base(celery.Item{Key: "sport_association_id", Value: id}),
			store: fakeSendMailStore{association: true, configuration: true}, handoff: true},
		{name: "skip check false still checks", kwargs: base(celery.Item{Key: "sport_association_id", Value: id},
			celery.Item{Key: "skip_association_check", Value: false}), store: fakeSendMailStore{association: true, configuration: true}, handoff: true},
		{name: "skip check", kwargs: base(celery.Item{Key: "sport_association_id", Value: id},
			celery.Item{Key: "skip_association_check", Value: true}), store: fakeSendMailStore{association: true, configuration: true}},
		{name: "deleted association", kwargs: base(celery.Item{Key: "sport_association_id", Value: id.String()}),
			store: fakeSendMailStore{configuration: true}},
		{name: "instance email", kwargs: base(), store: fakeSendMailStore{instance: true}},
		{name: "instance email without security", kwargs: base(), handoff: true,
			store: fakeSendMailStore{instance: true, instanceSettings: `{"host": "h", "port": 25, "username": "", "from_email": "i@example.test", "sender_name": ""}`}},
		{name: "instance email quoted sender", kwargs: base(), handoff: true,
			store: fakeSendMailStore{instance: true, instanceSettings: `{"host": "h", "port": 25, "username": "", "security": "none", "from_email": "i@example.test", "sender_name": "A.B"}`}},
		{name: "instance email empty settings", kwargs: base(), store: fakeSendMailStore{instance: true, instanceSettings: `{}`}},
		{name: "fail silently", kwargs: base(celery.Item{Key: "fail_silently", Value: true})},
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

// TestConsumerEmailPlans: the e-mail tasks bind their Python signatures and
// hand off inputs outside the forwarded or rendered subset.
func TestConsumerEmailPlans(t *testing.T) {
	id := uuid.MustParse("6f1c1e9e-6a59-4b0e-9a54-3f1f0a7c2d11")
	recipients := []any{"a@example.test"}
	data := celery.Dict{{Key: "athlete_first_name", Value: "Anna"}, {Key: "app_host", Value: "https://app.example.test"},
		{Key: "settings", Value: celery.Dict{{Key: "WHITELABEL_NAME", Value: "Brand"}, {Key: "IS_WHITELABEL", Value: false}}}}
	cases := []struct {
		name    string
		task    func(*SendMailAsync) celeryworker.Task
		args    []any
		kwargs  celery.Dict
		store   fakeSendMailStore
		handoff bool
	}{
		{name: "text", task: (*SendMailAsync).SendEmailTextTask, kwargs: celery.Dict{{Key: "subject", Value: "S"},
			{Key: "message", Value: "M"}, {Key: "recipient_list", Value: recipients}, {Key: "sport_association_id", Value: id}}},
		{name: "text positional", task: (*SendMailAsync).SendEmailTextTask, args: []any{"M", "S", recipients, nil, nil}},
		{name: "text missing subject", task: (*SendMailAsync).SendEmailTextTask, args: []any{"M"}, handoff: true},
		{name: "text unknown keyword", task: (*SendMailAsync).SendEmailTextTask, args: []any{"M", "S", recipients},
			kwargs: celery.Dict{{Key: "cc", Value: nil}}, handoff: true},
		{name: "text float", task: (*SendMailAsync).SendEmailTextTask, args: []any{"M", celery.Number("1.0"), recipients}, handoff: true},
		{name: "html configuration", task: (*SendMailAsync).SendEmailHTMLTask, args: []any{"<p>x</p>", "S", recipients, id.String()},
			store: fakeSendMailStore{configuration: true}},
		{name: "html malformed association", task: (*SendMailAsync).SendEmailHTMLTask, args: []any{"<p>x</p>", "S", recipients, "x"},
			handoff: true},
		{name: "html configuration recipients string", task: (*SendMailAsync).SendEmailHTMLTask,
			args: []any{"<p>x</p>", "S", "a@example.test", id}, store: fakeSendMailStore{configuration: true}, handoff: true},
		{name: "template", task: (*SendMailAsync).SendEmailTemplateTask, kwargs: celery.Dict{{Key: "template", Value: "email/account/email_new_subscription.html"},
			{Key: "subject", Value: "S"}, {Key: "recipient_list", Value: recipients}, {Key: "data", Value: data}}},
		{name: "template outside the embedded set", task: (*SendMailAsync).SendEmailTemplateTask,
			args: []any{"email/account/other.html", "S", recipients, data}, handoff: true},
		{name: "template list value", task: (*SendMailAsync).SendEmailTemplateTask,
			args: []any{"email/account/email_new_subscription.html", "S", recipients, celery.Dict{{Key: "x", Value: []any{}}}}, handoff: true},
	}
	for _, c := range cases {
		s := NewSendMailAsync(c.store, SystemEmail{Host: "smtp.example.test", Port: 465}, time.Now).
			WithEmailTasks(&celery.Recorder{}, ConsumerEmailSettings{WhitelabelName: "Brand"}, nil)
		request := &celeryworker.Request{Message: &celery.Message{Args: c.args, Kwargs: c.kwargs}}
		err := c.task(s).Plan(context.Background(), request)
		if handoff := errors.Is(err, celeryworker.ErrHandoff); handoff != c.handoff || (!handoff && err != nil) {
			t.Errorf("%s: %v", c.name, err)
		}
	}
}

// TestConsumerEmailChild: a message published from a running task carries
// its root and parent ids and is recorded as a child.
func TestConsumerEmailChild(t *testing.T) {
	recorder := &celery.Recorder{}
	s := NewSendMailAsync(fakeSendMailStore{}, SystemEmail{}, time.Now).WithEmailTasks(recorder, ConsumerEmailSettings{
		DefaultTeamEmail: "Il Team di Brand <noreply@example.test>"}, nil)
	task := s.SendEmailTextTask()
	request := &celeryworker.Request{Message: &celery.Message{ID: "p", RootID: "r", Args: []any{"M", "S", []any{"a@example.test"}}}}
	if err := task.Plan(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	if _, err := task.Run(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	if len(recorder.Tasks) != 1 || recorder.Tasks[0].Name != SendMailAsyncTask || recorder.Tasks[0].RootID != "r" ||
		recorder.Tasks[0].ParentID != "p" {
		t.Fatalf("%+v", recorder.Tasks)
	}
	// The prefork worker stores the published message in the task's meta.children.
	if children := request.Children(); len(children) != 1 || children[0] == "" {
		t.Fatalf("children %v", children)
	}
	keys := []string{}
	for _, item := range recorder.Tasks[0].Kwargs {
		keys = append(keys, item.Key)
	}
	if got := strings.Join(keys, ","); got != "subject,message,from_email,recipient_list,fail_silently,sport_association_id,reply_to" {
		t.Fatal(got)
	}
}
