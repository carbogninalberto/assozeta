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
