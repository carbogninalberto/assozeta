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

// The update view's form JSON bands (owner decision X15), measured on the parity
// harness's Django (CPython 3.13.16): course and meta_payment_categories texts
// up to 9934 levels validate and save as any shallow value, 9935..9937 keep
// auditlog's pre_save entry and skip the row UPDATE (psycopg2's json.dumps
// raises), and 9938 and deeper raise inside validation with nothing written.
func TestPaymentWritesUpdateFormJSONDepthBands(t *testing.T) {
	const measuredAudit, measuredRefused = 9935, 9938
	if paymentWritesFormJSONAuditDepth != measuredAudit || paymentWritesFormJSONRefusedDepth != measuredRefused {
		t.Fatalf("bands: %d %d, want %d %d", paymentWritesFormJSONAuditDepth, paymentWritesFormJSONRefusedDepth,
			measuredAudit, measuredRefused)
	}
	for _, test := range []struct {
		depth              int
		auditOnly, refused bool
	}{
		{0, false, false},
		{500, false, false},
		{9934, false, false},
		{9935, true, false},
		{9936, true, false},
		{9937, true, false},
		{9938, false, true},
		{9939, false, true},
		{10000, false, true},
	} {
		auditOnly, refused := paymentWritesFormJSONBand(test.depth)
		if auditOnly != test.auditOnly || refused != test.refused {
			t.Fatalf("depth %d: auditOnly=%v refused=%v, want %v %v", test.depth, auditOnly, refused, test.auditOnly, test.refused)
		}
	}
	if !errors.Is(errPaymentWritesFormJSONTooDeep, refusal.ErrAppState) ||
		errors.Is(errPaymentWritesFormJSONTooDeep, refusal.ErrNotPorted) {
		t.Fatalf("%v is not an application-state error", errPaymentWritesFormJSONTooDeep)
	}
}

// At the band edge the deep text validates like a shallow one: another field's
// error is the usual 400. One level past it, json.loads' RecursionError escapes
// that collected error: the 500 page, nothing written.
func TestPaymentWritesUpdateFormJSONDepthValidatesBeforeTheBand(t *testing.T) {
	association := "a1000000-0000-4000-8000-000000988777"
	for _, test := range []struct {
		depth int
		deep  bool
	}{
		{9934, false},
		{9938, true},
	} {
		row := &domain.PaymentWriteRow{ID: "a1000000-0000-4000-8000-000000988450", AssociationID: &association}
		tx := &paymentWritesUpdateTx{association: association, row: row}
		writes := paymentWritesWithStore(&paymentWritesUpdateStore{tx: tx})
		_, err := writes.Update(context.Background(), paymentWritesEntryIdentity(),
			PaymentWritesRequest{ID: row.ID, Form: true},
			domain.RequestObject{{Name: "paid", Value: "maybe"}, {Name: "course", Value: deepFormJSON(test.depth)}})
		if test.deep {
			if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
				t.Fatalf("depth %d: err=%v, want the application-state error", test.depth, err)
			}
		} else if _, invalid := err.(*dto.PaymentEntryValidationError); !invalid {
			t.Fatalf("depth %d: err=%v, want the field validation", test.depth, err)
		}
		if tx.audits != nil || tx.commits != 0 || tx.rollbacks != 1 {
			t.Fatalf("depth %d: audits=%d commits=%d rollbacks=%d", test.depth, len(tx.audits), tx.commits, tx.rollbacks)
		}
	}
}

