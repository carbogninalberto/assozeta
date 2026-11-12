package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pandascsv"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pandasxlsx"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
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
	ErrSubscriptionImportNotPorted = refusal.New("subscription import path not ported")
	// ErrSubscriptionImportFailed is the import whose rows raised: 400
	// {'msg': FAILED_MESSAGE}, nothing of the rows kept.
	ErrSubscriptionImportFailed = errors.New("subscription import: rows failed")
)

// SubscriptionImportFailedMessage is SubscriptionImportService.FAILED_MESSAGE.
const SubscriptionImportFailedMessage = "Importazione non riuscita. Controlla i dati e riprova."

// SubscriptionImportTooLarge is an upload above upload_limit(): 413 {'msg': too_large_message(limit)}.
type SubscriptionImportTooLarge struct{ Limit int64 }

func (e *SubscriptionImportTooLarge) Error() string {
	return "subscription import: file above the limit"
}

// Message is too_large_message(limit).
func (e *SubscriptionImportTooLarge) Message() string {
	size := strconv.FormatInt(e.Limit, 10) + " byte"
	if e.Limit%(1<<20) == 0 {
		size = strconv.FormatInt(e.Limit/(1<<20), 10) + " MB"
	}
	return "Il file supera la dimensione massima consentita (" + size + ")."
}

// SubscriptionImportLimits are SUBSCRIPTION_IMPORT_MAX_UPLOAD_BYTES and
// SUBSCRIPTION_IMPORT_RETENTION_HOURS.
type SubscriptionImportLimits struct {
	MaxUploadBytes int64
	RetentionHours int64
}

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

// subscriptionImportLower is str.lower(): U+0130 lowers to 'i' with a
// combining dot and a final sigma to its own form (dto.PythonLower).
func subscriptionImportLower(text string) []rune { return []rune(dto.PythonLower(text)) }

