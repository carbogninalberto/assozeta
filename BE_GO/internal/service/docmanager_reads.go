package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
	"github.com/google/uuid"
)

const (
	documentDownloadTokenSalt    = "docmanager.association-export-download"
	documentDownloadTokenPurpose = "association-export"
	documentDownloadTokenMaxAge  = 60 * 60
)

// DocumentObjects is the default storage as PrintingService reads it.
type DocumentObjects interface {
	Location() string
	Exists(ctx context.Context, key string) (bool, error)
	Open(ctx context.Context, key string) (*documentobjects.Object, error)
}

// DocmanagerReads ports retrieve_document (document_view.py) and the checks of
// application.document_scope.delete_document that precede its ownership walk.
type DocmanagerReads struct {
	store   domain.DocmanagerReadsStore
	objects DocumentObjects
	actors  *ActorAuthentication
	secret  string
	now     func() time.Time
	guess   func(string) (string, bool, error)
	deleter domain.DocumentDeleteStore
	printer DocumentReprinter
}

// DocumentReprinter is PrintingService.print_and_store_pdf as download_file
// and view_file call it: the outcome is not read.
type DocumentReprinter interface {
	PrintAndStore(ctx context.Context, view string, authorization *string, document domain.StoredDocument)
}

// WithReprinter wires the renderer and the storage task for missing objects.
func (s *DocmanagerReads) WithReprinter(printer DocumentReprinter) *DocmanagerReads {
	s.printer = printer
	return s
}

func NewDocmanagerReads(store domain.DocmanagerReadsStore, objects DocumentObjects, actors *ActorAuthentication, secret string,
	now func() time.Time) *DocmanagerReads {
	return &DocmanagerReads{store: store, objects: objects, actors: actors, secret: secret, now: now, guess: pymime.Guess}
}

// DocmanagerReply is a JSON answer or, with Stream set, the streamed object.
type DocmanagerReply struct {
	Status int
	Body   any
	Stream *DocumentStream
}

// DocumentStream is PrintingService._stream_file_response's response.
type DocumentStream struct {
	Object          *documentobjects.Object
	ContentType     string
	Disposition     *string
	ContentLength   *int64
	ContentEncoding string
}

// RetrieveRequest carries what retrieve_document reads from the request.
type RetrieveRequest struct {
	UID      string
	Query    domain.RequestObject
	Cookie   string // the raw Cookie header(s), joined with "; "
	Group    string // X-Group-Id
	Identity domain.ResolvedIdentity
	// Authorization is request.headers.get('authorization') (nil when absent).
	Authorization *string
}

func docmanagerJSON(status int, body any) *DocmanagerReply {
	return &DocmanagerReply{Status: status, Body: body}
}

func queryText(query domain.RequestObject, name string) (string, bool) {
	value, present := query.Lookup(name)
	text, _ := value.(string)
	return text, present
}

