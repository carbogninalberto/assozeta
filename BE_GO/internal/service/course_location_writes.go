package service

import (
	"context"
	"errors"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

type CourseLocationWrites struct {
	store domain.CourseLocationWriteStore
	now   func() time.Time
	ids   func() string
	// uploads stores documents_data files (default_storage.save); without
	// it every upload is refused.
	uploads *DocumentUploads
}

// WithUploads enables the documents_data uploads through the document storage.
func (s *CourseLocationWrites) WithUploads(uploads *DocumentUploads) *CourseLocationWrites {
	s.uploads = uploads
	return s
}

func NewCourseLocationWrites(store domain.CourseLocationWriteStore, now func() time.Time, ids func() string) *CourseLocationWrites {
	return &CourseLocationWrites{store: store, now: now, ids: ids}
}

var (
	// ErrCourseLocationDocumentKey is {'exception': 'document key not
	// present'} (400), raised after the location was saved.
	ErrCourseLocationDocumentKey = errors.New("course location document key not present")
	// ErrCourseLocationDocumentDecode is base64.b64decode's failure (500),
	// raised after the location was saved.
	ErrCourseLocationDocumentDecode = errors.New("course location document is not base64")
	// errCourseLocationDocumentStore is the exception Django raises while
	// creating or storing a document (the INSERT, os.path.join or
	// Storage.save): the 500 page, after the earlier writes. A name
	// Storage.save rejects is a SuspiciousFileOperation, which the walk returns
	// as a *documentkey.SuspiciousFileError (the 400 page) instead.
	errCourseLocationDocumentStore = errors.New("course location document could not be stored")
	// errCourseLocationNoStorage is a writer built without its document
	// storage: cmd/server/main.go always calls WithUploads. An
	// application-state error.
	errCourseLocationNoStorage = refusal.AppState("course location writes without document storage")
)

// CourseLocationValidationError is DRF's 400 field map.
type CourseLocationValidationError struct{ Fields map[string]any }

func (e *CourseLocationValidationError) Error() string { return "invalid course location input" }

// location is get_object(): get_queryset's SportAssociation.objects.get(
// user=request.user) precedes the pk lookup.
func (s *CourseLocationWrites) location(ctx context.Context, tx domain.CourseLocationWriteTransaction, identity domain.ResolvedIdentity,
	raw string) (*domain.CourseLocation, error) {
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, domain.ErrCourseLocationMalformed
	}
	row, err := tx.Location(ctx, id.String(), association)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, domain.ErrCourseLocationNotFound
	}
	return row, nil
}

// validate applies the sport_association queryset and tenant scope (its
// PermissionDenied escapes the serializer before the field errors are raised).
func (s *CourseLocationWrites) validate(ctx context.Context, tx domain.CourseLocationWriteTransaction, identity domain.ResolvedIdentity,
	write dto.CourseLocationWrite, invalid map[string]any) error {
	if invalid == nil {
		invalid = map[string]any{}
	}
	if write.AssociationID != nil {
		exists, err := tx.AssociationExists(ctx, *write.AssociationID)
		if err != nil {
			return err
		}
		if !exists {
			invalid["sport_association"] = []string{"related-does-not-exist:" + *write.AssociationID}
		} else {
			// current_association(): the scope user's association, if any.
			tenant, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
			if err != nil && !errors.Is(err, domain.ErrAssociationNotFound) {
				return err
			}
			if err == nil && tenant != *write.AssociationID {
				return domain.ErrCourseLocationForeignReference
			}
		}
	}
	if len(invalid) > 0 {
		return &CourseLocationValidationError{Fields: invalid}
	}
	return nil
}

// courseLocationUpload is one documents_data item _handle_documents stores.
type courseLocationUpload struct {
	filename string
	content  []byte
	// rowOnly creates the Document row and its auditlog entry, but does not
	// store the bytes and does not link it: the item's filename made
	// os.path.join or Storage.save raise after the INSERT.
	rowOnly bool
}

