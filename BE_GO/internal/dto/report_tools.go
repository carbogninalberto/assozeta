package dto

import (
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyhtml"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// The MCP tool functions of application/mcp_server (QueryBuilder in
// query_builder.py, security.py, schema.py) on the generic engine of
// mcp_query.go: query_data, count_data, get_field_values, aggregate_data
// and get_schema for every whitelisted model. Inputs whose Django behaviour
// is not ported are refused (ErrReportNotPorted) before any query; tool
// exceptions are *MCPToolException (errors.Is ErrReportFailed) with
// Django's str(e).

var (
	// ErrReportFailed is an exception inside the tool: the view's JSON 500.
	ErrReportFailed = errors.New("report tool raised")
	// ErrReportNotPorted marks inputs whose Django behaviour is not ported.
	ErrReportNotPorted = refusal.New("report tool input not ported")
)

// ReportToolError is a tool result carrying 'error' (the view's 400).
type ReportToolError struct {
	Message string
	// Result is the whole result when it carries more than 'error'.
	Result domain.RequestObject
}

func (e *ReportToolError) Error() string { return e.Message }

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
	// Value is a result that needs no query (get_schema).
	Value  any
	Render func(results [][][]*string) (any, error)
}

type reportArgs struct{ values []any }

func (a *reportArgs) add(value any) string {
	a.values = append(a.values, value)
	return "$" + strconv.Itoa(len(a.values))
}

// PlanReportTool binds the merged params like the Python call
// tool_fn(sport_association_id=..., **params) and plans the tool.
func PlanReportTool(tool string, params domain.RequestObject, association string, settings func() (ReportSettings, error)) (ReportPlan, error) {
	return PlanReportToolGroup(tool, params, association, nil, settings)
}

// PlanReportToolGroup is PlanReportTool during a request whose X-Group-Id is
// group (canonical text; nil without a usable header): the default managers
// of the group-aware models filter by it.
func PlanReportToolGroup(tool string, params domain.RequestObject, association string, group *string, settings func() (ReportSettings, error)) (ReportPlan, error) {
	if _, present := params.Lookup("sport_association_id"); present {
		return ReportPlan{}, ErrReportFailed // multiple values for keyword argument
	}
	switch tool {
	case "get_schema":
		value, _ := params.Lookup("model_name")
		return MCPSchema(value)
	case "query_data", "count_data", "aggregate_data", "get_field_values":
	default:
		// export_data, export_multi_sheet and get_attendance_matrix build files.
		return ReportPlan{}, ErrReportNotPorted
	}
	if _, present := params.Lookup("model_name"); !present {
		return ReportPlan{}, ErrReportFailed // missing required positional argument
	}
	if _, present := params.Lookup("field_name"); tool == "get_field_values" && !present {
		return ReportPlan{}, ErrReportFailed
	}
	return PlanMCPToolGroup(tool, params, association, group, settings)
}

// PlanMCPTool runs QueryBuilder for a bound call (the arguments are the
// function's keyword arguments; required ones are present).
func PlanMCPTool(tool string, params domain.RequestObject, association string, settings func() (ReportSettings, error)) (ReportPlan, error) {
	return PlanMCPToolGroup(tool, params, association, nil, settings)
}

// PlanMCPToolGroup is PlanMCPTool with the request's group (see PlanReportToolGroup).
func PlanMCPToolGroup(tool string, params domain.RequestObject, association string, group *string, settings func() (ReportSettings, error)) (ReportPlan, error) {
	// QueryBuilder.__init__ reads the integration settings before _resolve_model.
	config, err := settings()
	if err != nil {
		return ReportPlan{}, err
	}
	modelName, _ := params.Lookup("model_name")
	model, entry, err := mcpResolveModel(modelName)
	if err != nil {
		return ReportPlan{}, err
	}
	if tool == "get_field_values" {
		value, _ := params.Lookup("field_name")
		name, ok := value.(string)
		if !ok {
			return ReportPlan{}, mcpAttributeError(value, "split")
		}
		if !mcpPathAllowed(model, name) {
			return ReportPlan{}, mcpRaise("Field '%s' is not allowed", name)
		}
	}
	archived, _ := params.Lookup("include_archived")
	q, err := mcpBaseQuery(model, entry, association, group, PythonTruthy(archived))
	if err != nil {
		return ReportPlan{}, err
	}
	if err = q.applyFilters(params); err != nil {
		return ReportPlan{}, err
	}
	switch tool {
	case "count_data":
		if q.compileErr != nil {
			return ReportPlan{}, q.compileErr
		}
		return ReportPlan{Statements: []domain.ReportStatement{q.countStatement()},
			Render: func(results [][][]*string) (any, error) {
				return domain.RequestObject{{Name: "count", Value: json.Number(*results[0][0][0])}}, nil
			}}, nil
	case "query_data":
		return q.planQuery(params, config)
	case "get_field_values":
		value, _ := params.Lookup("field_name")
		return q.planFieldValues(value.(string), params)
	}
	return q.planAggregate(params)
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

// mcpAttributeError is the AttributeError of a method call on a value of
// the wrong type.
func mcpAttributeError(value any, method string) error {
	switch value.(type) {
	case []any, string, bool, json.Number, domain.RequestObject:
		return mcpRaise("'%s' object has no attribute '%s'", pythonTypeName(value), method)
	case nil:
		return mcpRaise("'NoneType' object has no attribute '%s'", method)
	}
	return mcpNotPorted("attribute error")
}

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
	return nil, mcpRaise("'%s' object is not iterable", pythonTypeName(value))
}

