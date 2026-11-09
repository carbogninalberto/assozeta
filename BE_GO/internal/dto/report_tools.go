package dto

import (
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// The MCP tool functions SavedReportViewSet.run calls (application/mcp_server).
// Ported: the QueryBuilder paths of query_data, count_data, get_field_values
// and aggregate_data for the Tags, PaymentCategory and Carnet models with
// direct (non-traversing) fields, plus get_schema's unknown-model answer.
// Everything else is refused explicitly (ErrReportNotPorted) before any query.

var (
	// ErrReportFailed is an exception inside the tool: the view's JSON 500.
	ErrReportFailed = errors.New("report tool raised")
	// ErrReportNotPorted marks inputs whose Django behaviour is not ported.
	ErrReportNotPorted = errors.New("report tool input not ported")
)

// ReportToolError is a tool result carrying 'error' (the view's 400).
type ReportToolError struct{ Message string }

func (e *ReportToolError) Error() string { return e.Message }

type reportKind int

const (
	reportUUID reportKind = iota
	reportText
	reportInt
	reportBool
	reportDecimal
	reportDateTime
	reportJSON
)

type reportField struct {
	Name, Attname string
	Kind          reportKind
	Null          bool
	// FK marks a forward relation: Name is the relation, Attname the column.
	FK bool
	// Choices are the labels _build_choice_map applies (Italian overrides).
	Choices map[int64]string
	Min     int64
	Max     int64
	// Digits and Places are the DecimalField's max_digits/decimal_places.
	Digits, Places int
}

type reportModel struct {
	Label, Name, Table string
	Fields             []reportField // get_fields() order of the concrete fields
	Reverse            []string      // reverse relation names
	SoftDelete         []string
	PK                 string
}

func (m *reportModel) field(name string) (*reportField, bool) {
	for i := range m.Fields {
		if m.Fields[i].Name == name || m.Fields[i].Attname == name {
			return &m.Fields[i], true
		}
	}
	return nil, false
}

func (m *reportModel) reverse(name string) bool {
	for _, item := range m.Reverse {
		if item == name {
			return true
		}
	}
	return false
}

// known is the name set of _validate_fields_for_model/_validate_group_by:
// every get_fields() name plus the forward relations' attnames.
func (m *reportModel) known(name string) bool {
	_, ok := m.field(name)
	return ok || m.reverse(name)
}

var reportModels = map[string]*reportModel{
	"application.Tags": {Label: "application.Tags", Name: "Tags", Table: "application_tags", PK: "tag_id",
		Reverse: []string{"subscription"},
		Fields: []reportField{{Name: "tag_id", Attname: "tag_id", Kind: reportUUID}, {Name: "tag_name", Attname: "tag_name", Kind: reportText},
			{Name: "sport_association", Attname: "sport_association_id", Kind: reportUUID, FK: true},
			{Name: "creation_date", Attname: "creation_date", Kind: reportDateTime}}},
	"application.PaymentCategory": {Label: "application.PaymentCategory", Name: "PaymentCategory", Table: "application_paymentcategory",
		PK: "payment_category_id", SoftDelete: []string{"archived"},
		Reverse: []string{"users_default_category", "users_default_category_courses", "payment", "campsandretreatsperiodsservice"},
		Fields: []reportField{{Name: "payment_category_id", Attname: "payment_category_id", Kind: reportUUID},
			{Name: "name", Attname: "name", Kind: reportText}, {Name: "creation_date", Attname: "creation_date", Kind: reportDateTime},
			{Name: "sport_association", Attname: "sport_association_id", Kind: reportUUID, FK: true, Null: true},
			{Name: "expense", Attname: "expense", Kind: reportBool}, {Name: "tax_deductible", Attname: "tax_deductible", Kind: reportBool},
			{Name: "archived", Attname: "archived", Kind: reportBool},
			{Name: "type", Attname: "type", Kind: reportInt, Min: 0, Max: 32767, Choices: map[int64]string{1: "Istituzionale", 2: "Commerciale"}},
			{Name: "vat_management", Attname: "vat_management_id", Kind: reportUUID, FK: true, Null: true},
			{Name: "deleted", Attname: "deleted", Kind: reportBool}}},
	"application.Carnet": {Label: "application.Carnet", Name: "Carnet", Table: "application_carnet", PK: "carnet_id",
		Reverse: []string{"carnetsubscription"},
		Fields: []reportField{{Name: "carnet_id", Attname: "carnet_id", Kind: reportUUID},
			{Name: "title", Attname: "title", Kind: reportText, Null: true}, {Name: "description", Attname: "description", Kind: reportText, Null: true},
			{Name: "user_id", Attname: "user_id_id", Kind: reportUUID, FK: true, Null: true},
			{Name: "sport_association", Attname: "sport_association_id", Kind: reportUUID, FK: true, Null: true},
			{Name: "lessons_number", Attname: "lessons_number", Kind: reportInt, Min: 0, Max: 32767},
			{Name: "fee", Attname: "fee", Kind: reportDecimal, Digits: 9, Places: 2},
			{Name: "creation_date", Attname: "creation_date", Kind: reportDateTime},
			{Name: "public", Attname: "public", Kind: reportBool}, {Name: "meta", Attname: "meta", Kind: reportJSON, Null: true}}},
}

// reportWhitelist is security.MODEL_WHITELIST in declaration order.
var reportWhitelist = []string{"application.SportAssociation", "application.Associate", "application.Group", "application.Subscription",
	"application.Course", "application.CourseSubscription", "application.Payment", "application.PaymentCategory", "application.Invoice",
	"application.InvoiceRows", "application.MedicalCertificate", "application.Instructor", "application.BalanceSheet",
	"application.CustomAccounts", "application.Tags", "application.CourseTags", "application.Carnet", "application.CarnetSubscription",
	"application.Module", "application.AttendanceRegistry", "application.AttendanceDay", "application.Reminders"}

var reportBlacklist = map[string]bool{"password": true, "token": true, "secret": true, "api_key": true, "stripe_account_id": true,
	"stripe_customer_id": true, "stripe_onboarded": true, "two_fa_secret": true, "two_fa_enabled": true, "two_fa_backup_codes": true,
	"signature_url": true, "signature_storage_key": true, "configuration": true, "collaborator_permissions": true,
	"dashboard_layout": true, "push_subscriptions": true, "social_auth": true, "last_login": true, "is_superuser": true,
	"is_staff": true, "groups": true, "user_permissions": true}

// reportAllowed is security.is_field_allowed.
func reportAllowed(name string) bool {
	if reportBlacklist[name] {
		return false
	}
	for _, prefix := range []string{"stripe_", "two_fa", "social_auth_"} {
		if strings.HasPrefix(name, prefix) {
			return false
		}
	}
	return true
}

// reportResolveModel is _resolve_model: a non-whitelisted name raises; a
// whitelisted model outside the ported set is refused.
func reportResolveModel(value any) (*reportModel, error) {
	name, ok := value.(string)
	if !ok {
		// TypeError, an unhashable lookup or no whitelist match: ValueError.
		return nil, ErrReportFailed
	}
	label := ""
	if strings.Contains(name, ".") {
		for _, item := range reportWhitelist {
			if item == name {
				label = item
			}
		}
	} else {
		for _, item := range reportWhitelist {
			if strings.SplitN(item, ".", 2)[1] == name {
				label = item
				break
			}
		}
	}
	if label == "" {
		return nil, ErrReportFailed
	}
	model, ported := reportModels[label]
	if !ported {
		return nil, ErrReportNotPorted
	}
	return model, nil
}

// ReportSettings are QueryBuilder's effective_integration('ai') values.
type ReportSettings struct {
	MaxResults   int64
	QueryTimeout int64
}

// ReportPlan is one tool call: statements to run in order, then Render.
type ReportPlan struct {
	Statements []domain.ReportStatement
	// Immediate is a result that needs no query (a 400 error dict).
	Immediate *ReportToolError
	Render    func(results [][][]*string) (any, error)
}

type reportArgs struct{ values []any }

func (a *reportArgs) add(value any) string {
	a.values = append(a.values, value)
	return "$" + strconv.Itoa(len(a.values))
}

func quote(column string) string { return `"` + column + `"` }

// ---------------------------------------------------------------------------
// Tool dispatch
// ---------------------------------------------------------------------------

// PlanReportTool binds the merged params like the Python call
// tool_fn(sport_association_id=..., **params) and plans the tool.
func PlanReportTool(tool string, params domain.RequestObject, association string, settings func() (ReportSettings, error)) (ReportPlan, error) {
	if _, present := params.Lookup("sport_association_id"); present {
		return ReportPlan{}, ErrReportFailed // multiple values for keyword argument
	}
	get := func(name string) (any, bool) { return params.Lookup(name) }
	switch tool {
	case "get_schema":
		return planReportSchema(params)
	case "query_data", "count_data", "aggregate_data", "get_field_values":
	default:
		// export_data, export_multi_sheet and get_attendance_matrix build files.
		return ReportPlan{}, ErrReportNotPorted
	}
	modelName, present := get("model_name")
	if !present {
		return ReportPlan{}, ErrReportFailed // missing required positional argument
	}
	var fieldName any
	if tool == "get_field_values" {
		if fieldName, present = get("field_name"); !present {
			return ReportPlan{}, ErrReportFailed
		}
	}
	// QueryBuilder.__init__ reads the integration settings before _resolve_model.
	config, err := settings()
	if err != nil {
		return ReportPlan{}, err
	}
	model, err := reportResolveModel(modelName)
	if err != nil {
		return ReportPlan{}, err
	}
	if tool == "get_field_values" {
		text, ok := fieldName.(string)
		if !ok {
			return ReportPlan{}, reportAttributeError(fieldName)
		}
		if !reportAllowed(strings.Split(text, "__")[0]) {
			return ReportPlan{}, ErrReportFailed
		}
	}
	args := &reportArgs{}
	where, err := model.where(args, association, params)
	if err != nil {
		return ReportPlan{}, err
	}
	switch tool {
	case "count_data":
		return ReportPlan{Statements: []domain.ReportStatement{{SQL: `SELECT COUNT(*)::text FROM ` + model.Table + where, Args: args.values, Text: 1}},
			Render: func(results [][][]*string) (any, error) {
				return domain.RequestObject{{Name: "count", Value: json.Number(*results[0][0][0])}}, nil
			}}, nil
	case "query_data":
		return model.planQuery(args, where, params, config)
	case "get_field_values":
		return model.planFieldValues(args, where, fieldName.(string), params)
	default:
		return model.planAggregate(args, where, params)
	}
}

// PythonDict is json.loads' dict for an object with repeated keys: the first
// position keeps the last value.
func PythonDict(object domain.RequestObject) domain.RequestObject {
	result := make(domain.RequestObject, 0, len(object))
	for _, field := range object {
		replaced := false
		for i := range result {
			if result[i].Name == field.Name {
				result[i].Value, replaced = field.Value, true
			}
		}
		if !replaced {
			result = append(result, field)
		}
	}
	return result
}

// reportAttributeError: a method call (.split, .items, .get, .lower,
// .replace) on a value of the wrong type raises AttributeError.
func reportAttributeError(any) error { return ErrReportFailed }

func planReportSchema(params domain.RequestObject) (ReportPlan, error) {
	value, _ := params.Lookup("model_name")
	if !PythonTruthy(value) || value == "null" {
		return ReportPlan{}, ErrReportNotPorted // get_full_schema()
	}
	var text string
	switch v := value.(type) {
	case string:
		text = v
		for _, item := range reportWhitelist {
			if item == "application."+v || item == v {
				return ReportPlan{}, ErrReportNotPorted // the model schema
			}
		}
	case bool:
		text = PythonStr(v)
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return ReportPlan{}, ErrReportNotPorted
		}
		text = PythonStr(v)
	default:
		return ReportPlan{}, ErrReportFailed // unhashable in MODEL_WHITELIST
	}
	return ReportPlan{Immediate: &ReportToolError{Message: "Model " + text + " not found"}}, nil
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------

// where is _base_queryset (tenant, soft delete unless include_archived)
// followed by _apply_filters.
func (m *reportModel) where(args *reportArgs, association string, params domain.RequestObject) (string, error) {
	clauses := []string{quote("sport_association_id") + " = " + args.add(association) + "::uuid"}
	archived, _ := params.Lookup("include_archived")
	if !PythonTruthy(archived) {
		for _, name := range m.SoftDelete {
			clauses = append(clauses, "NOT "+quote(name))
		}
	}
	if value, _ := params.Lookup("filters"); PythonTruthy(value) {
		group, err := m.group(args, value, false)
		if err != nil {
			return "", err
		}
		if group != "" {
			clauses = append(clauses, group)
		}
	}
	if value, _ := params.Lookup("exclude"); PythonTruthy(value) {
		group, err := m.group(args, value, true)
		if err != nil {
			return "", err
		}
		if group != "" {
			clauses = append(clauses, "NOT ("+group+")")
		}
	}
	if value, _ := params.Lookup("or_filters"); PythonTruthy(value) {
		if items, ok := value.([]any); ok {
			var alternatives []string
			for _, item := range items {
				object, ok := item.(domain.RequestObject)
				if !ok {
					continue
				}
				group, err := m.group(args, object, false)
				if err != nil {
					return "", err
				}
				if group != "" {
					alternatives = append(alternatives, "("+group+")")
				}
			}
			if len(alternatives) > 0 {
				clauses = append(clauses, "("+strings.Join(alternatives, " OR ")+")")
			}
		}
	}
	return " WHERE " + strings.Join(clauses, " AND "), nil
}

// group is validate_filter_fields + _coerce_filter_values + Q(**kwargs): the
// AND of the kept conditions ("" when every key was dropped).
func (m *reportModel) group(args *reportArgs, value any, negated bool) (string, error) {
	object, ok := value.(domain.RequestObject)
	if !ok {
		return "", reportAttributeError(value) // .items() on a non-dict
	}
	var conditions []string
	for _, item := range PythonDict(object) {
		if !reportAllowed(strings.Split(item.Name, "__")[0]) {
			continue
		}
		condition, err := m.condition(args, item.Name, item.Value, negated)
		if err != nil {
			return "", err
		}
		conditions = append(conditions, condition)
	}
	return strings.Join(conditions, " AND "), nil
}

var reportLookups = map[string]bool{"exact": true, "iexact": true, "contains": true, "icontains": true, "gt": true, "gte": true,
	"lt": true, "lte": true, "in": true, "isnull": true, "startswith": true, "istartswith": true, "endswith": true, "iendswith": true,
	"range": true}

func (m *reportModel) resolve(name string) (*reportField, error) {
	if name == "pk" {
		field, _ := m.field(m.PK)
		return field, nil
	}
	if field, ok := m.field(name); ok {
		return field, nil
	}
	if m.reverse(name) {
		return nil, ErrReportNotPorted
	}
	return nil, ErrReportFailed // FieldError: cannot resolve keyword
}

func (m *reportModel) condition(args *reportArgs, key string, value any, negated bool) (string, error) {
	parts := strings.Split(key, "__")
	field, err := m.resolve(parts[0])
	if err != nil {
		return "", err
	}
	lookup := "exact"
	switch {
	case len(parts) == 2 && reportLookups[parts[1]]:
		lookup = parts[1]
	case len(parts) > 1:
		return "", ErrReportNotPorted // relation traversal, transforms, other lookups
	}
	if field.Kind == reportJSON {
		return "", ErrReportNotPorted
	}
	if field.FK && lookup != "exact" && lookup != "in" && lookup != "isnull" {
		return "", ErrReportNotPorted
	}
	column := quote(field.Attname)
	if value == nil {
		if lookup == "exact" || lookup == "iexact" {
			return column + " IS NULL", nil
		}
		return "", ErrReportFailed // Cannot use None as a query value
	}
	var sql string
	switch lookup {
	case "isnull":
		flag, ok := value.(bool)
		if !ok {
			return "", ErrReportNotPorted
		}
		if flag {
			return column + " IS NULL", nil
		}
		return column + " IS NOT NULL", nil
	case "in", "range":
		items, ok := value.([]any)
		if !ok {
			return "", ErrReportNotPorted
		}
		var placeholders []string
		for _, item := range items {
			if item == nil && lookup == "in" {
				continue // In drops None
			}
			prepared, err := field.prepare(item, false)
			if err != nil {
				return "", err
			}
			placeholders = append(placeholders, args.add(prepared)+field.cast())
		}
		if lookup == "range" {
			if len(items) != 2 || len(placeholders) != 2 {
				return "", ErrReportNotPorted
			}
			sql = column + " BETWEEN " + placeholders[0] + " AND " + placeholders[1]
		} else if len(placeholders) == 0 {
			sql = "FALSE" // EmptyResultSet
		} else {
			sql = column + " IN (" + strings.Join(placeholders, ", ") + ")"
		}
	case "iexact", "contains", "icontains", "startswith", "istartswith", "endswith", "iendswith":
		if field.Kind != reportText {
			return "", ErrReportNotPorted
		}
		text, ok := value.(string)
		if !ok {
			return "", ErrReportNotPorted
		}
		if lookup == "iexact" {
			sql = "UPPER(" + column + "::text) = UPPER(" + args.add(text) + ")"
			break
		}
		escaped := strings.NewReplacer(`\`, `\\`, "%", `\%`, "_", `\_`).Replace(text)
		switch lookup {
		case "contains", "icontains":
			escaped = "%" + escaped + "%"
		case "startswith", "istartswith":
			escaped += "%"
		default:
			escaped = "%" + escaped
		}
		if strings.HasPrefix(lookup, "i") {
			sql = "UPPER(" + column + "::text) LIKE UPPER(" + args.add(escaped) + ")"
		} else {
			sql = column + "::text LIKE " + args.add(escaped)
		}
	default:
		if field.Kind == reportUUID && lookup != "exact" {
			return "", ErrReportNotPorted
		}
		if field.Kind == reportBool && lookup != "exact" {
			return "", ErrReportNotPorted
		}
		prepared, err := field.prepare(value, true)
		if err != nil {
			return "", err
		}
		operator := map[string]string{"exact": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[lookup]
		sql = column + " " + operator + " " + args.add(prepared) + field.cast()
	}
	if negated && field.Null {
		// exclude() on a nullable column keeps NULL rows.
		sql = "(" + sql + " AND " + column + " IS NOT NULL)"
	}
	return sql, nil
}

func (f *reportField) cast() string {
	switch f.Kind {
	case reportUUID:
		return "::uuid"
	case reportInt:
		return "::integer"
	case reportBool:
		return "::boolean"
	case reportDecimal:
		return "::numeric"
	case reportDateTime:
		return "::timestamptz"
	}
	return "::text"
}

var (
	reportPythonInt = regexp.MustCompile(`^[ \t\n\r\v\f]*[+-]?[0-9]+[ \t\n\r\v\f]*$`)
	reportDecimalRe = regexp.MustCompile(`^-?([0-9]+)(?:\.([0-9]+))?$`)
	reportDateTimes = []struct {
		pattern *regexp.Regexp
		layout  string
	}{
		{regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$`), "2006-01-02T15:04:05"},
		{regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$`), "2006-01-02T15:04"},
		{regexp.MustCompile(`^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$`), "2006-01-02 15:04:05"},
		{regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`), "2006-01-02"},
	}
)