// courseLocationDocuments walks _handle_documents without writing: the items
// that store a document (in order) up to the first one that raises, whose
// failure is returned after the save. A filename Django cannot store stops the
// walk with errCourseLocationDocumentStore: None, text PostgreSQL refuses (NUL,
// lone surrogates, over 255 characters) leaves no Document row, while a name
// Storage.save rejects, or a value that is not a str (CharField stores str()
// of it, then os.path.join raises TypeError on the original object), creates
// the row with that text and then raises.
func (s *CourseLocationWrites) courseLocationDocuments(write dto.CourseLocationWrite) ([]courseLocationUpload, error, error) {
	var uploads []courseLocationUpload
	for _, item := range write.Documents {
		switch dto.CourseLocationDocument(item) {
		case dto.CourseLocationDocumentMissingKey:
			return uploads, ErrCourseLocationDocumentKey, nil
		case dto.CourseLocationDocumentUndecodable:
			return uploads, ErrCourseLocationDocumentDecode, nil
		}
		if s.uploads == nil {
			return nil, nil, errCourseLocationNoStorage
		}
		raw, _ := item.Lookup("document")
		content, err := dto.PythonB64Decode(raw)
		if err != nil {
			return uploads, ErrCourseLocationDocumentDecode, nil
		}
		value, _ := item.Lookup("filename")
		if value == nil {
			// Document.objects.create(filename=None): NOT NULL, no row.
			return uploads, errCourseLocationDocumentStore, nil
		}
		// CharField stores str(value): a str unchanged, a JSON number/bool/
		// list/object its Python repr, an uploaded file its name.
		filename := dto.PythonStr(value)
		if !dto.AssociateDraftStorable(filename) || utf8.RuneCountInString(filename) > 255 {
			// psycopg2 refuses NUL and lone surrogates, varchar(255) the rest
			// over the column length: no row.
			return uploads, errCourseLocationDocumentStore, nil
		}
		if _, isText := value.(string); !isText {
			// os.path.join(storing_path, document.filename) is a TypeError for
			// a value that is not a str: the row is written, then it raises.
			uploads = append(uploads, courseLocationUpload{filename: filename, rowOnly: true})
			return uploads, errCourseLocationDocumentStore, nil
		}
		// The stored name uses the document's creation time, which has microsecond precision.
		if _, err = documentkey.UploadName(s.uploads.at(), uuid.New(), filename); err != nil {
			// Storage.save's validate_file_name rejects the name after the INSERT:
			// a SuspiciousFileOperation is a SuspiciousOperation (the 400 page),
			// any other failure the 500 page.
			uploads = append(uploads, courseLocationUpload{filename: filename, rowOnly: true})
			var suspicious *documentkey.SuspiciousFileError
			if errors.As(err, &suspicious) {
				return uploads, err, nil
			}
			return uploads, errCourseLocationDocumentStore, nil
		}
		uploads = append(uploads, courseLocationUpload{filename: filename, content: content})
	}
	return uploads, nil, nil
}

// storeDocuments creates each Document (auditlog entry), stores its bytes and
// links it to the location. A rowOnly upload stops after its INSERT: the item
// that created it raised before storing, and it is not linked.
func (s *CourseLocationWrites) storeDocuments(ctx context.Context, tx domain.CourseLocationWriteTransaction, identity domain.ResolvedIdentity,
	location string, uploads []courseLocationUpload, audit domain.LoginAuditContext) error {
	if len(uploads) == 0 {
		return nil
	}
	actor, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	for _, upload := range uploads {
		document := s.uploads.newDocument(upload.filename)
		if err = tx.CreateDocument(ctx, document, actor, audit, s.uploads.at()); err != nil {
			return err
		}
		if upload.rowOnly {
			continue
		}
		if err = s.uploads.save(ctx, document, upload.content); err != nil {
			return err
		}
		if err = tx.LinkDocument(ctx, location, document.ID); err != nil {
			return err
		}
	}
	return nil
}

func applyCourseLocationWrite(row *domain.CourseLocation, write dto.CourseLocationWrite) {
	fields := map[string]**string{"title": &row.Title, "description": &row.Description, "address": &row.Address}
	for name, value := range write.Text {
		*fields[name] = value
	}
}

