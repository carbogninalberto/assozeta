package service

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

var errPaymentCategoryTest = errors.New("payment category fixture failure")

const (
	paymentCategoryTestAssociation = "00000000-0000-4000-8000-000000000301"
	paymentCategoryTestCategory    = "00000000-0000-4000-8000-000000000401"
	paymentCategoryTestVAT         = "00000000-0000-4000-8000-000000000402"
)

type paymentCategoryStoreTest struct {
	tx         paymentCategoryTransactionTest
	beginErr   error
	begins     int
	listErr    error
	listedAsso string
	listed     []domain.PaymentCategoryState
}

func (s *paymentCategoryStoreTest) BeginPaymentCategories(context.Context) (domain.PaymentCategoryTransaction, error) {
	s.begins++
	return &s.tx, s.beginErr
}
func (s *paymentCategoryStoreTest) ListPaymentCategories(_ context.Context, association string, _ *string) ([]domain.PaymentCategoryState, error) {
	s.listedAsso = association
	return s.listed, s.listErr
}

type paymentCategoryTransactionTest struct {
	association, categoryAssociation string
	associationErr, lockErr          error
	category                         domain.PaymentCategoryState
	vat                              *domain.PaymentCategoryVAT
	vatErr                           error
	createVAT                        bool
	categoryExists                   bool
	associationExists                bool
	inserted, updated                bool
	audits                           []domain.PaymentCategoryAudit
	fail                             string
	committed                        bool
	commits, rollbacks               int
}

func (t *paymentCategoryTransactionTest) AssociationForOwner(_ context.Context, owner string, _ *string) (string, error) {
	if t.fail == "associations" {
		return "", errPaymentCategoryTest
	}
	if owner == "" || t.association == "" {
		return "", domain.ErrAssociationNotFound
	}
	return t.association, t.associationErr
}
func (t *paymentCategoryTransactionTest) AssociationForActor(_ context.Context, actor string) (string, error) {
	if actor == paymentCategoryIdentity().Actor.ID {
		return t.association, t.associationErr
	}
	return "", domain.ErrAssociationNotFound
}
func (t *paymentCategoryTransactionTest) OwnerForUpdate(_ context.Context, owner string) (int, bool, bool, error) {
	if t.fail == "owner" {
		return 0, false, false, errPaymentCategoryTest
	}
	return domain.RoleAssociation, true, false, nil
}
func (t *paymentCategoryTransactionTest) AssociationExists(context.Context, string) (bool, error) {
	return t.associationExists, nil
}
func (t *paymentCategoryTransactionTest) PaymentCategoryIDExists(context.Context, string) (bool, error) {
	return t.categoryExists, nil
}
func (t *paymentCategoryTransactionTest) CategoryForUpdate(_ context.Context, id, association string) (domain.PaymentCategoryState, error) {
	t.categoryAssociation = association
	if t.fail == "read" {
		return domain.PaymentCategoryState{}, errPaymentCategoryTest
	}
	if id != t.category.ID {
		return domain.PaymentCategoryState{}, domain.ErrPaymentCategoryNotFound
	}
	return t.category, t.lockErr
}
func (t *paymentCategoryTransactionTest) VATByID(context.Context, string) (*domain.PaymentCategoryVAT, error) {
	if t.fail == "vat-read" {
		return nil, errPaymentCategoryTest
	}
	return t.vat, t.vatErr
}
func (t *paymentCategoryTransactionTest) CreateVAT(_ context.Context, value domain.PaymentCategoryVAT) error {
	if t.fail == "vat-write" {
		return errPaymentCategoryTest
	}
	t.vat = &value
	return nil
}
func (t *paymentCategoryTransactionTest) InsertPaymentCategory(_ context.Context, value domain.PaymentCategoryState) error {
	if t.fail == "write" {
		return errPaymentCategoryTest
	}
	t.category = value
	t.inserted = true
	return nil
}
func (t *paymentCategoryTransactionTest) UpdatePaymentCategory(_ context.Context, value domain.PaymentCategoryState) error {
	if t.fail == "write" {
		return errPaymentCategoryTest
	}
	t.category = value
	t.updated = true
	return nil
}
func (t *paymentCategoryTransactionTest) AppendPaymentCategoryAudit(_ context.Context, event domain.PaymentCategoryAudit) error {
	if t.fail == "audit" {
		return errPaymentCategoryTest
	}
	t.audits = append(t.audits, event)
	return nil
}
func (t *paymentCategoryTransactionTest) Commit() error {
	t.commits++
	if t.fail == "commit" {
		return errPaymentCategoryTest
	}
	t.committed = true
	return nil
}
func (t *paymentCategoryTransactionTest) Rollback() error {
	t.rollbacks++
	return nil
}

