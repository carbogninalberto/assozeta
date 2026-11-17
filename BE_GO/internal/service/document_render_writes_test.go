package service

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// The three writes around a name the response or the storage refuses:
// fill_pdf_and_store's line break (Django's BadHeaderError after the form is
// stored), a storage name default_storage.save rejects (the provider's
// exception text after the Document) and a header value uvicorn refuses (the
// view's writes, then no response).

const (
	renderTestDocumentID = "a1000000-0000-4000-8000-000000000001"
	renderTestTokenID    = "a1000000-0000-4000-8000-000000000002"
	renderTestAssociate  = "a1000000-0000-4000-8000-000000000003"
	renderTestCreated    = "1767323045.678901" // datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=utc).timestamp()
)

var renderTestNow = time.Date(2026, 1, 2, 3, 4, 5, 678901000, time.UTC)

func drText(value string) *string { return &value }

// drStore is the store of one medical appointment or one subscription;
// every other read fails the test.
type drStore struct {
	t            *testing.T
	subscription *domain.RenderSubscription
	appointment  *domain.RenderAppointment
	form         *domain.RenderAppointmentForm
	documents    []domain.NewRenderDocument
	links        []domain.RenderLink
}

func (s *drStore) unexpected(name string) {
	s.t.Fatalf("unexpected store call: %s", name)
}

func (s *drStore) InstanceOwner(context.Context, string) (bool, error) {
	s.unexpected("InstanceOwner")
	return false, nil
}

func (s *drStore) MemberSubscription(context.Context, string, string) (bool, error) {
	s.unexpected("MemberSubscription")
	return false, nil
}

func (s *drStore) MemberInvoice(context.Context, string, string) (bool, error) {
	s.unexpected("MemberInvoice")
	return false, nil
}

func (s *drStore) MemberDocument(context.Context, string, string) (bool, error) {
	s.unexpected("MemberDocument")
	return false, nil
}

func (s *drStore) MemberCompensation(context.Context, string, string) (bool, error) {
	s.unexpected("MemberCompensation")
	return false, nil
}

func (s *drStore) RecordDocumentToken(context.Context, domain.RenderTokenRecord, string, string) (bool, error) {
	s.unexpected("RecordDocumentToken")
	return false, nil
}

func (s *drStore) ActorAssociation(context.Context, string) (*string, error) {
	s.unexpected("ActorAssociation")
	return nil, nil
}

func (s *drStore) OwnedAssociations(context.Context, string) ([]string, error) {
	s.unexpected("OwnedAssociations")
	return nil, nil
}

func (s *drStore) Subscription(context.Context, string, domain.RenderScope) (*domain.RenderSubscription, error) {
	if s.subscription == nil {
		s.unexpected("Subscription")
	}
	return s.subscription, nil
}

func (s *drStore) Invoice(context.Context, string, domain.RenderScope) (*domain.RenderInvoice, error) {
	s.unexpected("Invoice")
	return nil, nil
}

func (s *drStore) InvoicePayments(context.Context, string, domain.RenderScope) ([]domain.RenderPayment, error) {
	s.unexpected("InvoicePayments")
	return nil, nil
}

func (s *drStore) Compensation(context.Context, string, domain.RenderScope) (*domain.RenderPayment, error) {
	s.unexpected("Compensation")
	return nil, nil
}

func (s *drStore) CompensationHours(context.Context, string) ([]domain.RenderHours, error) {
	s.unexpected("CompensationHours")
	return nil, nil
}

func (s *drStore) CustomerInvoice(context.Context, string) (*domain.RenderCustomerInvoice, error) {
	s.unexpected("CustomerInvoice")
	return nil, nil
}

func (s *drStore) Template(context.Context, string, string) (*domain.RenderTemplate, error) {
	s.unexpected("Template")
	return nil, nil
}

func (s *drStore) FolderExists(context.Context, int64) (bool, error) {
	s.unexpected("FolderExists")
	return false, nil
}

func (s *drStore) Appointment(context.Context, string) (*domain.RenderAppointment, error) {
	if s.appointment == nil {
		s.unexpected("Appointment")
	}
	return s.appointment, nil
}

func (s *drStore) AppointmentForm(context.Context, string) (*domain.RenderAppointmentForm, error) {
	if s.form == nil {
		s.unexpected("AppointmentForm")
	}
	return s.form, nil
}

func (s *drStore) AppointmentFormTutor(context.Context, string) (*domain.RenderFormTutor, error) {
	return nil, nil
}

func (s *drStore) PromoteAppointmentFormTutor(context.Context, string, domain.LoginAuditContext, time.Time) (*domain.RenderFormTutor, error) {
	s.unexpected("PromoteAppointmentFormTutor")
	return nil, nil
}

