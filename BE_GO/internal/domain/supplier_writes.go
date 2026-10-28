package domain

import (
	"context"
	"errors"
	"time"
)

// ErrSupplierDeleteDenied is PermissionDenied("User not allowed.").
var ErrSupplierDeleteDenied = errors.New("supplier delete not allowed")

type SupplierDeleteTransaction interface {
	// OwnerAssociation is SportAssociation.objects.get(user=owner)
	// (ErrAssociationNotFound when absent).
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// LiveSuppliers locks the association's live, group-aware suppliers
	// among ids, ordered by key.
	LiveSuppliers(ctx context.Context, association string, ids []string, group *string) ([]string, error)
	// DeleteSupplier applies the collector's SET_NULL updates, deletes the row
	// and records its auditlog delete entry.
	DeleteSupplier(ctx context.Context, id string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type SupplierDeleteStore interface {
	BeginSupplierDelete(context.Context) (SupplierDeleteTransaction, error)
}

// ErrSupplierForeignReference is tenant_scope's generic PermissionDenied.
var ErrSupplierForeignReference = errors.New("supplier references another association")

type SupplierWriteTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	AssociationExists(ctx context.Context, id string) (bool, error)
	// GroupAssociation is the group's association (found false when absent).
	GroupAssociation(ctx context.Context, id string) (association *string, found bool, err error)
	// SupplierIDTaken is UniqueValidator over the live, group-aware manager.
	SupplierIDTaken(ctx context.Context, id string, group *string, exclude *string) (bool, error)
	// LiveSupplier locks the first live, group-aware supplier by key.
	LiveSupplier(ctx context.Context, id string, group *string) (*Supplier, error)
	InsertSupplier(ctx context.Context, row Supplier) error
	// SaveSupplier writes every field, as Model.save() does.
	SaveSupplier(ctx context.Context, row Supplier) error
	// AuditSupplier records an entry; created renders the stored row as a
	// create diff, otherwise changes are used as given.
	AuditSupplier(ctx context.Context, entry SupplierAudit) error
	Commit() error
	Rollback() error
}

type SupplierAudit struct {
	ID          string
	Action      int
	FullDiff    bool
	Changes     map[string][2]string
	Association *string
	Path        string
	Context     LoginAuditContext
	At          time.Time
}

type SupplierWriteStore interface {
	BeginSupplierWrite(context.Context) (SupplierWriteTransaction, error)
}
