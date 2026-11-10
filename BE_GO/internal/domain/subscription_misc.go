package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrSubscriptionMiscNotPorted refuses an input whose Django behaviour is not
	// reproduced (a codice fiscale input outside the ported subset, an outbound
	// public-link lookup, a Document other rows reference), before any write.
	ErrSubscriptionMiscNotPorted = refusal.New("subscription request not ported")
	// ErrSubscriptionMiscServer is an exception the views do not handle (a 500).
	ErrSubscriptionMiscServer = errors.New("subscription request failed")
)

// SubscriptionMiscCustom is a subscription id with its custom_data jsonb text
// (nil for SQL NULL).
type SubscriptionMiscCustom struct {
	ID         string
	CustomData *string
}

// SubscriptionMiscPeriod is an associate id (nil: no associate) and the
// subscription period of an active-candidate subscription.
type SubscriptionMiscPeriod struct {
	AssociateID *string
	Start, End  *time.Time
}

// SubscriptionMiscLink is a stored CumulativeSubscriptionGymLinks row.
type SubscriptionMiscLink struct {
	ID            string
	AssociationID string
	GymName       *string
}

// SubscriptionMiscFile is a SubscriptionFile with its Document id.
type SubscriptionMiscFile struct {
	ID         string
	DocumentID string
}

type SubscriptionMiscTransaction interface {
	// Association is request.user.sport_association for a non-athlete
	// (ErrAssociationNotFound without a live one).
	Association(ctx context.Context, owner string) (string, error)
	// ScopedFirstAssociation is scoped_queryset(SportAssociation.objects).filter(user=...).first().
	ScopedFirstAssociation(ctx context.Context, user string, scope *string) (*string, error)
	// ActorAssociation is SportAssociation.objects.filter(user=actor).first().
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	// ImportDoing is the newest importing status's doing (found false without one).
	ImportDoing(ctx context.Context, association string) (found bool, doing *bool, err error)
	// Federation is the association's group-aware, scoped subscriptions whose
	// custom_data text matches one of the patterns case-insensitively.
	Federation(ctx context.Context, association string, patterns []string, group, scope *string) ([]SubscriptionMiscCustom, error)
	// CreateLink inserts a link and its auditlog create entry (indexed directly).
	CreateLink(ctx context.Context, id, association, token, gymName string, expires, created time.Time, audit LoginAuditContext,
		at time.Time) error
	// LiveLink is filter(token=token, expires_at__gt=now).first().
	LiveLink(ctx context.Context, token string, now time.Time) (*SubscriptionMiscLink, error)
	// DeleteExpiredLinks deletes the links with expires_at < now, one auditlog
	// delete each (indexed from changes, else to the actor's association).
	DeleteExpiredLinks(ctx context.Context, now time.Time, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	// PairingLookup reports whether source_token would consult the paired
	// authority: a self-hosted pairing pending or paired with a remote id.
	PairingLookup(ctx context.Context) (bool, error)
	// ActivePeriods are the association's group-aware, scoped accepted or
	// pending subscriptions ending on or after today.
	ActivePeriods(ctx context.Context, association string, today time.Time, group, scope *string) ([]SubscriptionMiscPeriod, error)
	// Candidates are the association's group-aware, scoped subscriptions whose
	// custom_data text contains needle (case-insensitively), started on or
	// before cutoff and whose associate is not excluded.
	Candidates(ctx context.Context, association, needle string, cutoff time.Time, excluded []string, group, scope *string) (
		[]SubscriptionMiscCustom, error)
	// Subscription is the group-aware, scoped subscription of the association (false: none).
	Subscription(ctx context.Context, id, association string, group, scope *string) (bool, error)
	// File is SubscriptionFile.objects.filter(pk, subscription).first().
	File(ctx context.Context, id, subscription string) (*SubscriptionMiscFile, error)
	// DocumentShared reports whether any row other than the file references the document.
	DocumentShared(ctx context.Context, document, file string) (bool, error)
	// DeleteFile deletes the file with its auditlog delete entry and returns the changes.
	DeleteFile(ctx context.Context, id string, actorAssociation *string, audit LoginAuditContext, at time.Time) (map[string][2]string, error)
	// RecordFileDelete records one more delete entry for the already deleted
	// file (Model.delete() of the view's stale instance).
	RecordFileDelete(ctx context.Context, id string, changes map[string][2]string, actorAssociation *string, audit LoginAuditContext,
		at time.Time) error
	// DeleteSharedDocument is document.delete() of a document other rows
	// reference: the deletion collector with its audit entries. It returns the
	// delete diff of the file, which the collector deleted.
	DeleteSharedDocument(ctx context.Context, document, file string, group *string, audit LoginAuditContext, at time.Time) (
		map[string][2]string, error)
	// DeleteDocument deletes the document with its auditlog delete entry.
	DeleteDocument(ctx context.Context, id string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type SubscriptionMiscStore interface {
	BeginSubscriptionMisc(context.Context) (SubscriptionMiscTransaction, error)
}
