package dto

import (
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PythonContainsKey is `key in data` for a parsed request body (dict keys,
// list members, substrings); None, numbers and booleans raise TypeError.
func PythonContainsKey(data any, key string) (bool, error) { return pythonContains(data, key) }

// TextStorable reports whether every string (and dict key) in a parsed value
// is free of NUL characters and lone surrogates; numbers are not checked.
func TextStorable(value any) bool {
	text := func(s string) bool { return !strings.ContainsRune(s, 0) && !associateDraftSurrogate(s) }
	var walk func(any) bool
	walk = func(value any) bool {
		switch v := value.(type) {
		case string:
			return text(v)
		case domain.RequestObject:
			for _, field := range v {
				if !text(field.Name) || !walk(field.Value) {
					return false
				}
			}
		case []any:
			for _, item := range v {
				if !walk(item) {
					return false
				}
			}
		}
		return true
	}
	return walk(value)
}
