package dto

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// The Django ORM subset QueryBuilder (application/mcp_server/query_builder.py)
// relies on: alias map with Django's join reuse, trimming and promotion,
// filter()/exclude() with Q trees, transforms and lookups, values(),
// order_by() and aggregates. Results are equivalent to the SQL Django builds;
// the SQL keeps Django's shape (joins, DISTINCT, select list) so PostgreSQL
// plans unordered results the same way.

// MCPToolException is an exception raised inside a tool function: the MCP
// server answers {"error": str(e)}, the saved report route its JSON 500.
type MCPToolException struct{ Message string }

func (e *MCPToolException) Error() string        { return e.Message }
func (e *MCPToolException) Is(target error) bool { return target == ErrReportFailed }

func mcpRaise(format string, args ...any) error {
	return &MCPToolException{Message: fmt.Sprintf(format, args...)}
}

// mcpValidationError is str(ValidationError(message)): the list repr.
func mcpValidationError(template string, value string) error {
	message := strings.ReplaceAll(template, "%(value)s", value)
	return &MCPToolException{Message: PythonStr([]any{message})}
}

func mcpNotPorted(what string) error { return fmt.Errorf("%w: %s", ErrReportNotPorted, what) }

// ---------------------------------------------------------------------------
// Alias map
// ---------------------------------------------------------------------------

type mcpJoin struct {
	table, alias, parent, identity string
	nullable, louter               bool
	refs                           int
	lhs, rhs                       string
}

type mcpQuery struct {
	model *mcpModel
	joins []*mcpJoin // alias_map order; joins[0] is the base table
	where []string
	args  *reportArgs
	// compileErr is raised when the SQL is compiled (IsNull.as_sql).
	compileErr error
}

func newMCPQuery(model *mcpModel) *mcpQuery {
	return &mcpQuery{model: model, joins: []*mcpJoin{{table: model.Table, alias: model.Table, refs: 1}}, args: &reportArgs{}}
}

func (q *mcpQuery) clone() *mcpQuery {
	copyQuery := &mcpQuery{model: q.model, where: append([]string(nil), q.where...), compileErr: q.compileErr,
		args: &reportArgs{values: append([]any(nil), q.args.values...)}}
	for _, join := range q.joins {
		duplicate := *join
		copyQuery.joins = append(copyQuery.joins, &duplicate)
	}
	return copyQuery
}

func (q *mcpQuery) base() string { return q.joins[0].alias }

func (q *mcpQuery) get(alias string) *mcpJoin {
	for _, join := range q.joins {
		if join.alias == alias {
			return join
		}
	}
	panic("mcp query: unknown alias " + alias)
}

// tableAlias is Query.table_alias(create=True).
func (q *mcpQuery) tableAlias(table string) string {
	for _, join := range q.joins {
		if join.table == table {
			return "T" + strconv.Itoa(len(q.joins)+1)
		}
	}
	return table
}

// join is Query.join: reuse the last equal join among the reusable aliases
// (nil reuse: all), otherwise a new alias, LOUTER when nullable or below an
// outer join.
func (q *mcpQuery) join(parent string, hop mcpHop, reuse map[string]bool) string {
	identity := parent + "\x00" + hop.identity
	last := ""
	for _, join := range q.joins[1:] {
		if join.identity == identity && (reuse == nil || reuse[join.alias]) {
			last = join.alias
		}
	}
	if last != "" {
		q.get(last).refs++
		return last
	}
	alias := q.tableAlias(hop.table)
	q.joins = append(q.joins, &mcpJoin{table: hop.table, alias: alias, parent: parent, identity: identity,
		nullable: hop.nullable, louter: q.get(parent).louter || hop.nullable, refs: 1, lhs: hop.lhs, rhs: hop.rhs})
	return alias
}

func (q *mcpQuery) promote(aliases []string) {
	for len(aliases) > 0 {
		alias := aliases[0]
		aliases = aliases[1:]
		join := q.get(alias)
		if join.parent == "" {
			continue
		}
		if (join.nullable || q.get(join.parent).louter) && !join.louter {
			join.louter = true
			for _, child := range q.joins {
				if child.parent == alias && !mcpContains(aliases, child.alias) {
					aliases = append(aliases, child.alias)
				}
			}
		}
	}
}

func (q *mcpQuery) demote(aliases []string) {
	for len(aliases) > 0 {
		alias := aliases[0]
		aliases = aliases[1:]
		join := q.get(alias)
		if join.parent != "" && join.louter {
			join.louter = false
			if parent := q.get(join.parent); parent.parent != "" && !parent.louter {
				aliases = append(aliases, parent.alias)
			}
		}
	}
}

func mcpContains(items []string, item string) bool {
	for _, candidate := range items {
		if candidate == item {
			return true
		}
	}
	return false
}

func mcpColumn(alias, column string) string { return `"` + alias + `"."` + column + `"` }

// from renders the FROM clause with the referenced joins.
func (q *mcpQuery) from() string {
	var out strings.Builder
	out.WriteString(`"` + q.joins[0].table + `"`)
	for _, join := range q.joins[1:] {
		if join.refs <= 0 {
			continue
		}
		if join.louter {
			out.WriteString(" LEFT OUTER JOIN ")
		} else {
			out.WriteString(" INNER JOIN ")
		}
		out.WriteString(`"` + join.table + `"`)
		if join.alias != join.table {
			out.WriteString(" " + join.alias)
		}
		out.WriteString(" ON (" + mcpColumn(join.parent, join.lhs) + " = " + mcpColumn(join.alias, join.rhs) + ")")
	}
	return out.String()
}

func (q *mcpQuery) whereSQL() string {
	switch len(q.where) {
	case 0:
		return ""
	case 1:
		return " WHERE " + q.where[0]
	}
	return " WHERE (" + strings.Join(q.where, " AND ") + ")"
}

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

// mcpTarget is a column holding the final field's value.
type mcpTarget struct {
	column string
	// field is the field whose get_prep_value prepares values (its name is the
	// one Django quotes) and whose type renders the column.
	field     *mcpField
	valueType string
	null      bool
}

type mcpHop struct {
	table, identity, lhs, rhs string
	direct, m2m, nullable     bool
	local                     *mcpTarget // the target after trimming a direct hop
}

type mcpPath struct {
	hops   []mcpHop
	final  *mcpField
	target mcpTarget
	rest   []string
}

func mcpFieldTarget(field *mcpField) mcpTarget {
	return mcpTarget{column: field.Column, field: field, valueType: field.Internal, null: field.Null}
}

// mcpColumnTarget is a concrete field's own column (a forward relation's
// stores the target's type).
func mcpColumnTarget(field *mcpField) mcpTarget {
	return mcpTarget{column: field.Column, field: field, valueType: field.valueType(), null: field.Null}
}

