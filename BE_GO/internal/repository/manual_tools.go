package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type ManualToolContext struct{ db sqlx.QueryerContext }

func NewManualToolContext(db sqlx.QueryerContext) *ManualToolContext {
	return &ManualToolContext{db: db}
}

// One statement observes the first instance, its primary association and the
// effective user's current status. No account credentials or per-section reads.
func (r *ManualToolContext) ManualToolContext(ctx context.Context, userID string) (domain.ManualToolSnapshot, error) {
	var rows []domain.ManualToolSnapshot
	err := sqlx.SelectContext(ctx, r.db, &rows, manualToolContextSQL, userID)
	if err != nil {
		return domain.ManualToolSnapshot{}, err
	}
	if len(rows) == 0 {
		return domain.ManualToolSnapshot{}, nil
	}
	return rows[0], nil
}

const manualToolContextSQL = `SELECT c.self_hosted,
 COALESCE(c.primary_association_id::text,'') AS primary_association_id,
 COALESCE(a.user_id::text,'') AS primary_owner_id,
 COALESCE(u.user_id::text,'') AS active_user_id
 FROM instance_instanceconfiguration c
 LEFT JOIN application_sportassociation a ON a.sport_association_id=c.primary_association_id
 LEFT JOIN bakney_user u ON u.user_id=NULLIF($1,'')::uuid AND u.is_active AND NOT u.deleted
 ORDER BY c.id LIMIT 1`
