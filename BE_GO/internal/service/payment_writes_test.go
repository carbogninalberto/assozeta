package service

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
)

// paymentWritesDocuments is a writer whose document storage has no stores
// behind it: attachmentsStored only validates the storage name.
func paymentWritesDocuments() *PaymentWrites { return paymentWritesWithStore(nil) }

func paymentWritesWithStore(store domain.PaymentWritesStore) *PaymentWrites {
	now := func() time.Time { return time.Date(2026, 11, 10, 19, 45, 3, 123456000, time.UTC) }
	ids := func() string { return "a1000000-0000-4000-8000-000000988999" }
	uploads := NewDocumentUploads(nil, nil, nil, false, now, ids)
	return NewPaymentWrites(store, now, ids).WithDocuments(uploads)
}

func paymentWritesAttachment(filename any) domain.RequestObject {
	return domain.RequestObject{{Name: "document", Value: "UmVnb2xhbWVudG8="}, {Name: "filename", Value: filename}}
}

// A storage name Storage.save rejects is planned as a Document row only, and
// the walk reports the SuspiciousFileOperation: Django answers its 400 page
// after the INSERT.
func TestPaymentWritesAttachmentsTraversalNameIsSuspicious(t *testing.T) {
	writes := paymentWritesDocuments()
	plan, err := writes.attachmentsStored(dto.PaymentEntryInput{Attachments: []domain.RequestObject{
		paymentWritesAttachment("../x.pdf"),
	}})
	if err != nil {
		t.Fatalf("refused: %v", err)
	}
	if len(plan.stored) != 1 {
		t.Fatalf("plan=%+v", plan)
	}
	var suspicious *documentkey.SuspiciousFileError
	if !errors.As(plan.stored[0].suspicious, &suspicious) {
		t.Fatalf("attachment=%+v", plan.stored[0])
	}
	if plan.stored[0].filename != "../x.pdf" || plan.stored[0].content != nil || plan.stored[0].invalid || plan.stored[0].unlinked {
		t.Fatalf("attachment=%+v", plan.stored[0])
	}
}

// A valid name keeps its decoded bytes; the items before a failing name are
// unaffected by it.
func TestPaymentWritesAttachmentsValidThenTraversal(t *testing.T) {
	writes := paymentWritesDocuments()
	plan, err := writes.attachmentsStored(dto.PaymentEntryInput{Attachments: []domain.RequestObject{
		paymentWritesAttachment("ricevuta.pdf"),
		paymentWritesAttachment("..\\x.pdf"),
		paymentWritesAttachment("dopo.pdf"),
	}})
	if err != nil {
		t.Fatalf("refused: %v", err)
	}
	if len(plan.stored) != 2 || plan.stored[0].filename != "ricevuta.pdf" || string(plan.stored[0].content) != "Regolamento" {
		t.Fatalf("plan=%+v", plan)
	}
	var suspicious *documentkey.SuspiciousFileError
	if !errors.As(plan.stored[1].suspicious, &suspicious) || plan.stored[1].filename != "..\\x.pdf" {
		t.Fatalf("attachment=%+v", plan.stored[1])
	}
}

// A name that is not text is stored as str() and os.path.join raises: the 500
// page with the row kept.
func TestPaymentWritesAttachmentsNonTextNameKeepsRow(t *testing.T) {
	writes := paymentWritesDocuments()
	plan, err := writes.attachmentsStored(dto.PaymentEntryInput{Attachments: []domain.RequestObject{
		paymentWritesAttachment(dto.JSONInteger(12)),
	}})
	if err != nil {
		t.Fatalf("refused: %v", err)
	}
	if len(plan.stored) != 1 || !plan.stored[0].unlinked || plan.stored[0].filename != "12" || plan.stored[0].suspicious != nil {
		t.Fatalf("plan=%+v", plan)
	}
}

// paymentWritesEntryTx is the transaction entry() walks: the payment_id
// UniqueValidator lookup and the tenant scope.
type paymentWritesEntryTx struct {
	domain.PaymentWritesTransaction
	visible bool
	group   *string
	lookups int
}