// relationHops is the field's path_infos.
func mcpRelationHops(field *mcpField) ([]mcpHop, mcpTarget, error) {
	related := field.relatedModel()
	if related == nil {
		return nil, mcpTarget{}, mcpNotPorted("relation without metadata " + field.Related)
	}
	id := field.model.Label + "." + field.Name
	switch field.Kind {
	case "foreign_key", "one_to_one":
		targetField := related.fieldByColumn(field.TargetColumn)
		if targetField == nil {
			return nil, mcpTarget{}, mcpNotPorted("relation target")
		}
		local := mcpTarget{column: field.Column, field: targetField, valueType: field.TargetInternal, null: field.Null}
		return []mcpHop{{table: related.Table, identity: related.Table + "\x00" + id + ">", lhs: field.Column, rhs: field.TargetColumn,
			direct: true, nullable: field.Null, local: &local}}, mcpFieldTarget(targetField), nil
	case "foreign_key_reverse", "one_to_one_reverse":
		return []mcpHop{{table: related.Table, identity: related.Table + "\x00" + id + "<", lhs: field.LocalColumn, rhs: field.RemoteColumn,
			m2m: field.Kind == "foreign_key_reverse", nullable: true}}, mcpFieldTarget(related.pkField()), nil
	case "many_to_many", "many_to_many_reverse":
		pk := related.pkField()
		local := mcpTarget{column: field.TargetColumn, field: pk, valueType: pk.Internal}
		return []mcpHop{
			{table: field.Through, identity: field.Through + "\x00" + id + "<m2m", lhs: field.LocalColumn, rhs: field.SourceColumn, m2m: true, nullable: true},
			{table: related.Table, identity: related.Table + "\x00" + id + ">m2m", lhs: field.TargetColumn, rhs: field.RemoteColumn, direct: true, local: &local},
		}, mcpFieldTarget(pk), nil
	}
	return nil, mcpTarget{}, mcpNotPorted("relation kind " + field.Kind)
}

// errMCPMultiJoin is names_to_path's MultiJoin (exclude over a many-valued
// relation: split_exclude's subquery is not ported).
var errMCPMultiJoin = mcpNotPorted("exclude across a many-valued relation")

// namesToPath is Query.names_to_path.
func mcpNamesToPath(model *mcpModel, names []string, allowMany, failOnMissing bool) (mcpPath, error) {
	var path mcpPath
	opts := model
	consumed := 0
	for pos := 0; pos < len(names); pos++ {
		name := names[pos]
		if name == "pk" {
			name = opts.PK
		}
		field := opts.getField(name)
		if field == nil {
			if pos == 0 || failOnMissing {
				return path, mcpRaise("Cannot resolve keyword '%s' into field. Choices are: %s", name, opts.choiceNames())
			}
			break
		}
		if field.Kind == "unsupported" {
			return path, mcpNotPorted("field kind of " + name)
		}
		consumed = pos + 1
		if field.Relation {
			hops, target, err := mcpRelationHops(field)
			if err != nil {
				return path, err
			}
			if !allowMany {
				for _, hop := range hops {
					if hop.m2m {
						return path, errMCPMultiJoin
					}
				}
			}
			path.hops = append(path.hops, hops...)
			path.final, path.target = field, target
			opts = field.relatedModel()
			continue
		}
		path.final, path.target = field, mcpFieldTarget(field)
		if failOnMissing && pos+1 != len(names) {
			var quoted strings.Builder
			pythonStringRepr(&quoted, names[pos+1])
			return path, mcpRaise("Cannot resolve keyword %s into field. Join on '%s' not permitted.", quoted.String(), name)
		}
		break
	}
	path.rest = names[consumed:]
	return path, nil
}

type mcpJoinInfo struct {
	path       mcpPath
	joins      []string
	transforms []string
	lastErr    error
}

// setupJoins is Query.setup_joins; reuse nil means every alias is reusable
// (values, ordering, annotations), otherwise many-valued hops reuse only the
// aliases of the current filter() call.
func (q *mcpQuery) setupJoins(names []string, reuse map[string]bool, allowMany, failOnMissing bool) (*mcpJoinInfo, error) {
	info := &mcpJoinInfo{}
	if failOnMissing {
		resolved := false
		for pivot := len(names); pivot >= 1; pivot-- {
			path, err := mcpNamesToPath(q.model, names[:pivot], allowMany, true)
			if err != nil {
				if pivot == 1 || errors.Is(err, ErrReportNotPorted) {
					return nil, err
				}
				info.lastErr = err
				continue
			}
			info.path, info.transforms, resolved = path, names[pivot:], true
			break
		}
		if !resolved {
			return nil, info.lastErr
		}
	} else {
		path, err := mcpNamesToPath(q.model, names, allowMany, false)
		if err != nil {
			return nil, err
		}
		info.path, info.transforms = path, path.rest
	}
	alias := q.base()
	info.joins = []string{alias}
	for _, hop := range info.path.hops {
		hopReuse := reuse
		if !hop.m2m {
			hopReuse = nil
		}
		alias = q.join(alias, hop, hopReuse)
		info.joins = append(info.joins, alias)
	}
	return info, nil
}

// trimJoins is Query.trim_joins.
func (q *mcpQuery) trimJoins(info *mcpJoinInfo) (mcpTarget, string, []string) {
	target := info.path.target
	joins := append([]string(nil), info.joins...)
	for i := len(info.path.hops) - 1; i >= 0; i-- {
		hop := info.path.hops[i]
		if len(joins) == 1 || !hop.direct || target.column != hop.rhs {
			break
		}
		target = *hop.local
		q.get(joins[len(joins)-1]).refs--
		joins = joins[:len(joins)-1]
	}
	return target, joins[len(joins)-1], joins
}

// finalIsField is isinstance(final_field, Field): reverse relations are not.
func (p mcpPath) finalIsField() bool {
	return p.final != nil && p.final.Kind != "foreign_key_reverse" && p.final.Kind != "one_to_one_reverse" &&
		p.final.Kind != "many_to_many_reverse"
}

// ---------------------------------------------------------------------------
// Expressions
// ---------------------------------------------------------------------------

// mcpExpr is a resolved column or transform.
type mcpExpr struct {
	sql      string
	kind     string // value category, see mcpKind
	field    *mcpField
	relation bool   // a relation: Django's Related* lookups
	relClass string // the relation field's class (ForeignKey, ManyToOneRel, ...)
	extract  string // the date transform applied last, if any
	column   string // the untransformed column (YearLookup bounds)
	colKind  string
	jsonKey  bool
	// jsonCol and jsonKeys are the JSONField expression a key transform reads
	// and the keys applied to it (KeyTransform.preprocess_lhs); jsonArg is the
	// parameter holding the keys (a deeper transform reuses it).
	jsonCol  string
	jsonKeys []string
	jsonArg  int
	null     bool
	louter   bool
}

// mcpKind maps a Django internal type to the value category the port
// supports ("" when not supported).
func mcpKind(internal string) string {
	switch internal {
	case "AutoField", "BigAutoField", "SmallAutoField", "IntegerField", "BigIntegerField", "SmallIntegerField",
		"PositiveIntegerField", "PositiveSmallIntegerField", "PositiveBigIntegerField":
		return "int"
	case "UUIDField":
		return "uuid"
	case "CharField", "TextField", "EmailField", "URLField", "SlugField":
		return "text"
	case "BooleanField":
		return "bool"
	case "DecimalField":
		return "decimal"
	case "FloatField":
		return "float"
	case "DateField":
		return "date"
	case "DateTimeField":
		return "datetime"
	case "TimeField":
		return "time"
	case "JSONField":
		return "json"
	}
	return ""
}

// mcpIntRange is the column range of an integer type (Django 5 matches
// nothing out of range; those lookups are not ported).
func mcpIntRange(internal string) (int64, int64) {
	switch internal {
	case "SmallIntegerField", "SmallAutoField":
		return -32768, 32767
	case "PositiveSmallIntegerField":
		return 0, 32767
	case "IntegerField", "AutoField":
		return -2147483648, 2147483647
	case "PositiveIntegerField":
		return 0, 2147483647
	case "PositiveBigIntegerField":
		return 0, 9223372036854775807
	}
	return -9223372036854775808, 9223372036854775807
}

func (q *mcpQuery) targetExpr(info *mcpJoinInfo, target mcpTarget, alias string) (mcpExpr, error) {
	kind := mcpKind(target.valueType)
	if kind == "" {
		return mcpExpr{}, mcpNotPorted("field type " + target.valueType)
	}
	column := mcpColumn(alias, target.column)
	expr := mcpExpr{sql: column, kind: kind, field: target.field, relation: info.path.final.Relation,
		relClass: info.path.final.Class, column: column, colKind: kind, null: target.null,
		louter: q.get(alias).louter}
	if kind == "json" {
		expr.jsonCol = column
	}
	return expr, nil
}