// prepare is _coerce_filter_values (top-level strings only) followed by the
// field's get_prep_value; it returns the SQL parameter.
func (f *reportField) prepare(value any, top bool) (any, error) {
	switch f.Kind {
	case reportUUID:
		text, ok := value.(string)
		if !ok {
			return nil, ErrReportNotPorted
		}
		return reportUUIDValue(text)
	case reportText:
		text, ok := value.(string)
		if !ok {
			return nil, ErrReportNotPorted
		}
		return text, nil
	case reportBool:
		flag, ok := value.(bool)
		if !ok {
			return nil, ErrReportNotPorted
		}
		return flag, nil
	case reportInt:
		var text string
		switch v := value.(type) {
		case json.Number:
			text = string(v)
			if strings.ContainsAny(text, ".eE") {
				return nil, ErrReportNotPorted
			}
		case string:
			// int(value), both in the coercion and in IntegerField.get_prep_value.
			if !reportPythonInt.MatchString(v) {
				for _, r := range v {
					if r > 127 || r == '_' || r < 0x20 {
						return nil, ErrReportNotPorted
					}
				}
				return nil, ErrReportFailed
			}
			text = strings.TrimSpace(v)
		default:
			return nil, ErrReportNotPorted
		}
		number, ok := new(big.Int).SetString(strings.TrimPrefix(text, "+"), 10)
		if !ok || !number.IsInt64() || number.Int64() < f.Min || number.Int64() > f.Max {
			return nil, ErrReportNotPorted // out-of-range integer lookups
		}
		return number.Int64(), nil
	case reportDecimal:
		var text string
		switch v := value.(type) {
		case json.Number:
			text = string(v)
		case string:
			if !top {
				return nil, ErrReportNotPorted
			}
			text = v
		default:
			return nil, ErrReportNotPorted
		}
		match := reportDecimalRe.FindStringSubmatch(text)
		if match == nil || len(match[2]) > f.Places || len(strings.TrimLeft(match[1], "0"))+len(match[2]) > f.Digits {
			return nil, ErrReportNotPorted
		}
		return text, nil
	case reportDateTime:
		text, ok := value.(string)
		if !ok || !top {
			return nil, ErrReportNotPorted
		}
		for _, format := range reportDateTimes {
			if !format.pattern.MatchString(text) {
				continue
			}
			moment, err := time.ParseInLocation(format.layout, text, time.UTC)
			if err != nil {
				return nil, ErrReportNotPorted
			}
			return moment, nil
		}
		return nil, ErrReportNotPorted
	}
	return nil, ErrReportNotPorted
}

