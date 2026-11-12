package domain

import (
	"context"
	"errors"
)

var (
	ErrObjectDeleteInput        = errors.New("invalid object deletion batch")
	ErrObjectStoreConfiguration = errors.New("invalid object store configuration")
	ErrObjectStoreResponse      = errors.New("invalid object store response; deletion outcome unknown")
	ErrObjectStoreDenied        = errors.New("object store access denied")
	ErrObjectStoreMissingBucket = errors.New("object store bucket missing")
	ErrObjectStoreUnavailable   = errors.New("object store unavailable; deletion outcome unknown")
	ErrObjectStoreRejected      = errors.New("object store rejected request")
)

// Key is the resolved provider key, including any configured location prefix.
// An empty VersionID deletes the current object using ordinary S3 semantics.
type ObjectVersion struct{ Key, VersionID string }
type ObjectDeleteResult struct {
	Object  ObjectVersion
	Deleted bool
	Code    string
	// Nil preserves an omitted provider field, distinct from explicit false.
	DeleteMarker *bool
	// Provider marker-version metadata. Together with DeleteMarker and the
	// requested VersionID, this distinguishes creation from removal of a marker.
	DeleteMarkerID string
}

// ObjectDeleter is infrastructure, not authorization. Services must establish
// durable intent, reference/upload exclusion and restore fencing before calling.
type ObjectDeleter interface {
	DeleteBatch(context.Context, []ObjectVersion) ([]ObjectDeleteResult, error)
}
