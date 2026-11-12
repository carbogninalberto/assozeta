package importeffects

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/jmoiron/sqlx"
)

// LogEntry is one django-auditlog entry the effects write (the AuditLogIndex
// row is resolved by the writer, as create_audit_log_index does on the
// LogEntry's post_save).
type LogEntry struct {
	App, Model, ObjectPK, Repr string
	ObjectID                   *int64
	Action                     int
	Changes                    map[string][2]string
	At                         time.Time
}

// AuditWriter stores a LogEntry and its index row inside tx.
type AuditWriter func(ctx context.Context, tx *sqlx.Tx, entry LogEntry) error

// Instance is a model instance in memory: its attributes by attname and
// whether it is still being added (Model._state.adding).
type Instance struct {
	Model  *Model
	Values map[string]Value
	Adding bool
}

// Get returns the attribute named attname (None when absent).
func (i *Instance) Get(attname string) Value { return i.Values[attname] }

// PK is the primary key attribute.
func (i *Instance) PK() Value { return i.Values[i.Model.PK.Attname] }

// Effects performs the ORM operations of one import inside its transaction.
type Effects struct {
	Plan  *Plan
	Tx    *sqlx.Tx
	Audit AuditWriter
	// Now is timezone.now(); NewUUID is str(uuid.uuid4()).
	Now     func() time.Time
	NewUUID func() string
}

func (e *Effects) now() time.Time         { return e.Now() }
func (e *Effects) newUUID() string        { return e.NewUUID() }
func quote(name string) string            { return `"` + strings.ReplaceAll(name, `"`, `""`) + `"` }
func cast(field *Field) string            { return "::" + field.DBType }
func placeholder(n int) string            { return "$" + strconv.Itoa(n) }
func (e *Effects) tx() *sqlx.Tx           { return e.Tx }
func isAutoField(field *Field) bool       { return field.Type == "AutoField" || field.Type == "BigAutoField" }
func (m *Model) audited() bool            { return m.Audit != nil }
func (m *Model) pkIsInt() bool            { return m.PK.Kind == "int" }
func (i *Instance) set(a string, v Value) { i.Values[a] = v }

// NewInstance is model(**kwargs): every concrete field the keyword arguments
// (keyed by attname) do not set takes field.get_default().
func (e *Effects) NewInstance(model *Model, kwargs map[string]Value) (*Instance, error) {
	instance := &Instance{Model: model, Values: map[string]Value{}, Adding: true}
	for _, field := range model.Fields {
		if value, ok := kwargs[field.Attname]; ok {
			instance.Values[field.Attname] = value
			continue
		}
		value, err := e.Default(field)
		if err != nil {
			return nil, err
		}
		instance.Values[field.Attname] = value
	}
	for key := range kwargs {
		if model.FieldByAttname(key) == nil {
			return nil, unsupported("keyword %s for %s", key, model.Name)
		}
	}
	return instance, nil
}

// Save is instance.save() for a new instance: the model's save() override,
// the fields' pre_save (auto_now/auto_now_add), the INSERT, and auditlog's
// log_create with its index.
func (e *Effects) Save(ctx context.Context, instance *Instance) error {
	if !instance.Adding {
		return unsupported("save of an existing %s", instance.Model.Name)
	}
	model := instance.Model
	switch model.Save {
	case "payment":
		if err := e.paymentSave(ctx, instance); err != nil {
			return err
		}
	case "subscription":
		if err := e.subscriptionSave(ctx, instance); err != nil {
			return err
		}
	case "course", "default":
		// Course.save reads Course.objects.get(pk) and finds nothing for a new row.
	default:
		return unsupported("save override %s", model.Save)
	}
	now := e.now().UTC()
	for _, field := range model.Fields {
		if field.AutoNow || field.AutoNowAdd {
			if field.Type != "DateTimeField" {
				return unsupported("auto date on %s", field.Type)
			}
			instance.set(field.Attname, DateTimeValue(now))
		}
	}
	if err := e.insert(ctx, instance); err != nil {
		return err
	}
	instance.Adding = false
	return e.logCreate(ctx, instance)
}

