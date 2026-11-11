package service

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// InstanceAdmin ports the owner-only installation administration views of
// BE/instance: InstanceAdminView, EmailSettingsView, IntegrationSettingsView,
// DiagnosticsView (GET; POST in instance_diagnostics.go), and
// InstanceUpdatesView/InstanceRestartsView (the update runner in production,
// instance_updates.go). Paths that run outbound probes, and stored shapes
// the port does not interpret, return domain.ErrInstanceAdminRefused before
// any write.
type InstanceAdmin struct {
	store     InstanceAdminStore
	bootstrap InstanceAdminBootstrap
	env       InstanceAdminEnvironment
	now       func() time.Time
	random    io.Reader
	// runner and releases enable the production update paths (WithUpdates).
	runner   InstanceUpdatesRunner
	releases InstanceReleaseFetcher
}

type InstanceAdminStore interface {
	Read(context.Context) (*domain.InstanceAdminRow, error)
	Locked(context.Context, func(*domain.InstanceAdminRow) (*domain.InstanceAdminUpdate, error)) error
	UpdateBranding(context.Context, map[string]string, time.Time) error
	UIAPIVersion(context.Context) (string, error)
	AppendUIAPIVersionChange(context.Context, domain.InstanceUIAPIVersionChange) error
}

// InstanceAdminBootstrap is InstanceConfigSerializer and is_instance_administrator
// (the public bootstrap service).
type InstanceAdminBootstrap interface {
	Config(context.Context) (*dto.InstanceConfig, error)
	Access(context.Context, domain.Account) (*dto.InstanceAccess, error)
}

// InstanceAdminEnvironment holds the core.settings values these views read.
type InstanceAdminEnvironment struct {
	Version, ConfiguredVersion, DeploymentMode, AppURL string
	EmailHost, EmailUsername, EmailPassword            string
	EmailPort                                          int64
	EmailPortInvalid, EmailUseTLS, EmailUseSSL         bool
	// FromName and FromAddress are parseaddr(DEFAULT_FROM_EMAIL).
	FromName, FromAddress                           string
	AIAPIKey, AIModel, AICheapModel, AIBaseURL      string
	AIIntegers                                      [6]int64
	AIIntegersInvalid                               bool
	StripePublicKey, StripeKey, StripeWebhookSecret string
	GoogleClientID, AppleClientID                   string
	// SECRET_KEY then SECRET_KEY_FALLBACKS.
	SecretKeys []string
}

func NewInstanceAdmin(store InstanceAdminStore, bootstrap InstanceAdminBootstrap, env InstanceAdminEnvironment, now func() time.Time) *InstanceAdmin {
	return &InstanceAdmin{store: store, bootstrap: bootstrap, env: env, now: now, random: rand.Reader}
}

// InstanceAdminResponse is a response the view returns itself (not a
// validation error): Body with Status and optional Retry-After.
type InstanceAdminResponse struct {
	Status     int
	Body       any
	RetryAfter string
}

func (r *InstanceAdminResponse) Error() string {
	return fmt.Sprintf("instance administration response %d", r.Status)
}

func instanceAdminError(message string, status int) error {
	return &InstanceAdminResponse{Status: status, Body: map[string]any{"error": message}}
}

// ErrInstanceAdminProvider is IntegrationSettingsView.provider()'s NotFound.
var ErrInstanceAdminProvider = errors.New("unknown integration provider")

var errInstanceAdminCredential = errors.New("stored credential cannot be decrypted")

// errInstanceAdminEnvironmentPort is int(settings.EMAIL_PORT) raising ValueError: nothing handles it, so the
// view answers the 500 page.
var errInstanceAdminEnvironmentPort = errors.New("invalid literal for int() with base 10: EMAIL_PORT")

func refused(reason string) error {
	return fmt.Errorf("%w: %s", domain.ErrInstanceAdminRefused, reason)
}

const (
	instanceEmailCipher       = "assozeta:instance-email:v1:"
	instanceIntegrationCipher = "assozeta:instance-integrations:v1:"
	instanceEmailApplication  = "Nuovi invii da API e worker; le connessioni già aperte terminano con la configurazione precedente."
)

// Allowed is IsInstanceOwner.has_permission for the authenticated actor.
func (s *InstanceAdmin) Allowed(ctx context.Context, actor domain.Account) (bool, error) {
	access, err := s.bootstrap.Access(ctx, actor)
	if err != nil {
		return false, err
	}
	return access.IsAdministrator, nil
}

func (s *InstanceAdmin) production() bool { return s.env.DeploymentMode == "production" }

// ---------------------------------------------------------------------------
// admin
// ---------------------------------------------------------------------------

func (s *InstanceAdmin) config(ctx context.Context) (*dto.InstanceConfig, error) {
	config, err := s.bootstrap.Config(ctx)
	if errors.Is(err, domain.ErrInstanceNotConfigured) {
		return nil, refused("no configuration")
	}
	return config, err
}

func (s *InstanceAdmin) Admin(ctx context.Context) (map[string]any, error) {
	config, err := s.config(ctx)
	if err != nil {
		return nil, err
	}
	return map[string]any{"config": config, "running_version": s.env.Version, "configured_version": s.env.ConfiguredVersion,
		"mode": s.env.DeploymentMode}, nil
}

