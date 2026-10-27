package repository

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/jmoiron/sqlx"
)

// ErrModelDeleteMissing means the row to delete does not exist.
var ErrModelDeleteMissing = errors.New("model row to delete does not exist")

type modelColumn struct {
	Name       string `db:"name"`
	Type       string `db:"type"`
	ForeignKey bool   `db:"foreign_key"`
}

// deleteModelWithAudit deletes one row and renders auditlog's delete changes
// from its stored values: each concrete field against 'None' with None
// omitted, except null foreign keys (smart_str(strings_only=True) keeps None,
// which differs from the absent side's 'None') and null JSON fields ('null'). Strings are as stored,
// booleans True/False, numbers as their PostgreSQL text (str(Decimal) for
// numeric), dates ISO, timestamps naive UTC str(), JSON sorted ASCII dumps.
// Foreign keys use the Django field name (the column without "_id").
func deleteModelWithAudit(ctx context.Context, tx *sqlx.Tx, table, key, id string) (map[string][2]string, map[string]any, error) {
	return deleteModelWithAuditReverse(ctx, tx, table, key, id, nil)
}

// deleteModelWithAuditReverse also records the model's reverse foreign keys
// that declare a related_name: auditlog walks _meta.get_fields() on delete,
// and those accessors render as their empty manager ("app.Model.None").
func deleteModelWithAuditReverse(ctx context.Context, tx *sqlx.Tx, table, key, id string, reverse map[string]string) (map[string][2]string, map[string]any, error) {
	columns := []modelColumn{}
	if err := tx.SelectContext(ctx, &columns, `SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
 EXISTS(SELECT 1 FROM pg_constraint c WHERE c.conrelid=a.attrelid AND c.contype='f' AND c.conkey=ARRAY[a.attnum]) AS foreign_key
 FROM pg_attribute a WHERE a.attrelid=$1::regclass AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum`, table); err != nil {
		return nil, nil, err
	}
	var raw []byte
	err := tx.GetContext(ctx, &raw, `SELECT to_jsonb(t) FROM `+table+` t WHERE `+key+`=$1 FOR UPDATE`, id)
	if err != nil {
		return nil, nil, errors.Join(ErrModelDeleteMissing, err)
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	stored := map[string]any{}
	if err = decoder.Decode(&stored); err != nil {
		return nil, nil, err
	}
	changes := map[string][2]string{}
	for _, column := range columns {
		value := stored[column.Name]
		name := column.Name
		if column.ForeignKey {
			name = strings.TrimSuffix(name, "_id")
		}
		if value == nil {
			switch {
			case column.ForeignKey:
				changes[name] = [2]string{"None", "None"}
			case column.Type == "jsonb" || column.Type == "json":
				// JSONField values render through json.dumps: None is 'null'.
				changes[name] = [2]string{"null", "None"}
			}
			continue
		}
		rendered, err := renderAuditValue(column.Type, value, raw, column.Name)
		if err != nil {
			return nil, nil, err
		}
		changes[name] = [2]string{rendered, "None"}
	}
	for name, manager := range reverse {
		changes[name] = [2]string{manager, "None"}
	}
	if _, err = tx.ExecContext(ctx, `DELETE FROM `+table+` WHERE `+key+`=$1`, id); err != nil {
		return nil, nil, err
	}
	return changes, stored, nil
}

func renderAuditValue(kind string, value any, row []byte, column string) (string, error) {
	switch {
	case kind == "boolean":
		if value == true {
			return "True", nil
		}
		return "False", nil
	case kind == "jsonb" || kind == "json":
		// Re-parse the field's JSON with the Python-compatible reader.
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(row, &fields); err != nil {
			return "", err
		}
		parsed, err := dto.ParseProfileTablesJSON(fields[column])
		if err != nil {
			return "", err
		}
		return string(dto.PythonJSONDumpsSorted(parsed)), nil
	case strings.HasPrefix(kind, "timestamp"):
		moment, err := time.Parse(time.RFC3339Nano, value.(string))
		if err != nil {
			return "", err
		}
		return pythonNaiveUTC(moment), nil
	}
	switch v := value.(type) {
	case string:
		return v, nil
	case json.Number:
		return v.String(), nil
	}
	return "", errors.New("unsupported audit column type " + kind)
}

func pythonNaiveUTC(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02 15:04:05")
	}
	return value.Format("2006-01-02 15:04:05.000000")
}