// mcpDateTransforms are the extract transforms DateField and DateTimeField
// register in django/db/models/functions/datetime.py.
var mcpDateTransforms = map[string]bool{"year": true, "month": true, "day": true, "week": true, "week_day": true,
	"iso_week_day": true, "quarter": true}

// errMCPNoTransform marks a name that is not a transform of the expression
// (Django raises FieldError and build_lookup tries the name as a lookup).
var errMCPNoTransform = errors.New("mcp query: no such transform")

// tryTransform is Query.try_transform for the supported transforms.
func (q *mcpQuery) tryTransform(e mcpExpr, name string) (mcpExpr, error) {
	if e.relation {
		return e, errMCPNoTransform
	}
	switch e.kind {
	case "date", "datetime":
		source := e.sql
		if e.kind == "datetime" {
			source = e.sql + " AT TIME ZONE 'UTC'"
		}
		switch {
		case name == "date" && e.kind == "datetime":
			return mcpExpr{sql: "(" + source + ")::date", kind: "date", extract: "date", column: e.sql, colKind: e.kind,
				null: e.null, louter: e.louter}, nil
		case name == "time" && e.kind == "datetime":
			return mcpExpr{sql: "(" + source + ")::time", kind: "time", extract: "time", column: e.sql, colKind: e.kind,
				null: e.null, louter: e.louter}, nil
		case name == "iso_year":
			return mcpExpr{sql: "EXTRACT(ISOYEAR FROM " + source + ")", kind: "int", extract: name, column: e.sql,
				colKind: e.kind, null: e.null, louter: e.louter}, nil
		case name == "year" || mcpDateTransforms[name]:
			sql := "EXTRACT(" + strings.ToUpper(name) + " FROM " + source + ")"
			switch name {
			case "week_day":
				sql = "EXTRACT(DOW FROM " + source + ") + 1"
			case "iso_week_day":
				sql = "EXTRACT(ISODOW FROM " + source + ")"
			}
			column, colKind := e.column, e.colKind
			if name == "year" {
				// Year lookups filter the original expression (YearExact et al.).
				column, colKind = e.sql, e.kind
			}
			return mcpExpr{sql: sql, kind: "int", extract: name, column: column, colKind: colKind, null: e.null,
				louter: e.louter}, nil
		case (name == "hour" || name == "minute" || name == "second") && e.kind == "datetime":
			sql := "EXTRACT(" + strings.ToUpper(name) + " FROM " + source + ")"
			if name == "second" {
				sql = "EXTRACT(SECOND FROM DATE_TRUNC('second', " + source + "))"
			}
			return mcpExpr{sql: sql, kind: "int", extract: name, column: e.column, colKind: e.colKind, null: e.null,
				louter: e.louter}, nil
		}
		return e, errMCPNoTransform
	case "time":
		switch name {
		case "hour", "minute", "second":
			sql := "EXTRACT(" + strings.ToUpper(name) + " FROM " + e.sql + ")"
			if name == "second" {
				sql = "EXTRACT(SECOND FROM DATE_TRUNC('second', " + e.sql + "))"
			}
			return mcpExpr{sql: sql, kind: "int", extract: name, column: e.column, colKind: e.colKind, null: e.null,
				louter: e.louter}, nil
		}
		return e, errMCPNoTransform
	case "json":
		// JSONField.get_transform returns KeyTransformFactory(name) for any
		// name, so every name after a JSON field is another key of any depth.
		keys := append(append([]string(nil), e.jsonKeys...), name)
		next := mcpExpr{kind: "json", jsonKey: true, jsonCol: e.jsonCol, jsonKeys: keys, column: e.column,
			colKind: e.kind, null: e.null, louter: e.louter, field: e.field, jsonArg: e.jsonArg}
		sql, arg, err := q.jsonKeySQL(next, false)
		if err != nil {
			return e, err
		}
		next.sql, next.jsonArg = sql, arg
		return next, nil
	}
	return e, errMCPNoTransform
}

// jsonKeySQL is KeyTransform.as_postgresql: one key uses -> (->> for the text
// transform), several keys use #> (#>>) over the whole key list. It returns
// the placeholder bound to the key and its position: a transform applied on
// an existing key reuses that position, whose value the deeper key list
// replaces (the intermediate expression is never compiled).
func (q *mcpQuery) jsonKeySQL(e mcpExpr, text bool) (string, int, error) {
	nested, single := "#>", "->"
	if text {
		nested, single = "#>>", "->>"
	}
	argument := func(value any) (string, int) {
		if e.jsonArg > 0 && e.jsonArg <= len(q.args.values) {
			q.args.values[e.jsonArg-1] = value
			return "$" + strconv.Itoa(e.jsonArg), e.jsonArg
		}
		return q.args.add(value), len(q.args.values)
	}
	if len(e.jsonKeys) > 1 {
		keys := append([]string(nil), e.jsonKeys...)
		placeholder, index := argument(keys)
		return "(" + e.jsonCol + " " + nested + " " + placeholder + "::text[])", index, nil
	}
	key := e.jsonKeys[0]
	if number, ok := PythonIntText(key); ok {
		if !number.IsInt64() {
			return "", 0, mcpNotPorted("JSON key index outside 64 bits")
		}
		cast := "::int"
		if value := number.Int64(); value < math.MinInt32 || value > math.MaxInt32 {
			cast = "::bigint"
		}
		placeholder, index := argument(number.Int64())
		return "(" + e.jsonCol + " " + single + " " + placeholder + cast + ")", index, nil
	}
	placeholder, index := argument(key)
	return "(" + e.jsonCol + " " + single + " " + placeholder + ")", index, nil
}

// jsonTextExpr is KeyTransformTextLookupMixin: a text lookup on a key reads
// the key through KeyTextTransform (->> / #>>). A whole column keeps its own
// expression; the pattern lookup casts it to text itself.
func (q *mcpQuery) jsonTextExpr(e mcpExpr) (mcpExpr, error) {
	text := mcpExpr{sql: e.sql, kind: "text", column: e.column, colKind: e.kind, null: e.null, louter: e.louter}
	if !e.jsonKey {
		return text, nil
	}
	sql, arg, err := q.jsonKeySQL(e, true)
	if err != nil {
		return e, err
	}
	text.sql, text.jsonArg = sql, arg
	return text, nil
}

// fieldClass is the output field class Django names in FieldError messages.
func (e mcpExpr) fieldClass() string {
	if e.relation && e.relClass != "" {
		return e.relClass
	}
	if e.extract != "" {
		switch e.kind {
		case "int":
			return "IntegerField"
		case "date":
			return "DateField"
		case "time":
			return "TimeField"
		}
	}
	if e.field != nil && e.field.Class != "" {
		return e.field.Class
	}
	switch e.kind {
	case "int":
		return "IntegerField"
	case "text":
		return "CharField"
	case "uuid":
		return "UUIDField"
	case "bool":
		return "BooleanField"
	case "decimal":
		return "DecimalField"
	case "float":
		return "FloatField"
	case "date":
		return "DateField"
	case "datetime":
		return "DateTimeField"
	case "time":
		return "TimeField"
	case "json":
		return "JSONField"
	}
	return "Field"
}

