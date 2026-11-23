package whatsapp

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"os"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/google/uuid"
)

var rank = map[string]int{"queued": 0, "sent": 1, "failed": 2, "delivered": 3, "read": 4}

type DeliveryQueue struct {
	owner    *handoff.OutboxOwner
	db       *sql.DB
	settings func(context.Context) (config.WhatsAppSettings, error)
}

func NewQueue(db *sql.DB, settings func(context.Context) (config.WhatsAppSettings, error)) *DeliveryQueue {
	return &DeliveryQueue{owner: handoff.NewOutboxOwner("outbox"), db: db, settings: settings}
}

func (q *DeliveryQueue) Enqueue(ctx context.Context, user, kind, message string) error {
	cfg, err := q.settings(ctx)
	if err != nil || cfg.Disabled || cfg.Routes == "{}" {
		return err
	}
	var routes map[string]json.RawMessage
	if err := json.Unmarshal([]byte(cfg.Routes), &routes); err != nil {
		return errors.New("invalid WhatsApp notification routes")
	}
	raw := routes[kind]
	if len(raw) == 0 || string(raw) == "null" {
		return nil
	}
	var route string
	// An invalid value is recorded as a failed delivery for its own event.
	_ = json.Unmarshal(raw, &route)
	var phone string
	if err := q.db.QueryRowContext(ctx, "SELECT COALESCE(phone,'') FROM bakney_user WHERE user_id=$1", user).Scan(&phone); err != nil {
		return err
	}
	recipient := dto.MessageRecipientAddress(domain.MessageRecipient{Phone: phone}, "whatsapp")
	state, name, language, code, detail := "queued", "", "", "", ""
	if route != "text" {
		parts := dto.WhatsAppTemplateRoute.FindStringSubmatch(route)
		if parts == nil {
			code, detail = "configuration", "Invalid WhatsApp notification route"
		} else {
			name, language = parts[1], parts[2]
			if parts[3] == "" {
				message = ""
			}
		}
	}
	if cfg.PhoneID == "" || cfg.AccessToken == "" {
		code, detail = "configuration", "Missing WhatsApp resource ID or access token"
	}
	if recipient == "" {
		recipient, code, detail = phone, "invalid_recipient", "International phone number required"
	}
	id := uuid.NewString()
	if code != "" {
		state = "failed"
		slog.WarnContext(ctx, "WhatsApp enqueue failed", "id", id, "code", code)
	}
	_, err = q.db.ExecContext(ctx, `INSERT INTO communications_whatsappdelivery
		(id,event_type,recipient,body,template_name,template_language,status,attempts,error_code,error_message,created_at,updated_at,next_attempt_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,0,$8,$9,now(),now(),now())`, id, kind, recipient, message, name, language, state, code, detail)
	return err
}

// DeliverOne locks one due row through the bounded provider request. Both
// workers use the same locking protocol, including across backend rollouts.
func (q *DeliveryQueue) DeliverOne(ctx context.Context) (bool, error) {
	return q.deliverOne(ctx, q.owner.ID)
}

// Drain runs one bounded legacy poll under the broker consumer's identity.
func (q *DeliveryQueue) Drain(ctx context.Context, consumer string) error {
	for i := 0; i < 50; i++ {
		worked, err := q.deliverOne(ctx, consumer)
		if err != nil || !worked {
			return err
		}
	}
	return nil
}

