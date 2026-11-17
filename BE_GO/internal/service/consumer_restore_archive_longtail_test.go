package service

import (
	"archive/zip"
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyzipfile"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

func TestRestoreReaderErrorClasses(t *testing.T) {
	damaged := []string{"BadZipFile", "ValueError", "TypeError", "KeyError", "AttributeError", "zlib.error",
		"UnicodeError", "UnicodeDecodeError", "RuntimeError", "NotImplementedError", "RecursionError", "ValidationError"}
	for _, class := range damaged {
		err := restoreReaderError(&archiveError{exc: &pyzipfile.Exception{Type: class, Text: "x"}})
		if !errors.Is(err, errRestoreDamaged) {
			t.Errorf("%s = %v, want the damaged RestoreError", class, err)
		}
	}
	// Archive.validate() does not catch these: Django answers its 500 page.
	for _, class := range []string{"EOFError", "OSError", "LZMAError"} {
		err := restoreReaderError(&archiveError{exc: &pyzipfile.Exception{Type: class, Text: "x"}})
		if !errors.Is(err, refusal.ErrAppState) {
			t.Errorf("%s = %v, want AppState", class, err)
		}
	}
	if err := restoreReaderError(errors.New("read failed")); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("a file read failure = %v, want AppState", err)
	}
}

func TestRestoreJSONDecoding(t *testing.T) {
	cases := []struct {
		name string
		data string
		want any
	}{
		{"null", `null`, nil},
		{"bool", `true`, true},
		{"number", `12`, json.Number("12")},
		{"float", `1.5`, json.Number("1.5")},
		{"utf8-bom", "\xef\xbb\xbf{}", domain.RequestObject{}},
		{"lone-surrogate", `"\ud800"`, "\xed\xa0\x80"}, // U+D800 in Python's code-unit form
	}
	for _, c := range cases {
		value, err := restoreJSON([]byte(c.data), restoreInstanceJSONEdge)
		if err != nil {
			t.Errorf("%s: %v", c.name, err)
			continue
		}
		if !dto.PythonEqual(value, c.want) {
			t.Errorf("%s decoded as %#v, want %#v", c.name, value, c.want)
		}
	}
	value, err := restoreJSON([]byte(`NaN`), restoreInstanceJSONEdge)
	if err != nil || !math.IsNaN(value.(float64)) {
		t.Errorf("NaN decoded as %#v, %v", value, err)
	}
	value, err = restoreJSON([]byte(`{"n": Infinity, "m": -Infinity}`), restoreInstanceJSONEdge)
	object := value.(domain.RequestObject)
	n, _ := importGet(object, "n")
	m, _ := importGet(object, "m")
	if err != nil || !math.IsInf(n.(float64), 1) || !math.IsInf(m.(float64), -1) {
		t.Errorf("infinities decoded as %#v, %v", value, err)
	}
	value, err = restoreJSON([]byte(`1e999`), restoreInstanceJSONEdge)
	if err != nil || !math.IsInf(value.(float64), 1) {
		t.Errorf("1e999 decoded as %#v, %v", value, err)
	}
	// UTF-16LE with a BOM, as json.loads accepts it.
	utf16 := []byte{0xff, 0xfe, '{', 0, '}', 0}
	if value, err := restoreJSON(utf16, restoreInstanceJSONEdge); err != nil || len(value.(domain.RequestObject)) != 0 {
		t.Errorf("UTF-16 manifest decoded as %#v, %v", value, err)
	}
	for _, invalid := range [][]byte{[]byte(`{`), []byte(`"\ud800`), {0xff, 0xfe, 0xff}} {
		if _, err := restoreJSON(invalid, restoreInstanceJSONEdge); !errors.Is(err, errRestoreDamaged) {
			t.Errorf("invalid JSON %q = %v, want damaged", invalid, err)
		}
	}
}

