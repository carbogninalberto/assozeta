package service

import (
	"context"
	"errors"
	"strings"
	"sync"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pandascsv"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrSubscriptionImportUnauthorized is the view's Response(status=401).
	ErrSubscriptionImportUnauthorized = errors.New("subscription import: no association")
	// ErrSubscriptionImportServerError is an exception the view does not handle: the 500 page.
	ErrSubscriptionImportServerError = errors.New("subscription_import_upload raised")
	// ErrSubscriptionImportNotPorted refuses, before any write, what Go does
	// not reproduce: Excel workbooks, CSV input outside pandascsv's subset,
	// columns whose str.lower() differs from Go's, the import of an existing
	// document (codicefiscale and dateutil) and a file given as document_id.
	ErrSubscriptionImportNotPorted = refusal.New("subscription import path not ported")
)

// subscriptionImportKeys is SubscriptionMaps.MAP's key order.
var subscriptionImportKeys = []string{"nome", "cognome", "sesso", "codice fiscale", "data di nascita", "città di nascita", "indirizzo",
	"città di residenza", "cap", "email", "telefono", "nome tutore", "cognome tutore", "sesso tutore", "codice fiscale tutore",
	"data di nascita tutore", "città di nascita tutore", "città di residenza tutore", "indirizzo tutore", "cap tutore", "email tutore",
	"telefono tutore", "scadenza certificato medico", "data tesseramento", "data scadenza tesseramento", "numero tessera"}

// subscriptionImportMap is the class-level SubscriptionMaps.MAP that
// auto_map_columns updates in place: one per server process, kept across
// requests, as in each Django worker process.
var subscriptionImportMap = struct {
	sync.Mutex
	values map[string]string
}{values: map[string]string{}}

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

// subscriptionImportAutoMap is auto_map_columns(columns, MAP) followed by the
// rendered MAP, under the process-wide lock.
func subscriptionImportAutoMap(columns []string) domain.RequestObject {
	subscriptionImportMap.Lock()
	defer subscriptionImportMap.Unlock()
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
			subscriptionImportMap.values[keys[chosen]] = column
			keys = append(keys[:chosen], keys[chosen+1:]...)
		}
	}
	rendered := make(domain.RequestObject, 0, len(subscriptionImportKeys))
	for _, key := range subscriptionImportKeys {
		var value any
		if column, ok := subscriptionImportMap.values[key]; ok {
			value = column
		}
		rendered = append(rendered, domain.RequestField{Name: key, Value: value})
	}
	return rendered
}

// SubscriptionImportUpload ports subscription_import_upload's upload branch.
type SubscriptionImportUpload struct {
	store   domain.SubscriptionImportStore
	uploads *DocumentUploads
}

func NewSubscriptionImportUpload(store domain.SubscriptionImportStore, uploads *DocumentUploads) *SubscriptionImportUpload {
	return &SubscriptionImportUpload{store: store, uploads: uploads}
}

// storeImportFile is upload_and_store_file: Document.objects.create (audited,
// indexed to the actor's association), then default_storage.save.
func (s *DocumentUploads) storeImportFile(ctx context.Context, identity domain.ResolvedIdentity, file domain.RequestFile,
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
	return document, s.save(ctx, document, file.Content)
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
		return s.upload(ctx, identity, file, audit)
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
	found, err := tx.DocumentExists(ctx, id)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, ErrSubscriptionImportServerError // ValueError('No document found')
	}
	return nil, ErrSubscriptionImportNotPorted
}

func (s *SubscriptionImportUpload) upload(ctx context.Context, identity domain.ResolvedIdentity, file domain.RequestFile,
	audit domain.LoginAuditContext) (domain.RequestObject, error) {
	lower := strings.ToLower(file.Name)
	csv := strings.HasSuffix(lower, ".csv")
	if !csv {
		for _, suffix := range []string{"xls", "xlsx", "xlsm", "xlsb", "odf", "ods", "odt"} {
			if strings.HasSuffix(lower, suffix) {
				return nil, ErrSubscriptionImportNotPorted // read_excel (openpyxl, then xlrd)
			}
		}
	}
	var columns []string
	var parseErr error
	if csv {
		columns, parseErr = pandascsv.Columns(file.Content)
		if errors.Is(parseErr, pandascsv.ErrUnsupported) {
			return nil, errors.Join(ErrSubscriptionImportNotPorted, parseErr)
		}
		for _, column := range columns {
			if _, ok := subscriptionImportLower(column); !ok {
				return nil, ErrSubscriptionImportNotPorted
			}
		}
	}
	document, err := s.uploads.storeImportFile(ctx, identity, file, audit)
	if err != nil {
		return nil, err
	}
	if !csv || parseErr != nil {
		return nil, ErrSubscriptionImportServerError // TypeError, EmptyDataError or ParserError after the writes
	}
	list := make([]any, len(columns))
	for i, column := range columns {
		list[i] = column
	}
	return domain.RequestObject{{Name: "columns", Value: list}, {Name: "map", Value: subscriptionImportAutoMap(columns)},
		{Name: "document_id", Value: document.ID}}, nil
}
