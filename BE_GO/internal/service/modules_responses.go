package service

import (
	"context"
	"encoding/json"
	"errors"
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
	Response(ctx context.Context, id string) (*domain.ModuleResponseRow, error)
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
}

// modulesResponsesAssociations reads the profile of an association by id.
type modulesResponsesAssociations interface {
	ReadAssociation(ctx context.Context, association string) (*dto.AssociationProfile, error)
}

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

// moduleInitial is ModuleResponseSerializer(None).data: get_initial() of the
// writable fields.
func moduleInitial() map[string]any {
	return map[string]any{"module_response_id": nil, "module": nil, "creation_date": nil, "progressive_response_number": nil,
		"queue_position": nil, "response": nil, "approved": false, "user": nil, "payment": nil, "attachments": []any{}}
}

func moduleResponseData(response domain.ModuleResponseRow) map[string]any {
	return map[string]any{
		"module_response_id": response.ID, "module": response.ModuleID, "creation_date": dto.DRFUTCDateTime(response.CreationDate),
		"progressive_response_number": response.ProgressiveNumber, "queue_position": response.QueuePosition,
		"response": json.RawMessage(response.Response), "approved": response.Approved, "user": response.UserID,
		"payment": response.PaymentID, "attachments": json.RawMessage(response.Attachments),
		"attachments_docs": json.RawMessage(response.AttachmentsDocs),
	}
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
	var data any
	if responseID != nil {
		id, err := paymentInfoParseUUID(*responseID)
		if err != nil {
			return nil, err
		}
		response, err := s.reader.Response(ctx, id.String())
		if err != nil {
			return nil, err
		}
		if response == nil {
			data = moduleInitial()
		} else {
			data = moduleResponseData(*response)
		}
	}
	if !s.enabled(*row) {
		return nil, domain.ErrModuleNotActive
	}
	module := (&ModulesReads{now: s.now}).moduleFields(*row)
	module["sport_association"] = nil
	if row.AssociationID != nil {
		// The nested SportAssociationSerializer renders module.sport_association,
		// the row the related descriptor resolves by id.
		associations, ok := s.profiles.(modulesResponsesAssociations)
		if !ok {
			return nil, domain.ErrModuleResponsesNotPorted
		}
		profile, err := associations.ReadAssociation(ctx, *row.AssociationID)
		if err != nil {
			return nil, err
		}
		module["sport_association"] = profile
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
		// A QueryDict (and uploaded files) as JSONField value is not ported.
		return "", domain.ErrModuleResponsesNotPorted
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
	if form {
		// QueryDict bodies are not ported.
		return "", domain.ErrModuleResponsesNotPorted
	}
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
	text, ok := value.(string)
	if !ok {
		// UUIDField.to_python of other values (UUID(int=...) for ints and
		// booleans) is not ported.
		return "", domain.ErrModuleResponsesNotPorted
	}
	document, err := paymentInfoParseUUID(text)
	if err != nil {
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
		// document.delete() would cascade to (or set null on) other rows.
		return "", domain.ErrModuleResponsesNotPorted
	}
	if err = tx.DeleteDocument(ctx, response, document.String(), record); err != nil {
		return "", err
	}
	return AttachmentDeleted, tx.Commit()
}

// attachmentNamePorted reports the filenames whose storage path is ported:
// a plain file name (no path separators, not ”, '.' or '..', no NUL) of
// at most 255 characters. Compression suffixes are stored with mimetypes'
// encoding as ContentEncoding (S3Storage._get_write_parameters).
func attachmentNamePorted(name string) bool {
	return name != "" && name != "." && name != ".." && !strings.ContainsAny(name, "/\\\x00") && utf8.ValidString(name) &&
		utf8.RuneCountInString(name) <= 255
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
	filename, ok := name.(string)
	encoded, textual := data.(string)
	if !ok || !textual || !attachmentNamePorted(filename) {
		return "", domain.ErrModuleResponsesNotPorted
	}
	document := domain.ModuleDocumentCreate{ID: s.ids(), Filename: filename, Token: s.ids(), CreationDate: record.At}
	if err := tx.CreateDocument(ctx, document, record); err != nil {
		return "", err
	}
	// Document.objects.create commits before the decode and the upload.
	if err := tx.Commit(); err != nil {
		return "", err
	}
	content, err := pythonB64Decode(encoded)
	if err != nil {
		return "", err
	}
	stored, err := documentkey.StoredName(document.CreationDate, uuid.MustParse(document.ID), filename, nil)
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
