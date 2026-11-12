package domain

import "errors"

var ErrInvalidScopedRequest = errors.New("invalid scoped request metadata or value")

// RequestObject preserves parsed field order: Django reports the first failed
// reference, including when several fields would fail. Maps cannot preserve it.
// Values are nil, bool, string, json.Number, RequestObject or []any.
type RequestField struct {
	Name  string
	Value any
	// All holds every value of a form key that was sent more than once, in the order
	// QueryDict.getlist returns them (text parts first, then files); nil otherwise.
	// Value is the last one, as QueryDict.get returns it.
	All []any
}
type RequestObject []RequestField

// HasUpload reports a file part: only then does DRF hand the view a mutable copy of the form.
func (o RequestObject) HasUpload() bool {
	for _, field := range o {
		if _, upload := field.Value.(RequestUpload); upload {
			return true
		}
	}
	return false
}

// List is QueryDict.getlist(name) of a form body: every value sent for the key.
func (o RequestObject) List(name string) ([]any, bool) {
	for _, field := range o {
		if field.Name == name {
			if len(field.All) > 1 {
				return field.All, true
			}
			return []any{field.Value}, true
		}
	}
	return nil, false
}

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
