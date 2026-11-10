package service

import (
	"context"
	"errors"
	"io"
	"math"
	"math/big"
	"net/http"
	"sync"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/fatturapa"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrInvoiceSyncPrintDeferred is CoordinatedTask's maintenance deferral of an
// eager task (operation lock busy or unavailable, or a pending restore):
// Django re-applies the task synchronously; not ported (the 500 page).
var ErrInvoiceSyncPrintDeferred = refusal.New("print task deferred for maintenance")

// invoiceSyncUserAgent and the other headers are requests' defaults
// (requests 2.34.2, urllib3 without brotli/zstd: 'gzip, deflate').
const invoiceSyncUserAgent = "python-requests/2.34.2"

// InvoiceSyncPrint runs print_document_invoice / print_document_customer_invoice
// the way `.apply(args=[id, authorization])` does inside the request: the
// CoordinatedTask admission (DATA_RESTORE_LOCK_PATH), then the task body,
// requests.get(f"{CURRENT_HOST}{path}", headers={'Authorization': token})
// with every error swallowed.
type InvoiceSyncPrint struct {
	admission JobAdmission
	host      string
	client    *http.Client
}

func NewInvoiceSyncPrint(admission JobAdmission, host string) *InvoiceSyncPrint {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.DisableCompression = true
	client := &http.Client{Transport: transport, CheckRedirect: func(request *http.Request, via []*http.Request) error {
		// requests follows at most 30 redirects (Session.max_redirects) and sends no Referer.
		if len(via) > 30 {
			return errors.New("too many redirects")
		}
		request.Header.Del("Referer")
		return nil
	}}
	return &InvoiceSyncPrint{admission: admission, host: host, client: client}
}

// apply is the eager CoordinatedTask call. A pending restore or a busy lock
// defers (ErrInvoiceSyncPrintDeferred); a completed restore makes the eager
// call stale (StaleDataTask, kept in the eager result: no request); any
// other admission error is likewise kept in the result.
func (p *InvoiceSyncPrint) apply(ctx context.Context, path string, authorization *string) error {
	ctx = context.WithoutCancel(ctx)
	lease, cutoff, err := p.admission.Acquire(ctx)
	if err != nil {
		if errors.Is(err, domain.ErrRestorePending) || errors.Is(err, domain.ErrRestoreBusy) || errors.Is(err, domain.ErrRestoreVolumeUnavailable) {
			return ErrInvoiceSyncPrintDeferred
		}
		return nil
	}
	defer lease.Close()
	if cutoff != nil {
		return nil
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, p.host+path, nil)
	if err != nil {
		return nil
	}
	request.Header = http.Header{"User-Agent": {invoiceSyncUserAgent}, "Accept-Encoding": {"gzip, deflate"}, "Accept": {"*/*"},
		"Connection": {"keep-alive"}}
	if authorization != nil {
		request.Header["Authorization"] = []string{*authorization}
	}
	handoff.MarkEffect(ctx, "print request")
	response, err := p.client.Do(request)
	if err != nil {
		return nil
	}
	_, _ = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	return nil
}

// Invoice is print_document_invoice.apply(args=[str(invoice_id), authorization]).
func (p *InvoiceSyncPrint) Invoice(ctx context.Context, id string, authorization *string) error {
	return p.apply(ctx, "/document/invoice/"+id+"?send_receipt_email=False", authorization)
}

// Compensation is print_document_compensation.apply(args=[str(payment_id), authorization]).
func (p *InvoiceSyncPrint) Compensation(ctx context.Context, payment string, authorization *string) error {
	return p.apply(ctx, "/document/compensation/"+payment, authorization)
}

// CustomerInvoice is print_document_customer_invoice.apply(args=[str(id), authorization]).
func (p *InvoiceSyncPrint) CustomerInvoice(ctx context.Context, id string, authorization *string) error {
	return p.apply(ctx, "/document/einvoice/"+id, authorization)
}

// invoiceSyncProgressivo is a38's ProgressivoInvioField default: one counter
// per process, reset each second ((int(time.time()) << 16) + sequence).
var invoiceSyncProgressivo struct {
	sync.Mutex
	set      bool
	last     int64
	sequence int
}

func invoiceSyncNextProgressivo(now time.Time) (string, error) {
	invoiceSyncProgressivo.Lock()
	defer invoiceSyncProgressivo.Unlock()
	ts := now.Unix()
	if !invoiceSyncProgressivo.set || invoiceSyncProgressivo.last != ts {
		invoiceSyncProgressivo.set, invoiceSyncProgressivo.last, invoiceSyncProgressivo.sequence = true, ts, 0
	} else {
		invoiceSyncProgressivo.sequence++
		if invoiceSyncProgressivo.sequence > 64*64*64 {
			return "", fatturapa.ErrInvalid // OverflowError inside generate_einvoice
		}
	}
	return fatturapa.Progressivo(ts, invoiceSyncProgressivo.sequence), nil
}

// --- invoice/<uid>/update -------------------------------------------------------

// InvoiceSyncUpdates ports invoice_update.
type InvoiceSyncUpdates struct {
	store   domain.InvoiceSyncStore
	printer *InvoiceSyncPrint
	now     func() time.Time
}

func NewInvoiceSyncUpdates(store domain.InvoiceSyncStore, printer *InvoiceSyncPrint, now func() time.Time) *InvoiceSyncUpdates {
	return &InvoiceSyncUpdates{store: store, printer: printer, now: now}
}

// InvoiceSyncUpdateRequest carries what invoice_update reads besides the identity.
type InvoiceSyncUpdateRequest struct {
	UID           string
	Body          any
	Group         string
	Authorization *string
	Audit         domain.LoginAuditContext
}

func invoiceSyncPythonStr(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func invoiceSyncNumberStr(value *int64) string {
	if value == nil {
		return "None"
	}
	return big.NewInt(*value).String()
}

// Update follows invoice_update: nothing is atomic, so the payment save is
// committed before the description (a KeyError there is a 500 with the payment
// saved) and the invoice's audit entry before its UPDATE (a value the column
// rejects is a 500 with the entry kept).
func (s *InvoiceSyncUpdates) Update(ctx context.Context, identity domain.ResolvedIdentity, request InvoiceSyncUpdateRequest) (map[string]any, error) {
	id, err := messageReadsParseUUID(request.UID)
	if err != nil {
		return nil, err
	}
	// data.keys(): a list or string body is an AttributeError.
	object, ok := request.Body.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrPaymentStateServer
	}
	for _, field := range object {
		if _, upload := field.Value.(domain.RequestUpload); upload {
			return nil, domain.ErrPaymentStateNotPorted
		}
	}
	numberValue, present := object.Lookup("number")
	if !present {
		return nil, domain.ErrPaymentStateServer
	}
	cancelledValue, _ := object.Lookup("cancelled")
	var paymentDate *time.Time
	if value, present := object.Lookup("payment_date"); present {
		if text, ok := value.(string); ok {
			if parsed, ok := dto.PythonStrptimeDate(text); ok {
				paymentDate = &parsed
			}
		}
	}
	scope, group := paymentStateScope(identity), supplierGroup(request.Group)
	tx, err := s.store.BeginInvoiceSync(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := tx.ScopedAssociation(ctx, identity.ScopeUser.ID, scope)
	if err != nil {
		if errors.Is(err, domain.ErrAssociationNotFound) {
			return nil, domain.ErrPaymentStateServer
		}
		return nil, err
	}
	invoice, err := tx.SyncInvoice(ctx, id, association, scope, group)
	if err != nil {
		return nil, err
	}
	if invoice == nil {
		return nil, domain.ErrPaymentStateServer
	}
	number, result := dto.PythonInt(numberValue)
	if result != dto.PythonIntOK {
		return nil, domain.ErrPaymentStateServer
	}
	save := domain.InvoiceSyncSave{ID: invoice.ID, Cancelled: dto.PythonTruthy(cancelledValue), Description: invoice.Description}
	if tutor, present := object.Lookup("selected_tutor"); present {
		save.SelectedTutorID = invoice.SelectedTutorID
		if tutor != nil {
			// UUIDField.to_python of the filter value: a rejected value is a ValidationError.
			tutorID, ok := dto.DjangoUUIDLookup(tutor)
			if !ok {
				return nil, domain.ErrPaymentStateServer
			}
			found, err := tx.SyncAssociate(ctx, tutorID, scope, group)
			if err != nil {
				return nil, err
			}
			if found {
				save.SelectedTutorID = &tutorID
			}
		}
	}
	at := s.now().UTC().Truncate(time.Microsecond)
	audit := paymentCategoryAuditContext(identity, request.Audit)
	payment, err := tx.InvoicePayment(ctx, invoice.ID, scope, group)
	if err != nil {
		return nil, err
	}
	var describeErr error
	if payment != nil {
		changes := map[string][2]string{}
		if paymentDate != nil {
			if before, after := courseSubscriptionAuditTime(payment.PaymentDate), paymentDate.Format("2006-01-02 15:04:05"); before != after {
				changes["payment_date"] = [2]string{before, after}
			}
			payment.PaymentDate = paymentDate
		}
		if err = paymentSaveDefaults(ctx, tx, &payment.PaymentSaveRow, changes); err != nil {
			if errors.Is(err, ErrPaymentSaveFailed) {
				return nil, domain.ErrPaymentStateServer
			}
			return nil, err
		}
		details, err := tx.InvoiceGeneratePayment(ctx, payment.ID)
		if err != nil {
			return nil, err
		}
		description, err := (&InvoiceGenerations{}).description(ctx, tx, payment, details, payment.AssociateID, association, group)
		switch {
		case errors.Is(err, domain.ErrPaymentStateNotPorted):
			return nil, err
		case errors.Is(err, domain.ErrPaymentStateServer):
			describeErr = err
		case err != nil:
			return nil, err
		default:
			save.Description = &description
		}
		if err = tx.SaveInvoicePayment(ctx, payment.ID, payment.PaymentDate, payment.CategoryID, payment.Description); err != nil {
			return nil, err
		}
		if len(changes) > 0 {
			if err = tx.AppendPaymentAudit(ctx, domain.PaymentArchiveEvent{PaymentID: payment.ID, AssociationID: payment.AssociationID,
				Changes: changes, Context: audit, At: at}); err != nil {
				return nil, err
			}
		}
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	if describeErr != nil {
		return nil, describeErr
	}
	changes := map[string][2]string{}
	if before, after := invoiceSyncNumberStr(invoice.Number), number.String(); before != after {
		changes["number"] = [2]string{before, after}
	}
	if invoice.Cancelled != save.Cancelled {
		changes["cancelled"] = [2]string{pythonBool(invoice.Cancelled), pythonBool(save.Cancelled)}
	}
	if before, after := invoiceSyncPythonStr(invoice.SelectedTutorID), invoiceSyncPythonStr(save.SelectedTutorID); before != after {
		changes["selected_tutor"] = [2]string{before, after}
	}
	if before, after := invoiceSyncPythonStr(invoice.Description), invoiceSyncPythonStr(save.Description); before != after {
		changes["description"] = [2]string{before, after}
	}
	if len(changes) > 0 {
		if err = s.store.AppendInvoiceAudit(ctx, invoice.ID, invoice.AssociationID, changes, audit, at); err != nil {
			return nil, err
		}
	}
	// The integer column rejects numbers outside int32 after the entry is written.
	if !number.IsInt64() || number.Int64() < math.MinInt32 || number.Int64() > math.MaxInt32 {
		return nil, domain.ErrPaymentStateServer
	}
	save.Number = number.Int64()
	if err = s.store.SaveSyncInvoice(ctx, save); err != nil {
		return nil, err
	}
	if err = s.printer.Invoice(ctx, invoice.ID, request.Authorization); err != nil {
		return nil, err
	}
	row, err := s.store.RenderInvoice(ctx, invoice.ID)
	if err != nil {
		return nil, err
	}
	item, err := dto.InvoiceCoreInvoice(*row)
	if err != nil {
		return nil, err
	}
	return map[string]any{"message": "Invoice updated", "invoice": item}, nil
}
