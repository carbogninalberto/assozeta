package dto

import (
	"encoding/json"
	"math"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type ProfileTablesRequest struct {
	Present bool
	Value   any
}

type ProfileTablesValidationError struct{ Code, InputType string }

func (e *ProfileTablesValidationError) Error() string { return "invalid table preferences: " + e.Code }

// The model serializer is partial: absent/unknown fields do not overwrite data.
// HTML form values are JSON documents; ordinary JSON strings are literal values.
func ParseProfileTables(body any, form bool) (ProfileTablesRequest, error) {
	empty := ProfileTablesRequest{}
	if body == nil {
		return empty, &ProfileTablesValidationError{Code: "null-body"}
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		kind := "str"
		switch x := body.(type) {
		case []any:
			kind = "list"
		case bool:
			kind = "bool"
		case json.Number:
			kind = "int"
			if strings.ContainsAny(string(x), ".eE") {
				kind = "float"
			}
		}
		return empty, &ProfileTablesValidationError{Code: "object", InputType: kind}
	}
	value, present := object.Lookup("tables_settings")
	if !present {
		return empty, nil
	}
	if value == nil {
		return empty, &ProfileTablesValidationError{Code: "null"}
	}
	if form {
		text, ok := value.(string)
		if upload, isUpload := value.(domain.RequestUpload); isUpload {
			text = upload.Name
			ok = true
		}
		if !ok {
			return empty, &ProfileTablesValidationError{Code: "json"}
		}
		var err error
		value, err = ParseProfileTablesJSON([]byte(text))
		if err != nil {
			return empty, &ProfileTablesValidationError{Code: "json"}
		}
		// JSONField decodes form text after the allow_null check. A decoded null
		// therefore reaches the model's SQL NULL constraint, unlike direct JSON null.
	} else if !tableJSONSerializable(value) {
		return empty, &ProfileTablesValidationError{Code: "json"}
	}
	return ProfileTablesRequest{Present: true, Value: value}, nil
}

func tableJSONSerializable(value any) bool {
	switch v := value.(type) {
	case nil, bool, string:
		return true
	case json.Number:
		if !json.Valid([]byte(v)) {
			return false
		}
		if strings.ContainsAny(string(v), ".eE") {
			f, _ := strconv.ParseFloat(string(v), 64)
			return !math.IsNaN(f) && !math.IsInf(f, 0)
		}
		return true
	case domain.RequestObject:
		for _, field := range v {
			if !tableJSONSerializable(field.Value) {
				return false
			}
		}
		return true
	case []any:
		for _, item := range v {
			if !tableJSONSerializable(item) {
				return false
			}
		}
		return true
	}
	return false
}
