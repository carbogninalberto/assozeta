package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrAssociateDraftNotFound is the edit view's 404 {'msg': 'not found.'}.
	ErrAssociateDraftNotFound = errors.New("associate import draft not found")
	// ErrAssociateDraftUnauthorized is the views' empty Response(status=401)
	// (athletes; add without an association).
	ErrAssociateDraftUnauthorized = errors.New("associate import draft unauthorized")
	// ErrAssociateDraftNotList is bulk-delete's 400 for a non-list id value.
	ErrAssociateDraftNotList = errors.New("associate import draft ids are not a list")
)

// AssociateDraftRow is a stored AssociateImportDraft: Data is the jsonb text
// (nil for SQL NULL), Valid the nullable flag.
type AssociateDraftRow struct {
	ID    string
	Data  []byte
	Valid *bool
}

// AssociateDraftStatus is the newest AssociateImportDraftStatus of one type.
type AssociateDraftStatus struct {
	ID      string
	Doing   *bool
	Created time.Time
}

// AssociateDraftCertificate is a MedicalCertificate found by medical_id;
// Expiration is the stored date (YYYY-MM-DD) or nil.
type AssociateDraftCertificate struct {
	ID         string
	Expiration *string
}

type AssociateDraftTransaction interface {
	// Association is scoped_queryset(SportAssociation.objects).get(user=owner)
	// (ErrAssociationNotFound when missing); impersonation narrows it.
	Association(ctx context.Context, owner string, impersonation *string) (string, error)
	// FirstAssociation is the same queryset's filter(user=owner).first().
	FirstAssociation(ctx context.Context, owner string, impersonation *string) (*string, error)
	// ActorAssociation is SportAssociation.objects.filter(user=actor).first().
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	// Drafts is AssociateImportDraft.objects.filter(sport_association=...) (no ordering).
	Drafts(ctx context.Context, association string) ([]AssociateDraftRow, error)
	// LatestStatus is filter(sport_association, status_type).order_by('-creation_date').first().
	LatestStatus(ctx context.Context, association string, kind int) (*AssociateDraftStatus, error)
	// StopStatus saves doing=False with its auditlog update when it changed.
	StopStatus(ctx context.Context, status AssociateDraftStatus, association string, audit LoginAuditContext, at time.Time) error
	// AssociationExists and FamilyExists are the related fields' _default_manager lookups.
	AssociationExists(ctx context.Context, id string) (bool, error)
	FamilyExists(ctx context.Context, id string) (bool, error)
	// CreateDraft stores a new draft (data is the stored JSON text) and its create entry.
	CreateDraft(ctx context.Context, id, association string, data []byte, changes map[string][2]string, valid bool,
		audit LoginAuditContext, at time.Time) error
	// Draft is filter(sport_association, associate_import_draft_id).first(), locked.
	Draft(ctx context.Context, id, association string) (*AssociateDraftRow, error)
	// SaveDraft is draft.save(): data is the stored JSON text, sorted the
	// auditlog rendering of the in-memory value; the update entry lists the
	// fields that differ from the stored row.
	SaveDraft(ctx context.Context, id, association string, data, sorted []byte, valid bool, audit LoginAuditContext, at time.Time) error
	// Certificate is MedicalCertificate.objects.filter(medical_id=id).first().
	Certificate(ctx context.Context, id string) (*AssociateDraftCertificate, error)
	// CreateCertificate is MedicalCertificate.objects.create(user=user) with its
	// create entry, indexed to the actor's association when there is one.
	CreateCertificate(ctx context.Context, id, user string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	// SaveCertificateExpiration stores the expiration date with auditlog's
	// update entry (old stored date against str() of the aware datetime).
	SaveCertificateExpiration(ctx context.Context, certificate AssociateDraftCertificate, expiration, rendered string,
		actorAssociation *string, audit LoginAuditContext, at time.Time) error
	// DeleteDrafts deletes the association's drafts among ids, one delete entry each, and counts them.
	DeleteDrafts(ctx context.Context, association string, ids []string, audit LoginAuditContext, at time.Time) (int, error)
	Commit() error
	Rollback() error
}

type AssociateDraftStore interface {
	BeginAssociateDrafts(context.Context) (AssociateDraftTransaction, error)
}
