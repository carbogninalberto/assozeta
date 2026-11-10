package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

// DocumentUploads ports the views that create a Document and store its
// bytes: DocumentArchiveViewSet.add and .update, subscription_upload_document,
// medical_certificate_document and subscription_medical_certificate_upload.
type DocumentUploads struct {
	store     domain.DocumentUploadStore
	objects   domain.DocumentObjectStore
	grants    domain.MedicalUploadGrants
	overwrite bool
	now       func() time.Time
	ids       func() string
	// certificateLimit is MEDICAL_CERTIFICATE_MAX_UPLOAD_BYTES (0: the default).
	certificateLimit int64
}

// certificateDefaultLimit is core.settings' default of MEDICAL_CERTIFICATE_MAX_UPLOAD_BYTES.
const certificateDefaultLimit = 10 << 20

// WithCertificateLimit sets MEDICAL_CERTIFICATE_MAX_UPLOAD_BYTES.
func (s *DocumentUploads) WithCertificateLimit(limit int64) *DocumentUploads {
	s.certificateLimit = limit
	return s
}

// certificateAccepted is medical_certificate_type_allowed: a PDF or an
// image, by mimetypes.guess_type of the file name.
func certificateAccepted(name string) (bool, error) {
	kind, _, _, err := pymime.GuessType(name)
	if err != nil {
		return false, err
	}
	return kind == "application/pdf" || strings.HasPrefix(kind, "image/"), nil
}

// certificateTooLarge is medical_certificate_too_large_message(limit).
func certificateTooLarge(limit int64) string {
	size := strconv.FormatInt(limit, 10) + " byte"
	if limit%(1<<20) == 0 {
		size = strconv.FormatInt(limit/(1<<20), 10) + " MB"
	}
	return "Il file supera la dimensione massima consentita (" + size + ")."
}

func NewDocumentUploads(store domain.DocumentUploadStore, objects domain.DocumentObjectStore, grants domain.MedicalUploadGrants,
	overwrite bool, now func() time.Time, ids func() string) *DocumentUploads {
	return &DocumentUploads{store: store, objects: objects, grants: grants, overwrite: overwrite, now: now, ids: ids}
}

// ArchiveAddError is add's 400 {'exception': str(e)}.
type ArchiveAddError struct{ Message string }

func (e *ArchiveAddError) Error() string { return e.Message }

// DocumentValidationError is a serializer's 400 with field codes.
type DocumentValidationError struct{ Fields map[string]any }

func (e *DocumentValidationError) Error() string { return "invalid document archive update" }

// MedicalUploadError is subscription_medical_certificate_upload's
// {'error': str(e)} answer (400 for TypeError, otherwise 500).
type MedicalUploadError struct {
	Status  int
	Message string
}

func (e *MedicalUploadError) Error() string { return e.Message }

// ErrUploadDocumentMissing is upload-document's 400 {'exception': 'document key not present'}.
var ErrUploadDocumentMissing = errors.New("document key not present")

const addValidationText = "{'exception': ErrorDetail(string='document or filename key not present', code='invalid')}"

// ParseUUID is is_valid_uuid: a 400 with Python's message.
func (s *DocumentUploads) ParseUUID(raw string) (string, error) {
	parsed, err := paymentInfoParseUUID(raw)
	if err != nil {
		return "", err
	}
	return parsed.String(), nil
}

func (s *DocumentUploads) at() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// uploadAuditContext is paymentCategoryAuditContext, with no actor for an
// anonymous request.
func uploadAuditContext(identity domain.ResolvedIdentity, audit domain.LoginAuditContext) domain.LoginAuditContext {
	if identity.Actor == nil {
		audit.ActorID, audit.ActorEmail, audit.AdditionalData = nil, nil, nil
		return audit
	}
	return paymentCategoryAuditContext(identity, audit)
}

func (s *DocumentUploads) newDocument(filename string) domain.NewDocument {
	return domain.NewDocument{ID: s.ids(), Token: s.ids(), Filename: filename, Created: s.at()}
}

