package service

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/instancelogo"
	"github.com/google/uuid"
)

// InstanceSetup ports BE/instance/views.py InstanceSetupView,
// InstanceReconfigureView and InstanceLogoUploadView (also OwnerLogoView)
// after their permissions, and the permissions themselves
// (SetupTokenOrAuthenticated, is_primary_association_owner_or_superuser).
type InstanceSetup struct {
	store     InstanceSetupStore
	bootstrap InstanceAdminBootstrap
	storage   InstanceSetupLogoStorage
	hashes    InstanceSetupPasswords
	settings  InstanceSetupSettings
	now       func() time.Time
}

type InstanceSetupStore interface {
	First(context.Context) (*domain.InstanceSetupConfiguration, error)
	ImportTaskReady(context.Context, string) (bool, error)
	CreateFresh(context.Context, domain.InstanceSetupFresh, domain.LoginAuditContext, func() time.Time) error
	Reconfigure(context.Context, int64, map[string]any, *string, time.Time) error
	SetLogo(context.Context, int64, string, time.Time) error
}

// InstanceSetupLogoStorage is the default (S3) storage as the upload view uses it.
type InstanceSetupLogoStorage interface {
	Exists(context.Context, string) (bool, error)
	Delete(context.Context, string) error
	Save(context.Context, string, []byte, instancelogo.WriteParameters) error
}

// InstanceSetupPasswords is make_password with the default hasher.
type InstanceSetupPasswords interface {
	Encode(context.Context, string) (string, error)
}

// InstanceSetupSettings are the core.settings values the views read.
type InstanceSetupSettings struct {
	SetupToken string
	// AWS_S3_OBJECT_PARAMETERS CacheControl, AWS_DEFAULT_ACL and
	// AWS_S3_FILE_OVERWRITE of the default storage.
	CacheControl  string
	DefaultACL    *string
	FileOverwrite bool
	// DisplayDefaults is DEFAULT_DISPLAY_SETTINGS as stored JSON.
	DisplayDefaults string
}

func NewInstanceSetup(store InstanceSetupStore, bootstrap InstanceAdminBootstrap, storage InstanceSetupLogoStorage, hashes InstanceSetupPasswords,
	settings InstanceSetupSettings, now func() time.Time) *InstanceSetup {
	return &InstanceSetup{store: store, bootstrap: bootstrap, storage: storage, hashes: hashes, settings: settings, now: now}
}

// InstanceSetupReply is a view's Response(body, status).
type InstanceSetupReply struct {
	Status int
	Body   any
}

const instanceCanonicalLogo = "/api/instance/logo.png"

func setupFailure(status int, message string) *InstanceSetupReply {
	return &InstanceSetupReply{Status: status, Body: map[string]any{"success": false, "error": message}}
}

// ownerOrSuperuser is is_primary_association_owner_or_superuser(user, config).
func ownerOrSuperuser(user *domain.Account, config *domain.InstanceSetupConfiguration) bool {
	if user == nil {
		return false
	}
	if user.Superuser {
		return true
	}
	return config != nil && config.OwnerID != nil && *config.OwnerID == user.ID
}

// SetupAllowed is SetupTokenOrAuthenticated.has_permission for request.user
// (the actor itself on instance paths) and the X-Setup-Token header.
func (s *InstanceSetup) SetupAllowed(ctx context.Context, user *domain.Account, token string) (bool, error) {
	config, err := s.store.First(ctx)
	if err != nil {
		return false, err
	}
	if config != nil {
		return ownerOrSuperuser(user, config), nil
	}
	expected := s.settings.SetupToken
	if expected == "" {
		return false, nil
	}
	// constant_time_compare(provided_token, expected_token)
	return subtle.ConstantTimeCompare([]byte(token), []byte(expected)) == 1, nil
}

// Configured is InstanceConfiguration.is_configured().
func (s *InstanceSetup) Configured(ctx context.Context) (bool, error) {
	config, err := s.store.First(ctx)
	return config != nil, err
}