func (t *paymentWritesEntryTx) TenantAssociation(context.Context, string) (*string, error) {
	return nil, nil
}

func (t *paymentWritesEntryTx) PaymentIDVisible(_ context.Context, _ string, group *string) (bool, error) {
	t.group = group
	t.lookups++
	return t.visible, nil
}

func paymentWritesEntryIdentity() domain.ResolvedIdentity {
	return domain.ResolvedIdentity{Actor: &domain.Account{ID: "actor"}, ScopeUser: &domain.Account{ID: "user"}}
}

func paymentWritesEntryCodes(invalid *dto.PaymentEntryValidationError) map[string][]string {
	codes := map[string][]string{}
	for _, field := range invalid.Fields {
		codes[field.Name] = field.Codes
	}
	return codes
}

// The add body's payment_id runs the serializer's UniqueValidator on the
// live, group-aware manager: a visible key is DRF's unique error.
func TestPaymentWritesEntryPaymentIDTaken(t *testing.T) {
	tx := &paymentWritesEntryTx{visible: true}
	body := domain.RequestObject{{Name: "payment_id", Value: "a1000000-0000-4000-8000-000000988450"},
		{Name: "creation_date", Value: "2026-03-04"}}
	_, err := paymentWritesDocuments().entry(context.Background(), tx, paymentWritesEntryIdentity(), body, false, "")
	invalid, ok := err.(*dto.PaymentEntryValidationError)
	if !ok {
		t.Fatalf("err=%v", err)
	}
	codes := paymentWritesEntryCodes(invalid)
	if len(codes["payment_id"]) != 1 || codes["payment_id"][0] != "unique:payment:payment id" {
		t.Fatalf("codes=%v", codes)
	}
	if tx.lookups != 1 || tx.group != nil {
		t.Fatalf("lookups=%d group=%v", tx.lookups, tx.group)
	}
}

// Without a payment_id the validator never runs; a key of the request's group
// is looked up with that group.
func TestPaymentWritesEntryPaymentIDGroup(t *testing.T) {
	tx := &paymentWritesEntryTx{}
	body := domain.RequestObject{{Name: "creation_date", Value: "2026-03-04"}}
	if _, err := paymentWritesDocuments().entry(context.Background(), tx, paymentWritesEntryIdentity(), body, false, ""); err != nil {
		t.Fatalf("err=%v", err)
	}
	if tx.lookups != 0 {
		t.Fatalf("lookups=%d", tx.lookups)
	}
	body = append(body, domain.RequestField{Name: "payment_id", Value: "a1000000-0000-4000-8000-000000988450"})
	if _, err := paymentWritesDocuments().entry(context.Background(), tx, paymentWritesEntryIdentity(), body, false,
		"A1000000-0000-4000-8000-000000988450"); err != nil {
		t.Fatalf("err=%v", err)
	}
	if tx.lookups != 1 || tx.group == nil || *tx.group != "a1000000-0000-4000-8000-000000988450" {
		t.Fatalf("lookups=%d group=%v", tx.lookups, tx.group)
	}
}

// An X-Group-Id the UUIDField rejects makes the manager's own group filter
// raise: the error lands on payment_id instead of a lookup.
func TestPaymentWritesEntryPaymentIDInvalidGroup(t *testing.T) {
	tx := &paymentWritesEntryTx{}
	body := domain.RequestObject{{Name: "creation_date", Value: "2026-03-04"},
		{Name: "payment_id", Value: "a1000000-0000-4000-8000-000000988450"}}
	_, err := paymentWritesDocuments().entry(context.Background(), tx, paymentWritesEntryIdentity(), body, false, "abc")
	invalid, ok := err.(*dto.PaymentEntryValidationError)
	if !ok {
		t.Fatalf("err=%v", err)
	}
	codes := paymentWritesEntryCodes(invalid)
	if len(codes["payment_id"]) != 1 || codes["payment_id"][0] != "unique-group-invalid:abc" || tx.lookups != 0 {
		t.Fatalf("codes=%v lookups=%d", codes, tx.lookups)
	}
}

