package dto

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
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
	ErrReportNotPorted = errors.New("report tool input not ported")
)

// ReportToolError is a tool result carrying 'error' (the view's 400).
type ReportToolError struct{ Message string }

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
	return PlanMCPTool(tool, params, association, settings)
}

// PlanMCPTool runs QueryBuilder for a bound call (the arguments are the
// function's keyword arguments; required ones are present).
func PlanMCPTool(tool string, params domain.RequestObject, association string, settings func() (ReportSettings, error)) (ReportPlan, error) {
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
			return ReportPlan{}, mcpNotPorted("field_name type")
		}
		if !reportAllowed(strings.Split(name, "__")[0]) {
			return ReportPlan{}, mcpRaise("Field '%s' is not allowed", name)
		}
	}
	archived, _ := params.Lookup("include_archived")
	q, err := mcpBaseQuery(model, entry, association, PythonTruthy(archived))
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
	return nil, mcpNotPorted("iteration of a scalar")
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

// filterLeaves is validate_filter_fields + _coerce_filter_values.
func (q *mcpQuery) filterLeaves(value any) ([]mcpLeaf, error) {
	object, ok := value.(domain.RequestObject)
	if !ok {
		return nil, mcpAttributeError(value, "items")
	}
	var leaves []mcpLeaf
	for _, item := range PythonDict(object) {
		if !reportAllowed(strings.Split(item.Name, "__")[0]) {
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
		if !reportAllowed(strings.Split(clean, "__")[0]) {
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
		next, err := expr.transform(transform)
		if err != nil {
			if info.path.finalIsField() && info.lastErr != nil && !mcpTransformKnown(expr, transform) {
				return expr, info.lastErr
			}
			return expr, err
		}
		expr = next
	}
	return expr, nil
}

// mcpInteger is a limit/offset value (Python ints; integral floats behave
// like ints in slicing).
func mcpInteger(value any) (int64, error) {
	switch v := value.(type) {
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			number, err := strconv.ParseFloat(string(v), 64)
			if err != nil || math.Trunc(number) != number || math.Abs(number) > 1<<40 {
				return 0, mcpNotPorted("non-integral limit or offset")
			}
			return int64(number), nil
		}
		number, err := strconv.ParseInt(string(v), 10, 64)
		if err != nil || number > 1<<40 || number < -(1<<40) {
			return 0, mcpNotPorted("limit or offset out of range")
		}
		return number, nil
	}
	return 0, mcpNotPorted(fmt.Sprintf("limit or offset %T", value))
}

// slice is effective_limit = min(limit or MAX, MAX) and qs[offset:offset+limit].
func mcpSlice(params domain.RequestObject, maximum int64) (int64, int64, error) {
	limit := maximum
	if value, _ := params.Lookup("limit"); PythonTruthy(value) {
		switch value.(type) {
		case string, []any, domain.RequestObject:
			// min(limit, MAX) compares MAX < limit.
			return 0, 0, mcpRaise("'<' not supported between instances of 'int' and '%s'", pythonTypeName(value))
		}
		number, err := mcpInteger(value)
		if err != nil {
			return 0, 0, err
		}
		if number < limit {
			limit = number
		}
	}
	var offset int64
	if value, present := params.Lookup("offset"); present {
		if value == nil {
			return 0, 0, mcpRaise("unsupported operand type(s) for +: 'NoneType' and 'int'")
		}
		number, err := mcpInteger(value)
		if err != nil {
			return 0, 0, err
		}
		offset = number
	}
	if offset < 0 || offset+limit < 0 {
		return 0, 0, mcpRaise("Negative indexing is not supported.")
	}
	if limit < 0 {
		return 0, 0, mcpNotPorted("negative limit")
	}
	return limit, offset, nil
}

// queryFields is _validate_fields_for_model: the value keys, and whether
// values() got explicit fields (no fields: every concrete attname).
func (q *mcpQuery) queryFields(value any) ([]string, bool, error) {
	if !PythonTruthy(value) {
		var names []string
		for _, field := range q.model.Fields {
			if !field.Relation && reportAllowed(field.Name) {
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
		if !q.model.known(root) || !reportAllowed(root) {
			continue
		}
		if seen[item] {
			return nil, false, mcpNotPorted("duplicate field")
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

// planQuery is QueryBuilder.execute_query after the filters.
func (q *mcpQuery) planQuery(params domain.RequestObject, config ReportSettings) (ReportPlan, error) {
	if joins, _ := params.Lookup("joins"); PythonTruthy(joins) {
		// select_related/prefetch_related: values() ignores both.
		if _, err := pythonIter(joins); err != nil {
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
	limit, offset, err := mcpSlice(params, config.MaxResults)
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
	inner := `SELECT DISTINCT ` + strings.Join(selects, ", ") + ` FROM ` + q.from() + q.whereSQL()
	if len(keys) > 0 {
		inner += ` ORDER BY ` + strings.Join(keys, ", ")
	}
	inner += ` LIMIT ` + strconv.FormatInt(limit, 10)
	if offset > 0 {
		inner += ` OFFSET ` + strconv.FormatInt(offset, 10)
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
				{Name: "has_more", Value: offset+limit < total}, {Name: "data", Value: data}}, nil
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
		number, err := mcpInteger(value)
		if err != nil {
			return ReportPlan{}, err
		}
		if number < 0 {
			return ReportPlan{}, mcpRaise("Negative indexing is not supported.")
		}
		limit = " LIMIT " + strconv.FormatInt(number, 10)
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
	switch a.function {
	case "count":
	case "sum", "avg":
		if a.expr.kind != "int" && a.expr.kind != "decimal" && a.expr.kind != "float" {
			return "", fmt.Errorf("%w: PostgreSQL has no SUM/AVG of %s", ErrReportFailed, a.expr.kind) // a database error
		}
	default:
		switch a.expr.kind {
		case "int", "decimal", "float", "text", "date", "datetime":
		default:
			return "", fmt.Errorf("%w: PostgreSQL has no MIN/MAX of %s", ErrReportFailed, a.expr.kind) // a database error
		}
	}
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
			alias = name + "_" + PythonStr(fieldValue)
			if _, isText := fieldValue.(string); !isText {
				return ReportPlan{}, mcpNotPorted("aggregate field type")
			}
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
			if !reportAllowed(strings.Split(fieldText, "__")[0]) {
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
		if !reportAllowed(root) || !q.model.known(root) {
			continue
		}
		if mcpContains(groups, name) {
			return ReportPlan{}, mcpNotPorted("duplicate group_by")
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

var reportEntities = map[string]string{"amp;": "&", "AMP;": "&", "lt;": "<", "LT;": "<", "gt;": ">", "GT;": ">", "quot;": `"`,
	"QUOT;": `"`, "apos;": "'", "nbsp;": " "}

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
