package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/instancelogo"
	"github.com/google/uuid"
)

type modulesResponsesReader interface {
	ModuleByLink(ctx context.Context, link string) (*domain.ModuleRow, error)
	ResponseAttachments(ctx context.Context, id, module string) (*string, error)
	PublicAssociation(ctx context.Context, association string) (domain.ModulePublicAssociation, error)
	AssociationOwner(ctx context.Context, association string) (string, error)
}

// ModuleAttachmentStorage is the default (S3) storage as default_storage.save uses it.
type ModuleAttachmentStorage interface {
	Exists(ctx context.Context, name string) (bool, error)
	Save(ctx context.Context, name string, body []byte, parameters instancelogo.WriteParameters) error
}

// ModuleAttachmentSettings are the storage settings of the default storage:
// AWS_S3_OBJECT_PARAMETERS CacheControl, AWS_DEFAULT_ACL, AWS_S3_FILE_OVERWRITE.
type ModuleAttachmentSettings struct {
	CacheControl  string
	DefaultACL    *string
	FileOverwrite bool
	// PublicUploadLimit is PUBLIC_UPLOAD_MAX_BYTES (0: no limit).
	PublicUploadLimit int64
}

// PublicUploadTooLargeError is the 413 of an attachment above the upload limit,
// answered before any write.
type PublicUploadTooLargeError struct{ Limit int64 }

func (e *PublicUploadTooLargeError) Error() string { return PublicUploadTooLarge(e.Limit) }

// ModulesResponses follows modules_custom_link_info, modules_response_add,
// modules_response_approve, modules_response_delete and
// modules_response_add_attachment (BE/application/views/modules_views.py).
type ModulesResponses struct {
	reader   modulesResponsesReader
	store    domain.ModulesResponsesStore
	profiles modulesReadsProfiles
	storage  ModuleAttachmentStorage
	settings ModuleAttachmentSettings
	now      func() time.Time
	ids      func() string
}

func NewModulesResponses(reader modulesResponsesReader, store domain.ModulesResponsesStore, profiles modulesReadsProfiles,
	storage ModuleAttachmentStorage, settings ModuleAttachmentSettings, now func() time.Time, ids func() string) *ModulesResponses {
	return &ModulesResponses{reader: reader, store: store, profiles: profiles, storage: storage, settings: settings, now: now, ids: ids}
}

// ModuleBody is request.data, read when the view first touches it (a parse
// error is DRF's 400 at that point); form reports a QueryDict body.
type ModuleBody func() (value any, form bool, err error)

func (s *ModulesResponses) at() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

func (s *ModulesResponses) enabled(row domain.ModuleRow) bool {
	if row.AlwaysActive {
		return true
	}
	if row.StartDate == nil || row.EndDate == nil {
		return false
	}
	today := s.now().UTC().Format("2006-01-02")
	return *row.StartDate <= today && today <= *row.EndDate
}

// Info follows modules_custom_link_info. request.user is never read.
func (s *ModulesResponses) Info(ctx context.Context, link string, responseID *string) (map[string]any, error) {
	row, err := s.reader.ModuleByLink(ctx, link)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, domain.ErrModuleNotFound
	}
	// The association is module.sport_association, the row the foreign key
	// resolves by id; the page of a deleted one answers as an unknown link.
	var association any
	if row.AssociationID != nil {
		public, err := s.reader.PublicAssociation(ctx, *row.AssociationID)
		if err != nil {
			return nil, err
		}
		if public.Deleted {
			return nil, domain.ErrModuleNotFound
		}
		association = map[string]any{"denomination": public.Denomination}
	}
	var data any
	if responseID != nil {
		id, err := paymentInfoParseUUID(*responseID)
		if err != nil {
			return nil, err
		}
		docs, err := s.reader.ResponseAttachments(ctx, id.String(), row.ID)
		if err != nil {
			return nil, err
		}
		if docs != nil {
			data = map[string]any{"attachments_docs": json.RawMessage(*docs)}
		}
	}
	if !s.enabled(*row) {
		return nil, domain.ErrModuleNotActive
	}
	// PublicModuleSerializer: the fields the form reads.
	module := map[string]any{
		"module_id": row.ID, "title": row.Title, "response_message": row.ResponseMessage,
		"allow_attachments": row.AllowAttachments, "only_users": row.OnlyUsers, "elements": json.RawMessage(row.Elements),
		"sport_association": association,
	}
	return map[string]any{"data": module, "response_data": data}, nil
}

