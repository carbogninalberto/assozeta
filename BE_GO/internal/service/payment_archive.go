package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

var (
	// ErrPaymentNotAllowed is {'msg': 'User not allowed.'} for non-association users.
	ErrPaymentNotAllowed = errors.New("payment user not allowed")
	// ErrPaymentSaveFailed is a Payment.save() attribute/type error (a 500).
	ErrPaymentSaveFailed = errors.New("payment save failed")
)

type PaymentArchives struct {
	store domain.PaymentArchiveStore
	now   func() time.Time
}

func NewPaymentArchives(store domain.PaymentArchiveStore, now func() time.Time) *PaymentArchives {
	return &PaymentArchives{store: store, now: now}
}

func paymentJSON(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	return dto.ParseReferenceJSON([]byte(*text))
}

// paymentSaveDefaults is Payment.save()'s default category, then the shared
// ResolvePaymentDescription port of its description hook.
func paymentSaveDefaults(ctx context.Context, tx domain.PaymentArchiveTransaction, row *domain.PaymentSaveRow, changes map[string][2]string) error {
	if row.CategoryID == nil {
		id, name, err := tx.DefaultPaymentCategory(ctx)
		if err != nil {
			return err
		}
		if id != nil {
			changes["payment_category"] = [2]string{"None", *id}
			row.CategoryID, row.CategoryName = id, name
		}
	}
	meta, err := paymentJSON(row.Meta)
	if err != nil {
		return ErrPaymentSaveFailed
	}
	course, err := paymentJSON(row.Course)
	if err != nil {
		return ErrPaymentSaveFailed
	}
	input := domain.PaymentDescriptionInput{Subject: row.Subject, Description: row.Description, CategoryName: row.CategoryName, Meta: meta, Course: course}
	if row.Subject == 2 && row.Description == nil {
		linked, err := tx.PaymentLinkedName(ctx, row.ID)
		if err != nil {
			return err
		}
		if linked.Found {
			input.Related[linked.Slot] = &domain.PaymentDescriptionRelation{Title: linked.Title, Meta: meta, MissingCarnet: linked.MissingCarnet}
		}
	}
	description, err := ResolvePaymentDescription(ctx, input)
	if errors.Is(err, domain.ErrPaymentDescription) {
		return ErrPaymentSaveFailed
	}
	if err != nil {
		return err
	}
	if !samePointer(row.Description, description) {
		changes["description"] = [2]string{pythonOptional(row.Description), pythonOptional(description)}
	}
	row.Description = description
	return nil
}

func samePointer(a, b *string) bool {
	return (a == nil && b == nil) || (a != nil && b != nil && *a == *b)
}

func pythonOptional(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

// Archive follows payment_archive: is_valid_uuid, is_sport_association,
// the payment of request.user.sport_association (a miss is a 500), toggle
// archived, Payment.save() defaults and the audited save.
func (s *PaymentArchives) Archive(ctx context.Context, identity domain.ResolvedIdentity, raw, groupHeader string, audit domain.LoginAuditContext) (string, error) {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return "", err
	}
	user := identity.ScopeUser
	if user.Role != domain.RoleAssociation && user.Role != domain.RoleCollaborator {
		return "", ErrPaymentNotAllowed
	}
	tx, err := s.store.BeginPaymentArchive(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	owner := user.ID
	if user.Role == domain.RoleCollaborator && user.ConnectedUserID != nil {
		owner = *user.ConnectedUserID
	}
	association, err := tx.OwnerAssociation(ctx, owner)
	if err != nil {
		return "", err
	}
	row, err := tx.PaymentForArchive(ctx, id, association, supplierGroup(groupHeader))
	if err != nil {
		return "", err
	}
	changes := map[string][2]string{}
	archived := !row.Archived
	changes["archived"] = [2]string{pythonBool(row.Archived), pythonBool(archived)}
	if err = paymentSaveDefaults(ctx, tx, &row, changes); err != nil {
		return "", err
	}
	if err = tx.SavePaymentArchive(ctx, row.ID, archived, row.CategoryID, row.Description); err != nil {
		return "", err
	}
	event := domain.PaymentArchiveEvent{PaymentID: row.ID, AssociationID: row.AssociationID, Changes: changes,
		Context: paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)}
	if err = tx.AppendPaymentAudit(ctx, event); err != nil {
		return "", err
	}
	if err = tx.Commit(); err != nil {
		return "", err
	}
	if archived {
		return "Payment archived.", nil
	}
	return "Payment unarchived.", nil
}

func pythonBool(value bool) string {
	if value {
		return "True"
	}
	return "False"
}
