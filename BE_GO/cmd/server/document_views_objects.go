package main

import (
	"context"
	"errors"
	"io"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// documentViewSignatureMaxBytes bounds the stored signature read of the
// subscription page (like subscriptionImportMaxBytes for an import upload).
const documentViewSignatureMaxBytes = 64 << 20

// documentViewObjects is default_storage.open(name, 'rb').read(): the
// django-storages name is resolved under AWS_LOCATION into the S3 key.
type documentViewObjects struct{ *documentobjects.S3 }

func (o documentViewObjects) Read(ctx context.Context, name string) ([]byte, error) {
	key, err := documentkey.S3Key(name, o.S3.Location())
	if err != nil {
		return nil, err
	}
	object, err := o.S3.Open(ctx, key)
	if err != nil {
		return nil, err
	}
	defer object.Body.Close()
	content, err := io.ReadAll(io.LimitReader(object.Body, documentViewSignatureMaxBytes+1))
	if err != nil {
		return nil, err
	}
	if len(content) > documentViewSignatureMaxBytes {
		return nil, errors.New("document view: stored signature too large")
	}
	return content, nil
}
