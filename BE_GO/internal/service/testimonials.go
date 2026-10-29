package service

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// ErrTestimonialReviewTarget reproduces the reviewed lookup returning None
// (AttributeError, HTTP 500) after the testimonial row was already saved.
var ErrTestimonialReviewTarget = errors.New("testimonial review target does not exist")

type Testimonials struct {
	store domain.TestimonialStore
	now   func() time.Time
}

func NewTestimonials(store domain.TestimonialStore, now func() time.Time) *Testimonials {
	return &Testimonials{store: store, now: now}
}

// Add follows testimonials_create: body checks, then request.user.sport_association
// for the testimonial, then the impersonation-scoped reviewed lookup. Django
// runs without a request transaction, so a failed reviewed lookup still keeps
// the testimonial; Go commits it before reporting that failure.
func (s *Testimonials) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	input, err := dto.ParseTestimonialInput(body)
	if err != nil {
		return err
	}
	owner, err := emailLogsOwner(identity)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginTestimonialMutations(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.AssociationForOwner(ctx, owner, nil)
	if err != nil {
		return err
	}
	if err = tx.InsertTestimonial(ctx, domain.TestimonialWrite{ID: uuid.NewString(), AssociationID: association,
		Text: input.Text, Score: input.Score}); err != nil {
		return err
	}
	// scoped_queryset(...).filter(user=request.user): the scope user, bounded by
	// the impersonation association when the session names one.
	target, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if errors.Is(err, domain.ErrAssociationNotFound) {
		if err = tx.Commit(); err != nil {
			return err
		}
		return ErrTestimonialReviewTarget
	}
	if err != nil {
		return err
	}
	flipped, err := tx.MarkAssociationReviewed(ctx, target)
	if err != nil {
		return err
	}
	if flipped {
		if err = tx.AppendAssociationReviewedAudit(ctx, domain.AssociationReviewedAudit{AssociationID: target,
			Context: paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)}); err != nil {
			return err
		}
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}

var (
	// ErrTestimonialWebhookUnsigned is the view's bare 401: no header, a
	// signature mismatch, or (Go only) no configured secret.
	ErrTestimonialWebhookUnsigned = errors.New("testimonial webhook signature rejected")
	// ErrTestimonialWebhookInvalid is the bare 400 for a missing type or any
	// exception while handling testimonial_created.
	ErrTestimonialWebhookInvalid = errors.New("testimonial webhook payload rejected")
	// ErrTestimonialWebhookUnknown is the bare 404 for an unknown association.
	ErrTestimonialWebhookUnknown = errors.New("testimonial webhook association not found")
)

// Webhook follows testimonials_update. The signature covers Python's compact
// json.dumps of the parsed body, not the received bytes. hmac.compare_digest
// raises TypeError for non-ASCII header text (HTTP 500).
func (s *Testimonials) Webhook(ctx context.Context, identity *domain.ResolvedIdentity, body any, signature *string, secret *string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if signature == nil {
		return ErrTestimonialWebhookUnsigned
	}
	for i := 0; i < len(*signature); i++ {
		if (*signature)[i] >= 0x80 {
			return dto.ErrTestimonialServer
		}
	}
	if secret == nil {
		return ErrTestimonialWebhookUnsigned
	}
	mac := hmac.New(sha256.New, []byte(*secret))
	mac.Write(dto.PythonJSONDumpsCompact(body))
	if !hmac.Equal([]byte(hex.EncodeToString(mac.Sum(nil))), []byte(*signature)) {
		return ErrTestimonialWebhookUnsigned
	}
	payload, err := dto.ParseTestimonialWebhook(body)
	if errors.Is(err, dto.ErrTestimonialWebhookType) {
		return ErrTestimonialWebhookInvalid
	}
	if err != nil || !payload.Created {
		return err
	}
	if payload.Malformed {
		return ErrTestimonialWebhookInvalid
	}
	text, ok := payload.Association.(string)
	if !ok {
		return ErrTestimonialWebhookInvalid
	}
	id, err := paymentInfoParseUUID(text)
	if err != nil {
		return ErrTestimonialWebhookInvalid
	}
	err = s.markReviewed(ctx, identity, id.String(), audit)
	if errors.Is(err, ErrTestimonialWebhookUnknown) || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return err
	}
	if err != nil {
		// The view catches every exception after the type check as 400.
		return ErrTestimonialWebhookInvalid
	}
	return nil
}

func (s *Testimonials) markReviewed(ctx context.Context, identity *domain.ResolvedIdentity, association string, audit domain.LoginAuditContext) error {
	tx, err := s.store.BeginTestimonialMutations(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var scope *string
	if identity != nil {
		scope = paymentCategoryAssociationScope(*identity)
	}
	exists, err := tx.ActiveAssociation(ctx, association, scope)
	if err != nil {
		return err
	}
	if !exists {
		return ErrTestimonialWebhookUnknown
	}
	flipped, err := tx.MarkAssociationReviewed(ctx, association)
	if err != nil {
		return err
	}
	if flipped {
		// Anonymous webhook calls are audited without an actor.
		entry := domain.LoginAuditContext{RemoteAddress: audit.RemoteAddress, RemotePort: audit.RemotePort, CorrelationID: audit.CorrelationID}
		if identity != nil && identity.Actor != nil {
			entry = paymentCategoryAuditContext(*identity, audit)
		}
		if err = tx.AppendAssociationReviewedAudit(ctx, domain.AssociationReviewedAudit{AssociationID: association,
			Context: entry, At: s.now().UTC().Truncate(time.Microsecond)}); err != nil {
			return err
		}
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
