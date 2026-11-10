package dto

import (
	"encoding/json"
	"errors"
	"math/big"
	"net/netip"
	"regexp"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Serializer ports for BE/instance (administration.py, operations.py,
// integrations.py). Errors are DRF error structures of render codes (the
// handler's message table): field -> []code, nested field -> map, a list
// index -> map keyed by the index string. Literal messages raised by the
// views' own validators are their own codes.

// InstanceAdminInvalid is a 400 validation error body before rendering.
type InstanceAdminInvalid struct{ Body any }

func (e *InstanceAdminInvalid) Error() string { return "instance administration validation failed" }

// Render codes used only by this family's message table.
const (
	InstanceAdminRegex = "instance-admin-regex"
	InstanceAdminURL   = "instance-admin-url"
)

// ErrInstanceAdminUnhashable is set(request.data) on a list holding a list or
// a dictionary, or on a non-iterable body: a TypeError (server error page).
var ErrInstanceAdminUnhashable = errors.New("unhashable request data")

// InstanceAdminKeysEqual is `set(request.data) == {key}`.
func InstanceAdminKeysEqual(body any, key string) (bool, error) {
	switch v := body.(type) {
	case domain.RequestObject:
		seen := false
		for _, field := range v {
			if field.Name != key {
				return false, nil
			}
			seen = true
		}
		return seen, nil
	case []any:
		equal := len(v) > 0
		for _, item := range v {
			switch item.(type) {
			case domain.RequestObject, []any:
				return false, ErrInstanceAdminUnhashable
			}
			if text, ok := item.(string); !ok || text != key {
				equal = false
			}
		}
		return equal, nil
	case string:
		// A set of characters never equals a set holding one longer key.
		return len([]rune(key)) == 1 && v != "" && strings.Trim(v, key) == "", nil
	}
	return false, ErrInstanceAdminUnhashable
}

// InstanceAdminKeysWithin is `keys = set(request.data); keys and keys <=
// allowed`. A list holding a list or a dictionary, or a non-iterable body, is
// the TypeError of set() (ErrInstanceAdminUnhashable).
func InstanceAdminKeysWithin(body any, allowed ...string) (bool, error) {
	member := func(text string) bool {
		for _, key := range allowed {
			if text == key {
				return true
			}
		}
		return false
	}
	switch v := body.(type) {
	case domain.RequestObject:
		for _, field := range v {
			if !member(field.Name) {
				return false, nil
			}
		}
		return len(v) > 0, nil
	case []any:
		within := len(v) > 0
		for _, item := range v {
			switch item.(type) {
			case domain.RequestObject, []any:
				return false, ErrInstanceAdminUnhashable
			}
			if text, ok := item.(string); !ok || !member(text) {
				within = false
			}
		}
		return within, nil
	case string:
		// A set of characters: within only when every character is a key.
		if v == "" {
			return false, nil
		}
		for _, r := range v {
			if !member(string(r)) {
				return false, nil
			}
		}
		return true, nil
	}
	return false, ErrInstanceAdminUnhashable
}

type drfText struct {
	max        int
	allowBlank bool
	allowNull  bool
	keepSpaces bool
	invalid    string
	validators []func(string) []string
}

// run is CharField.run_validation for a present value.
func (f drfText) run(value any) (string, []string) {
	if value == nil {
		if f.allowNull {
			return "", nil
		}
		return "", []string{PaymentCategoryValidationNull}
	}
	text, isString := value.(string)
	if (isString && text == "") || (!f.keepSpaces && pythonStrip(PythonStr(value)) == "") {
		if !f.allowBlank {
			return "", []string{PaymentCategoryValidationBlank}
		}
		return "", nil
	}
	switch value.(type) {
	case string, json.Number:
	default:
		invalid := f.invalid
		if invalid == "" {
			invalid = PaymentCategoryValidationString
		}
		return "", []string{invalid}
	}
	text = PythonStr(value)
	if !f.keepSpaces {
		text = pythonStrip(text)
	}
	var codes []string
	if f.max > 0 && len([]rune(text)) > f.max {
		codes = append(codes, "max:"+strconv.Itoa(f.max))
	}
	if strings.ContainsRune(text, 0) {
		codes = append(codes, "null-character")
	}
	for _, validator := range f.validators {
		codes = append(codes, validator(text)...)
	}
	return text, codes
}

// drfIntegerField is IntegerField(min_value, max_value).run_validation.
func drfIntegerField(value any, minimum, maximum *int64) (*big.Int, []string) {
	if value == nil {
		return nil, []string{PaymentCategoryValidationNull}
	}
	number, code := DRFInteger(value)
	if code != "" {
		return nil, []string{code}
	}
	var codes []string
	if maximum != nil && number.Cmp(big.NewInt(*maximum)) > 0 {
		codes = append(codes, "max-value:"+strconv.FormatInt(*maximum, 10))
	}
	if minimum != nil && number.Cmp(big.NewInt(*minimum)) < 0 {
		codes = append(codes, "min-value:"+strconv.FormatInt(*minimum, 10))
	}
	return number, codes
}

// InstanceAdminRevision is serializers.IntegerField(min_value=0).run_validation(value).
func InstanceAdminRevision(value any) (*big.Int, error) {
	zero := int64(0)
	number, codes := drfIntegerField(value, &zero, nil)
	if codes != nil {
		return nil, &InstanceAdminInvalid{Body: codes}
	}
	return number, nil
}

func drfBooleanField(value any) (bool, []string) {
	if value == nil {
		return false, []string{PaymentCategoryValidationNull}
	}
	result, ok := DRFBoolean(value)
	if !ok {
		return false, []string{PaymentCategoryValidationBoolean}
	}
	return result, nil
}

func drfChoiceField(value any, choices ...string) (string, []string) {
	if value == nil {
		return "", []string{PaymentCategoryValidationNull}
	}
	text := PythonStr(value)
	for _, choice := range choices {
		if text == choice {
			return choice, nil
		}
	}
	return "", []string{"choice:" + text}
}

// drfChoiceList is ListField(child=ChoiceField(choices)).run_validation.
func drfChoiceList(value any, choices ...string) ([]string, any) {
	if value == nil {
		return nil, []string{PaymentCategoryValidationNull}
	}
	items, ok := value.([]any)
	if !ok {
		return nil, []string{"not-a-list:" + pythonTypeName(value)}
	}
	result, errs := []string{}, map[string]any{}
	for index, item := range items {
		choice, codes := drfChoiceField(item, choices...)
		if codes != nil {
			errs[strconv.Itoa(index)] = codes
		}
		result = append(result, choice)
	}
	if len(errs) > 0 {
		return nil, errs
	}
	return result, nil
}

// drfUUIDField is UUIDField.to_internal_value: ints (bool included) are
// uuid.UUID(int=...), strings uuid.UUID(hex=...).
func drfUUIDField(value any) (string, []string) {
	if value == nil {
		return "", []string{PaymentCategoryValidationNull}
	}
	if flag, ok := value.(bool); ok {
		value = json.Number(map[bool]string{true: "1", false: "0"}[flag])
	}
	if number, ok := value.(json.Number); ok && strings.ContainsAny(string(number), ".eE") {
		return "", []string{PaymentCategoryValidationUUID}
	}
	if id, ok := supplierUUID(value); ok {
		return id, nil
	}
	return "", []string{PaymentCategoryValidationUUID}
}

func instanceAdminObject(body any) (domain.RequestObject, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, &InstanceAdminInvalid{Body: map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(body)}}}
	}
	return object, nil
}

