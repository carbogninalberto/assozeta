package main

import (
	"context"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/instancelogo"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3credentials"
)

func composeInstanceLogo(cfg config.Config) *handler.InstanceLogoHandler {
	settings := cfg.Storage
	optional := func(value *string) string {
		if value == nil {
			return ""
		}
		return *value
	}
	storage := instancelogo.NewS3(instancelogo.S3Options{TempDir: cfg.LogoSpoolDirectory, Endpoint: settings.Endpoint, Region: settings.Region, Bucket: settings.Bucket, Location: settings.Location,
		Credentials: func(ctx context.Context) (aws.CredentialsProvider, error) {
			if err := settings.ValidateBackend(); err != nil {
				return nil, err
			}
			return s3credentials.New(ctx, s3credentials.Options{AccessKey: settings.AccessKey, SecretKey: settings.SecretKey,
				SessionToken: optional(settings.SessionToken), Profile: optional(settings.SessionProfile)}, nil)
		}})
	// core.settings always selects S3Boto3Storage. STORE_ON_DISK controls other
	// legacy operations; it does not change default_storage for this public view.
	return handler.NewInstanceLogoHandler(service.NewInstanceLogo(storage))
}
