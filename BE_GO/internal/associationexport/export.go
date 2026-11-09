package associationexport

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
)

// Conn is the connection the export reads through: Django's statements run
// with the simple protocol (client-side literals, text results, as psycopg's
// client-side binding), inside one transaction for the server-side cursors.
type Conn interface {
	Begin(ctx context.Context) (pgx.Tx, error)
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
}

// Stat is one entry of service.stats (insertion order matters: the manifest,
// the result and the notification e-mail list it).
type Stat struct {
	Name  string
	Count int64
}

// Serialized is the outcome of the model phase.
type Serialized struct {
	Stats     []Stat
	Documents []string // collected ids, first occurrence order
	Errors    []string
}

// Exporter serializes one association.
type Exporter struct {
	Plan        *Plan
	Association string
	Owner       string
	// Credentials are _credential_user_ids(); PasswordFor reports
	// is_restorable_password_hash of that user's stored password.
	Credentials map[string]bool
	PasswordFor func(id string) bool
	// Progress is called after each model (model_index, model_total, name).
	Progress  func(index, total int, name string)
	documents map[string]bool
	order     []string
}

const chunkSize = 1000

func (e *Exporter) bind(sql string, params []Param, expand map[string][]string) (string, []any, error) {
	var args []any
	var out strings.Builder
	index := 0
	for i := 0; i < len(sql); i++ {
		if sql[i] != '%' || i+1 >= len(sql) {
			out.WriteByte(sql[i])
			continue
		}
		if sql[i+1] == '%' {
			out.WriteByte('%')
			i++
			continue
		}
		if sql[i+1] != 's' {
			out.WriteByte(sql[i])
			continue
		}
		if index >= len(params) {
			return "", nil, errors.New("associationexport: parameter count")
		}
		p := params[index]
		index++
		i++
		var list []string
		switch {
		case p.Documents:
			list = expand["documents"]
		case p.Sources:
			list = expand["sources"]
		}
		if p.Documents || p.Sources {
			// IN (%s, %s) of the plan: the first placeholder is the list, the
			// second (dropped from params for prefetches) its continuation.
			if p.Documents {
				index++ // the plan records both placeholders of IN (%s, %s)
			}
			if !strings.HasPrefix(sql[i+1:], ", %s)") {
				return "", nil, errors.New("associationexport: IN list placeholder")
			}
			i += len(", %s")
			for j, value := range list {
				if j > 0 {
					out.WriteString(", ")
				}
				args = append(args, value)
				out.WriteString("$" + strconv.Itoa(len(args)))
			}
			continue
		}
		switch {
		case p.Symbol == "association":
			args = append(args, e.Association)
		case p.Symbol == "owner":
			args = append(args, e.Owner)
		case p.Bool != nil:
			args = append(args, *p.Bool)
		case p.Int != nil:
			args = append(args, *p.Int)
		case p.Str != nil:
			args = append(args, *p.Str)
		default:
			return "", nil, errors.New("associationexport: unknown parameter")
		}
		out.WriteString("$" + strconv.Itoa(len(args)))
	}
	if index != len(params) {
		return "", nil, errors.New("associationexport: unused parameters")
	}
	return out.String(), args, nil
}

// textRows reads all rows as PostgreSQL text (nil for NULL).
func textRows(rows pgx.Rows) ([]string, [][]*string, error) {
	defer rows.Close()
	var names []string
	for _, f := range rows.FieldDescriptions() {
		names = append(names, f.Name)
	}
	var out [][]*string
	for rows.Next() {
		raw := rows.RawValues()
		row := make([]*string, len(raw))
		for i, value := range raw {
			if value != nil {
				s := string(value)
				row[i] = &s
			}
		}
		out = append(out, row)
	}
	return names, out, rows.Err()
}

func (e *Exporter) collect(id string) {
	if e.documents == nil {
		e.documents = map[string]bool{}
	}
	if !e.documents[id] {
		e.documents[id] = true
		e.order = append(e.order, id)
	}
}

// Run writes data/<prefix>.json for every model into dir (the data, system
// and files directories exist), as AssociationExportService.export's model
// loop: a model whose export raises leaves "Failed to export <Model>" in the
// errors and the loop continues.
func (e *Exporter) Run(ctx context.Context, conn Conn, dir string) (*Serialized, error) {
	result := &Serialized{}
	for index, model := range e.Plan.Models {
		count, err := e.exportModel(ctx, conn, dir, model)
		if err != nil {
			return nil, err
		}
		result.Stats = append(result.Stats, Stat{Name: model.Name, Count: count})
		if e.Progress != nil {
			e.Progress(index+1, len(e.Plan.Models), model.Name)
		}
	}
	result.Documents = append([]string(nil), e.order...)
	return result, nil
}

