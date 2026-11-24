package business

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"net/url"
	"path"
	"slices"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3credentials"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3trust"
	"github.com/jackc/pgx/v5"
)

type subscriptionSignature struct {
	id               string
	status           int
	legacy, url, key *string
}

type signatureStorage struct {
	objects  *documentobjects.S3
	settings config.StorageSettings
	created  []string
}

func newSignatureStorage(settings config.StorageSettings) signatureStorage {
	profile := aws.ToString(settings.SessionProfile)
	objects := documentobjects.New(documentobjects.Options{
		Endpoint: settings.Endpoint, Region: settings.Region, Bucket: settings.Bucket, Location: settings.Location,
		Trust: func(ctx context.Context) (s3trust.Policy, error) {
			return s3credentials.LoadTrust(ctx, profile)
		},
		Credentials: func(ctx context.Context) (aws.CredentialsProvider, error) {
			if err := settings.ValidateBackend(); err != nil {
				return nil, err
			}
			return s3credentials.New(ctx, s3credentials.Options{
				AccessKey: settings.AccessKey, SecretKey: settings.SecretKey,
				SessionToken: aws.ToString(settings.SessionToken), Profile: profile,
			}, nil)
		},
	})
	return signatureStorage{objects: objects, settings: settings}
}

func (s signatureStorage) objectKey(key string) (string, error) {
	cleaned := path.Clean(strings.Trim(strings.ReplaceAll(key, "\\", "/"), "/"))
	if cleaned == "." || cleaned == ".." || strings.HasPrefix(cleaned, "../") {
		return "", errors.New("invalid signature storage key")
	}
	return path.Join(s.settings.Location, cleaned), nil
}

func (s signatureStorage) exists(ctx context.Context, key string) (bool, error) {
	if strings.TrimSpace(key) == "" {
		return false, nil
	}
	objectKey, err := s.objectKey(key)
	if err != nil {
		return false, err
	}
	return s.objects.Exists(ctx, objectKey)
}