// mcpStrings iterates a list argument whose items get .split/.lstrip.
func mcpStrings(value any, method string) ([]string, error) {
	items, err := pythonIter(value)
	if err != nil {
		return nil, err
	}
	texts := make([]string, 0, len(items))
	for _, item := range items {
		text, ok := item.(string)
		if !ok {
			return nil, mcpAttributeError(item, method)
		}
		texts = append(texts, text)
	}
	return texts, nil
}

// ---------------------------------------------------------------------------
// _apply_filters
// ---------------------------------------------------------------------------

// mcpParseFilterKey is security.parse_filter_key: the field path without a
// trailing lookup suffix, and that suffix ('exact' when there is none).
func mcpParseFilterKey(key string) (string, string) {
	parts := strings.Split(key, "__")
	if len(parts) >= 2 && mcpAllowedLookups[parts[len(parts)-1]] {
		return strings.Join(parts[:len(parts)-1], "__"), parts[len(parts)-1]
	}
	return key, "exact"
}

// filterLeaves is validate_filter_fields + _coerce_filter_values.
func (q *mcpQuery) filterLeaves(value any) ([]mcpLeaf, error) {
	object, ok := value.(domain.RequestObject)
	if !ok {
		return nil, mcpAttributeError(value, "items")
	}
	var leaves []mcpLeaf
	for _, item := range PythonDict(object) {
		fieldPath, _ := mcpParseFilterKey(item.Name)
		if !mcpPathAllowed(q.model, fieldPath) {
			continue
		}
		leaves = append(leaves, mcpLeaf{key: item.Name, value: mcpCoerce(q.model, item.Name, item.Value)})
	}
	return leaves, nil
}

func (q *mcpQuery) applyFilters(params domain.RequestObject) error {
	if value, _ := params.Lookup("filters"); PythonTruthy(value) {
		leaves, err := q.filterLeaves(value)
		if err != nil {
			return err
		}
		if err = q.addQ(mcpQ(leaves)); err != nil {
			return err
		}
	}
	if value, _ := params.Lookup("exclude"); PythonTruthy(value) {
		leaves, err := q.filterLeaves(value)
		if err != nil {
			return err
		}
		node := mcpQ(leaves)
		node.negated = true
		if err = q.addQ(node); err != nil {
			return err
		}
	}
	if value, _ := params.Lookup("or_filters"); PythonTruthy(value) {
		items, ok := value.([]any)
		if !ok {
			return nil
		}
		var combined *mcpNode
		for _, item := range items {
			if _, isDict := item.(domain.RequestObject); !isDict {
				continue
			}
			leaves, err := q.filterLeaves(item)
			if err != nil {
				return err
			}
			combined = mcpCombineOr(combined, mcpQ(leaves))
		}
		if combined != nil && len(combined.children) > 0 {
			if err := q.addQ(combined); err != nil {
				return err
			}
		}
	}
	return nil
}

// mcpCombineOr is Q.__or__ (Q._combine and tree.Node.add squashing).
func mcpCombineOr(left, right *mcpNode) *mcpNode {
	if left == nil || len(left.children) == 0 {
		return right
	}
	if len(right.children) == 0 {
		return left
	}
	result := &mcpNode{or: true}
	for _, side := range []*mcpNode{left, right} {
		if !side.negated && (side.or || len(side.children) == 1) {
			result.children = append(result.children, side.children...)
		} else {
			result.children = append(result.children, side)
		}
	}
	return result
}

// ---------------------------------------------------------------------------
// Statements
// ---------------------------------------------------------------------------

func (q *mcpQuery) concreteColumns() []string {
	var columns []string
	for _, field := range q.model.Fields {
		if field.Concrete {
			columns = append(columns, mcpColumn(q.base(), field.Column))
		}
	}
	return columns
}

// countStatement is QuerySet.count() of the distinct queryset.
func (q *mcpQuery) countStatement() domain.ReportStatement {
	var selects []string
	for i, column := range q.concreteColumns() {
		selects = append(selects, column+` AS "col`+strconv.Itoa(i+1)+`"`)
	}
	sql := `SELECT COUNT(*)::text FROM (SELECT DISTINCT ` + strings.Join(selects, ", ") + ` FROM ` + q.from() + q.whereSQL() + `) subquery`
	return domain.ReportStatement{SQL: sql, Args: q.args.values, Text: 1}
}

// mcpColumnOut is one rendered output column.
type mcpColumnOut struct {
	key   string
	expr  mcpExpr
	alias string
}

// wrap renders the outer text projection over an inner query whose output
// columns are aliased c0..cN.
func mcpWrap(inner string, columns []mcpColumnOut, args []any) domain.ReportStatement {
	selects := make([]string, len(columns))
	for i, column := range columns {
		selects[i] = mcpRender(column.expr, `q."`+column.alias+`"`)
	}
	return domain.ReportStatement{SQL: `SELECT ` + strings.Join(selects, ", ") + ` FROM (` + inner + `) q`, Args: args, Text: len(columns)}
}

// mcpRender is the SQL text rendering of a value of the expression's type.
func mcpRender(expr mcpExpr, sql string) string {
	switch expr.kind {
	case "datetime":
		return `to_char(` + sql + ` AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')`
	case "date":
		return `to_char(` + sql + `, 'YYYY-MM-DD')`
	}
	return sql + `::text`
}

// ---------------------------------------------------------------------------
// query_data (execute_query)
// ---------------------------------------------------------------------------

// mcpOrder is one validated order_by item.
type mcpOrder struct {
	name       string
	descending bool
}

