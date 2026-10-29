package service

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// StaffBoard owns staff-board authorization, authorship, transactions and the
// transactional invalidation event. It writes no audit rows: Django does not
// register StaffBoardMessage with auditlog.
type StaffBoard struct {
	store domain.StaffBoardStore
	now   func() time.Time
}

func NewStaffBoard(store domain.StaffBoardStore, now func() time.Time) *StaffBoard {
	return &StaffBoard{store: store, now: now}
}

// Admit precedes OPTIONS/method dispatch, like the other association views.
func (s *StaffBoard) Admit(ctx context.Context, identity domain.ResolvedIdentity) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	_, err := staffBoardReader(identity)
	return err
}

func staffBoardReader(identity domain.ResolvedIdentity) (string, error) {
	owner, err := messageReadsOwner(identity)
	if err != nil {
		return "", domain.ErrStaffBoardForbidden
	}
	return owner, nil
}

// staffBoardActor is message_actor: the acting collaborator, else request.user.
func staffBoardActor(identity domain.ResolvedIdentity) string {
	if identity.Collaborator && identity.Effective != nil {
		return identity.Effective.ID
	}
	return identity.ScopeUser.ID
}

func (s *StaffBoard) List(ctx context.Context, identity domain.ResolvedIdentity) ([]dto.StaffBoardMessageRow, error) {
	owner, err := staffBoardReader(identity)
	if err != nil {
		return nil, err
	}
	messages, err := s.store.StaffBoardMessages(ctx, owner)
	if err != nil {
		return nil, err
	}
	actor := staffBoardActor(identity)
	rows := make([]dto.StaffBoardMessageRow, 0, len(messages))
	for _, message := range messages {
		rows = append(rows, dto.StaffBoardMessageRowFrom(message, actor))
	}
	return rows, nil
}

func staffBoardEncode(input dto.StaffBoardMessageInput) json.RawMessage {
	if !input.DocumentSet || input.Document == nil {
		return nil
	}
	return dto.PythonJSONDumps(input.Document)
}

func (s *StaffBoard) begin(ctx context.Context, identity domain.ResolvedIdentity) (domain.StaffBoardTransaction, string, error) {
	owner, err := paymentCategoryOwner(identity, domain.ErrStaffBoardForbidden)
	if err != nil {
		return nil, "", err
	}
	tx, err := s.store.BeginStaffBoardMutations(ctx)
	if err != nil {
		return nil, "", err
	}
	role, active, deleted, err := tx.OwnerForUpdate(ctx, owner)
	if err == nil && (role != domain.RoleAssociation || !active || deleted) {
		err = domain.ErrStaffBoardForbidden
	}
	var association string
	if err == nil {
		association, err = tx.AssociationForOwner(ctx, owner, paymentCategoryAssociationScope(identity))
	}
	if err != nil {
		_ = tx.Rollback()
		return nil, "", err
	}
	return tx, association, nil
}

// finish appends the invalidation to the outbox inside the write transaction,
// then commits; the event exists exactly when the write does.
func (s *StaffBoard) finish(ctx context.Context, tx domain.StaffBoardTransaction, association string) error {
	if err := tx.AppendRealtimeEvent(ctx, domain.RealtimeStaffBoardChanged, association); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *StaffBoard) reload(ctx context.Context, tx domain.StaffBoardTransaction, id, association string) (domain.StaffBoardMessage, error) {
	return tx.StaffBoardMessageForUpdate(ctx, id, association)
}

// Add validates the transport-decoded body before any association work, as
// the view validates the serializer before creating the row.
func (s *StaffBoard) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool) (dto.StaffBoardMessageRow, error) {
	if err := ctx.Err(); err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	if _, err := paymentCategoryOwner(identity, domain.ErrStaffBoardForbidden); err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	input, err := dto.ParseStaffBoardMessageInput(body, form, false)
	if err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	defer tx.Rollback()
	pinned := input.Pinned != nil && *input.Pinned
	actor := staffBoardActor(identity)
	row := domain.StaffBoardWrite{ID: uuid.NewString(), AssociationID: association, AuthorID: actor,
		Content: *input.Content, Document: staffBoardEncode(input), Pinned: pinned, At: s.now().UTC().Truncate(time.Microsecond)}
	if err = tx.InsertStaffBoardMessage(ctx, row); err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	message, err := s.reload(ctx, tx, row.ID, association)
	if err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	if err = s.finish(ctx, tx, association); err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	return dto.StaffBoardMessageRowFrom(message, actor), nil
}

// Update applies Django's order: UUID validation, 404 lookup, the author check
// when the raw body names content or document, then serializer validation.
func (s *StaffBoard) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool) (dto.StaffBoardMessageRow, error) {
	if err := ctx.Err(); err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	defer tx.Rollback()
	current, err := tx.StaffBoardMessageForUpdate(ctx, id, association)
	if err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	actor := staffBoardActor(identity)
	editsText, err := staffBoardNamesText(body)
	if err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	if editsText && (current.Author == nil || current.Author.ID != actor) {
		return dto.StaffBoardMessageRow{}, domain.ErrStaffBoardEditNotAuthor
	}
	input, err := dto.ParseStaffBoardMessageInput(body, form, true)
	if err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	change := domain.StaffBoardChange{Content: input.Content, DocumentSet: input.DocumentSet,
		Document: staffBoardEncode(input), Pinned: input.Pinned, At: s.now().UTC().Truncate(time.Microsecond)}
	if err = tx.UpdateStaffBoardMessage(ctx, id, association, change); err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	message, err := s.reload(ctx, tx, id, association)
	if err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	if err = s.finish(ctx, tx, association); err != nil {
		return dto.StaffBoardMessageRow{}, err
	}
	return dto.StaffBoardMessageRowFrom(message, actor), nil
}

func (s *StaffBoard) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	current, err := tx.StaffBoardMessageForUpdate(ctx, id, association)
	if err != nil {
		return err
	}
	if current.Author == nil || current.Author.ID != staffBoardActor(identity) {
		return domain.ErrStaffBoardDeleteNotAuthor
	}
	if err = tx.DeleteStaffBoardMessage(ctx, id, association); err != nil {
		return err
	}
	return s.finish(ctx, tx, association)
}

// staffBoardNamesText evaluates Python's `'content' in request.data or
// 'document' in request.data`: key membership for objects and forms, element
// equality for lists, substring for strings, and TypeError otherwise.
func staffBoardNamesText(body any) (bool, error) {
	switch value := body.(type) {
	case domain.RequestObject:
		_, content := value.Lookup("content")
		_, document := value.Lookup("document")
		return content || document, nil
	case []any:
		for _, item := range value {
			if item == "content" || item == "document" {
				return true, nil
			}
		}
		return false, nil
	case string:
		return strings.Contains(value, "content") || strings.Contains(value, "document"), nil
	}
	return false, ErrStaffBoardUnsupportedBody
}

// ErrStaffBoardUnsupportedBody mirrors Django's uncaught TypeError (HTTP 500).
var ErrStaffBoardUnsupportedBody = errors.New("staff-board update body does not support membership")
