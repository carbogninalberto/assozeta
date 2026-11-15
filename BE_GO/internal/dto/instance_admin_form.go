package dto

import (
	"sort"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Form (QueryDict) input for the instance administration serializers. Field
// and ListField.get_value read an HTML form differently from a JSON object:
// an absent BooleanField is false, a blank value of a field that is not
// required is skipped, and a repeated key keeps its last value (a ListField
// keeps every value). The normalised object feeds the JSON validators above;
// the raw object stays available for set(request.data) and data.get().

// InstanceAdminFormKind is the serializer field class that decides how a form
// value (a string or an uploaded file) is read.
type InstanceAdminFormKind int

const (
	InstanceAdminFormText InstanceAdminFormKind = iota
	InstanceAdminFormInteger
	InstanceAdminFormBoolean
	InstanceAdminFormChoice
	InstanceAdminFormUUID
	InstanceAdminFormList
	InstanceAdminFormNested
)

// InstanceAdminFormField describes one serializer field for form input:
// Required is DRF's field.required (required=True without a default),
// AllowBlank and AllowNull the field's flags, Default the internal value of a
// declared default.
type InstanceAdminFormField struct {
	Name       string
	Kind       InstanceAdminFormKind
	Required   bool
	AllowBlank bool
	AllowNull  bool
	Default    any
}

// InstanceAdminFormInput is a form body after Field.get_value: Value holds the
// object the serializers validate, Raw the form's keys and last values.
type InstanceAdminFormInput struct {
	Raw   domain.RequestObject
	Value domain.RequestObject
}

// InstanceAdminHTMLForm is Serializer(data=form).is_valid() input: every
// declared field reads Field.get_value for an HTML form. The body is returned
// unchanged when it is not a form object.
func InstanceAdminHTMLForm(body any, fields []InstanceAdminFormField, partial bool) (any, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return body, nil
	}
	value := domain.RequestObject{}
	for _, field := range fields {
		if field.Kind == InstanceAdminFormNested {
			nested := instanceAdminHTMLDict(object, field.Name)
			if len(nested) > 0 {
				value = append(value, domain.RequestField{Name: field.Name, Value: nested})
			}
			continue
		}
		if field.Kind == InstanceAdminFormList {
			items, present, err := instanceAdminHTMLList(object, field.Name)
			if err != nil {
				return nil, err
			}
			switch {
			case present:
				value = append(value, domain.RequestField{Name: field.Name, Value: items})
			case !partial && field.Default != nil:
				value = append(value, domain.RequestField{Name: field.Name, Value: field.Default})
			}
			continue
		}
		raw, present := object.Lookup(field.Name)
		normalised, set := instanceAdminFormValue(field, raw, present, partial)
		if set {
			value = append(value, domain.RequestField{Name: field.Name, Value: normalised})
		}
	}
	return InstanceAdminFormInput{Raw: object, Value: value}, nil
}

// instanceAdminFormValue is Field.get_value for an HTML form followed by the
// value's interpretation: a missing BooleanField is false, a blank value of a
// field that is not required is skipped (or keeps the field's default), and an
// uploaded file reads as its name where the field calls str() on it.
func instanceAdminFormValue(field InstanceAdminFormField, raw any, present, partial bool) (any, bool) {
	if !present {
		if partial {
			return nil, false
		}
		if field.Kind == InstanceAdminFormBoolean {
			if field.Default != nil {
				return field.Default, true
			}
			return false, true
		}
		if field.Default != nil {
			return field.Default, true
		}
		return nil, false
	}
	if raw == "" {
		switch {
		case field.AllowNull:
			if field.AllowBlank {
				return "", true
			}
			return nil, true
		case !field.Required:
			if field.AllowBlank {
				return "", true
			}
			if field.Default != nil {
				return field.Default, true
			}
			return nil, false
		}
		return "", true
	}
	if upload, ok := raw.(domain.RequestUpload); ok {
		if field.Kind == InstanceAdminFormInteger || field.Kind == InstanceAdminFormChoice {
			return upload.Name, true // str(file)
		}
	}
	return raw, true
}