// validateOrder is the blacklist filter of execute_query followed by
// Query.add_ordering's names_to_path check of each item.
func (q *mcpQuery) validateOrder(value any) ([]mcpOrder, error) {
	if !PythonTruthy(value) {
		return nil, nil
	}
	items, err := mcpStrings(value, "lstrip")
	if err != nil {
		return nil, err
	}
	var orders []mcpOrder
	for _, item := range items {
		clean := strings.TrimLeft(item, "-")
		if !mcpPathAllowed(q.model, clean) {
			continue
		}
		if item == "?" {
			return nil, mcpNotPorted("random ordering")
		}
		name := strings.TrimPrefix(item, "-")
		if _, err := mcpNamesToPath(q.model, strings.Split(name, "__"), true, false); err != nil {
			return nil, err
		}
		orders = append(orders, mcpOrder{name: name, descending: name != item})
	}
	return orders, nil
}

// orderExpr is SQLCompiler.find_ordering_name.
func (q *mcpQuery) orderExpr(order mcpOrder) (mcpExpr, error) {
	pieces := strings.Split(order.name, "__")
	info, err := q.setupJoins(pieces, nil, true, true)
	if err != nil {
		return mcpExpr{}, err
	}
	final := info.path.final
	if final.Relation && len(info.transforms) == 0 && order.name != "pk" && final.Attname != pieces[len(pieces)-1] {
		if related := final.relatedModel(); related != nil && len(related.Ordering) > 0 {
			return mcpExpr{}, mcpNotPorted("ordering by a relation with Meta.ordering")
		}
	}
	target, alias, _ := q.trimJoins(info)
	expr, err := q.targetExpr(info, target, alias)
	if err != nil {
		return expr, err
	}
	for _, transform := range info.transforms {
		next, err := q.tryTransform(expr, transform)
		if err != nil {
			// setup_joins' transform function reports the names_to_path error
			// when the final field is a Field (find_ordering_name).
			if errors.Is(err, errMCPNoTransform) && info.path.finalIsField() && info.lastErr != nil {
				return expr, info.lastErr
			}
			return expr, err
		}
		expr = next
	}
	return expr, nil
}

// mcpPyNumber is a Python int or float (bool counts as int) for the
// limit/offset arithmetic. Integers keep their full precision.
type mcpPyNumber struct {
	isFloat bool
	isBool  bool
	integer *big.Int
	float   float64
}

func mcpPyInt(number int64) mcpPyNumber { return mcpPyNumber{integer: big.NewInt(number)} }

func (n mcpPyNumber) typeName() string {
	switch {
	case n.isFloat:
		return "float"
	case n.isBool:
		return "bool"
	}
	return "int"
}

func (n mcpPyNumber) asFloat() float64 {
	if n.isFloat {
		return n.float
	}
	value, _ := new(big.Float).SetInt(n.integer).Float64()
	return value
}

func (n mcpPyNumber) isNegative() bool {
	if n.isFloat {
		return n.float < 0
	}
	return n.integer.Sign() < 0
}

// intValue is int(n): truncation toward zero.
func (n mcpPyNumber) intValue() (*big.Int, error) {
	if !n.isFloat {
		return new(big.Int).Set(n.integer), nil
	}
	if math.IsNaN(n.float) || math.IsInf(n.float, 0) {
		return nil, mcpNotPorted("non-finite limit or offset")
	}
	value, _ := new(big.Float).SetFloat64(n.float).Int(nil)
	return value, nil
}

// mcpPyNumberValue is a parsed JSON int, float or bool as a Python number.
func mcpPyNumberValue(value any) (mcpPyNumber, bool) {
	switch typed := value.(type) {
	case bool:
		integer := big.NewInt(0)
		if typed {
			integer = big.NewInt(1)
		}
		return mcpPyNumber{isBool: true, integer: integer}, true
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			number, err := strconv.ParseFloat(string(typed), 64)
			if err != nil {
				return mcpPyNumber{}, false
			}
			return mcpPyNumber{isFloat: true, float: number}, true
		}
		number, ok := new(big.Int).SetString(string(typed), 10)
		if !ok {
			return mcpPyNumber{}, false
		}
		return mcpPyNumber{integer: number}, true
	}
	return mcpPyNumber{}, false
}

// mcpEffectiveLimit is min(limit or MAX, MAX).
func mcpEffectiveLimit(value any, maximum int64) (mcpPyNumber, error) {
	limit := mcpPyInt(maximum)
	if !PythonTruthy(value) {
		return limit, nil
	}
	number, ok := mcpPyNumberValue(value)
	if !ok {
		// min compares MAX < limit.
		return mcpPyNumber{}, mcpRaise("'<' not supported between instances of 'int' and '%s'", pythonTypeName(value))
	}
	if number.isFloat {
		if number.float < float64(maximum) {
			return number, nil
		}
		return limit, nil
	}
	if number.integer.Cmp(limit.integer) < 0 {
		return number, nil
	}
	return limit, nil
}

// mcpPyAdd is Python's offset + effective_limit.
func mcpPyAdd(left any, right mcpPyNumber) (mcpPyNumber, error) {
	number, ok := mcpPyNumberValue(left)
	if ok {
		if number.isFloat || right.isFloat {
			return mcpPyNumber{isFloat: true, float: number.asFloat() + right.asFloat()}, nil
		}
		return mcpPyNumber{integer: new(big.Int).Add(number.integer, right.integer)}, nil
	}
	switch left.(type) {
	case nil:
		return mcpPyNumber{}, mcpRaise("unsupported operand type(s) for +: 'NoneType' and '%s'", right.typeName())
	case string:
		return mcpPyNumber{}, mcpRaise("can only concatenate str (not \"%s\") to str", right.typeName())
	case []any:
		return mcpPyNumber{}, mcpRaise("can only concatenate list (not \"%s\") to list", right.typeName())
	case domain.RequestObject:
		return mcpPyNumber{}, mcpRaise("unsupported operand type(s) for +: 'dict' and '%s'", right.typeName())
	}
	return mcpPyNumber{}, mcpNotPorted("limit or offset type")
}

