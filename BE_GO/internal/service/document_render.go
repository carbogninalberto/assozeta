package service

import (
	"context"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"log/slog"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/djangoauth"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/medicalform"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pdfrender"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

// PDF routes of BE/docmanager/views/printing_views.py.
const (
	RenderSubscription = "subscription"
	RenderInvoice      = "invoice"
	RenderEinvoice     = "einvoice"
	RenderCompensation = "compensation"
	RenderTemplate     = "template"
	RenderMedical      = "medical-appointment"
)

const (
	saveToStorageTask         = "save_to_storage"
	documentRenderPublishWait = 10 * time.Second
)

var (
	// ErrDocumentRenderServer is an exception the view does not catch (the 500 page).
	ErrDocumentRenderServer = errors.New("document render: unhandled exception")
	// ErrDocumentRenderNotPorted refuses an input before any write (the 500 page).
	ErrDocumentRenderNotPorted = refusal.New("document render: input not ported")
	// ErrDocumentRenderUnauthenticated is DRF's NotAuthenticated (401).
	ErrDocumentRenderUnauthenticated = errors.New("document render: not authenticated")
)

// PDFRenderer is pdfrender.Client.
type PDFRenderer interface {
	Render(ctx context.Context, url string, authorization *string) ([]byte, error)
}

// DocumentRenderSettings are the core.settings values the views read.
type DocumentRenderSettings struct {
	BypassToken             string
	AppURL                  string
	TeamEmail, SupportEmail string
}

type DocumentRender struct {
	store     domain.DocumentRenderStore
	renderer  PDFRenderer
	publisher celery.Publisher
	settings  DocumentRenderSettings
	now       func() time.Time
	newID     func() string
	forms     RenderFormStorage
}

// RenderFormStorage is default_storage for the PDF forms a view fills itself
// (PrintingService.fill_pdf_and_store): service.DocumentUploads.
type RenderFormStorage interface {
	GeneratedName(created time.Time, id, filename string) error
	SaveGenerated(ctx context.Context, created time.Time, id, filename string, content []byte) error
}

// WithFormStorage sets the storage of the filled PDF forms; without it they are refused.
func (s *DocumentRender) WithFormStorage(forms RenderFormStorage) *DocumentRender {
	s.forms = forms
	return s
}

func NewDocumentRender(store domain.DocumentRenderStore, renderer PDFRenderer, publisher celery.Publisher, settings DocumentRenderSettings,
	now func() time.Time, newID func() string) *DocumentRender {
	return &DocumentRender{store: store, renderer: renderer, publisher: publisher, settings: settings, now: now, newID: newID}
}

// DocumentRenderRequest is one call of a PDF view.
type DocumentRenderRequest struct {
	Route, UID    string
	Query         domain.RequestObject
	Authorization *string
	Group         string
	// Body reads request.data (nil, domain.RequestObject or a decoded JSON
	// value); Form reports a form media type. Read only where Django reads it.
	Body  func() (any, bool, error)
	Audit domain.LoginAuditContext
}

// DocumentRenderReply is the view's answer: a JSON body or the PDF.
type DocumentRenderReply struct {
	Status      int
	JSON        any
	PDF         []byte
	Disposition string
}

func renderException(status int, text string) *DocumentRenderReply {
	return &DocumentRenderReply{Status: status, JSON: map[string]string{"exception": text}}
}

// pythonValidationText is str(ValidationError(['“value” is not a valid UUID.'])).
func pythonUUIDValidationText(value string) string {
	return "[" + paymentInfoPythonQuote("“"+value+"” is not a valid UUID.") + "]"
}

func renderOptional(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

// pythonFullName is Associate.get_full_name(): ('%s %s' % (first, last)).strip().
func pythonFullName(first, last *string) string {
	return dto.PythonStrip(renderOptional(first) + " " + renderOptional(last))
}

// documentRenderTokenRecords are the routes with a HasDocumentToken class and
// the record whose document the token belongs to.
var documentRenderTokenRecords = map[string]domain.RenderTokenRecord{
	RenderInvoice:      {Table: "application_invoice", Key: "invoice_id", Document: "document_pdf_id"},
	RenderEinvoice:     {Table: "application_customerinvoice", Key: "customer_invoice_id", Document: "pdf_id"},
	RenderCompensation: {Table: "application_instructorhours", Key: "payment_id", Document: "document_id"},
	RenderMedical:      {Table: "application_medicalappointments", Key: "medical_appointments_id", Document: "document_id"},
}

// Authorize evaluates the view's permission classes after identity
// resolution: IsAuthenticated, then HasDocumentToken (?token is the token of
// a document of the record the uid names; a token or uid UUIDField rejects
// admits nothing), then HasBypassAuthorizationHeader (hmac.compare_digest
// with the configured token).
func (s *DocumentRender) Authorize(ctx context.Context, identity domain.ResolvedIdentity, route, uid string, query domain.RequestObject,
	authorization *string) error {
	if identity.ScopeUser != nil {
		return nil
	}
	if record, tokens := documentRenderTokenRecords[route]; tokens {
		if value, present := query.Lookup("token"); present {
			text, _ := value.(string)
			token, tokenErr := paymentInfoParseUUID(text)
			key, keyErr := paymentInfoParseUUID(uid)
			if tokenErr == nil && keyErr == nil {
				admitted, err := s.store.RecordDocumentToken(ctx, record, key.String(), token.String())
				if err != nil {
					return err
				}
				if admitted {
					return nil
				}
			}
		}
	}
	if (route == RenderInvoice || route == RenderEinvoice) && authorization != nil {
		for _, r := range *authorization {
			if r > 0x7f {
				// compare_digest of a non-ASCII str raises TypeError.
				return ErrDocumentRenderServer
			}
		}
		if s.settings.BypassToken != "" && subtle.ConstantTimeCompare([]byte(*authorization), []byte(s.settings.BypassToken)) == 1 {
			return nil
		}
	}
	return ErrDocumentRenderUnauthenticated
}

func renderScope(identity domain.ResolvedIdentity, group string) domain.RenderScope {
	scope := domain.RenderScope{Group: supplierGroup(group)}
	if identity.ImpersonationAssociationID != "" && identity.Effective != nil {
		association := identity.ImpersonationAssociationID
		scope.Association = &association
		scope.Effective = identity.Effective.ID
		scope.Athlete = identity.Effective.Role == domain.RoleAthlete
	}
	return scope
}

// auditFor is the auditlog context: the initiating account and, during
// impersonation, the session record.
func auditFor(identity domain.ResolvedIdentity, audit domain.LoginAuditContext) domain.LoginAuditContext {
	audit.ActorID, audit.ActorEmail, audit.AdditionalData = nil, nil, nil
	if identity.ScopeUser == nil || identity.Actor == nil {
		return audit
	}
	audit.ActorID, audit.ActorEmail = &identity.Actor.ID, &identity.Actor.Email
	if identity.Impersonating {
		var association *string
		if identity.ImpersonationAssociationID != "" {
			value := identity.ImpersonationAssociationID
			association = &value
		}
		target := ""
		if identity.Effective != nil {
			target = identity.Effective.ID
		}
		audit.AdditionalData = &domain.AuditImpersonation{SessionID: identity.SessionID, ActorID: identity.Actor.ID, TargetID: target,
			AssociationID: association}
	}
	return audit
}

// renderJob is what a view hands print_and_store_pdf, and its follow-up.
type renderJob struct {
	filename string
	view     string
	links    []domain.RenderLink
	created  func(ctx context.Context, document domain.NewRenderDocument, audit domain.LoginAuditContext) error
	after    func(ctx context.Context, document domain.NewRenderDocument) error
	// ignore: the template route answers 200 whatever print_and_store_pdf returned.
	ignore bool
	// failed is the view's answer to an exception raised after the Document was created.
	failed *DocumentRenderReply
	// fill is fill_pdf_and_store: the PDF the view builds and saves itself instead of asking the renderer.
	fill func() []byte
}

// Render follows the route's view: lookups, the filename, then the shared
// Document / renderer / storage task / link sequence.
func (s *DocumentRender) Render(ctx context.Context, identity domain.ResolvedIdentity, request DocumentRenderRequest) (*DocumentRenderReply, error) {
	scope := renderScope(identity, request.Group)
	var job *renderJob
	var reply *DocumentRenderReply
	var err error
	switch request.Route {
	case RenderSubscription:
		job, reply, err = s.subscriptionJob(ctx, identity, request, scope)
	case RenderInvoice:
		job, reply, err = s.invoiceJob(ctx, identity, request, scope)
	case RenderEinvoice:
		job, reply, err = s.einvoiceJob(ctx, identity, request)
	case RenderCompensation:
		job, reply, err = s.compensationJob(ctx, identity, request, scope)
	case RenderTemplate:
		job, reply, err = s.templateJob(ctx, identity, request, scope)
	case RenderMedical:
		job, reply, err = s.medicalJob(ctx, identity, request)
	default:
		return nil, ErrDocumentRenderServer
	}
	if err != nil || reply != nil {
		return reply, err
	}
	if !job.ignore && job.failed == nil && !pdfrender.HeaderSafe(job.filename) {
		// BadHeaderError (or an ASGI header refusal) after the storage task: not ported.
		return nil, ErrDocumentRenderNotPorted
	}
	return s.print(ctx, identity, request, job)
}

func (s *DocumentRender) print(ctx context.Context, identity domain.ResolvedIdentity, request DocumentRenderRequest, job *renderJob) (*DocumentRenderReply, error) {
	audit := auditFor(identity, request.Audit)
	var actorAssociation *string
	if audit.ActorID != nil {
		var err error
		if actorAssociation, err = s.store.ActorAssociation(ctx, *audit.ActorID); err != nil {
			return nil, err
		}
	}
	created := s.now().UTC().Truncate(time.Microsecond)
	document := domain.NewRenderDocument{ID: s.newID(), Token: s.newID(), Filename: job.filename, Created: created}
	file, err := documentkey.StoredName(created, uuid.MustParse(document.ID), document.Filename, nil)
	if err != nil {
		return nil, err
	}
	if job.fill != nil {
		// A name default_storage.save would reject answers an exception text after the Document: not ported.
		if s.forms == nil || s.forms.GeneratedName(created, document.ID, document.Filename) != nil {
			return nil, ErrDocumentRenderNotPorted
		}
	}
	if err := s.store.CreateDocument(ctx, document, actorAssociation, audit, s.now()); err != nil {
		return nil, err
	}
	if job.created != nil {
		if err := job.created(ctx, document, audit); err != nil {
			return nil, err
		}
	}
	if job.failed != nil {
		return job.failed, nil
	}
	var pdf []byte
	if job.fill != nil {
		pdf = job.fill()
		// The view reads the saved file back for its answer: the same bytes.
		if err := s.forms.SaveGenerated(ctx, created, document.ID, document.Filename, pdf); err != nil {
			return nil, err
		}
	} else {
		pdf, err = s.renderer.Render(ctx, job.view, request.Authorization)
		var failure *pdfrender.Failure
		if errors.As(err, &failure) {
			if job.ignore {
				return &DocumentRenderReply{Status: 200, JSON: map[string]string{"msg": "Generato con successo."}}, nil
			}
			return renderException(500, "PDF rendering failed: "+failure.Text), nil
		}
		if err != nil {
			return nil, err
		}
		publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), documentRenderPublishWait)
		_, err = s.publisher.Publish(publish, celery.Task{Name: saveToStorageTask, Args: []any{file, base64.StdEncoding.EncodeToString(pdf)}})
		cancel()
		if err != nil {
			if job.ignore {
				slog.Error("document render: storage task not published", "error", err)
				return &DocumentRenderReply{Status: 200, JSON: map[string]string{"msg": "Generato con successo."}}, nil
			}
			return renderException(500, err.Error()), nil
		}
	}
	if job.ignore {
		return &DocumentRenderReply{Status: 200, JSON: map[string]string{"msg": "Generato con successo."}}, nil
	}
	disposition := "inline"
	if value, _ := queryText(request.Query, "download"); value != "" {
		disposition = "attachment"
	}
	for _, link := range job.links {
		if link.Table == "application_subscription" {
			// save(update_fields=['document_pdf']) runs Subscription.save(): its season range raises before the
			// write, inside the view's except clause.
			season, err := s.store.SubscriptionSaveSeason(ctx, link.ID)
			if err != nil {
				return nil, err
			}
			if err = modelHooksSubscriptionSave(ctx, season); err != nil {
				return renderException(500, err.Error()), nil
			}
		}
		if err := s.store.Link(ctx, link, document.ID, audit, s.now()); err != nil {
			return renderException(500, err.Error()), nil
		}
	}
	if job.after != nil {
		if err := job.after(ctx, document); err != nil {
			return renderException(500, err.Error()), nil
		}
	}
	return &DocumentRenderReply{Status: 200, PDF: pdf, Disposition: pdfrender.HeaderBytes(disposition + "; filename=" + job.filename)}, nil
}

