package service

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type fakeWorkflowStore struct {
	workflows     map[uuid.UUID]domain.WorkflowTaskBlockWorkflow
	order         []uuid.UUID
	subscriptions map[uuid.UUID]domain.WorkflowTaskSubscription
	relations     []domain.TutorRelation
	emails        map[uuid.UUID]*string
	cron          []uuid.UUID
	filter        *domain.WorkflowTaskCronFilter
	promoted      []domain.TutorRelation
}

func (f *fakeWorkflowStore) AssociationWorkflows(_ context.Context, association uuid.UUID) ([]domain.WorkflowTaskWorkflow, error) {
	var out []domain.WorkflowTaskWorkflow
	for _, id := range f.order {
		if w := f.workflows[id]; w.Association == association {
			out = append(out, w.WorkflowTaskWorkflow)
		}
	}
	return out, nil
}
func (f *fakeWorkflowStore) EnabledWorkflow(_ context.Context, id uuid.UUID) (*domain.WorkflowTaskWorkflow, error) {
	if w, ok := f.workflows[id]; ok {
		return &w.WorkflowTaskWorkflow, nil
	}
	return nil, nil
}
func (f *fakeWorkflowStore) BlockWorkflow(_ context.Context, id uuid.UUID) (*domain.WorkflowTaskBlockWorkflow, error) {
	if w, ok := f.workflows[id]; ok {
		return &w, nil
	}
	return nil, nil
}
func (f *fakeWorkflowStore) SubscriptionExists(_ context.Context, id uuid.UUID) (bool, error) {
	_, ok := f.subscriptions[id]
	return ok, nil
}
func (f *fakeWorkflowStore) BlockSubscription(_ context.Context, id uuid.UUID) (*domain.WorkflowTaskSubscription, error) {
	if s, ok := f.subscriptions[id]; ok {
		return &s, nil
	}
	return nil, nil
}
func (f *fakeWorkflowStore) Tag(context.Context, uuid.UUID) (*domain.WorkflowTaskTag, error) {
	return nil, nil
}
func (f *fakeWorkflowStore) CronSubscriptions(_ context.Context, _ uuid.UUID, _ time.Time, filter domain.WorkflowTaskCronFilter) ([]uuid.UUID, error) {
	f.filter = &filter
	return f.cron, nil
}
func (f *fakeWorkflowStore) FirstTutorRelation(_ context.Context, associate uuid.UUID, primaryOnly bool) (*domain.TutorRelation, error) {
	for _, r := range f.relations {
		if r.Associate == associate && (!primaryOnly || r.Primary) {
			return &r, nil
		}
	}
	return nil, nil
}
func (f *fakeWorkflowStore) AssociateEmail(_ context.Context, id uuid.UUID) (*string, error) {
	return f.emails[id], nil
}
func (f *fakeWorkflowStore) PromoteTutorRelation(_ context.Context, r domain.TutorRelation, _ time.Time) error {
	f.promoted = append(f.promoted, r)
	return nil
}

func workflowTestID(n int) uuid.UUID {
	return uuid.MustParse("00000000-0000-0000-0000-0000c60000" + string("0123456789abcdef"[n/16]) + string("0123456789abcdef"[n%16]))
}

func workflowTestTree(t *testing.T, text string) string {
	t.Helper()
	if _, err := celery.DecodeJSON([]byte(text)); err != nil {
		t.Fatal(err)
	}
	return text
}