func paymentCategoryIdentity() domain.ResolvedIdentity {
	owner := &domain.Account{ID: "00000000-0000-4000-8000-000000000101", Role: domain.RoleAssociation, Email: "owner@example.test"}
	return domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner}
}
func paymentCategoryCreateInput() dto.PaymentCategoryWrite {
	name, categoryID, vatID, deduction := "Quote", "00000000-0000-4000-8000-000000000201", "00000000-0000-4000-8000-000000000202", uint16(2)
	return dto.PaymentCategoryWrite{NamePresent: true, Name: &name, CategoryIDPresent: true, CategoryID: &categoryID,
		VAT: dto.PaymentCategoryVATWrite{Present: true, Value: dto.PaymentCategoryVATInput{ID: &vatID, VAT: &vatChoice, Deduction: &deduction}}}
}

var vatChoice = uint16(16)

func TestPaymentCategoriesListScopeAndOrder(t *testing.T) {
	store := &paymentCategoryStoreTest{}
	store.listed = []domain.PaymentCategoryState{{ID: paymentCategoryTestCategory}}
	got, err := NewPaymentCategories(store, time.Now).List(context.Background(), paymentCategoryIdentity())
	if err != nil || len(got) != 1 || got[0].ID != paymentCategoryTestCategory ||
		store.listedAsso != paymentCategoryIdentity().ScopeUser.ID {
		t.Fatal(got, err, store)
	}
	athlete := &domain.Account{ID: "athlete", Role: domain.RoleAthlete}
	if _, err = NewPaymentCategories(store, time.Now).List(context.Background(), domain.ResolvedIdentity{Actor: athlete, Effective: athlete, ScopeUser: athlete}); !errors.Is(err, domain.ErrPaymentCategoryListForbidden) {
		t.Fatal("athlete role admitted", err)
	}
}

func TestPaymentCategoriesAddForcesTenantAndAuditsVATThenCategory(t *testing.T) {
	owner := "00000000-0000-4000-8000-000000000101"
	association := paymentCategoryTestAssociation
	store := &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: association}}
	now := time.Date(2026, 10, 23, 12, 0, 0, 123456000, time.UTC)
	foreign := "00000000-0000-4000-8000-000000000999"
	input := paymentCategoryCreateInput()
	input.AssociationPresent, input.AssociationID = true, &foreign
	got, err := NewPaymentCategories(store, func() time.Time { return now }).Add(context.Background(), paymentCategoryIdentity(), input,
		domain.LoginAuditContext{RemoteAddress: stringPointer("127.0.0.1")})
	if err != nil || !store.tx.committed || !store.tx.inserted || store.tx.category.AssociationID == nil ||
		*store.tx.category.AssociationID != association || got.AssociationID == nil || *got.AssociationID != association {
		t.Fatal(got, err, store.tx)
	}
	if store.tx.category.ID != *input.CategoryID || store.tx.category.CreationDate != now ||
		store.tx.category.Expense || store.tx.category.TaxDeductible || store.tx.category.Archived ||
		store.tx.category.Type != 1 || store.tx.category.Deleted {
		t.Fatalf("defaults mismatch: %#v", store.tx.category)
	}
	if len(store.tx.audits) != 2 || store.tx.audits[0].Model != "vatmanagement" || store.tx.audits[1].Model != "paymentcategory" {
		t.Fatalf("audit order/model mismatch: %#v", store.tx.audits)
	}
	if store.tx.audits[0].Action != 0 || store.tx.audits[1].Action != 0 {
		t.Fatal("create audits did not use auditlog CREATE action", store.tx.audits)
	}
	categoryAudit := store.tx.audits[1]
	if categoryAudit.AssociationID == nil || *categoryAudit.AssociationID != association || categoryAudit.ResolutionPath == nil ||
		*categoryAudit.ResolutionPath != "direct" || categoryAudit.Context.ActorID == nil || *categoryAudit.Context.ActorID != owner {
		t.Fatal(categoryAudit)
	}
	if categoryAudit.Changes["users_default_category"] != [2]string{"None", "application.User.None"} ||
		categoryAudit.Changes["users_default_category_courses"] != [2]string{"None", "application.User.None"} {
		t.Fatal("CREATE audit omitted reverse default-category relations", categoryAudit.Changes)
	}
}

func TestPaymentCategoriesAtomicFailureRollsBackEveryWrite(t *testing.T) {
	for _, step := range []string{"associations", "vat-read", "vat-write", "write", "audit", "commit"} {
		t.Run(step, func(t *testing.T) {
			store := &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: paymentCategoryTestAssociation, fail: step}}
			_, err := NewPaymentCategories(store, time.Now).Add(context.Background(), paymentCategoryIdentity(), paymentCategoryCreateInput(), domain.LoginAuditContext{})
			if err == nil || store.tx.committed || store.tx.rollbacks != 1 {
				t.Fatal(err, store.tx)
			}
		})
	}
}