func (q *DeliveryQueue) deliverOne(ctx context.Context, consumer string) (bool, error) {
	cfg, err := q.settings(ctx)
	if err != nil || cfg.Disabled || cfg.PhoneID == "" || cfg.AccessToken == "" {
		return false, err
	}
	defer q.owner.Enter()()
	managed, err := handoff.OutboxInstalled(ctx, q.db)
	if err != nil {
		return false, err
	}
	tx, err := q.db.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	if managed {
		var outcome string
		err = tx.QueryRowContext(ctx, "SELECT outcome FROM public.assozeta_outbox_claim('go',$1,'whatsapp')", consumer).Scan(&outcome)
		if err != nil || outcome != "owner" {
			return false, err
		}
	}
	provider := NewCloudAPI(cfg)
	var id, recipient, body, name, language string
	var attempts int
	query := `SELECT id,recipient,body,template_name,template_language,attempts
		FROM communications_whatsappdelivery WHERE status='queued' AND next_attempt_at<=now()`
	if managed {
		query += ` AND NOT EXISTS (SELECT FROM public.assozeta_outbox_effects e WHERE e.outbox='whatsapp'
		AND e.item_id=communications_whatsappdelivery.id::text AND e.state IN ('confirmed','ambiguous','reconciled_sent'))`
	}
	err = tx.QueryRowContext(ctx, query+" ORDER BY next_attempt_at,id LIMIT 1 FOR UPDATE SKIP LOCKED").Scan(&id, &recipient, &body, &name, &language, &attempts)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	attempts++
	if managed {
		var outcome string
		err = tx.QueryRowContext(ctx, "SELECT public.assozeta_outbox_effect_intent('go',$1,'whatsapp',$2,$3)", consumer, id, attempts).Scan(&outcome)
		if err != nil {
			return false, err
		}
		if err = tx.Commit(); err != nil {
			return false, err
		}
		if outcome != "recorded" {
			return true, nil
		}
		// Only the caller that just committed a new intent has permission to
		// send. A competing poll can flag the intent while we reacquire the
		// item lock, but it cannot obtain a second permission.
		tx, err = q.db.BeginTx(ctx, nil)
		if err != nil {
			return false, err
		}
		defer tx.Rollback()
		err = tx.QueryRowContext(ctx, "SELECT outcome FROM public.assozeta_outbox_claim('go',$1,'whatsapp')", consumer).Scan(&outcome)
		if err != nil {
			return false, err
		}
		var queued bool
		err = tx.QueryRowContext(ctx, `SELECT status='queued' FROM communications_whatsappdelivery WHERE id=$1 FOR UPDATE`, id).Scan(&queued)
		if err != nil || !queued {
			return false, err
		}
		if outcome != "owner" {
			// The freeze won before the send started. No provider call was made.
			_, err = tx.ExecContext(ctx, "DELETE FROM public.assozeta_outbox_effects WHERE outbox='whatsapp' AND item_id=$1 AND attempt=$2 AND state IN ('intent','ambiguous')", id, attempts)
			if err != nil {
				return false, err
			}
			return false, tx.Commit()
		}
		var authorized bool
		err = tx.QueryRowContext(ctx, `SELECT state IN ('intent','ambiguous')
			FROM public.assozeta_outbox_effects e JOIN public.assozeta_go_job_ownership o
			ON e.runtime=o.runtime AND e.ownership_generation=o.generation
			CROSS JOIN LATERAL pg_advisory_xact_lock(hashtextextended(e.item_id,8))
			WHERE e.outbox='whatsapp' AND e.item_id=$1 AND e.attempt=$2 AND o.singleton=1`, id, attempts).Scan(&authorized)
		if errors.Is(err, sql.ErrNoRows) {
			return false, nil
		}
		if err != nil || !authorized {
			return false, err
		}
	}
	var providerID string
	if name == "" {
		providerID, err = provider.SendText(ctx, recipient, body, id)
	} else {
		providerID, err = provider.SendTemplate(ctx, recipient, name, language, body, id)
	}
	state, code, detail := "sent", "", ""
	delay := 0
	if err != nil {
		state = "failed"
		var failure *ProviderError
		if !errors.As(err, &failure) {
			return false, err
		}
		if managed {
			if failure.Code == "network" || failure.Code == "invalid_response" {
				_, err = tx.ExecContext(ctx, "UPDATE public.assozeta_outbox_effects SET state='ambiguous' WHERE outbox='whatsapp' AND item_id=$1 AND attempt=$2 AND state='intent'", id, attempts)
				if err != nil {
					return false, err
				}
				return true, tx.Commit()
			}
			_, err = tx.ExecContext(ctx, "SELECT public.assozeta_outbox_reconcile('whatsapp',$1,'not_sent',$2)", id, consumer)
			if err != nil {
				return false, err
			}
		}
		code, detail = failure.Code, failure.Message
		if failure.Transient && attempts < 3 {
			state = "queued"
			delay = 30 << (attempts - 1)
		}
		slog.WarnContext(ctx, "WhatsApp send failed", "id", id, "attempt", attempts, "code", code)
	} else if managed {
		handoff.Barrier("effect:accepted", id)
		if _, err = tx.ExecContext(ctx, "SELECT public.assozeta_outbox_effect_confirm('whatsapp',$1,$2,$3)", id, attempts, providerID); err != nil {
			return false, err
		}
	}
	_, err = tx.ExecContext(ctx, `UPDATE communications_whatsappdelivery SET provider_message_id=NULLIF($2,''),
		status=$3,attempts=$4,error_code=$5,error_message=$6,updated_at=clock_timestamp(),
		next_attempt_at=CASE WHEN $7::integer>0 THEN clock_timestamp()+$7 * interval '1 second' ELSE next_attempt_at END WHERE id=$1`,
		id, providerID, state, attempts, code, detail, delay)
	if err != nil {
		return false, err
	}
	return true, tx.Commit()
}