// save is default_storage.save(os.path.join(str(created.timestamp()), str(id),
// filename), content) on S3Storage: name validation (SuspiciousFileError),
// get_available_name's exists check, then one PutObject with
// mimetypes.guess_type's type and encoding.
func (s *DocumentUploads) save(ctx context.Context, document domain.NewDocument, content []byte) error {
	name, err := documentkey.UploadName(document.Created, uuid.MustParse(document.ID), document.Filename)
	if err != nil {
		return err
	}
	return s.saveNamed(ctx, name, content)
}

// GeneratedName reports whether default_storage.save would accept the name of
// a document generated at created (no storage call).
func (s *DocumentUploads) GeneratedName(created time.Time, id, filename string) error {
	name, err := documentkey.UploadName(created, uuid.MustParse(id), filename)
	if err != nil {
		return err
	}
	_, err = documentkey.S3Key(name, s.objects.Location())
	return err
}

// SaveGenerated is default_storage.save(os.path.join(str(created.timestamp()),
// str(id), filename), ContentFile(content)) for a document a view generated.
func (s *DocumentUploads) SaveGenerated(ctx context.Context, created time.Time, id, filename string, content []byte) error {
	return s.save(ctx, domain.NewDocument{ID: id, Filename: filename, Created: created}, content)
}

// saveNamed is save for a storage name already validated and cleaned.
func (s *DocumentUploads) saveNamed(ctx context.Context, name string, content []byte) error {
	key, err := documentkey.S3Key(name, s.objects.Location())
	if err != nil {
		return errors.Join(domain.ErrDocumentWritesNotPorted, err)
	}
	if !s.overwrite {
		exists, err := s.objects.Exists(ctx, key)
		if err != nil {
			return err
		}
		if exists {
			// get_available_name would pick a random alternative name.
			return domain.ErrDocumentWritesNotPorted
		}
	}
	kind, known, encoding, err := pymime.GuessType(key)
	if err != nil {
		return errors.Join(domain.ErrDocumentWritesNotPorted, err)
	}
	if !known {
		kind = "application/octet-stream"
	}
	return s.objects.Put(ctx, key, content, kind, encoding)
}

// storedText is the CharField text Document.objects.create(filename=value)
// stores: str() of a non-str value. ok=false refuses values whose database
// error text the port does not reproduce (None, NUL, lone surrogates).
func storedText(value any) (string, bool) {
	switch value.(type) {
	case nil, domain.RequestUpload, domain.RequestFile:
		return "", false
	}
	if name, isName := value.(uploadedFileName); isName {
		return string(name), dto.AssociateDraftStorable(string(name))
	}
	text := dto.PythonStr(value)
	return text, dto.AssociateDraftStorable(text)
}

// uploadedFileName marks a filename that came as an uploaded file: stored as
// its name, while the view's path join fails on the file object.
type uploadedFileName string

const tooLongText = "value too long for type character varying(255)\n"

// documentsUnstorableText is the text of the exception psycopg2 raises while
// quoting a filename, before any statement runs: a lone surrogate fails the
// UTF-8 encode (UnicodeEncodeError, consecutive surrogates reported as one
// range), then a NUL character is refused. Empty for None, files and text
// the driver accepts.
func documentsUnstorableText(value any) string {
	switch value.(type) {
	case nil, domain.RequestUpload, domain.RequestFile:
		return ""
	}
	text := dto.PythonStr(value)
	isSurrogate := func(i int) bool {
		return i+2 < len(text) && text[i] == 0xed && text[i+1] >= 0xa0 && text[i+1] <= 0xbf
	}
	position := 0
	for i := 0; i < len(text); position++ {
		if !isSurrogate(i) {
			_, size := utf8.DecodeRuneInString(text[i:])
			i += size
			continue
		}
		first := rune(text[i]&0x0f)<<12 | rune(text[i+1]&0x3f)<<6 | rune(text[i+2]&0x3f)
		end := position
		for j := i + 3; isSurrogate(j); j += 3 {
			end++
		}
		if end == position {
			return fmt.Sprintf("'utf-8' codec can't encode character '\\u%04x' in position %d: surrogates not allowed", first, position)
		}
		return fmt.Sprintf("'utf-8' codec can't encode characters in position %d-%d: surrogates not allowed", position, end)
	}
	if strings.ContainsRune(text, 0) {
		return "A string literal cannot contain NUL (0x00) characters."
	}
	return ""
}