// mcpPyNumberLess is (offset + effective_limit) < total_count.
func mcpPyNumberLess(number mcpPyNumber, total int64) bool {
	if number.isFloat {
		if value := new(big.Rat).SetFloat64(number.float); value != nil {
			return value.Cmp(new(big.Rat).SetInt64(total)) < 0
		}
		return number.float < float64(total)
	}
	return number.integer.Cmp(big.NewInt(total)) < 0
}

// mcpSliceBounds is the effective slice of execute_query.
type mcpSliceBounds struct {
	limit  *big.Int // SQL LIMIT (high_mark - low_mark)
	offset *big.Int // SQL OFFSET (low_mark)
	// end is offset + effective_limit before int() (has_more).
	end mcpPyNumber
	// empty is a slice whose int() bounds leave nothing to read
	// (Query.set_limits marks the query empty when the marks are equal).
	empty bool
}

// mcpSlice is effective_limit = min(limit or MAX, MAX) and
// qs[offset:offset+effective_limit] (Django's QuerySet.__getitem__ calls
// int() on both bounds).
func mcpSlice(params domain.RequestObject, maximum int64) (mcpSliceBounds, error) {
	effective := mcpPyInt(maximum)
	if value, _ := params.Lookup("limit"); PythonTruthy(value) {
		number, err := mcpEffectiveLimit(value, maximum)
		if err != nil {
			return mcpSliceBounds{}, err
		}
		effective = number
	}
	offsetValue, present := params.Lookup("offset")
	if !present {
		offsetValue = json.Number("0")
	}
	end, err := mcpPyAdd(offsetValue, effective)
	if err != nil {
		return mcpSliceBounds{}, err
	}
	offset, ok := mcpPyNumberValue(offsetValue)
	if !ok {
		return mcpSliceBounds{}, mcpNotPorted("limit or offset type")
	}
	if offset.isNegative() || end.isNegative() {
		return mcpSliceBounds{}, mcpRaise("Negative indexing is not supported.")
	}
	start, err := offset.intValue()
	if err != nil {
		return mcpSliceBounds{}, err
	}
	stop, err := end.intValue()
	if err != nil {
		return mcpSliceBounds{}, err
	}
	// set_limits(low, high) clamps low to high, so a stop at or below the
	// start leaves the two marks equal and the query empty.
	if stop.Cmp(start) <= 0 {
		return mcpSliceBounds{limit: new(big.Int), offset: start, end: end, empty: true}, nil
	}
	return mcpSliceBounds{limit: new(big.Int).Sub(stop, start), offset: start, end: end}, nil
}

// queryFields is _validate_fields_for_model: the value keys, and whether
// values() got explicit fields (no fields: every concrete attname).
func (q *mcpQuery) queryFields(value any) ([]string, bool, error) {
	if !PythonTruthy(value) {
		var names []string
		for _, field := range q.model.Fields {
			if !field.Relation && mcpPathAllowed(q.model, field.Name) {
				names = append(names, field.Name)
			}
		}
		return names, true, nil
	}
	items, err := mcpStrings(value, "split")
	if err != nil {
		return nil, false, err
	}
	var names []string
	seen := map[string]bool{}
	for _, item := range items {
		root := strings.Split(item, "__")[0]
		if !q.model.known(root) || !mcpPathAllowed(q.model, item) {
			continue
		}
		if seen[item] {
			// values() keeps one column per name in first-appearance order.
			continue
		}
		seen[item] = true
		names = append(names, item)
	}
	if len(names) == 0 {
		var attnames []string
		for _, field := range q.model.Fields {
			if field.Concrete {
				attnames = append(attnames, field.Attname)
			}
		}
		return attnames, false, nil
	}
	return names, true, nil
}

// validateJoins is _split_joins and execute_query's
// select_related/prefetch_related calls: values() drops both, but an item
// that is not a string reaches prefetch_related's lookup.split() (except a
// lone None, which clears the prefetch lookups), and a value that is not
// iterable fails the for loop.
func (q *mcpQuery) validateJoins(joins any) error {
	if !PythonTruthy(joins) {
		return nil
	}
	var items []any
	switch typed := joins.(type) {
	case []any:
		items = typed
	case string:
		for _, r := range typed {
			items = append(items, string(r))
		}
	case domain.RequestObject:
		for _, field := range typed {
			items = append(items, field.Name)
		}
	default:
		return mcpRaise("'%s' object is not iterable", pythonTypeName(joins))
	}
	var prefetch []any
	for _, item := range items {
		name, ok := item.(string)
		if !ok {
			prefetch = append(prefetch, item)
			continue
		}
		// _split_joins skips a path that may not be read and routes a
		// readable name to one of the two calls; values() ignores both, but a
		// many-valued lookup still joins prefetch_related's lookup list.
		if !mcpPathAllowed(q.model, name) {
			continue
		}
		field := q.model.getField(name)
		if field == nil || field.Kind == "foreign_key_reverse" || field.Kind == "many_to_many" ||
			field.Kind == "many_to_many_reverse" {
			prefetch = append(prefetch, name)
		}
	}
	if len(prefetch) == 1 && prefetch[0] == nil {
		// prefetch_related(None) clears the lookups.
		return nil
	}
	for _, item := range prefetch {
		if _, ok := item.(string); ok {
			continue
		}
		return mcpAttributeError(item, "split")
	}
	return nil
}