func courseLocationValue(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func (s *CourseLocationWrites) save(ctx context.Context, identity domain.ResolvedIdentity, raw *string, body any, form bool,
	audit domain.LoginAuditContext) (map[string]any, error) {
	tx, err := s.store.BeginCourseLocationWrite(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var row *domain.CourseLocation
	if raw != nil {
		if row, err = s.location(ctx, tx, identity, *raw); err != nil {
			return nil, err
		}
	}
	write, invalid := dto.ParseCourseLocationWrite(body, form, raw != nil)
	if err = s.validate(ctx, tx, identity, write, invalid); err != nil {
		return nil, err
	}
	auditContext, at := paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)
	if row == nil {
		// perform_create: SportAssociation.objects.get(user=request.user).
		association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
		if err != nil {
			return nil, err
		}
		uploads, failure, refused := s.courseLocationDocuments(write)
		if refused != nil {
			return nil, refused
		}
		created := domain.CourseLocation{ID: s.ids(), AssociationID: &association}
		applyCourseLocationWrite(&created, write)
		if err = tx.CreateLocation(ctx, created, auditContext, at); err != nil {
			return nil, err
		}
		if err = s.storeDocuments(ctx, tx, identity, created.ID, uploads, auditContext); err != nil {
			return nil, err
		}
		return s.finish(ctx, tx, created, failure)
	}
	uploads, failure, refused := s.courseLocationDocuments(write)
	if refused != nil {
		return nil, refused
	}
	old := *row
	applyCourseLocationWrite(row, write)
	if write.AssociationPresent {
		row.AssociationID = write.AssociationID
	}
	changes := map[string][2]string{}
	diff := func(name string, before, after *string) {
		if courseLocationValue(before) != courseLocationValue(after) {
			changes[name] = [2]string{courseLocationValue(before), courseLocationValue(after)}
		}
	}
	diff("title", old.Title, row.Title)
	diff("description", old.Description, row.Description)
	diff("address", old.Address, row.Address)
	diff("sport_association", old.AssociationID, row.AssociationID)
	if err = tx.SaveLocation(ctx, *row, changes, auditContext, at); err != nil {
		return nil, err
	}
	if err = s.storeDocuments(ctx, tx, identity, row.ID, uploads, auditContext); err != nil {
		return nil, err
	}
	return s.finish(ctx, tx, *row, failure)
}

// finish commits the saved location: requests are not atomic, so a document
// failure after the save keeps it.
func (s *CourseLocationWrites) finish(ctx context.Context, tx domain.CourseLocationWriteTransaction, row domain.CourseLocation,
	failure error) (map[string]any, error) {
	if failure != nil {
		if err := tx.Commit(); err != nil {
			return nil, err
		}
		return nil, failure
	}
	documents, err := tx.LocationDocuments(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	item, err := courseLocationItem(row, documents)
	if err != nil {
		return nil, err
	}
	return item, tx.Commit()
}

// Add follows CourseLocationViewSet.add (201).
func (s *CourseLocationWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool,
	audit domain.LoginAuditContext) (map[string]any, error) {
	return s.save(ctx, identity, nil, body, form, audit)
}

// Update follows CourseLocationViewSet.update (partial).
func (s *CourseLocationWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool,
	audit domain.LoginAuditContext) (map[string]any, error) {
	return s.save(ctx, identity, &raw, body, form, audit)
}

// Delete follows CourseLocationViewSet.delete (204).
func (s *CourseLocationWrites) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	tx, err := s.store.BeginCourseLocationWrite(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	row, err := s.location(ctx, tx, identity, raw)
	if err != nil {
		return err
	}
	if err = tx.DeleteLocation(ctx, row.ID, paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	return tx.Commit()
}

// Retrieve is the stock RetrieveModelMixin.retrieve of the router's detail
// route: get_object() then CourseLocationSerializer.
func (s *CourseLocationWrites) Retrieve(ctx context.Context, identity domain.ResolvedIdentity, raw string) (map[string]any, error) {
	tx, err := s.store.BeginCourseLocationWrite(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	row, err := s.location(ctx, tx, identity, raw)
	if err != nil {
		return nil, err
	}
	documents, err := tx.LocationDocuments(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	return courseLocationItem(*row, documents)
}