// instanceAdminHTMLDict is html.parse_html_dict: the values of the keys
// '<prefix>.<x>', in their first-seen order. A plain '<prefix>' key matches
// nothing, so the nested field is skipped.
func instanceAdminHTMLDict(object domain.RequestObject, prefix string) domain.RequestObject {
	result := domain.RequestObject{}
	for _, field := range object {
		if !strings.HasPrefix(field.Name, prefix+".") {
			continue
		}
		key := field.Name[len(prefix)+1:]
		if key == "" {
			continue
		}
		result = append(result, domain.RequestField{Name: key, Value: field.Value, All: field.All})
	}
	return result
}

// instanceAdminHTMLListPair is one key of a nested list item, in the order the
// body sent it (a replaced key keeps its position).
type instanceAdminHTMLListPair struct {
	key   string
	value any
}

// instanceAdminHTMLList is ListField.get_value for a form: every value of the
// plain key (getlist), else the keys '<name>[<digits>]<suffix>' ordered by
// their integer index, else the field's default (an empty list). present is
// false only when neither shape matched.
func instanceAdminHTMLList(object domain.RequestObject, name string) (items []any, present bool, err error) {
	if values, ok := object.List(name); ok {
		items = make([]any, len(values))
		for index, value := range values {
			items[index] = instanceAdminHTMLListItem(value)
		}
		return items, true, nil
	}
	type entry struct {
		value any
		// pairs is html.parse_html_list's MultiValueDict: nil for the plain
		// shape, at least one key once a suffix built one.
		pairs []instanceAdminHTMLListPair
	}
	indexed := map[string]entry{}
	for _, field := range object {
		rest, ok := strings.CutPrefix(field.Name, name+"[")
		if !ok {
			continue
		}
		closing := strings.IndexByte(rest, ']')
		if closing < 0 {
			continue
		}
		digits := rest[:closing]
		if digits == "" || strings.Trim(digits, "0123456789") != "" {
			continue
		}
		suffix, ok := instanceAdminHTMLListSuffix(rest[closing+1:])
		if !ok {
			continue
		}
		index := strings.TrimLeft(digits, "0")
		if index == "" {
			index = "0"
		}
		if suffix == "" {
			// A plain value with this index replaces the nested dictionary.
			indexed[index] = entry{value: instanceAdminHTMLListItem(field.Value)}
			continue
		}
		current := indexed[index]
		if current.pairs == nil {
			// A nested key on a plain value starts a new dictionary.
			current = entry{}
		}
		current.pairs = instanceAdminHTMLListSet(current.pairs, suffix, field.Value)
		indexed[index] = current
	}
	if len(indexed) == 0 {
		return []any{}, false, nil
	}
	indexes := make([]string, 0, len(indexed))
	for index := range indexed {
		indexes = append(indexes, index)
	}
	sort.Slice(indexes, func(left, right int) bool {
		if len(indexes[left]) != len(indexes[right]) {
			return len(indexes[left]) < len(indexes[right])
		}
		return indexes[left] < indexes[right]
	})
	items = make([]any, 0, len(indexes))
	for _, text := range indexes {
		entry := indexed[text]
		if entry.pairs != nil {
			repr, ok := instanceAdminHTMLListRepr(entry.pairs)
			if !ok {
				return nil, false, domain.ErrInstanceAdminRefused
			}
			items = append(items, repr)
			continue
		}
		items = append(items, entry.value)
	}
	return items, true, nil
}

// instanceAdminHTMLListSuffix is the '(.*)$' group of DRF's key regex: '.' does
// not cross a newline and '$' only also matches before a single final one, so
// a suffix holding any other newline makes the key match nothing.
func instanceAdminHTMLListSuffix(suffix string) (string, bool) {
	if at := strings.IndexByte(suffix, '\n'); at >= 0 {
		if at != len(suffix)-1 {
			return "", false
		}
		return suffix[:at], true
	}
	return suffix, true
}