// addItem is one iteration of add's loop before any write: an error raised
// before the Document exists (before), after it was created (after), or the
// decoded bytes and filename to store.
type addItem struct {
	before, after string
	filename      string
	content       []byte
}

func planAddItem(item any) (addItem, error) {
	var hasDocument, hasFilename bool
	switch v := item.(type) {
	case domain.RequestObject:
		_, hasDocument = v.Lookup("document")
		_, hasFilename = v.Lookup("filename")
	case string:
		if strings.Contains(v, "document") && strings.Contains(v, "filename") {
			return addItem{before: "string indices must be integers, not 'str'"}, nil
		}
	case []any:
		contains := func(key string) bool {
			for _, element := range v {
				if text, ok := element.(string); ok && text == key {
					return true
				}
			}
			return false
		}
		if contains("document") && contains("filename") {
			return addItem{before: "list indices must be integers or slices, not str"}, nil
		}
	default:
		return addItem{before: "argument of type '" + pythonTypeNameOf(item) + "' is not iterable"}, nil
	}
	if !hasDocument || !hasFilename {
		return addItem{before: addValidationText}, nil
	}
	object := item.(domain.RequestObject)
	document, _ := object.Lookup("document")
	content, err := dto.PythonB64Decode(document)
	if err != nil {
		var python *dto.PythonBase64Error
		if errors.As(err, &python) {
			return addItem{before: python.Message}, nil
		}
		return addItem{}, err
	}
	value, _ := object.Lookup("filename")
	text, ok := storedText(value)
	if !ok {
		if message := documentsUnstorableText(value); message != "" {
			return addItem{before: message}, nil
		}
		return addItem{}, domain.ErrDocumentWritesNotPorted
	}
	if utf8.RuneCountInString(text) > 255 {
		return addItem{before: tooLongText}, nil
	}
	if _, isText := value.(string); !isText {
		return addItem{filename: text, after: "join() argument must be str, bytes, or os.PathLike object, not '" + pythonTypeNameOf(value) + "'"}, nil
	}
	return addItem{filename: text, content: content}, nil
}

// Add follows DocumentArchiveViewSet.add. Items run in order; an exception
// inside the loop deletes the last Document the loop created and answers
// 400 {'exception': str(e)}. Items past the first error are never read.
func (s *DocumentUploads) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) ([]any, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrDocumentWritesFailed // request.data.get
	}
	files, present := object.Lookup("files")
	if !present {
		files = []any{}
	}
	tx, err := s.store.BeginDocumentUpload(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var folder *int64
	if value, _ := object.Lookup("folder"); value != nil {
		if _, upload := value.(domain.RequestUpload); upload {
			return nil, domain.ErrDocumentWritesNotPorted
		}
		pk, code, err := dto.FolderPKValue(value)
		if err != nil || code != "" || pk.Missing {
			return nil, domain.ErrDocumentWritesFailed // Folder.objects.get raises
		}
		if _, err = tx.FolderAssociation(ctx, pk.ID); err != nil {
			return nil, errors.Join(domain.ErrDocumentWritesFailed, err)
		}
		folder = &pk.ID
	}
	association, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		return nil, err
	}
	items, iterable := pythonIterate(files)
	if !iterable {
		return nil, domain.ErrDocumentWritesFailed
	}
	plan := make([]addItem, 0, len(items))
	for _, item := range items {
		step, err := planAddItem(item)
		if err != nil {
			return nil, err
		}
		plan = append(plan, step)
		if step.before != "" || step.after != "" {
			break
		}
	}
	actorAssociation, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return nil, err
	}
	context := uploadAuditContext(identity, audit)
	var last *domain.NewDocument
	// The except block: document.delete() of the last Document created, if any.
	unwind := func() error {
		if last != nil {
			if err := tx.DeleteDocument(ctx, *last, association, actorAssociation, context, s.at()); err != nil {
				return err
			}
		}
		return tx.Commit()
	}
	fail := func(message string) error {
		if err := unwind(); err != nil {
			return err
		}
		return &ArchiveAddError{Message: message}
	}
	rows := []any{}
	for _, step := range plan {
		if step.before != "" {
			return nil, fail(step.before)
		}
		document := s.newDocument(step.filename)
		if err = tx.CreateDocument(ctx, document, actorAssociation, context, s.at()); err != nil {
			return nil, err
		}
		last = &document
		if step.after != "" {
			return nil, fail(step.after)
		}
		if err = s.save(ctx, document, step.content); err != nil {
			var suspicious *documentkey.SuspiciousFileError
			if errors.As(err, &suspicious) {
				return nil, fail(suspicious.Message)
			}
			// Provider failures: Django answers with botocore's text after the delete.
			if failed := unwind(); failed != nil {
				return nil, failed
			}
			return nil, errors.Join(domain.ErrDocumentWritesNotPorted, err)
		}
		created := s.at()
		archive := domain.ArchiveRecord{ID: s.ids(), Association: association, Document: document.ID, Folder: folder}
		if err = tx.CreateArchive(ctx, archive, created, context, s.at()); err != nil {
			return nil, err
		}
		var rendered any
		if folder != nil {
			rendered = json.Number(strconv.FormatInt(*folder, 10))
		}
		rows = append(rows, domain.RequestObject{
			{Name: "sport_association_documents_archive_id", Value: archive.ID}, {Name: "sport_association", Value: association},
			{Name: "document", Value: domain.RequestObject{{Name: "document_id", Value: document.ID},
				{Name: "creation_date", Value: dto.DRFUTCDateTime(document.Created)}, {Name: "filepath", Value: nil},
				{Name: "filename", Value: document.Filename}, {Name: "file_size_bytes", Value: nil}, {Name: "token", Value: document.Token}}},
			{Name: "date", Value: pythonIsoformat(created)}, {Name: "folder", Value: rendered}})
	}
	return rows, tx.Commit()
}