// PrintAndStore is print_and_store_pdf for a stored document whose object is
// missing (download_file/view_file): the renderer prints the view and the
// save_to_storage task receives '<creation timestamp>/<id>/<filename>' with
// the PDF. The callers do not read the outcome: every failure is logged only.
func (s *DocumentRender) PrintAndStore(ctx context.Context, view string, authorization *string, document domain.StoredDocument) {
	id, err := uuid.Parse(document.ID)
	if err != nil {
		slog.Error("document reprint: document id", "error", err)
		return
	}
	file, err := documentkey.StoredName(document.Created, id, document.Filename, nil)
	if err != nil {
		slog.Error("document reprint: storage name", "error", err)
		return
	}
	pdf, err := s.renderer.Render(ctx, view, authorization)
	if err != nil {
		slog.Error("document reprint: renderer", "error", err)
		return
	}
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), documentRenderPublishWait)
	defer cancel()
	if _, err = s.publisher.Publish(publish, celery.Task{Name: saveToStorageTask, Args: []any{file, base64.StdEncoding.EncodeToString(pdf)}}); err != nil {
		slog.Error("document reprint: storage task not published", "error", err)
	}
}

// lookupFailure maps a lookup error to the view's except clause.
func lookupFailure(err error, missing *DocumentRenderReply) (*DocumentRenderReply, error) {
	if errors.Is(err, domain.ErrRenderNotFound) {
		return missing, nil
	}
	return nil, err
}