// association is request.user.sport_association.
func modulesResponsesAssociation(ctx context.Context, tx domain.ModulesResponsesTransaction, identity domain.ResolvedIdentity) (string, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return "", &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	return tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
}

// Add follows modules_response_add and returns the new response's id.
func (s *ModulesResponses) Add(ctx context.Context, identity domain.ResolvedIdentity, raw string, body ModuleBody,
	audit domain.LoginAuditContext) (string, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return "", err
	}
	tx, err := s.store.BeginModulesResponses(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	module, err := tx.ModuleByID(ctx, id.String())
	if err != nil {
		return "", err
	}
	if module == nil {
		return "", domain.ErrModuleNotFound
	}
	if !s.enabled(*module) {
		return "", domain.ErrModuleNotEnabled
	}
	if module.MaxResponses != nil && *module.MaxResponses != 0 && !module.QueueMode {
		count, err := tx.ApprovedCount(ctx, module.ID)
		if err != nil {
			return "", err
		}
		if count >= *module.MaxResponses {
			return "", domain.ErrModuleMaxResponses
		}
	}
	if module.OnlyUsers && identity.ScopeUser == nil {
		return "", domain.ErrModuleOnlyUsers
	}
	// payment_required: the view creates nothing.
	found, last, err := tx.LastProgressive(ctx, module.ID)
	if err != nil {
		return "", err
	}
	number := int64(0)
	if found {
		if last == nil {
			// None + 1 raises a TypeError.
			return "", errors.New("module response progressive number is None")
		}
		number = *last + 1
	}
	var queue *int64
	if module.QueueMode {
		position, err := tx.LastQueuePosition(ctx, module.ID)
		if err != nil {
			return "", err
		}
		next := int64(1)
		if position != nil {
			next = *position + 1
		}
		queue = &next
	}
	value, form, err := body()
	if err != nil {
		return "", err
	}
	if form {
		// request.data is a QueryDict: json.dumps reads it through items(), the
		// last value of every key. An uploaded file is not JSON serializable:
		// the create raises before anything is written.
		object, _ := value.(domain.RequestObject)
		last := domain.RequestObject{}
		positions := map[string]int{}
		for _, field := range object {
			if _, text := field.Value.(string); !text {
				return "", errors.New("module response form value is not JSON serializable")
			}
			if at, seen := positions[field.Name]; seen {
				last[at].Value = field.Value
				continue
			}
			positions[field.Name] = len(last)
			last = append(last, field)
		}
		value = last
	}
	if value == nil {
		// JSON null: the NOT NULL response column rejects the create.
		return "", errors.New("module response payload is null")
	}
	at := s.at()
	record := domain.ModuleResponseAudit{Context: subscriptionMiscAudit(identity, audit), At: at}
	response := domain.ModuleResponseCreate{ID: s.ids(), ModuleID: module.ID, CreationDate: at, ProgressiveNumber: number,
		QueuePosition: queue, Response: dto.ModuleJSONText(value), Approved: !module.RequireApproval}
	if err = tx.CreateResponse(ctx, response, module.AssociationID, record); err != nil {
		return "", err
	}
	if identity.ScopeUser != nil {
		if err = tx.SaveResponseUser(ctx, response.ID, identity.ScopeUser.ID, module.AssociationID, record); err != nil {
			return "", err
		}
	}
	if module.QueueMode {
		if err = tx.UpdateQueue(ctx, module.ID, module.AssociationID, record); err != nil {
			return "", err
		}
	}
	return response.ID, tx.Commit()
}