var instanceAdminColor = regexp.MustCompile(`^#[0-9a-fA-F]{6}$`)

// InstanceAdminUIAPIVersions are InstanceConfiguration.UI_API_VERSION_CHOICES.
var InstanceAdminUIAPIVersions = []string{"v1", "v2"}

// InstanceAdminEdit is InstanceAdminSerializer(config, data, partial=True)
// (InstanceReconfigureSerializer restricted to 'oem' and 'uiApiVersion'):
// the edited model columns. Field errors of both keys are reported together.
func InstanceAdminEdit(body any) (map[string]string, error) {
	object, err := instanceAdminObject(body)
	if err != nil {
		return nil, err
	}
	values, errs := map[string]string{}, map[string]any{}
	if oem, present := object.Lookup("oem"); present {
		columns, invalid := instanceAdminOEM(oem)
		if invalid != nil {
			errs["oem"] = invalid
		}
		for column, value := range columns {
			values[column] = value
		}
	}
	if version, present := object.Lookup("uiApiVersion"); present {
		// ChoiceField: null is not allowed; otherwise str(data) must be a choice.
		switch {
		case version == nil:
			errs["uiApiVersion"] = []string{PaymentCategoryValidationNull}
		default:
			text := PythonStr(version)
			valid := false
			for _, choice := range InstanceAdminUIAPIVersions {
				valid = valid || text == choice
			}
			if valid {
				values["ui_api_version"] = text
			} else {
				errs["uiApiVersion"] = []string{"choice:" + text}
			}
		}
	}
	if len(errs) > 0 {
		return nil, &InstanceAdminInvalid{Body: errs}
	}
	return values, nil
}