// pythonIsoformat is datetime.isoformat() of an aware UTC datetime.
func pythonIsoformat(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02T15:04:05") + "+00:00"
	}
	return value.Format("2006-01-02T15:04:05.000000") + "+00:00"
}

// Update follows DocumentArchiveViewSet.update: get_object, request.data,
// the partial DocumentArchiveSerializer and save(); rendering the nested
// many=True document then raises, so a valid request is the 500 page after
// the save.
func (s *DocumentUploads) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, read func() (any, error),
	audit domain.LoginAuditContext) error {
	tx, err := s.store.BeginDocumentUpload(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		return err
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return ErrArchiveLookup
	}
	row, err := tx.Archive(ctx, id.String(), association)
	if err != nil {
		return err
	}
	body, err := read()
	if err != nil {
		return err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return &DocumentValidationError{Fields: map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeNameOf(body)}}}
	}
	update, codes, err := dto.ParseArchiveUpdate(object)
	if err != nil {
		return err
	}
	invalid := map[string]any{}
	for name, list := range codes {
		invalid[name] = list
	}
	if update.ID != nil {
		taken, err := tx.ArchiveIDTaken(ctx, *update.ID, row.ID)
		if err != nil {
			return err
		}
		if taken {
			invalid["sport_association_documents_archive_id"] = []string{dto.ArchiveUpdateUnique}
		}
	}
	current, err := tx.CurrentAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return err
	}
	denied := &domain.PermissionDenied{Detail: "You do not have permission to perform this action."}
	if update.Association != nil {
		exists, err := tx.AssociationExists(ctx, *update.Association)
		if err != nil {
			return err
		}
		if !exists {
			invalid["sport_association"] = []string{"related-does-not-exist:" + dto.PythonStr(update.AssociationRaw)}
		} else if current != nil && *current != *update.Association {
			return denied // tenant_scope
		}
	}
	if update.Folder != nil {
		found := false
		var owner string
		if !update.Folder.Missing {
			if owner, found, err = tx.FolderExists(ctx, update.Folder.ID); err != nil {
				return err
			}
		}
		if !found {
			invalid["folder"] = []string{"related-does-not-exist:" + dto.PythonStr(update.Folder.Raw)}
		} else if current != nil && *current != owner {
			return denied
		}
	}
	if len(invalid) > 0 {
		return &DocumentValidationError{Fields: invalid}
	}
	next := row
	if update.Association != nil {
		next.Association = *update.Association
	}
	if update.Date != nil {
		next.Date = *update.Date
	}
	if update.FolderPresent {
		next.Folder = nil
		if update.Folder != nil {
			next.Folder = &update.Folder.ID
		}
	}
	context := uploadAuditContext(identity, audit)
	if update.ID != nil && *update.ID != row.ID {
		next.ID = *update.ID
		err = tx.InsertArchive(ctx, next, context, s.at())
	} else {
		changes := map[string][2]string{}
		if row.Association != next.Association {
			changes["sport_association"] = [2]string{row.Association, next.Association}
		}
		if row.Date != next.Date {
			changes["date"] = [2]string{row.Date, next.Date}
		}
		if before, after := archiveFolderText(row.Folder), archiveFolderText(next.Folder); before != after {
			changes["folder"] = [2]string{before, after}
		}
		err = tx.SaveArchive(ctx, next, changes, context, s.at())
	}
	if err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	return domain.ErrDocumentWritesFailed
}