// instanceAdminHTMLListSet is MultiValueDict.__setitem__: it adds the key or
// replaces its value, keeping the position of a replaced key.
func instanceAdminHTMLListSet(pairs []instanceAdminHTMLListPair, key string, value any) []instanceAdminHTMLListPair {
	for at := range pairs {
		if pairs[at].key == key {
			pairs[at].value = value
			return pairs
		}
	}
	return append(pairs, instanceAdminHTMLListPair{key: key, value: value})
}

// instanceAdminHTMLListRepr is str() of the MultiValueDict html.parse_html_list
// builds for a nested item, which the ChoiceField child then quotes in its
// "'%(input)s' is not a valid choice." message. ok is false for a part whose
// repr carries its content type (a multipart upload), metadata the form parser
// does not keep: only such a nested item is left to the Django backend.
func instanceAdminHTMLListRepr(pairs []instanceAdminHTMLListPair) (repr string, ok bool) {
	var out strings.Builder
	out.WriteString("<MultiValueDict: {")
	for index, pair := range pairs {
		value, isText := pair.value.(string)
		if !isText {
			return "", false
		}
		if index > 0 {
			out.WriteString(", ")
		}
		out.WriteString(PythonStringRepr(pair.key))
		out.WriteString(": [")
		out.WriteString(PythonStringRepr(value))
		out.WriteString("]")
	}
	out.WriteString("}>")
	return out.String(), true
}

// instanceAdminHTMLListItem reads one list value: the child is always a
// ChoiceField here, whose to_internal_value calls str() on the value.
func instanceAdminHTMLListItem(value any) any {
	if upload, ok := value.(domain.RequestUpload); ok {
		return upload.Name
	}
	return value
}

// The serializer field sets of the instance administration views.

var instanceAdminBrandingFormFields = []InstanceAdminFormField{
	{Name: "oem", Kind: InstanceAdminFormNested},
	{Name: "uiApiVersion", Kind: InstanceAdminFormChoice},
}

var instanceAdminEmailFormFields = []InstanceAdminFormField{
	{Name: "revision", Kind: InstanceAdminFormInteger, Required: true},
	{Name: "host", Kind: InstanceAdminFormText, Required: true},
	{Name: "port", Kind: InstanceAdminFormInteger, Required: true},
	{Name: "security", Kind: InstanceAdminFormChoice, Required: true},
	{Name: "username", Kind: InstanceAdminFormText, Required: true, AllowBlank: true},
	{Name: "password", Kind: InstanceAdminFormText},
	{Name: "clear_password", Kind: InstanceAdminFormBoolean, Default: false},
	{Name: "from_email", Kind: InstanceAdminFormText, Required: true},
	{Name: "sender_name", Kind: InstanceAdminFormText, Required: true, AllowBlank: true},
}

var instanceAdminIntegrationFormFields = []InstanceAdminFormField{
	{Name: "revision", Kind: InstanceAdminFormInteger, Required: true},
	{Name: "enabled", Kind: InstanceAdminFormBoolean, Required: true},
}

var instanceAdminStripeFormFields = append(append([]InstanceAdminFormField(nil), instanceAdminIntegrationFormFields...),
	InstanceAdminFormField{Name: "public_key", Kind: InstanceAdminFormText, Required: true, AllowBlank: true},
	InstanceAdminFormField{Name: "secret_key", Kind: InstanceAdminFormText, AllowBlank: true},
	InstanceAdminFormField{Name: "webhook_secret", Kind: InstanceAdminFormText, AllowBlank: true},
	InstanceAdminFormField{Name: "clear_secrets", Kind: InstanceAdminFormList, Default: []any{}},
)

