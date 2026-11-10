package service

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/google/uuid"
)

// ErrMedicalAppointmentAddServerError is an exception the view does not
// handle (data['subscription'] on a list or an immutable QueryDict): 500.
var ErrMedicalAppointmentAddServerError = errors.New("subscription_medical_appointments_add raised")

// ErrMedicalAppointmentPrintDeferred is CoordinatedTask.__call__ called
// directly while the restore operation lock is busy or a restore is pending:
// defer_for_maintenance raises Retry into the view (500 after the save).
var ErrMedicalAppointmentPrintDeferred = errors.New("medical appointment print deferred for maintenance")

// MedicalAppointment is print_document_medical_appointment(id, authorization)
// called directly: the operation lock and pending-restore check only (a
// direct call skips the completed-restore cutoff), then the task body's
// requests.get with every error swallowed. Any admission error propagates.
func (p *InvoiceSyncPrint) MedicalAppointment(ctx context.Context, id string, authorization *string) error {
	ctx = context.WithoutCancel(ctx)
	lease, _, err := p.admission.Acquire(ctx)
	if err != nil {
		if errors.Is(err, domain.ErrRestorePending) || errors.Is(err, domain.ErrRestoreBusy) || errors.Is(err, domain.ErrRestoreVolumeUnavailable) {
			return ErrMedicalAppointmentPrintDeferred
		}
		return err
	}
	defer lease.Close()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, p.host+"/document/medical-appointment/"+id, nil)
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

type MedicalAppointmentPrinter interface {
	MedicalAppointment(ctx context.Context, id string, authorization *string) error
}

// MedicalAppointmentAdd ports subscription_medical_appointments_add.
type MedicalAppointmentAdd struct {
	store   domain.MedicalAppointmentAddStore
	printer MedicalAppointmentPrinter
	now     func() time.Time
}

func NewMedicalAppointmentAdd(store domain.MedicalAppointmentAddStore, printer MedicalAppointmentPrinter, now func() time.Time) *MedicalAppointmentAdd {
	return &MedicalAppointmentAdd{store: store, printer: printer, now: now}
}

// medicalAppointmentAddPythonDateTime is str() of an aware UTC datetime.
func medicalAppointmentAddPythonDateTime(value time.Time) string {
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02 15:04:05+00:00")
	}
	return value.Format("2006-01-02 15:04:05.000000+00:00")
}

func medicalAppointmentAddNaive(value time.Time) string {
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02 15:04:05")
	}
	return value.Format("2006-01-02 15:04:05.000000")
}

// Add: is_valid_uuid, request.user.sport_association, the scoped subscription,
// data['subscription'], MedicalAppointmentsSerializer, save() and the direct
// print call.
func (s *MedicalAppointmentAdd) Add(ctx context.Context, identity domain.ResolvedIdentity, rawID, group string, body any, form bool,
	authorization *string, audit domain.LoginAuditContext) error {
	parsed, err := paymentInfoParseUUID(rawID)
	if err != nil {
		return err
	}
	id := parsed.String()
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	tx, err := s.store.BeginMedicalAppointmentAdd(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return err
	}
	found, err := tx.Subscription(ctx, id, association, paymentCategoryAssociationScope(identity), supplierGroup(group))
	if err != nil {
		return err
	}
	if !found {
		return domain.ErrSubscriptionMedicalNoSubscription
	}
	object, isObject := body.(domain.RequestObject)
	if body == nil {
		object, isObject = domain.RequestObject{}, true
	}
	if !isObject || form {
		return ErrMedicalAppointmentAddServerError
	}
	input, err := dto.ParseMedicalAppointmentAdd(object)
	if err != nil {
		return err
	}
	now := s.now().UTC().Truncate(time.Microsecond)
	row := domain.MedicalAppointmentAddRow{ID: uuid.NewString(), Subscription: id, Region: input.Region, Notes: input.Notes,
		Sport: input.Sport, Address: input.Address, CreationDate: now}
	changes := map[string][2]string{"medical_appointments_id": {"None", row.ID}, "subscription": {"None", id},
		"region": {"None", row.Region}, "document": {"None", "None"}}
	switch {
	case input.Date != nil:
		row.Date = input.Date
		changes["date"] = [2]string{"None", *input.Date}
	case !input.DateNull:
		// DateField default timezone.now(): the aware datetime in memory, its UTC date stored.
		day := now.Format("2006-01-02")
		row.Date = &day
		changes["date"] = [2]string{"None", medicalAppointmentAddPythonDateTime(now)}
	}
	meta := any(domain.RequestObject{})
	if input.MetaPresent {
		meta = input.Meta
	}
	if meta == nil {
		changes["meta"] = [2]string{"None", "null"}
	} else {
		encoded, err := json.Marshal(medicalAppointmentAddJSON(meta))
		if err != nil {
			return err
		}
		text := string(encoded)
		row.Meta = &text
		changes["meta"] = [2]string{"None", string(dto.PythonJSONDumpsSorted(meta))}
	}
	for name, value := range map[string]*string{"notes": row.Notes, "sport": row.Sport, "address": row.Address} {
		if value != nil {
			changes[name] = [2]string{"None", *value}
		}
	}
	if input.CreationDate != nil {
		row.CreationDate = *input.CreationDate
	}
	changes["creation_date"] = [2]string{"None", medicalAppointmentAddNaive(row.CreationDate)}
	row.Changes = changes
	if err = tx.InsertAppointment(ctx, row, association, paymentCategoryAuditContext(identity, audit), now); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	return s.printer.MedicalAppointment(ctx, row.ID, authorization)
}

// medicalAppointmentAddJSON turns a parsed request value into values
// encoding/json writes in the same order (json.Number stays as written).
func medicalAppointmentAddJSON(value any) any {
	switch typed := value.(type) {
	case domain.RequestObject:
		out := make(map[string]any, len(typed))
		for _, field := range typed {
			out[field.Name] = medicalAppointmentAddJSON(field.Value)
		}
		return out
	case []any:
		out := make([]any, len(typed))
		for i, item := range typed {
			out[i] = medicalAppointmentAddJSON(item)
		}
		return out
	}
	return value
}