// instanceAdminOEM is the nested OEMInputSerializer (partial): the edited
// columns, or its error structure.
func instanceAdminOEM(oem any) (map[string]string, any) {
	if oem == nil {
		return nil, []string{PaymentCategoryValidationNull}
	}
	nested, ok := oem.(domain.RequestObject)
	if !ok {
		return nil, map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(oem)}}
	}
	email := func(text string) []string {
		if !SupplierEmail(text) {
			return []string{SupplierValidationEmail}
		}
		return nil
	}
	color := func(text string) []string {
		if !instanceAdminColor.MatchString(text) {
			return []string{InstanceAdminRegex}
		}
		return nil
	}
	fields := []struct {
		name, column string
		field        drfText
	}{
		{"name", "name", drfText{max: 255}},
		{"abbreviation", "abbreviation", drfText{max: 50, allowBlank: true}},
		{"primaryColor", "primary_color", drfText{invalid: InstanceAdminRegex, validators: []func(string) []string{color}}},
		{"supportEmail", "support_email", drfText{allowBlank: true, invalid: SupplierValidationEmail, validators: []func(string) []string{email}}},
	}
	values, errs := map[string]string{}, map[string]any{}
	for _, spec := range fields {
		value, present := nested.Lookup(spec.name)
		if !present {
			continue // partial: neither required nor defaulted
		}
		text, codes := spec.field.run(value)
		if codes != nil {
			errs[spec.name] = codes
			continue
		}
		values[spec.column] = text
	}
	if len(errs) > 0 {
		return nil, errs
	}
	return values, nil
}

// InstanceAdminEmailInput is EmailSettingsSerializer.validated_data.
type InstanceAdminEmailInput struct {
	Revision                                        *big.Int
	Host, Security, Username, FromEmail, SenderName string
	Port                                            int64
	Password                                        *string
	ClearPassword                                   bool
}

var instanceAdminHostLabel = regexp.MustCompile(`^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$`)

