package repository

import (
	"context"
	"fmt"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// RelayGoRealtimeOutbox is the outbox access of Django's
// notifications.go_realtime.relay_once on public.assozeta_go_realtime_events.
type RelayGoRealtimeOutbox struct{ db *sqlx.DB }

func NewRelayGoRealtimeOutbox(db *sqlx.DB) *RelayGoRealtimeOutbox {
	return &RelayGoRealtimeOutbox{db: db}
}

var _ domain.RelayGoRealtimeStore = (*RelayGoRealtimeOutbox)(nil)

// RelayGoRealtimeTx is the claim transaction.
type RelayGoRealtimeTx struct{ tx *sqlx.Tx }

// Present is outbox_present(): SELECT to_regclass(...) IS NOT NULL.
func (r *RelayGoRealtimeOutbox) Present(ctx context.Context) (bool, error) {
	var present bool
	err := r.db.GetContext(ctx, &present, `SELECT to_regclass($1) IS NOT NULL`, "public.assozeta_go_realtime_events")
	return present, err
}

// Transaction runs fn inside one transaction (transaction.atomic()); an
// error from fn rolls it back.
func (r *RelayGoRealtimeOutbox) Transaction(ctx context.Context, fn func(domain.RelayGoRealtimeClaim) error) (err error) {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = tx.Rollback()
		}
	}()
	if err = fn(&RelayGoRealtimeTx{tx: tx}); err != nil {
		return err
	}
	return tx.Commit()
}

// Pending claims up to limit pending rows in id order, skipping rows locked
// by another relay.
func (t *RelayGoRealtimeTx) Pending(ctx context.Context, limit int) ([]domain.RelayGoRealtimeEvent, error) {
	var rows []domain.RelayGoRealtimeEvent
	err := t.tx.SelectContext(ctx, &rows, `SELECT id, envelope_version, kind, target_id::text AS target_id, attempts
		FROM public.assozeta_go_realtime_events
		WHERE delivered_at IS NULL AND failed_code IS NULL ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED`, limit)
	return rows, err
}

func (t *RelayGoRealtimeTx) exec(ctx context.Context, query string, args ...any) error {
	result, err := t.tx.ExecContext(ctx, query, args...)
	if err != nil {
		return err
	}
	if _, err := result.RowsAffected(); err != nil {
		return fmt.Errorf("relay outbox update: %w", err)
	}
	return nil
}

// Delivered records a delivered event.
func (t *RelayGoRealtimeTx) Delivered(ctx context.Context, id int64) error {
	return t.exec(ctx, `UPDATE public.assozeta_go_realtime_events SET attempts=attempts+1, delivered_at=clock_timestamp() WHERE id=$1`, id)
}

// Failed records a failed event with its code.
func (t *RelayGoRealtimeTx) Failed(ctx context.Context, id int64, code string) error {
	return t.exec(ctx, `UPDATE public.assozeta_go_realtime_events SET attempts=attempts+1, failed_code=$1 WHERE id=$2`, code, id)
}

// Retry records one more attempt of an event left pending.
func (t *RelayGoRealtimeTx) Retry(ctx context.Context, id int64) error {
	return t.exec(ctx, `UPDATE public.assozeta_go_realtime_events SET attempts=attempts+1 WHERE id=$1`, id)
}