func (s *DocumentRender) parseUID(raw string) (string, *DocumentRenderReply) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return "", renderException(500, pythonUUIDValidationText(raw))
	}
	return id.String(), nil
}

func (s *DocumentRender) subscriptionJob(ctx context.Context, identity domain.ResolvedIdentity, request DocumentRenderRequest, scope domain.RenderScope) (*renderJob, *DocumentRenderReply, error) {
	id, invalid := s.parseUID(request.UID)
	if invalid != nil {
		return nil, invalid, nil
	}
	subscription, err := s.store.Subscription(ctx, id, scope)
	if err != nil {
		reply, err := lookupFailure(err, renderException(500, "Subscription matching query does not exist."))
		return nil, reply, err
	}
	// Generated for the association's staff and for the member the subscription belongs to.
	if err := memberDocumentAccess(ctx, s.store, identity, identity.ScopeUser, func(ctx context.Context, member string) (bool, error) {
		return s.store.MemberSubscription(ctx, member, subscription.ID)
	}); err != nil {
		return nil, nil, err
	}
	if !subscription.HasAssociate {
		return nil, renderException(500, "'NoneType' object has no attribute 'get_full_name'"), nil
	}
	return &renderJob{
		filename: "[" + renderOptional(subscription.Denomination) + "] Modulo di Iscrizione " + pythonFullName(subscription.First, subscription.Last) + ".pdf",
		view:     "document/subscription/" + request.UID + "/view",
		links: []domain.RenderLink{{Table: "application_subscription", KeyColumn: "subscription_id", Column: "document_pdf_id", Field: "document_pdf",
			Model: "subscription", Representation: "Subscription", ID: subscription.ID, Old: subscription.Document,
			IndexAssociation: &subscription.Association, IndexPath: "direct"}},
	}, nil, nil
}

