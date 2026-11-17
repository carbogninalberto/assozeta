package importeffects

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// SaveForceUpdate is instance.save(force_update=True) on a model with the
// default save(). For an instance that was not loaded (still
// _state.adding, as model(**kwargs) builds it) auditlog writes nothing:
// log_update skips adding instances and log_create only follows a create.
// For a loaded one, log_update runs first (pre_save: the stored row through
// the default manager against the in-memory instance, every concrete field
// auditlog tracks, a field recorded when get_field_value differs). Then
// auto_now fields take timezone.now() and one UPDATE writes every concrete
// non-key field. An UPDATE that misses its row raises Django's DatabaseError
// "Forced update did not affect any rows.".
func (e *Effects) SaveForceUpdate(ctx context.Context, instance *Instance) error {
	model := instance.Model
	if model.Save != "default" {
		// plan.json: SaveForceUpdate is called for SportAssociation only, whose
		// save is the default one (consumer_restore_instance.go).
		return planUnreachable("force update with save override %s", model.Save)
	}
	pk, err := dbValue(model.PKField(), instance.PK())
	if err != nil {
		return err
	}
	if model.audited() && !instance.Adding {
		if len(model.Audit.Include) > 0 {
			// plan.json: no imported model registers include_fields.
			return planUnreachable("auditlog include_fields on %s", model.Name)
		}
		var raw sql.NullString
		err := e.tx().GetContext(ctx, &raw, `SELECT to_jsonb(t)::text FROM `+quote(model.Table)+` t WHERE `+quote(model.PK.Column)+
			` = $1`+cast(model.PKField()), pk)
		if errors.Is(err, sql.ErrNoRows) {
			// The row is gone: Django's UPDATE would affect no row and raise.
			return databaseError("Forced update did not affect any rows.")
		}
		if err == nil && !raw.Valid {
			// to_jsonb(row) of an existing row is never NULL.
			return planUnreachable("stored row of %s", model.Name)
		}
		if err != nil {
			return err
		}
		decoder := json.NewDecoder(strings.NewReader(raw.String))
		decoder.UseNumber()
		stored := map[string]any{}
		if err := decoder.Decode(&stored); err != nil {
			return err
		}
		excluded := map[string]bool{}
		for _, name := range model.Audit.Exclude {
			excluded[name] = true
		}
		changes := map[string][2]string{}
		for _, field := range model.Fields {
			if excluded[field.Name] {
				continue
			}
			oldText, err := storedAuditText(field, stored[field.Column])
			if err != nil {
				return err
			}
			newText, err := memoryAuditText(field, instance.Get(field.Attname))
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
	for _, field := range model.Fields {
		if field.PrimaryKey {
			continue
		}
		if field.AutoNow {
			instance.set(field.Attname, DateTimeValue(e.now().UTC()))
		}
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
	if affected, err := result.RowsAffected(); err != nil {
		return err
	} else if affected == 0 {
		// Django's save_base(force_update=True) raises DatabaseError.
		return databaseError("Forced update did not affect any rows.")
	} else if affected != 1 {
		return planUnreachable("forced update matched %d rows", affected)
	}
	instance.Adding = false
	return nil
}

// storedAuditText is smart_str(get_field_value(old, field)) of the value
// psycopg loads for the column (to_jsonb's rendering of it).
func storedAuditText(field *Field, value any) (string, error) {
	if value == nil {
		if field.Kind == "json" {
			return "null", nil
		}
		return "None", nil
	}
	kind := field.Kind
	if field.IsFK() {
		kind = field.TargetKind
	}
	switch kind {
	case "text", "uuid", "date":
		if text, ok := value.(string); ok {
			return text, nil
		}
	case "int", "decimal":
		if number, ok := value.(json.Number); ok {
			return number.String(), nil
		}
	case "bool":
		if b, ok := value.(bool); ok {
			if b {
				return "True", nil
			}
			return "False", nil
		}
	case "datetime":
		if text, ok := value.(string); ok {
			// plan.json: the column type is timestamp with time zone, whose
			// to_jsonb text is always an ISO timestamp.
			moment, err := time.Parse(time.RFC3339Nano, text)
			if err != nil {
				return "", planUnreachable("stored datetime %s", field.Name)
			}
			return pythonNaive(moment.UTC()), nil
		}
	case "time":
		if text, ok := value.(string); ok {
			// str() of the time psycopg loads: to_jsonb trims trailing zeros.
			return storedTimeText(text)
		}
	case "json":
		if err := checkJSONInts(jsonFromStored(value)); err != nil {
			return "", err
		}
		return string(dto.PythonJSONDumpsSorted(jsonFromStored(value))), nil
	}
	// The switch covers every column type in plan.json; a mismatch would need
	// a column whose type differs from its field's kind.
	return "", planUnreachable("stored audit value of %s", field.Name)
}

// storedTimeText is str(datetime.time) of a PostgreSQL time's text.
func storedTimeText(text string) (string, error) {
	parts := strings.SplitN(text, ":", 3)
	if len(parts) != 3 {
		return "", planUnreachable("stored time %q", text)
	}
	second := parts[2]
	fraction := ""
	if index := strings.IndexByte(second, '.'); index >= 0 {
		fraction, second = second[index+1:], second[:index]
	}
	if fraction != "" {
		fraction = fraction + strings.Repeat("0", 6-len(fraction))
		if len(fraction) > 6 {
			fraction = fraction[:6]
		}
	}
	text = parts[0] + ":" + parts[1] + ":" + second
	if strings.Trim(fraction, "0") != "" {
		text += "." + fraction
	}
	return text, nil
}

// jsonFromStored converts a decoded map to the ordered object the Python
// renderers take (sort_keys makes the order irrelevant).
func jsonFromStored(value any) any {
	switch v := value.(type) {
	case map[string]any:
		object := domain.RequestObject{}
		for key, item := range v {
			object = append(object, domain.RequestField{Name: key, Value: jsonFromStored(item)})
		}
		return object
	case []any:
		items := make([]any, len(v))
		for i, item := range v {
			items[i] = jsonFromStored(item)
		}
		return items
	}
	return value
}

// memoryAuditText is smart_str(get_field_value(instance, field)) of the
// in-memory attribute.
func memoryAuditText(field *Field, v Value) (string, error) {
	switch {
	case field.Kind == "json":
		if field.Encoder != nil {
			// plan.json: no imported JSON field has an encoder.
			return "", planUnreachable("json encoder on %s", field.Name)
		}
		return jsonDumpsValue(v, true)
	}
	return auditFieldText(field, v)
}
