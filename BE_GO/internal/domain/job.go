package domain

import (
	"encoding/json"
	"errors"
	"time"

	"github.com/google/uuid"
)

var (
	ErrJobInvalid   = errors.New("invalid durable job input")
	ErrJobConflict  = errors.New("durable job conflicts with existing intent or state")
	ErrJobNotFound  = errors.New("durable job not found in association scope")
	ErrJobLeaseLost = errors.New("durable job lease expired or replaced")
)

// AssociationID is a trusted service scope. Nil is reserved for instance jobs;
// it must never be substituted for a missing authenticated association.
type JobIntent struct {
	ID            uuid.UUID
	AssociationID uuid.UUID
	Kind          string
	Version       int
	Key           string
	Payload       json.RawMessage
	AvailableAt   time.Time
	MaxAttempts   int
}

type Job struct {
	ID            uuid.UUID        `db:"id"`
	AssociationID uuid.UUID        `db:"association_id"`
	Kind          string           `db:"kind"`
	Version       int              `db:"version"`
	Key           string           `db:"idempotency_key"`
	Payload       json.RawMessage  `db:"payload"`
	State         string           `db:"state"`
	Attempts      int              `db:"attempts"`
	MaxAttempts   int              `db:"max_attempts"`
	AvailableAt   time.Time        `db:"available_at"`
	CreatedAt     time.Time        `db:"created_at"`
	FinishedAt    *time.Time       `db:"finished_at"`
	LeaseOwner    string           `db:"lease_owner"`
	LeaseToken    uuid.UUID        `db:"lease_token"`
	LeaseUntil    *time.Time       `db:"lease_until"`
	ErrorCode     string           `db:"error_code"`
	Result        *json.RawMessage `db:"result"`
}
