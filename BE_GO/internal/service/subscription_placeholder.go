package service

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pilplaceholder"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
)

// SubscriptionPlaceholders draws and stores the placeholder certificate image
// of a typed expiration date without an upload: generate_image_with_text
// (BE/application/utils/api_utils.py) saved as PNG under
// <timestamp>/<document id>/medical_certificate_<first>_<last>.png.
type SubscriptionPlaceholders struct {
	uploads *DocumentUploads
	zlib    pilplaceholder.Zlib
}

// NewSubscriptionPlaceholders stores through uploads; zlib is the deflate
// implementation of the Django side's Pillow (pilplaceholder.ZlibSetting).
func NewSubscriptionPlaceholders(uploads *DocumentUploads, zlib pilplaceholder.Zlib) *SubscriptionPlaceholders {
	return &SubscriptionPlaceholders{uploads: uploads, zlib: zlib}
}

const placeholderNotice = "Attenzione: questo è un placeholder, il certificato medico deve essere caricato in seguito."

// placeholderCreateText is create_subscription's f-string (indented source
// lines); moment is str(make_aware(expiration)).
func placeholderCreateText(first, last, moment string) string {
	const indent = "            "
	return "\n" + indent + first + " " + last + "\n\n" + indent + "\n\n" + indent + "Questa certificazione è valida fino al " + moment +
		"\n\n" + indent + "\n\n" + indent + "\n\n" + indent + placeholderNotice + "\n" + indent + indent
}

// placeholderExpirationText is set-certificate-expiration's f-string.
func placeholderExpirationText(first, last, moment string) string {
	return "\n" + first + " " + last + "\n\n\n\nQuesta certificazione è valida fino al " + moment + "\n\n\n\n\n\n" + placeholderNotice +
		"\n            "
}

// placeholderMoment is str(make_aware(datetime)) of a date at midnight (UTC).
func placeholderMoment(date time.Time) string {
	return subscriptionPythonDateTime(date) + "+00:00"
}

// placeholderFilename is f"medical_certificate_{first}_{last}.png".
func placeholderFilename(first, last string) string {
	return "medical_certificate_" + first + "_" + last + ".png"
}

// plan is generate_image_with_text(text): the PNG Django stores, or the error
// Pillow raises for a text it does not draw (pilplaceholder.ErrUnsupported).
// Every code point the font lacks (a control character included) is drawn as
// its missing-glyph box, as Pillow draws it, so the name is not checked here:
// Django validates the file name at default_storage.save, after the rows it
// writes before it. An unwired drawing (an unknown PIL_PLACEHOLDER_ZLIB) is
// application state.
func (p *SubscriptionPlaceholders) plan(text string) ([]byte, error) {
	if p == nil {
		return nil, domain.ErrServerComposition
	}
	return pilplaceholder.RenderWith(text, p.zlib)
}

// store is default_storage.save(<timestamp>/<id>/<filename>, ContentFile(png)):
// Storage.save's name validation (*documentkey.SuspiciousFileError for a path
// traversal or an absolute name) and the storage write.
func (p *SubscriptionPlaceholders) store(ctx context.Context, created time.Time, id, filename string, image []byte) error {
	if p == nil || p.uploads == nil {
		return domain.ErrServerComposition
	}
	return p.uploads.SaveGenerated(ctx, created, id, filename, image)
}

// storedName is default_storage.save's return value for a placeholder it
// stored: clean_name(name), the key cleanup_storage_keys deletes.
func (p *SubscriptionPlaceholders) storedName(created time.Time, id, filename string) (string, bool) {
	name, err := documentkey.UploadName(created, uuid.MustParse(id), filename)
	return name, err == nil
}