// InstanceAdminEmail is EmailSettingsSerializer(data=body).is_valid().
func InstanceAdminEmail(body any) (*InstanceAdminEmailInput, error) {
	object, err := instanceAdminObject(body)
	if err != nil {
		return nil, err
	}
	errs := map[string]any{}
	input := &InstanceAdminEmailInput{}
	zero, one, top := int64(0), int64(1), int64(65535)
	lookup := func(name string, required bool) (any, bool) {
		value, present := object.Lookup(name)
		if !present && required {
			errs[name] = []string{PaymentCategoryValidationRequired}
		}
		return value, present
	}
	if value, ok := lookup("revision", true); ok {
		number, codes := drfIntegerField(value, &zero, nil)
		if codes != nil {
			errs["revision"] = codes
		}
		input.Revision = number
	}
	text := func(name string, field drfText, target *string, extra func(string) []string) {
		value, ok := lookup(name, true)
		if !ok {
			return
		}
		result, codes := field.run(value)
		if codes == nil && extra != nil {
			codes = extra(result)
		}
		if codes != nil {
			errs[name] = codes
		}
		*target = result
	}
	text("host", drfText{max: 253}, &input.Host, func(host string) []string {
		if _, err := netip.ParseAddr(host); err == nil {
			return nil
		}
		for _, label := range strings.Split(strings.TrimRight(host, "."), ".") {
			if !instanceAdminHostLabel.MatchString(label) {
				return []string{"Inserisci un nome host o indirizzo IP senza protocollo o percorso."}
			}
		}
		return nil
	})
	if value, ok := lookup("port", true); ok {
		number, codes := drfIntegerField(value, &one, &top)
		if codes != nil {
			errs["port"] = codes
		} else {
			input.Port = number.Int64()
		}
	}
	if value, ok := lookup("security", true); ok {
		choice, codes := drfChoiceField(value, "ssl", "tls", "none")
		if codes != nil {
			errs["security"] = codes
		}
		input.Security = choice
	}
	text("username", drfText{max: 254, allowBlank: true}, &input.Username, nil)
	if value, ok := object.Lookup("password"); ok {
		password, codes := drfText{max: 4096, keepSpaces: true}.run(value)
		if codes != nil {
			errs["password"] = codes
		}
		input.Password = &password
	}
	if value, ok := object.Lookup("clear_password"); ok {
		clear, codes := drfBooleanField(value)
		if codes != nil {
			errs["clear_password"] = codes
		}
		input.ClearPassword = clear
	}
	email := func(text string) []string {
		if !SupplierEmail(text) {
			return []string{SupplierValidationEmail}
		}
		return nil
	}
	text("from_email", drfText{max: 254, invalid: SupplierValidationEmail, validators: []func(string) []string{email}}, &input.FromEmail, nil)
	text("sender_name", drfText{max: 100, allowBlank: true}, &input.SenderName, func(name string) []string {
		if strings.ContainsAny(name, "\r\n") {
			return []string{"Il nome mittente deve essere su una sola riga."}
		}
		return nil
	})
	if len(errs) > 0 {
		return nil, &InstanceAdminInvalid{Body: errs}
	}
	if input.Password != nil && *input.Password != "" && input.ClearPassword {
		return nil, &InstanceAdminInvalid{Body: map[string]any{"non_field_errors": []string{"Scegli se sostituire o rimuovere la password."}}}
	}
	if input.Security == "none" && (input.Username != "" || input.Password != nil && *input.Password != "") {
		return nil, &InstanceAdminInvalid{Body: map[string]any{"non_field_errors": []string{"Usa TLS o SSL per inviare credenziali SMTP."}}}
	}
	return input, nil
}

// InstanceAdminIntegrationInput is the validated_data of the provider's
// serializer: Values holds every validated field except revision and
// clear_secrets (strings, bools and int64s), Clear the clear_secrets list.
type InstanceAdminIntegrationInput struct {
	Revision *big.Int
	Values   map[string]any
	Clear    []string
}

var (
	instanceAdminClientID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)
	instanceAdminStripe   = map[string]*regexp.Regexp{
		"public_key":     regexp.MustCompile(`^pk_(test|live)_[A-Za-z0-9_]+$`),
		"secret_key":     regexp.MustCompile(`^(sk|rk)_(test|live)_[A-Za-z0-9_]+$`),
		"webhook_secret": regexp.MustCompile(`^whsec_[A-Za-z0-9_]+$`),
	}
)

// InstanceAdminSecretFields is integration_configuration.SECRET_FIELDS.
var InstanceAdminSecretFields = map[string][]string{"ai": {"api_key"}, "stripe": {"secret_key", "webhook_secret"}, "google": {}, "apple": {}}

