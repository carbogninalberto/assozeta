package main

import (
	"context"
	"errors"
	"io"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3credentials"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3trust"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// consumerPrintObjects is the S3 default storage (core.settings always
// selects S3Boto3Storage) with S3Storage._save's write parameters:
// AWS_S3_OBJECT_PARAMETERS' CacheControl and AWS_DEFAULT_ACL. Credentials and
// trust load lazily, at the first request, as in cmd/server.
type consumerPrintObjects struct {
	*documentobjects.S3
	cacheControl, acl string
}

func (o consumerPrintObjects) Put(ctx context.Context, key string, body []byte, contentType, contentEncoding string) error {
	return o.S3.Put(ctx, key, body, documentobjects.Upload{ContentType: contentType, ContentEncoding: contentEncoding,
		CacheControl: o.cacheControl, ACL: o.acl})
}

// Read is S3File's read of the whole object.
func (o consumerPrintObjects) Read(ctx context.Context, key string) ([]byte, error) {
	object, err := o.Open(ctx, key)
	if err != nil {
		return nil, err
	}
	defer object.Body.Close()
	return io.ReadAll(object.Body)
}

func consumerPrintStorage(cfg config.Config) consumerPrintObjects {
	settings := cfg.Storage
	optional := func(value *string) string {
		if value == nil {
			return ""
		}
		return *value
	}
	objects := documentobjects.New(documentobjects.Options{Endpoint: settings.Endpoint, Region: settings.Region, Bucket: settings.Bucket,
		Location: settings.Location,
		Trust: func(ctx context.Context) (s3trust.Policy, error) {
			return s3credentials.LoadTrust(ctx, optional(settings.SessionProfile))
		},
		Credentials: func(ctx context.Context) (aws.CredentialsProvider, error) {
			if err := settings.ValidateBackend(); err != nil {
				return nil, err
			}
			return s3credentials.New(ctx, s3credentials.Options{AccessKey: settings.AccessKey, SecretKey: settings.SecretKey,
				SessionToken: optional(settings.SessionToken), Profile: optional(settings.SessionProfile)}, nil)
		}})
	return consumerPrintObjects{S3: objects, cacheControl: settings.CacheControl, acl: optional(settings.DefaultACL)}
}

// consumerPrintRegister adds the print tasks (GET CURRENT_HOST/document/...),
// save_to_storage and export_invoices_to_zip.
func consumerPrintRegister(tasks map[string]celeryworker.Task, cfg config.Config, db *sqlx.DB, envFile string) error {
	host, _, err := config.EnvironmentValue(envFile, "CURRENT_HOST")
	if err != nil {
		return err
	}
	for name, task := range service.NewConsumerPrint(host).Tasks() {
		tasks[name] = task
	}
	objects := consumerPrintStorage(cfg)
	tasks[service.ConsumerSaveToStorageTask] = service.NewConsumerSaveToStorage(objects, cfg.Storage.FileOverwrite).Task()
	export, err := service.NewConsumerExportInvoicesZip(repository.NewConsumerExportZipStore(db), objects, cfg.Storage.FileOverwrite,
		time.Now, uuid.NewString)
	if err != nil {
		return errors.New("celery consumer cannot load the Europe/Rome time zone")
	}
	tasks[service.ConsumerExportInvoicesZipTask] = export.Task()
	return nil
}