func (e *Exporter) exportModel(ctx context.Context, conn Conn, dir string, model Model) (int64, error) {
	path := filepath.Join(dir, filepath.FromSlash(model.File))
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		return 0, err
	}
	file, err := os.Create(path)
	if err != nil {
		return 0, err
	}
	defer file.Close()
	if _, err = file.WriteString("["); err != nil {
		return 0, err
	}
	var count int64
	documents := e.order
	if model.Name != "Document" || len(documents) > 0 {
		sql, args, err := e.bind(model.Query.SQL, model.Query.Params, map[string][]string{"documents": documents})
		if err != nil {
			return 0, err
		}
		tx, err := conn.Begin(ctx)
		if err != nil {
			return 0, err
		}
		defer tx.Rollback(ctx)
		if _, err = tx.Exec(ctx, "DECLARE _go_export_cursor NO SCROLL CURSOR FOR "+sql, append([]any{pgx.QueryExecModeSimpleProtocol}, args...)...); err != nil {
			return 0, err
		}
		for {
			rows, err := tx.Query(ctx, "FETCH "+strconv.Itoa(chunkSize)+" FROM _go_export_cursor", pgx.QueryExecModeSimpleProtocol)
			if err != nil {
				return 0, err
			}
			names, chunk, err := textRows(rows)
			if err != nil {
				return 0, err
			}
			if len(chunk) == 0 {
				break
			}
			records, err := e.serializeChunk(ctx, tx, model, names, chunk)
			if err != nil {
				return 0, err
			}
			for _, record := range records {
				if count > 0 {
					if _, err = file.WriteString(","); err != nil {
						return 0, err
					}
				}
				if _, err = file.Write(record); err != nil {
					return 0, err
				}
				count++
			}
			if len(chunk) < chunkSize {
				break
			}
		}
		if err = tx.Commit(ctx); err != nil {
			return 0, err
		}
	}
	if _, err = file.WriteString("]"); err != nil {
		return 0, err
	}
	return count, file.Close()
}

func (e *Exporter) serializeChunk(ctx context.Context, tx pgx.Tx, model Model, names []string, chunk [][]*string) ([][]byte, error) {
	position := map[string]int{}
	for i, name := range names {
		position[name] = i
	}
	pkIndex, ok := position[model.PK]
	if !ok {
		return nil, fmt.Errorf("associationexport: %s primary key column", model.Name)
	}
	related := make([]map[string][]string, len(model.M2M))
	if len(model.M2M) > 0 {
		var sources []string
		for _, row := range chunk {
			sources = append(sources, *row[pkIndex])
		}
		for i, relation := range model.M2M {
			sql, args, err := e.bind(relation.SQL, relation.Params, map[string][]string{"sources": sources})
			if err != nil {
				return nil, err
			}
			rows, err := tx.Query(ctx, sql, append([]any{pgx.QueryExecModeSimpleProtocol}, args...)...)
			if err != nil {
				return nil, err
			}
			rnames, rrows, err := textRows(rows)
			if err != nil {
				return nil, err
			}
			source, target := -1, -1
			for j, name := range rnames {
				switch name {
				case relation.SourceColumn:
					source = j
				case relation.TargetPK:
					if target < 0 {
						target = j
					}
				}
			}
			if source < 0 || target < 0 {
				return nil, fmt.Errorf("associationexport: prefetch columns of %s.%s", model.Name, relation.Key)
			}
			related[i] = map[string][]string{}
			for _, row := range rrows {
				related[i][*row[source]] = append(related[i][*row[source]], *row[target])
			}
		}
	}
	records := make([][]byte, 0, len(chunk))
	for _, row := range chunk {
		record := make(Dict, 0, len(model.Fields)+len(model.M2M)+1)
		value := func(column string) (string, bool) {
			i, ok := position[column]
			if !ok || row[i] == nil {
				return "", false
			}
			return *row[i], true
		}
		for _, field := range model.Fields {
			text, valid := "", false
			if field.Kind != "null" {
				if _, ok := position[field.Column]; !ok {
					return nil, fmt.Errorf("associationexport: column %s.%s", model.Name, field.Column)
				}
				text, valid = value(field.Column)
			}
			converted, err := Serialize(field, text, valid)
			if err != nil {
				return nil, err
			}
			record = append(record, Item{Key: field.Key, Value: converted})
		}
		pk := *row[pkIndex]
		for i, relation := range model.M2M {
			ids := related[i][pk]
			if ids == nil {
				ids = []string{}
			}
			record = append(record, Item{Key: relation.Key, Value: ids})
		}
		if model.Name == "User" && e.Credentials[pk] {
			if password, ok := value("password"); ok && e.PasswordFor(pk) {
				record = append(record, Item{Key: "password", Value: password})
			}
		}
		// _collect_document_references: the FK attributes, then the M2M ids.
		for _, column := range model.DocumentAttributes {
			if id, ok := value(column); ok && id != "" {
				e.collect(id)
			}
		}
		for i, relation := range model.M2M {
			if documentM2M[relation.Key] {
				for _, id := range related[i][pk] {
					e.collect(id)
				}
			}
		}
		encoded, err := AppendJSON(nil, record)
		if err != nil {
			return nil, err
		}
		records = append(records, encoded)
	}
	return records, nil
}
