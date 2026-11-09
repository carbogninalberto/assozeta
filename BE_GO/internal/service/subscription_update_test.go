package service

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// updateFake records the order of effects; the embedded interface panics on
// any method the update path must not call.
type updateFake struct {
	domain.SubscriptionUpdateTransaction
	events    *[]string
	row       domain.SubscriptionUpdateRow
	committed bool
}

func (f *updateFake) OwnerAssociation(context.Context, string, *string) (string, error) {
	return approveAssociation, nil
}
func (f *updateFake) ActorAssociation(context.Context, string) (*string, error) { return nil, nil }
func (f *updateFake) Subscription(context.Context, string, domain.SubscriptionUpdateLookup) (*domain.SubscriptionUpdateRow, error) {
	row := f.row
	return &row, nil
}
func (f *updateFake) SaveSubscription(_ context.Context, save domain.SubscriptionUpdateSave, _ domain.CourseSubscriptionAudit) error {
	*f.events = append(*f.events, "save")
	return nil
}
func (f *updateFake) AuditOnly(context.Context, domain.SubscriptionUpdateSave, domain.CourseSubscriptionAudit) error {
	*f.events = append(*f.events, "audit")
	return nil
}
func (f *updateFake) Commit() error {
	*f.events = append(*f.events, "commit")
	f.committed = true
	return nil
}
func (f *updateFake) Rollback() error { return nil }

type updateFakeStore struct{ tx *updateFake }

func (s updateFakeStore) BeginSubscriptionUpdate(context.Context) (domain.SubscriptionUpdateTransaction, error) {
	return s.tx, nil
}

func updateFixture() (*SubscriptionUpdates, *orderedPublisher, *[]string) {
	events := []string{}
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	tx := &updateFake{events: &events, row: domain.SubscriptionUpdateRow{ID: approveSubscription, AssociationID: approveAssociation,
		Type: 2, Role: 1, Status: 2, Start: &start, End: &start, Created: start, StartDay: 1, StartMonth: 1, Duration: 2}}
	publisher := &orderedPublisher{events: &events}
	return NewSubscriptionUpdates(updateFakeStore{tx}, publisher, time.Now), publisher, &events
}

func updateBody(pairs ...any) domain.RequestObject {
	body := domain.RequestObject{}
	for index := 0; index < len(pairs); index += 2 {
		body = append(body, domain.RequestField{Name: pairs[index].(string), Value: pairs[index+1]})
	}
	return body
}

// print_document_subscription is published after the commit with the
// subscription id and the Authorization header as positional arguments.
func TestSubscriptionUpdatePublishesAfterCommit(t *testing.T) {
	updates, publisher, events := updateFixture()
	token := "Bearer synthetic"
	if err := updates.Update(context.Background(), approveIdentity(), approveSubscription, "", updateBody("notes", "x"), false, &token,
		domain.LoginAuditContext{}); err != nil {
		t.Fatal(err)
	}
	if strings.Join(*events, ",") != "save,commit,publish" {
		t.Fatalf("events %v", *events)
	}
	task := publisher.tasks[0]
	if task.Name != "print_document_subscription" || len(task.Kwargs) != 0 || len(task.Args) != 2 || task.Args[0] != approveSubscription ||
		task.Args[1] != token {
		t.Fatalf("task %+v", task)
	}
	updates, publisher, _ = updateFixture()
	if err := updates.Update(context.Background(), approveIdentity(), approveSubscription, "", updateBody(), false, nil,
		domain.LoginAuditContext{}); err != nil || publisher.tasks[0].Args[1] != nil {
		t.Fatalf("missing header: %v %+v", err, publisher.tasks)
	}
}

// A value the save rejects commits only the pre_save entry and publishes nothing;
// a refused value writes nothing.
func TestSubscriptionUpdateKeptEntryDoesNotPublish(t *testing.T) {
	updates, _, events := updateFixture()
	if err := updates.Update(context.Background(), approveIdentity(), approveSubscription, "", updateBody("type", "-1"), false, nil,
		domain.LoginAuditContext{}); !errors.Is(err, ErrSubscriptionUpdateFailed) {
		t.Fatalf("err %v", err)
	}
	if strings.Join(*events, ",") != "audit,commit" {
		t.Fatalf("events %v", *events)
	}
	updates, _, events = updateFixture()
	if err := updates.Update(context.Background(), approveIdentity(), approveSubscription, "", updateBody("acceptance_date", "2026W01"), false, nil,
		domain.LoginAuditContext{}); !errors.Is(err, ErrSubscriptionUpdateNotPorted) || len(*events) != 0 {
		t.Fatalf("err %v events %v", err, *events)
	}
}
