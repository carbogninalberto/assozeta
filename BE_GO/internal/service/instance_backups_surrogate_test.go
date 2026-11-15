package service

import (
	"context"
	"errors"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
)

// surrogateRunner answers every runner call with one scripted reply.
type surrogateRunner struct {
	reply *updaterrunner.Reply
	err   error
	calls int
}

func (r *surrogateRunner) Call(context.Context, string, string) (*updaterrunner.Reply, error) {
	r.calls++
	return r.reply, r.err
}

func (r *surrogateRunner) CallPayload(context.Context, string, string, []byte) (*updaterrunner.Reply, error) {
	r.calls++
	return r.reply, r.err
}

func (r *surrogateRunner) CallJSON(context.Context, string, string, []byte, time.Duration) (*updaterrunner.Reply, error) {
	r.calls++
	return r.reply, r.err
}

func (r *surrogateRunner) SendStream(context.Context, string, io.Reader, int64) (*updaterrunner.Reply, error) {
	r.calls++
	return r.reply, r.err
}

func (r *surrogateRunner) Open(context.Context, string) (*updaterrunner.Stream, error) {
	r.calls++
	return &updaterrunner.Stream{Status: 200, ContentLength: 0, Body: io.NopCloser(strings.NewReader(""))}, r.err
}

func newSurrogateActions(runner InstanceBackupActionsRunner) *InstanceBackupActions {
	return &InstanceBackupActions{runner: runner, now: func() time.Time { return time.Unix(0, 0).UTC() },
		requestID: func() string { return "request-1" }}
}

// TestInstanceBackupActionsRejectionWithSurrogateIsUnhandled is a runner
// refusal whose message is a lone surrogate: Django's renderer fails on the
// {'error': message} body after the view returned, so the 500 page.
func TestInstanceBackupActionsRejectionWithSurrogateIsUnhandled(t *testing.T) {
	runner := &surrogateRunner{reply: &updaterrunner.Reply{Status: 409, Body: []byte(`{"error": "\ud800"}`)}}
	actions := newSurrogateActions(runner)
	reply, err := actions.relay(context.Background(), "PATCH", "/backups/assozeta-2024.tar.gz", nil, 200)
	if reply != nil || !errors.Is(err, errInstanceBackupsUnhandled) {
		t.Fatalf("got %#v %v", reply, err)
	}
	if runner.calls != 1 {
		t.Fatalf("runner called %d times", runner.calls)
	}
}

// TestInstanceBackupActionsSuccessWithSurrogateIsUnhandled is a relayed body
// holding a lone surrogate: the relay helper hands it back, and the caller's
// renderer (after its audit) answers the 500 page.
func TestInstanceBackupActionsSuccessWithSurrogateIsUnhandled(t *testing.T) {
	runner := &surrogateRunner{reply: &updaterrunner.Reply{Status: 200,
		Body: []byte(`{"operation": {"id": "\ud800", "status": "queued"}}`)}}
	actions := newSurrogateActions(runner)
	reply, err := actions.relay(context.Background(), "PATCH", "/backups/assozeta-2024.tar.gz", nil, 200)
	if err != nil || reply == nil {
		t.Fatalf("relay: got %#v %v", reply, err)
	}
	if rendered, err := backupActionsRendered(reply); rendered != nil || !errors.Is(err, errInstanceBackupsUnhandled) {
		t.Fatalf("rendered: got %#v %v", rendered, err)
	}
}

// TestInstanceBackupsRelayWithSurrogateIsUnhandled is the same for the
// instance-level relay of BE/instance/backups.py.
func TestInstanceBackupsRelayWithSurrogateIsUnhandled(t *testing.T) {
	runner := &surrogateRunner{reply: &updaterrunner.Reply{Status: 409, Body: []byte(`{"error": "\ud800"}`)}}
	backups := &InstanceBackups{runner: runner, now: func() time.Time { return time.Unix(0, 0).UTC() }}
	relayed, err := backups.Relay(context.Background(), "POST", "/backups", nil, 202)
	if relayed != nil || !errors.Is(err, errInstanceBackupsUnhandled) {
		t.Fatalf("got %#v %v", relayed, err)
	}
}

// TestInstanceBackupsOverviewNotFoundWithSurrogateIsUnavailable is
// BackupOverviewView.get with a runner 404 whose {'error'} holds a lone
// surrogate: Django answers unavailable(outdated) and never renders the
// message, so the check must not turn it into the 500 page.
func TestInstanceBackupsOverviewNotFoundWithSurrogateIsUnavailable(t *testing.T) {
	runner := &surrogateRunner{reply: &updaterrunner.Reply{Status: 404, Body: []byte(`{"error": "\ud800"}`)}}
	backups := &InstanceBackups{runner: runner, now: func() time.Time { return time.Unix(0, 0).UTC() }}
	reply, err := backups.Overview(context.Background(), false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if reply == nil || reply.Status != 200 {
		t.Fatalf("got %#v", reply)
	}
	reason, found := reply.Body.Lookup("reason")
	available, _ := reply.Body.Lookup("available")
	if !found || reason != instanceBackupsOutdated || available != false {
		t.Fatalf("got %#v", reply.Body)
	}
}

// The audit-then-render case (a Keep whose runner success body holds a lone
// surrogate) has no test here: the store of InstanceBackupActions is the
// concrete *repository.InstanceBackups, which writes the entry to PostgreSQL
// (LogTarget then AppendBackupAction), so no fake in this package records the
// audit. The success check of every caller runs after its audit
// (backupActionsRendered), and the relay helpers no longer check the value.

// TestInstanceUpdatesRejectionWithSurrogateIsUnhandled is a runner refusal
// whose message is a lone surrogate: runnerFailure's {'error': message} body
// fails the renderer, so the 500 page.
func TestInstanceUpdatesRejectionWithSurrogateIsUnhandled(t *testing.T) {
	runner := &surrogateRunner{reply: &updaterrunner.Reply{Status: 500, Body: []byte(`{"error": "\ud800"}`)}}
	admin := &InstanceAdmin{env: InstanceAdminEnvironment{DeploymentMode: "production"}, runner: runner}
	value, err := admin.UpdatesStatus(context.Background())
	if value != nil || !errors.Is(err, errInstanceUpdatesUnhandled) {
		t.Fatalf("got %#v %v", value, err)
	}
}

// TestInstanceUpdatesStatusWithSurrogateIsUnhandled is a relayed status body
// holding a lone surrogate.
func TestInstanceUpdatesStatusWithSurrogateIsUnhandled(t *testing.T) {
	runner := &surrogateRunner{reply: &updaterrunner.Reply{Status: 200,
		Body: []byte(`{"protocol": 1, "reason": "\ud800"}`)}}
	admin := &InstanceAdmin{env: InstanceAdminEnvironment{DeploymentMode: "production"}, runner: runner}
	value, err := admin.UpdatesStatus(context.Background())
	if value != nil || !errors.Is(err, errInstanceUpdatesUnhandled) {
		t.Fatalf("got %#v %v", value, err)
	}
}
