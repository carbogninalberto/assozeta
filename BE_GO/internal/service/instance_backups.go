package service

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
)

// ErrInstanceBackupsNotPorted marks inputs whose Django handling Go does not
// carry; the handler answers them with the server error page before any write.
var ErrInstanceBackupsNotPorted = refusal.New("instance backups request not ported")

// errInstanceBackupsUnhandled is an exception Django lets escape (a 500).
var errInstanceBackupsUnhandled = errors.New("instance backups unhandled exception")

const (
	instanceBackupsSalt        = "instance.backups.download"
	instanceBackupsTokenAge    = 300
	instanceBackupsAttempts    = 5
	instanceBackupsMarginMB    = 1024
	instanceBackupsOutdated    = "Il servizio di aggiornamento richiede una versione più recente per mostrare i backup."
	instanceBackupsInvalidLink = "Link di download non valido o scaduto."
)

// InstanceBackupsRunner is updater_client's call_runner and open_runner_stream.
type InstanceBackupsRunner interface {
	Call(ctx context.Context, method, path string) (*updaterrunner.Reply, error)
	CallPayload(ctx context.Context, method, path string, payload []byte) (*updaterrunner.Reply, error)
	Open(ctx context.Context, path string) (*updaterrunner.Stream, error)
}

// InstanceBackupsObjects measures the bucket the backup mirrors.
type InstanceBackupsObjects interface {
	TotalBytes(ctx context.Context) (int64, error)
}

type InstanceBackupsHasher interface {
	CheckDjangoPassword(context.Context, passwordcrypto.LoginPasswordValue, string) (passwordcrypto.LoginPasswordDecision, error)
	Encode(context.Context, string) (string, error)
}

// InstanceBackups is BE/instance/backups.py.
type InstanceBackups struct {
	store   *repository.InstanceBackups
	runner  InstanceBackupsRunner
	objects InstanceBackupsObjects
	hashes  InstanceBackupsHasher
	secret  string
	now     func() time.Time
	newID   func() string
}

func NewInstanceBackups(store *repository.InstanceBackups, runner InstanceBackupsRunner, objects InstanceBackupsObjects,
	hashes InstanceBackupsHasher, secret string, now func() time.Time, newID func() string) *InstanceBackups {
	return &InstanceBackups{store: store, runner: runner, objects: objects, hashes: hashes, secret: secret, now: now, newID: newID}
}

// InstanceBackupsReply is one response: a JSON body, the runner's relayed
// JSON value (Relayed set: Data is rendered as it is, null included) or the
// relayed archive.
type InstanceBackupsReply struct {
	Status  int
	Body    domain.RequestObject
	Data    any
	Relayed bool
	Stream  *InstanceBackupsStream
}

type InstanceBackupsStream struct {
	Body        io.ReadCloser
	Length      int64
	Disposition string
}

func instanceBackupsBody(status int, fields ...domain.RequestField) *InstanceBackupsReply {
	return &InstanceBackupsReply{Status: status, Body: domain.RequestObject(fields)}
}

func instanceBackupsError(status int, message string) *InstanceBackupsReply {
	return instanceBackupsBody(status, domain.RequestField{Name: "error", Value: message})
}

func instanceBackupsUnavailable(reason string) *InstanceBackupsReply {
	return instanceBackupsBody(200, domain.RequestField{Name: "available", Value: false}, domain.RequestField{Name: "reason", Value: reason})
}

// instanceBackupsJSON is json.loads(bytes): decoded=false for the
// ValueErrors call_runner catches (invalid JSON or UTF-8, Python's integer
// digit limit). A UTF-8 BOM is dropped (utf-8-sig). UTF-16/32 bodies,
// surrogate code points, NaN/Infinity literals and deep nesting are not ported.
func instanceBackupsJSON(raw []byte) (any, bool, error) {
	if bytes.HasPrefix(raw, []byte{0xff, 0xfe}) || bytes.HasPrefix(raw, []byte{0xfe, 0xff}) ||
		len(raw) >= 2 && (raw[0] == 0 || raw[1] == 0) {
		return nil, false, ErrInstanceBackupsNotPorted
	}
	raw = bytes.TrimPrefix(raw, []byte{0xef, 0xbb, 0xbf})
	for i := 0; i+1 < len(raw); i++ {
		if raw[i] == 0xed && raw[i+1] >= 0xa0 && raw[i+1] <= 0xbf {
			return nil, false, ErrInstanceBackupsNotPorted // surrogatepass decoding
		}
	}
	if !utf8.Valid(raw) {
		return nil, false, nil
	}
	value, ok, err := dto.AuditJSONLoads(string(raw))
	switch {
	case errors.Is(err, dto.ErrAuditUnhandled):
		return nil, false, nil
	case err != nil:
		return nil, false, ErrInstanceBackupsNotPorted
	}
	return value, ok, nil
}