func (s *drStore) CreateDocument(_ context.Context, document domain.NewRenderDocument, _ *string, _ domain.LoginAuditContext, _ time.Time) error {
	s.documents = append(s.documents, document)
	return nil
}

func (s *drStore) CreateArchive(context.Context, string, string, string, int64, time.Time, domain.LoginAuditContext, time.Time) error {
	s.unexpected("CreateArchive")
	return nil
}

func (s *drStore) CreateSubscriptionFile(context.Context, string, domain.RenderSubscription, string, time.Time, domain.LoginAuditContext, time.Time) error {
	s.unexpected("CreateSubscriptionFile")
	return nil
}

func (s *drStore) Link(_ context.Context, link domain.RenderLink, _ string, _ domain.LoginAuditContext, _ time.Time) error {
	s.links = append(s.links, link)
	return nil
}

func (s *drStore) SubscriptionSaveSeason(context.Context, string) (domain.SubscriptionSaveSeason, error) {
	// A season range that does not raise (31 April is the model-hook case).
	return domain.SubscriptionSaveSeason{Created: renderTestNow, Rule: domain.CalendarRule{StartDay: 1, StartMonth: 1}}, nil
}

// drForms records the storage calls of a filled form.
type drForms struct {
	nameErr error
	saved   []string
}

func (f *drForms) GeneratedName(time.Time, string, string) error { return f.nameErr }

func (f *drForms) SaveGenerated(_ context.Context, _ time.Time, _ string, filename string, _ []byte) error {
	f.saved = append(f.saved, filename)
	return nil
}

type drRenderer struct{ pdf []byte }

func (r *drRenderer) Render(context.Context, string, *string) ([]byte, error) { return r.pdf, nil }

// drObjects is the default_storage location of a DocumentObjectStore.
type drObjects struct{ location string }

func (o drObjects) Location() string { return o.location }

func (o drObjects) Exists(context.Context, string) (bool, error) { return false, nil }

func (o drObjects) Put(context.Context, string, []byte, string, string) error { return nil }

func drService(store domain.DocumentRenderStore, forms RenderFormStorage, publisher celery.Publisher, renderer PDFRenderer) *DocumentRender {
	next := 0
	ids := []string{renderTestDocumentID, renderTestTokenID}
	newID := func() string {
		next++
		return ids[next-1]
	}
	return NewDocumentRender(store, renderer, publisher, DocumentRenderSettings{}, func() time.Time { return renderTestNow }, newID).
		WithFormStorage(forms)
}

func drAppointment(denomination, date string) *domain.RenderAppointment {
	return &domain.RenderAppointment{ID: "a1000000-0000-4000-8000-000000000010", Association: "a1000000-0000-4000-8000-000000000011",
		Subscription: "a1000000-0000-4000-8000-000000000012", Denomination: drText(denomination), Region: medicalFormRegion, Date: drText(date)}
}

func drForm() *domain.RenderAppointmentForm {
	return &domain.RenderAppointmentForm{Associate: drText(renderTestAssociate), Text: map[string]*string{}, Created: renderTestNow}
}

func drRequest() DocumentRenderRequest {
	return DocumentRenderRequest{Route: RenderMedical, UID: "a1000000-0000-4000-8000-000000000010"}
}

// fill_pdf_and_store saves the form, opens it and refuses the Content-Disposition
// header: Django answers BadHeaderError's text and the view links nothing.
func TestDocumentRenderFilledFormLineBreakAnswersBadHeader(t *testing.T) {
	store := &drStore{t: t, appointment: drAppointment("Polisportiva\nBiella", "2026-05-12"), form: drForm()}
	forms := &drForms{}
	reply, err := drService(store, forms, &celery.Recorder{}, &drRenderer{}).Render(context.Background(), domain.ResolvedIdentity{}, drRequest())
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	want := "Header values can't contain newlines (got 'inline; filename=[Polisportiva\\nBiella] Visita Agonistica Piemonte Biella del 2026-05-12.pdf')"
	if reply.Status != 500 || reply.JSON == nil || reply.JSON.(map[string]string)["exception"] != want {
		t.Fatalf("reply = %d %v, want 500 %q", reply.Status, reply.JSON, want)
	}
	if len(store.documents) != 1 || len(forms.saved) != 1 {
		t.Fatalf("documents = %d, saved = %d, want the Document and the stored form", len(store.documents), len(forms.saved))
	}
	if store.documents[0].Filename != "[Polisportiva\nBiella] Visita Agonistica Piemonte Biella del 2026-05-12.pdf" {
		t.Fatalf("filename = %q", store.documents[0].Filename)
	}
	if len(store.links) != 0 {
		t.Fatalf("links = %v, want none", store.links)
	}
}

