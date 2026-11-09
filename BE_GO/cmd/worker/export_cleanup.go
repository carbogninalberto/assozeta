package main

import (
	"context"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3credentials"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3trust"
)

// composeExportCleanupObjects is the S3 default storage cleanup_old_exports
// deletes from (core.settings always selects S3Boto3Storage). Credentials and
// trust load lazily, at the first delete, as in cmd/server.
func composeExportCleanupObjects(cfg config.Config) *documentobjects.S3 {
	settings := cfg.Storage
	optional := func(value *string) string {
		if value == nil {
			return ""
		}
		return *value
	}
	return documentobjects.New(documentobjects.Options{Endpoint: settings.Endpoint, Region: settings.Region, Bucket: settings.Bucket,
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
}