// receiptEmail is send_receipt_email: the query value, or when it is falsy
// the form body's (request.POST: empty for other media types).
func receiptEmail(request DocumentRenderRequest) (bool, error) {
	value, present := queryText(request.Query, "send_receipt_email")
	if !present || value == "" {
		body, form, err := request.Body()
		if err != nil {
			return false, err
		}
		value = ""
		if fields, ok := body.(domain.RequestObject); ok && form {
			if raw, found := fields.Lookup("send_receipt_email"); found {
				value, _ = raw.(string)
			}
		}
	}
	return value == "true" || value == "True" || value == "1", nil
}

func (s *DocumentRender) invoiceJob(ctx context.Context, identity domain.ResolvedIdentity, request DocumentRenderRequest, scope domain.RenderScope) (*renderJob, *DocumentRenderReply, error) {
	email, err := receiptEmail(request)
	if err != nil {
		return nil, nil, err
	}
	id, invalid := s.parseUID(request.UID)
	if invalid != nil {
		return nil, invalid, nil
	}
	invoice, err := s.store.Invoice(ctx, id, scope)
	if err != nil {
		reply, err := lookupFailure(err, renderException(404, "Invoice not found"))
		return nil, reply, err
	}
	// Generated for the association's staff and for the member the receipt belongs to.
	if err := memberDocumentAccess(ctx, s.store, identity, identity.ScopeUser, func(ctx context.Context, member string) (bool, error) {
		return s.store.MemberInvoice(ctx, member, invoice.ID)
	}); err != nil {
		return nil, nil, err
	}
	payments, err := s.store.InvoicePayments(ctx, invoice.ID, scope)
	if err != nil {
		return nil, nil, err
	}
	switch {
	case len(payments) == 0:
		return nil, renderException(500, "Payment matching query does not exist."), nil
	case len(payments) > 20:
		return nil, renderException(500, "get() returned more than one Payment -- it returned more than 20!"), nil
	case len(payments) > 1:
		return nil, renderException(500, "get() returned more than one Payment -- it returned "+itoa(len(payments))+"!"), nil
	}
	payment := payments[0]
	if payment.Date == nil {
		return nil, renderException(500, "'NoneType' object has no attribute 'date'"), nil
	}
	day := payment.Date.UTC()
	job := &renderJob{
		filename: "[" + renderOptional(invoice.Denomination) + "] Ricevuta " + day.Format("2006-01-02") + ".pdf",
		view:     "document/invoice/" + request.UID + "/view",
		links: []domain.RenderLink{{Table: "application_invoice", KeyColumn: "invoice_id", Column: "document_pdf_id", Field: "document_pdf",
			Model: "invoice", Representation: "Invoice", ID: invoice.ID, Old: invoice.Document, IndexAssociation: &invoice.Association,
			IndexPath: "direct"}},
	}
	if email && payment.HasAssociate && payment.AssociateEmail != nil && *payment.AssociateEmail != "" {
		job.after = func(ctx context.Context, document domain.NewRenderDocument) error {
			short := day.Format("02/01/06")
			message := "Gentile " + pythonFullName(payment.AssociateFirst, payment.AssociateLast) + ",\n" +
				"abbiamo il piacere di inviarle la ricevuta di pagamento di " + strings.ReplaceAll(strings.ReplaceAll(payment.Amount, ",", " "), ".", ",") +
				" € del " + short + ".\n\n" +
				"Ecco il link per scaricare la ricevuta:\n " + s.settings.AppURL + "/api/document/retrieve/" + document.ID +
				"?download=false&token=" + document.Token + "\n\n" +
				"Cordiali saluti,\n" + renderOptional(invoice.Denomination)
			publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), documentRenderPublishWait)
			defer cancel()
			_, err := s.publisher.Publish(publish, celery.Task{Name: sendMailTask, Kwargs: celery.Dict{
				{Key: "subject", Value: "Ricevuta di pagamento " + short}, {Key: "message", Value: message},
				{Key: "from_email", Value: s.settings.TeamEmail}, {Key: "reply_to", Value: []any{s.settings.SupportEmail}},
				{Key: "recipient_list", Value: []any{*payment.AssociateEmail}}, {Key: "html_message", Value: nil},
				{Key: "fail_silently", Value: false},
			}})
			return err
		}
	}
	return job, nil, nil
}

