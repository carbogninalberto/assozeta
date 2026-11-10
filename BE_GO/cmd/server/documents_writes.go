package main

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// documentUploadObjects is S3Storage._save's write parameters on top of the
// shared document storage: AWS_S3_OBJECT_PARAMETERS' CacheControl and AWS_DEFAULT_ACL.
type documentUploadObjects struct {
	*documentobjects.S3
	cacheControl, acl string
}

func (o documentUploadObjects) Put(ctx context.Context, key string, body []byte, contentType, contentEncoding string) error {
	return o.S3.Put(ctx, key, body, documentobjects.Upload{ContentType: contentType, ContentEncoding: contentEncoding,
		CacheControl: o.cacheControl, ACL: o.acl})
}

// composeCourseLocationUploads is the document storage for course location
// documents_data uploads (default_storage.save).
func composeCourseLocationUploads(cfg config.Config, db *sqlx.DB) *service.DocumentUploads {
	objects := documentUploadObjects{S3: composeDocumentObjects(cfg), cacheControl: cfg.Storage.CacheControl}
	if cfg.Storage.DefaultACL != nil {
		objects.acl = *cfg.Storage.DefaultACL
	}
	return service.NewDocumentUploads(repository.NewDocumentUploads(db), objects, nil, cfg.Storage.FileOverwrite, time.Now, uuid.NewString)
}

func composeDocumentUploads(cfg config.Config, db *sqlx.DB, cache *redis.Client, sessions *repository.DjangoImpersonationCache,
	actors *service.ActorAuthentication, identities *service.IdentityResolver, audit handler.LoginAuditReader,
	operations *paymentcategorylifecycle.Lifecycle) (*handler.DocumentUploadsHandler, error) {
	uploads := composeDocumentStorage(cfg, db, sessions).WithCertificateLimit(cfg.MedicalCertificateUpload.MaxUploadBytes)
	// MedicalCertificateUploadThrottle keeps its histories in Django's cache.
	throttle, err := repository.NewDRFThrottleCache(cache)
	if err != nil {
		return nil, err
	}
	return handler.NewDocumentUploadsHandler(uploads, service.NewArchiveWrites(repository.NewDocumentArchive(db), time.Now), actors, identities,
		audit, operations).WithCertificateThrottle(throttle, int(cfg.MedicalCertificateUpload.UploadsPerHour), time.Now), nil
}

// composeDocumentStorage is the default_storage the document upload views and
// payment attachments_data write through.
func composeDocumentStorage(cfg config.Config, db *sqlx.DB, sessions *repository.DjangoImpersonationCache) *service.DocumentUploads {
	objects := documentUploadObjects{S3: composeDocumentObjects(cfg), cacheControl: cfg.Storage.CacheControl}
	if cfg.Storage.DefaultACL != nil {
		objects.acl = *cfg.Storage.DefaultACL
	}
	return service.NewDocumentUploads(repository.NewDocumentUploads(db), objects, sessions, cfg.Storage.FileOverwrite, time.Now, uuid.NewString)
}

// composeMembershipDocumentUploads is the document storage subscription/memberships' documents_data uses.
func composeMembershipDocumentUploads(cfg config.Config, db *sqlx.DB) *service.DocumentUploads {
	return composeDocumentStorage(cfg, db, nil)
}

// composePersonaPictures is the default storage of the personas picture_path upload.
func composePersonaPictures(cfg config.Config) service.PersonaPictureObjects {
	objects := documentUploadObjects{S3: composeDocumentObjects(cfg), cacheControl: cfg.Storage.CacheControl}
	if cfg.Storage.DefaultACL != nil {
		objects.acl = *cfg.Storage.DefaultACL
	}
	return objects
}
