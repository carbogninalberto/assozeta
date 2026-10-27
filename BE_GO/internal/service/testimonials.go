package service

import (
	"context"
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