func (q *DeliveryQueue) Run(ctx context.Context, acquire func(context.Context) (io.Closer, error)) {
	interval, err := time.ParseDuration(os.Getenv("ASSOZETA_HANDOFF_HEARTBEAT"))
	if err != nil || interval <= 0 {
		interval = time.Second
	}
	stop := q.owner.Start(ctx, q.db, interval)
	defer stop()
	ticker := time.NewTicker(pollInterval())
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			for i := 0; i < 50; i++ {
				lease, err := acquire(ctx)
				if err != nil {
					slog.WarnContext(ctx, "WhatsApp worker could not acquire restore lock")
					break
				}
				worked, err := q.DeliverOne(ctx)
				lease.Close()
				if err != nil {
					slog.ErrorContext(ctx, "WhatsApp outbox unavailable")
					break
				}
				if !worked {
					break
				}
			}
		}
	}
}

func recordStatus(ctx context.Context, db *sql.DB, event Status) error {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var reference any
	if ref, err := uuid.Parse(event.Reference); err == nil {
		reference = ref.String()
	}
	var id, state string
	var currentID sql.NullString
	err = tx.QueryRowContext(ctx, `SELECT id,status,provider_message_id FROM communications_whatsappdelivery
		WHERE id=$2 OR ($2::uuid IS NULL AND provider_message_id=$1) FOR UPDATE`, event.ID, reference).Scan(&id, &state, &currentID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	managed, err := handoff.OutboxInstalled(ctx, tx)
	if err != nil {
		return err
	}
	if managed {
		_, err = tx.ExecContext(ctx, `UPDATE public.assozeta_outbox_effects SET state='reconciled_sent',
			provider_ref=$1,resolved_at=clock_timestamp(),resolved_by='webhook'
			WHERE outbox='whatsapp' AND item_id=$2 AND state IN ('intent','ambiguous')`, event.ID, id)
		if err != nil {
			return err
		}
	}
	result, err := tx.ExecContext(ctx, `INSERT INTO communications_whatsappreceipt
		(delivery_id,provider_message_id,status,occurred_at,error_code,error_message) VALUES($1,$2,$3,$4,$5,$6)
		ON CONFLICT (delivery_id,provider_message_id,status) DO NOTHING`, id, event.ID, event.State, event.At, event.Code, event.Message)
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if count > 0 && (!currentID.Valid || rank[event.State] > rank[state]) {
		_, err = tx.ExecContext(ctx, `UPDATE communications_whatsappdelivery SET provider_message_id=COALESCE(provider_message_id,$2),status=$3,error_code=$4,
			error_message=$5,updated_at=now() WHERE id=$1`, id, event.ID, event.State, event.Code, event.Message)
		if err != nil {
			return err
		}
		if event.State == "failed" {
			slog.WarnContext(ctx, "WhatsApp delivery failed", "id", id, "code", event.Code)
		}
	}
	return tx.Commit()
}