// RawSave is save_base(raw=True, force_insert=True) without auditlog (the
// importer's message history path): no save override, no pre_save values.
// logCreate then calls auditlog's log_create as the importer does for Message.
func (e *Effects) RawSave(ctx context.Context, instance *Instance, logCreate bool) error {
	if err := e.insert(ctx, instance); err != nil {
		return err
	}
	instance.Adding = false
	if logCreate {
		return e.logCreate(ctx, instance)
	}
	return nil
}

func (e *Effects) insert(ctx context.Context, instance *Instance) error {
	model := instance.Model
	columns, values, args := []string{}, []string{}, []any{}
	var returning *Field
	for _, field := range model.Fields {
		value := instance.Get(field.Attname)
		if field.PrimaryKey && isAutoField(field) && value.IsNone() {
			returning = field
			continue
		}
		bound, err := dbValue(field, value)
		if err != nil {
			return err
		}
		args = append(args, bound)
		columns = append(columns, quote(field.Column))
		values = append(values, placeholder(len(args))+cast(field))
	}
	query := `INSERT INTO ` + quote(model.Table) + ` (` + strings.Join(columns, ", ") + `) VALUES (` + strings.Join(values, ", ") + `)`
	if returning == nil {
		_, err := e.tx().ExecContext(ctx, query, args...)
		return err
	}
	var id int64
	if err := e.tx().GetContext(ctx, &id, query+` RETURNING `+quote(returning.Column), args...); err != nil {
		return err
	}
	instance.set(returning.Attname, IntValue(id))
	return nil
}

// Repr is str(instance).
func (e *Effects) Repr(instance *Instance) (string, error) {
	model := instance.Model
	switch model.Str {
	case "default":
		pk, err := instance.PK().Text()
		return model.Name + " object (" + pk + ")", err
	case "user":
		first, err := instance.Get("first_name").Text()
		if err != nil {
			return "", err
		}
		last, err := instance.Get("last_name").Text()
		return "name: " + first + " " + last, err
	}
	return "", unsupported("__str__ of %s", model.Name)
}

func (e *Effects) entry(instance *Instance, action int, changes map[string][2]string) (LogEntry, error) {
	model := instance.Model
	repr, err := e.Repr(instance)
	if err != nil {
		return LogEntry{}, err
	}
	pk, err := instance.PK().Text()
	if err != nil {
		return LogEntry{}, err
	}
	entry := LogEntry{App: model.App, Model: model.ModelName, ObjectPK: pk, Repr: repr, Action: action, Changes: changes,
		At: e.now()}
	if model.pkIsInt() {
		id, err := strconv.ParseInt(pk, 10, 64)
		if err != nil {
			return LogEntry{}, unsupported("integer key")
		}
		entry.ObjectID = &id
	}
	return entry, nil
}

func (e *Effects) logCreate(ctx context.Context, instance *Instance) error {
	model := instance.Model
	if !model.audited() {
		return nil
	}
	excluded := map[string]bool{}
	for _, name := range model.Audit.Exclude {
		excluded[name] = true
	}
	if len(model.Audit.Include) > 0 {
		return unsupported("auditlog include_fields")
	}
	changes := map[string][2]string{}
	for _, field := range model.Fields {
		if excluded[field.Name] {
			continue
		}
		text, include, err := auditCreate(field, instance.Get(field.Attname))
		if err != nil {
			return err
		}
		if include {
			changes[field.Name] = [2]string{"None", text}
		}
	}
	for name, text := range model.Audit.Reverse {
		if !excluded[name] {
			changes[name] = [2]string{"None", text}
		}
	}
	entry, err := e.entry(instance, 0, changes)
	if err != nil {
		return err
	}
	return e.Audit(ctx, e.Tx, entry)
}