// Configure is InstanceSetupView.post after the is_configured check; body
// is request.data and audit the request's auditlog context.
func (s *InstanceSetup) Configure(ctx context.Context, body any, audit domain.LoginAuditContext) (*InstanceSetupReply, error) {
	input, err := dto.InstanceSetupData(body)
	if err != nil {
		return nil, err
	}
	if input.RefusedURL || !input.DomainASCII {
		return nil, domain.ErrInstanceSetupRefused
	}
	if !input.Fresh {
		// _link_imported_instance: AsyncResult(import_task_id). A task without a
		// ready result raises inside the transaction, which rolls back the
		// configuration; linking a finished import is not ported.
		ready, err := s.store.ImportTaskReady(ctx, input.ImportTask)
		if err != nil {
			return nil, err
		}
		if ready {
			return nil, domain.ErrInstanceSetupRefused
		}
		return setupFailure(500, "Import task is still in progress. Please wait for completion."), nil
	}
	email, ok := normalizeSetupEmail(input.OwnerEmail)
	// The model columns (EmailField max_length=254) reject longer values
	// inside the transaction; Django answers with the database error text.
	if !ok || len([]rune(email)) > 254 || len([]rune(input.SupportEmail)) > 254 {
		return nil, domain.ErrInstanceSetupRefused
	}
	hash, err := s.hashes.Encode(ctx, input.OwnerPassword)
	if err != nil {
		return nil, err
	}
	username := make([]byte, 10)
	if _, err = rand.Read(username); err != nil {
		return nil, err
	}
	logo := ""
	if input.Logo != "" {
		logo = input.Logo
	}
	fresh := domain.InstanceSetupFresh{Domain: input.Domain, Name: input.Name, Abbreviation: input.Abbreviation, PrimaryColor: input.PrimaryColor,
		SupportEmail: input.SupportEmail, LogoPath: logo, GoogleClientID: input.GoogleClientID, AppleClientID: input.AppleClientID,
		AssociationName: input.AssociationName, OwnerEmail: email, PasswordHash: hash, Username: hex.EncodeToString(username),
		UserID: uuid.NewString(), AssociationID: uuid.NewString(), CashAccountID: uuid.NewString(), BankAccountID: uuid.NewString(),
		OnboardingID: uuid.NewString()}
	if err = s.store.CreateFresh(ctx, fresh, audit, s.now); err != nil {
		if errors.Is(err, domain.ErrInstanceSetupExists) {
			return setupFailure(500, domain.ErrInstanceSetupExists.Error()), nil
		}
		return nil, err
	}
	return &InstanceSetupReply{Status: 200, Body: map[string]any{"success": true, "message": "Istanza configurata con successo",
		"association_id": fresh.AssociationID, "redirect": "/login"}}, nil
}

// normalizeSetupEmail is BaseUserManager.normalize_email: the domain part
// lowercased. ok=false when that needs Python's Unicode lowercasing.
func normalizeSetupEmail(email string) (string, bool) {
	at := strings.LastIndex(email, "@")
	if at < 0 {
		return email, true
	}
	domainPart := email[at+1:]
	for _, r := range domainPart {
		if r > 127 {
			return "", false
		}
	}
	return email[:at] + "@" + strings.ToLower(domainPart), true
}

// Reconfigure is InstanceReconfigureView.put for request.user; body is read
// only after the configuration and owner checks.
func (s *InstanceSetup) Reconfigure(ctx context.Context, user *domain.Account, body func() (any, error)) (*InstanceSetupReply, error) {
	config, err := s.store.First(ctx)
	if err != nil {
		return nil, err
	}
	if config == nil {
		return setupFailure(404, "Instance not configured"), nil
	}
	if !ownerOrSuperuser(user, config) {
		return setupFailure(403, "Only the instance owner can reconfigure"), nil
	}
	data, err := body()
	if err != nil {
		return nil, err
	}
	update, err := dto.InstanceReconfigureInput(data)
	if err != nil {
		return nil, err
	}
	if update.RefusedURL {
		return nil, domain.ErrInstanceSetupRefused
	}
	var display *string
	if update.HasDisplay {
		// InstanceConfiguration.save replaces empty display settings with the defaults.
		stored := s.settings.DisplayDefaults
		if dto.PythonTruthy(update.DisplaySettings) {
			stored = string(dto.PythonJSONDumps(update.DisplaySettings))
		}
		display = &stored
	}
	if err = s.store.Reconfigure(ctx, config.ID, update.Columns, display, s.now()); err != nil {
		return nil, err
	}
	return &InstanceSetupReply{Status: 200, Body: map[string]any{"success": true, "message": "Configurazione aggiornata"}}, nil
}