// planQuery is QueryBuilder.execute_query after the filters.
func (q *mcpQuery) planQuery(params domain.RequestObject, config ReportSettings) (ReportPlan, error) {
	if joins, _ := params.Lookup("joins"); PythonTruthy(joins) {
		if err := q.validateJoins(joins); err != nil {
			return ReportPlan{}, err
		}
	}
	if q.compileErr != nil {
		return ReportPlan{}, q.compileErr
	}
	count := q.countStatement()
	orderValue, _ := params.Lookup("order_by")
	orders, err := q.validateOrder(orderValue)
	if err != nil {
		return ReportPlan{}, err
	}
	fieldsValue, _ := params.Lookup("fields")
	names, explicit, err := q.queryFields(fieldsValue)
	if err != nil {
		return ReportPlan{}, err
	}
	labels := mcpChoiceMap(q.model, nil)
	if explicit {
		labels = mcpChoiceMap(q.model, names)
	}
	bounds, err := mcpSlice(params, config.MaxResults)
	if err != nil {
		return ReportPlan{}, err
	}
	var columns []mcpColumnOut
	var selects []string
	for i, name := range names {
		var expr mcpExpr
		if explicit {
			expr, err = q.resolveField(name)
		} else {
			field := q.model.getField(name)
			expr, err = q.targetExpr(&mcpJoinInfo{path: mcpPath{final: field}}, mcpColumnTarget(field), q.base())
		}
		if err != nil {
			return ReportPlan{}, err
		}
		alias := "c" + strconv.Itoa(i)
		columns = append(columns, mcpColumnOut{key: name, expr: expr, alias: alias})
		selects = append(selects, expr.sql+` AS "`+alias+`"`)
	}
	var keys []string
	for i, order := range orders {
		expr, err := q.orderExpr(order)
		if err != nil {
			return ReportPlan{}, err
		}
		reference := expr.sql
		found := false
		for _, column := range columns {
			if column.expr.sql == expr.sql {
				found = true
			}
		}
		if !found {
			selects = append(selects, expr.sql+` AS "__o`+strconv.Itoa(i)+`"`)
		}
		if order.descending {
			reference += " DESC"
		} else {
			reference += " ASC"
		}
		keys = append(keys, reference)
	}
	where := q.whereSQL()
	if bounds.empty {
		// The marks are equal: Django's compiler raises EmptyResultSet and the
		// listing is empty (the same FALSE predicate as an empty filter).
		if where == "" {
			where = ` WHERE FALSE`
		} else {
			where += ` AND FALSE`
		}
	}
	inner := `SELECT DISTINCT ` + strings.Join(selects, ", ") + ` FROM ` + q.from() + where
	if len(keys) > 0 {
		inner += ` ORDER BY ` + strings.Join(keys, ", ")
	}
	if !bounds.empty {
		inner += ` LIMIT ` + bounds.limit.String()
		if bounds.offset.Sign() > 0 {
			inner += ` OFFSET ` + bounds.offset.String()
		}
	}
	statement := mcpWrap(inner, columns, q.args.values)
	return ReportPlan{Statements: []domain.ReportStatement{count, statement},
		Render: func(results [][][]*string) (any, error) {
			total, _ := strconv.ParseInt(*results[0][0][0], 10, 64)
			data := []any{}
			for _, record := range results[1] {
				row := domain.RequestObject{}
				for i, column := range columns {
					value, err := mcpValue(column.expr, record[i])
					if err != nil {
						return nil, err
					}
					row = append(row, domain.RequestField{Name: column.key, Value: value})
				}
				if err := mcpApplyLabels(row, labels); err != nil {
					return nil, err
				}
				data = append(data, row)
			}
			return domain.RequestObject{{Name: "count", Value: json.Number(strconv.FormatInt(total, 10))},
				{Name: "has_more", Value: mcpPyNumberLess(bounds.end, total)}, {Name: "data", Value: data}}, nil
		}}, nil
}

// ---------------------------------------------------------------------------
// get_field_values
// ---------------------------------------------------------------------------

