package domain

import "errors"

var ErrInvalidScopedRequest = errors.New("invalid scoped request metadata or value")

// RequestObject preserves parsed field order: Django reports the first failed
// reference, including when several fields would fail. Maps cannot preserve it.
// Values are nil, bool, string, json.Number, RequestObject or []any.
type RequestField struct {
	Name  string
	Value any
}
type RequestObject []RequestField

// RequestUpload retains file metadata while its content stays opaque. The login
// OTP view uses Python len(file) and str(file), which mean size and filename.
// Other credential/reference consumers must not treat a file as textual input.
type RequestUpload struct {
	Name string
	Size int64
	// Content is the whole file when it fits one 64 KiB chunk (Whole), else empty.
	Content string
	Whole   bool
	// Temp is a request over FILE_UPLOAD_MAX_MEMORY_SIZE: Django spools the file (TemporaryUploadedFile).
	Temp bool
}

// RequestUploadHead is the size of the first chunk Django's File.chunks reads.
const RequestUploadHead = 65536

// RequestUploadMemory is FILE_UPLOAD_MAX_MEMORY_SIZE, tested against the request Content-Length.
const RequestUploadMemory = 2621440

func (o RequestObject) Lookup(name string) (any, bool) {
	for _, field := range o {
		if field.Name == name {
			return field.Value, true
		}
	}
	return nil, false
}

// Operation and Path names must come from server route registration, never from
// a client-supplied operation header. Query contains last values for repeated
// parameters, in first-key order; Body is already transport-decoded.
type RequestReferences struct {
	Operation   string
	Path, Query RequestObject
	Body        any
	// Deferred transport parse failure: the operation/path/query gates precede
	// access to request.data in Django. Parsing must not change denial ordering.
	BodyError error
}
