package handoff

import (
	"context"
	"database/sql"
	"log/slog"
	"sync"
	"time"

	"github.com/google/uuid"
)

type OutboxOwner struct {
	ID   string
	role string
	mu   sync.RWMutex
}

func NewOutboxOwner(role string) *OutboxOwner {
	return &OutboxOwner{ID: "go-" + role + "-" + uuid.NewString(), role: role}
}

// Enter marks a delivery in flight until its transaction and provider call finish.
func (o *OutboxOwner) Enter() func() {
	o.mu.RLock()
	return o.mu.RUnlock
}

func OutboxInstalled(ctx context.Context, db interface {
	QueryRowContext(context.Context, string, ...any) *sql.Row
}) (bool, error) {
	var installed bool
	err := db.QueryRowContext(ctx, "SELECT to_regclass('public.assozeta_outbox_effects') IS NOT NULL").Scan(&installed)
	return installed, err
}

func (o *OutboxOwner) heartbeat(ctx context.Context, db *sql.DB) error {
	installed, err := OutboxInstalled(ctx, db)
	if err != nil || !installed {
		return err
	}
	if !o.mu.TryLock() {
		// A close waiting for a relay's FOR SHARE must not stop its liveness updates.
		_, err = db.ExecContext(ctx, "UPDATE public.assozeta_handoff_consumers SET heartbeat_at=clock_timestamp(),heartbeat_seq=heartbeat_seq+1 WHERE consumer_id=$1", o.ID)
		return err
	}
	defer o.mu.Unlock()
	var operation sql.NullString
	var owner string
	err = db.QueryRowContext(ctx, "SELECT runtime,operation_id FROM public.assozeta_handoff_heartbeat($1,'go',$2)", o.ID, o.role).Scan(&owner, &operation)
	if err == nil && owner == "go" && operation.Valid {
		_, err = db.ExecContext(ctx, "SELECT public.assozeta_handoff_acknowledge_freeze($1,$2)", o.ID, operation.String)
	}
	return err
}

// Start registers before the first claim, and joins the heartbeat on shutdown.
func (o *OutboxOwner) Start(ctx context.Context, db *sql.DB, interval time.Duration) func() {
	ctx, cancel := context.WithCancel(ctx)
	pulse := func() {
		if err := o.heartbeat(ctx, db); err != nil && ctx.Err() == nil {
			slog.Warn("outbox heartbeat unavailable", "role", o.role)
		}
	}
	pulse()
	done := make(chan struct{})
	go func() {
		defer close(done)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				pulse()
			}
		}
	}()
	return func() { cancel(); <-done }
}