// Retrieve follows retrieve_document line by line.
func (s *DocmanagerReads) Retrieve(ctx context.Context, request RetrieveRequest) (*DocmanagerReply, error) {
	id, err := paymentInfoParseUUID(request.UID)
	if err != nil {
		return nil, err
	}
	uid := id.String()
	downloadValue, downloadPresent := queryText(request.Query, "download")
	download := !(downloadPresent && downloadValue == "false")
	token, _ := queryText(request.Query, "token")
	downloadToken, _ := queryText(request.Query, "download_token")
	identity := request.Identity
	user := identity.ScopeUser
	if !identity.Impersonating {
		if user, err = s.requestUser(ctx, identity, request.Cookie, token); err != nil {
			return nil, err
		}
	}
	group := supplierGroup(request.Group)

	var document *domain.StoredDocument
	exportArchive := false
	if downloadToken != "" {
		loaded, err := djangoSigningLoads(downloadToken, documentDownloadTokenSalt, s.secret, documentDownloadTokenMaxAge, s.now())
		if err != nil {
			return nil, err
		}
		payload, valid := downloadTokenPayload(loaded)
		if !valid || payload["document_id"] != any(request.UID) {
			return docmanagerJSON(401, map[string]string{"error": "Invalid download token."}), nil
		}
		association, err := djangoUUIDValue(payload["sport_association_id"])
		if err != nil {
			return nil, err
		}
		if document, err = s.store.ExportArchiveDocument(ctx, association, uid); err != nil {
			return nil, err
		}
		if document == nil {
			return docmanagerJSON(404, map[string]string{"error": "Document not found."}), nil
		}
		exportArchive = true
	}
	if document == nil && token != "" {
		// A token UUIDField rejects raises inside the try and is ignored.
		if parsed, err := paymentInfoParseUUID(token); err == nil {
			if document, err = s.store.DocumentWithToken(ctx, uid, parsed.String()); err != nil {
				return nil, err
			}
		}
	}
	byAccount := false
	if document == nil {
		if user == nil {
			return docmanagerJSON(401, map[string]string{"error": "Invalid token. Not Authorized."}), nil
		}
		if document, err = s.relatedDocument(ctx, uid, group); err != nil {
			return nil, err
		}
		if document == nil {
			return docmanagerJSON(404, map[string]string{"error": "Document not found."}), nil
		}
		byAccount = true
	}
	if !exportArchive && strings.HasPrefix(document.Filename, "export_") {
		owner, found, err := s.store.FirstArchiveOwner(ctx, document.ID)
		if err != nil {
			return nil, err
		}
		if found && !(user != nil && user.Role == domain.RoleAssociation && user.ID == owner) {
			return docmanagerJSON(403, map[string]string{"error": "Not authorized to access this export."}), nil
		}
	}
	if byAccount {
		// Without a document token, documents are served to the association's staff and to the member they belong to.
		if err := memberDocumentAccess(ctx, s.store, identity, user, func(ctx context.Context, member string) (bool, error) {
			return s.store.MemberDocument(ctx, member, document.ID)
		}); err != nil {
			return nil, err
		}
	}
	if identity.ImpersonationAssociationID != "" {
		if identity.Effective == nil {
			return nil, domain.ErrInvalidScopedRequest
		}
		scope := domain.ResourceScope{AssociationID: identity.ImpersonationAssociationID, EffectiveID: identity.Effective.ID, Role: identity.Effective.Role}
		if group != nil {
			scope.GroupID = *group
		}
		visible, err := s.store.VisibleDocument(ctx, scope, document.ID)
		if err != nil {
			return nil, err
		}
		if !visible {
			return nil, &domain.PermissionDenied{Detail: "Documento non disponibile per questa associazione."}
		}
	}
	// header_auth_token: the Authorization header, else 'Bearer <?token>'.
	authorization := request.Authorization
	if _, present := queryText(request.Query, "token"); authorization == nil && present {
		bearer := "Bearer " + token
		authorization = &bearer
	}
	return s.stream(ctx, document, download, downloadValue, group, authorization)
}

// requestUser is _get_authenticated_user: the Authorization header, then the
// BKN_AUTH cookie, then ?token, each with plain JWTAuthentication (no identity
// resolution, so a collaborator stays the collaborator).
func (s *DocmanagerReads) requestUser(ctx context.Context, identity domain.ResolvedIdentity, cookie, token string) (*domain.Account, error) {
	if identity.Actor != nil {
		return identity.Actor, nil
	}
	for _, raw := range []string{djangoCookies(cookie)["BKN_AUTH"], token} {
		if raw == "" {
			continue
		}
		actor, err := s.actors.Authenticate(ctx, "Bearer "+raw)
		var failure *domain.AuthenticationFailure
		if errors.As(err, &failure) || (err == nil && actor == nil) {
			continue
		}
		if err != nil {
			return nil, err
		}
		return actor, nil
	}
	return nil, nil
}

// relatedDocument is _get_document_from_related_uid.
func (s *DocmanagerReads) relatedDocument(ctx context.Context, uid string, group *string) (*domain.StoredDocument, error) {
	for _, lookup := range []func() (*domain.StoredDocument, error){
		func() (*domain.StoredDocument, error) { return s.store.Document(ctx, uid) },
		func() (*domain.StoredDocument, error) { return s.store.SubscriptionDocument(ctx, uid, group) },
		func() (*domain.StoredDocument, error) { return s.store.MedicalDocument(ctx, uid) },
		func() (*domain.StoredDocument, error) { return s.store.InvoiceDocument(ctx, uid, group) },
	} {
		document, err := lookup()
		if document != nil || err != nil {
			return document, err
		}
	}
	return nil, nil
}