// migrateSubscriptionSignatures ports application migration 0426, including
// recovery of existing objects. The deterministic object key makes retries
// safe when the object was uploaded before the database transaction committed.
func migrateSubscriptionSignatures(ctx context.Context, tx pgx.Tx, settings config.StorageSettings) (failure error) {
	var hasLegacy bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS (
		SELECT 1 FROM information_schema.columns
		WHERE table_schema = 'public' AND table_name = 'application_subscription' AND column_name = 'signature'
	)`).Scan(&hasLegacy); err != nil {
		return err
	}
	legacy := "NULL::text"
	if hasLegacy {
		legacy = "signature"
	}
	query := `SELECT subscription_id::text, status_flag, ` + legacy + `, signature_url, signature_storage_key
		FROM application_subscription
		WHERE ($1::text IS NULL OR subscription_id > $1::text::uuid)
		ORDER BY subscription_id LIMIT 500`
	storage := newSignatureStorage(settings)
	defer func() {
		if failure == nil || len(storage.created) == 0 {
			return
		}
		cleanup, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		for _, objectKey := range storage.created {
			_ = storage.objects.Delete(cleanup, objectKey)
		}
	}()
	var after *string
	for {
		rows, err := tx.Query(ctx, query, after)
		if err != nil {
			return err
		}
		batch, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (subscriptionSignature, error) {
			var signature subscriptionSignature
			err := row.Scan(&signature.id, &signature.status, &signature.legacy, &signature.url, &signature.key)
			return signature, err
		})
		if err != nil {
			return err
		}
		for _, signature := range batch {
			signatureURL, key, err := storage.resolve(ctx, signature)
			if err != nil {
				return err
			}
			if signatureURL != signature.url || key != signature.key {
				if _, err := tx.Exec(ctx, `UPDATE application_subscription
					SET signature_url = $2, signature_storage_key = $3 WHERE subscription_id = $1::text::uuid`,
					signature.id, signatureURL, key); err != nil {
					return err
				}
			}
			after = &signature.id
		}
		if len(batch) < 500 {
			break
		}
	}
	if hasLegacy {
		_, err := tx.Exec(ctx, "ALTER TABLE application_subscription DROP COLUMN signature CASCADE")
		return err
	}
	return nil
}

func (s *signatureStorage) resolve(ctx context.Context, row subscriptionSignature) (*string, *string, error) {
	signatureURL, key := row.url, row.key
	value := strings.TrimSpace(aws.ToString(row.legacy))
	switch {
	case value == "":
	case strings.HasPrefix(strings.ToLower(value), "http://") || strings.HasPrefix(strings.ToLower(value), "https://"):
		if strings.TrimSpace(aws.ToString(signatureURL)) == "" {
			signatureURL = &value
		}
	default:
		resolved, err := s.uploadLegacy(ctx, row.id, value, key)
		if err != nil {
			return nil, nil, err
		}
		key = resolved
	}
	if strings.TrimSpace(aws.ToString(key)) != "" {
		return signatureURL, key, nil
	}
	rawURL := strings.TrimSpace(aws.ToString(signatureURL))
	recovered := ""
	var err error
	if rawURL != "" {
		recovered, err = s.keyFromURL(ctx, row.id, rawURL)
	} else if row.status >= 2 && row.status <= 5 {
		recovered, err = s.orphanKey(ctx, row.id)
	}
	if recovered != "" {
		key = &recovered
	}
	return signatureURL, key, err
}

func (s *signatureStorage) uploadLegacy(ctx context.Context, id, value string, key *string) (*string, error) {
	if _, encoded, found := strings.Cut(value, ","); found {
		value = encoded
	}
	decoded, err := base64.StdEncoding.DecodeString(value)
	if err != nil || len(decoded) == 0 || strings.ContainsAny(value, "\r\n") {
		return nil, errors.New("invalid legacy subscription signature")
	}
	exists, err := s.exists(ctx, aws.ToString(key))
	if err != nil || exists {
		return key, err
	}
	digest := sha256.Sum256(decoded)
	migrated := fmt.Sprintf("subscriptions/%s/signature_migrated_%x.png", id, digest[:10])
	exists, err = s.exists(ctx, migrated)
	if err != nil {
		return nil, err
	}
	if !exists {
		objectKey, err := s.objectKey(migrated)
		if err != nil {
			return nil, err
		}
		if err := s.objects.Put(ctx, objectKey, decoded, documentobjects.Upload{
			ContentType: "image/png", CacheControl: s.settings.CacheControl, ACL: aws.ToString(s.settings.DefaultACL),
		}); err != nil {
			return nil, err
		}
		s.created = append(s.created, objectKey)
	}
	return &migrated, nil
}

func (s signatureStorage) locations() []string {
	location := strings.Trim(s.settings.Location, "/")
	if location == "" || location == "storage" {
		return []string{"storage"}
	}
	return []string{location, "storage"}
}

func (s signatureStorage) keyFromURL(ctx context.Context, id, rawURL string) (string, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return "", err
	}
	urlPath := strings.Trim(parsed.Path, "/")
	rawPaths := []string{urlPath}
	if stripped, ok := strings.CutPrefix(urlPath, s.settings.Bucket+"/"); ok {
		rawPaths = append(rawPaths, stripped)
	}
	prefix := "subscriptions/" + id + "/"
	locations := s.locations()
	seen := make(map[string]bool)
	for _, rawPath := range rawPaths {
		var candidates []string
		parts := strings.Split(rawPath, "/")
		for index, part := range parts {
			if part == "subscriptions" && index+1 < len(parts) && parts[index+1] == id {
				candidates = append(candidates, strings.Join(parts[index:], "/"))
			}
		}
		for _, location := range locations {
			if stripped, ok := strings.CutPrefix(rawPath, location+"/"); ok {
				candidates = append(candidates, stripped)
			}
		}
		candidates = append(candidates, rawPath)
		for _, candidate := range candidates {
			candidate = strings.Trim(candidate, "/")
			if seen[candidate] {
				continue
			}
			seen[candidate] = true
			belongs := strings.HasPrefix(candidate, prefix)
			for _, location := range locations {
				belongs = belongs || strings.HasPrefix(candidate, location+"/"+prefix)
			}
			if belongs {
				exists, err := s.exists(ctx, candidate)
				if err != nil || exists {
					return candidate, err
				}
			}
		}
	}
	return "", nil
}

func (s signatureStorage) orphanKey(ctx context.Context, id string) (string, error) {
	prefix := "subscriptions/" + id
	directories := []string{prefix}
	for _, location := range s.locations() {
		directories = append(directories, location+"/"+prefix)
	}
	var candidates []string
	for _, dir := range directories {
		objectDir, err := s.objectKey(dir)
		if err != nil {
			return "", err
		}
		_, keys, err := s.objects.List(ctx, objectDir+"/", "/")
		if err != nil {
			return "", err
		}
		for _, objectKey := range keys {
			name := path.Base(objectKey)
			if strings.HasPrefix(name, "signature_") && strings.HasSuffix(name, ".png") {
				candidate := dir + "/" + name
				exists, err := s.exists(ctx, candidate)
				if err != nil {
					return "", err
				}
				if exists {
					candidates = append(candidates, candidate)
				}
			}
		}
	}
	if len(candidates) == 0 {
		return "", nil
	}
	return slices.Max(candidates), nil
}