func (q *mcpQuery) planFieldValues(name string, params domain.RequestObject) (ReportPlan, error) {
	labels := mcpChoiceMap(q.model, []string{name})
	counted := q.clone()
	expr, err := counted.resolveField(name)
	if err != nil {
		return ReportPlan{}, err
	}
	if counted.compileErr != nil {
		return ReportPlan{}, counted.compileErr
	}
	total := domain.ReportStatement{SQL: `SELECT COUNT(*)::text FROM (SELECT DISTINCT ` + expr.sql + ` AS "c0" FROM ` + counted.from() +
		counted.whereSQL() + `) subquery`, Args: counted.args.values, Text: 1}
	listed := q.clone()
	expr, err = listed.resolveField(name)
	if err != nil {
		return ReportPlan{}, err
	}
	order, err := listed.orderExpr(mcpOrder{name: name})
	if err != nil {
		return ReportPlan{}, err
	}
	limit := ""
	value, present := params.Lookup("limit")
	switch {
	case !present:
		limit = " LIMIT 100"
	case value == nil:
	default:
		// values_list(...)[:limit]: __getitem__ compares stop < 0 before
		// calling int() on it.
		number, ok := mcpPyNumberValue(value)
		if !ok {
			return ReportPlan{}, mcpRaise("'<' not supported between instances of '%s' and 'int'", pythonTypeName(value))
		}
		if number.isNegative() {
			return ReportPlan{}, mcpRaise("Negative indexing is not supported.")
		}
		stop, err := number.intValue()
		if err != nil {
			return ReportPlan{}, err
		}
		limit = " LIMIT " + stop.String()
	}
	selects := expr.sql + ` AS "c0"`
	if order.sql != expr.sql {
		selects += ", " + order.sql + ` AS "__o0"`
	}
	inner := `SELECT DISTINCT ` + selects + ` FROM ` + listed.from() + listed.whereSQL() + ` ORDER BY ` + order.sql + ` ASC` + limit
	column := mcpColumnOut{key: name, expr: expr, alias: "c0"}
	return ReportPlan{Statements: []domain.ReportStatement{total, mcpWrap(inner, []mcpColumnOut{column}, listed.args.values)},
		Render: func(results [][][]*string) (any, error) {
			items := []any{}
			mapping := labels[name]
			for _, record := range results[1] {
				value, err := mcpValue(expr, record[0])
				if err != nil {
					return nil, err
				}
				switch value.(type) {
				case domain.RequestObject:
					return nil, mcpRaise("unhashable type: 'dict'")
				case []any:
					return nil, mcpRaise("unhashable type: 'list'")
				}
				if label, ok := mcpLabel(mapping, value); ok {
					value = label
				}
				items = append(items, value)
			}
			return domain.RequestObject{{Name: "values", Value: items}, {Name: "total_distinct", Value: json.Number(*results[0][0][0])}}, nil
		}}, nil
}

// ---------------------------------------------------------------------------
// aggregate_data
// ---------------------------------------------------------------------------

type mcpAggregate struct {
	alias, function string
	field           string // "" for Count('*')
	expr            mcpExpr
}

func (a mcpAggregate) sql(argument string) (string, error) {
	if a.field == "" {
		return "COUNT(*)", nil
	}
	// Unsupported argument types are left to PostgreSQL, which answers the
	// same database error Django's statement raises.
	return strings.ToUpper(a.function) + "(" + argument + ")", nil
}

// out is the aggregate's output expression type.
func (a mcpAggregate) out() mcpExpr {
	switch {
	case a.field == "" || a.function == "count":
		return mcpExpr{kind: "int"}
	case a.function == "avg":
		return mcpExpr{kind: "float"}
	}
	return mcpExpr{kind: a.expr.kind}
}

var mcpForbiddenAlias = func(alias string) bool {
	for _, r := range alias {
		if strings.ContainsRune("'`\"[];#", r) || r <= 0x1f || (r >= 0x7f && r <= 0x9f) || PythonIsSpace(r) {
			return true
		}
	}
	return strings.Contains(alias, "--") || strings.Contains(alias, "/*") || strings.Contains(alias, "*/")
}

const mcpAliasError = "Column aliases cannot contain whitespace characters, hashes, control characters, quotation marks, semicolons, or SQL comments."

