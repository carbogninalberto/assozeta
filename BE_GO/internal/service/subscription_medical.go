package service

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// SubscriptionMedical ports the subscription/<uid> medical-appointments list
// and delete and the medical-certificate edit and set-certificate-expiration
// function views (subscriptions_views.py).
type SubscriptionMedical struct {
	store        domain.SubscriptionMedicalStore
	now          func() time.Time
	ids          func() string
	placeholders *SubscriptionPlaceholders
	printer      MedicalAppointmentPrinter
}

func NewSubscriptionMedical(store domain.SubscriptionMedicalStore, now func() time.Time, ids func() string) *SubscriptionMedical {
	return &SubscriptionMedical{store: store, now: now, ids: ids}
}

// WithPlaceholders enables the placeholder certificate set-certificate-expiration
// creates for a subscription without one.
func (s *SubscriptionMedical) WithPlaceholders(placeholders *SubscriptionPlaceholders) *SubscriptionMedical {
	s.placeholders = placeholders
	return s
}

// WithAppointmentPrinter sets the synchronous print the list runs for an
// appointment without a document.
func (s *SubscriptionMedical) WithAppointmentPrinter(printer MedicalAppointmentPrinter) *SubscriptionMedical {
	s.printer = printer
	return s
}

var (
	// ErrSubscriptionMedicalFailed is an exception the views do not handle (a 500).
	ErrSubscriptionMedicalFailed = errors.New("subscription medical request failed")
	// ErrSubscriptionMedicalNotPorted refuses, before any write, what the Go
	// port does not reproduce: printing an appointment without a document
	// (print_document_medical_appointment) and the placeholder certificate
	// image a subscription without a certificate would get uploaded.
	ErrSubscriptionMedicalNotPorted = refusal.New("subscription medical behaviour not ported")
)

// medicalAppointmentRegions is MedicalAppointments.REGIONS.
var medicalAppointmentRegions = map[string]string{
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

func (s *SubscriptionMedical) at() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// ParseUUID is is_valid_uuid: a 400 with Python's message.
func (s *SubscriptionMedical) ParseUUID(raw string) (string, error) {
	parsed, err := paymentInfoParseUUID(raw)
	if err != nil {
		return "", err
	}
	return parsed.String(), nil
}

// owner resolves request.user.sport_association: athletes are refused, a
// missing association is a 500.
func (s *SubscriptionMedical) owner(ctx context.Context, identity domain.ResolvedIdentity) (domain.SubscriptionMedicalTransaction, string, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, "", &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	tx, err := s.store.BeginSubscriptionMedical(ctx)
	if err != nil {
		return nil, "", err
	}
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		tx.Rollback()
		return nil, "", err
	}
	return tx, association, nil
}

// List follows subscription_medical_appointments_list.
func (s *SubscriptionMedical) List(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, authorization *string) (
	domain.RequestObject, error) {
	id, err := s.ParseUUID(raw)
	if err != nil {
		return nil, err
	}
	tx, association, err := s.owner(ctx, identity)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	found, err := tx.AppointmentSubscription(ctx, id, association, paymentCategoryAssociationScope(identity), supplierGroup(group))
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, domain.ErrSubscriptionMedicalNoSubscription
	}
	rows, err := tx.Appointments(ctx, id)
	if err != nil {
		return nil, err
	}
	for _, row := range rows {
		if row.Document == nil && s.printer == nil {
			return nil, ErrSubscriptionMedicalNotPorted
		}
	}
	// The rows are read: the prints below run outside the read transaction.
	if err = tx.Rollback(); err != nil {
		return nil, err
	}
	data := domain.RequestObject{}
	for index, row := range rows {
		if row.Document == nil {
			// print_document_medical_appointment(id, authorization) called directly; the
			// instance read before it is serialized (no document).
			if err = s.printer.MedicalAppointment(ctx, row.ID, authorization); err != nil {
				return nil, err
			}
		}
		region, known := medicalAppointmentRegions[row.Region]
		if !known {
			return nil, ErrSubscriptionMedicalFailed // REGIONS[obj.region]
		}
		var meta any
		if row.Meta != nil {
			if meta, err = dto.ParseProfileTablesJSON(row.Meta); err != nil {
				return nil, err
			}
		}
		data = append(data, domain.RequestField{Name: strconv.Itoa(index), Value: domain.RequestObject{
			{Name: "medical_appointments_id", Value: row.ID}, {Name: "document_token", Value: optionalValue(row.Token)},
			{Name: "appointment_region", Value: region}, {Name: "region", Value: row.Region}, {Name: "date", Value: optionalValue(row.Date)},
			{Name: "notes", Value: optionalValue(row.Notes)}, {Name: "sport", Value: optionalValue(row.Sport)}, {Name: "meta", Value: meta},
			{Name: "address", Value: optionalValue(row.Address)}, {Name: "creation_date", Value: dto.DRFUTCDateTime(row.Created)},
			{Name: "subscription", Value: row.Subscription}, {Name: "document", Value: optionalValue(row.Document)}}})
	}
	return domain.RequestObject{{Name: "data", Value: data}}, nil
}