// subscriptionImportAutoMap is auto_map_columns(columns, map) on the
// request's own copy of SubscriptionMaps.MAP, rendered.
func subscriptionImportAutoMap(columns []string) domain.RequestObject {
	mapped := make(map[string]string, len(columns))
	keys := append([]string(nil), subscriptionImportKeys...)
	for _, column := range columns {
		lowered := subscriptionImportLower(column)
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

// subscriptionImportLabels is columns.tolist() as the JSON renderer writes
// it: text, numbers and booleans as they are, a datetime, a date and a time
// of day as isoformat(), a duration as str() of its seconds, NaT as 'NaT'.
// nil: a label that is not a number (an error cell), which the renderer
// refuses (ValueError).
func subscriptionImportLabels(labels []pandasxlsx.Value) []any {
	rendered := make([]any, 0, len(labels))
	for _, label := range labels {
		switch label.Kind {
		case pandasxlsx.String:
			rendered = append(rendered, label.S)
		case pandasxlsx.Int, pandasxlsx.Int64:
			rendered = append(rendered, json.Number(strconv.FormatInt(label.I, 10)))
		case pandasxlsx.BigInt, pandasxlsx.Uint64:
			rendered = append(rendered, json.Number(label.Str()))
		case pandasxlsx.Float:
			if math.IsNaN(label.F) || math.IsInf(label.F, 0) {
				return nil
			}
			rendered = append(rendered, label.F)
		case pandasxlsx.Bool, pandasxlsx.NumpyBool:
			rendered = append(rendered, label.B)
		case pandasxlsx.Time:
			rendered = append(rendered, strings.Replace(label.TimeText(), " ", "T", 1))
		case pandasxlsx.Timedelta, pandasxlsx.PyTimedelta:
			rendered = append(rendered, dto.PythonFloatText(float64(label.D/time.Microsecond)/1e6))
		case pandasxlsx.NaT, pandasxlsx.Date, pandasxlsx.TimeOfDay:
			rendered = append(rendered, label.Str())
		default:
			return nil
		}
	}
	return rendered
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
	limits  SubscriptionImportLimits
}

func NewSubscriptionImportUpload(store domain.SubscriptionImportStore, uploads *DocumentUploads,
	objects domain.SubscriptionImportObjects, limits SubscriptionImportLimits) *SubscriptionImportUpload {
	return &SubscriptionImportUpload{store: store, uploads: uploads, objects: objects, limits: limits}
}

var (
	subscriptionImportStampFolder    = regexp.MustCompile(`^[0-9]+(\.[0-9]+)?$`)
	subscriptionImportDocumentFolder = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
)

// removeExpired is remove_expired_uploads: the association's import uploads
// whose folder timestamp is older than the retention time lose their
// Document (audited) and their stored objects, in folder order.
func (s *SubscriptionImportUpload) removeExpired(ctx context.Context, identity domain.ResolvedIdentity, association string,
	audit domain.LoginAuditContext) error {
	prefix, err := documentkey.S3Key(subscriptionImportFolder(association), s.uploads.objects.Location())
	if err != nil {
		return err
	}
	keys, err := s.objects.Keys(ctx, prefix)
	if err != nil {
		return err
	}
	cutoff := float64(s.uploads.now().UnixMicro())/1e6 - float64(s.limits.RetentionHours)*3600
	var ids, expired []string
	seen := map[string]bool{}
	for _, key := range keys {
		parts := strings.Split(strings.TrimPrefix(key, prefix), "/")
		if len(parts) != 3 || parts[2] == "" || !subscriptionImportStampFolder.MatchString(parts[0]) ||
			!subscriptionImportDocumentFolder.MatchString(parts[1]) {
			continue
		}
		if created, err := strconv.ParseFloat(parts[0], 64); err != nil || !(created < cutoff) {
			continue
		}
		if !seen[parts[1]] {
			seen[parts[1]] = true
			ids = append(ids, parts[1])
		}
		expired = append(expired, key)
	}
	if len(expired) == 0 {
		return nil
	}
	var actor *string
	if identity.Actor != nil {
		actor = &identity.Actor.ID
	}
	if err = s.store.RemoveDocuments(ctx, ids, actor, uploadAuditContext(identity, audit), s.uploads.at); err != nil {
		return err
	}
	for _, key := range expired {
		if err = s.objects.Delete(ctx, key); err != nil {
			return err
		}
	}
	return nil
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
	audit domain.LoginAuditContext, spooled bool) (domain.RequestObject, error) {
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
		if int64(len(file.Content)) > s.limits.MaxUploadBytes {
			return nil, &SubscriptionImportTooLarge{Limit: s.limits.MaxUploadBytes}
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
		// uuid.UUID(file): the upload class depends on the request's length
		// (FILE_UPLOAD_MAX_MEMORY_SIZE), which spooled reports.
		class := "InMemoryUploadedFile"
		if spooled {
			class = "TemporaryUploadedFile"
		}
		return nil, &PaymentInfoUUIDError{Detail: "'" + class + "' object has no attribute 'replace'"}
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
// the import between the two status changes, the columns of codes read as
// text. Rows that raise answer ErrSubscriptionImportFailed after the status
// changes; otherwise the upload is removed (its Document with the import, then
// its object) and the answer names the columns and the request's map, which
// no upload filled.
func (s *SubscriptionImportUpload) importDocument(ctx context.Context, identity domain.ResolvedIdentity, association, id string,
	document domain.SubscriptionImportDocument, data domain.RequestObject, audit domain.LoginAuditContext) (domain.RequestObject, error) {
	lower := strings.ToLower(document.Filename)
	csv := strings.HasSuffix(lower, ".csv")
	if !csv && !subscriptionImportExcel(lower) {
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
	mapping, _ := data.Lookup("map")
	text := subscriptionImportTextColumns(mapping)
	textual := func(column string) bool { return text[column] }
	var names []any
	var rows [][]any
	var lookup func(column any) (int, bool, bool, error)
	if csv {
		frame, err := pandascsv.ReadText(content, textual)
		switch {
		case errors.Is(err, pandascsv.ErrUnsupported):
			return nil, errors.Join(ErrSubscriptionImportNotPorted, err)
		case err != nil:
			return nil, errors.Join(ErrSubscriptionImportServerError, err) // EmptyDataError, ParserError, UnicodeDecodeError
		}
		// read_csv's labels are text: any other key is no column.
		positions := make(map[string]int, len(frame.Columns))
		for index, column := range frame.Columns {
			names = append(names, column)
			positions[column] = index
		}
		lookup = func(column any) (int, bool, bool, error) {
			text, isText := column.(string)
			index, found := positions[text]
			return index, isText && found, false, nil
		}
		for _, cells := range frame.Rows {
			row := make([]any, len(cells))
			for index, cell := range cells {
				row[index] = subscriptionImportCSVCell(cell)
			}
			rows = append(rows, row)
		}
	} else {
		frame, err := pandasxlsx.ReadAll(content, textual)
		switch {
		case errors.Is(err, pandasxlsx.ErrInvalid):
			return nil, ErrSubscriptionImportServerError
		case err != nil:
			return nil, errors.Join(ErrSubscriptionImportNotPorted, err)
		}
		names, lookup = subscriptionImportLabels(frame.Labels), frame.Lookup
		for _, cells := range frame.Rows {
			row := make([]any, len(cells))
			for index, cell := range cells {
				row[index] = subscriptionImportCell(cell)
			}
			rows = append(rows, row)
		}
	}
	effects, err := s.subscriptionImportPlan(lookup, rows, data)
	if err != nil {
		return nil, err
	}
	var actor *string
	if identity.Actor != nil {
		actor = &identity.Actor.ID
	}
	effects.DocumentID = id
	if err = s.store.ImportRows(ctx, association, identity.ScopeUser.ID, actor, effects, uploadAuditContext(identity, audit), s.uploads.at); err != nil {
		return nil, err
	}
	if effects.Failed {
		return nil, ErrSubscriptionImportFailed
	}
	// remove_upload's default_storage.delete: a failure is logged by the view and the import stands.
	_ = s.objects.Delete(ctx, key)
	if names == nil && !csv {
		return nil, ErrSubscriptionImportServerError // a label the renderer cannot write, after the import
	}
	if names == nil {
		names = []any{}
	}
	return domain.RequestObject{{Name: "columns", Value: names}, {Name: "map", Value: subscriptionImportRendered(nil)}}, nil
}

func (s *SubscriptionImportUpload) upload(ctx context.Context, identity domain.ResolvedIdentity, association string, file domain.RequestFile,
	audit domain.LoginAuditContext) (domain.RequestObject, error) {
	lower := strings.ToLower(file.Name)
	csv := strings.HasSuffix(lower, ".csv")
	excel := !csv && subscriptionImportExcel(lower)
	var columns []string
	labels := []any{}
	var parseErr error
	switch {
	case csv:
		var frame *pandascsv.Frame
		frame, parseErr = pandascsv.Read(file.Content)
		if errors.Is(parseErr, pandascsv.ErrUnsupported) {
			return nil, errors.Join(ErrSubscriptionImportNotPorted, parseErr)
		}
		if parseErr == nil {
			columns = frame.Columns
			for _, column := range columns {
				labels = append(labels, column)
			}
		}
	case excel:
		// read_excel reads the bytes whatever the suffix; a failure (then the
		// xlrd fallback's ImportError) is the 500 page after the writes.
		var frame *pandasxlsx.Frame
		frame, parseErr = pandasxlsx.ReadAll(file.Content, nil)
		if errors.Is(parseErr, pandasxlsx.ErrUnsupported) {
			return nil, errors.Join(ErrSubscriptionImportNotPorted, parseErr)
		}
		if parseErr == nil {
			// auto_map_columns maps str() of each label.
			columns, labels = frame.Columns, subscriptionImportLabels(frame.Labels)
		}
	}
	document, err := s.uploads.storeImportFile(ctx, identity, association, file, audit)
	if err != nil {
		return nil, err
	}
	// The view logs a failed cleanup and goes on.
	_ = s.removeExpired(ctx, identity, association, audit)
	if !csv && !excel || parseErr != nil || labels == nil {
		// TypeError, EmptyDataError or ParserError after the writes, or a label the renderer cannot write.
		return nil, ErrSubscriptionImportServerError
	}
	return domain.RequestObject{{Name: "columns", Value: labels}, {Name: "map", Value: subscriptionImportAutoMap(columns)},
		{Name: "document_id", Value: document.ID}}, nil
}
