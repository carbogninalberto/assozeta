package service

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type deletionEvents struct {
	log      []string
	users    []domain.UserDeletionCandidate
	saveErr  map[uuid.UUID]error
	beginErr error
}

func (e *deletionEvents) BeginUserDeletionRequests(context.Context, domain.Job) (domain.UserDeletionRequestTransaction, error) {
	if e.beginErr != nil {
		return nil, e.beginErr
	}
	e.log = append(e.log, "begin")
	return &deletionTx{events: e}, nil
}

type deletionTx struct {
	events   *deletionEvents
	finished bool
}

func (t *deletionTx) DueUsers(context.Context, time.Time) ([]domain.UserDeletionCandidate, error) {
	t.events.log = append(t.events.log, "select")
	return t.events.users, nil
}
func (t *deletionTx) MarkDeleted(_ context.Context, user domain.UserDeletionCandidate, _ time.Time) error {
	t.events.log = append(t.events.log, "save "+user.Username)
	return t.events.saveErr[user.ID]
}
func (t *deletionTx) CompleteJob(context.Context, uuid.UUID, uuid.UUID) error {
	t.events.log = append(t.events.log, "complete")
	return nil
}
func (t *deletionTx) Commit() error {
	t.finished = true
	t.events.log = append(t.events.log, "commit")
	return nil
}
func (t *deletionTx) Rollback() error {
	if !t.finished {
		t.finished = true
		t.events.log = append(t.events.log, "rollback")
	}
	return nil
}

type orderedRecorder struct {
	events *deletionEvents
	err    error
}

func (r *orderedRecorder) Publish(_ context.Context, task celery.Task) (string, error) {
	r.events.log = append(r.events.log, "publish "+task.Kwargs[2].Value.([]any)[0].(string))
	return "", r.err
}

func deletionJob() domain.Job {
	return domain.Job{ID: uuid.New(), LeaseToken: uuid.New(), State: "running", Kind: domain.JobDeleteUsersWithRequest, Version: 1, Payload: []byte(`{}`)}
}

func TestUserDeletionRequestsOrder(t *testing.T) {
	users := []domain.UserDeletionCandidate{{ID: uuid.New(), Username: "a", Email: "a@x"}, {ID: uuid.New(), Username: "b", Email: "b@x"}}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	now := func() time.Time { return time.Date(2026, 11, 9, 23, 59, 0, 0, time.FixedZone("x", 3600)) }
	t.Run("each notice precedes its own committed save", func(t *testing.T) {
		events := &deletionEvents{users: users}
		err := NewUserDeletionRequests(events, &orderedRecorder{events: events}, "Brand", now, logger).Run(context.Background(), deletionJob())
		want := []string{"begin", "select", "rollback", "begin", "publish a@x", "save a", "commit", "begin", "publish b@x", "save b", "commit", "begin", "complete", "commit"}
		if err != nil || !reflect.DeepEqual(events.log, want) {
			t.Fatal(err, events.log)
		}
	})
	t.Run("failed save keeps the audit and ends the task", func(t *testing.T) {
		events := &deletionEvents{users: users, saveErr: map[uuid.UUID]error{users[0].ID: domain.ErrUserDeletionSaveFailed}}
		err := NewUserDeletionRequests(events, &orderedRecorder{events: events}, "Brand", now, logger).Run(context.Background(), deletionJob())
		want := []string{"begin", "select", "rollback", "begin", "publish a@x", "save a", "commit", "begin", "complete", "commit"}
		if err != nil || !reflect.DeepEqual(events.log, want) {
			t.Fatal(err, events.log)
		}
	})
	t.Run("publish failure saves nothing and ends the task", func(t *testing.T) {
		events := &deletionEvents{users: users}
		err := NewUserDeletionRequests(events, &orderedRecorder{events: events, err: errors.New("broker down")}, "Brand", now, logger).Run(context.Background(), deletionJob())
		want := []string{"begin", "select", "rollback", "begin", "publish a@x", "rollback", "begin", "complete", "commit"}
		if err != nil || !reflect.DeepEqual(events.log, want) {
			t.Fatal(err, events.log)
		}
	})
	t.Run("lost lease is not a handled outcome", func(t *testing.T) {
		events := &deletionEvents{users: users, beginErr: domain.ErrJobLeaseLost}
		err := NewUserDeletionRequests(events, &celery.Recorder{}, "Brand", now, logger).Run(context.Background(), deletionJob())
		if !errors.Is(err, domain.ErrJobLeaseLost) || len(events.log) != 0 {
			t.Fatal(err, events.log)
		}
	})
	t.Run("notice text and argument order", func(t *testing.T) {
		recorder := &celery.Recorder{}
		events := &deletionEvents{users: []domain.UserDeletionCandidate{{ID: uuid.New(), Username: "z", Email: "Z@x", FirstName: "Zoë"}}}
		if err := NewUserDeletionRequests(events, recorder, "Brand", now, logger).Run(context.Background(), deletionJob()); err != nil {
			t.Fatal(err)
		}
		want := celery.Task{Name: "send_email_text", Kwargs: celery.Dict{
			{Key: "subject", Value: "Brand | Il tuo account è stato eliminato"},
			{Key: "message", Value: "Ciao Zoë, Il tuo account è stato eliminato dal sistema perché hai richiesto la cancellazione 30 giorni fa. "},
			{Key: "recipient_list", Value: []any{"Z@x"}}}}
		if !reflect.DeepEqual(recorder.Tasks, []celery.Task{want}) {
			t.Fatal(recorder.Tasks)
		}
	})
	t.Run("invalid envelope is rejected before any transaction", func(t *testing.T) {
		events := &deletionEvents{users: users}
		job := deletionJob()
		job.AssociationID = uuid.New()
		if err := NewUserDeletionRequests(events, &celery.Recorder{}, "Brand", now, logger).Run(context.Background(), job); !errors.Is(err, domain.ErrJobInvalid) || len(events.log) != 0 {
			t.Fatal(err, events.log)
		}
	})
}
