package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrSubscriptionMedicalNoSubscription is the appointments list's 404 {'data': 'no subscription found.'}.
	ErrSubscriptionMedicalNoSubscription = errors.New("medical appointments subscription not found")
	// ErrSubscriptionMedicalNoAppointment is the delete view's 404 {'data': 'no medical appointment found.'}.
	ErrSubscriptionMedicalNoAppointment = errors.New("medical appointment not found")
	// ErrSubscriptionMedicalNotFound is the certificate views' 404 {'exception': 'Subscription not found'}.
	ErrSubscriptionMedicalNotFound = errors.New("medical certificate subscription not found")
	// ErrSubscriptionMedicalNoCertificate is edit's 404 {'exception': 'Medical certificate not found'}.
	ErrSubscriptionMedicalNoCertificate = errors.New("medical certificate not found")
)

// MedicalAppointmentRow is a stored MedicalAppointments row with its
// document's token. Meta is the jsonb text (nil for SQL NULL).
type MedicalAppointmentRow struct {
	ID, Subscription, Region string
	Document, Token, Date    *string
	Notes, Sport, Address    *string
	Meta                     []byte
	Created                  time.Time
}

// MedicalCertificateRow is a stored MedicalCertificate (Expiration is YYYY-MM-DD).
type MedicalCertificateRow struct {
	ID          string
	Document    *string
	User        *string
	Expiration  *string
	Competitive bool
	Notes       *string
}

// MedicalSubscription is a subscription the certificate views resolved:
// its association, certificate (nil without one) and what Subscription.save() reads.
type MedicalSubscription struct {
	ID, Association string
	Certificate     *MedicalCertificateRow
	Target          SubscriptionTagTarget
}

// MedicalDraft is an AssociateImportDraft the expiration view resolved; Data
// is the stored jsonb text (nil for SQL NULL).
type MedicalDraft struct {
	ID, Association string
	Data            []byte
}

// MedicalTargetScope is _medical_certificate_targets: the impersonated
// association, the athlete's own subscriptions (no drafts) or the owner's
// active associations. Group is the group-aware manager's X-Group-Id.
type MedicalTargetScope struct {
	Impersonation *string
	Athlete       bool
	User          string
	Group         *string
}

type SubscriptionMedicalTransaction interface {
	// OwnerAssociation is request.user.sport_association (ErrAssociationNotFound when missing).
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// ActorAssociation is SportAssociation.objects.filter(user=actor).first().
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	// AppointmentSubscription is the group-aware, scoped Subscription lookup of the list.
	AppointmentSubscription(ctx context.Context, id, association string, impersonation, group *string) (bool, error)
	// Appointments are the subscription's appointments ordered by -date.
	Appointments(ctx context.Context, subscription string) ([]MedicalAppointmentRow, error)
	// LockAppointment is the delete view's filter through the subscription join.
	LockAppointment(ctx context.Context, id, subscription, association string) (bool, error)
	DeleteAppointment(ctx context.Context, id string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	// Subscription is targets.filter(subscription_id=id).first(), locked.
	Subscription(ctx context.Context, id string, scope MedicalTargetScope) (*MedicalSubscription, error)
	// Draft is the drafts target's filter(associate_import_draft_id=id).first(), locked.
	Draft(ctx context.Context, id string, scope MedicalTargetScope) (*MedicalDraft, error)
	// Certificate is MedicalCertificate.objects.filter(medical_id=id, user=user).first(), locked.
	Certificate(ctx context.Context, id, user string) (*MedicalCertificateRow, error)
	// SharedElsewhere is isolate_medical_certificate's group-aware check for
	// another association's subscription using the certificate.
	SharedElsewhere(ctx context.Context, certificate, association string, group *string) (bool, error)
	// ForeignReference is _medical_certificate_has_foreign_reference.
	ForeignReference(ctx context.Context, certificate, association string) (bool, error)
	// CopyCertificate creates a certificate with the source's values and its create entry.
	CopyCertificate(ctx context.Context, id string, source MedicalCertificateRow, actorAssociation *string, audit LoginAuditContext,
		at time.Time) error
	// CreateCertificate is MedicalCertificate.objects.create(user=user) with its create entry.
	CreateCertificate(ctx context.Context, id, user string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	// SubscriptionMemberNames is subscription.associate's first and last name (found is false without a member).
	SubscriptionMemberNames(ctx context.Context, subscription string) (first, last *string, found bool, err error)
	// CreatePlaceholderDocument is Document.objects.create(filename=...) with its create entry.
	CreatePlaceholderDocument(ctx context.Context, document NewDocument, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	// AttachNewCertificate is Subscription.save() after medical = the new
	// certificate: the certificate and the dates Subscription.save() filled.
	AttachNewCertificate(ctx context.Context, subscription MedicalSubscription, certificate string, dates SubscriptionDates,
		audit LoginAuditContext, at time.Time) error
	// SavePlaceholderCertificate is the new certificate's save() with its document and expiration date.
	SavePlaceholderCertificate(ctx context.Context, certificate, document, expiration string, actorAssociation *string,
		audit LoginAuditContext, at time.Time) error
	// SetSubscriptionMedical is Subscription.save(update_fields=['medical']).
	SetSubscriptionMedical(ctx context.Context, subscription, association, old string, medical *string, audit LoginAuditContext, at time.Time) error
	// DetachSubscription is Subscription.save() after medical = None: the
	// certificate and the dates Subscription.save() filled.
	DetachSubscription(ctx context.Context, subscription MedicalSubscription, dates SubscriptionDates, audit LoginAuditContext, at time.Time) error
	// SaveCertificate is MedicalCertificate.save() of the new notes and
	// competitive flag: the update entry lists the changed fields.
	SaveCertificate(ctx context.Context, old MedicalCertificateRow, notes *string, competitive bool, actorAssociation *string,
		audit LoginAuditContext, at time.Time) error
	// SaveExpiration stores the date with auditlog's update entry (the stored
	// date against str() of the aware datetime).
	SaveExpiration(ctx context.Context, certificate MedicalCertificateRow, expiration string, actorAssociation *string,
		audit LoginAuditContext, at time.Time) error
	// DeleteCertificate is certificate.delete(): the collector nulls
	// Subscription.medical and auditlog records the delete.
	DeleteCertificate(ctx context.Context, id string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	// DeleteUnreferencedDocument deletes the document (with its entry) when no row references it.
	DeleteUnreferencedDocument(ctx context.Context, id string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	// SaveDraftData is draft.save() of new data: data is the stored JSON text,
	// sorted auditlog's rendering; an entry only when the data changed.
	SaveDraftData(ctx context.Context, draft MedicalDraft, data, sorted []byte, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type SubscriptionMedicalStore interface {
	BeginSubscriptionMedical(context.Context) (SubscriptionMedicalTransaction, error)
}