func TestPaymentCategoriesRejectExistingWritableIdentifiers(t *testing.T) {
	input := paymentCategoryCreateInput()
	store := &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: paymentCategoryTestAssociation, categoryExists: true}}
	if _, err := NewPaymentCategories(store, time.Now).Add(context.Background(), paymentCategoryIdentity(), input, domain.LoginAuditContext{}); !paymentCategoryValidationCode(err, "payment_category_id", dto.PaymentCategoryValidationUniqueID) {
		t.Fatal("existing category ID accepted", err)
	}
	if store.tx.inserted || store.tx.committed || store.tx.rollbacks != 1 {
		t.Fatal("duplicate category ID caused a write", store.tx)
	}

	store = &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: paymentCategoryTestAssociation,
		vat: &domain.PaymentCategoryVAT{ID: *input.VAT.Value.ID, VAT: 1, Deduction: 0}}}
	if _, err := NewPaymentCategories(store, time.Now).Add(context.Background(), paymentCategoryIdentity(), input, domain.LoginAuditContext{}); !paymentCategoryValidationCode(err, "vat_management", dto.PaymentCategoryValidationUniqueVAT) {
		t.Fatal("existing VAT ID accepted", err)
	}
	if store.tx.inserted || store.tx.committed || store.tx.rollbacks != 1 {
		t.Fatal("duplicate VAT ID caused a write", store.tx)
	}
}

func TestPaymentCategoriesMutationRequiresActiveAssociation(t *testing.T) {
	for _, operation := range []string{"add", "update", "delete"} {
		t.Run(operation, func(t *testing.T) {
			store := &paymentCategoryStoreTest{}
			flow := NewPaymentCategories(store, time.Now)
			var err error
			switch operation {
			case "add":
				_, err = flow.Add(context.Background(), paymentCategoryIdentity(), paymentCategoryCreateInput(), domain.LoginAuditContext{})
			case "update":
				_, err = flow.Update(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory, dto.PaymentCategoryWrite{}, domain.LoginAuditContext{})
			case "delete":
				err = flow.Delete(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory, domain.LoginAuditContext{})
			}
			if !errors.Is(err, domain.ErrAssociationNotFound) || store.tx.rollbacks != 1 || store.tx.committed {
				t.Fatal(err, store.tx)
			}
		})
	}
}

func TestPaymentCategoriesUpdateAdmissionPrecedesBodyValidation(t *testing.T) {
	store := &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: paymentCategoryTestAssociation,
		category: domain.PaymentCategoryState{ID: paymentCategoryTestCategory, AssociationID: stringPointer(paymentCategoryTestAssociation)}}}
	if err := NewPaymentCategories(store, time.Now).AdmitUpdate(context.Background(), paymentCategoryIdentity(), "not-a-uuid"); !errors.Is(err, domain.ErrPaymentCategoryInvalidID) || store.tx.rollbacks != 1 {
		t.Fatal("active association must reach UUID parsing", err)
	}
	store = &paymentCategoryStoreTest{}
	if err := NewPaymentCategories(store, time.Now).AdmitUpdate(context.Background(), paymentCategoryIdentity(), "not-a-uuid"); !errors.Is(err, domain.ErrAssociationNotFound) || store.tx.rollbacks != 1 {
		t.Fatal("missing association must precede UUID/body validation", err)
	}
	store = &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: paymentCategoryTestAssociation}}
	if err := NewPaymentCategories(store, time.Now).AdmitUpdate(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory); !errors.Is(err, domain.ErrPaymentCategoryNotFound) {
		t.Fatal("missing target must fail admission", err)
	}
}