// reportUUIDValue is uuid.UUID(hex=...) for canonical spellings; other
// spellings Python could still accept are refused.
func reportUUIDValue(text string) (string, error) {
	hex := strings.ReplaceAll(strings.ReplaceAll(text, "urn:", ""), "uuid:", "")
	hex = strings.ReplaceAll(strings.Trim(hex, "{}"), "-", "")
	if len(hex) != 32 {
		return "", ErrReportFailed
	}
	for _, r := range hex {
		if !strings.ContainsRune("0123456789abcdefABCDEF", r) {
			return "", ErrReportNotPorted
		}
	}
	lower := strings.ToLower(hex)
	return lower[:8] + "-" + lower[8:12] + "-" + lower[12:16] + "-" + lower[16:20] + "-" + lower[20:], nil
}

// ---------------------------------------------------------------------------
// Values
// ---------------------------------------------------------------------------

// pythonIter is `for item in value` for the parsed JSON types.
func pythonIter(value any) ([]any, error) {
	switch v := value.(type) {
	case []any:
		return v, nil
	case string:
		items := []any{}
		for _, r := range v {
			items = append(items, string(r))
		}
		return items, nil
	case domain.RequestObject:
		items := []any{}
		for _, field := range v {
			items = append(items, field.Name)
		}
		return items, nil
	}
	return nil, ErrReportFailed
}