// Branding is InstanceAdminView.put: the branding ('oem') and the web UI API
// version ('uiApiVersion'). A version change writes one UPDATE log entry on
// the configuration ({'ui_api_version': [previous, new]}) after the save.
func (s *InstanceAdmin) Branding(ctx context.Context, body any, actor string, audit domain.LoginAuditContext) (map[string]any, error) {
	within, err := dto.InstanceAdminKeysWithin(body, "oem", "uiApiVersion")
	if err != nil {
		return nil, err
	}
	if !within {
		return nil, &dto.InstanceAdminInvalid{Body: []string{"Only instance branding can be edited here."}}
	}
	previous, err := s.store.UIAPIVersion(ctx)
	if err != nil {
		return nil, err
	}
	fields, err := dto.InstanceAdminEdit(body)
	if err != nil {
		return nil, err
	}
	if err = s.store.UpdateBranding(ctx, fields, s.now()); err != nil {
		return nil, err
	}
	if version, edited := fields["ui_api_version"]; edited && version != previous {
		if err = s.store.AppendUIAPIVersionChange(ctx, domain.InstanceUIAPIVersionChange{
			ActorID: actor, Previous: previous, Version: version, Audit: audit, At: s.now()}); err != nil {
			return nil, err
		}
	}
	config, err := s.config(ctx)
	if err != nil {
		return nil, err
	}
	return map[string]any{"config": config}, nil
}

// ---------------------------------------------------------------------------
// updates and restarts (development mode only)
// ---------------------------------------------------------------------------

func (s *InstanceAdmin) Updates() (map[string]any, error) {
	if s.production() {
		return nil, refused("update runner status")
	}
	return map[string]any{"available": false, "reason": "Gli aggiornamenti dell’installazione sono disabilitati in sviluppo.",
		"active": nil, "history": []any{}}, nil
}

func (s *InstanceAdmin) RequestUpdate(body any) error {
	if err := dto.InstanceAdminUpdateRequest(body); err != nil {
		return err
	}
	if s.production() {
		return refused("update runner request")
	}
	return instanceAdminError("Gli aggiornamenti reali sono disabilitati in sviluppo.", 409)
}

func (s *InstanceAdmin) RequestRestart(body any) error {
	only, err := dto.InstanceAdminKeysEqual(body, "request_id")
	if err != nil {
		return err
	}
	if !only {
		return &dto.InstanceAdminInvalid{Body: []string{"È richiesto soltanto l’identificativo della richiesta."}}
	}
	if err = dto.InstanceAdminRestartRequest(body); err != nil {
		return err
	}
	if s.production() {
		return refused("update runner restart")
	}
	return instanceAdminError("Il riavvio dei container è disabilitato in sviluppo.", 409)
}

// ---------------------------------------------------------------------------
// JSON values with Python semantics
// ---------------------------------------------------------------------------

func instanceAdminDecode(raw []byte) (any, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	return value, nil
}

// instanceAdminObject is `value or {}` where the result must be a dict.
func instanceAdminObject(value any) (map[string]any, error) {
	if !instanceTruthy(value) {
		return map[string]any{}, nil
	}
	object, ok := value.(map[string]any)
	if !ok {
		return nil, refused("stored value is not an object")
	}
	return object, nil
}

func instanceAdminCopy(value map[string]any) map[string]any {
	result := make(map[string]any, len(value))
	for key, item := range value {
		result[key] = item
	}
	return result
}

// instanceAdminNumber is the numeric value Python compares: bool is 0/1, JSON
// floats are binary doubles.
func instanceAdminNumber(value any) (*big.Rat, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewRat(1, 1), true
		}
		return big.NewRat(0, 1), true
	case json.Number:
		text := string(v)
		if strings.ContainsAny(text, ".eE") {
			f, err := strconv.ParseFloat(text, 64)
			if err != nil {
				return nil, false
			}
			r := new(big.Rat)
			if r.SetFloat64(f) == nil {
				return nil, false
			}
			return r, true
		}
		r, ok := new(big.Rat).SetString(text)
		return r, ok
	case int64:
		return big.NewRat(v, 1), true
	case *big.Int:
		return new(big.Rat).SetInt(v), true
	}
	return nil, false
}