func TestPaymentCategoriesPatchPartialAndRejectsTenantChange(t *testing.T) {
	association := paymentCategoryTestAssociation
	category := domain.PaymentCategoryState{ID: paymentCategoryTestCategory, Name: "Old", AssociationID: &association, CreationDate: time.Unix(1, 0), Type: 1,
		VAT: &domain.PaymentCategoryVAT{ID: paymentCategoryTestVAT, VAT: 1, Deduction: 0}}
	store := &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: association, category: category}}
	name := "New"
	input := dto.PaymentCategoryWrite{NamePresent: true, Name: &name}
	got, err := NewPaymentCategories(store, time.Now).Update(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory, input, domain.LoginAuditContext{})
	if err != nil || got.Name != "New" || got.VAT == nil || got.VAT.ID != paymentCategoryTestVAT || !store.tx.updated || !store.tx.committed {
		t.Fatal(got, err, store.tx)
	}
	if len(store.tx.audits) != 1 || !reflect.DeepEqual(store.tx.audits[0].Changes, map[string][2]string{"name": {"Old", "New"}}) {
		t.Fatalf("update diff mismatch: %#v", store.tx.audits)
	}

	store = &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: association, category: category, associationExists: true}}
	input = dto.PaymentCategoryWrite{VAT: dto.PaymentCategoryVATWrite{Present: true, Value: dto.PaymentCategoryVATInput{VAT: func() *uint16 { value := uint16(0); return &value }(), Deduction: func() *uint16 { value := uint16(0); return &value }()}}}
	got, err = NewPaymentCategories(store, time.Now).Update(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory, input, domain.LoginAuditContext{})
	if err != nil || got.VAT == nil || got.VAT.ID != paymentCategoryTestVAT || len(store.tx.audits) != 0 || store.tx.vat != nil {
		t.Fatal("empty PATCH VAT was not a no-op", got, err, store.tx)
	}

	store = &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: association, category: category, associationExists: true}}
	foreign := "00000000-0000-4000-8000-000000000999"
	input = dto.PaymentCategoryWrite{AssociationPresent: true, AssociationID: &foreign}
	if _, err = NewPaymentCategories(store, time.Now).Update(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory, input, domain.LoginAuditContext{}); !errors.Is(err, domain.ErrPaymentCategoryForeignReference) {
		t.Fatal("foreign tenant change admitted", err)
	}
	if store.begins != 1 || store.tx.updated || store.tx.committed || store.tx.rollbacks != 1 {
		t.Fatal("foreign tenant change caused a write", store)
	}
	missingAssociation := "00000000-0000-4000-8000-000000000997"
	store = &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: association, category: category}}
	input = dto.PaymentCategoryWrite{AssociationPresent: true, AssociationID: &missingAssociation}
	if _, err = NewPaymentCategories(store, time.Now).Update(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory, input, domain.LoginAuditContext{}); !paymentCategoryValidationCode(err, "sport_association", "related-does-not-exist:"+missingAssociation) {
		t.Fatal("missing related association validation admitted", err)
	}

	newID := "00000000-0000-4000-8000-000000000998"
	store = &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: association, category: category}}
	input = dto.PaymentCategoryWrite{CategoryIDPresent: true, CategoryID: &newID}
	if _, err = NewPaymentCategories(store, time.Now).Update(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory, input, domain.LoginAuditContext{}); !paymentCategoryValidationCode(err, "payment_category_id", "immutable-category-id") {
		t.Fatal("primary key rewrite admitted", err)
	}
}

func paymentCategoryValidationCode(err error, field, code string) bool {
	var validation *dto.PaymentCategoryValidationError
	if !errors.As(err, &validation) {
		return false
	}
	value := validation.Errors[field]
	if nested, ok := value.(map[string]any); ok {
		value = nested[field+"_id"]
	}
	messages, _ := value.([]string)
	for _, message := range messages {
		if message == code {
			return true
		}
	}
	return false
}

func TestPaymentCategoriesDeleteIsIdempotentAndAuditedOnce(t *testing.T) {
	association := paymentCategoryTestAssociation
	for _, deleted := range []bool{false, true} {
		store := &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: association,
			category: domain.PaymentCategoryState{ID: paymentCategoryTestCategory, AssociationID: &association, Name: "Old", Deleted: deleted}}}
		err := NewPaymentCategories(store, time.Now).Delete(context.Background(), paymentCategoryIdentity(), paymentCategoryTestCategory, domain.LoginAuditContext{})
		if err != nil || !store.tx.committed || !store.tx.category.Deleted {
			t.Fatal(err, store.tx)
		}
		if deleted && len(store.tx.audits) != 0 || !deleted && len(store.tx.audits) != 1 {
			t.Fatalf("deleted=%v audits=%#v", deleted, store.tx.audits)
		}
	}
}

func TestPaymentCategoriesCancellationStopsBeforeEffects(t *testing.T) {
	for _, operation := range []string{"list", "add", "update", "delete"} {
		t.Run(operation, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			cancel()
			store := &paymentCategoryStoreTest{tx: paymentCategoryTransactionTest{association: paymentCategoryTestAssociation}}
			service := NewPaymentCategories(store, time.Now)
			var err error
			switch operation {
			case "list":
				_, err = service.List(ctx, paymentCategoryIdentity())
			case "add":
				_, err = service.Add(ctx, paymentCategoryIdentity(), paymentCategoryCreateInput(), domain.LoginAuditContext{})
			case "update":
				_, err = service.Update(ctx, paymentCategoryIdentity(), paymentCategoryTestCategory, dto.PaymentCategoryWrite{}, domain.LoginAuditContext{})
			case "delete":
				err = service.Delete(ctx, paymentCategoryIdentity(), paymentCategoryTestCategory, domain.LoginAuditContext{})
			}
			if !errors.Is(err, context.Canceled) || store.begins != 0 {
				t.Fatal(err, store)
			}
		})
	}
}