func optionalValue(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

// Delete follows subscription_medical_appointments_delete.
func (s *SubscriptionMedical) Delete(ctx context.Context, identity domain.ResolvedIdentity, rawSubscription, rawAppointment string,
	audit domain.LoginAuditContext) error {
	subscription, err := s.ParseUUID(rawSubscription)
	if err != nil {
		return err
	}
	appointment, err := s.ParseUUID(rawAppointment)
	if err != nil {
		return err
	}
	tx, association, err := s.owner(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	found, err := tx.LockAppointment(ctx, appointment, subscription, association)
	if err != nil {
		return err
	}
	if !found {
		return domain.ErrSubscriptionMedicalNoAppointment
	}
	actorAssociation, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	if err = tx.DeleteAppointment(ctx, appointment, actorAssociation, paymentCategoryAuditContext(identity, audit), s.at()); err != nil {
		return err
	}
	return tx.Commit()
}

func medicalScope(identity domain.ResolvedIdentity, group string) domain.MedicalTargetScope {
	return domain.MedicalTargetScope{Impersonation: paymentCategoryAssociationScope(identity), Athlete: identity.ScopeUser.Role == domain.RoleAthlete,
		User: identity.ScopeUser.ID, Group: supplierGroup(group)}
}

// medicalRequest is one certificate request's transaction and audit state.
// fail commits the writes so far (requests are not atomic) and answers 500.
type medicalRequest struct {
	tx               domain.SubscriptionMedicalTransaction
	identity         domain.ResolvedIdentity
	audit            domain.LoginAuditContext
	actorAssociation *string
	group            *string
	service          *SubscriptionMedical
}

func (r *medicalRequest) fail() error {
	if err := r.tx.Commit(); err != nil {
		return err
	}
	return ErrSubscriptionMedicalFailed
}

func (s *SubscriptionMedical) begin(ctx context.Context, identity domain.ResolvedIdentity, group string,
	audit domain.LoginAuditContext) (*medicalRequest, error) {
	tx, err := s.store.BeginSubscriptionMedical(ctx)
	if err != nil {
		return nil, err
	}
	actorAssociation, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		tx.Rollback()
		return nil, err
	}
	return &medicalRequest{tx: tx, identity: identity, audit: paymentCategoryAuditContext(identity, audit), actorAssociation: actorAssociation,
		group: supplierGroup(group), service: s}, nil
}

// copyCertificate is MedicalCertificate.objects.create(...) with the source's values.
func (r *medicalRequest) copyCertificate(ctx context.Context, source domain.MedicalCertificateRow) (*domain.MedicalCertificateRow, error) {
	copied := source
	copied.ID = r.service.ids()
	if err := r.tx.CopyCertificate(ctx, copied.ID, source, r.actorAssociation, r.audit, r.service.at()); err != nil {
		return nil, err
	}
	return &copied, nil
}

// attach is subscription.medical = certificate; save(update_fields=['medical']):
// Subscription.save() computes the season range first (a 500 that keeps the copy).
func (r *medicalRequest) attach(ctx context.Context, subscription *domain.MedicalSubscription, certificate *domain.MedicalCertificateRow) error {
	if _, err := ResolveSubscriptionDates(ctx, subscription.Target.Created, subscription.Target.Rule, subscription.Target.Dates); err != nil {
		if commit := r.tx.Commit(); commit != nil {
			return commit
		}
		return errors.Join(ErrSubscriptionMedicalFailed, err)
	}
	if err := r.tx.SetSubscriptionMedical(ctx, subscription.ID, subscription.Association, subscription.Certificate.ID, &certificate.ID, r.audit,
		r.service.at()); err != nil {
		return err
	}
	subscription.Certificate = certificate
	return nil
}

// certificateForUpdate is _medical_certificate_for_update (with
// isolate_medical_certificate during an association-scoped impersonation).
func (r *medicalRequest) certificateForUpdate(ctx context.Context, subscription *domain.MedicalSubscription) (*domain.MedicalCertificateRow, error) {
	certificate := subscription.Certificate
	if scope := paymentCategoryAssociationScope(r.identity); scope != nil {
		shared, err := r.tx.SharedElsewhere(ctx, certificate.ID, *scope, r.group)
		if err != nil {
			return nil, err
		}
		if shared {
			if certificate, err = r.copyCertificate(ctx, *certificate); err != nil {
				return nil, err
			}
			if err = r.attach(ctx, subscription, certificate); err != nil {
				return nil, err
			}
		}
	}
	foreign, err := r.tx.ForeignReference(ctx, certificate.ID, subscription.Association)
	if err != nil {
		return nil, err
	}
	if foreign {
		if certificate, err = r.copyCertificate(ctx, *certificate); err != nil {
			return nil, err
		}
		if err = r.attach(ctx, subscription, certificate); err != nil {
			return nil, err
		}
	}
	return certificate, nil
}

// pythonContains is `key in data` for a parsed body that is not a dict: a
// list compares its items, a str looks for a substring, anything else raises.
func pythonContains(body any, key string) (bool, error) {
	switch value := body.(type) {
	case []any:
		for _, item := range value {
			if text, ok := item.(string); ok && text == key {
				return true, nil
			}
		}
		return false, nil
	case string:
		return strings.Contains(value, key), nil
	}
	return false, ErrSubscriptionMedicalFailed
}

// Edit follows subscription_medical_certificate_edit after is_valid_uuid and request.data.
func (s *SubscriptionMedical) Edit(ctx context.Context, identity domain.ResolvedIdentity, id, group string, body any,
	audit domain.LoginAuditContext) error {
	r, err := s.begin(ctx, identity, group, audit)
	if err != nil {
		return err
	}
	defer r.tx.Rollback()
	subscription, err := r.tx.Subscription(ctx, id, medicalScope(identity, group))
	if err != nil {
		return err
	}
	if subscription == nil {
		return domain.ErrSubscriptionMedicalNotFound
	}
	if subscription.Certificate == nil {
		return domain.ErrSubscriptionMedicalNoCertificate
	}
	certificate, err := r.certificateForUpdate(ctx, subscription)
	if err != nil {
		return err
	}
	notes, competitive := certificate.Notes, certificate.Competitive
	if object, isObject := body.(domain.RequestObject); isObject {
		if value, present := object.Lookup("notes"); present {
			notes = nil
			if value != nil {
				stored := dto.PythonStr(value) // TextField.to_python: str() of any other value
				if !dto.AssociateDraftStorable(stored) {
					return r.fail() // PostgreSQL refuses NUL characters and lone surrogates
				}
				notes = &stored
			}
		}
		if value, present := object.Lookup("competitive_medical_certificate"); present {
			if flag, isBool := value.(bool); isBool {
				competitive = flag
			}
		}
	} else {
		for _, key := range []string{"notes", "competitive_medical_certificate"} {
			present, err := pythonContains(body, key)
			if err != nil || present {
				return r.fail() // `in` on a non-container, or data[key] on a list or str
			}
		}
	}
	if err = r.tx.SaveCertificate(ctx, *certificate, notes, competitive, r.actorAssociation, r.audit, s.at()); err != nil {
		return err
	}
	return r.tx.Commit()
}

// ExpirationTarget is set-certificate-expiration's lookup before request.data
// is read: ErrSubscriptionMedicalNotFound without a subscription or draft.
func (s *SubscriptionMedical) ExpirationTarget(ctx context.Context, identity domain.ResolvedIdentity, id, group string) error {
	tx, err := s.store.BeginSubscriptionMedical(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	_, _, err = s.expirationTarget(ctx, tx, identity, id, group)
	return err
}

func (s *SubscriptionMedical) expirationTarget(ctx context.Context, tx domain.SubscriptionMedicalTransaction, identity domain.ResolvedIdentity, id,
	group string) (*domain.MedicalSubscription, *domain.MedicalDraft, error) {
	scope := medicalScope(identity, group)
	subscription, err := tx.Subscription(ctx, id, scope)
	if err != nil || subscription != nil {
		return subscription, nil, err
	}
	draft, err := tx.Draft(ctx, id, scope)
	if err != nil {
		return nil, nil, err
	}
	if draft == nil {
		return nil, nil, domain.ErrSubscriptionMedicalNotFound
	}
	return nil, draft, nil
}

// SetExpiration follows subscription_medical_certificate_set_certificate_expiration
// and returns the response message.
func (s *SubscriptionMedical) SetExpiration(ctx context.Context, identity domain.ResolvedIdentity, id, group string, body any,
	audit domain.LoginAuditContext) (string, error) {
	r, err := s.begin(ctx, identity, group, audit)
	if err != nil {
		return "", err
	}
	defer r.tx.Rollback()
	subscription, draft, err := s.expirationTarget(ctx, r.tx, identity, id, group)
	if err != nil {
		return "", err
	}
	object, isObject := body.(domain.RequestObject)
	if !isObject {
		return "", ErrSubscriptionMedicalFailed // request.data[...] on a non-dict
	}
	expiration, present := object.Lookup("certificate_expiring_date")
	if !present {
		return "", ErrSubscriptionMedicalFailed // KeyError
	}
	if draft != nil {
		return r.draftExpiration(ctx, *draft, expiration)
	}
	if subscription.Certificate == nil {
		return r.placeholderExpiration(ctx, subscription, expiration)
	}
	certificate, err := r.certificateForUpdate(ctx, subscription)
	if err != nil {
		return "", err
	}
	if expiration == nil {
		return r.remove(ctx, subscription, certificate)
	}
	date, parsed := dto.ParseSubscriptionMembershipDate(expiration)
	if !parsed {
		return "", r.fail() // strptime(value, '%d/%m/%Y')
	}
	if err = r.tx.SaveExpiration(ctx, *certificate, date, r.actorAssociation, r.audit, s.at()); err != nil {
		return "", err
	}
	return "Medical certificate update with date " + date + " 00:00:00+00:00", r.tx.Commit()
}

// placeholderExpiration is the view's branch for a subscription without a
// certificate: MedicalCertificate.objects.create(user=request.user), the
// placeholder image and its Document, the stored PNG, subscription.save() with
// the new certificate, then the certificate's save() with the document and the
// date. The request is not atomic: a missing member, a value that is no
// '%d/%m/%Y' date (None included) or a failed storage write answers 500 and
// keeps the rows written before it.
func (r *medicalRequest) placeholderExpiration(ctx context.Context, subscription *domain.MedicalSubscription, expiration any) (string, error) {
	s := r.service
	date, parsed := "", false
	if expiration != nil {
		date, parsed = dto.ParseSubscriptionMembershipDate(expiration)
	}
	first, last, found, err := r.tx.SubscriptionMemberNames(ctx, subscription.ID)
	if err != nil {
		return "", err
	}
	created := s.at()
	document := domain.NewDocument{ID: s.ids(), Token: s.ids(), Created: created}
	var image []byte
	if found && parsed {
		if first == nil || last == nil {
			return "", ErrSubscriptionMedicalNotPorted // str(None) in the file name and the image
		}
		document.Filename = placeholderFilename(*first, *last)
		planned, ok := s.placeholders.plan(placeholderExpirationText(*first, *last, date+" 00:00:00+00:00"), document.Filename, created,
			document.ID)
		if !ok {
			return "", ErrSubscriptionMedicalNotPorted
		}
		image = planned
	}
	certificate := domain.MedicalCertificateRow{ID: s.ids()}
	if err = r.tx.CreateCertificate(ctx, certificate.ID, r.identity.ScopeUser.ID, r.actorAssociation, r.audit, s.at()); err != nil {
		return "", err
	}
	if !found || !parsed {
		return "", r.fail() // None.first_name, or strptime(value, '%d/%m/%Y')
	}
	if err = r.tx.CreatePlaceholderDocument(ctx, document, r.actorAssociation, r.audit, created); err != nil {
		return "", err
	}
	if err = s.placeholders.store(ctx, created, document.ID, document.Filename, image); err != nil {
		return "", errors.Join(r.fail(), err)
	}
	dates, err := ResolveSubscriptionDates(ctx, subscription.Target.Created, subscription.Target.Rule, subscription.Target.Dates)
	if err != nil {
		return "", errors.Join(r.fail(), err)
	}
	if err = r.tx.AttachNewCertificate(ctx, *subscription, certificate.ID, dates, r.audit, s.at()); err != nil {
		return "", err
	}
	if err = r.tx.SavePlaceholderCertificate(ctx, certificate.ID, document.ID, date, r.actorAssociation, r.audit, s.at()); err != nil {
		return "", err
	}
	return "Medical certificate update with date " + date + " 00:00:00+00:00", r.tx.Commit()
}

// remove detaches the certificate (Subscription.save() fills missing dates)
// and, outside an association-scoped impersonation, deletes it and its
// document when nothing else references the document.
func (r *medicalRequest) remove(ctx context.Context, subscription *domain.MedicalSubscription, certificate *domain.MedicalCertificateRow) (string, error) {
	dates, err := ResolveSubscriptionDates(ctx, subscription.Target.Created, subscription.Target.Rule, subscription.Target.Dates)
	if err != nil {
		return "", errors.Join(r.fail(), err)
	}
	if err = r.tx.DetachSubscription(ctx, *subscription, dates, r.audit, r.service.at()); err != nil {
		return "", err
	}
	if paymentCategoryAssociationScope(r.identity) == nil {
		if err = r.tx.DeleteCertificate(ctx, certificate.ID, r.actorAssociation, r.audit, r.service.at()); err != nil {
			return "", err
		}
		if certificate.Document != nil {
			if err = r.tx.DeleteUnreferencedDocument(ctx, *certificate.Document, r.actorAssociation, r.audit, r.service.at()); err != nil {
				return "", err
			}
		}
	}
	return "Medical certificate removed", r.tx.Commit()
}

// draftExpiration is the view's draft branch: the certificate entry is
// added and saved when missing, the certificate is found by medical_id and
// uploader (copied when another association references it, created when
// missing), dated, and written back into the draft.
func (r *medicalRequest) draftExpiration(ctx context.Context, draft domain.MedicalDraft, expiration any) (string, error) {
	if draft.Data == nil {
		return "", ErrSubscriptionMedicalFailed // None.keys()
	}
	parsed, err := dto.ParseProfileTablesJSON(draft.Data)
	if err != nil {
		return "", err
	}
	data, isObject := parsed.(domain.RequestObject)
	if !isObject {
		return "", ErrSubscriptionMedicalFailed // .keys() on a non-dict
	}
	save := func() error {
		if err := r.tx.SaveDraftData(ctx, draft, dto.PythonJSONDumps(data), dto.PythonJSONDumpsSorted(data), r.audit, r.service.at()); err != nil {
			return err
		}
		draft.Data = dto.PythonJSONDumps(data)
		return nil
	}
	if _, present := data.Lookup("medical_certificate"); !present {
		data = dto.SetRequestField(data, "medical_certificate", domain.RequestObject{
			{Name: "medical_id", Value: nil}, {Name: "filename", Value: nil}, {Name: "certificate_expring_date", Value: nil}})
		if err = save(); err != nil {
			return "", err
		}
	}
	value, _ := data.Lookup("medical_certificate")
	medical, isObject := value.(domain.RequestObject)
	if !isObject {
		return "", r.fail() // .get() on a non-dict
	}
	var certificate *domain.MedicalCertificateRow
	if medicalID, _ := medical.Lookup("medical_id"); medicalID != nil {
		lookup, err := djangoUUIDValue(medicalID)
		if err != nil {
			return "", r.fail()
		}
		if certificate, err = r.tx.Certificate(ctx, lookup, r.identity.ScopeUser.ID); err != nil {
			return "", err
		}
	}
	if certificate != nil {
		foreign, err := r.tx.ForeignReference(ctx, certificate.ID, draft.Association)
		if err != nil {
			return "", err
		}
		if foreign {
			if certificate, err = r.copyCertificate(ctx, *certificate); err != nil {
				return "", err
			}
		}
	}
	if certificate == nil {
		certificate = &domain.MedicalCertificateRow{ID: r.service.ids()}
		if err = r.tx.CreateCertificate(ctx, certificate.ID, r.identity.ScopeUser.ID, r.actorAssociation, r.audit, r.service.at()); err != nil {
			return "", err
		}
	}
	date, parsedDate := dto.ParseSubscriptionMembershipDate(expiration)
	if !parsedDate {
		return "", r.fail()
	}
	if err = r.tx.SaveExpiration(ctx, *certificate, date, r.actorAssociation, r.audit, r.service.at()); err != nil {
		return "", err
	}
	medical = dto.SetRequestField(medical, "medical_id", certificate.ID)
	medical = dto.SetRequestField(medical, "certificate_expring_date", expiration)
	data = dto.SetRequestField(data, "medical_certificate", medical)
	if err = save(); err != nil {
		return "", err
	}
	return "Medical certificate update with date " + date + " 00:00:00+00:00", r.tx.Commit()
}