func (q *mcpQuery) planAggregate(params domain.RequestObject) (ReportPlan, error) {
	value, _ := params.Lookup("aggregations")
	if !PythonTruthy(value) {
		return ReportPlan{Immediate: &ReportToolError{Message: "No aggregations specified"}}, nil
	}
	items, err := pythonIter(value)
	if err != nil {
		return ReportPlan{}, err
	}
	var aggregates []mcpAggregate
	for _, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return ReportPlan{}, mcpAttributeError(item, "get")
		}
		object = PythonDict(object)
		function, present := object.Lookup("function")
		if !present {
			function = ""
		}
		name, ok := function.(string)
		if !ok {
			return ReportPlan{}, mcpAttributeError(function, "lower")
		}
		name = strings.ToLower(name)
		fieldValue, present := object.Lookup("field")
		if !present {
			fieldValue = "*"
		}
		alias, present := object.Lookup("alias")
		if !present {
			// f'{func_name}_{field}': any field value is written with str().
			alias = name + "_" + PythonStr(fieldValue)
		}
		aliasText, ok := alias.(string)
		if !ok {
			return ReportPlan{}, mcpAttributeError(alias, "replace")
		}
		aliasText = strings.ReplaceAll(strings.ReplaceAll(aliasText, ".", "_"), "-", "_")
		if name != "count" && name != "sum" && name != "avg" && name != "min" && name != "max" {
			continue
		}
		fieldText, ok := fieldValue.(string)
		if !ok {
			return ReportPlan{}, mcpAttributeError(fieldValue, "split")
		}
		aggregate := mcpAggregate{alias: aliasText, function: name}
		if !(fieldText == "*" && name == "count") {
			if !mcpPathAllowed(q.model, fieldText) {
				continue
			}
			aggregate.field = fieldText
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
	groupValue, _ := params.Lookup("group_by")
	if !PythonTruthy(groupValue) {
		return q.planFlatAggregate(aggregates)
	}
	groupNames, err := mcpStrings(groupValue, "split")
	if err != nil {
		return ReportPlan{}, err
	}
	var groups []string
	for _, name := range groupNames {
		root := strings.Split(name, "__")[0]
		if !mcpPathAllowed(q.model, name) || !q.model.known(root) {
			continue
		}
		if mcpContains(groups, name) {
			// values() keeps one column per name in first-appearance order.
			continue
		}
		groups = append(groups, name)
	}
	if len(groups) == 0 {
		return ReportPlan{Immediate: &ReportToolError{Message: "No valid group_by fields"}}, nil
	}
	labels := mcpChoiceMap(q.model, groups)
	var columns []mcpColumnOut
	var selects, groupBy []string
	for i, name := range groups {
		expr, err := q.resolveField(name)
		if err != nil {
			return ReportPlan{}, err
		}
		alias := "c" + strconv.Itoa(i)
		columns = append(columns, mcpColumnOut{key: name, expr: expr, alias: alias})
		selects = append(selects, expr.sql+` AS "`+alias+`"`)
		groupBy = append(groupBy, expr.sql)
	}
	for _, aggregate := range aggregates {
		if mcpContains(groups, aggregate.alias) {
			return ReportPlan{}, mcpRaise("The annotation '%s' conflicts with a field on the model.", aggregate.alias)
		}
	}
	for i := range aggregates {
		if mcpForbiddenAlias(aggregates[i].alias) {
			return ReportPlan{}, mcpRaise(mcpAliasError)
		}
		argument := ""
		if aggregates[i].field != "" {
			expr, err := q.resolveField(aggregates[i].field)
			if err != nil {
				return ReportPlan{}, err
			}
			aggregates[i].expr, argument = expr, expr.sql
		}
		sql, err := aggregates[i].sql(argument)
		if err != nil {
			return ReportPlan{}, err
		}
		alias := "a" + strconv.Itoa(i)
		columns = append(columns, mcpColumnOut{key: aggregates[i].alias, expr: aggregates[i].out(), alias: alias})
		selects = append(selects, sql+` AS "`+alias+`"`)
	}
	var keys []string
	for i, name := range groups {
		expr, err := q.orderExpr(mcpOrder{name: name})
		if err != nil {
			return ReportPlan{}, err
		}
		if !mcpContains(groupBy, expr.sql) {
			groupBy = append(groupBy, expr.sql)
			selects = append(selects, expr.sql+` AS "__o`+strconv.Itoa(i)+`"`)
		}
		keys = append(keys, expr.sql+" ASC")
	}
	if q.compileErr != nil {
		return ReportPlan{}, q.compileErr
	}
	inner := `SELECT DISTINCT ` + strings.Join(selects, ", ") + ` FROM ` + q.from() + q.whereSQL() + ` GROUP BY ` +
		strings.Join(groupBy, ", ") + ` ORDER BY ` + strings.Join(keys, ", ")
	return ReportPlan{Statements: []domain.ReportStatement{mcpWrap(inner, columns, q.args.values)},
		Render: func(results [][][]*string) (any, error) {
			data := []any{}
			for _, record := range results[0] {
				row := domain.RequestObject{}
				for i, column := range columns {
					value, err := mcpValue(column.expr, record[i])
					if err != nil {
						return nil, err
					}
					row = append(row, domain.RequestField{Name: column.key, Value: value})
				}
				if err := mcpApplyLabels(row, labels); err != nil {
					return nil, err
				}
				data = append(data, row)
			}
			return domain.RequestObject{{Name: "data", Value: data}, {Name: "count", Value: json.Number(strconv.Itoa(len(data)))}}, nil
		}}, nil
}

// planFlatAggregate is QuerySet.aggregate() on the distinct queryset: the
// referenced columns join the DISTINCT subquery as __colN.
func (q *mcpQuery) planFlatAggregate(aggregates []mcpAggregate) (ReportPlan, error) {
	var selects []string
	for i, column := range q.concreteColumns() {
		selects = append(selects, column+` AS "col`+strconv.Itoa(i+1)+`"`)
	}
	var outer []string
	var columns []mcpColumnOut
	references := 0
	for i := range aggregates {
		if mcpForbiddenAlias(aggregates[i].alias) {
			return ReportPlan{}, mcpRaise(mcpAliasError)
		}
		argument := ""
		if aggregates[i].field != "" {
			expr, err := q.resolveField(aggregates[i].field)
			if err != nil {
				return ReportPlan{}, err
			}
			references++
			name := "__col" + strconv.Itoa(references)
			selects = append(selects, expr.sql+` AS "`+name+`"`)
			aggregates[i].expr, argument = expr, `"`+name+`"`
		}
		sql, err := aggregates[i].sql(argument)
		if err != nil {
			return ReportPlan{}, err
		}
		alias := "a" + strconv.Itoa(i)
		outer = append(outer, sql+` AS "`+alias+`"`)
		columns = append(columns, mcpColumnOut{key: aggregates[i].alias, expr: aggregates[i].out(), alias: alias})
	}
	if q.compileErr != nil {
		return ReportPlan{}, q.compileErr
	}
	inner := `SELECT ` + strings.Join(outer, ", ") + ` FROM (SELECT DISTINCT ` + strings.Join(selects, ", ") + ` FROM ` + q.from() +
		q.whereSQL() + `) subquery`
	return ReportPlan{Statements: []domain.ReportStatement{mcpWrap(inner, columns, q.args.values)},
		Render: func(results [][][]*string) (any, error) {
			row := domain.RequestObject{}
			for i, column := range columns {
				value, err := mcpValue(column.expr, results[0][0][i])
				if err != nil {
					return nil, err
				}
				row = append(row, domain.RequestField{Name: column.key, Value: value})
			}
			return row, nil
		}}, nil
}

// ---------------------------------------------------------------------------
// Values
// ---------------------------------------------------------------------------