// A course in the audit band parses and validates, so auditlog's pre_save
// update entry is written; the jsonb write then raises (psycopg2's json.dumps at
// that depth): the row UPDATE is skipped, the entry stays (the view is not
// atomic) and the answer is the 500 page.
func TestPaymentWritesUpdateFormJSONAuditBandKeepsAudit(t *testing.T) {
	for _, depth := range []int{9935, 9936, 9937} {
		association := "a1000000-0000-4000-8000-000000988777"
		row := &domain.PaymentWriteRow{ID: "a1000000-0000-4000-8000-000000988450", AssociationID: &association,
			Type: "default", Subject: 0, Amount: "10.00"}
		tx := &paymentWritesUpdateTx{association: association, row: row, before: map[string]string{
			"type": "default", "subject": "0", "amount": "10.00", "course": "null", "meta_payment_categories": "null"}}
		writes := paymentWritesWithStore(&paymentWritesUpdateStore{tx: tx})
		_, err := writes.Update(context.Background(), paymentWritesEntryIdentity(),
			PaymentWritesRequest{ID: row.ID, Form: true},
			domain.RequestObject{{Name: "course", Value: deepFormJSON(depth)}})
		if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
			t.Fatalf("depth %d: err=%v, want the application-state error", depth, err)
		}
		if tx.commits != 1 || tx.rollbacks != 1 {
			t.Fatalf("depth %d: commits=%d rollbacks=%d", depth, tx.commits, tx.rollbacks)
		}
		if len(tx.audits) != 1 || tx.audits[0].Action != 1 || tx.audits[0].PaymentID != row.ID {
			t.Fatalf("depth %d: audits=%+v", depth, tx.audits)
		}
		changes := tx.audits[0].Changes
		if len(changes) != 1 || changes["course"] != [2]string{"null", deepFormJSON(depth)} {
			t.Fatalf("depth %d: changes=%v", depth, changes)
		}
		if row.Course != nil {
			t.Fatalf("depth %d: the UPDATE ran: course=%v", depth, row.Course)
		}
	}
}

// The other JSON field follows the same measured band: a meta_payment_categories
// document in the audit band validates, the save hook sums its amounts and the
// pre_save entry is written before the jsonb write raises.
func TestPaymentWritesUpdateFormJSONAuditBandMetaKeepsAudit(t *testing.T) {
	meta := `[{"amount": 0.1, "deep": ` + strings.Repeat(`{"a": `, 9932) + "{}" + strings.Repeat("}", 9932) + "}]"
	if depth := dto.AuditJSONDepth(meta); depth != paymentWritesFormJSONAuditDepth {
		t.Fatalf("authored depth %d, want %d", depth, paymentWritesFormJSONAuditDepth)
	}
	association := "a1000000-0000-4000-8000-000000988777"
	row := &domain.PaymentWriteRow{ID: "a1000000-0000-4000-8000-000000988450", AssociationID: &association,
		Type: "default", Subject: 0, Amount: "20.00"}
	tx := &paymentWritesUpdateTx{association: association, row: row, before: map[string]string{
		"type": "default", "subject": "0", "amount": "20.00", "course": "null", "meta_payment_categories": "null"}}
	writes := paymentWritesWithStore(&paymentWritesUpdateStore{tx: tx})
	_, err := writes.Update(context.Background(), paymentWritesEntryIdentity(),
		PaymentWritesRequest{ID: row.ID, Form: true},
		domain.RequestObject{{Name: "amount", Value: "20"}, {Name: "meta_payment_categories", Value: meta}})
	if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("err=%v, want the application-state error", err)
	}
	if tx.commits != 1 || tx.rollbacks != 1 || len(tx.audits) != 1 {
		t.Fatalf("commits=%d rollbacks=%d audits=%+v", tx.commits, tx.rollbacks, tx.audits)
	}
	if changes := tx.audits[0].Changes; changes["amount"] != [2]string{"20.00", "20.1"} ||
		changes["meta_payment_categories"] != [2]string{"null", meta} {
		t.Fatalf("changes=%v", changes)
	}
	if row.MetaCategories != nil {
		t.Fatalf("the UPDATE ran: meta=%v", *row.MetaCategories)
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
	if errors.Is(err, refusal.ErrNotPorted) || errors.Is(err, refusal.ErrAppState) || tx.audits != nil || tx.commits != 0 ||
		tx.rollbacks != 1 {
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
	if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("err=%v, want the application-state error", err)
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