func itoa(n int) string { return strconv.Itoa(n) }

func (s *DocumentRender) einvoiceJob(ctx context.Context, identity domain.ResolvedIdentity, request DocumentRenderRequest) (*renderJob, *DocumentRenderReply, error) {
	id, invalid := s.parseUID(request.UID)
	if invalid != nil {
		return nil, invalid, nil
	}
	invoice, err := s.store.CustomerInvoice(ctx, id)
	if err != nil {
		// except Invoice.DoesNotExist does not catch CustomerInvoice.DoesNotExist.
		reply, err := lookupFailure(err, renderException(500, "CustomerInvoice matching query does not exist."))
		return nil, reply, err
	}
	// Generated for the association's staff.
	if err := memberDocumentStaffAccess(ctx, s.store, identity); err != nil {
		return nil, nil, err
	}
	number := func(value *int64) string {
		if value == nil {
			return "None"
		}
		return strconv.FormatInt(*value, 10)
	}
	return &renderJob{
		filename: "[" + renderOptional(invoice.Denomination) + "] Fattura " + renderOptional(invoice.Prefix) + " " + number(invoice.Number) + "/" +
			number(invoice.Year) + ".pdf",
		view: "document/einvoice/" + request.UID + "/view",
		links: []domain.RenderLink{{Table: "application_customerinvoice", KeyColumn: "customer_invoice_id", Column: "pdf_id", Field: "pdf",
			Model: "customerinvoice", Representation: "CustomerInvoice", ID: invoice.ID, Old: invoice.Document,
			IndexAssociation: &invoice.Association, IndexPath: "direct"}},
	}, nil, nil
}

