package service

import (
	"context"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// instanceSetupRevokedStore is InstanceSetupStore for importedAssociation: a
// single task row and the last association by primary key.
type instanceSetupRevokedStore struct {
	row    *domain.AssociationTaskResult
	latest *string
}

func (s instanceSetupRevokedStore) First(context.Context) (*domain.InstanceSetupConfiguration, error) {
	return nil, nil
}

func (s instanceSetupRevokedStore) ImportTask(context.Context, string) (*domain.AssociationTaskResult, error) {
	return s.row, nil
}

func (s instanceSetupRevokedStore) ImportedAssociation(context.Context, string) (bool, error) {
	return false, nil
}

func (s instanceSetupRevokedStore) LatestAssociation(context.Context) (*string, error) {
	return s.latest, nil
}

func (s instanceSetupRevokedStore) CreateImported(context.Context, domain.InstanceSetupImported, func() time.Time) error {
	return nil
}

func (s instanceSetupRevokedStore) CreateFresh(context.Context, domain.InstanceSetupFresh, domain.LoginAuditContext,
	func() time.Time) error {
	return nil
}

func (s instanceSetupRevokedStore) Reconfigure(context.Context, int64, map[string]any, *string, time.Time) error {
	return nil
}

func (s instanceSetupRevokedStore) SetLogo(context.Context, int64, string, time.Time) error {
	return nil
}

// TestInstanceSetupRevokedResult is _link_imported_instance for a REVOKED row:
// AsyncResult.failed() is the FAILURE state alone, so the view decodes the
// stored exception instead and, because the rebuilt object is not a dict, links
// the last association. A rebuild that raises is the view's 500 text without
// the "Import task failed: " prefix.
func TestInstanceSetupRevokedResult(t *testing.T) {
	latest := "b2000000-0000-4000-8000-000000000001"
	pointer := func(text string) *string { return &text }
	cases := []struct {
		name        string
		result      *string
		latest      *string
		association string
		message     string
	}{
		{
			name: "stored exception falls back to the last association",
			result: pointer(`{"exc_type": "TaskRevokedError", "exc_message": ["Revoked"],` +
				` "exc_module": "celery.exceptions"}`),
			latest:      pointer(latest),
			association: latest,
		},
		{
			name:    "non-exception attribute answers the rebuild text",
			result:  pointer(`{"exc_type": "system", "exc_message": {"cmd": "noop"}, "exc_module": "os"}`),
			latest:  pointer(latest),
			message: "Expected an exception class, got os.system with payload {'cmd': 'noop'}",
		},
		{
			name:    "no stored result falls back, without an association to link",
			result:  nil,
			message: "No association found after import.",
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			setup := NewInstanceSetup(instanceSetupRevokedStore{
				row:    &domain.AssociationTaskResult{Status: "REVOKED", Result: c.result, Encoding: "utf-8"},
				latest: c.latest,
			}, nil, nil, nil, InstanceSetupSettings{}, time.Now)
			association, message, err := setup.importedAssociation(context.Background(), "b3000000-0000-4000-8000-000000000001")
			if err != nil {
				t.Fatal(err)
			}
			if association != c.association || message != c.message {
				t.Errorf("got (%q, %q), want (%q, %q)", association, message, c.association, c.message)
			}
		})
	}
}
