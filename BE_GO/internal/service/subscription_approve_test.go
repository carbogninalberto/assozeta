package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

const (
	approveAssociation  = "a1000000-0000-4000-8000-000000000001"
	approveSubscription = "a1000000-0000-4000-8000-000000000002"
)

// approveFake records the order of effects; the embedded interface panics on
// any method the approve path must not call.
type approveFake struct {
	domain.SubscriptionApproveTransaction
	events    *[]string
	row       domain.SubscriptionLifecycleRow
	saveErr   error
	committed bool
	onCommit  func()
}

func (f *approveFake) LifecycleAssociation(context.Context, string, *string) (string, error) {
	return approveAssociation, nil
}
func (f *approveFake) ActorAssociation(context.Context, string) (*string, error) { return nil, nil }
func (f *approveFake) Subscription(context.Context, string, *string, *string) (*domain.SubscriptionLifecycleRow, error) {
	row := f.row
	return &row, nil
}
func (f *approveFake) Signature(context.Context, string) (domain.SubscriptionSignature, error) {
	return domain.SubscriptionSignature{}, nil
}
func (f *approveFake) PastSignature(context.Context, string, *string, *string, *string) (*domain.SubscriptionSignature, error) {
	return nil, nil
}
func (f *approveFake) SaveSubscription(_ context.Context, save domain.SubscriptionLifecycleSave, _ domain.CourseSubscriptionAudit) error {
	*f.events = append(*f.events, "save:"+save.Changes["acceptance_date"][1])
	return f.saveErr
}
func (f *approveFake) Commit() error {
	*f.events = append(*f.events, "commit")
	f.committed = true
	if f.onCommit != nil {
		f.onCommit()
	}
	return nil
}
func (f *approveFake) Rollback() error {
	if !f.committed {
		*f.events = append(*f.events, "rollback")
	}
	return nil
}

type approveFakeStore struct{ tx *approveFake }

func (s approveFakeStore) BeginSubscriptionApprove(context.Context) (domain.SubscriptionApproveTransaction, error) {
	return s.tx, nil
}

type orderedPublisher struct {
	events *[]string
	tasks  []celery.Task
	err    error
}

func (p *orderedPublisher) Publish(ctx context.Context, task celery.Task) (string, error) {
	*p.events = append(*p.events, "publish")
	if ctx.Err() != nil {
		return "", ctx.Err()
	}
	p.tasks = append(p.tasks, task)
	return "id", p.err
}

func approveIdentity() domain.ResolvedIdentity {
	owner := &domain.Account{ID: "a1000000-0000-4000-8000-000000000003"}
	return domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner}
}

func approveFixture(status int) (*SubscriptionApprovals, *approveFake, *orderedPublisher, *[]string) {
	events := []string{}
	start, end := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), time.Date(2026, 12, 31, 0, 0, 0, 0, time.UTC)
	tx := &approveFake{events: &events, row: domain.SubscriptionLifecycleRow{ID: approveSubscription, AssociationID: approveAssociation, Status: status,
		Start: &start, End: &end, Created: start, StartDay: 1, StartMonth: 1, Duration: 1}}
	publisher := &orderedPublisher{events: &events}
	now := func() time.Time { return time.Date(2026, 11, 9, 10, 0, 0, 500000, time.UTC) }
	return NewSubscriptionApprovals(approveFakeStore{tx}, publisher, now), tx, publisher, &events
}

// The task is published only after the commit, with Django's kwargs.
func TestApprovePublishesAfterCommit(t *testing.T) {
	approvals, _, publisher, events := approveFixture(2)
	if err := approvals.Approve(context.Background(), approveIdentity(), approveSubscription, "", domain.LoginAuditContext{}); err != nil {
		t.Fatal(err)
	}
	want := []string{"save:2026-11-09 10:00:00.000500+00:00", "commit", "publish"}
	if len(*events) != len(want) || (*events)[0] != want[0] || (*events)[1] != want[1] || (*events)[2] != want[2] {
		t.Fatalf("events %v", *events)
	}
	task := publisher.tasks[0]
	if task.Name != "check_workflows_trigger" || len(task.Args) != 0 || len(task.Kwargs) != 3 ||
		task.Kwargs[0] != (celery.Item{Key: "trigger_type", Value: "subscription_approved"}) ||
		task.Kwargs[1] != (celery.Item{Key: "sport_association_id", Value: uuid.MustParse(approveAssociation)}) ||
		task.Kwargs[2] != (celery.Item{Key: "subscription_id", Value: uuid.MustParse(approveSubscription)}) {
		t.Fatalf("task %+v", task)
	}
}

// A cancelled request after the commit still publishes (Django's view is not interrupted).
func TestApprovePublishIgnoresRequestCancellation(t *testing.T) {
	approvals, tx, publisher, _ := approveFixture(2)
	ctx, cancel := context.WithCancel(context.Background())
	tx.onCommit = cancel
	if err := approvals.Approve(ctx, approveIdentity(), approveSubscription, "", domain.LoginAuditContext{}); err != nil {
		t.Fatal(err)
	}
	if len(publisher.tasks) != 1 {
		t.Fatal("publish was cancelled with the request")
	}
}

func TestApproveDoesNotPublishWithoutCommit(t *testing.T) {
	approvals, _, _, events := approveFixture(4)
	if err := approvals.Approve(context.Background(), approveIdentity(), approveSubscription, "", domain.LoginAuditContext{}); !errors.Is(err, ErrSubscriptionApproveAccepted) {
		t.Fatalf("err %v", err)
	}
	approvals, tx, _, failed := approveFixture(2)
	tx.saveErr = errors.New("write failed")
	if err := approvals.Approve(context.Background(), approveIdentity(), approveSubscription, "", domain.LoginAuditContext{}); err == nil {
		t.Fatal("save error ignored")
	}
	for _, list := range [][]string{*events, *failed} {
		for _, event := range list {
			if event == "publish" || event == "commit" {
				t.Fatalf("published or committed: %v", list)
			}
		}
	}
	// A publish failure after the commit is returned (the 500 page); the save stays committed.
	approvals, _, publisher, events := approveFixture(2)
	publisher.err = errors.New("broker down")
	if err := approvals.Approve(context.Background(), approveIdentity(), approveSubscription, "", domain.LoginAuditContext{}); err == nil {
		t.Fatal("publish error ignored")
	}
	if (*events)[len(*events)-2] != "commit" {
		t.Fatalf("events %v", *events)
	}
}