// SaveFields is instance.save(update_fields=attnames) for a saved instance:
// the model's save() override, auditlog's log_update (the old row through the
// default manager, the diff restricted to the fields), then the UPDATE.
func (e *Effects) SaveFields(ctx context.Context, instance *Instance, names ...string) error {
	model := instance.Model
	if instance.Adding {
		return unsupported("update of an unsaved %s", model.Name)
	}
	switch model.Save {
	case "subscription":
		if err := e.subscriptionSave(ctx, instance); err != nil {
			return err
		}
	case "default", "course":
	default:
		return unsupported("save override %s on update", model.Save)
	}
	fields := make([]*Field, 0, len(names))
	for _, name := range names {
		field := model.Field(name)
		if field == nil || field.IsFK() || field.AutoNow {
			return unsupported("update field %s", name)
		}
		fields = append(fields, field)
	}
	pk, err := dbValue(model.PKField(), instance.PK())
	if err != nil {
		return err
	}
	if model.audited() {
		changes := map[string][2]string{}
		selects := make([]string, len(fields))
		for i, field := range fields {
			selects[i] = quote(field.Column)
		}
		row := e.tx().QueryRowxContext(ctx, `SELECT `+strings.Join(selects, ", ")+` FROM `+quote(model.Table)+` WHERE `+
			quote(model.PK.Column)+` = $1`+cast(model.PKField()), pk)
		old := make([]sql.NullString, len(fields))
		targets := make([]any, len(fields))
		for i := range old {
			targets[i] = &old[i]
		}
		found := true
		if err := row.Scan(targets...); errors.Is(err, sql.ErrNoRows) {
			found = false
		} else if err != nil {
			return err
		}
		if !found {
			return unsupported("update audit of a row the default manager hides")
		}
		for i, field := range fields {
			before := NoneValue
			if old[i].Valid {
				before = StrValue(old[i].String)
			}
			oldText, err := auditUpdateText(field, before)
			if err != nil {
				return err
			}
			newText, err := auditUpdateText(field, instance.Get(field.Attname))
			if err != nil {
				return err
			}
			if oldText != newText {
				changes[field.Name] = [2]string{oldText, newText}
			}
		}
		if len(changes) > 0 {
			entry, err := e.entry(instance, 1, changes)
			if err != nil {
				return err
			}
			if err := e.Audit(ctx, e.Tx, entry); err != nil {
				return err
			}
		}
	}
	sets, args := []string{}, []any{}
	for _, field := range fields {
		bound, err := dbValue(field, instance.Get(field.Attname))
		if err != nil {
			return err
		}
		args = append(args, bound)
		sets = append(sets, quote(field.Column)+" = "+placeholder(len(args))+cast(field))
	}
	args = append(args, pk)
	result, err := e.tx().ExecContext(ctx, `UPDATE `+quote(model.Table)+` SET `+strings.Join(sets, ", ")+` WHERE `+
		quote(model.PK.Column)+` = `+placeholder(len(args))+cast(model.PKField()), args...)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		return unsupported("update_fields affected no row")
	}
	return nil
}

// UpdatePassword is user.save(update_fields=['password']) on a User: auditlog
// excludes the field, so the diff is empty and no entry is written.
func (e *Effects) UpdatePassword(ctx context.Context, model *Model, pk, password string) error {
	if model.Name != "User" || model.Audit == nil || !contains(model.Audit.Exclude, "password") {
		return unsupported("password update of %s", model.Name)
	}
	result, err := e.tx().ExecContext(ctx, `UPDATE `+quote(model.Table)+` SET "password" = $1 WHERE `+quote(model.PK.Column)+
		` = $2::uuid`, password, pk)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		return unsupported("password update affected no row")
	}
	return nil
}

func contains(list []string, item string) bool {
	for _, value := range list {
		if value == item {
			return true
		}
	}
	return false
}