// render is the SQL expression whose text Render turns into the Python value.
func (f *reportField) render(expression string) string {
	if f.Kind == reportDateTime {
		return `to_char(` + expression + ` AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')`
	}
	return expression + "::text"
}

// value is _serialize_value of the column text (nil: None).
func (f *reportField) value(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	switch f.Kind {
	case reportUUID:
		return *text, nil
	case reportText:
		return reportUnescape(*text)
	case reportInt:
		return json.Number(*text), nil
	case reportBool:
		return *text == "true", nil
	case reportDecimal:
		return reportFloat(*text)
	case reportDateTime:
		return reportISO(*text), nil
	case reportJSON:
		parsed, err := ParseReferenceJSON([]byte(*text))
		if err != nil {
			return nil, err
		}
		if inner, ok := parsed.(string); ok {
			return reportUnescape(inner)
		}
		return parsed, nil
	}
	return nil, ErrReportNotPorted
}

func reportFloat(text string) (any, error) {
	number, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return nil, err
	}
	return json.Number(PythonFloatStr(number)), nil
}

// reportISO turns to_char's UTC text into datetime.isoformat().
func reportISO(text string) string {
	return strings.TrimSuffix(text, ".000000") + "+00:00"
}

var reportEntities = map[string]string{"amp;": "&", "AMP;": "&", "lt;": "<", "LT;": "<", "gt;": ">", "GT;": ">", "quot;": `"`,
	"QUOT;": `"`, "apos;": "'", "nbsp;": " "}