// Overview is BackupOverviewView.get.
func (s *InstanceBackups) Overview(ctx context.Context, refresh bool) (*InstanceBackupsReply, error) {
	reply, err := s.runner.Call(ctx, "GET", "/backups")
	if message, ok := updaterrunner.IsUnavailable(err); ok {
		return instanceBackupsUnavailable(message), nil
	}
	if err != nil {
		return nil, err
	}
	value, decoded, err := instanceBackupsJSON(reply.Body)
	if err != nil {
		return nil, err
	}
	if !decoded {
		return instanceBackupsUnavailable(updaterrunner.UnreachableText), nil
	}
	if reply.Status >= 400 {
		body, ok := value.(domain.RequestObject)
		if !ok {
			return nil, errInstanceBackupsUnhandled // body.get: AttributeError
		}
		message, found := body.Lookup("error")
		if !found {
			message = "Aggiornamento non disponibile."
		}
		switch {
		case reply.Status == 404:
			return instanceBackupsUnavailable(instanceBackupsOutdated), nil
		case reply.Status > 599:
			return nil, errInstanceBackupsUnhandled // HttpResponse: status out of range
		case reply.Status >= 500:
			return instanceBackupsError(reply.Status, dto.PythonStr(message)), nil
		}
		return instanceBackupsError(503, dto.PythonStr(message)), nil
	}
	inventory, ok := value.(domain.RequestObject)
	if !ok {
		return nil, errInstanceBackupsUnhandled // inventory.get: AttributeError
	}
	sizes, err := s.dataSizes(ctx, refresh)
	if err != nil {
		return nil, err
	}
	archives, found := inventory.Lookup("archives")
	if !found {
		archives = []any{}
	}
	retention, found := inventory.Lookup("retention")
	if !found {
		retention = domain.RequestObject{}
	}
	diskValue, found := inventory.Lookup("disk")
	if !found {
		diskValue = domain.RequestObject{}
	}
	disk, ok := diskValue.(domain.RequestObject)
	if !ok {
		return nil, errInstanceBackupsUnhandled // {**disk}: TypeError
	}
	merged := append(domain.RequestObject(nil), disk...)
	for _, field := range sizes {
		merged = dto.PythonSet(merged, field.Name, field.Value)
	}
	free, _ := disk.Lookup("free")
	margin, _ := inventory.Lookup("space_margin_mb")
	next, err := instanceBackupsNext(sizes, free, margin)
	if err != nil {
		return nil, err
	}
	// Older runners have no schedule or actions (the page stays read-only)
	// and no remote copy status.
	schedule, _ := inventory.Lookup("schedule")
	actions, found := inventory.Lookup("actions")
	if !found {
		actions = domain.RequestObject{}
	}
	remote, _ := inventory.Lookup("remote")
	body := domain.RequestObject{{Name: "available", Value: true}, {Name: "archives", Value: archives},
		{Name: "retention", Value: retention}, {Name: "disk", Value: merged}, {Name: "next_backup", Value: next},
		{Name: "schedule", Value: schedule}, {Name: "actions", Value: actions}, {Name: "remote", Value: remote}}
	if !dto.AuditFiniteJSON(body) {
		return nil, errInstanceBackupsUnhandled // strict JSONRenderer: ValueError
	}
	return &InstanceBackupsReply{Status: 200, Body: body}, nil
}