// InstanceAdminIntegration is the provider serializer's is_valid():
// StripeSettingsSerializer, AISettingsSerializer or LoginSettingsSerializer.
// It returns ErrInstanceAdminRefused-wrapped errors for input it does not
// validate (a non-ASCII AI base URL).
func InstanceAdminIntegration(provider string, body any) (*InstanceAdminIntegrationInput, error) {
	object, err := instanceAdminObject(body)
	if err != nil {
		return nil, err
	}
	errs := map[string]any{}
	input := &InstanceAdminIntegrationInput{Values: map[string]any{}, Clear: []string{}}
	zero := int64(0)
	var refused error
	if value, ok := object.Lookup("revision"); !ok {
		errs["revision"] = []string{PaymentCategoryValidationRequired}
	} else if number, codes := drfIntegerField(value, &zero, nil); codes != nil {
		errs["revision"] = codes
	} else {
		input.Revision = number
	}
	if value, ok := object.Lookup("enabled"); !ok {
		errs["enabled"] = []string{PaymentCategoryValidationRequired}
	} else if flag, codes := drfBooleanField(value); codes != nil {
		errs["enabled"] = codes
	} else {
		input.Values["enabled"] = flag
	}
	text := func(name string, field drfText, required bool, extra func(string) []string) {
		value, ok := object.Lookup(name)
		if !ok {
			if required {
				errs[name] = []string{PaymentCategoryValidationRequired}
			}
			return
		}
		result, codes := field.run(value)
		if codes == nil && extra != nil {
			codes = extra(result)
		}
		if codes != nil {
			errs[name] = codes
			return
		}
		input.Values[name] = result
	}
	integer := func(name string, minimum, maximum int64) {
		value, ok := object.Lookup(name)
		if !ok {
			errs[name] = []string{PaymentCategoryValidationRequired}
			return
		}
		number, codes := drfIntegerField(value, &minimum, &maximum)
		if codes != nil {
			errs[name] = codes
			return
		}
		input.Values[name] = number.Int64()
	}
	clear := func(choices ...string) {
		value, ok := object.Lookup("clear_secrets")
		if !ok {
			return
		}
		list, problem := drfChoiceList(value, choices...)
		if problem != nil {
			errs["clear_secrets"] = problem
			return
		}
		input.Clear = list
	}
	switch provider {
	case "stripe":
		text("public_key", drfText{max: 300, allowBlank: true}, true, nil)
		text("secret_key", drfText{max: 500, allowBlank: true}, false, nil)
		text("webhook_secret", drfText{max: 500, allowBlank: true}, false, nil)
		clear("secret_key", "webhook_secret")
	case "ai":
		text("api_key", drfText{max: 2000, allowBlank: true}, false, nil)
		text("model", drfText{max: 255}, true, nil)
		text("cheap_model", drfText{max: 255}, true, nil)
		urlValidator := func(value string) []string {
			valid, ascii := DjangoURL(value)
			if !ascii {
				refused = domain.ErrInstanceAdminRefused
			}
			if !valid {
				return []string{InstanceAdminURL}
			}
			return nil
		}
		text("base_url", drfText{max: 2000, allowBlank: true, invalid: InstanceAdminURL, validators: []func(string) []string{urlValidator}}, true, func(value string) []string {
			if value != "" && !instanceAdminPlainHTTP(value) {
				return []string{"Usa un URL HTTP o HTTPS senza credenziali, query o frammenti."}
			}
			return nil
		})
		integer("max_iterations", 1, 100)
		integer("max_results", 1, 100000)
		integer("query_timeout", 1, 300)
		integer("ws_rate_limit", 1, 1000)
		integer("ws_timeout", 1, 3600)
		integer("history_cap", 2, 1000)
		clear("api_key")
	default:
		text("client_id", drfText{max: 255, allowBlank: true}, true, func(value string) []string {
			if value != "" && !instanceAdminClientID.MatchString(value) {
				return []string{"Inserisci un client ID senza spazi, URL o percorsi."}
			}
			return nil
		})
	}
	if refused != nil {
		return nil, refused
	}
	if len(errs) > 0 {
		return nil, &InstanceAdminInvalid{Body: errs}
	}
	if err := InstanceAdminIntegrationRules(provider, input.Values, input.Clear); err != nil {
		return nil, err
	}
	return input, nil
}