func TestRestoreValueConversions(t *testing.T) {
	inf := math.Inf(1)
	nan := math.NaN()
	cases := []struct {
		name string
		got  error
		want error
	}{
		{"int from infinity", restoreToPython("int", "", false, inf), errRestoreReaderRaised},
		{"int from NaN", restoreToPython("int", "", false, nan), errRestoreDamaged},
		{"int from huge int", restoreToPython("int", "", false, json.Number(strings.Repeat("9", 5000))), nil},
		{"uuid from float", restoreToPython("uuid", "", false, json.Number("1.5")), errRestoreDamaged},
		{"uuid from int", restoreToPython("uuid", "", false, json.Number("5")), nil},
		{"decimal from infinity", restoreToPython("decimal", "", false, inf), errRestoreDamaged},
		{"decimal from huge int", restoreToPython("decimal", "", false, json.Number(strings.Repeat("9", 5000))), nil},
		{"decimal from underscored text", restoreToPython("decimal", "", false, " 1_000.50 "), nil},
		{"datetime from unicode digits", restoreToPython("datetime", "", false, "٢٠٢٦-٠١-٠٢"), nil},
		{"time from one digit hour", restoreToPython("time", "", false, "3:04"), nil},
		{"time from one digit minute", restoreToPython("time", "", false, "03:0"), nil},
	}
	for _, c := range cases {
		if (c.want == nil) != (c.got == nil) || (c.want != nil && !errors.Is(c.got, c.want)) {
			t.Errorf("%s = %v, want %v", c.name, c.got, c.want)
		}
	}
	for _, text := range []string{"0x345678123456781234567812345678", "-0123456789abcdef0123456789abcdef",
		"١٢٣٤٥٦٧٨٩abcdef0123456789abcdeff"} {
		if _, err := restoreUUIDText(text); err != nil {
			t.Errorf("UUID(%q) = %v", text, err)
		}
	}
	if got, err := restoreIdentity("uuid", true); err != nil || got != "00000000-0000-0000-0000-000000000001" {
		t.Errorf("uuid identity of True = %q, %v", got, err)
	}
	if _, err := restoreIdentity("int", inf); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("int identity of Infinity = %v, want AppState", err)
	}
	if !restoreEqual(inf, json.Number("1e999")) || restoreEqual(nan, nan) {
		t.Error("restoreEqual must make infinities equal and NaN unequal")
	}
}

func TestRestoreSkippedRecordsShapes(t *testing.T) {
	plan, err := importeffects.LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	archive := &restoreArchive{edge: restoreInstanceJSONEdge, zip: restoreTestOpenZip(t, map[string]string{
		"system/00_billing_plans.json": `[{"a": 1}]`,
	})}
	defer archive.Close()
	cases := []struct {
		models any
		want   int
		broken bool
	}{
		{nil, 0, true},
		{"", 0, false},
		{domain.RequestObject{}, 0, false},
		{[]any{domain.RequestObject{{Name: "name", Value: "BillingPlan"},
			{Name: "file", Value: "system/00_billing_plans.json"}, {Name: "count", Value: json.Number("1.0")}}}, 0, true},
		{[]any{domain.RequestObject{{Name: "name", Value: "BillingPlan"},
			{Name: "file", Value: "system/00_billing_plans.json"}, {Name: "count", Value: json.Number("1")}}}, 1, false},
		{[]any{domain.RequestObject{{Name: "name", Value: []any{"x"}}}}, 0, true},
		{domain.RequestObject{{Name: "x", Value: true}}, 0, true},
	}
	for _, c := range cases {
		archive.manifest = domain.RequestObject{{Name: "models_exported", Value: c.models}}
		counts, err := archive.skippedRecords(plan)
		if c.broken {
			if !errors.Is(err, errRestoreDamaged) {
				t.Errorf("models_exported %#v = %v, want damaged", c.models, err)
			}
			continue
		}
		if err != nil || len(counts) != c.want {
			t.Errorf("models_exported %#v = %d counts, %v; want %d", c.models, len(counts), err, c.want)
		}
	}
}

