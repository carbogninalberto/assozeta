package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type Testimonials struct{ db *sqlx.DB }

func NewTestimonials(db *sqlx.DB) *Testimonials { return &Testimonials{db: db} }

var _ domain.TestimonialStore = (*Testimonials)(nil)

func (r *Testimonials) BeginTestimonialMutations(ctx context.Context) (domain.TestimonialTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &testimonialTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

// Reuse the shared owned transaction and active-association lookup.
type testimonialTransaction struct{ *paymentCategoryTransaction }

var _ domain.TestimonialTransaction = (*testimonialTransaction)(nil)

func (t *testimonialTransaction) InsertTestimonial(ctx context.Context, row domain.TestimonialWrite) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_testimonial (testimonial_id,sport_association_id,text,score)
 VALUES ($1,$2,$3,$4)`, row.ID, row.AssociationID, row.Text, row.Score)
	return err
}

// The view's full-row save changes only reviewed; auditlog logs it only when
// the stored value was false.
func (t *testimonialTransaction) MarkAssociationReviewed(ctx context.Context, association string) (bool, error) {
	var reviewed bool
	err := t.tx.GetContext(ctx, &reviewed, `SELECT reviewed FROM application_sportassociation WHERE sport_association_id=$1 FOR UPDATE`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return false, domain.ErrAssociationNotFound
	}
	if err != nil || reviewed {
		return false, err
	}
	_, err = t.tx.ExecContext(ctx, `UPDATE application_sportassociation SET reviewed=true WHERE sport_association_id=$1`, association)
	return err == nil, err
}

func (t *testimonialTransaction) AppendAssociationReviewedAudit(ctx context.Context, event domain.AssociationReviewedAudit) error {
	path := "self"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "sportassociation", Key: event.AssociationID,
		Representation: "SportAssociation object (" + event.AssociationID + ")", Action: 1,
		Changes: map[string][2]string{"reviewed": {"False", "True"}}, Context: event.Context,
		AssociationID: &event.AssociationID, ResolutionPath: &path, At: event.At})
}