func (s *DocumentRender) compensationJob(ctx context.Context, identity domain.ResolvedIdentity, request DocumentRenderRequest, scope domain.RenderScope) (*renderJob, *DocumentRenderReply, error) {
	id, invalid := s.parseUID(request.UID)
	if invalid != nil {
		return nil, invalid, nil
	}
	payment, err := s.store.Compensation(ctx, id, scope)
	if err != nil {
		reply, err := lookupFailure(err, renderException(404, "Compensation not found"))
		return nil, reply, err
	}
	// Generated for the association's staff and for the account tied to the instructor.
	if err := memberDocumentAccess(ctx, s.store, identity, identity.ScopeUser, func(ctx context.Context, member string) (bool, error) {
		return s.store.MemberCompensation(ctx, member, payment.ID)
	}); err != nil {
		return nil, nil, err
	}
	if !payment.HasInstructor {
		return nil, renderException(500, "'NoneType' object has no attribute 'first_name'"), nil
	}
	if payment.Date == nil {
		return nil, renderException(500, "'NoneType' object has no attribute 'date'"), nil
	}
	hours, err := s.store.CompensationHours(ctx, payment.ID)
	if err != nil {
		return nil, nil, err
	}
	job := &renderJob{
		filename: "[" + renderOptional(payment.Denomination) + "] Compenso per " + payment.InstructorFirst + " " + payment.InstructorLast + " del " +
			payment.Date.UTC().Format("2006-01-02") + ".pdf",
		view: "document/compensation/" + request.UID + "/view",
	}
	for _, hour := range hours {
		link := domain.RenderLink{Table: "application_instructorhours", KeyColumn: "instructor_hours_id", Column: "document_id", Field: "document",
			Model: "instructorhours", Representation: "InstructorHours", ID: hour.ID, Old: hour.Document}
		if hour.OwnerAssoc != nil {
			link.IndexAssociation, link.IndexPath = hour.OwnerAssoc, "instructor.user.sport_association"
		}
		job.links = append(job.links, link)
	}
	if len(hours) > 0 && job.links[0].IndexAssociation == nil {
		// The actor fallback of an instructor without an owning association is not modelled.
		return nil, nil, ErrDocumentRenderNotPorted
	}
	return job, nil, nil
}

// requestAssociation is request.user.sport_association.
func (s *DocumentRender) requestAssociation(ctx context.Context, identity domain.ResolvedIdentity) (string, *DocumentRenderReply, error) {
	user := identity.ScopeUser
	switch {
	case user == nil:
		return "", renderException(500, "'AnonymousUser' object has no attribute 'sport_association'"), nil
	case user.Role == domain.RoleAthlete:
		return "", renderException(500, "User is not a sport association"), nil
	case user.Role != domain.RoleAssociation:
		return "", nil, ErrDocumentRenderNotPorted
	}
	owned, err := s.store.OwnedAssociations(ctx, user.ID)
	if err != nil {
		return "", nil, err
	}
	switch {
	case len(owned) == 0:
		return "", renderException(500, "SportAssociation matching query does not exist."), nil
	case len(owned) > 20:
		return "", renderException(500, "get() returned more than one SportAssociation -- it returned more than 20!"), nil
	case len(owned) > 1:
		return "", renderException(500, "get() returned more than one SportAssociation -- it returned "+itoa(len(owned))+"!"), nil
	}
	return owned[0], nil, nil
}

// templateQueryValue is str() of additionalData['subscription'] for the
// value types modelled.
func templateQueryValue(value any) (string, bool) {
	switch v := value.(type) {
	case nil:
		return "None", true
	case string:
		return v, true
	case bool:
		if v {
			return "True", true
		}
		return "False", true
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "", false
		}
		return string(v), true
	}
	return "", false
}

