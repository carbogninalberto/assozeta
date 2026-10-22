package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type ProfileInfoDetails struct{ db *sqlx.DB }

func NewProfileInfoDetails(db *sqlx.DB) *ProfileInfoDetails { return &ProfileInfoDetails{db: db} }
func (r *ProfileInfoDetails) Read(ctx context.Context, user, owner string) (domain.ProfileInfoDetails, error) {
	var result domain.ProfileInfoDetails
	err := r.db.GetContext(ctx, &result, `SELECT u.user_id,u.temporary_invoice_deletion,a.sport_association_id,a.review_url,a.review_url_enabled
 FROM bakney_user u JOIN application_sportassociation a ON a.user_id=$2 AND NOT a.deleted
 WHERE u.user_id=$1 AND NOT u.deleted AND (u.user_id=a.user_id OR (u.role=3 AND u.connected_user_id=a.user_id))`, user, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.ProfileInfoDetails{}, domain.ErrAssociationNotFound
	}
	if err != nil {
		return domain.ProfileInfoDetails{}, err
	}
	return result, nil
}