// createPlaceholder is create_subscription's branch for a certificate entry
// without a medical_id: for a parsed date other than today it draws the
// placeholder image, creates its Document, stores the PNG and creates the
// association account's MedicalCertificate with the date; it returns the new
// certificate's id. Every failure inside the view's try block (a value that is
// no date, a missing name, today's date, a text the drawing raises for, a name
// the storage rejects, a storage error) leaves no certificate: the Document the
// view already created is deleted and a saved object removed, and the
// registration goes on without a certificate.
func (s *SubscriptionCreates) createPlaceholder(ctx context.Context, tx domain.SubscriptionCreateTransaction, audit subscriptionAudit,
	data, associate domain.RequestObject, owner string, request SubscriptionCreateRequest) (*string, error) {
	value, _ := data.Lookup("medical_certificate")
	object, isObject := value.(domain.RequestObject)
	if !isObject {
		return nil, nil
	}
	expiration, present := object.Lookup("certificate_expring_date")
	if !present {
		return nil, nil
	}
	date, parsed := subscriptionMedicalDate(expiration)
	if !parsed {
		return nil, nil // strptime: the ValueError the view's try block swallows
	}
	first, hasFirst := associate.Lookup("first_name")
	last, hasLast := associate.Lookup("last_name")
	if !hasFirst || !hasLast {
		return nil, nil // KeyError inside the try
	}
	// The f-string's str() of whatever the request carried.
	firstText, lastText := dto.PythonStr(first), dto.PythonStr(last)
	if date.Format("2006-01-02") == subscriptionCreateToday(s.now()) {
		return nil, nil // an exception, so no certificate
	}
	created := s.now().UTC().Truncate(time.Microsecond)
	document := domain.NewDocument{ID: uuid.NewString(), Token: uuid.NewString(), Filename: placeholderFilename(firstText, lastText), Created: created}
	// generate_image_with_text, before the Document is created.
	image, err := s.placeholders.plan(placeholderCreateText(firstText, lastText, placeholderMoment(date)))
	if err != nil {
		if errors.Is(err, domain.ErrServerComposition) {
			return nil, err
		}
		return nil, nil // the drawing's exception is inside the try
	}
	actorAssociation, err := s.actorAssociation(ctx, tx, request.Audit)
	if err != nil {
		return nil, err
	}
	// Document.objects.create: a database error (a name over the column's 255
	// characters, text the connection cannot encode) breaks the transaction, and
	// the view's except block then fails on its next query (the 500 page).
	if err = tx.CreatePlaceholderDocument(ctx, document, actorAssociation, request.Audit, created); err != nil {
		return nil, err
	}
	// default_storage.save's failures: the except block deletes the Document and
	// the registration goes on without a certificate.
	if err = s.placeholders.store(ctx, created, document.ID, document.Filename, image); err != nil {
		if errors.Is(err, domain.ErrServerComposition) {
			return nil, err
		}
		return nil, s.abandonPlaceholder(ctx, tx, document, actorAssociation, request)
	}
	name, stored := s.placeholders.storedName(created, document.ID, document.Filename)
	id := uuid.NewString()
	if err = tx.InsertPlaceholderCertificate(ctx, id, owner, document.ID, date, s.now().UTC().Truncate(time.Microsecond)); err != nil {
		// cleanup_storage_keys([saved_file]) runs before the except block's own
		// failing query (document.delete() on the broken transaction).
		if stored {
			s.cleanupStorage(ctx, []string{name})
		}
		return nil, err
	}
	// The parsed datetime assigned to the DateField: auditlog renders str(datetime).
	// With it the certificate's create is complete; Django appends saved_file to
	// created_storage_keys only now.
	if err = audit.created(ctx, "medicalcertificate", "MedicalCertificate", "application_medicalcertificate", "medical_id", id,
		map[string][2]string{"expiration_date": {"None", subscriptionPythonDateTime(date)}}, actorAssociation, "actor.sport_association"); err != nil {
		return nil, err
	}
	if stored && request.StorageKeys != nil {
		*request.StorageKeys = append(*request.StorageKeys, name)
	}
	return &id, nil
}

// abandonPlaceholder is create_subscription's except block for a placeholder
// the storage did not take: saved_file is None, so cleanup_storage_keys has
// nothing to remove; the Document the view created is deleted with its entry,
// and the registration continues without a certificate.
func (s *SubscriptionCreates) abandonPlaceholder(ctx context.Context, tx domain.SubscriptionCreateTransaction, document domain.NewDocument,
	actorAssociation *string, request SubscriptionCreateRequest) error {
	return tx.DeletePlaceholderDocument(ctx, document, actorAssociation, request.Audit, s.now().UTC().Truncate(time.Microsecond))
}