func (s *DocumentRender) templateJob(ctx context.Context, identity domain.ResolvedIdentity, request DocumentRenderRequest,
	scope domain.RenderScope) (*renderJob, *DocumentRenderReply, error) {
	association, reply, err := s.requestAssociation(ctx, identity)
	if reply != nil || err != nil {
		return nil, reply, err
	}
	id, invalid := s.parseUID(request.UID)
	if invalid != nil {
		return nil, invalid, nil
	}
	template, err := s.store.Template(ctx, id, association)
	if err != nil {
		reply, err := lookupFailure(err, renderException(404, "Template not found"))
		return nil, reply, err
	}
	// request.data is read after the Document exists; malformed or
	// unmodelled bodies are refused before it.
	body, _, err := request.Body()
	if err != nil {
		return nil, nil, ErrDocumentRenderNotPorted
	}
	var additional domain.RequestObject
	switch data := body.(type) {
	case nil:
	case domain.RequestObject:
		if value, present := data.Lookup("additionalData"); present {
			object, ok := value.(domain.RequestObject)
			if !ok {
				return nil, nil, ErrDocumentRenderNotPorted
			}
			additional = object
		}
	default:
		return nil, nil, ErrDocumentRenderNotPorted
	}
	query := "?"
	var folder *int64
	var subscriptionValue any
	subscriptionPresent := false
	if additional != nil {
		if parent, present := additional.Lookup("parent"); present {
			if number, result := dto.PythonInt(parent); result == dto.PythonIntOK && number.IsInt64() &&
				number.Int64() >= math.MinInt32 && number.Int64() <= math.MaxInt32 {
				value := number.Int64()
				folder = &value
			}
		}
		if value, present := additional.Lookup("subscription"); present {
			text, ok := templateQueryValue(value)
			if !ok {
				return nil, nil, ErrDocumentRenderNotPorted
			}
			query += "subscription_id=" + text + "&"
			subscriptionValue, subscriptionPresent = value, true
		}
	}
	name := strings.ReplaceAll(strings.ReplaceAll(template.Name, ",", " "), ".", " ")
	job := &renderJob{
		filename: "[" + renderOptional(template.Denomination) + "] " + name + " del " + s.now().UTC().Format("02/01/2006") + ".pdf",
		view:     "document/template/" + request.UID + "/view" + query,
		ignore:   true,
	}
	job.created = func(ctx context.Context, document domain.NewRenderDocument, audit domain.LoginAuditContext) error {
		if folder != nil {
			found, err := s.store.FolderExists(ctx, *folder)
			if err != nil {
				return err
			}
			if found {
				if err := s.store.CreateArchive(ctx, s.newID(), association, document.ID, *folder, s.now().UTC(), audit, s.now()); err != nil {
					return err
				}
			}
		}
		if subscriptionPresent && subscriptionValue != nil {
			key, err := djangoUUIDValue(subscriptionValue)
			if err != nil {
				return nil
			}
			subscription, err := s.store.Subscription(ctx, key, scope)
			if errors.Is(err, domain.ErrRenderNotFound) {
				return nil
			}
			if err != nil {
				return err
			}
			created := s.now().UTC().Truncate(time.Microsecond)
			return s.store.CreateSubscriptionFile(ctx, s.newID(), *subscription, document.ID, created, audit, s.now())
		}
		return nil
	}
	return job, nil, nil
}

var renderRegions = map[string]string{
	"agonistica-emilia-romagna":     "Visita Agonistica Emilia Romagna",
	"agonistica-lombardia":          "Visita Agonistica Lombardia",
	"agonistica-piemonte":           "Visita Agonistica Piemonte",
	"agonistica-piemonte-biella":    "Visita Agonistica Piemonte Biella",
	"agonistica-sicilia":            "Visita Agonistica Sicilia",
	"agonistica-toscana":            "Visita Agonistica Toscana",
	"agonistica":                    "Visita Agonistica",
	"non-agonistica-emilia-romagna": "Visita Non Agonistica Emilia Romagna",
	"non-agonistica-sicilia":        "Visita Non Agonistica Sicilia",
	"non-agonistica":                "Visita Non Agonistica",
}