// An invalid payment_id is a field error, so the validator does not run.
func TestPaymentWritesEntryPaymentIDInvalid(t *testing.T) {
	tx := &paymentWritesEntryTx{visible: true}
	body := domain.RequestObject{{Name: "creation_date", Value: "2026-03-04"}, {Name: "payment_id", Value: "abc"}}
	_, err := paymentWritesDocuments().entry(context.Background(), tx, paymentWritesEntryIdentity(), body, false, "")
	invalid, ok := err.(*dto.PaymentEntryValidationError)
	if !ok {
		t.Fatalf("err=%v", err)
	}
	if codes := paymentWritesEntryCodes(invalid); codes["payment_id"][0] != dto.PaymentCategoryValidationUUID || tx.lookups != 0 {
		t.Fatalf("codes=%v lookups=%d", codes, tx.lookups)
	}
}

// paymentWritesUpdateStore is the transaction Update opens.
type paymentWritesUpdateStore struct {
	domain.PaymentWritesStore
	tx *paymentWritesUpdateTx
}

func (s *paymentWritesUpdateStore) BeginPaymentWrites(context.Context) (domain.PaymentWritesTransaction, error) {
	return s.tx, nil
}

// paymentWritesUpdateTx is the transaction Update opens and walks. The fields
// the tests set answer the reads the ported path makes; the audit values are
// the stored row.
type paymentWritesUpdateTx struct {
	domain.PaymentWritesTransaction
	association string
	row         *domain.PaymentWriteRow
	before      map[string]string
	audits      []domain.PaymentWritesAuditEntry
	commits     int
	rollbacks   int
}

func (t *paymentWritesUpdateTx) ScopedAssociation(context.Context, string, *string) (string, error) {
	return t.association, nil
}

func (t *paymentWritesUpdateTx) LockPayment(context.Context, string, *string, *string) (*domain.PaymentWriteRow, error) {
	return t.row, nil
}

func (t *paymentWritesUpdateTx) TenantAssociation(context.Context, string) (*string, error) {
	return nil, nil
}

func (t *paymentWritesUpdateTx) DefaultPaymentCategory(context.Context, *string) (*string, *string, error) {
	return nil, nil, nil
}

func (t *paymentWritesUpdateTx) PaymentAuditValues(context.Context, string) (map[string]string, error) {
	values := map[string]string{}
	for field, value := range t.before {
		values[field] = value
	}
	return values, nil
}

func (t *paymentWritesUpdateTx) AppendPaymentWritesAudit(_ context.Context, entry domain.PaymentWritesAuditEntry) error {
	t.audits = append(t.audits, entry)
	return nil
}

func (t *paymentWritesUpdateTx) Commit() error {
	t.commits++
	return nil
}

func (t *paymentWritesUpdateTx) Rollback() error {
	t.rollbacks++
	return nil
}

// A form body whose course or meta_payment_categories JSON text nests past the
// ported depth stays the handoff refusal before validation and before any
// write: the depth at which Python's JSON codec raises depends on the live call
// stack.
func TestPaymentWritesUpdateRefusesFormJSONTooDeep(t *testing.T) {
	association := "a1000000-0000-4000-8000-000000988777"
	row := &domain.PaymentWriteRow{ID: "a1000000-0000-4000-8000-000000988450", AssociationID: &association}
	tx := &paymentWritesUpdateTx{association: association, row: row}
	writes := paymentWritesWithStore(&paymentWritesUpdateStore{tx: tx})
	_, err := writes.Update(context.Background(), paymentWritesEntryIdentity(),
		PaymentWritesRequest{ID: row.ID, Form: true},
		domain.RequestObject{{Name: "paid", Value: "maybe"}, {Name: "course", Value: strings.Repeat("[", 502) + strings.Repeat("]", 502)}})
	if !errors.Is(err, domain.ErrPaymentWritesNotPorted) {
		t.Fatalf("depth: err=%v", err)
	}
	if errors.Is(err, refusal.ErrAppState) || tx.rollbacks != 1 {
		t.Fatalf("depth: err=%v rollbacks=%d", err, tx.rollbacks)
	}
}