// Lookup names registered in Django, in class_lookups order: Field's own
// lookups in django/db/models/lookups.py, then the transforms registered on
// DateField, DateTimeField and TimeField in functions/datetime.py, the
// ForeignObject lookups in fields/related.py, and JSONField's in fields/json.py.
var (
	mcpBaseLookupNames = []string{"exact", "iexact", "gt", "gte", "lt", "lte", "in", "contains", "icontains",
		"startswith", "istartswith", "endswith", "iendswith", "range", "isnull", "regex", "iregex"}
	mcpDateLookupNames = append(append([]string(nil), mcpBaseLookupNames...), "year", "month", "day", "week_day",
		"iso_week_day", "week", "iso_year", "quarter")
	mcpDateTimeLookupNames = append(append([]string(nil), mcpDateLookupNames...), "hour", "minute", "second", "date", "time")
	mcpTimeLookupNames     = append(append([]string(nil), mcpBaseLookupNames...), "hour", "minute", "second")
	mcpRelatedLookupNames  = []string{"in", "exact", "lt", "gt", "gte", "lte", "isnull"}
	mcpJSONLookupNames     = append(append([]string(nil), mcpBaseLookupNames...), "contained_by", "has_key", "has_keys",
		"has_any_keys")
	mcpJSONKeyLookupNames = []string{"in", "exact", "iexact", "isnull", "icontains", "startswith", "istartswith",
		"endswith", "iendswith", "regex", "iregex", "lt", "lte", "gt", "gte"}
)

func (e mcpExpr) lookupCandidates() []string {
	switch {
	case e.relation:
		return mcpRelatedLookupNames
	case e.jsonKey:
		return mcpJSONKeyLookupNames
	}
	if e.extract != "" {
		switch e.kind {
		case "date":
			return mcpDateLookupNames
		case "time":
			return mcpTimeLookupNames
		}
		return mcpBaseLookupNames
	}
	switch e.kind {
	case "json":
		return mcpJSONLookupNames
	case "date":
		return mcpDateLookupNames
	case "datetime":
		return mcpDateTimeLookupNames
	case "time":
		return mcpTimeLookupNames
	}
	return mcpBaseLookupNames
}

// unsupportedLookup is Query.try_transform's FieldError, with difflib's
// suggestion among the output field's lookup names.
func (e mcpExpr) unsupportedLookup(name string, remaining []string) error {
	unsupported := name
	if remaining != nil {
		unsupported = strings.Join(remaining, "__")
	}
	suggestion := "."
	if matches := mcpCloseMatches(name, e.lookupCandidates()); len(matches) > 0 {
		suggestion = ", perhaps you meant " + strings.Join(matches, " or ") + "?"
	}
	return mcpRaise("Unsupported lookup '%s' for %s or join on the field not permitted%s", unsupported, e.fieldClass(),
		suggestion)
}

// mcpCloseMatches is difflib.get_close_matches(word, possibilities) with the
// default n=3 and cutoff=0.6 (SequenceMatcher.ratio over short names).
func mcpCloseMatches(word string, possibilities []string) []string {
	type scored struct {
		ratio float64
		word  string
	}
	var scoredMatches []scored
	for _, candidate := range possibilities {
		ratio := mcpSequenceRatio(candidate, word)
		if ratio >= 0.6 {
			scoredMatches = append(scoredMatches, scored{ratio, candidate})
		}
	}
	sort.SliceStable(scoredMatches, func(i, j int) bool {
		if scoredMatches[i].ratio != scoredMatches[j].ratio {
			return scoredMatches[i].ratio > scoredMatches[j].ratio
		}
		return scoredMatches[i].word > scoredMatches[j].word
	})
	if len(scoredMatches) > 3 {
		scoredMatches = scoredMatches[:3]
	}
	matches := make([]string, len(scoredMatches))
	for i, match := range scoredMatches {
		matches[i] = match.word
	}
	return matches
}

// mcpSequenceRatio is SequenceMatcher.ratio: 2 * matching blocks / total length.
func mcpSequenceRatio(a, b string) float64 {
	total := len(a) + len(b)
	if total == 0 {
		return 1
	}
	return 2 * float64(mcpMatchingBlocks(a, b, 0, len(a), 0, len(b))) / float64(total)
}

// mcpMatchingBlocks sums the longest matching blocks (find_longest_match with
// no junk elements, recursing left and right of each block).
func mcpMatchingBlocks(a, b string, alo, ahi, blo, bhi int) int {
	besti, bestj, bestsize := alo, blo, 0
	for i := alo; i < ahi; i++ {
		for j := blo; j < bhi; j++ {
			size := 0
			for i+size < ahi && j+size < bhi && a[i+size] == b[j+size] {
				size++
			}
			if size > bestsize {
				besti, bestj, bestsize = i, j, size
			}
		}
	}
	if bestsize == 0 {
		return 0
	}
	return bestsize + mcpMatchingBlocks(a, b, alo, besti, blo, bestj) +
		mcpMatchingBlocks(a, b, besti+bestsize, ahi, bestj+bestsize, bhi)
}

func mcpDigits(text string) bool {
	if text == "" {
		return false
	}
	text = strings.TrimPrefix(text, "-")
	for _, r := range text {
		if r < '0' || r > '9' {
			return false
		}
	}
	return text != ""
}

// resolveField is add_fields/resolve_ref for one field expression: joins
// (reusing any alias), trimming, transforms.
func (q *mcpQuery) resolveField(name string) (mcpExpr, error) {
	info, err := q.setupJoins(strings.Split(name, "__"), nil, true, true)
	if err != nil {
		return mcpExpr{}, err
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
			// when the final field is a Field (add_fields, ordering).
			if errors.Is(err, errMCPNoTransform) && info.path.finalIsField() && info.lastErr != nil {
				return expr, info.lastErr
			}
			return expr, err
		}
		expr = next
	}
	return expr, nil
}

// ---------------------------------------------------------------------------
// Filters: Q trees, build_filter and join promotion
// ---------------------------------------------------------------------------

type mcpLeaf struct {
	key   string
	value any
}

type mcpNode struct {
	or, negated bool
	children    []any // mcpLeaf or *mcpNode
}

// mcpQ is Q(**kwargs): the children sorted by key.
func mcpQ(items []mcpLeaf) *mcpNode {
	sorted := append([]mcpLeaf(nil), items...)
	sort.SliceStable(sorted, func(i, j int) bool { return sorted[i].key < sorted[j].key })
	node := &mcpNode{}
	for _, item := range sorted {
		node.children = append(node.children, item)
	}
	return node
}

// addQ is Query.add_q for one filter()/exclude() call.
func (q *mcpQuery) addQ(node *mcpNode) error {
	var existingInner []string
	for _, join := range q.joins[1:] {
		if !join.louter {
			existingInner = append(existingInner, join.alias)
		}
	}
	clause, _, result, err := q.addQInner(node, map[string]bool{}, false, false, false)
	if err != nil {
		return err
	}
	switch {
	case clause != "":
		q.where = append(q.where, clause)
	case result == mcpResultEmpty:
		// The whole filter matches nothing: keep the statement valid and
		// rowless (an EmptyResultSet in Django skips the query entirely).
		q.where = append(q.where, "FALSE")
	}
	q.demote(existingInner)
	return nil
}

