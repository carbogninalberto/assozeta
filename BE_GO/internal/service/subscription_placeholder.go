package service

import (
	"context"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
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

// plan checks, before any write, that the image can be drawn and its file
// stored under the name Django would use: the PNG, or false for the inputs
// that are refused (a name the storage or the filename column rejects, a text
// the image port does not draw).
func (p *SubscriptionPlaceholders) plan(text, filename string, created time.Time, id string) ([]byte, bool) {
	if p == nil || p.uploads == nil || utf8.RuneCountInString(filename) > 255 || !utf8.ValidString(filename) {
		return nil, false
	}
	for _, r := range filename {
		if r < 0x20 || r == 0x7f {
			return nil, false
		}
	}
	if p.uploads.GeneratedName(created, id, filename) != nil {
		return nil, false
	}
	image, err := pilplaceholder.RenderWith(text, p.zlib)
	if err != nil {
		return nil, false
	}
	return image, true
}

// store is default_storage.save(<timestamp>/<id>/<filename>, ContentFile(png)).
func (p *SubscriptionPlaceholders) store(ctx context.Context, created time.Time, id, filename string, image []byte) error {
	return p.uploads.SaveGenerated(ctx, created, id, filename, image)
}

// createPlaceholder is create_subscription's branch for a certificate entry
// without a medical_id: for a parsed date other than today it draws the
// placeholder image, creates its Document, stores the PNG and creates the
// association account's MedicalCertificate with the date; it returns the new
// certificate's id. Every failure inside the view's try block (a value that is
// no date, a missing name, today's date) leaves no certificate.
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
	date, parsed, refused := subscriptionMedicalDate(expiration)
	if refused {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	if !parsed {
		return nil, nil
	}
	first, hasFirst := associate.Lookup("first_name")
	last, hasLast := associate.Lookup("last_name")
	if !hasFirst || !hasLast {
		return nil, nil // KeyError
	}
	firstText, firstIsText := first.(string)
	lastText, lastIsText := last.(string)
	if !firstIsText || !lastIsText {
		// str() of other values in the file name and the image is not ported.
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	if date.Format("2006-01-02") == subscriptionCreateToday(s.now()) {
		return nil, nil
	}
	created := s.now().UTC().Truncate(time.Microsecond)
	document := domain.NewDocument{ID: uuid.NewString(), Token: uuid.NewString(), Filename: placeholderFilename(firstText, lastText),
		Created: created}
	image, ok := s.placeholders.plan(placeholderCreateText(firstText, lastText, placeholderMoment(date)), document.Filename, created,
		document.ID)
	if !ok {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	actorAssociation, err := s.actorAssociation(ctx, tx, request.Audit)
	if err != nil {
		return nil, err
	}
	if err = tx.CreatePlaceholderDocument(ctx, document, actorAssociation, request.Audit, created); err != nil {
		return nil, err
	}
	if err = s.placeholders.store(ctx, created, document.ID, document.Filename, image); err != nil {
		return nil, err
	}
	if request.StorageKeys != nil {
		if name, nameErr := documentkey.UploadName(created, uuid.MustParse(document.ID), document.Filename); nameErr == nil {
			*request.StorageKeys = append(*request.StorageKeys, name)
		}
	}
	id := uuid.NewString()
	if err = tx.InsertPlaceholderCertificate(ctx, id, owner, document.ID, date, s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return nil, err
	}
	// The parsed datetime assigned to the DateField: auditlog renders str(datetime).
	if err = audit.created(ctx, "medicalcertificate", "MedicalCertificate", "application_medicalcertificate", "medical_id", id,
		map[string][2]string{"expiration_date": {"None", subscriptionPythonDateTime(date)}}, actorAssociation, "actor.sport_association"); err != nil {
		return nil, err
	}
	return &id, nil
}