// dataSizes is data_sizes(refresh): the cached dict, or both measurements
// (each failure is None) stored for ten minutes. A cached value that is not
// a dict fails the response like Django's TypeError.
func (s *InstanceBackups) dataSizes(ctx context.Context, refresh bool) (domain.RequestObject, error) {
	if !refresh {
		value, found, err := s.store.CachedSizes(ctx)
		if err != nil {
			if errors.Is(err, repository.ErrInstanceBackupsCacheShape) {
				return nil, ErrInstanceBackupsNotPorted
			}
			return nil, err
		}
		if found {
			return instanceBackupsCached(value)
		}
	}
	var database, objects *int64
	if size, err := s.store.DatabaseBytes(ctx); err != nil {
		slog.WarnContext(ctx, "could not measure database size for the backup page", "error", err)
	} else {
		database = &size
	}
	if size, err := s.objects.TotalBytes(ctx); err != nil {
		slog.WarnContext(ctx, "could not measure objects size for the backup page", "error", err)
	} else {
		objects = &size
	}
	if err := s.store.StoreSizes(ctx, database, objects); err != nil {
		return nil, err
	}
	value := func(size *int64) any {
		if size == nil {
			return nil
		}
		return json.Number(strconv.FormatInt(*size, 10))
	}
	return domain.RequestObject{{Name: "database", Value: value(database)}, {Name: "objects", Value: value(objects)}}, nil
}

// instanceBackupsCached renders a cached dict of None, int, str and bool
// values; other values are not ported.
func instanceBackupsCached(value any) (domain.RequestObject, error) {
	dict, ok := value.(*repository.PickledDict)
	if !ok {
		return nil, errInstanceBackupsUnhandled
	}
	out := domain.RequestObject{}
	for _, key := range dict.Keys {
		switch v := dict.Values[key].(type) {
		case nil, string, bool:
			out = append(out, domain.RequestField{Name: key, Value: v})
		case int64:
			out = append(out, domain.RequestField{Name: key, Value: json.Number(strconv.FormatInt(v, 10))})
		default:
			return nil, ErrInstanceBackupsNotPorted
		}
	}
	return out, nil
}

// instanceBackupsNext is next_backup(sizes, free, margin_mb).
func instanceBackupsNext(sizes domain.RequestObject, free, margin any) (any, error) {
	database, found := sizes.Lookup("database")
	if !found {
		return nil, errInstanceBackupsUnhandled // KeyError
	}
	objects, found := sizes.Lookup("objects")
	if !found {
		return nil, errInstanceBackupsUnhandled
	}
	if database == nil || objects == nil {
		return nil, nil
	}
	integer := func(value any) (*big.Int, bool) {
		number, ok := value.(json.Number)
		if !ok || strings.ContainsAny(string(number), ".eE") {
			return nil, false
		}
		return new(big.Int).SetString(string(number), 10)
	}
	left, ok := integer(database)
	right, ok2 := integer(objects)
	if !ok || !ok2 {
		return nil, ErrInstanceBackupsNotPorted
	}
	estimated := new(big.Int).Add(left, right)
	marginMB, ok := integer(margin)
	if !ok || marginMB.Sign() < 1 {
		marginMB = big.NewInt(instanceBackupsMarginMB)
	}
	required := new(big.Int).Add(new(big.Int).Mul(estimated, big.NewInt(2)), new(big.Int).Mul(marginMB, big.NewInt(1024*1024)))
	fits := true
	switch v := free.(type) {
	case nil:
	case bool:
		available := int64(0)
		if v {
			available = 1
		}
		fits = big.NewInt(available).Cmp(required) >= 0
	case json.Number:
		if number, ok := integer(v); ok {
			fits = number.Cmp(required) >= 0
			break
		}
		value, _ := strconv.ParseFloat(string(v), 64)
		switch {
		case math.IsInf(value, 1):
			fits = true
		case math.IsInf(value, -1):
			fits = false
		default:
			fits = new(big.Float).SetFloat64(value).Cmp(new(big.Float).SetInt(required)) >= 0
		}
	default:
		return nil, errInstanceBackupsUnhandled // '>=' between str/list/dict and int: TypeError
	}
	return domain.RequestObject{{Name: "estimated", Value: json.Number(estimated.String())},
		{Name: "required", Value: json.Number(required.String())}, {Name: "fits", Value: fits}}, nil
}

