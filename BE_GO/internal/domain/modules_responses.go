package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrModuleNotEnabled is modules_response_add's 404 {'error': 'module not enabled'}.
	ErrModuleNotEnabled = errors.New("module not enabled")
	// ErrModuleNotActive is modules_custom_link_info's 404 {'error': 'module not active'}.
	ErrModuleNotActive = errors.New("module not active")
	// ErrModuleMaxResponses is 404 {'error': 'max responses reached'}.
	ErrModuleMaxResponses = errors.New("max responses reached")
	// ErrModuleOnlyUsers is 404 {'error': 'only users'}.
	ErrModuleOnlyUsers = errors.New("only users")
	// ErrModuleResponseNotFound is 404 {'error': 'response not found'}.
	ErrModuleResponseNotFound = errors.New("response not found")
	// ErrModuleDocumentNotFound is 404 {'error': 'document not found'}.
	ErrModuleDocumentNotFound = errors.New("document not found")
	// ErrModuleResponsesNotPorted refuses an input whose Django path is not
	// ported, before any write (the 500 page).
	ErrModuleResponsesNotPorted = errors.New("module responses input not ported")
)

// ModuleQueueRow is one response of update_queue's queryset.
type ModuleQueueRow struct {
	ID       string `db:"module_response_id"`
	Approved bool   `db:"approved"`
	Queue    *int64 `db:"queue_position"`
}

// ModuleResponseRef is a response found by id with its module.
type ModuleResponseRef struct {
	ID            string  `db:"module_response_id"`
	ModuleID      string  `db:"module_id"`
	Approved      bool    `db:"approved"`
	QueueMode     bool    `db:"queue_mode"`
	AssociationID *string `db:"sport_association_id"`
}

// ModuleResponseCreate is the ModuleResponses.objects.create(...) row.
type ModuleResponseCreate struct {
	ID, ModuleID      string
	CreationDate      time.Time
	ProgressiveNumber int64
	QueuePosition     *int64
	Response          string
	Approved          bool
}

// ModuleDocumentCreate is the Document.objects.create(filename=...) row.
type ModuleDocumentCreate struct {
	ID, Filename, Token string
	CreationDate        time.Time
}

// ModuleResponseAudit is the request's audit context and the index of the
// entries of one module's responses (module.sport_association, else the
// actor's association).
type ModuleResponseAudit struct {
	Context LoginAuditContext
	At      time.Time
}

type ModulesResponsesStore interface {
	BeginModulesResponses(ctx context.Context) (ModulesResponsesTransaction, error)
}

type ModulesResponsesTransaction interface {
	OwnerAssociation(ctx context.Context, user string) (string, error)
	ModuleByID(ctx context.Context, id string) (*ModuleRow, error)
	ApprovedCount(ctx context.Context, module string) (int64, error)
	LastProgressive(ctx context.Context, module string) (found bool, number *int64, err error)
	LastQueuePosition(ctx context.Context, module string) (*int64, error)
	CreateResponse(ctx context.Context, row ModuleResponseCreate, association *string, audit ModuleResponseAudit) error
	SaveResponseUser(ctx context.Context, id, user string, association *string, audit ModuleResponseAudit) error
	ResponseInAssociation(ctx context.Context, id, association string) (*ModuleResponseRef, error)
	ResponseExists(ctx context.Context, id string) (bool, error)
	ApproveResponse(ctx context.Context, ref ModuleResponseRef, audit ModuleResponseAudit) error
	DeleteResponse(ctx context.Context, id string, audit ModuleResponseAudit) error
	UpdateQueue(ctx context.Context, module string, association *string, audit ModuleResponseAudit) error
	DocumentExists(ctx context.Context, id string) (bool, error)
	DocumentReferencedElsewhere(ctx context.Context, id string) (bool, error)
	DeleteDocument(ctx context.Context, response, document string, audit ModuleResponseAudit) error
	CreateDocument(ctx context.Context, row ModuleDocumentCreate, audit ModuleResponseAudit) error
	LinkDocument(ctx context.Context, response, document string) error
	Commit() error
	Rollback() error
}
