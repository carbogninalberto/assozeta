package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrModuleUpdateNotFound is modules_update's 404 {'error': 'Camp or retreat not found'}.
	ErrModuleUpdateNotFound = errors.New("camp or retreat not found")
	// ErrModuleWriteNotPorted refuses inputs whose Django path is not ported,
	// before any write.
	ErrModuleWriteNotPorted = refusal.New("module write input not ported")
)

// ModuleUpdateFailure is modules_update's {'error': str(e)}: Status 400 for
// a DRF ValidationError, 500 for any other exception the view catches.
type ModuleUpdateFailure struct {
	Status int
	Text   string
}

func (e *ModuleUpdateFailure) Error() string { return e.Text }

type ModulesWriteStore interface {
	BeginModulesWrite(ctx context.Context) (ModulesWriteTransaction, error)
}

type ModulesWriteTransaction interface {
	OwnerAssociation(ctx context.Context, user string) (string, error)
	Module(ctx context.Context, id string, association *string) (*ModuleRow, error)
	Taken(ctx context.Context, column, value string, exclude *string) (bool, error)
	CreateModule(ctx context.Context, row ModuleRow, audit LoginAuditContext, at time.Time) error
	SaveModule(ctx context.Context, row ModuleRow, audit LoginAuditContext, at time.Time) error
	DeleteModule(ctx context.Context, id string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}