// Token is BackupDownloadTokenView.post for the authenticated actor.
func (s *InstanceBackups) Token(ctx context.Context, actor string, body any, audit domain.LoginAuditContext) (*InstanceBackupsReply, error) {
	input, invalid, err := dto.ParseInstanceBackupsToken(body)
	if err != nil {
		return nil, ErrInstanceBackupsNotPorted
	}
	if invalid != nil {
		return &InstanceBackupsReply{Status: 400, Body: invalid}, nil
	}
	if status, message, err := instanceBackupsPasswordRefusal(ctx, s.store, s.hashes, actor, input.Password); err != nil || status != 0 {
		if err != nil {
			return nil, err
		}
		return instanceBackupsError(status, message), nil
	}
	target, err := s.store.LogTarget(ctx)
	if err != nil {
		return nil, err
	}
	if target == nil {
		return nil, errInstanceBackupsUnhandled // log_create(None): no content type
	}
	at := s.now()
	if err := s.store.AppendAccess(ctx, repository.InstanceBackupsAccess{Target: *target, ActorID: actor, Backup: input.Name,
		Audit: audit, At: at}); err != nil {
		return nil, err
	}
	token := s.sign(domain.RequestObject{{Name: "name", Value: input.Name}, {Name: "actor", Value: actor},
		{Name: "id", Value: s.newID()}}, at)
	return instanceBackupsBody(200, domain.RequestField{Name: "download_token", Value: token},
		domain.RequestField{Name: "expires_in", Value: json.Number(strconv.Itoa(instanceBackupsTokenAge))}), nil
}

// sign is signing.dumps(payload, salt=TOKEN_SALT) with SECRET_KEY.
func (s *InstanceBackups) sign(payload domain.RequestObject, at time.Time) string {
	value := base64.RawURLEncoding.EncodeToString(dto.PythonJSONDumpsCompact(payload)) + ":" + base62(at.Unix())
	key := sha256.Sum256([]byte(instanceBackupsSalt + "signer" + s.secret))
	mac := hmac.New(sha256.New, key[:])
	mac.Write([]byte(value))
	return value + ":" + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

// instanceBackupsUserID is User.objects.filter(pk=value) for a claimed actor:
// None matches nothing; UUIDField.to_python takes an int (bool included) as
// UUID(int=...) and anything else as UUID(hex=...). A value it rejects is a
// ValidationError (a 500). Hex forms other than 32 hex digits after Python's
// 'urn:'/'uuid:'/braces/hyphen removal are not ported.
func instanceBackupsUserID(value any) (string, bool, error) {
	var number *big.Int
	switch v := value.(type) {
	case nil:
		return "", false, nil
	case bool:
		number = big.NewInt(0)
		if v {
			number = big.NewInt(1)
		}
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "", false, errInstanceBackupsUnhandled // float.replace: AttributeError
		}
		number, _ = new(big.Int).SetString(string(v), 10)
	case string:
		text := strings.ReplaceAll(strings.ReplaceAll(v, "urn:", ""), "uuid:", "")
		text = strings.ReplaceAll(strings.Trim(text, "{}"), "-", "")
		switch {
		case len(text) == 32 && strings.Trim(text, "0123456789abcdefABCDEF") == "":
			number, _ = new(big.Int).SetString(text, 16)
		case len(text) == 32 && (!instanceBackupsASCII(text) || strings.ContainsAny(text, "+_xX \t\n\r\v\f")):
			// int(hex, 16) also takes a sign, underscores, 0x, whitespace and
			// non-ASCII digits.
			return "", false, ErrInstanceBackupsNotPorted
		default:
			return "", false, errInstanceBackupsUnhandled
		}
	default:
		return "", false, errInstanceBackupsUnhandled
	}
	if number == nil || number.Sign() < 0 || number.BitLen() > 128 {
		return "", false, errInstanceBackupsUnhandled
	}
	hex := number.Text(16)
	hex = strings.Repeat("0", 32-len(hex)) + hex
	return hex[:8] + "-" + hex[8:12] + "-" + hex[12:16] + "-" + hex[16:20] + "-" + hex[20:], true, nil
}

