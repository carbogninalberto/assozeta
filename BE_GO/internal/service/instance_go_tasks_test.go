package service

import (
	"context"
	"errors"
	"io"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
)

// goTasksCall is one runner request the switch sent.
type goTasksCall struct {
	method, path string
	payload      []byte
	timeout      time.Duration
}

type goTasksRunner struct {
	calls []goTasksCall
	reply *updaterrunner.Reply
	err   error
}

func (r *goTasksRunner) CallJSON(_ context.Context, method, path string, payload []byte, timeout time.Duration) (*updaterrunner.Reply, error) {
	r.calls = append(r.calls, goTasksCall{method: method, path: path, payload: payload, timeout: timeout})
	return r.reply, r.err
}

func (r *goTasksRunner) SendStream(context.Context, string, io.Reader, int64) (*updaterrunner.Reply, error) {
	panic("the go-tasks switch never uploads")
}

type goTasksStore struct {
	changes []domain.InstanceGoTasksChange
}

func (s *goTasksStore) SetGoTasksEnabled(_ context.Context, change domain.InstanceGoTasksChange) error {
	s.changes = append(s.changes, change)
	return nil
}

func newGoTasksActions(runner InstanceBackupActionsRunner, tasks InstanceGoTasksStore) *InstanceBackupActions {
	return &InstanceBackupActions{passwords: &backupsPasswordStore{}, tasks: tasks, runner: runner,
		hashes: backupsPasswordHasher{"right"}, now: func() time.Time { return time.Unix(0, 0).UTC() },
		requestID: func() string { return "request-1" }}
}

func goTasksBody(enabled any, password any) domain.RequestObject {
	return domain.RequestObject{{Name: "goTasksEnabled", Value: enabled}, {Name: "password", Value: password}}
}

func TestInstanceGoTasksOnlyTheSwitchAndThePassword(t *testing.T) {
	runner := &goTasksRunner{}
	actions := newGoTasksActions(runner, &goTasksStore{})
	extra := append(goTasksBody(true, "right"), domain.RequestField{Name: "tasks", Value: "x"})
	reply, err := actions.GoTasks(context.Background(), BackupActionsRequest{Actor: "owner"}, extra)
	var invalid *dto.InstanceAdminInvalid
	if reply != nil || !errors.As(err, &invalid) {
		t.Fatalf("got %#v %v", reply, err)
	}
	if want := []string{backupActionsGoTasksOnly}; !reflect.DeepEqual(invalid.Body, want) {
		t.Fatalf("body %#v, want %#v", invalid.Body, want)
	}
	if len(runner.calls) != 0 {
		t.Fatalf("runner called %d times", len(runner.calls))
	}
}

func TestInstanceGoTasksAStringSwitchIsNotABool(t *testing.T) {
	runner := &goTasksRunner{}
	actions := newGoTasksActions(runner, &goTasksStore{})
	// A form value is a string: the bool check answers before the password.
	reply, err := actions.GoTasks(context.Background(), BackupActionsRequest{Actor: "owner", Form: true},
		domain.RequestObject{{Name: "goTasksEnabled", Value: "true"}, {Name: "password", Value: "right"}})
	if err != nil {
		t.Fatal(err)
	}
	want := domain.RequestObject{{Name: "error", Value: "Richiesta non valida."}}
	if reply.Status != 400 || !reflect.DeepEqual(reply.Body, want) {
		t.Fatalf("got %d %#v", reply.Status, reply.Body)
	}
	if len(runner.calls) != 0 {
		t.Fatalf("runner called %d times", len(runner.calls))
	}
}

