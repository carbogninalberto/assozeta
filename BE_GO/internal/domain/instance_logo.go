package domain

import (
	"context"
	"errors"
	"io"
)

var ErrInstanceLogoMissing = errors.New("instance logo not found")

// ErrInstanceLogoPath matches the storage backend's unsafe normalized key error.
var ErrInstanceLogoPath = errors.New("unsafe instance logo storage path")

// InstanceLogoFile owns a prepared, seekable-storage read. Close releases every
// storage resource, including an S3 download spool and its admission slot.
type InstanceLogoFile struct {
	Body io.ReadCloser
	Size int64
}

type InstanceLogoStorage interface {
	Exists(context.Context, string) (bool, error)
	Open(context.Context, string) (InstanceLogoFile, error)
}

type InstanceLogo struct {
	InstanceLogoFile
	ContentType string
	Filename    string
}
