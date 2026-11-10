package service

import (
	"context"
	"errors"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pandascsv"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pandasxlsx"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

var (
	// ErrSubscriptionImportUnauthorized is the view's Response(status=401).
	ErrSubscriptionImportUnauthorized = errors.New("subscription import: no association")
	// ErrSubscriptionImportServerError is an exception the view does not handle: the 500 page.
	ErrSubscriptionImportServerError = errors.New("subscription_import_upload raised")
	// ErrSubscriptionImportNotPorted refuses, before any write, what Go does
	// not reproduce: workbooks outside pandasxlsx's subset, CSV input outside
	// pandascsv's subset, columns whose str.lower() differs from Go's, the
	// import of a stored CSV document, import values outside the ported
	// dateutil and codicefiscale subsets and a file given as document_id.
	ErrSubscriptionImportNotPorted = errors.New("subscription import path not ported")
)

// subscriptionImportKeys is SubscriptionMaps.MAP's key order.
var subscriptionImportKeys = []string{"nome", "cognome", "sesso", "codice fiscale", "data di nascita", "città di nascita", "indirizzo",
	"città di residenza", "cap", "email", "telefono", "nome tutore", "cognome tutore", "sesso tutore", "codice fiscale tutore",
	"data di nascita tutore", "città di nascita tutore", "città di residenza tutore", "indirizzo tutore", "cap tutore", "email tutore",
	"telefono tutore", "scadenza certificato medico", "data tesseramento", "data scadenza tesseramento", "numero tessera"}

// subscriptionImportFolder is the storage folder of the association's import
// uploads (SubscriptionImportService.import_file_path), with its final slash.
func subscriptionImportFolder(association string) string {
	return "subscription-import/" + association + "/"
}

// subscriptionImportName is import_file_path as default_storage cleans it:
// the association's import folder, then the document's own upload name.
func subscriptionImportName(association string, created time.Time, id, filename string) (string, error) {
	name, err := documentkey.UploadName(created, uuid.MustParse(id), filename)
	if err != nil {
		return "", err
	}
	return subscriptionImportFolder(association) + name, nil
}

// subscriptionImportDistance is editdistance.eval over code points.
func subscriptionImportDistance(left, right []rune) int {
	previous := make([]int, len(right)+1)
	for j := range previous {
		previous[j] = j
	}
	for i := 1; i <= len(left); i++ {
		current := make([]int, len(right)+1)
		current[0] = i
		for j := 1; j <= len(right); j++ {
			cost := 1
			if left[i-1] == right[j-1] {
				cost = 0
			}
			current[j] = min(previous[j]+1, current[j-1]+1, previous[j-1]+cost)
		}
		previous = current
	}
	return previous[len(right)]
}

// subscriptionImportLower is str.lower() for text without İ (full case
// mapping) and Σ (final sigma), which callers refuse.
func subscriptionImportLower(text string) ([]rune, bool) {
	if strings.ContainsAny(text, "İΣ") {
		return nil, false
	}
	return []rune(strings.ToLower(text)), true
}

// subscriptionImportAutoMap is auto_map_columns(columns, map) on the
// request's own copy of SubscriptionMaps.MAP, rendered.
func subscriptionImportAutoMap(columns []string) domain.RequestObject {
	mapped := make(map[string]string, len(columns))
	keys := append([]string(nil), subscriptionImportKeys...)
	for _, column := range columns {
		lowered, _ := subscriptionImportLower(column)
		lowest, chosen := float64(utf8.RuneCountInString(column))*0.25, -1
		for index, key := range keys {
			distance := float64(subscriptionImportDistance(lowered, []rune(key)))
			if distance < lowest {
				lowest, chosen = distance, index
				if distance == 0 {
					break
				}
			}
		}
		if chosen >= 0 {
			mapped[keys[chosen]] = column
			keys = append(keys[:chosen], keys[chosen+1:]...)
		}
	}
	return subscriptionImportRendered(mapped)
}

// subscriptionImportRendered is the request's map as the response renders
// it: every key of SubscriptionMaps.MAP in order, None unless mapped.
func subscriptionImportRendered(mapped map[string]string) domain.RequestObject {
	rendered := make(domain.RequestObject, 0, len(subscriptionImportKeys))
	for _, key := range subscriptionImportKeys {
		var value any
		if column, ok := mapped[key]; ok {
			value = column
		}
		rendered = append(rendered, domain.RequestField{Name: key, Value: value})
	}
	return rendered
}

// SubscriptionImportUpload ports subscription_import_upload: the upload
// branch and the import of a stored workbook.
type SubscriptionImportUpload struct {
	store   domain.SubscriptionImportStore
	uploads *DocumentUploads
	objects domain.SubscriptionImportObjects
}

func NewSubscriptionImportUpload(store domain.SubscriptionImportStore, uploads *DocumentUploads,
	objects domain.SubscriptionImportObjects) *SubscriptionImportUpload {
	return &SubscriptionImportUpload{store: store, uploads: uploads, objects: objects}
}

// storeImportFile is upload_and_store_file: Document.objects.create (audited,
// indexed to the actor's association), then default_storage.save under the
// association's import folder.
func (s *DocumentUploads) storeImportFile(ctx context.Context, identity domain.ResolvedIdentity, association string, file domain.RequestFile,
	audit domain.LoginAuditContext) (domain.NewDocument, error) {
	tx, err := s.store.BeginDocumentUpload(ctx)
	if err != nil {
		return domain.NewDocument{}, err
	}
	defer tx.Rollback()
	var actorAssociation *string
	if identity.Actor != nil {
		if actorAssociation, err = tx.ActorAssociation(ctx, identity.Actor.ID); err != nil {
			return domain.NewDocument{}, err
		}
	}
	document := s.newDocument(file.Name)
	if err = tx.CreateDocument(ctx, document, actorAssociation, uploadAuditContext(identity, audit), s.at()); err != nil {
		return document, err
	}
	if err = tx.Commit(); err != nil {
		return document, err
	}
	name, err := subscriptionImportName(association, document.Created, document.ID, document.Filename)
	if err != nil {
		return document, err
	}
	return document, s.saveNamed(ctx, name, file.Content)
}

// Upload follows the view: the association (401 without one), data.keys(),
// then the upload branch, the mapping branch's lookups, or TypeError.
func (s *SubscriptionImportUpload) Upload(ctx context.Context, identity domain.ResolvedIdentity, body any,
	audit domain.LoginAuditContext) (domain.RequestObject, error) {
	tx, err := s.store.BeginSubscriptionImport(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := tx.Association(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return nil, err
	}
	if association == nil {
		return nil, ErrSubscriptionImportUnauthorized
	}
	data, ok := body.(domain.RequestObject)
	if body == nil {
		data, ok = domain.RequestObject{}, true
	}
	if !ok {
		return nil, ErrSubscriptionImportServerError // data.keys()
	}
	if value, present := data.Lookup("associates_file"); present {
		file, isFile := value.(domain.RequestFile)
		if !isFile {
			return nil, ErrSubscriptionImportServerError // associates_file.name
		}
		if err = tx.Rollback(); err != nil {
			return nil, err
		}
		return s.upload(ctx, identity, *association, file, audit)
	}
	_, hasDocument := data.Lookup("document_id")
	_, hasMap := data.Lookup("map")
	if !hasDocument || !hasMap {
		return nil, ErrSubscriptionImportServerError // TypeError: associates_file key not present
	}
	value, _ := data.Lookup("document_id")
	var id string
	switch typed := value.(type) {
	case string:
		parsed, err := paymentInfoParseUUID(typed)
		if err != nil {
			return nil, err
		}
		id = parsed.String()
	case nil:
		return nil, &PaymentInfoUUIDError{Detail: "one of the hex, bytes, bytes_le, fields, or int arguments must be given"}
	case domain.RequestFile:
		return nil, ErrSubscriptionImportNotPorted
	default:
		return nil, &PaymentInfoUUIDError{Detail: "'" + pythonTypeNameOf(value) + "' object has no attribute 'replace'"}
	}
	document, err := tx.Document(ctx, id)
	if err != nil {
		return nil, err
	}
	if document == nil {
		return nil, ErrSubscriptionImportServerError // ValueError('No document found')
	}
	if err = tx.Rollback(); err != nil {
		return nil, err
	}
	return s.importDocument(ctx, identity, *association, id, *document, data, audit)
}

// subscriptionImportExcel reports whether get_pandas_df_from_file reads the
// lower-cased name with read_excel.
func subscriptionImportExcel(lower string) bool {
	for _, suffix := range []string{"xls", "xlsx", "xlsm", "xlsb", "odf", "ods", "odt"} {
		if strings.HasSuffix(lower, suffix) {
			return true
		}
	}
	return false
}

// importDocument is the mapping branch after the lookup: the stored file's
// frame, read from the association's import folder (a document with no
// object there, or a failed read, is the 500 page before any write), then
// the import between the two status changes; the answer names the columns
// and the request's map, which no upload filled.
func (s *SubscriptionImportUpload) importDocument(ctx context.Context, identity domain.ResolvedIdentity, association, id string,
	document domain.SubscriptionImportDocument, data domain.RequestObject, audit domain.LoginAuditContext) (domain.RequestObject, error) {
	lower := strings.ToLower(document.Filename)
	switch {
	case strings.HasSuffix(lower, ".csv"):
		return nil, ErrSubscriptionImportNotPorted // read_csv's values
	case !subscriptionImportExcel(lower):
		return nil, ErrSubscriptionImportServerError // TypeError: file extension not recognized
	}
	name, err := subscriptionImportName(association, document.Created, id, document.Filename)
	if err != nil {
		return nil, errors.Join(ErrSubscriptionImportNotPorted, err)
	}
	key, err := documentkey.S3Key(name, s.uploads.objects.Location())
	if err != nil {
		return nil, errors.Join(ErrSubscriptionImportNotPorted, err)
	}
	content, err := s.objects.Read(ctx, key)
	if err != nil {
		return nil, errors.Join(ErrSubscriptionImportServerError, err) // ValueError('No document found') or the failed read
	}
	frame, err := pandasxlsx.Read(content)
	switch {
	case errors.Is(err, pandasxlsx.ErrInvalid):
		return nil, ErrSubscriptionImportServerError
	case err != nil:
		return nil, errors.Join(ErrSubscriptionImportNotPorted, err)
	}
	effects, err := s.subscriptionImportPlan(frame, data)
	if err != nil {
		return nil, err
	}
	var actor *string
	if identity.Actor != nil {
		actor = &identity.Actor.ID
	}
	if err = s.store.ImportRows(ctx, association, identity.ScopeUser.ID, actor, effects, uploadAuditContext(identity, audit), s.uploads.at); err != nil {
		return nil, err
	}
	list := make([]any, len(frame.Columns))
	for i, column := range frame.Columns {
		list[i] = column
	}
	return domain.RequestObject{{Name: "columns", Value: list}, {Name: "map", Value: subscriptionImportRendered(nil)}}, nil
}

func (s *SubscriptionImportUpload) upload(ctx context.Context, identity domain.ResolvedIdentity, association string, file domain.RequestFile,
	audit domain.LoginAuditContext) (domain.RequestObject, error) {
	lower := strings.ToLower(file.Name)
	csv := strings.HasSuffix(lower, ".csv")
	excel := !csv && subscriptionImportExcel(lower)
	var columns []string
	var parseErr error
	switch {
	case csv:
		columns, parseErr = pandascsv.Columns(file.Content)
		if errors.Is(parseErr, pandascsv.ErrUnsupported) {
			return nil, errors.Join(ErrSubscriptionImportNotPorted, parseErr)
		}
	case excel:
		// read_excel reads the bytes whatever the suffix; a failure (then the
		// xlrd fallback's ImportError) is the 500 page after the writes.
		var frame *pandasxlsx.Frame
		frame, parseErr = pandasxlsx.Read(file.Content)
		if errors.Is(parseErr, pandasxlsx.ErrUnsupported) {
			return nil, errors.Join(ErrSubscriptionImportNotPorted, parseErr)
		}
		if parseErr == nil {
			columns = frame.Columns
		}
	}
	for _, column := range columns {
		if _, ok := subscriptionImportLower(column); !ok {
			return nil, ErrSubscriptionImportNotPorted
		}
	}
	document, err := s.uploads.storeImportFile(ctx, identity, association, file, audit)
	if err != nil {
		return nil, err
	}
	if !csv && !excel || parseErr != nil {
		return nil, ErrSubscriptionImportServerError // TypeError, EmptyDataError or ParserError after the writes
	}
	list := make([]any, len(columns))
	for i, column := range columns {
		list[i] = column
	}
	return domain.RequestObject{{Name: "columns", Value: list}, {Name: "map", Value: subscriptionImportAutoMap(columns)},
		{Name: "document_id", Value: document.ID}}, nil
}