func TestInstanceGoTasksThePasswordIsConfirmedBeforeTheRunner(t *testing.T) {
	runner := &goTasksRunner{}
	store := &goTasksStore{}
	actions := newGoTasksActions(runner, store)
	reply, err := actions.GoTasks(context.Background(), BackupActionsRequest{Actor: "owner"}, goTasksBody(true, "wrong"))
	if err != nil {
		t.Fatal(err)
	}
	want := domain.RequestObject{{Name: "error", Value: "Password non corretta."}}
	if reply.Status != 400 || !reflect.DeepEqual(reply.Body, want) {
		t.Fatalf("got %d %#v", reply.Status, reply.Body)
	}
	if len(runner.calls) != 0 || len(store.changes) != 0 {
		t.Fatalf("runner %d calls, %d stored changes", len(runner.calls), len(store.changes))
	}
}

func TestInstanceGoTasksAsksTheRunnerOnceAndStoresTheSwitch(t *testing.T) {
	runner := &goTasksRunner{reply: &updaterrunner.Reply{Status: 202,
		Body: []byte(`{"operation": {"id": "a1", "kind": "go-tasks", "status": "queued"}}`)}}
	store := &goTasksStore{}
	actions := newGoTasksActions(runner, store)
	reply, err := actions.GoTasks(context.Background(), BackupActionsRequest{Actor: "owner"}, goTasksBody(true, "right"))
	if err != nil {
		t.Fatal(err)
	}
	if reply.Status != 202 || !reply.NoStore {
		t.Fatalf("got %d no-store %v", reply.Status, reply.NoStore)
	}
	if len(runner.calls) != 1 {
		t.Fatalf("runner called %d times", len(runner.calls))
	}
	call := runner.calls[0]
	if call.method != "POST" || call.path != "/go-tasks" || call.timeout != 30*time.Second {
		t.Fatalf("got %s %s %s", call.method, call.path, call.timeout)
	}
	payload := `{"request_id": "request-1", "actor_id": "owner", "enabled": true}`
	if string(call.payload) != payload {
		t.Fatalf("payload %s, want %s", call.payload, payload)
	}
	if len(store.changes) != 1 || !store.changes[0].Enabled || store.changes[0].ActorID != "owner" {
		t.Fatalf("stored %#v", store.changes)
	}
}

func TestInstanceGoTasksARunnerRefusalLeavesTheSwitch(t *testing.T) {
	runner := &goTasksRunner{reply: &updaterrunner.Reply{Status: 409,
		Body: []byte(`{"error": "Questa versione non include il servizio worker-go."}`)}}
	store := &goTasksStore{}
	actions := newGoTasksActions(runner, store)
	reply, err := actions.GoTasks(context.Background(), BackupActionsRequest{Actor: "owner"}, goTasksBody(true, "right"))
	if err != nil {
		t.Fatal(err)
	}
	want := domain.RequestObject{{Name: "error", Value: "Questa versione non include il servizio worker-go."}}
	if reply.Status != 409 || !reflect.DeepEqual(reply.Body, want) {
		t.Fatalf("got %d %#v", reply.Status, reply.Body)
	}
	if len(store.changes) != 0 {
		t.Fatalf("stored %#v", store.changes)
	}
}

func TestInstanceGoTasksPathIsSet(t *testing.T) {
	// set(request.data) & {'goTasksEnabled', 'password'}: only these bodies
	// take the go-tasks path; a bare string never holds a longer key.
	cases := []struct {
		body any
		want bool
	}{
		{domain.RequestObject{{Name: "oem", Value: domain.RequestObject{}}}, false},
		{domain.RequestObject{{Name: "password", Value: "x"}}, true},
		{[]any{"goTasksEnabled"}, true},
		{[]any{"oem", "uiApiVersion"}, false},
		{"goTasksEnabled", false},
		{dto.InstanceAdminFormInput{Raw: domain.RequestObject{{Name: "password", Value: "x"}}}, true},
	}
	for _, c := range cases {
		got, err := dto.InstanceAdminKeysIntersect(c.body, "goTasksEnabled", "password")
		if err != nil || got != c.want {
			t.Fatalf("%#v: got %v %v, want %v", c.body, got, err, c.want)
		}
	}
	if _, err := dto.InstanceAdminKeysIntersect([]any{domain.RequestObject{}}, "password"); err == nil {
		t.Fatal("a list holding a dictionary is unhashable")
	}
}
