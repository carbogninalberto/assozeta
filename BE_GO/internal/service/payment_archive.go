package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
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
var errSecCategoryAssociation = errors.New("payment save defaults: payment association not loaded")

// errPaymentStoredJSON is a stored meta/course value the JSON decoder refuses:
// both columns are jsonb, whose text PostgreSQL always returns as valid JSON,
// so this is application state rather than a value a request can send.
var errPaymentStoredJSON = refusal.AppState("payment save: stored meta or course is not JSON")

// errPaymentStoredJSONUnmeasured is a save hook reached without the view's
// measured JSON edges reading a stored meta/course nested past the reference
// parser's 1000 levels. Every view that saves a Payment sets its edges
// (withPaymentJSONEdges, owner decision X15), so this is a programming error:
// application state, the 500 page.
var errPaymentStoredJSONUnmeasured = refusal.AppState("payment save: stored JSON past 1000 levels on a view without measured edges")

// paymentStoredJSON is paymentJSON for a save hook without measured edges:
// nesting past the reference parser and any other failure are application
// state.
func paymentStoredJSON(text *string) (any, error) {
	value, err := paymentJSON(text)
	switch {
	case errors.Is(err, dto.ErrReferenceNesting):
		return nil, errPaymentStoredJSONUnmeasured
	case err != nil:
		return nil, errPaymentStoredJSON
	}
	return value, nil
}

// paymentStoredJSONAtDepth is the save hook's parse once the view's measured
// edges are known: the load's own recursion edge bounds the parse (the 1000
// level reference limit no longer applies), and anything else it raises is a
// stored jsonb text the decoder refuses, as before.
func paymentStoredJSONAtDepth(text *string, maxDepth int) (any, error) {
	if text == nil {
		return nil, nil
	}
	value, err := dto.ParseStoredJSONDepth(*text, maxDepth)
	if err != nil {
		return nil, errPaymentStoredJSON
	}
	return value, nil
}

// paymentJSONRecursionFailure maps the measured edges to the RecursionError the
// view meets: past Load the view fails before writing anything, deeper than
// Save but within Load it fails after the writes it made before the save.
func paymentJSONRecursionFailure(row *domain.PaymentSaveRow, edges paymentJSONEdges) error {
	switch {
	case row.JSONDepth > edges.Load:
		return &PaymentStoredJSONRecursionError{AfterWrites: false, Text: paymentJSONRecursionTextAt(row, edges.Load)}
	case row.JSONDepth > edges.Save:
		return &PaymentStoredJSONRecursionError{AfterWrites: true, Text: paymentJSONRecursionTextAt(row, edges.Save)}
	}
	return nil
}

// paymentJSONStoredEdges applies the measured edges to a payment row another
// query loaded (the store keeps the columns as text, so the view's own load and
// the audit's refetch of the stored row are not decoded by the save hook): the
// RecursionError they raise, and whether the index resolver's reload of the same
// row fails so the entry takes the resolver's fallbacks.
func paymentJSONStoredEdges(metaPaymentCategories, meta, course *string, edges paymentJSONEdges) (bool, error) {
	row := domain.PaymentSaveRow{MetaPaymentCategories: metaPaymentCategories, Meta: meta, Course: course}
	row.JSONDepth = paymentJSONColumnsDepth(&row)
	if err := paymentJSONRecursionFailure(&row, edges); err != nil {
		return false, err
	}
	return row.JSONDepth > edges.Index, nil
}

func paymentSaveDefaults(ctx context.Context, tx domain.PaymentArchiveTransaction, row *domain.PaymentSaveRow, changes map[string][2]string) error {
	if row.CategoryID == nil && row.AssociationID == "" {
		// The row's association must be loaded: the default is taken from it.
		return errSecCategoryAssociation
	}
	return secCategorySaveDefaults(ctx, tx, row, &row.AssociationID, changes)
}

// secCategorySaveDefaults is paymentSaveDefaults for a payment whose association
// may be NULL (nil: only the global categories).
func secCategorySaveDefaults(ctx context.Context, tx domain.PaymentArchiveTransaction, row *domain.PaymentSaveRow, association *string,
	changes map[string][2]string) error {
	row.JSONDepth = paymentJSONColumnsDepth(row)
	edges, measured := paymentJSONEdgesOf(ctx)
	if measured {
		if err := paymentJSONRecursionFailure(row, edges); err != nil {
			return err
		}
		row.IndexFallback = row.JSONDepth > edges.Index
	}
	if row.CategoryID == nil {
		id, name, err := tx.DefaultPaymentCategory(ctx, association)
		if err != nil {
			return err
		}
		if id != nil {
			changes["payment_category"] = [2]string{"None", *id}
			row.CategoryID, row.CategoryName = id, name
		}
	}
	var meta, course any
	var err error
	if measured {
		if meta, err = paymentStoredJSONAtDepth(row.Meta, edges.Save); err == nil {
			course, err = paymentStoredJSONAtDepth(row.Course, edges.Save)
		}
	} else {
		if meta, err = paymentStoredJSON(row.Meta); err == nil {
			course, err = paymentStoredJSON(row.Course)
		}
	}
	if err != nil {
		return err
	}
	input := domain.PaymentDescriptionInput{Subject: row.Subject, Description: row.Description, CategoryName: row.CategoryName, Meta: meta, Course: course,
		CustomAccounts: row.CustomAccounts}
	if row.Subject == 2 && row.Description == nil {
		linked, err := tx.PaymentLinkedName(ctx, row.ID)
		if err != nil {
			return err
		}
		if linked.Found {
			input.Related[linked.Slot] = &domain.PaymentDescriptionRelation{Title: linked.Title, Meta: meta, MissingCarnet: linked.MissingCarnet}
		}
	}
	description, memory, err := resolvePaymentDescriptionValue(ctx, input, row.DescriptionValue)
	if errors.Is(err, domain.ErrPaymentDescription) {
		return errors.Join(ErrPaymentSaveFailed, err)
	}
	if err != nil {
		return err
	}
	if !samePointer(row.Description, description) {
		changes["description"] = [2]string{pythonOptional(row.Description), pythonOptional(description)}
	}
	row.Description, row.DescriptionValue = description, memory
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

// paymentArchiveJSONEdges are payment_archive's measured edges: the view's own
// load, the smallest load before the UPDATE (get_course_carnet_name, else
// auditlog's refetch of the stored row) and the audit index resolver's reload.
var paymentArchiveJSONEdges = paymentJSONEdges{Load: 9935, Save: 9933, Index: 9925}

// Archive follows payment_archive: is_valid_uuid, is_sport_association,
// the payment of request.user.sport_association (a miss is a 500), toggle
// archived, Payment.save() defaults and the audited save.
func (s *PaymentArchives) Archive(ctx context.Context, identity domain.ResolvedIdentity, raw, groupHeader string, audit domain.LoginAuditContext) (string, error) {
	ctx = withPaymentJSONEdges(ctx, paymentArchiveJSONEdges)
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
		IndexFallback: row.IndexFallback,
		Context:       paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)}
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