// TestWorkflowTasks: Django's branches, the Python exceptions the task
// swallows (None, effects so far kept) and the handoffs.
func TestWorkflowTasks(t *testing.T) {
	association, owner, athlete := workflowTestID(1), workflowTestID(2), workflowTestID(3)
	minor, tutor, sub, ownSub := workflowTestID(4), workflowTestID(5), workflowTestID(6), workflowTestID(7)
	w1, w2, wCron, wBad := workflowTestID(10), workflowTestID(11), workflowTestID(12), workflowTestID(13)
	ownerMail, tutorMail := "owner@example.test", "tutor@example.test"
	store := func() *fakeWorkflowStore {
		block := func(id uuid.UUID, tree string) domain.WorkflowTaskBlockWorkflow {
			return domain.WorkflowTaskBlockWorkflow{WorkflowTaskWorkflow: domain.WorkflowTaskWorkflow{ID: id, Association: association,
				Tree: workflowTestTree(t, tree)}, OwnerID: owner, OwnerEmail: ownerMail}
		}
		return &fakeWorkflowStore{
			workflows: map[uuid.UUID]domain.WorkflowTaskBlockWorkflow{
				w1:    block(w1, `[{"id": "trigger", "value": "new_subscription"}, {"id": "message", "value": "email", "data": {"content": "<p>x</p>", "subject": "S", "recipients": "all"}}, {"id": "wait", "value": "wait", "data": {"type": "hours", "amount": " 3 "}}, {"id": "wait", "value": "wait", "data": {"type": "years", "amount": 1}}, {"id": "wait", "value": "wait", "data": {"type": "minutes", "amount": 1.5}}]`),
				w2:    block(w2, `[{"id": "trigger", "value": "new_subscription"}]`),
				wCron: block(wCron, `[{"id": "trigger", "value": "cron", "data": {"target": "approved"}}, {"id": "message", "value": "sms"}]`),
				wBad:  block(wBad, `[]`),
			},
			order: []uuid.UUID{w1, w2, wCron, wBad},
			subscriptions: map[uuid.UUID]domain.WorkflowTaskSubscription{
				sub:    {ID: sub, UserID: athlete, UserEmail: "athlete@example.test", AssociateID: &minor, AssociateMinor: true},
				ownSub: {ID: ownSub, UserID: owner, UserEmail: ownerMail},
			},
			relations: []domain.TutorRelation{{ID: 7, Associate: minor, Tutor: tutor}},
			emails:    map[uuid.UUID]*string{tutor: &tutorMail},
			cron:      []uuid.UUID{sub, sub},
		}
	}
	type published struct {
		name      string
		args      []any
		countdown int64
	}
	run := func(name string, s *fakeWorkflowStore, args []any) ([]published, any, error) {
		tasks := NewWorkflowTasks(s, &celery.Recorder{}, func() time.Time { return time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC) })
		tasks.randint = func(int, int) int { return 42 }
		request := &celeryworker.Request{Message: &celery.Message{Task: name, ID: "parent", Args: args}}
		task := tasks.Tasks()[name]
		if err := task.Plan(context.Background(), request); err != nil {
			return nil, nil, err
		}
		plan := request.State.(*workflowTasksPlan)
		var out []published
		for _, a := range plan.actions {
			if a.task != nil {
				out = append(out, published{a.task.Name, a.task.Args, a.countdown})
			}
		}
		result, err := task.Run(context.Background(), request)
		// The prefork worker stores every published message, in order, in the task's meta.children.
		if err == nil && len(request.Children()) != len(out) {
			t.Fatalf("%s children %v, published %d", name, request.Children(), len(out))
		}
		return out, result, err
	}
	// Email block for a minor: tutor promoted, recipients deduplicated in order, same countdown for both messages.
	s := store()
	out, result, err := run(WorkflowBlockExecuteTask, s, []any{w1.String(), sub, celery.Number("1")})
	if err != nil || result != nil || len(out) != 2 || out[0].name != "send_email_html" || out[1].countdown != 42 ||
		!reflect.DeepEqual(out[1].args, []any{w1.String(), sub, int64(2)}) || len(s.promoted) != 1 || !s.promoted[0].Primary {
		t.Fatalf("email block: %v %v %v %v", out, result, err, s.promoted)
	}
	// Wait block with a padded decimal string amount.
	if out, _, err = run(WorkflowBlockExecuteTask, store(), []any{w1, sub, celery.Number("2")}); err != nil || len(out) != 1 || out[0].countdown != 3*3600 {
		t.Fatalf("wait block: %v %v", out, err)
	}
	// Unknown wait type with a next block: UnboundLocalError, nothing published.
	if out, result, err = run(WorkflowBlockExecuteTask, store(), []any{w1, sub, celery.Number("3")}); err != nil || len(out) != 0 || result != nil {
		t.Fatalf("unknown wait type: %v %v", out, err)
	}
	// Float amount: handed off; bool index: handed off.
	for _, args := range [][]any{{w1, sub, celery.Number("4")}, {w1, sub, true}, {w1, sub, celery.Number("1"), "extra"}} {
		if _, _, err = run(WorkflowBlockExecuteTask, store(), args); !errors.Is(err, celeryworker.ErrHandoff) {
			t.Fatalf("handoff %v: %v", args, err)
		}
	}
	// Missing subscription: the tuple; malformed id: None.
	if _, result, _ = run(WorkflowBlockExecuteTask, store(), []any{w1, workflowTestID(99), celery.Number("1")}); !reflect.DeepEqual(result, []any{false, "Subscription is None"}) {
		t.Fatalf("missing subscription: %v", result)
	}
	if out, result, err = run(WorkflowBlockExecuteTask, store(), []any{w1, "nope", celery.Number("1")}); err != nil || result != nil || len(out) != 0 {
		t.Fatalf("malformed subscription: %v %v %v", out, result, err)
	}
	// Trigger: w1 publishes, w2 has one node, wCron does not match, wBad raises after w1's publish.
	if out, result, err = run(CheckWorkflowsTriggerTask, store(), []any{"new_subscription", association.String(), sub.String()}); err != nil ||
		result != nil || len(out) != 1 || !reflect.DeepEqual(out[0].args, []any{w1, sub, int64(1)}) {
		t.Fatalf("trigger: %v %v %v", out, result, err)
	}
	if _, _, err = run(CheckWorkflowsTriggerTask, store(), []any{celery.Number("5"), association}); !errors.Is(err, celeryworker.ErrHandoff) {
		t.Fatal("non-string trigger type is handed off")
	}
	// Cron: one block per subscription row (duplicates kept), approved filter.
	s = store()
	if out, _, err = run(CheckWorkflowsTriggerCronTask, s, []any{wCron}); err != nil || len(out) != 2 || s.filter == nil ||
		s.filter.Status == nil || *s.filter.Status != 4 {
		t.Fatalf("cron: %v %v %+v", out, err, s.filter)
	}
	if out, _, _ = run(CheckWorkflowsTriggerCronTask, store(), []any{w2}); len(out) != 0 {
		t.Fatal("a single-node workflow does nothing")
	}
}