const (
	restoreTestAssociation = "d7000000-0000-4000-8000-00000000e001"
	restoreTestOwner       = "d7000000-0000-4000-8000-00000000e002"
	restoreTestDocument    = "d7000000-0000-4000-8000-00000000e003"
	restoreTestAssociate1  = "d7000000-0000-4000-8000-00000000f001"
	restoreTestAssociate2  = "d7000000-0000-4000-8000-00000000f002"
	restoreTestForeign     = "d7000000-0000-4000-8000-00000000f0ff"
	restoreTestInvoice     = "d7000000-0000-4000-8000-00000000f004"
)

// restoreTestOpenZip writes a ZIP with the given member contents.
func restoreTestOpenZip(t *testing.T, entries map[string]string) *consumerImportArchive {
	t.Helper()
	path := filepath.Join(t.TempDir(), "backup.zip")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	writer := zip.NewWriter(file)
	for name, content := range entries {
		entry, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte(content)); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	archive, err := consumerImportOpenArchive(path)
	if err != nil {
		t.Fatal(err)
	}
	return archive
}

func restoreTestRows() map[string][]string {
	return map[string][]string{
		"SportAssociation": {`{"sport_association_id":"` + restoreTestAssociation + `","user_id":"` + restoreTestOwner + `","denomination":"Associazione archiviata"}`},
		"User":             {`{"user_id":"` + restoreTestOwner + `","email":"titolare@archivio.example.test"}`},
	}
}

// restoreTestArchive writes the export layout for rows and validates it.
func restoreTestArchive(t *testing.T, rows map[string][]string, extra map[string]string) (*restoreArchive, *consumerImportRun) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "backup.zip")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	writer := zip.NewWriter(file)
	sections := map[string]string{}
	for _, model := range restoreExportPlan.Models {
		sections[model.Name] = model.File
	}
	for name, file := range extra {
		sections[name] = file
	}
	modelsExported := []any{}
	for name, file := range sections {
		modelsExported = append(modelsExported, map[string]any{"name": name, "file": file, "count": len(rows[name])})
	}
	manifest := map[string]any{"export_format": "bakney_sport_export_v1", "version": "1.0.0",
		"export_date": "2026-03-30T10:00:00+00:00",
		"association": map[string]any{"sport_association_id": restoreTestAssociation,
			"denomination": "Associazione archiviata"},
		"models_exported": modelsExported, "statistics": map[string]any{"files_exported": 0, "files_failed": 0},
		"errors": []any{}}
	manifestRaw, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	entries := map[string]string{"manifest.json": string(manifestRaw)}
	for name, file := range sections {
		entries[file] = "[" + strings.Join(rows[name], ",") + "]"
	}
	for name, content := range extra {
		if _, listed := sections[name]; !listed {
			entries[name] = content
		}
	}
	for name, content := range entries {
		entry, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte(content)); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	plan, err := importeffects.LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	run := &consumerImportRun{s: &ConsumerImportAssociation{plan: plan}, ctx: context.Background(), path: path}
	archive, err := validateRestoreArchive(run, path, restoreLimits{Expanded: 20 << 30, JSON: 512 << 20, File: 5 << 30})
	if err != nil {
		t.Fatalf("validate: %v", err)
	}
	return archive, run
}

func TestRestoreNormalizesLegacyInvoices(t *testing.T) {
	rows := restoreTestRows()
	rows["Document"] = []string{`{"document_id":"` + restoreTestDocument + `","filename":"scheda.pdf","filepath":"","file_size_bytes":1}`}
	rows["SportAssociationInvoices"] = []string{`{"sport_association_invoice_id":"` + restoreTestInvoice +
		`","sport_association_id":"` + restoreTestAssociation + `","invoice_date":"2026-03-01","document_id":"` + restoreTestDocument + `"}`}
	archive, _ := restoreTestArchive(t, rows, map[string]string{"SportAssociationInvoices": "data/48_sport_association_invoices.json"})
	defer archive.Close()
	if archive.legacyInvoices != 1 || len(archive.records["SportAssociationDocumentsArchive"]) != 1 {
		t.Fatalf("legacy invoices = %d, archive rows = %d", archive.legacyInvoices, len(archive.records["SportAssociationDocumentsArchive"]))
	}
	if _, kept := archive.records["SportAssociationInvoices"]; kept {
		t.Fatal("the legacy invoice section must be popped")
	}
	preview, err := archive.summary()
	if err != nil {
		t.Fatal(err)
	}
	if value, _ := importGet(preview, "legacy_invoices"); value != json.Number("1") {
		t.Errorf("preview legacy_invoices = %#v", value)
	}
}