// pythonEqual is == between JSON-decoded values (and Go ints).
func pythonEqual(a, b any) bool {
	if x, ok := instanceAdminNumber(a); ok {
		y, ok := instanceAdminNumber(b)
		return ok && x.Cmp(y) == 0
	}
	switch x := a.(type) {
	case nil:
		return b == nil
	case string:
		y, ok := b.(string)
		return ok && x == y
	case []any:
		y, ok := b.([]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for i := range x {
			if !pythonEqual(x[i], y[i]) {
				return false
			}
		}
		return true
	case map[string]any:
		y, ok := b.(map[string]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for key, item := range x {
			other, present := y[key]
			if !present || !pythonEqual(item, other) {
				return false
			}
		}
		return true
	}
	return false
}

func instanceAdminInteger(value any) (*big.Int, bool) {
	number, ok := value.(json.Number)
	if !ok || strings.ContainsAny(string(number), ".eE") {
		return nil, false
	}
	return new(big.Int).SetString(string(number), 10)
}

func instanceAdminMarshal(value any) (json.RawMessage, error) {
	raw, err := json.Marshal(value)
	return raw, err
}

// pythonISOFormat is timezone.now().isoformat().
func pythonISOFormat(t time.Time) string {
	t = t.UTC()
	text := t.Format("2006-01-02T15:04:05")
	if micro := t.Nanosecond() / 1000; micro != 0 {
		text += fmt.Sprintf(".%06d", micro)
	}
	return text + "+00:00"
}

// ---------------------------------------------------------------------------
// Fernet (cryptography.fernet) keyed by sha256(prefix + secret)
// ---------------------------------------------------------------------------

func instanceAdminFernetKey(prefix, secret string) [32]byte {
	return sha256.Sum256([]byte(prefix + secret))
}

func (s *InstanceAdmin) encrypt(prefix, plain string) (string, error) {
	if plain == "" {
		return "", nil
	}
	if len(s.env.SecretKeys) == 0 {
		return "", refused("no SECRET_KEY")
	}
	key := instanceAdminFernetKey(prefix, s.env.SecretKeys[0])
	block, err := aes.NewCipher(key[16:])
	if err != nil {
		return "", err
	}
	padding := aes.BlockSize - len(plain)%aes.BlockSize
	padded := append([]byte(plain), bytes.Repeat([]byte{byte(padding)}, padding)...)
	token := make([]byte, 1+8+16, 1+8+16+len(padded)+32)
	token[0] = 0x80
	binary.BigEndian.PutUint64(token[1:9], uint64(s.now().Unix()))
	if _, err = io.ReadFull(s.random, token[9:25]); err != nil {
		return "", err
	}
	ciphertext := make([]byte, len(padded))
	cipher.NewCBCEncrypter(block, token[9:25]).CryptBlocks(ciphertext, padded)
	token = append(token, ciphertext...)
	mac := hmac.New(sha256.New, key[:16])
	mac.Write(token)
	token = mac.Sum(token)
	return base64.URLEncoding.EncodeToString(token), nil
}

// decrypt is decrypt_password / decrypt_secret: "" for a falsy value,
// errInstanceAdminCredential when no key opens the token (Django's
// ImproperlyConfigured). A non-string or non-UTF-8 plaintext is refused.
func (s *InstanceAdmin) decrypt(prefix string, value any) (string, error) {
	if !instanceTruthy(value) {
		return "", nil
	}
	token, ok := value.(string)
	if !ok {
		return "", refused("stored credential is not a string")
	}
	for _, secret := range s.env.SecretKeys {
		key := instanceAdminFernetKey(prefix, secret)
		if plain, ok := fernetOpen(token, key); ok {
			if !utf8.Valid(plain) {
				return "", refused("stored credential is not UTF-8")
			}
			return string(plain), nil
		}
	}
	return "", errInstanceAdminCredential
}

func fernetOpen(token string, key [32]byte) ([]byte, bool) {
	data, err := base64.URLEncoding.DecodeString(token)
	if err != nil {
		return nil, false
	}
	if len(data) < 1+8+16+16+32 || data[0] != 0x80 || (len(data)-1-8-16-32)%aes.BlockSize != 0 {
		return nil, false
	}
	body, tag := data[:len(data)-32], data[len(data)-32:]
	mac := hmac.New(sha256.New, key[:16])
	mac.Write(body)
	if !hmac.Equal(mac.Sum(nil), tag) {
		return nil, false
	}
	block, err := aes.NewCipher(key[16:])
	if err != nil {
		return nil, false
	}
	plain := make([]byte, len(body)-25)
	cipher.NewCBCDecrypter(block, body[9:25]).CryptBlocks(plain, body[25:])
	pad := int(plain[len(plain)-1])
	if pad == 0 || pad > aes.BlockSize {
		return nil, false
	}
	for _, b := range plain[len(plain)-pad:] {
		if int(b) != pad {
			return nil, false
		}
	}
	return plain[:len(plain)-pad], true
}

// ---------------------------------------------------------------------------
// admin/email
// ---------------------------------------------------------------------------

func (s *InstanceAdmin) row(ctx context.Context) (*domain.InstanceAdminRow, error) {
	row, err := s.store.Read(ctx)
	if err == nil && row == nil {
		return nil, refused("no configuration")
	}
	return row, err
}

// emailSettings is config.email_settings when effective_email uses it.
func emailSettings(row *domain.InstanceAdminRow) (map[string]any, bool, error) {
	value, err := instanceAdminDecode(row.EmailSettings)
	if err != nil {
		return nil, false, err
	}
	if !row.SelfHosted || !instanceTruthy(value) {
		return nil, false, nil
	}
	settings, ok := value.(map[string]any)
	if !ok {
		return nil, true, refused("email settings are not an object")
	}
	return settings, true, nil
}

// environmentEmail is effective_email's environment branch.
func (s *InstanceAdmin) environmentEmail(revision int64) (map[string]any, error) {
	if s.env.EmailPortInvalid {
		return nil, errInstanceAdminEnvironmentPort
	}
	security := "none"
	switch {
	case s.env.EmailUseTLS && s.env.EmailUseSSL:
		security = "invalid"
	case s.env.EmailUseTLS:
		security = "tls"
	case s.env.EmailUseSSL:
		security = "ssl"
	}
	return map[string]any{"source": "environment", "host": s.env.EmailHost, "port": s.env.EmailPort, "username": s.env.EmailUsername,
		"password": s.env.EmailPassword, "security": security, "from_email": s.environmentFrom(), "sender_name": s.env.FromName,
		"revision": revision}, nil
}

// environmentFrom is effective_email's from_email: the parsed address
// unless it is empty or 'None' (an unset EMAIL_HOST_USER).
func (s *InstanceAdmin) environmentFrom() string {
	if s.env.FromAddress == "None" {
		return ""
	}
	return s.env.FromAddress
}

// publicEmail is email_configuration.public_email(config).
func (s *InstanceAdmin) publicEmail(row *domain.InstanceAdminRow) (map[string]any, error) {
	settings, instance, err := emailSettings(row)
	if err != nil {
		return nil, err
	}
	var value map[string]any
	credentialError := false
	if instance {
		value = instanceAdminCopy(settings)
		plain, err := s.decrypt(instanceEmailCipher, row.EmailPassword)
		switch {
		case errors.Is(err, errInstanceAdminCredential):
			credentialError, plain = true, ""
		case err != nil:
			return nil, err
		}
		value["password"], value["source"], value["revision"] = plain, "instance", row.EmailRevision
		value["password_configured"] = row.EmailPassword != ""
	} else {
		if value, err = s.environmentEmail(row.EmailRevision); err != nil {
			return nil, err
		}
		value["password_configured"] = value["password"] != ""
	}
	delete(value, "password")
	value["credential_error"], value["restart_required"], value["application"] = credentialError, false, instanceEmailApplication
	return value, nil
}

func (s *InstanceAdmin) Email(ctx context.Context) (map[string]any, error) {
	row, err := s.row(ctx)
	if err != nil {
		return nil, err
	}
	results, err := s.diagnosticResults(row)
	if err != nil {
		return nil, err
	}
	lastTest := results["email_test"]
	if instanceTruthy(lastTest) {
		test, ok := lastTest.(map[string]any)
		if !ok {
			return nil, refused("stored email test is not an object")
		}
		if !pythonEqual(test["revision"], row.EmailRevision) {
			lastTest = nil
		}
	}
	body, err := s.publicEmail(row)
	if err != nil {
		return nil, err
	}
	body["last_test"] = lastTest
	return body, nil
}

func (s *InstanceAdmin) diagnosticResults(row *domain.InstanceAdminRow) (map[string]any, error) {
	value, err := instanceAdminDecode(row.DiagnosticResults)
	if err != nil {
		return nil, err
	}
	return instanceAdminObject(value)
}

// persistEmail is EmailSettingsView.persist after the new settings.
func (s *InstanceAdmin) persistEmail(row *domain.InstanceAdminRow, settings json.RawMessage, password string) (*domain.InstanceAdminUpdate, error) {
	encrypted, err := s.encrypt(instanceEmailCipher, password)
	if err != nil {
		return nil, err
	}
	results, err := s.diagnosticResults(row)
	if err != nil {
		return nil, err
	}
	results = instanceAdminCopy(results)
	checks := []any{}
	if stored, present := results["checks"]; present {
		items, ok := stored.([]any)
		if !ok {
			return nil, refused("stored checks are not a list")
		}
		for _, item := range items {
			check, ok := item.(map[string]any)
			if !ok {
				return nil, refused("stored check is not an object")
			}
			if !pythonEqual(check["id"], "email") {
				checks = append(checks, item)
			}
		}
	}
	results["checks"] = checks
	delete(results, "email_test")
	diagnostics, err := instanceAdminMarshal(results)
	if err != nil {
		return nil, err
	}
	revision, now := row.EmailRevision+1, s.now()
	row.EmailSettings, row.EmailPassword, row.EmailRevision, row.DiagnosticResults = settings, encrypted, revision, diagnostics
	return &domain.InstanceAdminUpdate{EmailSettings: settings, EmailPassword: &encrypted, EmailRevision: &revision,
		DiagnosticResults: diagnostics, UpdatedAt: &now}, nil
}

// PutEmail is EmailSettingsView.put (transaction.atomic).
func (s *InstanceAdmin) PutEmail(ctx context.Context, body any) (map[string]any, error) {
	input, err := dto.InstanceAdminEmail(body)
	if err != nil {
		return nil, err
	}
	var response map[string]any
	err = s.store.Locked(ctx, func(row *domain.InstanceAdminRow) (*domain.InstanceAdminUpdate, error) {
		if row == nil {
			return nil, refused("no configuration")
		}
		if !pythonEqual(input.Revision, row.EmailRevision) {
			return nil, instanceAdminError("Configurazione modificata in un’altra sessione. Ricarica prima di salvare.", 409)
		}
		password := ""
		if input.Password != nil {
			password = *input.Password
		} else if !input.ClearPassword {
			// effective_email(config)['password']; any failure asks for a new value.
			settings, instance, err := emailSettings(row)
			switch {
			case err != nil && !instance:
				return nil, err
			case err != nil:
				return nil, instanceAdminError("Reinserisci o rimuovi esplicitamente la password salvata.", 400)
			case settings != nil:
				plain, err := s.decrypt(instanceEmailCipher, row.EmailPassword)
				if err != nil {
					return nil, instanceAdminError("Reinserisci o rimuovi esplicitamente la password salvata.", 400)
				}
				password = plain
			case s.env.EmailPortInvalid:
				return nil, instanceAdminError("Reinserisci o rimuovi esplicitamente la password salvata.", 400)
			default:
				password = s.env.EmailPassword
			}
		}
		if input.ClearPassword {
			password = ""
		}
		if input.Security == "none" && password != "" {
			return nil, instanceAdminError("Rimuovi la password oppure abilita TLS o SSL.", 400)
		}
		settings, err := instanceAdminMarshal(map[string]any{"host": input.Host, "port": input.Port, "security": input.Security,
			"username": input.Username, "from_email": input.FromEmail, "sender_name": input.SenderName})
		if err != nil {
			return nil, err
		}
		update, err := s.persistEmail(row, settings, password)
		if err != nil {
			return nil, err
		}
		if response, err = s.publicEmail(row); err != nil {
			return nil, err
		}
		return update, nil
	})
	return response, err
}

// requestRevision is IntegerField(min_value=0).run_validation(request.data.get('revision')).
func requestRevision(body any) (*big.Int, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, refused("request data has no get()")
	}
	value, _ := object.Lookup("revision")
	return dto.InstanceAdminRevision(value)
}

// DeleteEmail is EmailSettingsView.delete (transaction.atomic).
func (s *InstanceAdmin) DeleteEmail(ctx context.Context, body any) (map[string]any, error) {
	revision, err := requestRevision(body)
	if err != nil {
		return nil, err
	}
	var response map[string]any
	err = s.store.Locked(ctx, func(row *domain.InstanceAdminRow) (*domain.InstanceAdminUpdate, error) {
		if row == nil {
			return nil, refused("no configuration")
		}
		if !pythonEqual(revision, row.EmailRevision) {
			return nil, instanceAdminError("Configurazione modificata. Ricarica prima di ripristinare.", 409)
		}
		update, err := s.persistEmail(row, json.RawMessage(`{}`), "")
		if err != nil {
			return nil, err
		}
		if response, err = s.publicEmail(row); err != nil {
			return nil, err
		}
		return update, nil
	})
	return response, err
}

// ---------------------------------------------------------------------------
// admin/integrations/<provider>
// ---------------------------------------------------------------------------

func instanceAdminProvider(provider string) error {
	if _, ok := dto.InstanceAdminSecretFields[provider]; !ok {
		return ErrInstanceAdminProvider
	}
	return nil
}

type effectiveIntegration struct {
	value    map[string]any
	instance bool
	revision any
}

// effective is integration_configuration.effective_integration.
func (s *InstanceAdmin) effective(provider string, row *domain.InstanceAdminRow, decrypt bool) (*effectiveIntegration, error) {
	record := map[string]any{}
	if row.SelfHosted {
		raw, err := instanceAdminDecode(row.IntegrationSettings)
		if err != nil {
			return nil, err
		}
		all, err := instanceAdminObject(raw)
		if err != nil {
			return nil, err
		}
		if stored, present := all[provider]; present {
			object, ok := stored.(map[string]any)
			if !ok {
				return nil, refused("integration record is not an object")
			}
			record = object
		}
	}
	result := &effectiveIntegration{revision: json.Number("0")}
	if revision, present := record["revision"]; present {
		result.revision = revision
	}
	if settings := record["settings"]; settings != nil {
		object, ok := settings.(map[string]any)
		if !ok {
			return nil, refused("integration settings are not an object")
		}
		result.value, result.instance = instanceAdminCopy(object), true
		if decrypt {
			for _, key := range dto.InstanceAdminSecretFields[provider] {
				plain, err := s.decrypt(instanceIntegrationCipher, result.value[key])
				if err != nil {
					return nil, err
				}
				result.value[key] = plain
			}
		}
	} else {
		value, err := s.environmentIntegration(provider, row)
		if err != nil {
			return nil, err
		}
		result.value = value
	}
	return result, nil
}

func (s *InstanceAdmin) environmentIntegration(provider string, row *domain.InstanceAdminRow) (map[string]any, error) {
	switch provider {
	case "ai":
		if s.env.AIIntegersInvalid {
			return nil, refused("environment AI settings")
		}
		value := map[string]any{"api_key": s.env.AIAPIKey, "model": s.env.AIModel, "cheap_model": s.env.AICheapModel, "base_url": s.env.AIBaseURL}
		for index, key := range []string{"max_iterations", "max_results", "query_timeout", "ws_rate_limit", "ws_timeout", "history_cap"} {
			if number := s.env.AIIntegers[index]; number != 0 {
				value[key] = number
			} else {
				value[key] = ""
			}
		}
		value["enabled"] = s.env.AIAPIKey != ""
		return value, nil
	case "stripe":
		return map[string]any{"public_key": s.env.StripePublicKey, "secret_key": s.env.StripeKey, "webhook_secret": s.env.StripeWebhookSecret,
			"enabled": s.env.StripePublicKey != "" || s.env.StripeKey != "" || s.env.StripeWebhookSecret != ""}, nil
	case "apple":
		return map[string]any{"client_id": s.env.AppleClientID, "enabled": s.env.AppleClientID != ""}, nil
	}
	var enabled any = s.env.GoogleClientID != ""
	if enabled == true && row.SelfHosted {
		display, err := instanceDisplay(row.DisplaySettings)
		if err != nil {
			return nil, err
		}
		if enabled, err = instanceAllowOAuth(display); err != nil {
			return nil, err
		}
	}
	return map[string]any{"client_id": s.env.GoogleClientID, "enabled": enabled}, nil
}

// publicIntegration is integration_configuration.public_integration.
func (s *InstanceAdmin) publicIntegration(provider string, row *domain.InstanceAdminRow) (map[string]any, error) {
	current, err := s.effective(provider, row, false)
	if err != nil {
		return nil, err
	}
	value := current.value
	credentialError := false
	for _, key := range dto.InstanceAdminSecretFields[provider] {
		secret, present := value[key]
		if !present {
			secret = ""
		}
		delete(value, key)
		value[key+"_configured"] = instanceTruthy(secret)
		if current.instance {
			if _, err := s.decrypt(instanceIntegrationCipher, secret); errors.Is(err, errInstanceAdminCredential) {
				credentialError = true
			} else if err != nil {
				return nil, err
			}
		}
	}
	value["source"], value["revision"] = map[bool]string{true: "instance", false: "environment"}[current.instance], current.revision
	results, err := s.diagnosticResults(row)
	if err != nil {
		return nil, err
	}
	var lastTest any
	if tests, present := results["integration_tests"]; present {
		object, ok := tests.(map[string]any)
		if !ok {
			return nil, refused("stored integration tests are not an object")
		}
		lastTest = object[provider]
	}
	if instanceTruthy(lastTest) {
		test, ok := lastTest.(map[string]any)
		if !ok {
			return nil, refused("stored integration test is not an object")
		}
		if !pythonEqual(test["revision"], current.revision) {
			lastTest = nil
		}
	}
	value["provider"], value["credential_error"], value["last_test"], value["restart_required"] = provider, credentialError, lastTest, false
	value["webhook_url"], value["authorized_origin"], value["web_login_available"] = nil, nil, nil
	if provider == "stripe" {
		value["webhook_url"] = s.env.AppURL + "/api/stripe/webhook"
	} else {
		value["web_login_available"] = provider == "google"
	}
	if provider == "google" {
		value["authorized_origin"] = s.env.AppURL
	}
	return value, nil
}

// CheckProvider is IntegrationSettingsView.provider().
func (s *InstanceAdmin) CheckProvider(provider string) error { return instanceAdminProvider(provider) }

func (s *InstanceAdmin) Integration(ctx context.Context, provider string) (map[string]any, error) {
	if err := instanceAdminProvider(provider); err != nil {
		return nil, err
	}
	row, err := s.row(ctx)
	if err != nil {
		return nil, err
	}
	return s.publicIntegration(provider, row)
}

func (s *InstanceAdmin) persistIntegration(row *domain.InstanceAdminRow, provider string, revision *big.Int, settings any) (*domain.InstanceAdminUpdate, error) {
	raw, err := instanceAdminDecode(row.IntegrationSettings)
	if err != nil {
		return nil, err
	}
	all, ok := raw.(map[string]any)
	if !ok {
		return nil, refused("integration settings are not an object")
	}
	all = instanceAdminCopy(all)
	all[provider] = map[string]any{"revision": json.Number(revision.String()), "settings": settings}
	stored, err := instanceAdminMarshal(all)
	if err != nil {
		return nil, err
	}
	now := s.now()
	row.IntegrationSettings = stored
	return &domain.InstanceAdminUpdate{IntegrationSettings: stored, UpdatedAt: &now}, nil
}

func instanceAdminFieldError(key, message string) error {
	return &dto.InstanceAdminInvalid{Body: map[string]any{key: message}}
}

// PutIntegration is IntegrationSettingsView.put (transaction.atomic).
func (s *InstanceAdmin) PutIntegration(ctx context.Context, provider string, body any) (map[string]any, error) {
	input, err := dto.InstanceAdminIntegration(provider, body)
	if err != nil {
		return nil, err
	}
	var response map[string]any
	err = s.store.Locked(ctx, func(row *domain.InstanceAdminRow) (*domain.InstanceAdminUpdate, error) {
		if row == nil {
			return nil, refused("no configuration")
		}
		current, err := s.effective(provider, row, false)
		if err != nil {
			return nil, err
		}
		if !pythonEqual(current.revision, input.Revision) {
			return nil, instanceAdminError("Integrazione modificata in un’altra sessione. Ricarica prima di salvare.", 409)
		}
		revision, ok := instanceAdminInteger(current.revision)
		if !ok {
			return nil, refused("stored revision is not an integer")
		}
		values := instanceAdminCopy(input.Values)
		for _, key := range dto.InstanceAdminSecretFields[provider] {
			cleared := false
			for _, item := range input.Clear {
				cleared = cleared || item == key
			}
			if _, given := values[key]; cleared {
				values[key] = ""
			} else if !given {
				stored, present := current.value[key]
				if !present {
					return nil, refused("retained secret is missing")
				}
				if current.instance {
					plain, err := s.decrypt(instanceIntegrationCipher, stored)
					if errors.Is(err, errInstanceAdminCredential) {
						return nil, instanceAdminFieldError(key, "Reinserisci o rimuovi esplicitamente questa credenziale.")
					} else if err != nil {
						return nil, err
					}
					values[key] = plain
				} else {
					values[key] = stored
				}
			}
		}
		if provider == "stripe" {
			if err := dto.InstanceAdminStripeRevalidate(values, revision); err != nil {
				return nil, err
			}
		}
		if values["enabled"] == true {
			if err := enabledIntegrationRules(provider, values); err != nil {
				return nil, err
			}
		}
		settings := instanceAdminCopy(values)
		for _, key := range dto.InstanceAdminSecretFields[provider] {
			plain, _ := settings[key].(string)
			if settings[key], err = s.encrypt(instanceIntegrationCipher, plain); err != nil {
				return nil, err
			}
		}
		update, err := s.persistIntegration(row, provider, new(big.Int).Add(revision, big.NewInt(1)), settings)
		if err != nil {
			return nil, err
		}
		if response, err = s.publicIntegration(provider, row); err != nil {
			return nil, err
		}
		return update, nil
	})
	return response, err
}

func enabledIntegrationRules(provider string, values map[string]any) error {
	switch provider {
	case "stripe":
		missing := map[string]any{}
		for _, key := range []string{"public_key", "secret_key", "webhook_secret"} {
			if !instanceTruthy(values[key]) {
				missing[key] = "Campo richiesto per abilitare Stripe."
			}
		}
		if len(missing) > 0 {
			return &dto.InstanceAdminInvalid{Body: missing}
		}
		public, _ := values["public_key"].(string)
		secret, _ := values["secret_key"].(string)
		test := strings.HasPrefix(public, "pk_test_") && (strings.HasPrefix(secret, "sk_test_") || strings.HasPrefix(secret, "rk_test_"))
		live := strings.HasPrefix(public, "pk_live_") && (strings.HasPrefix(secret, "sk_live_") || strings.HasPrefix(secret, "rk_live_"))
		if !test && !live {
			return &dto.InstanceAdminInvalid{Body: []string{"Chiave pubblica e segreta devono appartenere allo stesso ambiente Stripe."}}
		}
	case "ai":
		if !instanceTruthy(values["api_key"]) {
			return instanceAdminFieldError("api_key", "Chiave API richiesta per abilitare il bot AI.")
		}
	default:
		client, _ := values["client_id"].(string)
		if client == "" {
			return instanceAdminFieldError("client_id", "Client ID richiesto per abilitare l’integrazione.")
		}
		if provider == "google" && !strings.HasSuffix(client, ".apps.googleusercontent.com") {
			return instanceAdminFieldError("client_id", "Inserisci il client ID web fornito da Google.")
		}
	}
	return nil
}

// DeleteIntegration is IntegrationSettingsView.delete (transaction.atomic).
func (s *InstanceAdmin) DeleteIntegration(ctx context.Context, provider string, body any) (map[string]any, error) {
	revision, err := requestRevision(body)
	if err != nil {
		return nil, err
	}
	var response map[string]any
	err = s.store.Locked(ctx, func(row *domain.InstanceAdminRow) (*domain.InstanceAdminUpdate, error) {
		if row == nil {
			return nil, refused("no configuration")
		}
		current, err := s.effective(provider, row, false)
		if err != nil {
			return nil, err
		}
		if !pythonEqual(current.revision, revision) {
			return nil, instanceAdminError("Integrazione modificata. Ricarica prima di ripristinare.", 409)
		}
		update, err := s.persistIntegration(row, provider, new(big.Int).Add(revision, big.NewInt(1)), nil)
		if err != nil {
			return nil, err
		}
		if response, err = s.publicIntegration(provider, row); err != nil {
			return nil, err
		}
		return update, nil
	})
	return response, err
}

// ---------------------------------------------------------------------------
// admin/diagnostics
// ---------------------------------------------------------------------------

var instanceAdminChecks = []struct {
	id, label, section string
	core               bool
}{
	{"public_url", "URL pubblico e HTTPS", "overview", true}, {"api", "API", "diagnostics", true},
	{"database", "Database", "diagnostics", true}, {"storage", "Archivio file", "diagnostics", true},
	{"worker", "Worker", "diagnostics", true}, {"scheduler", "Scheduler", "diagnostics", true},
	{"renderer", "Renderer PDF", "diagnostics", true}, {"email", "Configurazione email", "email", false},
	{"updater", "Servizio aggiornamenti", "backups", false}, {"backups", "Preparazione backup", "backups", false},
}

// Diagnostics is diagnostics.snapshot(config).
func (s *InstanceAdmin) Diagnostics(ctx context.Context) (map[string]any, error) {
	row, err := s.row(ctx)
	if err != nil {
		return nil, err
	}
	stored, err := s.diagnosticResults(row)
	if err != nil {
		return nil, err
	}
	values := map[string]map[string]any{}
	if raw, present := stored["checks"]; present {
		items, ok := raw.([]any)
		if !ok {
			return nil, refused("stored checks are not a list")
		}
		for _, item := range items {
			check, ok := item.(map[string]any)
			if !ok {
				return nil, refused("stored check is not an object")
			}
			id, present := check["id"]
			if !present {
				return nil, refused("stored check has no id")
			}
			switch key := id.(type) {
			case string:
				values[key] = check
			case map[string]any, []any:
				return nil, refused("stored check id is not hashable")
			}
		}
	}
	checks, core := []any{}, []any{}
	for _, spec := range instanceAdminChecks {
		check := map[string]any{"status": "not_checked", "level": "connectivity",
			"message": "Esegui la diagnostica per verificare questo servizio.", "checked_at": nil}
		if stored, ok := values[spec.id]; ok {
			check = instanceAdminCopy(stored)
		}
		check["id"], check["label"], check["core"], check["section"] = spec.id, spec.label, spec.core, spec.section
		if spec.core {
			status, present := check["status"]
			if !present {
				return nil, refused("stored check has no status")
			}
			core = append(core, status)
		}
		checks = append(checks, check)
	}
	overall := "not_checked"
	failed, warning, passed := false, false, len(core) > 0
	for _, status := range core {
		failed = failed || pythonEqual(status, "failed")
		warning = warning || pythonEqual(status, "warning") || pythonEqual(status, "not_configured")
		passed = passed && pythonEqual(status, "passed")
	}
	switch {
	case failed:
		overall = "failed"
	case warning:
		overall = "warning"
	case passed:
		overall = "passed"
	}
	integrations, err := s.integrationChecks(row)
	if err != nil {
		return nil, err
	}
	return map[string]any{"checks": checks, "overall": overall, "checked_at": stored["checked_at"], "email_test": stored["email_test"],
		"integrations": integrations}, nil
}

// integrationChecks is diagnostics.integrations(config).
func (s *InstanceAdmin) integrationChecks(row *domain.InstanceAdminRow) ([]any, error) {
	checks := []any{}
	for _, spec := range []struct{ key, label string }{{"stripe", "Stripe"}, {"google", "Accesso Google"}, {"apple", "Accesso Apple"}} {
		value, err := s.publicIntegration(spec.key, row)
		if err != nil {
			return nil, err
		}
		state, message := "not_configured", "Integrazione facoltativa non configurata."
		lookup := func(key string) (any, error) {
			item, present := value[key]
			if !present {
				return nil, refused("integration value is missing " + key)
			}
			return item, nil
		}
		switch {
		case !instanceTruthy(value["enabled"]):
			if value["source"] == "instance" || instanceTruthy(value["client_id"]) || instanceTruthy(value["public_key"]) {
				state, message = "not_applicable", "Integrazione disabilitata. Le credenziali salvate sono conservate."
			}
		case value["credential_error"] == true:
			state, message = "warning", "Una credenziale salvata non può essere letta. Reinseriscila nella configurazione dell’integrazione."
		case spec.key == "stripe":
			public, err := lookup("public_key")
			if err != nil {
				return nil, err
			}
			if !(instanceTruthy(public) && value["secret_key_configured"] == true && value["webhook_secret_configured"] == true) {
				state, message = "warning", "Completa chiave pubblica, chiave segreta e firma webhook nella configurazione Stripe."
				break
			}
			effective, err := s.effective("stripe", row, true)
			if err != nil {
				return nil, err
			}
			publicKey, ok := public.(string)
			secret, valid := effective.value["secret_key"].(string)
			if !ok || !valid {
				return nil, refused("stripe keys are not strings")
			}
			test := strings.HasPrefix(publicKey, "pk_test_") && (strings.HasPrefix(secret, "sk_test_") || strings.HasPrefix(secret, "rk_test_"))
			live := strings.HasPrefix(publicKey, "pk_live_") && (strings.HasPrefix(secret, "sk_live_") || strings.HasPrefix(secret, "rk_live_"))
			switch {
			case test:
				state, message = "passed", "Credenziali sandbox presenti. Pagamenti e webhook non verificati."
			case live:
				state, message = "passed", "Credenziali produzione presenti. Pagamenti e webhook non verificati."
			default:
				state, message = "warning", "Le chiavi Stripe non indicano lo stesso ambiente. Correggi la configurazione."
			}
		default:
			client, err := lookup("client_id")
			if err != nil {
				return nil, err
			}
			if !instanceTruthy(client) {
				break
			}
			state, message = "passed", "Client ID configurato. Un accesso reale con il provider non è stato verificato."
			legacy := row.GoogleClientID
			if spec.key == "apple" {
				legacy = row.AppleClientID
			}
			if value["source"] == "environment" && legacy != "" && !pythonEqual(legacy, client) {
				state, message = "warning", "Il client ID pubblico precedente e quello del server non coincidono. Salva il client ID corretto qui per allinearli."
			} else if spec.key == "apple" {
				state, message = "warning", "Client ID configurato per verificare token Apple. Il pulsante di accesso Apple sul web non è disponibile in questa versione."
			}
		}
		checks = append(checks, map[string]any{"id": spec.key, "label": spec.label, "status": state, "level": "configuration",
			"message": message, "checked_at": pythonISOFormat(s.now()), "core": false})
	}
	return checks, nil
}