// A lone surrogate escape is not refused: Django validates every field first,
// so another field's error is the usual 400.
func TestPaymentWritesUpdateFormJSONSurrogateValidatesFirst(t *testing.T) {
	association := "a1000000-0000-4000-8000-000000988777"
	row := &domain.PaymentWriteRow{ID: "a1000000-0000-4000-8000-000000988450", AssociationID: &association}
	tx := &paymentWritesUpdateTx{association: association, row: row}
	writes := paymentWritesWithStore(&paymentWritesUpdateStore{tx: tx})
	_, err := writes.Update(context.Background(), paymentWritesEntryIdentity(),
		PaymentWritesRequest{ID: row.ID, Form: true},
		domain.RequestObject{{Name: "paid", Value: "maybe"}, {Name: "course", Value: `"\ud800"`}})
	if _, invalid := err.(*dto.PaymentEntryValidationError); !invalid {
		t.Fatalf("err=%v, want the field validation", err)
	}
	if errors.Is(err, domain.ErrPaymentWritesNotPorted) || tx.audits != nil || tx.commits != 0 || tx.rollbacks != 1 {
		t.Fatalf("err=%v audits=%d commits=%d rollbacks=%d", err, len(tx.audits), tx.commits, tx.rollbacks)
	}
}

// A form JSON value the parser keeps with a lone surrogate escape parses and
// validates, so auditlog's pre_save update entry is written; the jsonb column
// then refuses the value: the row UPDATE is skipped, the entry stays (the view
// is not atomic) and the answer is the 500 page.
func TestPaymentWritesUpdateFormJSONSurrogateKeepsAudit(t *testing.T) {
	association := "a1000000-0000-4000-8000-000000988777"
	subject, amount := 0, "20.00"
	row := &domain.PaymentWriteRow{ID: "a1000000-0000-4000-8000-000000988450", AssociationID: &association,
		Subject: subject, Amount: amount, CreationDate: time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC)}
	tx := &paymentWritesUpdateTx{association: association, row: row, before: map[string]string{
		"subject": "0", "amount": "20.00", "meta_payment_categories": "null", "course": "null"}}
	writes := paymentWritesWithStore(&paymentWritesUpdateStore{tx: tx})
	_, err := writes.Update(context.Background(), paymentWritesEntryIdentity(),
		PaymentWritesRequest{ID: row.ID, Form: true},
		domain.RequestObject{{Name: "amount", Value: "20"},
			{Name: "meta_payment_categories", Value: `[{"amount": 0.1, "title": "\ud800"}]`}})
	if !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("err=%v, want the application-state error", err)
	}
	if errors.Is(err, domain.ErrPaymentWritesNotPorted) {
		t.Fatal("the jsonb refusal must never be handed off")
	}
	if tx.commits != 1 || tx.rollbacks != 1 {
		t.Fatalf("commits=%d rollbacks=%d", tx.commits, tx.rollbacks)
	}
	if len(tx.audits) != 1 || tx.audits[0].Action != 1 || tx.audits[0].PaymentID != row.ID {
		t.Fatalf("audits=%+v", tx.audits)
	}
	changes := tx.audits[0].Changes
	if len(changes) != 2 || changes["amount"] != [2]string{"20.00", "20.1"} ||
		changes["meta_payment_categories"] != [2]string{"null", `[{"amount": 0.1, "title": "\ud800"}]`} {
		t.Fatalf("changes=%v", changes)
	}
	if row.MetaCategories != nil {
		t.Fatalf("the UPDATE ran: meta=%v", row.MetaCategories)
	}
}