func TestRestoreNormalizesLegacyTutors(t *testing.T) {
	rows := restoreTestRows()
	rows["Associate"] = []string{
		`{"associate_id":"` + restoreTestAssociate1 + `","sport_association_id":"` + restoreTestAssociation + `","tax_code":"aaa"}`,
		`{"associate_id":"` + restoreTestAssociate2 + `","tax_code":"aaa","first_name":"Vecchio","last_name":"Tutore"}`,
	}
	archive, _ := restoreTestArchive(t, rows, nil)
	defer archive.Close()
	if len(archive.tutorReassignments) != 1 {
		t.Fatalf("tutor reassignments = %d", len(archive.tutorReassignments))
	}
	detail := archive.tutorReassignments[0]
	if value, _ := importGet(detail, "original_tax_code"); !dto.PythonEqual(value, "aaa") {
		t.Errorf("original_tax_code = %#v", value)
	}
	if value, _ := importGet(detail, "conflicting_associate_id"); !dto.PythonEqual(value, restoreTestAssociate1) {
		t.Errorf("conflicting_associate_id = %#v", value)
	}
	preview, err := archive.summary()
	if err != nil {
		t.Fatal(err)
	}
	if value, _ := importGet(preview, "tutor_tax_code_conflicts"); value != json.Number("1") {
		t.Errorf("preview tutor_tax_code_conflicts = %#v", value)
	}
	// The legacy row keeps its place and has its conflicting tax code cleared.
	for _, row := range archive.records["Associate"] {
		if value, _ := importGet(row, "associate_id"); dto.PythonEqual(value, restoreTestAssociate2) {
			if tax, _ := importGet(row, "tax_code"); tax != nil {
				t.Errorf("the conflicting legacy tax code must be cleared, got %#v", tax)
			}
		}
	}
}

func TestRestoreNormalizesReassignedTutor(t *testing.T) {
	rows := restoreTestRows()
	rows["Associate"] = []string{
		`{"associate_id":"` + restoreTestAssociate1 + `","sport_association_id":"` + restoreTestAssociation + `","tax_code":"aaa"}`,
		`{"associate_id":"` + restoreTestAssociate2 + `","sport_association_id":"` + restoreTestForeign + `","tax_code":"bbb"}`,
	}
	rows["AssociateTutorRelation"] = []string{`{"id":1,"associate_id":"` + restoreTestAssociate1 + `","tutor_id":"` + restoreTestAssociate2 + `"}`}
	archive, _ := restoreTestArchive(t, rows, nil)
	defer archive.Close()
	if !archive.reassignedTutors[restoreTestAssociate2] || len(archive.tutorReassignments) != 1 {
		t.Fatalf("reassigned tutors = %#v", archive.reassignedTutors)
	}
	preview, err := archive.summary()
	if err != nil {
		t.Fatal(err)
	}
	if value, _ := importGet(preview, "reassigned_tutors"); value != json.Number("1") {
		t.Errorf("preview reassigned_tutors = %#v", value)
	}
	for _, row := range archive.records["Associate"] {
		if value, _ := importGet(row, "associate_id"); dto.PythonEqual(value, restoreTestAssociate2) {
			if association, _ := importGet(row, "sport_association_id"); !dto.PythonEqual(association, restoreTestAssociation) {
				t.Errorf("reassigned association = %#v", association)
			}
		}
	}
}