// reportUnescape is html.unescape for text with '&' (the common named
// references); numeric and other references are refused.
func reportUnescape(text string) (any, error) {
	if !strings.Contains(text, "&") {
		return text, nil
	}
	var out strings.Builder
	for i := 0; i < len(text); {
		if text[i] != '&' {
			out.WriteByte(text[i])
			i++
			continue
		}
		rest := text[i+1:]
		if rest == "" || strings.ContainsRune("\t\n\f <&;", rune(rest[0])) {
			out.WriteByte('&')
			i++
			continue
		}
		end := strings.IndexAny(rest, "\t\n\f <&#;")
		if end < 0 || rest[end] != ';' {
			return nil, ErrReportNotPorted
		}
		replacement, ok := reportEntities[rest[:end+1]]
		if !ok {
			return nil, ErrReportNotPorted
		}
		out.WriteString(replacement)
		i += end + 2
	}
	return out.String(), nil
}

// labels is _build_choice_map for the selected expressions.
func (m *reportModel) labels(row domain.RequestObject) domain.RequestObject {
	for i, item := range row {
		field, ok := m.field(item.Name)
		if !ok || field.Choices == nil || field.Name != item.Name {
			continue
		}
		if number, ok := item.Value.(json.Number); ok {
			if value, err := strconv.ParseInt(string(number), 10, 64); err == nil {
				if label, ok := field.Choices[value]; ok {
					row[i].Value = label
				}
			}
		}
	}
	return row
}

// ---------------------------------------------------------------------------
// query_data
// ---------------------------------------------------------------------------

type reportColumn struct {
	key   string
	field *reportField
}