func archiveFolderText(folder *int64) string {
	if folder == nil {
		return "None"
	}
	return strconv.FormatInt(*folder, 10)
}

func (s *DocumentUploads) uploadSubscription(ctx context.Context, tx domain.DocumentUploadTransaction, identity domain.ResolvedIdentity, id,
	group string) (*domain.UploadSubscription, error) {
	subscription, err := tx.UploadSubscription(ctx, id, paymentCategoryAssociationScope(identity), supplierGroup(group))
	if err != nil {
		return nil, err
	}
	if subscription == nil {
		return nil, domain.ErrDocumentWritesFailed // TypeError("subscription not found!")
	}
	// Files are attached by the association's staff and by the member the subscription belongs to.
	if err := memberDocumentAccess(ctx, s.store, identity, identity.ScopeUser, func(ctx context.Context, member string) (bool, error) {
		return s.store.MemberSubscription(ctx, member, subscription.ID)
	}); err != nil {
		return nil, err
	}
	return subscription, nil
}

// UploadTarget is subscription_upload_document's lookup before request.data.
func (s *DocumentUploads) UploadTarget(ctx context.Context, identity domain.ResolvedIdentity, id, group string) error {
	tx, err := s.store.BeginDocumentUpload(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	_, err = s.uploadSubscription(ctx, tx, identity, id, group)
	return err
}

// UploadDocument follows subscription_upload_document after the lookup:
// the Document is created first, so a failing path join, base64 payload or
// storage name keeps it (requests are not atomic).
func (s *DocumentUploads) UploadDocument(ctx context.Context, identity domain.ResolvedIdentity, id, group string, body any,
	audit domain.LoginAuditContext) error {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return domain.ErrDocumentWritesFailed // data.keys()
	}
	payload, hasDocument := object.Lookup("document")
	value, hasFilename := object.Lookup("filename")
	if !hasDocument || !hasFilename {
		return ErrUploadDocumentMissing
	}
	if value == nil {
		return domain.ErrDocumentWritesFailed // NOT NULL violation before any row exists
	}
	// A file sent as the filename is stored by its name (str() of the upload).
	switch file := value.(type) {
	case domain.RequestFile:
		value = uploadedFileName(file.Name)
	case domain.RequestUpload:
		value = uploadedFileName(file.Name)
	}
	text, storable := storedText(value)
	if !storable {
		if documentsUnstorableText(value) != "" {
			return domain.ErrDocumentWritesFailed // the driver refuses the text: no row
		}
		return domain.ErrDocumentWritesNotPorted
	}
	if utf8.RuneCountInString(text) > 255 {
		return domain.ErrDocumentWritesFailed // value too long: no row
	}
	tx, err := s.store.BeginDocumentUpload(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	subscription, err := s.uploadSubscription(ctx, tx, identity, id, group)
	if err != nil {
		return err
	}
	actorAssociation, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	context := uploadAuditContext(identity, audit)
	document := s.newDocument(text)
	if err = tx.CreateDocument(ctx, document, actorAssociation, context, s.at()); err != nil {
		return err
	}
	keep := func(cause error) error {
		if err := tx.Commit(); err != nil {
			return err
		}
		return cause
	}
	if _, isText := value.(string); !isText {
		return keep(domain.ErrDocumentWritesFailed) // os.path.join TypeError
	}
	switch payload.(type) {
	case domain.RequestFile, domain.RequestUpload:
		return keep(domain.ErrDocumentWritesFailed) // b64decode of an uploaded file: TypeError
	}
	content, err := dto.PythonB64Decode(payload)
	if err != nil {
		return keep(domain.ErrDocumentWritesFailed)
	}
	if err = s.save(ctx, document, content); err != nil {
		return keep(err) // SuspiciousFileError is Django's 400 page; anything else the 500 page
	}
	if err = tx.CreateSubscriptionFile(ctx, s.ids(), *subscription, document.ID, s.at(), context, s.at()); err != nil {
		return err
	}
	return tx.Commit()
}

// uploadCertificate is MedicalCertificateService.upload_medical_certificate
// (and the same steps in medical_certificate_document): Document, storage,
// then MedicalCertificate(document, user).
func (s *DocumentUploads) uploadCertificate(ctx context.Context, tx domain.DocumentUploadTransaction, identity domain.ResolvedIdentity,
	file domain.RequestFile, context domain.LoginAuditContext) (domain.NewDocument, string, error) {
	var actorAssociation *string
	var err error
	if identity.Actor != nil {
		if actorAssociation, err = tx.ActorAssociation(ctx, identity.Actor.ID); err != nil {
			return domain.NewDocument{}, "", err
		}
	}
	document := s.newDocument(file.Name)
	if err = tx.CreateDocument(ctx, document, actorAssociation, context, s.at()); err != nil {
		return document, "", err
	}
	if err = s.save(ctx, document, file.Content); err != nil {
		if commit := tx.Commit(); commit != nil {
			return document, "", commit
		}
		return document, "", errors.Join(domain.ErrDocumentWritesNotPorted, err)
	}
	var user *string
	if identity.ScopeUser != nil {
		user = &identity.ScopeUser.ID
	}
	certificate := s.ids()
	if err = tx.CreateUploadedCertificate(ctx, certificate, document.ID, user, s.at(), actorAssociation, context, s.at()); err != nil {
		return document, "", err
	}
	return document, certificate, nil
}

// CertificateDocument follows medical_certificate_document (AllowAny): the
// certificate id, remembered for an association-scoped session.
func (s *DocumentUploads) CertificateDocument(ctx context.Context, identity domain.ResolvedIdentity, body any, session string,
	audit domain.LoginAuditContext) (string, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return "", domain.ErrDocumentWritesFailed // data.keys()
	}
	value, present := object.Lookup("medical_certificate")
	if !present {
		return "", domain.ErrDocumentWritesFailed // TypeError
	}
	file, isFile := value.(domain.RequestFile)
	if !isFile {
		return "", domain.ErrDocumentWritesFailed // .name
	}
	accepted, err := certificateAccepted(file.Name)
	if err != nil {
		// A name mimetypes reads as a URL: not reproduced, refused before any write.
		return "", errors.Join(domain.ErrDocumentWritesFailed, err)
	}
	if !accepted {
		return "", &MedicalUploadError{Status: 400, Message: "Seleziona un file PDF o un’immagine."}
	}
	limit := s.certificateLimit
	if limit == 0 {
		limit = certificateDefaultLimit
	}
	if int64(len(file.Content)) > limit {
		return "", &MedicalUploadError{Status: 413, Message: certificateTooLarge(limit)}
	}
	tx, err := s.store.BeginDocumentUpload(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	_, certificate, err := s.uploadCertificate(ctx, tx, identity, file, uploadAuditContext(identity, audit))
	if err != nil {
		return "", err
	}
	if err = tx.Commit(); err != nil {
		return "", err
	}
	if scope := paymentCategoryAssociationScope(identity); scope != nil {
		if err = s.grants.RememberMedicalUpload(ctx, session, certificate, *scope); err != nil {
			return "", err
		}
	}
	return certificate, nil
}

// MedicalTarget is subscription_medical_certificate_upload's lookup before request.data.
func (s *DocumentUploads) MedicalTarget(ctx context.Context, identity domain.ResolvedIdentity, id, group string) error {
	tx, err := s.store.BeginDocumentUpload(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	_, _, err = s.medicalTarget(ctx, tx, identity, id, group)
	return err
}

func (s *DocumentUploads) medicalTarget(ctx context.Context, tx domain.DocumentUploadTransaction, identity domain.ResolvedIdentity, id,
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

// MedicalUpload follows subscription_medical_certificate_upload after the
// lookup; the body is read inside its try block, so every exception is
// {'error': str(e)} (TypeError 400, otherwise 500). It returns the response.
func (s *DocumentUploads) MedicalUpload(ctx context.Context, identity domain.ResolvedIdentity, id, group string, body any,
	audit domain.LoginAuditContext) (domain.RequestObject, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, &MedicalUploadError{Status: 500, Message: "'" + pythonTypeNameOf(body) + "' object has no attribute 'get'"}
	}
	value, _ := object.Lookup("medical_certificate")
	if value == nil {
		return nil, &MedicalUploadError{Status: 400, Message: "medical_certificate key not present!"}
	}
	file, isFile := value.(domain.RequestFile)
	if !isFile {
		if _, upload := value.(domain.RequestUpload); upload {
			return nil, domain.ErrDocumentWritesNotPorted
		}
		return nil, &MedicalUploadError{Status: 500, Message: "'" + pythonTypeNameOf(value) + "' object has no attribute 'name'"}
	}
	tx, err := s.store.BeginDocumentUpload(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	subscription, draft, err := s.medicalTarget(ctx, tx, identity, id, group)
	if err != nil {
		return nil, err
	}
	context := uploadAuditContext(identity, audit)
	document, certificate, err := s.uploadCertificate(ctx, tx, identity, file, context)
	if err != nil {
		return nil, err
	}
	fail := func(message string) error {
		if err := tx.Commit(); err != nil {
			return err
		}
		return &MedicalUploadError{Status: 500, Message: message}
	}
	if draft != nil {
		data, message, err := draftWithCertificate(*draft, certificate, document.Filename)
		if err != nil {
			return nil, err
		}
		if message != "" {
			return nil, fail(message)
		}
		if err = tx.SaveDraftData(ctx, *draft, dto.PythonJSONDumps(data), dto.PythonJSONDumpsSorted(data), context, s.at()); err != nil {
			return nil, err
		}
		return domain.RequestObject{{Name: "msg", Value: "updated draft"}, {Name: "expiring_date", Value: nil}}, tx.Commit()
	}
	dates, err := ResolveSubscriptionDates(ctx, subscription.Target.Created, subscription.Target.Rule, subscription.Target.Dates)
	if err != nil {
		// Subscription.save() raised: the season error's text, after the certificate was stored.
		var calendar *domain.CalendarDateError
		if errors.As(err, &calendar) {
			return nil, fail(calendar.Message)
		}
		if commit := tx.Commit(); commit != nil {
			return nil, commit
		}
		return nil, errors.Join(domain.ErrDocumentWritesNotPorted, err)
	}
	if err = tx.AttachCertificate(ctx, *subscription, certificate, dates, context, s.at()); err != nil {
		return nil, err
	}
	return domain.RequestObject{{Name: "medical", Value: document.ID}, {Name: "expiring_date", Value: nil}}, tx.Commit()
}

// draftWithCertificate is attach_certificate_to_draft on the stored data: a
// missing medical_certificate entry is added, then its medical_id and
// filename are set. message is the exception text for data it cannot update.
func draftWithCertificate(draft domain.MedicalDraft, certificate, filename string) (domain.RequestObject, string, error) {
	if draft.Data == nil {
		return nil, "'NoneType' object has no attribute 'keys'", nil
	}
	parsed, err := dto.ParseProfileTablesJSON(draft.Data)
	if err != nil {
		return nil, "", err
	}
	data, isObject := parsed.(domain.RequestObject)
	if !isObject {
		return nil, "'" + pythonTypeNameOf(parsed) + "' object has no attribute 'keys'", nil
	}
	entry, present := data.Lookup("medical_certificate")
	if !present {
		entry = domain.RequestObject{{Name: "medical_id", Value: nil}, {Name: "filename", Value: nil}, {Name: "certificate_expring_date", Value: nil}}
	}
	switch value := entry.(type) {
	case domain.RequestObject:
		value = dto.SetRequestField(value, "medical_id", certificate)
		value = dto.SetRequestField(value, "filename", filename)
		return dto.SetRequestField(data, "medical_certificate", value), "", nil
	case []any:
		return nil, "list indices must be integers or slices, not str", nil
	}
	return nil, "'" + pythonTypeNameOf(entry) + "' object does not support item assignment", nil
}

// archiveLookup is get_object() over the owner's archive rows.
func (s *DocumentUploads) archiveLookup(ctx context.Context, tx domain.DocumentUploadTransaction, identity domain.ResolvedIdentity,
	raw string) (domain.ArchiveRecord, error) {
	association, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		return domain.ArchiveRecord{}, err
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return domain.ArchiveRecord{}, ErrArchiveLookup
	}
	return tx.Archive(ctx, id.String(), association)
}

// Retrieve is the router detail route's stock retrieve: get_object(), then
// DocumentArchiveSerializer, whose document field (many=True on a foreign
// key) iterates a Document instance: a TypeError (500) for every row found.
func (s *DocumentUploads) Retrieve(ctx context.Context, identity domain.ResolvedIdentity, raw string) error {
	tx, err := s.store.BeginDocumentUpload(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = s.archiveLookup(ctx, tx, identity, raw); err != nil {
		return err
	}
	return domain.ErrDocumentWritesFailed
}

// Create is the router list route's stock CreateModelMixin.create: the
// JSON-only request.data, DocumentArchiveSerializer validation (the
// sport_association is required; it and the folder are tenant-scoped), then
// perform_create's SportAssociation.objects.get(user=request.user) and the
// insert, whose document column (read-only in the serializer, NOT NULL in
// the table) fails: a 500 with nothing written once the input is valid.
func (s *DocumentUploads) Create(ctx context.Context, identity domain.ResolvedIdentity, read func() (any, error)) error {
	body, err := read()
	if err != nil {
		return err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return &DocumentValidationError{Fields: map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeNameOf(body)}}}
	}
	update, codes, err := dto.ParseArchiveUpdate(object)
	if err != nil {
		return err
	}
	invalid := map[string]any{}
	for name, list := range codes {
		invalid[name] = list
	}
	if _, present := object.Lookup("sport_association"); !present {
		invalid["sport_association"] = []string{dto.PaymentCategoryValidationRequired}
	}
	tx, err := s.store.BeginDocumentUpload(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if update.ID != nil {
		except := "00000000-0000-0000-0000-000000000000"
		if *update.ID == except {
			except = "ffffffff-ffff-ffff-ffff-ffffffffffff"
		}
		taken, err := tx.ArchiveIDTaken(ctx, *update.ID, except)
		if err != nil {
			return err
		}
		if taken {
			invalid["sport_association_documents_archive_id"] = []string{dto.ArchiveUpdateUnique}
		}
	}
	current, err := tx.CurrentAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return err
	}
	denied := &domain.PermissionDenied{Detail: "You do not have permission to perform this action."}
	if update.Association != nil {
		exists, err := tx.AssociationExists(ctx, *update.Association)
		if err != nil {
			return err
		}
		if !exists {
			invalid["sport_association"] = []string{"related-does-not-exist:" + dto.PythonStr(update.AssociationRaw)}
		} else if current != nil && *current != *update.Association {
			return denied // tenant_scope
		}
	}
	if update.Folder != nil {
		found := false
		var owner string
		if !update.Folder.Missing {
			if owner, found, err = tx.FolderExists(ctx, update.Folder.ID); err != nil {
				return err
			}
		}
		if !found {
			invalid["folder"] = []string{"related-does-not-exist:" + dto.PythonStr(update.Folder.Raw)}
		} else if current != nil && *current != owner {
			return denied
		}
	}
	if len(invalid) > 0 {
		return &DocumentValidationError{Fields: invalid}
	}
	if _, err = tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil); err != nil {
		return err
	}
	return domain.ErrDocumentWritesFailed
}