// InstanceSetupUpload is request.FILES['file'].
type InstanceSetupUpload struct {
	ContentType string
	Size        int64
	// Content holds the bytes of an upload within the size limit.
	Content []byte
}

// InstanceLogoMaximum is the view's 5 MiB limit.
const InstanceLogoMaximum = 5 * 1024 * 1024

var instanceLogoExtensions = map[string]string{"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}

// UploadLogo is InstanceLogoUploadView.post after request.FILES is parsed.
func (s *InstanceSetup) UploadLogo(ctx context.Context, file *InstanceSetupUpload) (*InstanceSetupReply, error) {
	if file == nil {
		return setupFailure(400, "No file provided"), nil
	}
	extension, allowed := instanceLogoExtensions[file.ContentType]
	if !allowed {
		return setupFailure(400, "Invalid file type. Use PNG, JPG, or WebP."), nil
	}
	if file.Size > InstanceLogoMaximum {
		return setupFailure(400, "File too large. Maximum size is 5MB."), nil
	}
	reply, err := s.saveLogo(ctx, "instance/logo."+extension, file)
	if errors.Is(err, domain.ErrInstanceSetupRefused) {
		return nil, err
	}
	if err != nil {
		// The view's except Exception branch.
		return setupFailure(500, "Failed to save logo"), nil
	}
	return reply, nil
}

func (s *InstanceSetup) saveLogo(ctx context.Context, name string, file *InstanceSetupUpload) (*InstanceSetupReply, error) {
	for _, old := range []string{"png", "jpg", "svg", "webp"} {
		path := "instance/logo." + old
		exists, err := s.storage.Exists(ctx, path)
		if err != nil {
			return nil, err
		}
		if exists {
			if err = s.storage.Delete(ctx, path); err != nil {
				return nil, err
			}
		}
	}
	if !s.settings.FileOverwrite {
		// get_available_name: a name taken again after the deletes would be
		// saved under a generated alternative.
		exists, err := s.storage.Exists(ctx, name)
		if err != nil {
			return nil, err
		}
		if exists {
			return nil, domain.ErrInstanceSetupRefused
		}
	}
	if err := s.storage.Save(ctx, name, file.Content, instancelogo.WriteParameters{ContentType: file.ContentType,
		CacheControl: s.settings.CacheControl, ACL: s.settings.DefaultACL}); err != nil {
		return nil, err
	}
	config, err := s.store.First(ctx)
	if err != nil {
		return nil, err
	}
	logo := instanceCanonicalLogo
	if config != nil {
		if err = s.store.SetLogo(ctx, config.ID, instanceCanonicalLogo, s.now()); err != nil {
			return nil, err
		}
		// InstanceConfigSerializer(config).data['oem']['logo']
		serialized, err := s.bootstrap.Config(ctx)
		if err != nil {
			return nil, err
		}
		logo = serialized.OEM.Logo
	}
	return &InstanceSetupReply{Status: 200, Body: map[string]any{"success": true, "logo_url": logo}}, nil
}

// InstanceOwner is IsInstanceOwner.has_permission for the authenticated actor.
func (s *InstanceSetup) InstanceOwner(ctx context.Context, actor domain.Account) (bool, error) {
	access, err := s.bootstrap.Access(ctx, actor)
	if err != nil {
		return false, err
	}
	return access.IsAdministrator, nil
}