func (m *reportModel) planQuery(args *reportArgs, where string, params domain.RequestObject, config ReportSettings) (ReportPlan, error) {
	if joins, _ := params.Lookup("joins"); PythonTruthy(joins) {
		return ReportPlan{}, ErrReportNotPorted
	}
	limit, offset, err := reportSlice(params, config.MaxResults)
	if err != nil {
		return ReportPlan{}, err
	}
	// Ordering (after the count) is validated before the fields.
	var orders []string
	orderValue, _ := params.Lookup("order_by")
	if PythonTruthy(orderValue) {
		items, err := pythonIter(orderValue)
		if err != nil {
			return ReportPlan{}, err
		}
		seen := map[string]bool{}
		for _, item := range items {
			text, ok := item.(string)
			if !ok {
				return ReportPlan{}, reportAttributeError(item)
			}
			clean := strings.TrimLeft(text, "-")
			if !reportAllowed(strings.Split(clean, "__")[0]) {
				continue
			}
			if text == "?" || clean == "" || len(text)-len(clean) > 1 || strings.Contains(clean, "__") {
				return ReportPlan{}, ErrReportNotPorted
			}
			field, err := m.resolve(clean)
			if err != nil {
				return ReportPlan{}, err
			}
			if field.FK && clean == field.Name {
				return ReportPlan{}, ErrReportNotPorted // relation ordering
			}
			if seen[field.Attname] {
				return ReportPlan{}, ErrReportNotPorted
			}
			seen[field.Attname] = true
			direction := " ASC"
			if text != clean {
				direction = " DESC"
			}
			orders = append(orders, quote(field.Attname)+" AS o"+strconv.Itoa(len(orders))+direction)
		}
	}
	columns, labelled, err := m.queryColumns(params)
	if err != nil {
		return ReportPlan{}, err
	}
	// Output aliases keep the raw ordering columns distinct from their text
	// renderings; the raw columns join the DISTINCT like Django's extra select.
	selects := make([]string, 0, len(columns)+len(orders))
	for i, column := range columns {
		selects = append(selects, column.field.render(quote(column.field.Attname))+" AS t"+strconv.Itoa(i))
	}
	keys := make([]string, 0, len(orders))
	for i, order := range orders {
		parts := strings.Fields(order) // column AS alias direction
		selects = append(selects, parts[0]+" AS o"+strconv.Itoa(i))
		keys = append(keys, "o"+strconv.Itoa(i)+" "+parts[3])
	}
	hidden := len(orders)
	query := `SELECT DISTINCT ` + strings.Join(selects, ", ") + ` FROM ` + m.Table + where
	if len(orders) > 0 {
		query += ` ORDER BY ` + strings.Join(keys, ", ")
	}
	query += ` LIMIT ` + strconv.FormatInt(limit, 10)
	if offset > 0 {
		query += ` OFFSET ` + strconv.FormatInt(offset, 10)
	}
	count := `SELECT COUNT(*)::text FROM ` + m.Table + where
	return ReportPlan{Statements: []domain.ReportStatement{{SQL: count, Args: args.values, Text: 1},
		{SQL: query, Args: args.values, Text: len(columns), Hidden: hidden}},
		Render: func(results [][][]*string) (any, error) {
			total, _ := strconv.ParseInt(*results[0][0][0], 10, 64)
			data := []any{}
			for _, record := range results[1] {
				row := domain.RequestObject{}
				for i, column := range columns {
					value, err := column.field.value(record[i])
					if err != nil {
						return nil, err
					}
					row = append(row, domain.RequestField{Name: column.key, Value: value})
				}
				if labelled {
					row = m.labels(row)
				}
				data = append(data, row)
			}
			return domain.RequestObject{{Name: "count", Value: json.Number(strconv.FormatInt(total, 10))},
				{Name: "has_more", Value: offset+limit < total}, {Name: "data", Value: data}}, nil
		}}, nil
}

// queryColumns is _validate_fields_for_model and the values() call; labelled
// is false for values() without fields (no choice map).
func (m *reportModel) queryColumns(params domain.RequestObject) ([]reportColumn, bool, error) {
	value, _ := params.Lookup("fields")
	var columns []reportColumn
	if !PythonTruthy(value) {
		for i := range m.Fields {
			if !m.Fields[i].FK && reportAllowed(m.Fields[i].Name) {
				columns = append(columns, reportColumn{m.Fields[i].Name, &m.Fields[i]})
			}
		}
		return columns, true, nil
	}
	items, err := pythonIter(value)
	if err != nil {
		return nil, false, err
	}
	seen := map[string]bool{}
	for _, item := range items {
		text, ok := item.(string)
		if !ok {
			return nil, false, reportAttributeError(item)
		}
		root := strings.Split(text, "__")[0]
		if !m.known(root) || !reportAllowed(root) {
			continue
		}
		field, ok := m.field(text)
		if !ok || seen[text] {
			return nil, false, ErrReportNotPorted // traversals, reverse relations, duplicates
		}
		seen[text] = true
		columns = append(columns, reportColumn{text, field})
	}
	if len(columns) == 0 {
		// values() without fields: every concrete column by attname.
		for i := range m.Fields {
			columns = append(columns, reportColumn{m.Fields[i].Attname, &m.Fields[i]})
		}
		return columns, false, nil
	}
	return columns, true, nil
}