// stream is PrintingService.download_file (download) or view_file. Their
// exceptions become 500 {'exception': str(e)}; the ones whose text is a
// provider error other than a missing key are refused instead. A missing
// object that one subscription or invoice has as its PDF is printed again
// (the renderer, then the save_to_storage task) and the stream is still
// attempted: until the worker has stored the file the answer is the missing
// key exception.
func (s *DocmanagerReads) stream(ctx context.Context, document *domain.StoredDocument, download bool, downloadValue string,
	group *string, authorization *string) (*DocmanagerReply, error) {
	exception := func(text string) *DocmanagerReply { return docmanagerJSON(500, map[string]string{"exception": text}) }
	id, err := uuid.Parse(document.ID)
	if err != nil {
		return nil, err
	}
	name, err := documentkey.StoredName(document.Created, id, document.Filename, document.Filepath)
	if err != nil {
		return nil, domain.ErrDocmanagerNotPorted
	}
	key, err := documentkey.S3Key(name, s.objects.Location())
	var outside *documentkey.OutsideLocationError
	if errors.As(err, &outside) {
		return exception(outside.Error()), nil
	}
	if err != nil {
		return nil, domain.ErrDocmanagerNotPorted
	}
	exists, err := s.objects.Exists(ctx, key)
	if err != nil {
		return nil, domain.ErrDocmanagerNotPorted
	}
	reprint := ""
	if !exists {
		view, err := s.store.RenderSource(ctx, document.ID, group)
		if err != nil {
			return nil, err
		}
		if view == "" {
			return exception("File not found"), nil
		}
		if s.printer == nil {
			return nil, domain.ErrDocmanagerNotPorted
		}
		reprint = view
	}
	filename := document.Filename
	var disposition *string
	if download {
		filename = strings.ReplaceAll(filename, ",", " ")
		value, ok := contentDisposition(downloadValue != "", filename)
		if !ok {
			return nil, domain.ErrDocmanagerNotPorted
		}
		disposition = value
	}
	guessed, known, err := s.guess(filename)
	if err != nil {
		return nil, domain.ErrDocmanagerNotPorted
	}
	if reprint != "" {
		// After the refusals above, so a refused name prints nothing.
		s.printer.PrintAndStore(ctx, reprint, authorization, *document)
	}
	object, err := s.objects.Open(ctx, key)
	var missing *documentobjects.NoSuchKeyError
	if errors.As(err, &missing) {
		return exception(missing.Error()), nil
	}
	if err != nil {
		return nil, domain.ErrDocmanagerNotPorted
	}
	contentType := guessed
	if !known {
		contentType = "application/octet-stream"
		if object.ContentType != nil && *object.ContentType != "" {
			contentType = *object.ContentType
		}
	}
	reply := &DocumentStream{Object: object, ContentType: contentType, Disposition: disposition, ContentLength: object.ContentLength}
	if object.ContentEncoding != nil {
		reply.ContentEncoding = *object.ContentEncoding
	}
	return &DocmanagerReply{Status: 200, Stream: reply}, nil
}

// contentDisposition is django.utils.http.content_disposition_header. ok is
// false for values Django's response headers reject (newlines) and for the
// None an inline header without a filename would set.
func contentDisposition(attachment bool, filename string) (*string, bool) {
	if filename == "" {
		if !attachment {
			return nil, false
		}
		value := "attachment"
		return &value, true
	}
	kind := "inline"
	if attachment {
		kind = "attachment"
	}
	quotable := true
	for i, r := range filename {
		if r == '\n' && i == len(filename)-1 {
			// re.match('^[...]*$') accepts one trailing newline; the header
			// then fails Django's newline check.
			return nil, false
		}
		if !(r == '\t' || r >= 0x20 && r <= 0x7e) {
			quotable = false
		}
	}
	var value string
	if quotable {
		value = kind + `; filename="` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(filename) + `"`
	} else {
		value = kind + "; filename*=utf-8''" + pythonQuote(filename)
	}
	return &value, true
}