func (q *mcpQuery) addQInner(node *mcpNode, used map[string]bool, branchNegated, currentNegated, branchOR bool) (string, []string, mcpResultKind, error) {
	argsStart := len(q.args.values)
	currentNegated = currentNegated != node.negated
	branchNegated = branchNegated || node.negated
	branchOR = branchOR || node.or
	effectiveOR := node.or
	if currentNegated {
		effectiveOR = !node.or
	}
	votes := map[string]int{}
	var order []string
	var parts []string
	var kinds []mcpResultKind
	for _, child := range node.children {
		var clause string
		var needed []string
		var result mcpResultKind
		var err error
		switch typed := child.(type) {
		case mcpLeaf:
			clause, needed, result, err = q.buildFilter(typed, used, branchNegated, currentNegated, branchOR)
		case *mcpNode:
			clause, needed, result, err = q.addQInner(typed, used, branchNegated, currentNegated, branchOR)
		}
		if err != nil {
			return "", nil, mcpResultValue, err
		}
		seen := map[string]bool{}
		for _, alias := range needed {
			if seen[alias] {
				continue
			}
			seen[alias] = true
			if _, ok := votes[alias]; !ok {
				order = append(order, alias)
			}
			votes[alias]++
		}
		parts = append(parts, clause)
		kinds = append(kinds, result)
	}
	var promote, demote []string
	for _, alias := range order {
		count := votes[alias]
		if effectiveOR && count < len(node.children) {
			promote = append(promote, alias)
		}
		if !effectiveOR || count == len(node.children) {
			demote = append(demote, alias)
		}
	}
	q.promote(promote)
	q.demote(demote)
	// WhereNode.as_sql: an EmptyResultSet child of an OR is dropped, a
	// FullResultSet child of an AND is dropped, and a node all of whose
	// children are empty (OR) or full (AND) is itself empty/full. Negation
	// swaps the two outcomes.
	fullNeeded, emptyNeeded := len(node.children), 1
	if node.or {
		fullNeeded, emptyNeeded = 1, len(node.children)
	}
	var sqlParts []string
	result := mcpResultValue
	for i, kind := range kinds {
		switch kind {
		case mcpResultEmpty:
			emptyNeeded--
		case mcpResultFull:
			fullNeeded--
		default:
			if parts[i] != "" {
				sqlParts = append(sqlParts, parts[i])
			} else {
				fullNeeded--
			}
		}
		if emptyNeeded == 0 {
			if node.negated {
				result = mcpResultFull
			} else {
				result = mcpResultEmpty
			}
			break
		}
		if fullNeeded == 0 {
			if node.negated {
				result = mcpResultEmpty
			} else {
				result = mcpResultFull
			}
			break
		}
	}
	if result != mcpResultValue {
		// Django drops the whole node's SQL (and its parameters) when it
		// compiles to EmptyResultSet or FullResultSet.
		q.args.values = q.args.values[:argsStart]
		return "", demote, result, nil
	}
	if len(sqlParts) == 0 {
		q.args.values = q.args.values[:argsStart]
		return "", demote, mcpResultFull, nil
	}
	connector := " AND "
	if node.or {
		connector = " OR "
	}
	clause := strings.Join(sqlParts, connector)
	if node.negated {
		clause = "NOT (" + clause + ")"
	} else if len(sqlParts) > 1 {
		clause = "(" + clause + ")"
	}
	return clause, demote, mcpResultValue, nil
}

// buildFilter is Query.build_filter for key=value.
func (q *mcpQuery) buildFilter(leaf mcpLeaf, used map[string]bool, branchNegated, currentNegated, branchOR bool) (string, []string, mcpResultKind, error) {
	if leaf.key == "" {
		return "", nil, mcpResultValue, mcpRaise("Cannot parse keyword query ''")
	}
	info, err := q.setupJoins(strings.Split(leaf.key, "__"), used, !branchNegated, false)
	if err != nil {
		return "", nil, mcpResultValue, err
	}
	usedJoins := append([]string(nil), info.joins...)
	target, alias, remaining := q.trimJoins(info)
	for _, join := range remaining {
		used[join] = true
	}
	lhs, err := q.targetExpr(info, target, alias)
	if err != nil {
		return "", nil, mcpResultValue, err
	}
	lookup, err := q.buildLookup(info.transforms, lhs, leaf.value)
	if err != nil {
		return "", nil, mcpResultValue, err
	}
	if lookup.result != mcpResultValue {
		// IntegerFieldOverflow raises while the condition compiles; the node
		// (including its negation) decides the outcome, not the leaf.
		return "", usedJoins, lookup.result, nil
	}
	condition := lookup.sql
	requireOuter := lookup.name == "isnull" && lookup.isnullTrue && !currentNegated
	if currentNegated && (lookup.name != "isnull" || !lookup.isnullTrue) {
		requireOuter = true
		if lookup.name != "isnull" && (target.null || q.get(alias).louter) {
			condition = condition + " AND " + mcpColumn(alias, target.column) + " IS NOT NULL"
		}
	}
	if requireOuter {
		return condition, nil, mcpResultValue, nil
	}
	return condition, usedJoins, mcpResultValue, nil
}

// mcpResultKind is the outcome of compiling a condition: a value, or the
// EmptyResultSet/FullResultSet WhereNode.as_sql raises per node.
type mcpResultKind int

const (
	mcpResultValue mcpResultKind = iota
	mcpResultEmpty
	mcpResultFull
)

type mcpLookup struct {
	name       string
	sql        string
	isnullTrue bool
	result     mcpResultKind
}

var mcpRelatedLookups = map[string]bool{"exact": true, "in": true, "isnull": true, "gt": true, "gte": true, "lt": true, "lte": true}

var mcpFieldLookups = map[string]bool{"exact": true, "iexact": true, "gt": true, "gte": true, "lt": true, "lte": true, "in": true,
	"contains": true, "icontains": true, "startswith": true, "istartswith": true, "endswith": true, "iendswith": true,
	"range": true, "isnull": true, "regex": true, "iregex": true}

var mcpJSONLookups = map[string]bool{"exact": true, "isnull": true, "has_key": true, "has_keys": true, "has_any_keys": true,
	"contains": true, "contained_by": true, "icontains": true, "iexact": true, "startswith": true, "istartswith": true,
	"endswith": true, "iendswith": true, "regex": true, "iregex": true, "in": true, "range": true, "lt": true, "lte": true,
	"gt": true, "gte": true}

func (e mcpExpr) hasLookup(name string) bool {
	switch {
	case e.relation:
		return mcpRelatedLookups[name]
	case e.kind == "json":
		return mcpJSONLookups[name]
	}
	return mcpFieldLookups[name]
}

// buildLookup is Query.build_lookup.
func (q *mcpQuery) buildLookup(lookups []string, lhs mcpExpr, value any) (mcpLookup, error) {
	name := "exact"
	if len(lookups) > 0 {
		name = lookups[len(lookups)-1]
		for i, transform := range lookups[:len(lookups)-1] {
			next, err := q.tryTransform(lhs, transform)
			if errors.Is(err, errMCPNoTransform) {
				return mcpLookup{}, lhs.unsupportedLookup(transform, lookups[i:])
			}
			if err != nil {
				return mcpLookup{}, err
			}
			lhs = next
		}
	}
	if !lhs.hasLookup(name) {
		next, err := q.tryTransform(lhs, name)
		switch {
		case errors.Is(err, errMCPNoTransform):
			return mcpLookup{}, lhs.unsupportedLookup(name, nil)
		case err != nil:
			return mcpLookup{}, err
		}
		lhs, name = next, "exact"
	}
	return q.lookupSQL(lhs, name, value)
}

