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
}