// pythonQuote is urllib.parse.quote(text) with its default safe '/'.
func pythonQuote(text string) string {
	const hex = "0123456789ABCDEF"
	var out strings.Builder
	for _, b := range []byte(text) {
		if b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z' || b >= '0' && b <= '9' || strings.IndexByte("_.-~/", b) >= 0 {
			out.WriteByte(b)
			continue
		}
		out.WriteByte('%')
		out.WriteByte(hex[b>>4])
		out.WriteByte(hex[b&15])
	}
	return out.String()
}

// downloadTokenPayload is the rest of load_document_download_token.
func downloadTokenPayload(loaded djangoSignedValue) (map[string]any, bool) {
	if !loaded.valid {
		return nil, false
	}
	payload, ok := loaded.payload.(map[string]any)
	if !ok || payload["purpose"] != any(documentDownloadTokenPurpose) {
		return nil, false
	}
	if !pythonTruthy(payload["document_id"]) || !pythonTruthy(payload["sport_association_id"]) {
		return nil, false
	}
	return payload, true
}

func pythonTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		number, ok := new(big.Float).SetString(string(v))
		return !ok || number.Sign() != 0
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	}
	return true
}

// djangoCookies is django.http.cookie.parse_cookie: ';'-separated, '=' split
// once, stripped, quoted values unquoted, the last duplicate wins.
func djangoCookies(header string) map[string]string {
	cookies := map[string]string{}
	for _, chunk := range strings.Split(header, ";") {
		key, value, found := strings.Cut(chunk, "=")
		if !found {
			key, value = "", chunk
		}
		key, value = strings.TrimSpace(key), strings.TrimSpace(value)
		if key != "" || value != "" {
			cookies[key] = cookieUnquote(value)
		}
	}
	return cookies
}

// cookieUnquote is http.cookies._unquote.
func cookieUnquote(value string) string {
	if len(value) < 2 || value[0] != '"' || value[len(value)-1] != '"' {
		return value
	}
	inner := value[1 : len(value)-1]
	var out strings.Builder
	for i := 0; i < len(inner); i++ {
		if inner[i] != '\\' || i+1 >= len(inner) {
			out.WriteByte(inner[i])
			continue
		}
		if i+3 < len(inner) && isOctal(inner[i+1]) && isOctal(inner[i+2]) && isOctal(inner[i+3]) && inner[i+1] <= '3' {
			out.WriteByte((inner[i+1]-'0')<<6 | (inner[i+2]-'0')<<3 | (inner[i+3] - '0'))
			i += 3
			continue
		}
		out.WriteByte(inner[i+1])
		i++
	}
	return out.String()
}

func isOctal(b byte) bool { return b >= '0' && b <= '7' }

// WithDocumentDelete installs the store that runs delete_document's ownership
// walk and deletion.
func (s *DocmanagerReads) WithDocumentDelete(deleter domain.DocumentDeleteStore) *DocmanagerReads {
	s.deleter = deleter
	return s
}

// Delete follows application.document_scope.delete_document: is_valid_uuid,
// then a missing document, a requester (request.user, the owner for
// collaborators) without an active association and a document that does not
// belong to that association are all 404; otherwise Document.delete().
func (s *DocmanagerReads) Delete(ctx context.Context, identity domain.ResolvedIdentity, uid string, audit domain.LoginAuditContext,
	groupHeader string) (*DocmanagerReply, error) {
	id, err := paymentInfoParseUUID(uid)
	if err != nil {
		return nil, err
	}
	if s.deleter == nil {
		return nil, domain.ErrDocmanagerNotPorted
	}
	found := false
	if identity.ScopeUser != nil {
		found, err = s.deleter.Delete(ctx, id.String(), identity.ScopeUser.ID, paymentCategoryAuditContext(identity, audit),
			supplierGroup(groupHeader))
		if err != nil {
			return nil, err
		}
	}
	if !found {
		return docmanagerJSON(404, map[string]string{"error": "Document not found."}), nil
	}
	return docmanagerJSON(200, map[string]string{"msg": "success!"}), nil
}