// InstanceAdminIntegrationRules is the serializer's validate(data).
func InstanceAdminIntegrationRules(provider string, values map[string]any, clear []string) error {
	switch provider {
	case "stripe":
		for _, key := range []string{"public_key", "secret_key", "webhook_secret"} {
			if text, _ := values[key].(string); text != "" && !instanceAdminStripe[key].MatchString(text) {
				return &InstanceAdminInvalid{Body: map[string]any{key: []string{"Formato della chiave non valido."}}}
			}
		}
		for _, key := range clear {
			if text, _ := values[key].(string); text != "" {
				return &InstanceAdminInvalid{Body: map[string]any{"non_field_errors": []string{"Scegli se sostituire o rimuovere ciascuna credenziale."}}}
			}
		}
	case "ai":
		if text, _ := values["api_key"].(string); text != "" {
			for _, key := range clear {
				if key == "api_key" {
					return &InstanceAdminInvalid{Body: map[string]any{"non_field_errors": []string{"Scegli se sostituire o rimuovere la chiave API."}}}
				}
			}
		}
	}
	return nil
}

// InstanceAdminStripeRevalidate is StripeSettingsSerializer(data={**value,
// 'revision': current}).is_valid(raise_exception=True) on the retained values.
func InstanceAdminStripeRevalidate(values map[string]any, revision *big.Int) error {
	object := domain.RequestObject{{Name: "revision", Value: json.Number(revision.String())}}
	for _, key := range []string{"enabled", "public_key", "secret_key", "webhook_secret"} {
		if value, ok := values[key]; ok {
			object = append(object, domain.RequestField{Name: key, Value: value})
		}
	}
	_, err := InstanceAdminIntegration("stripe", object)
	return err
}

// instanceAdminPlainHTTP is AISettingsSerializer.validate_base_url after
// URLValidator passed: urlsplit scheme http/https, no userinfo, query or
// fragment.
func instanceAdminPlainHTTP(value string) bool {
	scheme, rest, _ := strings.Cut(value, ":")
	scheme = strings.ToLower(scheme)
	if scheme != "http" && scheme != "https" {
		return false
	}
	netloc := ""
	if strings.HasPrefix(rest, "//") {
		rest = rest[2:]
		end := strings.IndexAny(rest, "/?#")
		if end < 0 {
			end = len(rest)
		}
		netloc, rest = rest[:end], rest[end:]
	}
	fragment := ""
	if at := strings.Index(rest, "#"); at >= 0 {
		rest, fragment = rest[:at], rest[at+1:]
	}
	query := ""
	if at := strings.Index(rest, "?"); at >= 0 {
		query = rest[at+1:]
	}
	if at := strings.LastIndex(netloc, "@"); at >= 0 {
		user, password, hasPassword := strings.Cut(netloc[:at], ":")
		if user != "" || hasPassword && password != "" {
			return false
		}
	}
	return query == "" && fragment == ""
}

// Django 5.2 URLValidator (schemes http, https, ftp, ftps) for ASCII values;
// the lookarounds of its domain and TLD patterns are expanded into
// equivalent label patterns.
const (
	urlIPv4  = `(?:0|25[0-5]|2[0-4][0-9]|1[0-9]?[0-9]?|[1-9][0-9]?)(?:\.(?:0|25[0-5]|2[0-4][0-9]|1[0-9]?[0-9]?|[1-9][0-9]?)){3}`
	urlLabel = `[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?`
	urlHost  = `(?:` + urlLabel + `(?:\.` + urlLabel + `)*\.(?:[a-z][a-z-]{0,61}[a-z]|xn--[a-z0-9]{1,59})\.?|localhost)`
	urlSpace = `\t\n\v\f\r \x1c-\x1f`
)

var (
	djangoURLPattern = regexp.MustCompile(`(?i)^[a-z0-9.+-]*://(?:[^` + urlSpace + `:@/]+(?::[^` + urlSpace + `:@/]*)?@)?(?:` +
		urlIPv4 + `|\[[0-9a-f:.]+\]|` + urlHost + `)(?::[0-9]{1,5})?(?:[/?#][^` + urlSpace + `]*)?$`)
	djangoURLBracket = regexp.MustCompile(`^\[(.+)\](?::[0-9]{1,5})?$`)
)

