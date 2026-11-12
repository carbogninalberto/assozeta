package service

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/channels"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Port of Django's relay_go_realtime management command and
// notifications.go_realtime.relay_once: pending outbox rows are claimed in
// one transaction and forwarded through the Channels layer.

// RelayGoRealtimeLimit and RelayGoRealtimeMaxAttempts are relay_once's
// limit default and go_realtime.MAX_ATTEMPTS.
const (
	RelayGoRealtimeLimit       = 100
	RelayGoRealtimeMaxAttempts = 30
)

// RelayGoRealtimeSender is channel_layer.group_send.
type RelayGoRealtimeSender interface {
	GroupSend(ctx context.Context, group string, message channels.Dict) error
}

// RelayGoRealtimeCounts is relay_once's result dict.
type RelayGoRealtimeCounts struct{ Delivered, Retry, Failed int }

// String is Python's str() of {'delivered': n, 'retry': n, 'failed': n}.
func (c RelayGoRealtimeCounts) String() string {
	return fmt.Sprintf("{'delivered': %d, 'retry': %d, 'failed': %d}", c.Delivered, c.Retry, c.Failed)
}

// relayGoRealtimeHandlers is go_realtime.HANDLERS: kind -> (group for the
// target, Channels message).
var relayGoRealtimeHandlers = map[string]func(target string) (string, channels.Dict){
	// communications.staff_board.staff_board_group
	"staff_board_changed": func(target string) (string, channels.Dict) {
		return "staff_board_" + target, channels.Dict{{Key: "type", Value: "staff_board_changed"}}
	},
}

// RelayGoRealtime runs relay_once and the command's loop.
type RelayGoRealtime struct {
	store  domain.RelayGoRealtimeStore
	sender RelayGoRealtimeSender
	logger *slog.Logger
}

func NewRelayGoRealtime(store domain.RelayGoRealtimeStore, sender RelayGoRealtimeSender, logger *slog.Logger) *RelayGoRealtime {
	if logger == nil {
		logger = slog.Default()
	}
	return &RelayGoRealtime{store: store, sender: sender, logger: logger}
}

// Once is relay_once(): process up to RelayGoRealtimeLimit pending events.
func (r *RelayGoRealtime) Once(ctx context.Context) (RelayGoRealtimeCounts, error) {
	var counts RelayGoRealtimeCounts
	present, err := r.store.Present(ctx)
	if err != nil || !present {
		return counts, err
	}
	err = r.store.Transaction(ctx, func(claim domain.RelayGoRealtimeClaim) error {
		counts = RelayGoRealtimeCounts{}
		rows, err := claim.Pending(ctx, RelayGoRealtimeLimit)
		if err != nil {
			return err
		}
		for _, row := range rows {
			outcome, code := r.deliver(ctx, row)
			switch {
			case outcome == "delivered":
				err = claim.Delivered(ctx, row.ID)
				counts.Delivered++
			case outcome == "failed" || row.Attempts+1 >= RelayGoRealtimeMaxAttempts:
				if code == "" {
					code = "delivery_exhausted"
				}
				err = claim.Failed(ctx, row.ID, code)
				counts.Failed++
			default:
				err = claim.Retry(ctx, row.ID)
				counts.Retry++
			}
			if err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		return RelayGoRealtimeCounts{}, err
	}
	return counts, nil
}

// deliver is go_realtime.deliver. The configured layer always exists, so
// Django's branch for a missing layer has no counterpart.
func (r *RelayGoRealtime) deliver(ctx context.Context, row domain.RelayGoRealtimeEvent) (string, string) {
	handler, ok := relayGoRealtimeHandlers[row.Kind]
	if row.Version != 1 || !ok {
		return "failed", "unsupported_event"
	}
	group, message := handler(row.Target)
	if err := r.sender.GroupSend(ctx, group, message); err != nil {
		r.logger.Error("Unable to relay Go realtime event", "error", err)
		return "retry", ""
	}
	return "delivered", ""
}

// Run is the command's loop without --once: drain while making progress
// (a retry waits for the next interval), then wait for a notification on
// wake or until poll elapses. It returns on the first error or when ctx ends.
func (r *RelayGoRealtime) Run(ctx context.Context, poll time.Duration, wake <-chan struct{}) error {
	for {
		for {
			counts, err := r.Once(ctx)
			if err != nil {
				return err
			}
			if counts.Retry > 0 || counts.Delivered == 0 && counts.Failed == 0 {
				break
			}
		}
		timer := time.NewTimer(poll)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-wake:
			// raw.notifies.clear(): several notifications wake the loop once.
			for drained := false; !drained; {
				select {
				case <-wake:
				default:
					drained = true
				}
			}
		case <-timer.C:
		}
		timer.Stop()
	}
}