// reportSlice is effective_limit = min(limit or MAX, MAX) and qs[offset:offset+limit].
func reportSlice(params domain.RequestObject, maximum int64) (int64, int64, error) {
	integer := func(value any) (int64, error) {
		switch v := value.(type) {
		case json.Number:
			if strings.ContainsAny(string(v), ".eE") {
				return 0, ErrReportNotPorted
			}
			number, err := strconv.ParseInt(string(v), 10, 64)
			if err != nil || number > 1<<40 || number < -(1<<40) {
				return 0, ErrReportNotPorted
			}
			return number, nil
		case bool:
			return 0, ErrReportNotPorted
		}
		return 0, ErrReportFailed // TypeError in min() or +
	}
	limit := maximum
	if value, _ := params.Lookup("limit"); PythonTruthy(value) {
		number, err := integer(value)
		if err != nil {
			return 0, 0, err
		}
		if number < limit {
			limit = number
		}
	}
	var offset int64
	if value, present := params.Lookup("offset"); present {
		number, err := integer(value)
		if err != nil {
			return 0, 0, err
		}
		offset = number
	}
	if offset < 0 || offset+limit < 0 {
		return 0, 0, ErrReportFailed // Negative indexing is not supported.
	}
	if limit < 0 {
		return 0, 0, ErrReportNotPorted
	}
	return limit, offset, nil
}

// ---------------------------------------------------------------------------
// get_field_values
// ---------------------------------------------------------------------------

func (m *reportModel) planFieldValues(args *reportArgs, where, name string, params domain.RequestObject) (ReportPlan, error) {
	if strings.Contains(name, "__") || name == "pk" || m.reverse(name) {
		return ReportPlan{}, ErrReportNotPorted
	}
	field, ok := m.field(name)
	if !ok {
		return ReportPlan{}, ErrReportFailed // FieldError
	}
	if (field.FK && name == field.Name) || field.Kind == reportJSON {
		return ReportPlan{}, ErrReportNotPorted
	}
	limit := ""
	value, present := params.Lookup("limit")
	switch v := value.(type) {
	case nil:
		if !present {
			limit = " LIMIT 100"
		}
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return ReportPlan{}, ErrReportNotPorted
		}
		number, err := strconv.ParseInt(string(v), 10, 64)
		if err != nil || number > 1<<40 {
			return ReportPlan{}, ErrReportNotPorted
		}
		if number < 0 {
			return ReportPlan{}, ErrReportFailed
		}
		limit = " LIMIT " + strconv.FormatInt(number, 10)
	case bool:
		return ReportPlan{}, ErrReportNotPorted
	default:
		return ReportPlan{}, ErrReportFailed
	}
	column := quote(field.Attname)
	total := `SELECT COUNT(*)::text FROM (SELECT DISTINCT ` + column + ` FROM ` + m.Table + where + `) AS values_subquery`
	values := `SELECT DISTINCT ` + field.render(column) + ` AS t0, ` + column + ` AS o0 FROM ` + m.Table + where + ` ORDER BY o0 ASC` + limit
	return ReportPlan{Statements: []domain.ReportStatement{{SQL: total, Args: args.values, Text: 1},
		{SQL: values, Args: args.values, Text: 1, Hidden: 1}},
		Render: func(results [][][]*string) (any, error) {
			items := []any{}
			for _, record := range results[1] {
				value, err := field.value(record[0])
				if err != nil {
					return nil, err
				}
				if number, ok := value.(json.Number); ok && field.Choices != nil {
					if raw, err := strconv.ParseInt(string(number), 10, 64); err == nil {
						if label, ok := field.Choices[raw]; ok {
							value = label
						}
					}
				}
				items = append(items, value)
			}
			return domain.RequestObject{{Name: "values", Value: items}, {Name: "total_distinct", Value: json.Number(*results[0][0][0])}}, nil
		}}, nil
}

// ---------------------------------------------------------------------------
// aggregate_data
// ---------------------------------------------------------------------------

var reportForbiddenAlias = regexp.MustCompile("['`\"\\]\\[;\\s]|--|/\\*|\\*/")

type reportAggregate struct {
	alias, function string
	field           *reportField // nil: COUNT(*)
}

