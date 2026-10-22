package repository

import (
	"context"
	"database/sql"
	"errors"
	"math"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// ScheduleCandidates reads only bounded discovery metadata. The occurrence
// publisher must reread every authoritative definition and cursor under locks.
// No static schedule, task allowlist or enabled filter changes which PKs are
// visited; otherwise sparse matches could turn LIMIT into an unbounded scan.
type ScheduleCandidates struct{ db *sqlx.DB }

func NewScheduleCandidates(db *sqlx.DB) *ScheduleCandidates { return &ScheduleCandidates{db: db} }

// HighWater fixes a rotation's upper ID so continuously arriving higher IDs
// cannot prevent a return to earlier sources. It does not lock editors or imply
// a stable snapshot: edits/deletions are resolved by each fresh publication.
func (r *ScheduleCandidates) HighWater(ctx context.Context) (int64, error) {
	var id int64
	err := r.db.GetContext(ctx, &id, `SELECT id FROM public.django_celery_beat_periodictask ORDER BY id DESC LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, nil
	}
	return id, err
}

// ListCandidates is one indexed raw PK range, never OFFSET or filtered matches.
// The caller preserves throughID across a rotation and advances even when a
// considered source is disabled, unsupported, edited or deleted.
func (r *ScheduleCandidates) ListCandidates(ctx context.Context, afterID, throughID int64, limit int) ([]domain.PeriodicCandidate, error) {
	if afterID < 0 || throughID < afterID || throughID > math.MaxInt32 || limit < 1 || limit > 64 {
		return nil, domain.ErrSchedulePageInvalid
	}
	rows := []domain.PeriodicCandidate{}
	err := r.db.SelectContext(ctx, &rows, `SELECT id,task,enabled FROM public.django_celery_beat_periodictask WHERE id>$1 AND id<=$2 ORDER BY id LIMIT $3`, afterID, throughID, limit)
	return rows, err
}