func (s *DocumentRender) medicalJob(ctx context.Context, identity domain.ResolvedIdentity, request DocumentRenderRequest) (*renderJob, *DocumentRenderReply, error) {
	id, invalid := s.parseUID(request.UID)
	if invalid != nil {
		return nil, invalid, nil
	}
	appointment, err := s.store.Appointment(ctx, id)
	if err != nil {
		reply, err := lookupFailure(err, renderException(500, "'NoneType' object has no attribute 'subscription'"))
		return nil, reply, err
	}
	// Generated for the association's staff and for the member of the appointment's subscription.
	if err := memberDocumentAccess(ctx, s.store, identity, identity.ScopeUser, func(ctx context.Context, member string) (bool, error) {
		return s.store.MemberSubscription(ctx, member, appointment.Subscription)
	}); err != nil {
		return nil, nil, err
	}
	region, known := renderRegions[appointment.Region]
	if !known {
		return nil, renderException(500, paymentInfoPythonQuote(appointment.Region)), nil
	}
	job := &renderJob{
		filename: "[" + renderOptional(appointment.Denomination) + "] " + region + " del " + renderOptional(appointment.Date) + ".pdf",
		view:     "document/medical-appointment/" + request.UID + "/view",
		links: []domain.RenderLink{{Table: "application_medicalappointments", KeyColumn: "medical_appointments_id", Column: "document_id",
			Field: "document", Model: "medicalappointments", Representation: "MedicalAppointments", ID: appointment.ID, Old: appointment.Document,
			IndexAssociation: &appointment.Association, IndexPath: "subscription.sport_association"}},
	}
	switch appointment.Region {
	case "agonistica", "non-agonistica":
		return job, nil, nil
	case medicalFormRegion:
	default:
		// MODELS_TEMPLATE_MAP has no form for the region: get_template() answers the default page, which
		// get_pdf_fields opens as a PDF file after the Document was created.
		job.failed = renderException(500, "[Errno 2] No such file or directory: 'document/medical-appointments/non-agonistica.html'")
		return job, nil, nil
	}
	form, err := s.store.AppointmentForm(ctx, id)
	if err != nil {
		reply, err := lookupFailure(err, renderException(500, "'NoneType' object has no attribute 'subscription'"))
		return nil, reply, err
	}
	if form.Associate == nil {
		job.failed = renderException(500, "'NoneType' object has no attribute 'main_tutor'")
		return job, nil, nil
	}
	tutor, err := s.store.AppointmentFormTutor(ctx, *form.Associate)
	if errors.Is(err, domain.ErrViewTutorWrite) {
		// get_main_tutor() would promote a tutor relation (a write).
		return nil, nil, ErrDocumentRenderNotPorted
	}
	if err != nil {
		return nil, nil, err
	}
	values := medicalFormValues(form, tutor)
	if _, signed := values[medicalform.SignatureKey]; signed {
		// fill_pdf_fields draws the stored signature (and stamp) on the first page with PyMuPDF: image processing, not ported.
		return nil, nil, ErrDocumentRenderNotPorted
	}
	job.fill = func() []byte { return medicalform.FillBiella(values) }
	return job, nil, nil
}

// medicalFormRegion is the region of MODELS_TEMPLATE_MAP whose template is a PDF form.
const medicalFormRegion = "agonistica-piemonte-biella"

var (
	// Associate.address_number / address_no_number: r'\d+\s*[A-Za-z]?' on a str (Unicode digits and whitespace).
	medicalFormNumber = regexp.MustCompile(`\p{Nd}+[\t\n\v\f\r\x1c-\x1f \x{85}\x{a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]*[A-Za-z]?`)
)

// medicalFormValues is document_medical_appointment's mapped_fields for a PDF
// form: extract_values over the form's keys (a value is kept when it is not
// empty and its text is not 'None'), each prefixed with two spaces and
// upper-cased unless it contains 'base64', then a value that strips to
// YYYY-MM-DD rewritten as DD/MM/YYYY.
func medicalFormValues(form *domain.RenderAppointmentForm, tutor *domain.RenderFormTutor) map[string]string {
	source := map[string]*string{}
	for key, value := range form.Text {
		source[key] = value
	}
	if form.Address != nil {
		number := medicalFormNumber.FindString(*form.Address)
		rest := dto.PythonStrip(strings.ReplaceAll(medicalFormNumber.ReplaceAllString(*form.Address, ""), ",", ""))
		source["associate.address_number"], source["associate.address_no_number"] = &number, &rest
	} else {
		empty := ""
		source["associate.address_number"] = &empty
	}
	if tutor != nil {
		source["main_tutor.phone"], source["main_tutor.email"] = tutor.Phone, tutor.Email
	}
	// other.today is not a field the writer fills; today_datetime is the appointment's creation date in UTC.
	stamp := form.Created.UTC().Format("02/01/2006 15:04")
	source["other.today_datetime"] = &stamp
	values := map[string]string{}
	for _, key := range medicalform.BiellaKeys() {
		value := source[key]
		if value == nil || *value == "" || *value == "None" {
			continue
		}
		text := *value
		if !strings.Contains(text, "base64") {
			text = djangoauth.PythonUpper(text)
		}
		text = "  " + text
		if stripped := []rune(dto.PythonStrip(text)); len(stripped) == 10 && stripped[4] == '-' && stripped[7] == '-' {
			text = string(stripped[8:10]) + "/" + string(stripped[5:7]) + "/" + string(stripped[0:4])
		}
		values[key] = text
	}
	return values
}