var instanceAdminAIFormFields = append(append([]InstanceAdminFormField(nil), instanceAdminIntegrationFormFields...),
	InstanceAdminFormField{Name: "api_key", Kind: InstanceAdminFormText, AllowBlank: true},
	InstanceAdminFormField{Name: "model", Kind: InstanceAdminFormText, Required: true},
	InstanceAdminFormField{Name: "cheap_model", Kind: InstanceAdminFormText, Required: true},
	InstanceAdminFormField{Name: "base_url", Kind: InstanceAdminFormText, Required: true, AllowBlank: true},
	InstanceAdminFormField{Name: "max_iterations", Kind: InstanceAdminFormInteger, Required: true},
	InstanceAdminFormField{Name: "max_results", Kind: InstanceAdminFormInteger, Required: true},
	InstanceAdminFormField{Name: "query_timeout", Kind: InstanceAdminFormInteger, Required: true},
	InstanceAdminFormField{Name: "ws_rate_limit", Kind: InstanceAdminFormInteger, Required: true},
	InstanceAdminFormField{Name: "ws_timeout", Kind: InstanceAdminFormInteger, Required: true},
	InstanceAdminFormField{Name: "history_cap", Kind: InstanceAdminFormInteger, Required: true},
	InstanceAdminFormField{Name: "clear_secrets", Kind: InstanceAdminFormList, Default: []any{}},
)

var instanceAdminLoginFormFields = append(append([]InstanceAdminFormField(nil), instanceAdminIntegrationFormFields...),
	InstanceAdminFormField{Name: "client_id", Kind: InstanceAdminFormText, Required: true, AllowBlank: true},
)

var instanceAdminEmailTestFormFields = []InstanceAdminFormField{
	{Name: "revision", Kind: InstanceAdminFormInteger, Required: true},
	{Name: "action", Kind: InstanceAdminFormChoice, Required: true},
	{Name: "recipient", Kind: InstanceAdminFormText},
}

var instanceAdminUpdateFormFields = []InstanceAdminFormField{
	{Name: "release_id", Kind: InstanceAdminFormInteger, Required: true},
	{Name: "tag", Kind: InstanceAdminFormText, Required: true},
	{Name: "request_id", Kind: InstanceAdminFormUUID, Required: true},
}

var instanceAdminRestartFormFields = []InstanceAdminFormField{
	{Name: "request_id", Kind: InstanceAdminFormUUID, Required: true},
}

// InstanceAdminBrandingForm is InstanceAdminSerializer(config, data=form,
// partial=True).
func InstanceAdminBrandingForm(body any) (any, error) {
	return InstanceAdminHTMLForm(body, instanceAdminBrandingFormFields, true)
}

// InstanceAdminEmailForm is EmailSettingsSerializer(data=form).
func InstanceAdminEmailForm(body any) (any, error) {
	return InstanceAdminHTMLForm(body, instanceAdminEmailFormFields, false)
}

// InstanceAdminIntegrationForm is the provider serializer with a form body.
func InstanceAdminIntegrationForm(provider string, body any) (any, error) {
	fields := instanceAdminLoginFormFields
	switch provider {
	case "stripe":
		fields = instanceAdminStripeFormFields
	case "ai":
		fields = instanceAdminAIFormFields
	}
	return InstanceAdminHTMLForm(body, fields, false)
}

// InstanceAdminEmailTestForm is EmailTestSerializer(data=form).
func InstanceAdminEmailTestForm(body any) (any, error) {
	return InstanceAdminHTMLForm(body, instanceAdminEmailTestFormFields, false)
}

// InstanceAdminUpdateForm is UpdateRequestSerializer(data=form).
func InstanceAdminUpdateForm(body any) (any, error) {
	return InstanceAdminHTMLForm(body, instanceAdminUpdateFormFields, false)
}

// InstanceAdminRestartForm is RestartRequestSerializer(data=form).
func InstanceAdminRestartForm(body any) (any, error) {
	return InstanceAdminHTMLForm(body, instanceAdminRestartFormFields, false)
}

// InstanceAdminRevisionForm is the revision-only read of the two DELETE views
// and the integration test: request.data.get('revision').
func InstanceAdminRevisionForm(body any) (any, error) {
	return InstanceAdminHTMLForm(body, nil, false)
}

// InstanceAdminFormGet is request.data.get(name) for a form body: the last
// value of the key (a file reads as its name where the field calls str()).
func InstanceAdminFormGet(body any, name string) (any, bool) {
	if form, ok := body.(InstanceAdminFormInput); ok {
		return form.Raw.Lookup(name)
	}
	if object, ok := body.(domain.RequestObject); ok {
		return object.Lookup(name)
	}
	return nil, false
}