// TestRestoreNormalizedArchives: the archives validate() rewrites are the ones
// the Celery restore hands to Django (normalized), an ignored foreign tutor
// included; a plain archive is not.
func TestRestoreNormalizedArchives(t *testing.T) {
	plain, _ := restoreTestArchive(t, restoreTestRows(), nil)
	defer plain.Close()
	if plain.normalized() {
		t.Error("a plain archive is not normalized")
	}
	rows := restoreTestRows()
	rows["Associate"] = []string{
		`{"associate_id":"` + restoreTestAssociate1 + `","sport_association_id":"` + restoreTestAssociation + `","tax_code":"aaa"}`,
		`{"associate_id":"` + restoreTestAssociate2 + `","sport_association_id":"` + restoreTestForeign + `","tax_code":"bbb"}`,
	}
	ignored, _ := restoreTestArchive(t, rows, nil)
	defer ignored.Close()
	if ignored.ignoredTutors != 1 || !ignored.normalized() || len(ignored.records["Associate"]) != 1 {
		t.Errorf("ignored tutors = %d, normalized = %v, associates = %d", ignored.ignoredTutors, ignored.normalized(),
			len(ignored.records["Associate"]))
	}
}

// restoreTestManifestExtra copies a valid archive with an extra JSON value
// appended to manifest.json.
func restoreTestManifestExtra(t *testing.T, path, extra string) string {
	t.Helper()
	reader, err := zip.OpenReader(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()
	file, err := os.Create(filepath.Join(t.TempDir(), "deep.zip"))
	if err != nil {
		t.Fatal(err)
	}
	writer := zip.NewWriter(file)
	for _, entry := range reader.File {
		part, err := entry.Open()
		if err != nil {
			t.Fatal(err)
		}
		data, err := io.ReadAll(part)
		part.Close()
		if err != nil {
			t.Fatal(err)
		}
		if entry.Name == "manifest.json" {
			data = append(append([]byte{}, data[:len(data)-1]...), []byte(`, "extra": `+extra+`}`)...)
		}
		member, err := writer.Create(entry.Name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := member.Write(data); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	return file.Name()
}

// TestRestoreUploadJSONEdge: Archive.validate's json.loads on the upload
// request thread parses up to dataRestoreUploadJSONEdge and answers the
// damaged archive one level past it (owner decision X15).
func TestRestoreUploadJSONEdge(t *testing.T) {
	base, _ := restoreTestArchive(t, restoreTestRows(), nil)
	defer base.Close()
	plan, err := importeffects.LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	limits := restoreLimits{Expanded: 20 << 30, JSON: 512 << 20, File: 5 << 30}
	for _, total := range []int{dataRestoreUploadJSONEdge - 4, dataRestoreUploadJSONEdge} {
		path := restoreTestManifestExtra(t, base.path, strings.Repeat("[", total-1)+strings.Repeat("]", total-1))
		run := &consumerImportRun{s: &ConsumerImportAssociation{plan: plan}, ctx: context.Background(), path: path}
		archive, err := validateRestoreArchive(run, path, limits, dataRestoreUploadJSONEdge)
		if err != nil {
			t.Fatalf("manifest depth %d: %v", total, err)
		}
		archive.Close()
	}
	for _, total := range []int{dataRestoreUploadJSONEdge + 1, dataRestoreUploadJSONEdge + 30} {
		path := restoreTestManifestExtra(t, base.path, strings.Repeat("[", total-1)+strings.Repeat("]", total-1))
		run := &consumerImportRun{s: &ConsumerImportAssociation{plan: plan}, ctx: context.Background(), path: path}
		if _, err := validateRestoreArchive(run, path, limits, dataRestoreUploadJSONEdge); !errors.Is(err, errRestoreDamaged) {
			t.Fatalf("manifest depth %d: %v, want the damaged archive", total, err)
		}
	}
}

// TestMeasuredStoredJSONEdges pins the per-call edges of owner decision X15.
func TestMeasuredStoredJSONEdges(t *testing.T) {
	for name, edge := range map[string]struct{ got, want int }{
		"instructor report":   {instructorReportJSONEdge, 9940},
		"import validation":   {importValidationJSONEdge, 9942},
		"data restore upload": {dataRestoreUploadJSONEdge, 9942},
		"Celery restore":      {restoreInstanceJSONEdge, 9975},
		"Celery import":       {consumerImportJSONEdge, 9975},
	} {
		if edge.got != edge.want {
			t.Errorf("%s edge = %d, want %d", name, edge.got, edge.want)
		}
	}
}