// owned is is_valid_uuid and the response within request.user.sport_association.
func (s *ModulesResponses) owned(ctx context.Context, tx domain.ModulesResponsesTransaction, identity domain.ResolvedIdentity,
	raw string) (*domain.ModuleResponseRef, error) {
	association, err := modulesResponsesAssociation(ctx, tx, identity)
	if err != nil {
		return nil, err
	}
	ref, err := tx.ResponseInAssociation(ctx, raw, association)
	if err != nil {
		return nil, err
	}
	if ref == nil {
		return nil, domain.ErrModuleResponseNotFound
	}
	return ref, nil
}

// Approve follows modules_response_approve.
func (s *ModulesResponses) Approve(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginModulesResponses(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	ref, err := s.owned(ctx, tx, identity, id.String())
	if err != nil {
		return err
	}
	record := domain.ModuleResponseAudit{Context: subscriptionMiscAudit(identity, audit), At: s.at()}
	if err = tx.ApproveResponse(ctx, *ref, record); err != nil {
		return err
	}
	if ref.QueueMode {
		if err = tx.UpdateQueue(ctx, ref.ModuleID, ref.AssociationID, record); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// Delete follows modules_response_delete.
func (s *ModulesResponses) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginModulesResponses(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	ref, err := s.owned(ctx, tx, identity, id.String())
	if err != nil {
		return err
	}
	record := domain.ModuleResponseAudit{Context: subscriptionMiscAudit(identity, audit), At: s.at()}
	if err = tx.DeleteResponse(ctx, ref.ID, record); err != nil {
		return err
	}
	if ref.QueueMode {
		if err = tx.UpdateQueue(ctx, ref.ModuleID, ref.AssociationID, record); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// errModuleBodyType is a TypeError of `in` or of indexing request.data.
var errModuleBodyType = errors.New("module attachment request data type error")

// moduleContains is `key in request.data` and request.data[key] for the
// JSON values a body can have: a dict looks the key up, a list or a str
// answers membership but raises when indexed, anything else raises.
func moduleContains(body any, key string) (bool, any, error) {
	switch value := body.(type) {
	case domain.RequestObject:
		for index := len(value) - 1; index >= 0; index-- {
			if value[index].Name == key {
				return true, value[index].Value, nil
			}
		}
		return false, nil, nil
	case []any:
		for _, item := range value {
			if text, ok := item.(string); ok && text == key {
				return true, nil, errModuleBodyType
			}
		}
		return false, nil, nil
	case string:
		if strings.Contains(value, key) {
			return true, nil, errModuleBodyType
		}
		return false, nil, nil
	}
	return false, nil, errModuleBodyType
}

// AttachmentResult is the add-attachment view's 200 message.
type AttachmentResult string

const (
	AttachmentAdded   AttachmentResult = "attachment added"
	AttachmentDeleted AttachmentResult = "attachment deleted"
)

// Attachment follows modules_response_add_attachment for POST and DELETE.
func (s *ModulesResponses) Attachment(ctx context.Context, identity domain.ResolvedIdentity, method, raw string, body ModuleBody, audit domain.LoginAuditContext) (AttachmentResult, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return "", err
	}
	tx, err := s.store.BeginModulesResponses(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	exists, err := tx.ResponseExists(ctx, id.String())
	if err != nil {
		return "", err
	}
	value, form, err := body()
	if err != nil {
		return "", err
	}
	// A QueryDict body answers 'in' and [] like a dict of its last values.
	_ = form
	record := domain.ModuleResponseAudit{Context: subscriptionMiscAudit(identity, audit), At: s.at()}
	if method == "DELETE" {
		return s.detach(ctx, tx, id.String(), exists, value, record)
	}
	return s.attach(ctx, tx, id.String(), exists, value, record)
}

func (s *ModulesResponses) detach(ctx context.Context, tx domain.ModulesResponsesTransaction, response string, exists bool, body any,
	record domain.ModuleResponseAudit) (AttachmentResult, error) {
	present, value, err := moduleContains(body, "document_id")
	if err != nil {
		return "", err
	}
	if !present || value == nil {
		return "", domain.ErrModuleDocumentNotFound
	}
	document, ok := moduleDocumentKey(value)
	if !ok {
		// The lookup's ValidationError is not handled by DRF.
		return "", errors.New("module attachment document_id is not a UUID")
	}
	found, err := tx.DocumentExists(ctx, document.String())
	if err != nil {
		return "", err
	}
	if !found {
		return "", domain.ErrModuleDocumentNotFound
	}
	if !exists {
		// None.attachments raises before the document is touched.
		return "", errors.New("module response is None")
	}
	elsewhere, err := tx.DocumentReferencedElsewhere(ctx, document.String())
	if err != nil {
		return "", err
	}
	if elsewhere {
		// document.delete() collects the rows of other tables that reference it.
		if err = tx.DeleteReferencedDocument(ctx, response, document.String(), record); err != nil {
			return "", err
		}
		return AttachmentDeleted, tx.Commit()
	}
	if err = tx.DeleteDocument(ctx, response, document.String(), record); err != nil {
		return "", err
	}
	return AttachmentDeleted, tx.Commit()
}

// moduleDocumentKey is UUIDField.to_python of the lookup value: a str through
// uuid.UUID(hex=...), an int (a bool included) through uuid.UUID(int=...),
// which accepts 0 <= value < 2**128; any other value is refused.
func moduleDocumentKey(value any) (uuid.UUID, bool) {
	switch v := value.(type) {
	case string:
		id, err := paymentInfoParseUUID(v)
		return id, err == nil
	case bool:
		id := uuid.UUID{}
		if v {
			id[15] = 1
		}
		return id, true
	case json.Number:
		number, ok := new(big.Int).SetString(string(v), 10)
		if !ok || number.Sign() < 0 || number.BitLen() > 128 {
			return uuid.UUID{}, false
		}
		id := uuid.UUID{}
		number.FillBytes(id[:])
		return id, true
	}
	return uuid.UUID{}, false
}

func (s *ModulesResponses) attach(ctx context.Context, tx domain.ModulesResponsesTransaction, response string, exists bool, body any,
	record domain.ModuleResponseAudit) (AttachmentResult, error) {
	hasName, _, nameErr := moduleContains(body, "filename")
	if nameErr != nil && !hasName {
		return "", nameErr
	}
	if !hasName {
		return AttachmentAdded, nil
	}
	hasData, _, dataErr := moduleContains(body, "base64")
	if dataErr != nil && !hasData {
		return "", dataErr
	}
	if !hasData {
		return AttachmentAdded, nil
	}
	if nameErr != nil {
		// request.data['filename'] on a list or a str.
		return "", nameErr
	}
	_, name, _ := moduleContains(body, "filename")
	_, data, _ := moduleContains(body, "base64")
	if text, textual := data.(string); textual && s.settings.PublicUploadLimit > 0 {
		// The decoded size is checked before the document is created; a value that
		// does not decode goes on to fail where it always did.
		if decoded, err := pythonB64Decode(text); err == nil && int64(len(decoded)) > s.settings.PublicUploadLimit {
			return "", &PublicUploadTooLargeError{Limit: s.settings.PublicUploadLimit}
		}
	}
	if _, upload := name.(domain.RequestUpload); upload {
		// str(UploadedFile) is the name the multipart parser sanitized (not ported).
		return "", domain.ErrModuleResponsesNotPorted
	}
	if name == nil {
		// The NOT NULL filename column rejects the create.
		return "", errors.New("module attachment filename is null")
	}
	// CharField stores str() of the value; the instance keeps the value itself.
	_, named := name.(string)
	filename := dto.PythonStr(name)
	if !utf8.ValidString(filename) {
		return "", domain.ErrModuleResponsesNotPorted
	}
	if strings.ContainsRune(filename, 0) || utf8.RuneCountInString(filename) > 255 {
		// The database refuses the text: nothing is written.
		return "", errors.New("module attachment filename is not storable")
	}
	document := domain.ModuleDocumentCreate{ID: s.ids(), Filename: filename, Token: s.ids(), CreationDate: record.At}
	if err := tx.CreateDocument(ctx, document, record); err != nil {
		return "", err
	}
	// Document.objects.create commits before the decode and the upload.
	if err := tx.Commit(); err != nil {
		return "", err
	}
	if !named {
		// os.path.join(storing_path, document.filename) on a value that is not a str.
		return "", errors.New("module attachment filename is not a str")
	}
	encoded, textual := data.(string)
	if !textual {
		// decode_base64 of a value that is not a str (an uploaded file included).
		return "", errors.New("module attachment base64 is not a str")
	}
	content, err := pythonB64Decode(encoded)
	if err != nil {
		return "", err
	}
	// Storage.save validates the name (SuspiciousFileOperation: the 400 page)
	// and S3Storage stores it under clean_name(name).
	stored, err := documentkey.UploadName(document.CreationDate, uuid.MustParse(document.ID), filename)
	if err != nil {
		return "", err
	}
	kind, known, encoding, err := pymime.GuessType(stored)
	if err != nil {
		return "", err
	}
	if !known {
		kind = "application/octet-stream"
	}
	if !s.settings.FileOverwrite {
		// get_available_name: a taken name would be saved under a generated alternative.
		taken, err := s.storage.Exists(ctx, stored)
		if err != nil {
			return "", err
		}
		if taken {
			return "", domain.ErrModuleResponsesNotPorted
		}
	}
	if err = s.storage.Save(ctx, stored, content, instancelogo.WriteParameters{ContentType: kind, ContentEncoding: encoding,
		CacheControl: s.settings.CacheControl, ACL: s.settings.DefaultACL}); err != nil {
		return "", err
	}
	if !exists {
		// None.attachments raises after the upload.
		return "", errors.New("module response is None")
	}
	link, err := s.store.BeginModulesResponses(ctx)
	if err != nil {
		return "", err
	}
	defer link.Rollback()
	if err = link.LinkDocument(ctx, response, document.ID); err != nil {
		return "", err
	}
	return AttachmentAdded, link.Commit()
}

// pythonB64Decode is the view's decode_base64: the text after ';base64,'
// (when present), '=' padding to a multiple of four, then
// base64.b64decode(data) without validation (binascii.a2b_base64,
// strict_mode=False, Python 3.13): characters outside the alphabet are
// skipped; '=' characters are skipped too, except that enough of them after
// the last data character close an incomplete final group.
func pythonB64Decode(data string) ([]byte, error) {
	if parts := strings.Split(data, ";base64,"); len(parts) > 1 {
		data = parts[1]
	}
	if missing := utf8.RuneCountInString(data) % 4; missing != 0 {
		data += strings.Repeat("=", 4-missing)
	}
	for i := 0; i < len(data); i++ {
		if data[i] >= 0x80 {
			return nil, errors.New("string argument should contain only ASCII characters")
		}
	}
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
	out := make([]byte, 0, len(data)*3/4)
	quad, pads := 0, 0
	var left byte
	for i := 0; i < len(data); i++ {
		c := data[i]
		if c == '=' {
			// Python 3.13 non-strict mode: a pad only counts towards closing
			// the last group; data after it keeps decoding.
			if quad >= 2 {
				pads++
			}
			continue
		}
		value := strings.IndexByte(alphabet, c)
		if value < 0 {
			continue
		}
		pads = 0
		v := byte(value)
		switch quad {
		case 0:
			quad, left = 1, v
		case 1:
			out = append(out, left<<2|v>>4)
			quad, left = 2, v&0x0f
		case 2:
			out = append(out, left<<4|v>>2)
			quad, left = 3, v&0x03
		case 3:
			out = append(out, left<<6|v)
			quad, left = 0, 0
		}
	}
	if quad != 0 && (quad == 1 || quad+pads < 4) {
		return nil, errors.New("invalid base64 padding")
	}
	return out, nil
}