// DjangoURL reports URLValidator()(value) for ASCII text; ascii=false means
// the value needs the IDNA fallback, which is not ported.
func DjangoURL(value string) (valid, ascii bool) {
	for _, r := range value {
		if r > 127 {
			return false, false
		}
	}
	if len(value) > 2048 || strings.ContainsAny(value, "\t\r\n") {
		return false, true
	}
	scheme, _, _ := strings.Cut(value, "://")
	switch strings.ToLower(scheme) {
	case "http", "https", "ftp", "ftps":
	default:
		return false, true
	}
	if !djangoURLPattern.MatchString(value) {
		return false, true
	}
	// urlsplit: the network location follows the scheme's "//".
	rest := value[strings.Index(value, "//")+2:]
	if end := strings.IndexAny(rest, "/?#"); end >= 0 {
		rest = rest[:end]
	}
	netloc := rest
	if strings.Count(netloc, "[") != strings.Count(netloc, "]") {
		return false, true
	}
	if match := djangoURLBracket.FindStringSubmatch(netloc); match != nil {
		address, err := netip.ParseAddr(match[1])
		if len(match[1]) > 39 || err != nil || !address.Is6() || address.Zone() != "" {
			return false, true
		}
	}
	host := netloc
	if at := strings.LastIndex(host, "@"); at >= 0 {
		host = host[at+1:]
	}
	if strings.HasPrefix(host, "[") {
		end := strings.Index(host, "]")
		host = host[1:end]
	} else if at := strings.Index(host, ":"); at >= 0 {
		host = host[:at]
	}
	return host != "" && len(host) <= 253, true
}

// InstanceAdminUpdateRequest is UpdateRequestSerializer(data=body).is_valid().
func InstanceAdminUpdateRequest(body any) error {
	object, err := instanceAdminObject(body)
	if err != nil {
		return err
	}
	errs := map[string]any{}
	one := int64(1)
	if value, ok := object.Lookup("release_id"); !ok {
		errs["release_id"] = []string{PaymentCategoryValidationRequired}
	} else if _, codes := drfIntegerField(value, &one, nil); codes != nil {
		errs["release_id"] = codes
	}
	if value, ok := object.Lookup("tag"); !ok {
		errs["tag"] = []string{PaymentCategoryValidationRequired}
	} else if _, codes := (drfText{max: 64}).run(value); codes != nil {
		errs["tag"] = codes
	}
	if value, ok := object.Lookup("request_id"); !ok {
		errs["request_id"] = []string{PaymentCategoryValidationRequired}
	} else if _, codes := drfUUIDField(value); codes != nil {
		errs["request_id"] = codes
	}
	if len(errs) > 0 {
		return &InstanceAdminInvalid{Body: errs}
	}
	return nil
}

// InstanceAdminRestartRequest is RestartRequestSerializer(data=body).is_valid().
func InstanceAdminRestartRequest(body any) error {
	object, err := instanceAdminObject(body)
	if err != nil {
		return err
	}
	value, _ := object.Lookup("request_id")
	if _, codes := drfUUIDField(value); codes != nil {
		return &InstanceAdminInvalid{Body: map[string]any{"request_id": codes}}
	}
	return nil
}

// InstanceAdminUpdate is UpdateRequestSerializer.validated_data.
type InstanceAdminUpdate struct {
	ReleaseID *big.Int
	Tag       string
	RequestID string
}

// InstanceAdminUpdateValues is InstanceAdminUpdateRequest with the validated values.
func InstanceAdminUpdateValues(body any) (*InstanceAdminUpdate, error) {
	if err := InstanceAdminUpdateRequest(body); err != nil {
		return nil, err
	}
	object, _ := instanceAdminObject(body)
	one := int64(1)
	release, _ := object.Lookup("release_id")
	tag, _ := object.Lookup("tag")
	request, _ := object.Lookup("request_id")
	update := &InstanceAdminUpdate{}
	update.ReleaseID, _ = drfIntegerField(release, &one, nil)
	update.Tag, _ = (drfText{max: 64}).run(tag)
	update.RequestID, _ = drfUUIDField(request)
	return update, nil
}

// InstanceAdminRestartValue is RestartRequestSerializer.validated_data['request_id'].
func InstanceAdminRestartValue(body any) (string, error) {
	if err := InstanceAdminRestartRequest(body); err != nil {
		return "", err
	}
	object, _ := instanceAdminObject(body)
	value, _ := object.Lookup("request_id")
	id, _ := drfUUIDField(value)
	return id, nil
}
