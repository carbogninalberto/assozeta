package service

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type paymentInfoReaderTest struct {
	record         *domain.PaymentInfoRecord
	lookup         domain.PaymentInfoLookup
	names          map[uuid.UUID]string
	calls, batches int
	ids            []uuid.UUID
	err            error
}

func (r *paymentInfoReaderTest) ReadPaymentInfo(_ context.Context, lookup domain.PaymentInfoLookup) (*domain.PaymentInfoRecord, error) {
	r.calls++
	r.lookup = lookup
	return r.record, r.err
}
func (r *paymentInfoReaderTest) ReadPaymentInfoCategoryNames(_ context.Context, ids []uuid.UUID) (map[uuid.UUID]string, error) {
	r.batches++
	r.ids = ids
	return r.names, r.err
}
func paymentInfoString(v string) *string { return &v }
func paymentInfoFixture() *domain.PaymentInfoRecord {
	id := uuid.MustParse("a1000000-0000-4000-8000-000000226000")
	owner := uuid.MustParse("a1000000-0000-4000-8000-000000220000")
	at := time.Date(2026, 10, 23, 8, 9, 10, 120000000, time.UTC)
	return &domain.PaymentInfoRecord{ID: id, Amount: paymentInfoString("123.40"), CreatedAt: &at,
		Association: &domain.PaymentInfoAssociation{ID: uuid.MustParse("a1000000-0000-4000-8000-000000221000"), OwnerID: &owner,
			Denomination: paymentInfoString("Checkout"), TaxCode: paymentInfoString("12345678901")},
		Course: json.RawMessage(`false`), Meta: json.RawMessage(`null`), CategoryName: paymentInfoString("Own")}
}
func TestPaymentInfoMetadataBatchPreservesNumbersAndDuplicateEntries(t *testing.T) {
	r := &paymentInfoReaderTest{record: paymentInfoFixture()}
	id := uuid.MustParse("a1000000-0000-4000-8000-000000225001")
	r.names = map[uuid.UUID]string{id: "Foreign"}
	r.record.Meta = json.RawMessage(`[{"payment_category_id":"a1000000-0000-4000-8000-000000225001","value":900719925474099312345,"flag":false},{"payment_category_id":"a1000000-0000-4000-8000-000000225001","value":null}]`)
	got, err := NewPaymentInfo(r).Read(context.Background(), domain.ResolvedIdentity{}, r.record.ID.String(), "malformed")
	if err != nil {
		t.Fatal(err)
	}
	if r.batches != 1 || !reflect.DeepEqual(r.ids, []uuid.UUID{id}) || r.lookup.GroupID != nil || r.lookup.AssociationID != nil {
		t.Fatal("batch/scope", r)
	}
	info := got["info"].(map[string]any)
	entries := info["meta_payment_categories"].([]any)
	first := entries[0].(map[string]any)
	if first["value"] != json.Number("900719925474099312345") || first["flag"] != false || first["payment_category_name"] != "Foreign" || len(entries) != 2 {
		t.Fatal(entries)
	}
	if got["creation_date"] != "2026-10-23T08:09:10.120000Z" || got["course"] != false {
		t.Fatal(got)
	}
}

func TestPaymentInfoStoredJSONFractionRendering(t *testing.T) {
	value, err := paymentInfoJSON(json.RawMessage(`{"integer":9007199254740993,"fraction":9007199254740993.0,"tiny":1e-400,"nested":[false,{"number":12.5}]}`))
	if err != nil {
		t.Fatal(err)
	}
	object := value.(map[string]any)
	if object["integer"] != json.Number("9007199254740993") || object["fraction"] != float64(9007199254740992) || object["tiny"] != float64(0) {
		t.Fatal(object)
	}
	if object["nested"].([]any)[1].(map[string]any)["number"] != 12.5 {
		t.Fatal(object)
	}
	if _, err := paymentInfoJSON(json.RawMessage(`{"fraction":1.0e400}`)); err == nil {
		t.Fatal("non-finite JSON fraction must fail before response rendering")
	}
}