// mcpValue is _serialize_value of the column text (nil: None).
func mcpValue(expr mcpExpr, text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	switch expr.kind {
	case "uuid":
		return *text, nil
	case "text":
		return reportUnescape(*text)
	case "int":
		if strings.Contains(*text, ".") {
			number, ok := new(big.Float).SetString(*text)
			if !ok || !number.IsInt() {
				return nil, mcpNotPorted("non-integral integer output")
			}
			integer, _ := number.Int(nil)
			return json.Number(integer.String()), nil
		}
		return json.Number(*text), nil
	case "bool":
		return *text == "true", nil
	case "decimal", "float":
		return reportFloat(*text)
	case "date":
		return *text, nil
	case "datetime":
		return strings.TrimSuffix(*text, ".000000") + "+00:00", nil
	case "time":
		return mcpTimeISO(*text)
	case "json":
		parsed, err := ParseReferenceJSON([]byte(*text))
		if err != nil {
			return nil, err
		}
		if inner, ok := parsed.(string); ok {
			return reportUnescape(inner)
		}
		return parsed, nil
	}
	return nil, mcpNotPorted("output type " + expr.kind)
}

// mcpTimeISO turns PostgreSQL's time text into time.isoformat().
func mcpTimeISO(text string) (any, error) {
	main, fraction, found := strings.Cut(text, ".")
	if len(main) != 8 {
		return nil, mcpNotPorted("time output")
	}
	if !found || strings.Trim(fraction, "0") == "" {
		return main, nil
	}
	return main + "." + (fraction + "000000")[:6], nil
}

func reportFloat(text string) (any, error) {
	number, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return nil, err
	}
	return json.Number(PythonFloatStr(number)), nil
}

// reportUnescape is _serialize_value's html.unescape for a string with '&'.
func reportUnescape(text string) (any, error) {
	if !strings.Contains(text, "&") {
		return text, nil
	}
	return pyhtml.Unescape(text), nil
}

// ---------------------------------------------------------------------------
// Choice labels
// ---------------------------------------------------------------------------

// mcpItalianChoices is ITALIAN_CHOICE_OVERRIDES.
var mcpItalianChoices = map[string][]mcpChoice{
	"Associate.sex":           {{"A", "Altro"}, {"M", "Maschio"}, {"F", "Femmina"}},
	"Course.status_flag":      {{json.Number("1"), "Archiviato"}, {json.Number("2"), "Attivo"}, {json.Number("3"), "Interno"}},
	"Course.course_type":      {{json.Number("1"), "Standard"}, {json.Number("2"), "Quote multiple"}, {json.Number("3"), "Tesseramento"}},
	"CourseSubscription.type": {{json.Number("1"), "Standard"}, {json.Number("2"), "Quote multiple"}, {json.Number("3"), "Tesseramento"}},
	"Payment.type": {{"default", "Predefinito"}, {"cash", "Contanti"}, {"transfer", "Bonifico"}, {"online", "Online"},
		{"sepa-transfer", "Bonifico SEPA"}, {"stripe", "Stripe"}, {"pos", "POS"}},
	"PaymentCategory.type":      {{json.Number("1"), "Istituzionale"}, {json.Number("2"), "Commerciale"}},
	"BalanceSheet.status_flag":  {{json.Number("1"), "Bozza"}, {json.Number("2"), "Approvato"}},
	"AttendanceRegistry.status": {{json.Number("1"), "Bozza"}, {json.Number("2"), "Pubblicato"}},
}

type mcpLabels map[string][]mcpChoice

// mcpChoiceMap is _build_choice_map.
func mcpChoiceMap(model *mcpModel, expressions []string) mcpLabels {
	result := mcpLabels{}
	for _, expression := range expressions {
		current := model
		var target *mcpField
		for _, part := range strings.Split(expression, "__") {
			if mcpDateParts[part] {
				break
			}
			field := current.getField(part)
			if field == nil {
				break
			}
			if field.Relation && field.relatedModel() != nil {
				current = field.relatedModel()
			} else {
				target = field
			}
		}
		if target == nil || (len(target.Choices) == 0 && !target.GroupedChoices) {
			continue
		}
		if override, ok := mcpItalianChoices[current.Name+"."+target.Name]; ok {
			result[expression] = override
		} else if target.GroupedChoices {
			result[expression] = nil // refused on use
		} else {
			result[expression] = target.Choices
		}
	}
	return result
}

// mcpLabel is `raw in mapping` / mapping.get(raw) with Python equality.
func mcpLabel(mapping []mcpChoice, raw any) (string, bool) {
	for i := len(mapping) - 1; i >= 0; i-- {
		if mcpChoiceEqual(mapping[i].Value, raw) {
			return mapping[i].Label, true
		}
	}
	return "", false
}

func mcpChoiceEqual(key, raw any) bool {
	switch k := key.(type) {
	case string:
		text, ok := raw.(string)
		return ok && text == k
	case json.Number:
		var number float64
		switch r := raw.(type) {
		case json.Number:
			parsed, err := strconv.ParseFloat(string(r), 64)
			if err != nil {
				return false
			}
			number = parsed
		case bool:
			if r {
				number = 1
			}
		default:
			return false
		}
		keyNumber, err := strconv.ParseFloat(string(k), 64)
		return err == nil && keyNumber == number
	}
	return false
}

// mcpApplyLabels is _apply_choice_labels on a serialized row.
func mcpApplyLabels(row domain.RequestObject, labels mcpLabels) error {
	for key, mapping := range labels {
		for i := range row {
			if row[i].Name != key {
				continue
			}
			if mapping == nil {
				return mcpNotPorted("grouped choices")
			}
			switch row[i].Value.(type) {
			case domain.RequestObject, []any:
				return mcpNotPorted("unhashable choice lookup")
			}
			if label, ok := mcpLabel(mapping, row[i].Value); ok {
				row[i].Value = label
			}
		}
	}
	return nil
}