// Download is BackupDownloadView.get (no authentication).
func (s *InstanceBackups) Download(ctx context.Context, name, token string) (*InstanceBackupsReply, error) {
	signed, err := djangoSigningLoads(token, instanceBackupsSalt, s.secret, instanceBackupsTokenAge, s.now())
	if err != nil {
		return nil, err
	}
	claims, ok := signed.payload.(map[string]any)
	if !signed.valid || !ok {
		return instanceBackupsError(403, instanceBackupsInvalidLink), nil
	}
	if claimed, ok := claims["name"].(string); !ok || claimed != name || !dto.InstanceBackupsArchive.MatchString(name) {
		return instanceBackupsError(403, instanceBackupsInvalidLink), nil
	}
	id, found, err := instanceBackupsUserID(claims["actor"])
	if err != nil {
		return nil, err
	}
	var account *domain.Account
	if found {
		if account, err = s.store.Account(ctx, id); err != nil {
			return nil, err
		}
	}
	config, err := s.store.Instance(ctx)
	if err != nil {
		return nil, err
	}
	if account == nil || !dataRestoreAdministrator(account, config) {
		return instanceBackupsError(403, "Download non autorizzato."), nil
	}
	used, ok := claims["id"].(string)
	if ok {
		added, err := s.store.MarkUsed(ctx, used)
		if err != nil {
			return nil, err
		}
		ok = added
	}
	if !ok {
		return instanceBackupsError(403, "Link di download già usato. Conferma di nuovo la password."), nil
	}
	stream, err := s.runner.Open(ctx, "/backups/"+name)
	if message, ok := updaterrunner.IsUnavailable(err); ok {
		return instanceBackupsError(503, message), nil
	}
	if err != nil {
		return nil, err
	}
	if stream.Status >= 400 {
		raw, err := updaterrunner.ReadError(stream)
		if message, ok := updaterrunner.IsUnavailable(err); ok {
			return instanceBackupsError(503, message), nil
		}
		value, decoded, err := instanceBackupsJSON(raw)
		if err != nil {
			return nil, err
		}
		var message any = "Risorsa non disponibile."
		if decoded {
			body, ok := value.(domain.RequestObject)
			if !ok {
				return nil, errInstanceBackupsUnhandled // .get on a non-dict: AttributeError
			}
			if text, found := body.Lookup("error"); found {
				message = text
			}
		}
		if stream.Status == 404 {
			return instanceBackupsError(404, "Backup non trovato."), nil
		}
		return instanceBackupsError(503, dto.PythonStr(message)), nil
	}
	if stream.ContentLength < 0 {
		_ = stream.Body.Close()
		return instanceBackupsError(503, updaterrunner.UnreachableText), nil // int(''): ValueError
	}
	disposition, _ := contentDisposition(true, name)
	return &InstanceBackupsReply{Status: 200, Stream: &InstanceBackupsStream{Body: stream.Body, Length: stream.ContentLength,
		Disposition: *disposition}}, nil
}

func instanceBackupsASCII(text string) bool {
	for i := 0; i < len(text); i++ {
		if text[i] >= 0x80 {
			return false
		}
	}
	return true
}

// instanceBackupsAttemptStore is the attempts cache and the actor's stored hash
// (repository.InstanceBackups).
type instanceBackupsAttemptStore interface {
	Attempts(ctx context.Context, actor string) (int64, error)
	CountAttempt(ctx context.Context, actor string) (int64, error)
	ClearAttempts(ctx context.Context, actor string) error
	Password(ctx context.Context, id string) (string, error)
	SetPassword(ctx context.Context, id, encoded string) error
}

// instanceBackupsPasswordRefusal is backups.password_refusal on the shared
// attempts key: 429 at the limit, then the try is counted (atomically, before
// the check) and a count over the limit is 429 too; a wrong password is 400
// (the count stays), a match clears the key (status 0). check_password
// upgrades an outdated hash of a matching password.
func instanceBackupsPasswordRefusal(ctx context.Context, store instanceBackupsAttemptStore, hashes InstanceBackupsHasher,
	actor, password string) (int, string, error) {
	const tooMany = "Troppi tentativi. Riprova tra qualche minuto."
	attempts, err := store.Attempts(ctx, actor)
	if err != nil {
		if errors.Is(err, repository.ErrInstanceBackupsCacheShape) {
			return 0, "", ErrInstanceBackupsNotPorted
		}
		return 0, "", err
	}
	if attempts >= instanceBackupsAttempts {
		return 429, tooMany, nil
	}
	counted, err := store.CountAttempt(ctx, actor)
	if err != nil {
		return 0, "", err
	}
	if counted > instanceBackupsAttempts {
		return 429, tooMany, nil
	}
	encoded, err := store.Password(ctx, actor)
	if err != nil {
		return 0, "", err
	}
	decision, err := hashes.CheckDjangoPassword(ctx, passwordcrypto.LoginPasswordValue{Text: password, String: true}, encoded)
	if err != nil {
		return 0, "", err
	}
	if decision.Match && decision.Upgrade {
		// check_password's setter: set_password and save(update_fields=['password']).
		upgraded, err := hashes.Encode(ctx, password)
		if err != nil {
			return 0, "", err
		}
		if err := store.SetPassword(ctx, actor, upgraded); err != nil {
			return 0, "", err
		}
	}
	if !decision.Match {
		return 400, "Password non corretta.", nil
	}
	return 0, "", store.ClearAttempts(ctx, actor)
}
