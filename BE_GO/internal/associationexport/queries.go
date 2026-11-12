package associationexport

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

// query runs one of the plan's statements with the simple protocol and
// returns its text rows.
func (e *Exporter) query(ctx context.Context, conn Conn, q Query, expand map[string][]string) ([]string, [][]*string, error) {
	sql, args, err := e.bind(q.SQL, q.Params, expand)
	if err != nil {
		return nil, nil, err
	}
	rows, err := conn.Query(ctx, sql, append([]any{pgx.QueryExecModeSimpleProtocol}, args...)...)
	if err != nil {
		return nil, nil, err
	}
	return textRows(rows)
}

func column(names []string, name string) int {
	for i, n := range names {
		if n == name {
			return i
		}
	}
	return -1
}

// CredentialPasswords is _credential_user_ids() with each account's stored
// password.
func (e *Exporter) CredentialPasswords(ctx context.Context, conn Conn) (map[string]string, error) {
	_, rows, err := e.query(ctx, conn, e.Plan.Queries.Credentials, nil)
	if err != nil {
		return nil, err
	}
	out := map[string]string{}
	if len(rows) == 0 {
		return out, nil
	}
	ids := make([]string, 0, len(rows))
	for _, row := range rows {
		if row[0] == nil {
			return nil, errors.New("associationexport: credential id")
		}
		ids = append(ids, *row[0])
	}
	result, err := conn.Query(ctx, `SELECT user_id::text, password FROM bakney_user WHERE user_id = ANY($1::uuid[])`, ids)
	if err != nil {
		return nil, err
	}
	_, passwords, err := textRows(result)
	if err != nil {
		return nil, err
	}
	for _, row := range passwords {
		password := ""
		if row[1] != nil {
			password = *row[1]
		}
		out[*row[0]] = password
	}
	return out, nil
}

// CleanupExport is one export archive row cleanup_old_exports may delete.
type CleanupExport struct {
	Archive, Association, Document string
	Created                        time.Time
	Filepath                       string
}

// CleanupCandidates is the exports queryset of cleanup_old_exports
// (select_related document, newest date first); excess is
// remaining_exports[max_count:].
func (e *Exporter) CleanupCandidates(ctx context.Context, conn Conn, excess bool) ([]CleanupExport, error) {
	q := e.Plan.Queries.Cleanup
	if excess {
		q = e.Plan.Queries.CleanupExcess
	}
	_, rows, err := e.query(ctx, conn, q, nil)
	if err != nil {
		return nil, err
	}
	var out []CleanupExport
	for _, row := range rows {
		// archive: id, association, document, date, folder; document: id,
		// creation_date, filepath, filename, file_size_bytes, token.
		if len(row) != 11 || row[0] == nil || row[1] == nil || row[2] == nil || row[6] == nil {
			return nil, errors.New("associationexport: cleanup row")
		}
		created, err := parseTimestamp(*row[6])
		if err != nil {
			return nil, err
		}
		export := CleanupExport{Archive: *row[0], Association: *row[1], Document: *row[2], Created: created}
		if row[7] != nil {
			export.Filepath = *row[7]
		}
		out = append(out, export)
	}
	return out, nil
}

// FileDocument is one document of export_files' annotated queryset.
type FileDocument struct {
	ID, Filename, Filepath string
	FilepathSet            bool
	Created                time.Time
	Medical, Invoice, File bool
}

// Files is Document.objects.filter(document_id__in=ids).annotate(...)
// iterated in query order.
func (e *Exporter) Files(ctx context.Context, conn Conn, ids []string) ([]FileDocument, error) {
	if len(ids) == 0 {
		return nil, nil
	}
	names, rows, err := e.query(ctx, conn, e.Plan.Queries.Files, map[string][]string{"documents": ids})
	if err != nil {
		return nil, err
	}
	index := map[string]int{}
	for _, name := range []string{"document_id", "creation_date", "filepath", "filename", "export_is_medical_certificate",
		"export_is_invoice", "export_is_subscription_file"} {
		if index[name] = column(names, name); index[name] < 0 {
			return nil, errors.New("associationexport: files columns")
		}
	}
	var out []FileDocument
	for _, row := range rows {
		doc := FileDocument{ID: *row[index["document_id"]]}
		if v := row[index["filename"]]; v != nil {
			doc.Filename = *v
		}
		if v := row[index["filepath"]]; v != nil {
			doc.Filepath, doc.FilepathSet = *v, true
		}
		if v := row[index["creation_date"]]; v != nil {
			if doc.Created, err = parseTimestamp(*v); err != nil {
				return nil, err
			}
		}
		flag := func(name string) bool { v := row[index[name]]; return v != nil && *v == "t" }
		doc.Medical, doc.Invoice, doc.File = flag("export_is_medical_certificate"), flag("export_is_invoice"), flag("export_is_subscription_file")
		out = append(out, doc)
	}
	return out, nil
}

// SignatureFile is one subscription with a private signature object.
type SignatureFile struct{ Subscription, Key string }

// Signatures is _export_subscription_signatures' queryset.
func (e *Exporter) Signatures(ctx context.Context, conn Conn) ([]SignatureFile, error) {
	names, rows, err := e.query(ctx, conn, e.Plan.Queries.Signatures, nil)
	if err != nil {
		return nil, err
	}
	id, key := column(names, "subscription_id"), column(names, "signature_storage_key")
	if id < 0 || key < 0 {
		return nil, errors.New("associationexport: signature columns")
	}
	var out []SignatureFile
	for _, row := range rows {
		out = append(out, SignatureFile{Subscription: *row[id], Key: *row[key]})
	}
	return out, nil
}

// AppendJSONIndent is json.dump(value, indent=n, ensure_ascii=False).
func AppendJSONIndent(b []byte, value any, indent int) ([]byte, error) {
	return appendIndented(b, value, indent, 0)
}

func appendIndented(b []byte, value any, indent, level int) ([]byte, error) {
	newline := func(b []byte, level int) []byte {
		b = append(b, '\n')
		for i := 0; i < indent*level; i++ {
			b = append(b, ' ')
		}
		return b
	}
	switch v := value.(type) {
	case []any:
		if len(v) == 0 {
			return append(b, "[]"...), nil
		}
		b = append(b, '[')
		for i, item := range v {
			if i > 0 {
				b = append(b, ',')
			}
			b = newline(b, level+1)
			var err error
			if b, err = appendIndented(b, item, indent, level+1); err != nil {
				return nil, err
			}
		}
		return append(newline(b, level), ']'), nil
	case Dict:
		if len(v) == 0 {
			return append(b, "{}"...), nil
		}
		b = append(b, '{')
		for i, item := range v {
			if i > 0 {
				b = append(b, ',')
			}
			b = newline(b, level+1)
			b = appendPythonString(b, item.Key)
			b = append(b, ": "...)
			var err error
			if b, err = appendIndented(b, item.Value, indent, level+1); err != nil {
				return nil, err
			}
		}
		return append(newline(b, level), '}'), nil
	}
	return AppendJSON(b, value)
}