// Exists is <unfiltered manager>.filter(pk=key).exists().
func (e *Effects) Exists(ctx context.Context, model *Model, key Value) (bool, error) {
	bound, err := dbValue(model.PKField(), key)
	if err != nil {
		return false, err
	}
	var exists bool
	err = e.tx().GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM `+quote(model.Table)+` WHERE `+quote(model.PK.Column)+
		` = $1`+cast(model.PKField())+`)`, bound)
	return exists, err
}

// QuerysetUpdate is <unfiltered manager>.filter(pk=key).update(attname=value):
// no save(), no signal, no audit. It returns the number of rows matched.
func (e *Effects) QuerysetUpdate(ctx context.Context, model *Model, key Value, attname string, value Value) (int64, error) {
	field := model.FieldByAttname(attname)
	if field == nil {
		return 0, unsupported("update of %s.%s", model.Name, attname)
	}
	bound, err := dbValue(field, value)
	if err != nil {
		return 0, err
	}
	pk, err := dbValue(model.PKField(), key)
	if err != nil {
		return 0, err
	}
	result, err := e.tx().ExecContext(ctx, `UPDATE `+quote(model.Table)+` SET `+quote(field.Column)+` = $1`+cast(field)+
		` WHERE `+quote(model.PK.Column)+` = $2`+cast(model.PKField()), bound, pk)
	if err != nil {
		return 0, err
	}
	return result.RowsAffected()
}

// SetM2M is getattr(instance, field).set(keys) on a saved instance with no
// related rows: the through rows of the distinct keys, in the order of the
// CPython set _add_items builds, in one INSERT ... ON CONFLICT DO NOTHING.
func (e *Effects) SetM2M(ctx context.Context, model *Model, field *M2M, key Value, targets []string) error {
	if !field.AutoCreated || field.TargetKind != "uuid" || model.PK.Kind != "uuid" {
		return unsupported("m2m %s.%s", model.Name, field.Name)
	}
	for _, target := range targets {
		if !CanonicalUUID(target) {
			return unsupported("m2m key")
		}
	}
	var existing int
	if err := e.tx().GetContext(ctx, &existing, `SELECT count(*) FROM `+quote(field.Table)+` WHERE `+quote(field.Source)+` = $1::uuid`,
		key.S); err != nil {
		return err
	}
	if existing != 0 {
		return unsupported("m2m set over existing rows")
	}
	ordered := pySetOrder(targets)
	if len(ordered) == 0 {
		return nil
	}
	values, args := []string{}, []any{key.S}
	for _, target := range ordered {
		args = append(args, target)
		values = append(values, "($1::uuid, "+placeholder(len(args))+"::uuid)")
	}
	_, err := e.tx().ExecContext(ctx, `INSERT INTO `+quote(field.Table)+` (`+quote(field.Source)+`, `+quote(field.Target)+`) VALUES `+
		strings.Join(values, ", ")+` ON CONFLICT DO NOTHING`, args...)
	return err
}

// CheckConstraints is connection.check_constraints() on PostgreSQL.
func (e *Effects) CheckConstraints(ctx context.Context) error {
	if _, err := e.tx().ExecContext(ctx, `SET CONSTRAINTS ALL IMMEDIATE`); err != nil {
		return err
	}
	_, err := e.tx().ExecContext(ctx, `SET CONSTRAINTS ALL DEFERRED`)
	return err
}

// SaveTreeNode is MPTTModel.save() for a new node without order_insertion_by:
// a root gets the next tree id (max + 1) with lft 1, rght 2, level 0; a child
// is inserted as the parent's last child after _create_space(2, rght - 1)
// moves the following nodes of the tree (the parent is refreshed first).
func (e *Effects) SaveTreeNode(ctx context.Context, instance *Instance) error {
	model := instance.Model
	if !model.IsMPTT || model.audited() {
		return unsupported("tree save of %s", model.Name)
	}
	parent := instance.Get("parent_id")
	table := quote(model.Table)
	if parent.IsNone() {
		var max sql.NullInt64
		if err := e.tx().GetContext(ctx, &max, `SELECT MAX("tree_id") FROM `+table); err != nil {
			return err
		}
		instance.set("lft", IntValue(1))
		instance.set("rght", IntValue(2))
		instance.set("level", IntValue(0))
		instance.set("tree_id", IntValue(max.Int64+1))
	} else {
		var target struct {
			Left   int64 `db:"lft"`
			Right  int64 `db:"rght"`
			TreeID int64 `db:"tree_id"`
			Level  int64 `db:"level"`
		}
		if err := e.tx().GetContext(ctx, &target, `SELECT "lft", "rght", "tree_id", "level" FROM `+table+` WHERE "id" = $1::integer`,
			parent.S); err != nil {
			return err
		}
		space := target.Right - 1
		if _, err := e.tx().ExecContext(ctx, `UPDATE `+table+` SET "lft" = CASE WHEN "lft" > $1 THEN "lft" + $2 ELSE "lft" END,
 "rght" = CASE WHEN "rght" > $1 THEN "rght" + $2 ELSE "rght" END WHERE "tree_id" = $3 AND ("lft" > $1 OR "rght" > $1)`,
			space, 2, target.TreeID); err != nil {
			return err
		}
		instance.set("lft", IntValue(target.Right))
		instance.set("rght", IntValue(target.Right+1))
		instance.set("level", IntValue(target.Level+1))
		instance.set("tree_id", IntValue(target.TreeID))
	}
	if err := e.insert(ctx, instance); err != nil {
		return err
	}
	instance.Adding = false
	return nil
}