func TestPaymentInfoCarnetNaNRejectsBeforeTransport(t *testing.T) {
	r := &paymentInfoReaderTest{record: paymentInfoFixture()}
	r.record.Subject = 2
	r.record.Carnet = &domain.PaymentInfoRelation{Offering: &domain.PaymentInfoOffering{Fee: paymentInfoString("NaN")}}
	data, err := NewPaymentInfo(r).Read(context.Background(), domain.ResolvedIdentity{}, r.record.ID.String(), "")
	if err == nil || data != nil {
		t.Fatalf("persisted carnet NaN must reach the normal HTML error path before JSON encoding: data=%v err=%v", data, err)
	}
}
func TestPaymentInfoFalseyMetadataAndMalformedShapes(t *testing.T) {
	for _, raw := range []string{`null`, `false`, `0`, `""`, `[]`, `{}`} {
		r := &paymentInfoReaderTest{record: paymentInfoFixture()}
		r.record.Meta = json.RawMessage(raw)
		result, err := NewPaymentInfo(r).Read(context.Background(), domain.ResolvedIdentity{}, r.record.ID.String(), "")
		if err != nil || r.batches != 0 {
			t.Fatal(raw, err, r.batches)
		}
		encoded, _ := json.Marshal(result["info"].(map[string]any)["meta_payment_categories"])
		if string(encoded) != raw {
			t.Fatal(raw, string(encoded))
		}
	}
	for _, raw := range []string{`true`, `1`, `"invalid"`, `{"payment_category_id":"missing"}`, `[null]`, `[{}]`, `[{"payment_category_id":null}]`, `[{"payment_category_id":"bad"}]`} {
		r := &paymentInfoReaderTest{record: paymentInfoFixture()}
		r.record.Meta = json.RawMessage(raw)
		if _, err := NewPaymentInfo(r).Read(context.Background(), domain.ResolvedIdentity{}, r.record.ID.String(), ""); err == nil {
			t.Fatal("accepted malformed metadata", raw)
		}
	}
}
func TestPaymentInfoPrecedenceAndCarnetProjection(t *testing.T) {
	r := &paymentInfoReaderTest{record: paymentInfoFixture()}
	r.record.Subject = 2
	r.record.Direct = &domain.PaymentInfoRelation{Offering: &domain.PaymentInfoOffering{Title: paymentInfoString("Direct"), Fee: paymentInfoString("1.20")}}
	r.record.Installment = &domain.PaymentInfoRelation{Offering: &domain.PaymentInfoOffering{Title: paymentInfoString("Installment")}}
	r.record.Carnet = &domain.PaymentInfoRelation{Offering: &domain.PaymentInfoOffering{Title: paymentInfoString("Carnet"), Fee: paymentInfoString("89.10"), Association: r.record.Association}}
	flow := NewPaymentInfo(r)
	got, err := flow.Read(context.Background(), domain.ResolvedIdentity{}, r.record.ID.String(), "")
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(got["info"])
	if string(encoded) != `{"description":null,"fee":"1.20","payment_category_name":"Own","sport_association":null,"title":"Direct"}` {
		t.Fatal(string(encoded))
	}
	r.record.Direct = nil
	r.record.Installment = nil
	got, err = flow.Read(context.Background(), domain.ResolvedIdentity{}, r.record.ID.String(), "")
	if err != nil {
		t.Fatal(err)
	}
	info := got["info"].(map[string]any)
	if _, ok := info["payment_category_name"]; ok || info["title"] != "Carnet (Carnet)" || info["fee"] != json.Number("89.10") {
		t.Fatal(info)
	}
	r.record.Carnet.Offering = nil
	if _, err = flow.Read(context.Background(), domain.ResolvedIdentity{}, r.record.ID.String(), ""); err == nil {
		t.Fatal("missing carnet accepted")
	}
}
func TestPaymentInfoAdmissionLookupAndFailures(t *testing.T) {
	r := &paymentInfoReaderTest{record: paymentInfoFixture()}
	flow := NewPaymentInfo(r)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := flow.Read(ctx, domain.ResolvedIdentity{}, r.record.ID.String(), ""); !errors.Is(err, context.Canceled) || r.calls != 0 {
		t.Fatal(err, r.calls)
	}
	if _, err := flow.Read(context.Background(), domain.ResolvedIdentity{}, "bad", ""); err == nil || r.calls != 0 {
		t.Fatal(err, r.calls)
	}
	scope := r.record.Association.ID.String()
	group := uuid.New()
	_, err := flow.Read(context.Background(), domain.ResolvedIdentity{ImpersonationAssociationID: scope}, r.record.ID.String(), group.String())
	if err != nil || r.lookup.AssociationID == nil || *r.lookup.AssociationID != r.record.Association.ID || r.lookup.GroupID == nil || *r.lookup.GroupID != group {
		t.Fatal(err, r.lookup)
	}
	r.record = nil
	if _, err = flow.Read(context.Background(), domain.ResolvedIdentity{}, uuid.NewString(), ""); !errors.Is(err, ErrPaymentInfoNotFound) {
		t.Fatal(err)
	}
}