func (m *reportModel) planAggregate(args *reportArgs, where string, params domain.RequestObject) (ReportPlan, error) {
	value, _ := params.Lookup("aggregations")
	if !PythonTruthy(value) {
		return ReportPlan{Immediate: &ReportToolError{Message: "No aggregations specified"}}, nil
	}
	items, err := pythonIter(value)
	if err != nil {
		return ReportPlan{}, err
	}
	var aggregates []reportAggregate
	for _, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return ReportPlan{}, reportAttributeError(item)
		}
		object = PythonDict(object)
		function, present := object.Lookup("function")
		if !present {
			function = ""
		}
		name, ok := function.(string)
		if !ok {
			return ReportPlan{}, reportAttributeError(function)
		}
		name = strings.ToLower(name)
		fieldValue, present := object.Lookup("field")
		if !present {
			fieldValue = "*"
		}
		alias, present := object.Lookup("alias")
		if !present {
			if _, isText := fieldValue.(string); !isText {
				if name == "count" || name == "sum" || name == "avg" || name == "min" || name == "max" {
					return ReportPlan{}, reportAttributeError(fieldValue)
				}
				continue
			}
			alias = name + "_" + fieldValue.(string)
		}
		aliasText, ok := alias.(string)
		if !ok {
			return ReportPlan{}, reportAttributeError(alias)
		}
		aliasText = strings.ReplaceAll(strings.ReplaceAll(aliasText, ".", "_"), "-", "_")
		if name != "count" && name != "sum" && name != "avg" && name != "min" && name != "max" {
			continue
		}
		fieldText, ok := fieldValue.(string)
		if !ok {
			return ReportPlan{}, reportAttributeError(fieldValue)
		}
		aggregate := reportAggregate{alias: aliasText, function: name}
		if fieldText == "*" && name == "count" {
			// Count('*')
		} else if !reportAllowed(strings.Split(fieldText, "__")[0]) {
			continue
		} else if strings.Contains(fieldText, "__") {
			return ReportPlan{}, ErrReportNotPorted
		} else {
			field, err := m.resolve(fieldText)
			if err != nil {
				return ReportPlan{}, err
			}
			aggregate.field = field
		}
		if reportForbiddenAlias.MatchString(aliasText) || strings.Contains(aliasText, "__") || m.known(aliasText) || aliasText == "pk" {
			return ReportPlan{}, ErrReportNotPorted
		}
		replaced := false
		for i := range aggregates {
			if aggregates[i].alias == aliasText {
				aggregates[i], replaced = aggregate, true
			}
		}
		if !replaced {
			aggregates = append(aggregates, aggregate)
		}
	}
	if len(aggregates) == 0 {
		return ReportPlan{Immediate: &ReportToolError{Message: "No valid aggregations"}}, nil
	}
	expressions := make([]string, len(aggregates))
	for i, aggregate := range aggregates {
		expression, err := aggregate.sql()
		if err != nil {
			return ReportPlan{}, err
		}
		expressions[i] = expression
	}
	groupValue, _ := params.Lookup("group_by")
	if !PythonTruthy(groupValue) {
		query := `SELECT ` + strings.Join(expressions, ", ") + ` FROM ` + m.Table + where
		return ReportPlan{Statements: []domain.ReportStatement{{SQL: query, Args: args.values, Text: len(expressions)}},
			Render: func(results [][][]*string) (any, error) {
				row := domain.RequestObject{}
				for i, aggregate := range aggregates {
					value, err := aggregate.value(results[0][0][i])
					if err != nil {
						return nil, err
					}
					row = append(row, domain.RequestField{Name: aggregate.alias, Value: value})
				}
				return row, nil
			}}, nil
	}
	groupItems, err := pythonIter(groupValue)
	if err != nil {
		return ReportPlan{}, err
	}
	var groups []reportColumn
	seen := map[string]bool{}
	for _, item := range groupItems {
		text, ok := item.(string)
		if !ok {
			return ReportPlan{}, reportAttributeError(item)
		}
		root := strings.Split(text, "__")[0]
		if !reportAllowed(root) || !m.known(root) {
			continue
		}
		field, ok := m.field(text)
		if !ok || (field.FK && text == field.Name) || field.Kind == reportJSON || seen[text] {
			return ReportPlan{}, ErrReportNotPorted // date parts, relations, duplicates
		}
		seen[text] = true
		groups = append(groups, reportColumn{text, field})
	}
	if len(groups) == 0 {
		return ReportPlan{Immediate: &ReportToolError{Message: "No valid group_by fields"}}, nil
	}
	// Aliased outputs: ORDER BY then names the input columns.
	var selects, keys []string
	for i, group := range groups {
		selects = append(selects, group.field.render(quote(group.field.Attname))+" AS g"+strconv.Itoa(i))
		keys = append(keys, quote(group.field.Attname))
	}
	for i, expression := range expressions {
		selects = append(selects, expression+" AS a"+strconv.Itoa(i))
	}
	query := `SELECT ` + strings.Join(selects, ", ") + ` FROM ` + m.Table + where + ` GROUP BY ` + strings.Join(keys, ", ") +
		` ORDER BY ` + strings.Join(keys, " ASC, ") + ` ASC`
	return ReportPlan{Statements: []domain.ReportStatement{{SQL: query, Args: args.values, Text: len(selects)}},
		Render: func(results [][][]*string) (any, error) {
			data := []any{}
			for _, record := range results[0] {
				row := domain.RequestObject{}
				for i, group := range groups {
					value, err := group.field.value(record[i])
					if err != nil {
						return nil, err
					}
					row = append(row, domain.RequestField{Name: group.key, Value: value})
				}
				for i, aggregate := range aggregates {
					value, err := aggregate.value(record[len(groups)+i])
					if err != nil {
						return nil, err
					}
					row = append(row, domain.RequestField{Name: aggregate.alias, Value: value})
				}
				data = append(data, m.labels(row))
			}
			return domain.RequestObject{{Name: "data", Value: data}, {Name: "count", Value: json.Number(strconv.Itoa(len(data)))}}, nil
		}}, nil
}

func (a reportAggregate) sql() (string, error) {
	if a.field == nil {
		return "COUNT(*)::text", nil
	}
	column := quote(a.field.Attname)
	switch a.function {
	case "count":
		return "COUNT(" + column + ")::text", nil
	case "min", "max":
		return a.field.render(strings.ToUpper(a.function) + "(" + column + ")"), nil
	}
	// SUM/AVG of non-numeric columns fail in PostgreSQL, as in Django.
	return strings.ToUpper(a.function) + "(" + column + ")::text", nil
}

// value renders an aggregate like Django's output field and _serialize_value.
func (a reportAggregate) value(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	switch {
	case a.field == nil || a.function == "count":
		return json.Number(*text), nil
	case a.function == "avg":
		return reportFloat(*text)
	case a.function == "sum" && a.field.Kind == reportInt:
		return json.Number(*text), nil
	case a.function == "sum" && a.field.Kind == reportDecimal:
		return reportFloat(*text)
	case a.function == "min" || a.function == "max":
		return a.field.value(text)
	}
	return nil, fmt.Errorf("%w: aggregate %s", ErrReportNotPorted, a.function)
}
