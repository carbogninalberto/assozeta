package domain

import "context"

// RelayGoRealtimeEvent is one claimed row of public.assozeta_go_realtime_events.
type RelayGoRealtimeEvent struct {
	ID       int64  `db:"id"`
	Version  int64  `db:"envelope_version"`
	Kind     string `db:"kind"`
	Target   string `db:"target_id"`
	Attempts int64  `db:"attempts"`
}

// RelayGoRealtimeClaim is the claim transaction of relay_once.
type RelayGoRealtimeClaim interface {
	Pending(ctx context.Context, limit int) ([]RelayGoRealtimeEvent, error)
	Delivered(ctx context.Context, id int64) error
	Failed(ctx context.Context, id int64, code string) error
	Retry(ctx context.Context, id int64) error
}

// RelayGoRealtimeStore is the outbox seen by relay_once.
type RelayGoRealtimeStore interface {
	Present(ctx context.Context) (bool, error)
	Transaction(ctx context.Context, fn func(RelayGoRealtimeClaim) error) error
}