// default_storage.save refuses the generated name (validate_file_name's
// SuspiciousFileOperation): the Document exists, the form is not stored, the
// appointment is not linked and the answer is str(e).
func TestDocumentRenderFilledFormRejectedStorageNameAnswersException(t *testing.T) {
	store := &drStore{t: t, appointment: drAppointment("Polisportiva/../Città", "2026-05-16"), form: drForm()}
	forms := &drRecordingForms{inner: NewDocumentUploads(nil, drObjects{location: "document-render"}, nil, true,
		func() time.Time { return renderTestNow }, func() string { return renderTestDocumentID })}
	reply, err := drService(store, forms, &celery.Recorder{}, &drRenderer{}).Render(context.Background(), domain.ResolvedIdentity{}, drRequest())
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	want := "Detected path traversal attempt in '" + renderTestCreated + "/" + renderTestDocumentID +
		"/[Polisportiva/../Città] Visita Agonistica Piemonte Biella del 2026-05-16.pdf'"
	if reply.Status != 500 || reply.JSON == nil || reply.JSON.(map[string]string)["exception"] != want {
		t.Fatalf("reply = %d %v, want 500 %q", reply.Status, reply.JSON, want)
	}
	if len(store.documents) != 1 {
		t.Fatalf("documents = %d, want the Document", len(store.documents))
	}
	if len(forms.saved) != 0 {
		t.Fatalf("saved = %v, want nothing stored", forms.saved)
	}
	if len(store.links) != 0 {
		t.Fatalf("links = %v, want none", store.links)
	}
}

// drRecordingForms is DocumentUploads behind a record of the stored names.
type drRecordingForms struct {
	inner RenderFormStorage
	saved []string
}

func (r *drRecordingForms) GeneratedName(created time.Time, id, filename string) error {
	return r.inner.GeneratedName(created, id, filename)
}

func (r *drRecordingForms) SaveGenerated(ctx context.Context, created time.Time, id, filename string, content []byte) error {
	r.saved = append(r.saved, filename)
	return r.inner.SaveGenerated(ctx, created, id, filename, content)
}

// A header value uvicorn refuses: print_and_store_pdf returns Django's 200, the
// view links the row, and the connection is then closed with no response.
func TestDocumentRenderPrintServerRefusedHeaderClosesAfterLink(t *testing.T) {
	store := &drStore{t: t, subscription: &domain.RenderSubscription{ID: "a1000000-0000-4000-8000-000000000020",
		Association: "a1000000-0000-4000-8000-000000000011", Denomination: drText("Società\x01Sportiva"), HasAssociate: true,
		First: drText("Mario"), Last: drText("Rossi")}}
	publisher := &celery.Recorder{}
	reply, err := drService(store, &drForms{}, publisher, &drRenderer{pdf: []byte("%PDF-1.7")}).
		Render(context.Background(), domain.ResolvedIdentity{}, DocumentRenderRequest{Route: RenderSubscription,
			UID: "a1000000-0000-4000-8000-000000000020"})
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if !reply.CloseConnection || reply.PDF != nil || reply.JSON != nil {
		t.Fatalf("reply = %+v, want a closed connection", reply)
	}
	if len(store.documents) != 1 || len(store.links) != 1 || len(publisher.Tasks) != 1 {
		t.Fatalf("documents = %d, links = %d, tasks = %d, want the Document, the link and the storage task",
			len(store.documents), len(store.links), len(publisher.Tasks))
	}
	if store.documents[0].Filename != "[Società\x01Sportiva] Modulo di Iscrizione Mario Rossi.pdf" {
		t.Fatalf("filename = %q", store.documents[0].Filename)
	}
}

// The fill path writes the Document, the stored form and the appointment link,
// then the server refuses the header value: no response.
func TestDocumentRenderFilledFormServerRefusedHeaderClosesAfterLink(t *testing.T) {
	store := &drStore{t: t, appointment: drAppointment("Polisportiva\x02Biella", "2026-05-17"), form: drForm()}
	forms := &drForms{}
	reply, err := drService(store, forms, &celery.Recorder{}, &drRenderer{}).
		Render(context.Background(), domain.ResolvedIdentity{}, drRequest())
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if !reply.CloseConnection || reply.PDF != nil || reply.JSON != nil {
		t.Fatalf("reply = %+v, want a closed connection", reply)
	}
	if len(store.documents) != 1 || len(forms.saved) != 1 || len(store.links) != 1 {
		t.Fatalf("documents = %d, saved = %d, links = %d, want the Document, the form and the link",
			len(store.documents), len(forms.saved), len(store.links))
	}
	if strings.Contains(store.documents[0].Filename, "\x01") {
		t.Fatalf("filename = %q", store.documents[0].Filename)
	}
}