func (q *mcpQuery) lookupSQL(lhs mcpExpr, name string, value any) (mcpLookup, error) {
	if lhs.kind == "json" {
		return q.jsonLookup(lhs, name, value)
	}
	if name == "isnull" {
		flag, ok := value.(bool)
		if !ok {
			if value == nil {
				return mcpLookup{}, mcpRaise("Cannot use None as a query value")
			}
			if q.compileErr == nil {
				q.compileErr = mcpRaise("The QuerySet value for an isnull lookup must be True or False.")
			}
			flag = PythonTruthy(value)
		}
		if lhs.jsonKey {
			return mcpLookup{}, mcpNotPorted("JSON key isnull")
		}
		if flag {
			return mcpLookup{name: name, sql: lhs.sql + " IS NULL", isnullTrue: true}, nil
		}
		return mcpLookup{name: name, sql: lhs.sql + " IS NOT NULL"}, nil
	}
	if name == "has_key" {
		key, ok := value.(string)
		if !ok {
			return mcpLookup{}, mcpNotPorted("has_key value")
		}
		return mcpLookup{name: name, sql: lhs.sql + " ? " + q.args.add(key)}, nil
	}
	if lhs.jsonKey {
		if value == nil {
			return mcpLookup{}, mcpNotPorted("JSON key None")
		}
		encoded, ok := mcpJSONScalar(value)
		if !ok {
			return mcpLookup{}, mcpNotPorted("JSON key value")
		}
		return mcpLookup{name: name, sql: lhs.sql + " = " + q.args.add(encoded) + "::jsonb"}, nil
	}
	if value == nil {
		if name == "exact" || name == "iexact" {
			return mcpLookup{name: "isnull", sql: lhs.sql + " IS NULL", isnullTrue: true}, nil
		}
		if name == "in" || name == "range" {
			return mcpLookup{}, mcpRaise("'NoneType' object is not iterable")
		}
		return mcpLookup{}, mcpRaise("Cannot use None as a query value")
	}
	switch name {
	case "in", "range":
		items, err := mcpLookupItems(value)
		if err != nil {
			return mcpLookup{}, err
		}
		prepared := make([]mcpPrepared, 0, len(items))
		for _, item := range items {
			value, err := q.prepare(lhs, item, false)
			if err != nil {
				return mcpLookup{}, err
			}
			prepared = append(prepared, value)
		}
		if name == "in" {
			// In.process_rhs: OrderedSet(discard None), first-seen order; an
			// empty set is an EmptyResultSet.
			var placeholders []string
			seen := map[string]bool{}
			for _, value := range prepared {
				if value.isNull {
					continue
				}
				argument, err := mcpPreparedArgument(value)
				if err != nil {
					return mcpLookup{}, err
				}
				key := fmt.Sprint(argument)
				if value.integer != nil {
					key = "i" + value.integer.String()
				}
				if seen[key] {
					continue
				}
				seen[key] = true
				placeholders = append(placeholders, q.args.add(argument)+mcpPreparedCast(lhs, value))
			}
			if len(placeholders) == 0 {
				return mcpLookup{name: name, result: mcpResultEmpty}, nil
			}
			return mcpLookup{name: name, sql: lhs.sql + " IN (" + strings.Join(placeholders, ", ") + ")"}, nil
		}
		// Range.get_rhs_op indexes rhs[0], rhs[1]; a third value leaves one
		// placeholder unfilled when the driver formats the statement.
		if len(prepared) < 2 {
			return mcpLookup{}, mcpRaise("tuple index out of range")
		}
		if len(prepared) > 2 {
			return mcpLookup{}, mcpRaise("not all arguments converted during string formatting")
		}
		var placeholders []string
		for _, value := range prepared {
			if value.isNull {
				placeholders = append(placeholders, q.args.add(nil))
				continue
			}
			argument, err := mcpPreparedArgument(value)
			if err != nil {
				return mcpLookup{}, err
			}
			placeholders = append(placeholders, q.args.add(argument)+mcpPreparedCast(lhs, value))
		}
		return mcpLookup{name: name, sql: lhs.sql + " BETWEEN " + placeholders[0] + " AND " + placeholders[1]}, nil
	case "regex", "iregex":
		if lhs.kind == "json" {
			return mcpLookup{}, mcpNotPorted("lookup " + name)
		}
		argument, err := mcpRawArgument(value)
		if err != nil {
			return mcpLookup{}, err
		}
		operator := "~"
		if name == "iregex" {
			operator = "~*"
		}
		return mcpLookup{name: name, sql: lhs.sql + "::text " + operator + " " + q.args.add(argument)}, nil
	case "iexact":
		argument, err := mcpRawArgument(value)
		if err != nil {
			return mcpLookup{}, err
		}
		return mcpLookup{name: name, sql: "UPPER(" + lhs.sql + "::text) = UPPER(" + q.args.add(argument) + ")"}, nil
	case "contains", "icontains", "startswith", "istartswith", "endswith", "iendswith":
		text, err := mcpPatternText(value)
		if err != nil {
			return mcpLookup{}, err
		}
		column := lhs.sql + "::text"
		insensitive := strings.HasPrefix(name, "i")
		if insensitive {
			column = "UPPER(" + column + ")"
		}
		escaped := strings.NewReplacer(`\`, `\\`, "%", `\%`, "_", `\_`).Replace(text)
		switch strings.TrimPrefix(name, "i") {
		case "contains":
			escaped = "%" + escaped + "%"
		case "startswith":
			escaped += "%"
		default:
			escaped = "%" + escaped
		}
		pattern := q.args.add(escaped)
		if insensitive {
			pattern = "UPPER(" + pattern + ")"
		}
		return mcpLookup{name: name, sql: column + " LIKE " + pattern}, nil
	}
	// exact, gt, gte, lt, lte
	if lhs.kind == "int" && (name == "gte" || name == "lt") {
		// IntegerGreaterThanOrEqual/IntegerLessThan round a float up before
		// IntegerField.get_prep_value truncates it.
		value = mcpCeilIntegerValue(value)
	}
	prepared, err := q.prepare(lhs, value, true)
	if err != nil {
		return mcpLookup{}, err
	}
	if prepared.isNull {
		if name == "exact" {
			return mcpLookup{name: "isnull", sql: lhs.sql + " IS NULL", isnullTrue: true}, nil
		}
		return mcpLookup{}, mcpRaise("Cannot use None as a query value")
	}
	if lhs.extract == "year" || lhs.extract == "iso_year" {
		return q.yearLookup(lhs, name, prepared)
	}
	if name == "exact" && lhs.kind == "bool" && lhs.extract == "" && !lhs.relation {
		if prepared.param.(bool) {
			return mcpLookup{name: name, sql: lhs.sql}, nil
		}
		return mcpLookup{name: name, sql: "NOT " + lhs.sql}, nil
	}
	if lhs.kind == "int" && prepared.integer != nil {
		if result, argument, ok := mcpIntegerComparison(lhs, name, prepared.integer); ok {
			if result != mcpResultValue {
				return mcpLookup{name: name, result: result}, nil
			}
			prepared.param = argument
		}
	}
	operator := map[string]string{"exact": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[name]
	return mcpLookup{name: name, sql: lhs.sql + " " + operator + " " + q.args.add(prepared.param) + prepared.cast}, nil
}

// mcpJSONValue is JSONField.get_db_prep_value's json.dumps(value) text.
func mcpJSONValue(value any) (string, error) {
	switch value.(type) {
	case nil, string, bool, json.Number, []any, domain.RequestObject:
		return string(PythonJSONDumps(value)), nil
	}
	return "", mcpNotPorted("JSON value")
}

// mcpJSONKeyArgument is a has_key value: Django passes it through untouched,
// so PostgreSQL reports the operator error for the type it receives.
func (q *mcpQuery) mcpJSONKeyArgument(value any) (string, error) {
	switch typed := value.(type) {
	case string:
		return q.args.add(typed), nil
	case bool:
		return q.args.add(typed) + "::boolean", nil
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			number, err := strconv.ParseFloat(string(typed), 64)
			if err != nil || math.IsNaN(number) || math.IsInf(number, 0) {
				return "", mcpNotPorted("has_key number")
			}
			return q.args.add(number) + "::double precision", nil
		}
		number, ok := new(big.Int).SetString(string(typed), 10)
		if !ok || !number.IsInt64() {
			return "", mcpNotPorted("has_key number")
		}
		cast := "::int"
		if value := number.Int64(); value < math.MinInt32 || value > math.MaxInt32 {
			cast = "::bigint"
		}
		return q.args.add(number.Int64()) + cast, nil
	case []any:
		items := make([]string, len(typed))
		allText := true
		for i, item := range typed {
			text, ok := item.(string)
			if !ok {
				allText = false
				break
			}
			items[i] = text
		}
		if !allText {
			return "", mcpNotPorted("has_key list")
		}
		return q.args.add(items) + "::text[]", nil
	}
	return "", mcpNotPorted("has_key value")
}

// jsonLookup ports django/db/models/fields/json.py on PostgreSQL: JSONField's
// own lookups and the lookups KeyTransform and KeyTextTransform register.
func (q *mcpQuery) jsonLookup(lhs mcpExpr, name string, value any) (mcpLookup, error) {
	// Names the expression's lookups do not offer stay refused.
	switch name {
	case "range":
		if !lhs.jsonKey {
			return mcpLookup{}, mcpNotPorted("JSON lookup " + name)
		}
	case "contained_by", "has_keys", "has_any_keys":
		if lhs.jsonKey {
			return mcpLookup{}, mcpNotPorted("JSON key lookup " + name)
		}
	}
	switch name {
	case "isnull":
		// KeyTransformIsNull is IsNull: the key expression IS [NOT] NULL.
		flag, ok := value.(bool)
		if !ok {
			if value == nil {
				return mcpLookup{}, mcpRaise("Cannot use None as a query value")
			}
			if q.compileErr == nil {
				q.compileErr = mcpRaise("The QuerySet value for an isnull lookup must be True or False.")
			}
			flag = PythonTruthy(value)
		}
		if flag {
			return mcpLookup{name: name, sql: lhs.sql + " IS NULL", isnullTrue: true}, nil
		}
		return mcpLookup{name: name, sql: lhs.sql + " IS NOT NULL"}, nil
	case "has_key":
		if value == nil {
			return mcpLookup{}, mcpRaise("Cannot use None as a query value")
		}
		argument, err := q.mcpJSONKeyArgument(value)
		if err != nil {
			return mcpLookup{}, err
		}
		return mcpLookup{name: name, sql: lhs.sql + " ? " + argument}, nil
	case "has_keys", "has_any_keys":
		// HasKeys.get_prep_lookup iterates the value and stringifies each key.
		items, err := mcpLookupItems(value)
		if err != nil {
			return mcpLookup{}, err
		}
		keys := make([]string, len(items))
		for i, item := range items {
			if item == nil {
				keys[i] = "None"
				continue
			}
			text, err := mcpTextValue(item)
			if err != nil {
				return mcpLookup{}, err
			}
			keys[i] = text
		}
		operator := "?&"
		if name == "has_any_keys" {
			operator = "?|"
		}
		return mcpLookup{name: name, sql: lhs.sql + " " + operator + " " + q.args.add(keys) + "::text[]"}, nil
	case "contained_by", "contains":
		if value == nil {
			return mcpLookup{}, mcpRaise("Cannot use None as a query value")
		}
		encoded, err := mcpJSONValue(value)
		if err != nil {
			return mcpLookup{}, err
		}
		operator := "@>"
		if name == "contained_by" {
			operator = "<@"
		}
		return mcpLookup{name: name, sql: lhs.sql + " " + operator + " " + q.args.add(encoded) + "::jsonb"}, nil
	case "iexact", "icontains", "startswith", "istartswith", "endswith", "iendswith", "regex", "iregex":
		// KeyTransformTextLookupMixin reads the key through ->> / #>>.
		text, err := q.jsonTextExpr(lhs)
		if err != nil {
			return mcpLookup{}, err
		}
		return q.lookupSQL(text, name, value)
	case "in":
		items, err := mcpLookupItems(value)
		if err != nil {
			return mcpLookup{}, err
		}
		// In.process_rhs: OrderedSet(self.rhs) discards None and repeats; an
		// unhashable value skips the set for the whole list. Values are rows
		// again once adapted, so every value becomes one jsonb parameter.
		deduplicate := true
		for _, item := range items {
			switch item.(type) {
			case []any, domain.RequestObject:
				deduplicate = false
			}
		}
		var placeholders []string
		seen := map[string]bool{}
		for _, item := range items {
			if item == nil {
				continue
			}
			encoded, err := mcpJSONValue(item)
			if err != nil {
				return mcpLookup{}, err
			}
			if deduplicate {
				if seen[encoded] {
					continue
				}
				seen[encoded] = true
			}
			placeholders = append(placeholders, q.args.add(encoded)+"::jsonb")
		}
		if len(placeholders) == 0 {
			return mcpLookup{name: name, result: mcpResultEmpty}, nil
		}
		return mcpLookup{name: name, sql: lhs.sql + " IN (" + strings.Join(placeholders, ", ") + ")"}, nil
	case "range":
		items, err := mcpLookupItems(value)
		if err != nil {
			return mcpLookup{}, err
		}
		if len(items) < 2 {
			return mcpLookup{}, mcpRaise("tuple index out of range")
		}
		if len(items) > 2 {
			return mcpLookup{}, mcpRaise("not all arguments converted during string formatting")
		}
		var placeholders []string
		for _, item := range items {
			if item == nil {
				placeholders = append(placeholders, q.args.add(nil))
				continue
			}
			encoded, err := mcpJSONValue(item)
			if err != nil {
				return mcpLookup{}, err
			}
			placeholders = append(placeholders, q.args.add(encoded)+"::jsonb")
		}
		return mcpLookup{name: name, sql: lhs.sql + " BETWEEN " + placeholders[0] + " AND " + placeholders[1]}, nil
	}
	// exact, gt, gte, lt, lte: Exact's get_prep_lookup adapts the value with
	// json.dumps; None stays None, and JSONExact.can_use_none_as_rhs turns it
	// into the JSON null value.
	if value == nil && name != "exact" {
		return mcpLookup{}, mcpRaise("Cannot use None as a query value")
	}
	encoded, err := mcpJSONValue(value)
	if err != nil {
		return mcpLookup{}, err
	}
	operators := map[string]string{"exact": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
	return mcpLookup{name: name, sql: lhs.sql + " " + operators[name] + " " + q.args.add(encoded) + "::jsonb"}, nil
}

// mcpIntegerInternal is the field type whose storage range applies to an
// integer expression (date parts are IntegerField in Django).
func mcpIntegerInternal(lhs mcpExpr) string {
	if lhs.extract == "" && lhs.field != nil {
		return lhs.field.Internal
	}
	return "IntegerField"
}

// mcpPreparedCast widens an integer parameter the column's type cannot hold,
// so the driver sends a value PostgreSQL can bind (Django adapts it as int8).
func mcpPreparedCast(lhs mcpExpr, prepared mcpPrepared) string {
	if prepared.integer == nil {
		return prepared.cast
	}
	low, high := mcpIntRange(mcpIntegerInternal(lhs))
	if prepared.integer.Cmp(big.NewInt(low)) < 0 || prepared.integer.Cmp(big.NewInt(high)) > 0 {
		return "::bigint"
	}
	return prepared.cast
}

// mcpLookupItems is Python iteration of an in/range value.
func mcpLookupItems(value any) ([]any, error) {
	switch typed := value.(type) {
	case []any:
		return typed, nil
	case string:
		items := make([]any, 0, len([]rune(typed)))
		for _, r := range typed {
			items = append(items, string(r))
		}
		return items, nil
	case domain.RequestObject:
		items := make([]any, 0, len(typed))
		for _, field := range typed {
			items = append(items, field.Name)
		}
		return items, nil
	case nil:
		return nil, mcpRaise("'NoneType' object is not iterable")
	case mcpCoercedInt:
		return nil, mcpRaise("'int' object is not iterable")
	case mcpCoercedFloat:
		return nil, mcpRaise("'float' object is not iterable")
	case mcpCoercedDate:
		return nil, mcpRaise("'date' object is not iterable")
	case mcpCoercedDateTime:
		return nil, mcpRaise("'datetime' object is not iterable")
	}
	return nil, mcpRaise("'%s' object is not iterable", pythonTypeName(value))
}

// mcpCeilIntegerValue is IntegerFieldFloatRounding: only a Python float is
// rounded, strings are left for get_prep_value to reject.
func mcpCeilIntegerValue(value any) any {
	switch typed := value.(type) {
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			if number, err := strconv.ParseFloat(string(typed), 64); err == nil && !math.IsInf(number, 0) && !math.IsNaN(number) {
				return mcpCoercedFloat{math.Ceil(number)}
			}
		}
	case mcpCoercedFloat:
		return mcpCoercedFloat{math.Ceil(typed.v)}
	}
	return value
}

// mcpIntegerComparison is IntegerFieldOverflow: out-of-range direct values
// become EmptyResultSet (FALSE) or FullResultSet (TRUE).
func mcpIntegerComparison(lhs mcpExpr, name string, number *big.Int) (mcpResultKind, int64, bool) {
	low, high := mcpIntRange(mcpIntegerInternal(lhs))
	below := number.Cmp(big.NewInt(low)) < 0
	above := number.Cmp(big.NewInt(high)) > 0
	if below || above {
		switch name {
		case "exact":
			return mcpResultEmpty, 0, true
		case "gt", "gte":
			if below {
				return mcpResultFull, 0, true
			}
			return mcpResultEmpty, 0, true
		case "lt", "lte":
			if above {
				return mcpResultFull, 0, true
			}
			return mcpResultEmpty, 0, true
		}
	}
	return mcpResultValue, number.Int64(), true
}

// mcpISOYearStart is date.fromisocalendar(year, 1, 1): the Monday of week 1.
func mcpISOYearStart(year int) time.Time {
	day := time.Date(year, time.January, 4, 0, 0, 0, 0, time.UTC)
	weekday := int(day.Weekday())
	if weekday == 0 {
		weekday = 7
	}
	return day.AddDate(0, 0, -(weekday - 1))
}

// yearLookup is YearExact/YearGt/... : bounds on the expression the year was
// extracted from (the untransformed column for a plain __year).
func (q *mcpQuery) yearLookup(lhs mcpExpr, name string, prepared mcpPrepared) (mcpLookup, error) {
	number := prepared.integer
	if number == nil {
		return mcpLookup{}, mcpNotPorted("year value")
	}
	iso := lhs.extract == "iso_year"
	year := number.Int64()
	if !number.IsInt64() || year < 1 || year > 9999 {
		return mcpLookup{}, mcpRaise("year %s is out of range", number.String())
	}
	if iso && year == 9999 {
		// year + 1 is outside datetime's range.
		return mcpLookup{}, mcpRaise("year %d is out of range", year+1)
	}
	var first, second string
	cast := "::date"
	if lhs.colKind == "datetime" {
		var start, finish time.Time
		if iso {
			start = mcpISOYearStart(int(year))
			finish = mcpISOYearStart(int(year + 1)).Add(-time.Microsecond)
		} else {
			start = time.Date(int(year), time.January, 1, 0, 0, 0, 0, time.UTC)
			finish = time.Date(int(year), time.December, 31, 23, 59, 59, 999999000, time.UTC)
		}
		first = start.Format("2006-01-02T15:04:05.999999+00:00")
		second = finish.Format("2006-01-02T15:04:05.999999+00:00")
		cast = "::timestamptz"
	} else {
		var start, finish time.Time
		if iso {
			start = mcpISOYearStart(int(year))
			finish = mcpISOYearStart(int(year+1)).AddDate(0, 0, -1)
		} else {
			start = time.Date(int(year), time.January, 1, 0, 0, 0, 0, time.UTC)
			finish = time.Date(int(year), time.December, 31, 0, 0, 0, 0, time.UTC)
		}
		first = start.Format("2006-01-02")
		second = finish.Format("2006-01-02")
	}
	switch name {
	case "exact":
		return mcpLookup{name: name, sql: lhs.column + " BETWEEN " + q.args.add(first) + cast + " AND " + q.args.add(second) + cast}, nil
	case "gt":
		return mcpLookup{name: name, sql: lhs.column + " > " + q.args.add(second) + cast}, nil
	case "gte":
		return mcpLookup{name: name, sql: lhs.column + " >= " + q.args.add(first) + cast}, nil
	case "lt":
		return mcpLookup{name: name, sql: lhs.column + " < " + q.args.add(first) + cast}, nil
	}
	return mcpLookup{name: name, sql: lhs.column + " <= " + q.args.add(second) + cast}, nil
}

// ---------------------------------------------------------------------------
// The QueryBuilder base queryset and filters
// ---------------------------------------------------------------------------

type mcpWhitelistEntry = struct {
	Label, Tenant string
	SoftDelete    []string
}

// mcpResolveModel is _resolve_model.
func mcpResolveModel(value any) (*mcpModel, *mcpWhitelistEntry, error) {
	name, ok := value.(string)
	if !ok {
		switch value.(type) {
		case []any, domain.RequestObject:
			// '.' in value is False; no whitelist label equals it.
			return nil, nil, mcpRaise("Model '%s' is not whitelisted", PythonStr(value))
		}
		return nil, nil, mcpRaise("argument of type '%s' is not iterable", pythonTypeName(value))
	}
	if strings.Contains(name, ".") {
		for i := range mcpWhitelist {
			if mcpWhitelist[i].Label == name {
				return mcpModelByLabel(name), &mcpWhitelist[i], nil
			}
		}
		return nil, nil, mcpRaise("Model '%s' is not whitelisted", name)
	}
	for i := range mcpWhitelist {
		if strings.SplitN(mcpWhitelist[i].Label, ".", 2)[1] == name {
			return mcpModelByLabel(mcpWhitelist[i].Label), &mcpWhitelist[i], nil
		}
	}
	return nil, nil, mcpRaise("Model '%s' is not whitelisted", name)
}

// baseQuery is _base_queryset: the default manager, the tenant filter, the
// soft-delete filters unless include_archived, distinct().
func mcpBaseQuery(model *mcpModel, entry *mcpWhitelistEntry, association string, group *string, includeArchived bool) (*mcpQuery, error) {
	q := newMCPQuery(model)
	// GroupAwareManager.get_queryset: for_group(X-Group-Id) before its other filters.
	if group != nil && model.GroupManager {
		if err := q.addQ(mcpQ([]mcpLeaf{{key: "group_id", value: *group}})); err != nil {
			return nil, err
		}
	}
	if len(model.ManagerFilters) > 0 {
		var leaves []mcpLeaf
		for _, column := range model.ManagerFilters {
			leaves = append(leaves, mcpLeaf{key: column, value: false})
		}
		if err := q.addQ(mcpQ(leaves)); err != nil {
			return nil, err
		}
	}
	key := entry.Tenant
	switch {
	case key == "_self":
		key = "sport_association_id"
	case strings.HasSuffix(key, "sport_association"):
		key += "_id"
	}
	if err := q.addQ(mcpQ([]mcpLeaf{{key: key, value: association}})); err != nil {
		return nil, err
	}
	if !includeArchived && len(entry.SoftDelete) > 0 {
		var leaves []mcpLeaf
		for _, field := range entry.SoftDelete {
			leaves = append(leaves, mcpLeaf{key: field, value: false})
		}
		if err := q.addQ(mcpQ(leaves)); err != nil {
			return nil, err
		}
	}
	return q, nil
}
