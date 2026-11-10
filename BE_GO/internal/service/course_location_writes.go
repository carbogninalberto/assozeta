package service

import (
	"context"
	"errors"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
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
	// ErrCourseLocationUploadUnsupported refuses documents_data uploads the
	// port does not reproduce (indexed form keys, filenames that are not plain
	// storable text, names Storage.save rejects, no document storage) before
	// any write.
	ErrCourseLocationUploadUnsupported = errors.New("course location document upload is not ported")
	// ErrCourseLocationPrimaryKeyChange refuses a new course_location_id on
	// update: Model.save() would insert a copy under the new key (not ported).
	ErrCourseLocationPrimaryKeyChange = errors.New("course location primary key change is not ported")
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

// validate applies the course_location_id UniqueValidator and the
// sport_association queryset and tenant scope (its PermissionDenied escapes
// the serializer before the field errors are raised).
func (s *CourseLocationWrites) validate(ctx context.Context, tx domain.CourseLocationWriteTransaction, identity domain.ResolvedIdentity,
	write dto.CourseLocationWrite, invalid map[string]any, exclude *string) error {
	if invalid == nil {
		invalid = map[string]any{}
	}
	if write.ID != nil {
		taken, err := tx.LocationIDTaken(ctx, *write.ID, exclude)
		if err != nil {
			return err
		}
		if taken {
			invalid["course_location_id"] = []string{dto.CourseLocationUnique}
		}
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
}

// courseLocationDocuments walks _handle_documents without writing: the items
// that store a document (in order) up to the first one that raises, whose
// failure is returned after the save. Inputs whose failure the port does not
// reproduce (filenames that are not plain storable text of at most 255
// characters, names Storage.save rejects) are refused before any write.
func (s *CourseLocationWrites) courseLocationDocuments(write dto.CourseLocationWrite) ([]courseLocationUpload, error, error) {
	if write.IndexedDocuments {
		return nil, nil, ErrCourseLocationUploadUnsupported
	}
	var uploads []courseLocationUpload
	for _, item := range write.Documents {
		switch dto.CourseLocationDocument(item) {
		case dto.CourseLocationDocumentMissingKey:
			return uploads, ErrCourseLocationDocumentKey, nil
		case dto.CourseLocationDocumentUndecodable:
			return uploads, ErrCourseLocationDocumentDecode, nil
		}
		if s.uploads == nil {
			return nil, nil, ErrCourseLocationUploadUnsupported
		}
		raw, _ := item.Lookup("document")
		content, err := dto.PythonB64Decode(raw)
		if err != nil {
			return uploads, ErrCourseLocationDocumentDecode, nil
		}
		value, _ := item.Lookup("filename")
		filename, ok := value.(string)
		if !ok || !dto.AssociateDraftStorable(filename) || utf8.RuneCountInString(filename) > 255 {
			return nil, nil, ErrCourseLocationUploadUnsupported
		}
		if _, err = documentkey.UploadName(s.now(), uuid.New(), filename); err != nil {
			return nil, nil, ErrCourseLocationUploadUnsupported
		}
		uploads = append(uploads, courseLocationUpload{filename: filename, content: content})
	}
	return uploads, nil, nil
}

// storeDocuments creates each Document (auditlog entry), stores its bytes and
// links it to the location.
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
	write, invalid := dto.ParseCourseLocationWrite(body, form)
	var exclude *string
	if row != nil {
		exclude = &row.ID
	}
	if err = s.validate(ctx, tx, identity, write, invalid, exclude); err != nil {
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
		if write.ID != nil {
			created.ID = *write.ID
		}
		applyCourseLocationWrite(&created, write)
		if err = tx.CreateLocation(ctx, created, auditContext, at); err != nil {
			return nil, err
		}
		if err = s.storeDocuments(ctx, tx, identity, created.ID, uploads, auditContext); err != nil {
			return nil, err
		}
		return s.finish(ctx, tx, created, failure)
	}
	if write.ID != nil && *write.ID != row.ID {
		return nil, ErrCourseLocationPrimaryKeyChange
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
