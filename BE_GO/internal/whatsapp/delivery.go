package whatsapp

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"regexp"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

var templateRoute = regexp.MustCompile(`^([a-z0-9_]+)/([A-Za-z0-9_]+)(/message)?$`)
var rank = map[string]int{"queued": 0, "sent": 1, "failed": 2, "delivered": 3, "read": 4}

type DeliveryQueue struct {
	db       *sql.DB
	config   config.WhatsAppSettings
	provider Provider
}

func NewQueue(db *sql.DB, cfg config.WhatsAppSettings, provider Provider) *DeliveryQueue {
	return &DeliveryQueue{db, cfg, provider}
}

func (q *DeliveryQueue) Enqueue(ctx context.Context, user, kind, message string) error {
	if q.config.Routes == "{}" {
		return nil
	}
	var routes map[string]json.RawMessage
	if err := json.Unmarshal([]byte(q.config.Routes), &routes); err != nil {
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
		parts := templateRoute.FindStringSubmatch(route)
		if parts == nil {
			code, detail = "configuration", "Invalid WhatsApp notification route"
		} else {
			name, language = parts[1], parts[2]
			if parts[3] == "" {
				message = ""
			}
		}
	}
	if q.config.PhoneID == "" || q.config.AccessToken == "" {
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
	_, err := q.db.ExecContext(ctx, `INSERT INTO communications_whatsappdelivery
		(id,event_type,recipient,body,template_name,template_language,status,attempts,error_code,error_message,created_at,updated_at,next_attempt_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,0,$8,$9,now(),now(),now())`, id, kind, recipient, message, name, language, state, code, detail)
	return err
}

// DeliverOne locks one due row through the bounded provider request. Both
// workers use the same locking protocol, including across backend rollouts.
func (q *DeliveryQueue) DeliverOne(ctx context.Context) (bool, error) {
	tx, err := q.db.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	var id, recipient, body, name, language string
	var attempts int
	err = tx.QueryRowContext(ctx, `SELECT id,recipient,body,template_name,template_language,attempts
		FROM communications_whatsappdelivery WHERE status='queued' AND next_attempt_at<=now()
		ORDER BY next_attempt_at,id LIMIT 1 FOR UPDATE SKIP LOCKED`).Scan(&id, &recipient, &body, &name, &language, &attempts)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	attempts++
	var providerID string
	if name == "" {
		providerID, err = q.provider.SendText(ctx, recipient, body, id)
	} else {
		providerID, err = q.provider.SendTemplate(ctx, recipient, name, language, body, id)
	}
	state, code, detail := "sent", "", ""
	delay := 0
	if err != nil {
		state = "failed"
		var failure *ProviderError
		if !errors.As(err, &failure) {
			return false, err
		}
		code, detail = failure.Code, failure.Message
		if failure.Transient && attempts < 3 {
			state = "queued"
			delay = 30 << (attempts - 1)
		}
		slog.WarnContext(ctx, "WhatsApp send failed", "id", id, "attempt", attempts, "code", code)
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
	ticker := time.NewTicker(10 * time.Second)
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
