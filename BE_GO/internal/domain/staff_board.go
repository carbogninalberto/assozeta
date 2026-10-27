package domain

import (
	"context"
	"encoding/json"
	"errors"
	"time"
)

// StaffBoardAuthor is the joined author row; nil on StaffBoardMessage means the
// author was deleted (author_id SET_NULL).
type StaffBoardAuthor struct {
	ID, FirstName, LastName, Username string
	AvatarImage                       *string
}

// StaffBoardMessage is one communications_staffboardmessage row with the
// association owner and denomination needed for author_name.
type StaffBoardMessage struct {
	ID, AssociationID                           string
	AssociationOwnerID, AssociationDenomination string
	Author                                      *StaffBoardAuthor
	Content                                     string
	Document                                    json.RawMessage // nil is SQL/JSON null
	Pinned                                      bool
	CreatedAt, UpdatedAt                        time.Time
}

var (
	ErrStaffBoardForbidden = errors.New("staff-board access forbidden")
	ErrStaffBoardNotFound  = errors.New("staff-board message not found")
	// Django's literal author-only 403 details; they are not translated.
	ErrStaffBoardEditNotAuthor   = errors.New("Solo l’autore può modificare questo messaggio.")
	ErrStaffBoardDeleteNotAuthor = errors.New("Solo l’autore può eliminare questo messaggio.")
)

// StaffBoardWrite is one new row; At fills created_at and updated_at.
type StaffBoardWrite struct {
	ID, AssociationID string
	AuthorID          string
	Content           string
	Document          json.RawMessage // nil writes SQL NULL
	Pinned            bool
	At                time.Time
}

// StaffBoardChange carries only validated fields. Unlike Django's full-row
// save(), absent fields are not rewritten; updated_at always advances.
type StaffBoardChange struct {
	Content     *string
	DocumentSet bool
	Document    json.RawMessage
	Pinned      *bool
	At          time.Time
}

type StaffBoardReadStore interface {
	StaffBoardMessages(ctx context.Context, owner string) ([]StaffBoardMessage, error)
}

type StaffBoardStore interface {
	StaffBoardReadStore
	BeginStaffBoardMutations(ctx context.Context) (StaffBoardTransaction, error)
}

type StaffBoardTransaction interface {
	OwnerForUpdate(context.Context, string) (int, bool, bool, error)
	AssociationForOwner(context.Context, string, *string) (string, error)
	StaffBoardMessageForUpdate(ctx context.Context, id, association string) (StaffBoardMessage, error)
	InsertStaffBoardMessage(context.Context, StaffBoardWrite) error
	UpdateStaffBoardMessage(ctx context.Context, id, association string, change StaffBoardChange) error
	DeleteStaffBoardMessage(ctx context.Context, id, association string) error
	// AppendRealtimeEvent writes the invalidation to the transactional outbox;
	// it becomes visible (and is notified) only if the transaction commits.
	AppendRealtimeEvent(ctx context.Context, kind, target string) error
	Commit() error
	Rollback() error
}

// RealtimeStaffBoardChanged is the outbox kind for NotificationConsumer's
// staff_board_changed invalidation; the target is the association ID.
const RealtimeStaffBoardChanged = "staff_board_changed"
