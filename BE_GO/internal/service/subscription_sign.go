package service

import (
	"context"
	"encoding/base64"
	"errors"
	"log/slog"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

var (
	// ErrSubscriptionSignValidation is Response({'msg': 'validation errors.'}, 400).
	ErrSubscriptionSignValidation = errors.New("subscription sign validation errors")
	// ErrSubscriptionSignInvalid is InvalidSignatureError (400).
	ErrSubscriptionSignInvalid = errors.New("invalid signature")
	// ErrSubscriptionSignStorage is StorageUnavailableError (503).
	ErrSubscriptionSignStorage = errors.New("signature storage unavailable")
	// ErrSubscriptionSignFailed is an exception the view does not handle (the 500 page).
	ErrSubscriptionSignFailed = errors.New("subscription sign failed")
	// ErrSubscriptionSignNotPorted refuses, before any write, a path the port does not reproduce.
	ErrSubscriptionSignNotPorted = errors.New("subscription sign path not ported")
)

// SubscriptionSignUUIDError is is_valid_uuid's ValidationError(<exception>): a 400 list.
type SubscriptionSignUUIDError struct{ Detail string }

func (e *SubscriptionSignUUIDError) Error() string { return e.Detail }

// SubscriptionSignFieldsError is SignatureRequestSerializer's 400 field errors.
type SubscriptionSignFieldsError struct {
	Fields []dto.SubscriptionSignatureField
}

func (e *SubscriptionSignFieldsError) Error() string { return "invalid signature request" }

// SubscriptionSignObjects is the default storage the signature upload uses.
type SubscriptionSignObjects interface {
	Location() string
	Exists(ctx context.Context, key string) (bool, error)
	Put(ctx context.Context, key string, body []byte, contentType, contentEncoding string) error
	PutACL(ctx context.Context, key, acl string) error
	Delete(ctx context.Context, key string) error
}

// SubscriptionSignSettings are AWS_S3_USE_OBJECT_ACL and AWS_S3_PUBLIC_BASE_URL.
type SubscriptionSignSettings struct {
	UseObjectACL  bool
	PublicBaseURL string
}

type SubscriptionSignRequest struct {
	Body          any
	Group         string
	Authorization *string // request.headers.get('authorization')
	Audit         domain.LoginAuditContext
}

// SubscriptionSigns ports subscription_sign (subscription-sign.md).
type SubscriptionSigns struct {
	store    domain.SubscriptionSignStore
	objects  SubscriptionSignObjects
	settings SubscriptionSignSettings
	tasks    celery.Publisher
	now      func() time.Time
	logger   *slog.Logger
}

func NewSubscriptionSigns(store domain.SubscriptionSignStore, objects SubscriptionSignObjects, settings SubscriptionSignSettings,
	tasks celery.Publisher, now func() time.Time, logger *slog.Logger) *SubscriptionSigns {
	if logger == nil {
		logger = slog.Default()
	}
	return &SubscriptionSigns{store: store, objects: objects, settings: settings, tasks: tasks, now: now, logger: logger}
}

// subscriptionSignUUID is is_valid_uuid(value): uuid.UUID(value) raising for
// anything but a well-formed str.
func subscriptionSignUUID(value any) (string, error) {
	switch v := value.(type) {
	case nil:
		return "", &SubscriptionSignUUIDError{Detail: "one of the hex, bytes, bytes_le, fields, or int arguments must be given"}
	case string:
		id, err := paymentInfoParseUUID(v)
		var invalid *PaymentInfoUUIDError
		if errors.As(err, &invalid) {
			return "", &SubscriptionSignUUIDError{Detail: invalid.Detail}
		}
		if err != nil {
			return "", err
		}
		return id.String(), nil
	}
	return "", &SubscriptionSignUUIDError{Detail: "'" + pythonTypeNameOf(value) + "' object has no attribute 'replace'"}
}

// subscriptionSignTimestamp is str(datetime.now().timestamp()) for the naive
// local now: the C implementation adds microsecond / 1e6 to the whole seconds.
func subscriptionSignTimestamp(now time.Time) string {
	seconds := float64(now.Unix()) + float64(now.Nanosecond()/1000)/1e6
	text := strconv.FormatFloat(seconds, 'f', -1, 64)
	if !strings.Contains(text, ".") {
		text += ".0"
	}
	return text
}

func (s *SubscriptionSigns) lookup(identity domain.ResolvedIdentity, id, group string) domain.SubscriptionSignLookup {
	lookup := domain.SubscriptionSignLookup{ID: id, Group: supplierGroup(group)}
	if identity.ImpersonationAssociationID != "" && identity.Effective != nil {
		lookup.Scope = &domain.ResourceScope{AssociationID: identity.ImpersonationAssociationID, EffectiveID: identity.Effective.ID,
			Role: identity.Effective.Role}
	}
	return lookup
}

// signatureObjectKey is Subscription._signature_storage_object_key(saved_path).
func (s *SubscriptionSigns) signatureObjectKey(saved string) string {
	location := strings.Trim(s.objects.Location(), "/")
	cleaned := strings.TrimLeft(saved, "/")
	if location != "" && !strings.HasPrefix(cleaned, location+"/") {
		return location + "/" + cleaned
	}
	return cleaned
}

// upload is set_signature_from_base64 past the URL check: the stored name
// (signature_storage_key) and public URL, or an error the view answers.
func (s *SubscriptionSigns) upload(ctx context.Context, subscription, data string) (string, *string, error) {
	encoded := data
	if _, after, found := strings.Cut(data, ","); found {
		encoded = after
	}
	// str input must be ASCII; validate=True accepts only complete base64.
	if !asciiText(encoded) || !dto.SubscriptionSignatureBase64(encoded) {
		return "", nil, ErrSubscriptionSignInvalid
	}
	decoded, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil || len(decoded) == 0 {
		return "", nil, ErrSubscriptionSignInvalid
	}
	name := "subscriptions/" + subscription + "/signature_" + subscriptionSignTimestamp(s.now()) + ".png"
	key, err := documentkey.S3Key(name, s.objects.Location())
	if err != nil {
		return "", nil, err
	}
	// Storage.save: get_available_name (HeadObject), then one PutObject.
	exists, err := s.objects.Exists(ctx, key)
	if err != nil {
		s.logger.Error("Failed to upload signature to storage", "subscription_id", subscription, "error", err)
		return "", nil, ErrSubscriptionSignStorage
	}
	if exists {
		// get_available_name would pick a random alternative name.
		return "", nil, ErrSubscriptionSignNotPorted
	}
	contentType, known, encoding, err := pymime.GuessType(key)
	if err != nil {
		return "", nil, err
	}
	if !known {
		contentType = "application/octet-stream"
	}
	if err := s.objects.Put(ctx, key, decoded, contentType, encoding); err != nil {
		s.logger.Error("Failed to upload signature to storage", "subscription_id", subscription, "error", err)
		return "", nil, ErrSubscriptionSignStorage
	}
	if s.settings.UseObjectACL {
		if err := s.objects.PutACL(ctx, s.signatureObjectKey(name), "public-read"); err != nil {
			if cleanup := s.objects.Delete(context.WithoutCancel(ctx), key); cleanup != nil {
				s.logger.Warn("Failed to clean up signature object after upload failure", "subscription_id", subscription, "error", cleanup)
			}
			s.logger.Error("Failed to upload signature to storage", "subscription_id", subscription, "error", err)
			return "", nil, ErrSubscriptionSignStorage
		}
	}
	var public *string
	if base := strings.TrimRight(strings.TrimSpace(s.settings.PublicBaseURL), "/"); base != "" {
		value := base + "/" + strings.TrimLeft(s.signatureObjectKey(name), "/")
		public = &value
	}
	return name, public, nil
}

// Sign follows subscription_sign.
func (s *SubscriptionSigns) Sign(ctx context.Context, identity domain.ResolvedIdentity, request SubscriptionSignRequest) error {
	object, ok := request.Body.(domain.RequestObject)
	if !ok {
		return ErrSubscriptionSignFailed // data.keys() on a non-mapping
	}
	names := map[string]bool{}
	for _, field := range object {
		names[field.Name] = true
	}
	if len(names) != 2 || !names["subscription_id"] || !names["signature"] {
		return ErrSubscriptionSignValidation
	}
	raw, _ := object.Lookup("subscription_id")
	id, err := subscriptionSignUUID(raw)
	if err != nil {
		return err
	}
	lookup := s.lookup(identity, id, request.Group)
	row, err := s.store.Subscription(ctx, lookup)
	if err != nil {
		return err
	}
	if row == nil {
		return ErrSubscriptionSignFailed // filter(...)[0]: IndexError
	}
	value, _ := object.Lookup("signature")
	signature, invalid := dto.ParseSubscriptionSignature(value)
	if invalid != nil {
		return &SubscriptionSignFieldsError{Fields: invalid}
	}
	audit := paymentCategoryAuditContext(identity, request.Audit)
	tx, err := s.store.BeginSubscriptionSign(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	locked, err := tx.Lock(ctx, lookup)
	if err != nil {
		return err
	}
	if locked == nil {
		return ErrSubscriptionSignFailed
	}
	var newKey *string
	saved := false
	if signature.There && signature.Data != nil && *signature.Data != "" {
		data := *signature.Data
		save := domain.SubscriptionSignSave{SignatureID: uuid.NewString(), User: identity.ScopeUser.ID, Data: data, Audit: audit,
			At: s.now().UTC().Truncate(time.Microsecond)}
		if strings.HasPrefix(data, "http://") || strings.HasPrefix(data, "https://") {
			save.URL = &data
		} else {
			name, public, err := s.upload(ctx, locked.ID, data)
			if err != nil {
				return err
			}
			save.Key, save.URL, newKey = &name, public, &name
		}
		// save(update_fields=[...]) runs Subscription.save(): its season range raises after the upload,
		// the object is deleted and the block rolls back.
		season, err := tx.SaveSeason(ctx, locked.ID)
		if err == nil {
			err = modelHooksSubscriptionSave(ctx, season)
		}
		if err != nil {
			s.cleanup(ctx, newKey)
			return err
		}
		if err := tx.Save(ctx, *locked, save); err != nil {
			s.cleanup(ctx, newKey)
			return err
		}
		saved = true
	}
	if err := tx.Commit(); err != nil {
		s.cleanup(ctx, newKey)
		return err
	}
	s.afterCommit(ctx, *locked, saved, newKey, request.Authorization)
	return nil
}

// cleanup is cleanup_storage_keys([new_storage_key]) after a failure.
func (s *SubscriptionSigns) cleanup(ctx context.Context, name *string) {
	if name == nil {
		return
	}
	key, err := documentkey.S3Key(*name, s.objects.Location())
	if err == nil {
		err = s.objects.Delete(context.WithoutCancel(ctx), key)
	}
	if err != nil {
		s.logger.Warn("Failed to clean up storage object after subscription rollback", "error", err)
	}
}

const subscriptionSignAfterCommitTimeout = 30 * time.Second

// afterCommit runs the on_commit callbacks (robust: failures are logged) in
// registration order: the old key's object when it is no longer referenced,
// subscription_signed's check_workflows_trigger and print_document_subscription.
func (s *SubscriptionSigns) afterCommit(ctx context.Context, row domain.SubscriptionSignRow, saved bool, newKey *string, authorization *string) {
	work, cancel := context.WithTimeout(context.WithoutCancel(ctx), subscriptionSignAfterCommitTimeout)
	defer cancel()
	if saved && row.Key != nil && *row.Key != "" && (newKey == nil || *newKey != *row.Key) {
		if err := s.deleteUnreferenced(work, *row.Key); err != nil {
			s.logger.Warn("Failed to delete unreferenced signature object", "storage_key", *row.Key, "error", err)
		}
	}
	if _, err := s.tasks.Publish(work, celery.Task{Name: "check_workflows_trigger", Kwargs: celery.Dict{
		{Key: "trigger_type", Value: "subscription_signed"},
		{Key: "sport_association_id", Value: uuid.MustParse(row.Association)},
		{Key: "subscription_id", Value: uuid.MustParse(row.ID)},
	}}); err != nil {
		s.logger.Error("subscription_signed callback failed", "error", err)
	}
	var header any
	if authorization != nil {
		header = *authorization
	}
	if _, err := s.tasks.Publish(work, celery.Task{Name: "print_document_subscription", Args: []any{row.ID, header}}); err != nil {
		s.logger.Error("print_document_subscription dispatch failed", "error", err)
	}
}

// deleteUnreferenced is Subscription.delete_signature_key_if_unreferenced.
func (s *SubscriptionSigns) deleteUnreferenced(ctx context.Context, name string) error {
	referenced, err := s.store.KeyReferenced(ctx, name)
	if err != nil || referenced {
		return err
	}
	key, err := documentkey.S3Key(name, s.objects.Location())
	if err != nil {
		return err
	}
	return s.objects.Delete(ctx, key)
}
