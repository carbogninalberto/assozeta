package service

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/associationexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pycodecs"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pydatetime"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyzipfile"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// restoreDamaged is the RestoreError Archive.validate() raises for the Python
// exceptions it catches (ValueError, TypeError, KeyError, AttributeError,
// BadZipFile, zlib.error, UnicodeError, RuntimeError, ValidationError).
const restoreDamaged = "Il backup ZIP è danneggiato o non rispetta il formato Bakney."

// restoreError is a RestoreError: its message becomes the receipt's error.
type restoreError struct{ message string }

func (e *restoreError) Error() string { return e.message }

func restoreFail(format string, args ...any) error {
	return &restoreError{message: fmt.Sprintf(format, args...)}
}

var errRestoreDamaged = &restoreError{message: restoreDamaged}

// errRestoreReaderRaised is the exception Archive.validate() does not catch
// (EOFError, OSError from bz2, LZMAError, or a class outside its except tuple):
// Django lets it escape the view and answers its 500 page.
var errRestoreReaderRaised = refusal.AppState("data restore: ZIP reader raised a class Archive.validate() does not catch")

// restoreDamagedClass reports whether validate()'s except tuple catches the
// Python exception class (including its subclasses the ZIP reader raises:
// NotImplementedError is a RuntimeError, UnicodeDecodeError a UnicodeError).
func restoreDamagedClass(class string) bool {
	switch class {
	case "ValueError", "TypeError", "KeyError", "AttributeError", "BadZipFile", "zlib.error",
		"UnicodeError", "UnicodeDecodeError", "UnicodeEncodeError", "UnicodeTranslateError",
		"RuntimeError", "NotImplementedError", "RecursionError", "ValidationError":
		return true
	}
	return false
}

// restoreReaderError classifies an exception the ZIP reader raised: validate()
// turns a caught class into its RestoreError and lets every other class escape.
func restoreReaderError(err error) error {
	if err == nil {
		return nil
	}
	var exc *pyzipfile.Exception
	if errors.As(err, &exc) && restoreDamagedClass(exc.Type) {
		return errRestoreDamaged
	}
	// EOFError, OSError, LZMAError (and a non-Python read failure: Python's
	// io.open/read raises OSError there too).
	return errRestoreReaderRaised
}

// restoreLimits are DATA_RESTORE_MAX_EXPANDED_BYTES, _JSON_BYTES, _FILE_BYTES.
type restoreLimits struct{ Expanded, JSON, File int64 }

// restoreMissingRelation is one optional link Archive._validate_graph records.
type restoreMissingRelation struct{ model, record, field, targetModel, target string }

// restoreArchive is instance.restore.archive.Archive after validate().
type restoreArchive struct {
	path     string
	zip      *consumerImportArchive
	manifest domain.RequestObject
	// records are the non-retired data files by model name.
	records    map[string][]domain.RequestObject
	identities map[string]map[string]bool
	// identityList keeps each model's keys for the deletion plan.
	identityList     map[string][]string
	mediaKeys        []string
	media            map[string]string
	missingRelations []restoreMissingRelation
	missingBinary    int
	missingMedia     int
	associationID    string
	ownerID          string
	// skipped is skipped_records() of the validated manifest (summary reuses it).
	skipped celery.Dict
	// legacyInvoices counts the normalized legacy platform invoices.
	legacyInvoices int
	// reassignedTutors and tutorReassignments are _normalize_legacy_tutors'
	// report for the preview.
	reassignedTutors   map[string]bool
	tutorReassignments []domain.RequestObject
	// ignoredTutors counts the foreign anagrafiche nothing references.
	ignoredTutors int
}

func (a *restoreArchive) Close() {
	if a.zip != nil {
		a.zip.Close()
		a.zip = nil
	}
}

// restoreStr is str(value) for a decoded JSON value: Python's str() of a
// scalar, and repr() of a list or object. It never raises.
func restoreStr(value any) (string, error) {
	if parsed, ok := value.(importeffects.Value); ok {
		return parsed.Text()
	}
	return dto.PythonStr(value), nil
}

func restoreFormatSize(size int64) string {
	for _, unit := range []struct {
		name    string
		divisor int64
	}{{"GiB", 1 << 30}, {"MiB", 1 << 20}, {"KiB", 1 << 10}} {
		if size >= unit.divisor {
			return strconv.FormatFloat(float64(size)/float64(unit.divisor), 'f', 2, 64) + " " + unit.name
		}
	}
	return strconv.FormatInt(size, 10) + " byte"
}

// restoreFormatSizeUint is format_size for zipfile's unsigned file_size.
func restoreFormatSizeUint(size uint64) string {
	if size <= math.MaxInt64 {
		return restoreFormatSize(int64(size))
	}
	return strconv.FormatFloat(float64(size)/float64(1<<30), 'f', 2, 64) + " GiB"
}

// restorePosixParts is PurePosixPath(name).parts without the root.
func restorePosixParts(name string) []string {
	parts := []string{}
	for _, part := range strings.Split(name, "/") {
		if part != "" && part != "." {
			parts = append(parts, part)
		}
	}
	return parts
}

// validateRestoreArchive is Archive(path).validate(). It returns a
// *restoreError for the RestoreErrors Django raises, errRestoreReaderRaised
// where an uncaught exception reaches Django's 500 page, or another error.
func validateRestoreArchive(run *consumerImportRun, path string, limits restoreLimits) (*restoreArchive, error) {
	archive := &restoreArchive{path: path, records: map[string][]domain.RequestObject{}, media: map[string]string{}}
	isZip, err := consumerImportIsZipFile(path)
	if err != nil {
		return nil, err
	}
	if !isZip {
		return nil, errRestoreDamaged
	}
	zf, err := consumerImportOpenArchive(path)
	if err != nil {
		return nil, restoreReaderError(err)
	}
	archive.zip = zf
	if err := archive.validate(run, limits); err != nil {
		archive.Close()
		return nil, err
	}
	return archive, nil
}

func (a *restoreArchive) entries() []pyzipfile.Info {
	out := make([]pyzipfile.Info, len(a.zip.names))
	for i, name := range a.zip.names {
		out[i] = a.zip.byName[name]
	}
	return out
}

func (a *restoreArchive) json(name string) (any, error) {
	data, err := a.zip.read(name)
	if err != nil {
		return nil, restoreReaderError(err)
	}
	return restoreJSON(data)
}

// restoreJSON is json.loads(zf.read(name)) for the bytes of a member: the
// same BOM/UTF-8/16/32 detection and surrogatepass decoding, and the same
// NaN/Infinity constants. Every ValueError/UnicodeError it raises is caught by
// validate(), so a parse failure is errRestoreDamaged.
func restoreJSON(data []byte) (any, error) {
	name := restoreJSONEncoding(data)
	runes, err, ok := pycodecs.Decode(name, data, pycodecs.SurrogatePass)
	if !ok || err != nil {
		return nil, errRestoreDamaged
	}
	text := restoreJSONText(runes)
	text, floats := restoreJSONConstants(text)
	value, err := dto.ParseProfileTablesJSON([]byte(text))
	if err != nil {
		return nil, errRestoreDamaged
	}
	return restoreJSONFloats(value, floats), nil
}

// restoreJSONEncoding is json.detect_encoding.
func restoreJSONEncoding(data []byte) string {
	switch {
	case bytes.HasPrefix(data, []byte{0x00, 0x00, 0xfe, 0xff}), bytes.HasPrefix(data, []byte{0xff, 0xfe, 0x00, 0x00}):
		return "utf-32"
	case bytes.HasPrefix(data, []byte{0xfe, 0xff}), bytes.HasPrefix(data, []byte{0xff, 0xfe}):
		return "utf-16"
	case bytes.HasPrefix(data, []byte{0xef, 0xbb, 0xbf}):
		return "utf-8-sig"
	}
	if len(data) >= 4 {
		switch {
		case data[0] == 0:
			if data[1] == 0 {
				return "utf-32-be"
			}
			return "utf-16-be"
		case data[1] == 0:
			if data[2] == 0 && data[3] == 0 {
				return "utf-32-le"
			}
			return "utf-16-le"
		}
	} else if len(data) == 2 {
		if data[0] == 0 {
			return "utf-16-be"
		}
		if data[1] == 0 {
			return "utf-16-le"
		}
	}
	return "utf-8"
}

// restoreJSONText is the decoded str as UTF-8 JSON text. A lone surrogate
// (which a Python str keeps) becomes its \uXXXX escape so the JSON scanner can
// carry it through; the value it produces is the same lone surrogate.
func restoreJSONText(runes []rune) string {
	surrogate := false
	for _, r := range runes {
		if r >= 0xd800 && r <= 0xdfff {
			surrogate = true
			break
		}
	}
	if !surrogate {
		return string(runes)
	}
	var out strings.Builder
	for _, r := range runes {
		if r >= 0xd800 && r <= 0xdfff {
			fmt.Fprintf(&out, `\u%04x`, r)
			continue
		}
		out.WriteRune(r)
	}
	return out.String()
}

// restoreJSONConstants replaces the bare NaN, Infinity and -Infinity tokens
// json.loads accepts (outside strings) with number sentinels that no token in
// text contains. The sentinels come back as float64 values in restoreJSONFloats.
func restoreJSONConstants(text string) (string, map[string]float64) {
	nan := restoreJSONSentinel(text, "1")
	inf := restoreJSONSentinel(text, "2")
	negative := restoreJSONSentinel(text, "3")
	var out strings.Builder
	out.Grow(len(text))
	inString, escaped := false, false
	for i := 0; i < len(text); {
		c := text[i]
		if inString {
			out.WriteByte(c)
			switch {
			case escaped:
				escaped = false
			case c == '\\':
				escaped = true
			case c == '"':
				inString = false
			}
			i++
			continue
		}
		if c == '"' {
			inString = true
			out.WriteByte(c)
			i++
			continue
		}
		switch {
		case strings.HasPrefix(text[i:], "-Infinity"):
			out.WriteString("-" + negative)
			i += len("-Infinity")
		case strings.HasPrefix(text[i:], "Infinity"):
			out.WriteString(inf)
			i += len("Infinity")
		case strings.HasPrefix(text[i:], "NaN"):
			out.WriteString(nan)
			i += len("NaN")
		default:
			out.WriteByte(c)
			i++
		}
	}
	return out.String(), map[string]float64{nan: math.NaN(), inf: math.Inf(1), "-" + negative: math.Inf(-1)}
}

// restoreJSONSentinel is a decimal integer absent from text.
func restoreJSONSentinel(text, mark string) string {
	base := strings.Repeat("9", 6000)
	for extra := 0; ; extra++ {
		candidate := base + strings.Repeat("7", extra) + mark
		if !strings.Contains(text, candidate) {
			return candidate
		}
	}
}

// restoreJSONFloats replaces the sentinels with float64 and normalizes a float
// literal Python turns into an infinity (json.loads("1e999")) the same way.
func restoreJSONFloats(value any, floats map[string]float64) any {
	switch v := value.(type) {
	case json.Number:
		if f, ok := floats[string(v)]; ok {
			return f
		}
		if strings.ContainsAny(string(v), ".eE") {
			if f, _ := strconv.ParseFloat(string(v), 64); math.IsInf(f, 0) {
				return f
			}
		}
		return v
	case domain.RequestObject:
		for i := range v {
			v[i].Value = restoreJSONFloats(v[i].Value, floats)
		}
		return v
	case []any:
		for i := range v {
			v[i] = restoreJSONFloats(v[i], floats)
		}
		return v
	}
	return value
}

// restoreSpecialFloat is the float64 a decoded JSON value holds when Python's
// json module produced a non-finite float (NaN, +/-Infinity or an overflow).
func restoreSpecialFloat(value any) (float64, bool) {
	switch v := value.(type) {
	case float64:
		return v, true
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			if f, _ := strconv.ParseFloat(string(v), 64); math.IsInf(f, 0) {
				return f, true
			}
		}
	}
	return 0, false
}

// restoreEqual is Python == of two decoded JSON values, including the
// non-finite floats json.loads produces (NaN never equals, infinities do).
func restoreEqual(left, right any) bool {
	lf, leftFloat := restoreSpecialFloat(left)
	rf, rightFloat := restoreSpecialFloat(right)
	if leftFloat || rightFloat {
		return leftFloat && rightFloat && lf == rf
	}
	return dto.PythonEqual(left, right)
}

func (a *restoreArchive) validate(run *consumerImportRun, limits restoreLimits) error {
	entries := a.entries()
	if len(entries) > 50000 {
		return restoreFail("Il backup contiene più di 50.000 file.")
	}
	var expanded uint64
	names := map[string]bool{}
	for _, entry := range entries {
		name := entry.Filename
		if names[name] {
			return restoreFail("File duplicato nel backup: %s.", name)
		}
		names[name] = true
		if strings.HasPrefix(name, "/") || restoreContains(restorePosixParts(name), "..") || restoreWindowsDrive(name) {
			return restoreFail("Percorso non consentito nel backup: %s.", name)
		}
		if strings.Contains(name, "\\") && !restoreBackslashMedia(name) {
			return restoreFail("Percorso non consentito nel backup: %s.", name)
		}
		if (entry.ExternalAttrs>>16)&0o170000 == 0o120000 {
			return restoreFail("Il backup contiene un collegamento simbolico non consentito: %s.", name)
		}
		if entry.FlagBits&1 != 0 {
			return restoreFail("Il backup contiene un file cifrato non supportato: %s.", name)
		}
		isJSON := strings.HasSuffix(pystr.Lower(name), ".json")
		limit, setting := limits.File, "DATA_RESTORE_MAX_FILE_BYTES"
		if isJSON {
			limit, setting = limits.JSON, "DATA_RESTORE_MAX_JSON_BYTES"
		}
		if limit < 0 || entry.FileSize > uint64(limit) {
			return restoreFail("Il file %s è troppo grande: %s non compressi; limite %s (%s).", name,
				restoreFormatSizeUint(entry.FileSize), restoreFormatSize(limit), setting)
		}
		expanded += entry.FileSize
	}
	if expanded > uint64(limits.Expanded) {
		return restoreFail("Il backup occupa %s non compressi; limite %s (DATA_RESTORE_MAX_EXPANDED_BYTES).",
			restoreFormatSize(int64(expanded)), restoreFormatSize(limits.Expanded))
	}
	if !a.zip.has("manifest.json") {
		return errRestoreDamaged
	}
	decoded, err := a.json("manifest.json")
	if err != nil {
		return err
	}
	manifest, ok := decoded.(domain.RequestObject)
	if !ok {
		return errRestoreDamaged
	}
	a.manifest = manifest
	format, _ := importGet(manifest, "export_format")
	version, _ := importGet(manifest, "version")
	if !restoreEqual(format, "bakney_sport_export_v1") || !restoreEqual(version, "1.0.0") {
		return restoreFail("Formato non supportato. Carica un export ZIP Bakney versione 1.0.0.")
	}
	manifestErrors, _ := importGet(manifest, "errors")
	if importTruthy(manifestErrors) {
		return restoreFail("Il backup segnala errori di esportazione. Genera un export completo.")
	}
	statistics, err := restoreGetDict(manifest, "statistics")
	if err != nil {
		return err
	}
	failed, _ := importGet(statistics, "files_failed")
	if importTruthy(failed) {
		return restoreFail("Il backup segnala errori di esportazione. Genera un export completo.")
	}
	plan := run.s.plan
	expected := map[string]string{}
	for _, model := range restoreExportPlan.Models {
		expected[model.Name] = model.File
	}
	for name, file := range plan.DeprecatedSections {
		expected[name] = file
	}
	skipped, err := a.skippedRecords(plan)
	if err != nil {
		return err
	}
	a.skipped = skipped
	expected["SportAssociationInvoices"] = "data/48_sport_association_invoices.json"
	specsValue, present := importGet(manifest, "models_exported")
	var specs []any
	switch v := specsValue.(type) {
	case []any:
		specs = v
	case domain.RequestObject:
		if len(v) > 0 {
			return errRestoreDamaged
		}
	case string:
		if v != "" {
			return errRestoreDamaged
		}
	default:
		if present {
			return errRestoreDamaged
		}
	}
	seen := map[string]bool{}
	for _, raw := range specs {
		spec, ok := raw.(domain.RequestObject)
		if !ok {
			return errRestoreDamaged
		}
		nameValue, ok1 := importGet(spec, "name")
		fileValue, ok2 := importGet(spec, "file")
		if !ok1 || !ok2 {
			return errRestoreDamaged
		}
		switch nameValue.(type) {
		case []any, domain.RequestObject:
			return errRestoreDamaged
		}
		nameText, _ := restoreStr(nameValue)
		name, isText := nameValue.(string)
		file, fileIsText := fileValue.(string)
		if !isText || expected[name] == "" || seen[name] || !fileIsText || file != expected[name] {
			return restoreFail("Modello o percorso non supportato nel backup: %s.", nameText)
		}
		seen[name] = true
		if !a.zip.has(file) {
			return restoreFail("File dichiarato ma mancante: %s.", file)
		}
		decoded, err := a.json(file)
		if err != nil {
			return err
		}
		rows, isList := decoded.([]any)
		if isList {
			count, present := importGet(spec, "count")
			if !present {
				return errRestoreDamaged
			}
			if !restoreEqual(int64ToNumber(len(rows)), count) {
				isList = false
			}
		}
		records := []domain.RequestObject{}
		for _, row := range rows {
			record, ok := row.(domain.RequestObject)
			if !ok {
				isList = false
				break
			}
			records = append(records, record)
		}
		if !isList {
			return restoreFail("Conteggio o contenuto non valido: %s.", file)
		}
		if _, deprecated := plan.DeprecatedSections[name]; !deprecated {
			a.records[name] = records
		}
	}
	missing := []string{}
	for _, entry := range plan.Order {
		if !seen[entry.Model] {
			missing = append(missing, entry.Model)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return restoreFail("Export incompleto: mancano %s.", strings.Join(missing, ", "))
	}
	associations := a.records["SportAssociation"]
	if len(associations) != 1 {
		return restoreFail("Il backup deve contenere una sola associazione.")
	}
	association := associations[0]
	idValue, ok1 := importGet(association, "sport_association_id")
	ownerValue, ok2 := importGet(association, "user_id")
	if !ok1 || !ok2 {
		return errRestoreDamaged
	}
	a.associationID, _ = restoreStr(idValue)
	a.ownerID, _ = restoreStr(ownerValue)
	owners := 0
	for _, user := range a.records["User"] {
		text, _ := restoreStr(importGetValue(user, "user_id"))
		if text == a.ownerID {
			owners++
		}
	}
	if owners != 1 {
		return restoreFail("Il titolare del backup è mancante o ambiguo.")
	}
	info, err := restoreGetDict(manifest, "association")
	if err != nil {
		return err
	}
	infoID, _ := restoreStr(importGetValue(info, "sport_association_id"))
	if infoID != a.associationID || !restoreEqual(importGetValue(info, "denomination"), importGetValue(association, "denomination")) {
		return restoreFail("Il manifest non corrisponde all’associazione esportata.")
	}
	if err := a.normalizeLegacyInvoices(); err != nil {
		return err
	}
	if err := a.normalizeLegacyTutors(plan); err != nil {
		return err
	}
	if err := a.validateGraph(plan); err != nil {
		return err
	}
	for _, entry := range entries {
		name := entry.Filename
		if strings.HasSuffix(name, "/") || !strings.HasPrefix(name, "files/") {
			continue
		}
		parts := strings.Split(name, "/")
		if len(parts) != 4 || parts[3] == "" {
			return restoreFail("Percorso allegato non valido.")
		}
		model := "Document"
		if parts[1] == "signatures" || parts[1] == "subscription_signatures" {
			model = "Subscription"
		}
		if !a.identities[model][parts[2]] {
			return restoreFail("Un allegato fa riferimento a un record mancante.")
		}
		key := model + ":" + parts[2]
		if _, duplicate := a.media[key]; duplicate {
			return restoreFail("Allegati duplicati per lo stesso record.")
		}
		a.media[key] = name
		a.mediaKeys = append(a.mediaKeys, key)
	}
	exported, present := importGet(statistics, "files_exported")
	if present && !restoreEqual(exported, int64ToNumber(len(a.media))) {
		return restoreFail("Il numero degli allegati non corrisponde al manifest.")
	}
	for _, name := range a.zip.names {
		if _, err := a.zip.verify(name); err != nil {
			var exc *pyzipfile.Exception
			if errors.As(err, &exc) && exc.Type == "BadZipFile" {
				// testzip() returns the first name whose read raised BadZipFile.
				return restoreFail("Il backup contiene un file danneggiato.")
			}
			return restoreReaderError(err)
		}
	}
	for _, row := range a.records["Document"] {
		id, _ := restoreStr(importGetValue(row, "document_id"))
		if _, staged := a.media["Document:"+id]; importTruthy(importGetValue(row, "filepath")) && !staged {
			a.missingBinary++
		}
	}
	urlOnly := 0
	for _, row := range a.records["Subscription"] {
		id, _ := restoreStr(importGetValue(row, "subscription_id"))
		_, staged := a.media["Subscription:"+id]
		key := importTruthy(importGetValue(row, "signature_storage_key"))
		if key && !staged {
			a.missingBinary++
		}
		if importTruthy(importGetValue(row, "signature_url")) && !key && !staged {
			urlOnly++
		}
	}
	a.missingMedia = a.missingBinary + urlOnly
	return nil
}

func int64ToNumber(n int) json.Number { return json.Number(strconv.Itoa(n)) }

// restoreGetDict is manifest.get(key, {}) used as a dict: None or another
// type raises AttributeError.
func restoreGetDict(object domain.RequestObject, key string) (domain.RequestObject, error) {
	value, present := importGet(object, key)
	if !present {
		return domain.RequestObject{}, nil
	}
	dict, ok := value.(domain.RequestObject)
	if !ok {
		return nil, errRestoreDamaged
	}
	return dict, nil
}

// restoreWindowsSeparator is a separator of ntpath ("\\" or "/").
func restoreWindowsSeparator(r rune) bool { return r == '\\' || r == '/' }

// restoreWindowsDrive is bool(PureWindowsPath(name).drive): ntpath.splitroot
// gives a drive to any code point followed by ":" and to a name starting with
// two separators (a UNC or device path).
func restoreWindowsDrive(name string) bool {
	runes := pystr.Runes(name)
	if len(runes) < 2 {
		return false
	}
	return runes[1] == ':' || restoreWindowsSeparator(runes[0]) && restoreWindowsSeparator(runes[1])
}

// restoreBackslashMedia is the check _validate_entries makes on a name holding
// a backslash: only a media member files/<category>/<id>/<basename> may carry
// one, in its basename, and that basename must have no Windows drive or root
// and no ".." component once its backslashes are read as separators.
func restoreBackslashMedia(name string) bool {
	parts := strings.Split(name, "/")
	if len(parts) != 4 || parts[0] != "files" {
		return false
	}
	for index, part := range parts {
		if part == "" || index < len(parts)-1 && strings.Contains(part, "\\") {
			return false
		}
	}
	basename := parts[3]
	runes := pystr.Runes(basename)
	if restoreWindowsDrive(basename) || len(runes) > 0 && restoreWindowsSeparator(runes[0]) {
		return false
	}
	return !restoreContains(restorePosixParts(strings.ReplaceAll(basename, "\\", "/")), "..")
}

// restoreIterable is Python's iteration over a decoded JSON value as
// `for value in values`: lists yield their items, a str its code points and a
// dict its keys; None and numbers raise TypeError.
func restoreIterable(value any) ([]any, bool) {
	switch v := value.(type) {
	case []any:
		return v, true
	case string:
		items := []any{}
		for _, r := range pystr.Runes(v) {
			items = append(items, pystr.String([]rune{r}))
		}
		return items, true
	case domain.RequestObject:
		items := make([]any, len(v))
		for i, field := range v {
			items[i] = field.Name
		}
		return items, true
	}
	return nil, false
}

// restoreUpper is str.upper(): the full case mapping of dto.PythonUpper, with
// a lone surrogate (which a decoded JSON str can hold) kept as is.
func restoreUpper(text string) string {
	var out strings.Builder
	for _, r := range pystr.Runes(text) {
		if r >= 0xd800 && r <= 0xdfff {
			out.WriteString(pystr.String([]rune{r}))
			continue
		}
		out.WriteString(dto.PythonUpper(string(r)))
	}
	return out.String()
}

// restoreDatePattern is django.utils.dateparse.date_re: four digits, two
// one-or-two digit groups, with a final newline allowed by $.
func restoreDatePattern(text string) bool {
	text = strings.TrimSuffix(text, "\n")
	parts := strings.Split(text, "-")
	if len(parts) != 3 {
		return false
	}
	for index, part := range parts {
		runes := []rune(part)
		if len(runes) == 0 || (index == 0 && len(runes) != 4) || (index > 0 && len(runes) > 2) {
			return false
		}
		for _, r := range runes {
			if !unicode.IsDigit(r) {
				return false
			}
		}
	}
	return true
}

// normalizeLegacyInvoices is _normalize_legacy_invoices: each legacy platform
// invoice becomes an association document with the same identity and date.
func (a *restoreArchive) normalizeLegacyInvoices() error {
	rows := a.records["SportAssociationInvoices"]
	links := make([]domain.RequestObject, 0, len(rows))
	for _, row := range rows {
		idValue, present := importGet(row, "sport_association_invoice_id")
		if !present {
			return errRestoreDamaged // KeyError
		}
		identity, err := restoreUUIDText(dto.PythonStr(idValue))
		if err != nil {
			return err
		}
		associationValue, present := importGet(row, "sport_association_id")
		if !present {
			return errRestoreDamaged // KeyError
		}
		association, _ := restoreStr(associationValue)
		if association != a.associationID {
			return restoreFail("Una fattura Bakney appartiene a un’altra associazione.")
		}
		dateValue, present := importGet(row, "invoice_date")
		if !present {
			return errRestoreDamaged // KeyError
		}
		text, isText := dateValue.(string)
		if !isText {
			return errRestoreDamaged // TypeError from date.fromisoformat
		}
		date, ok := dto.PythonParseDate(text)
		if !ok {
			if restoreDatePattern(text) {
				return errRestoreDamaged // well formatted but not a date: ValueError
			}
			return restoreFail("Data della fattura Bakney non valida.")
		}
		documentValue, present := importGet(row, "document_id")
		if !present {
			return errRestoreDamaged // KeyError
		}
		document, err := restoreUUIDText(dto.PythonStr(documentValue))
		if err != nil {
			return err
		}
		links = append(links, domain.RequestObject{
			{Name: "sport_association_documents_archive_id", Value: identity},
			{Name: "sport_association_id", Value: a.associationID},
			{Name: "document_id", Value: document},
			{Name: "date", Value: date},
		})
	}
	if _, present := a.records["SportAssociationDocumentsArchive"]; !present {
		return errRestoreDamaged // KeyError from records[...].extend
	}
	a.records["SportAssociationDocumentsArchive"] = append(a.records["SportAssociationDocumentsArchive"], links...)
	a.legacyInvoices = len(links)
	delete(a.records, "SportAssociationInvoices")
	return nil
}

// skippedRecords is legacy_backup.skipped_records. Every check it makes raises
// ValueError/AttributeError/TypeError/KeyError (or a JSON read error), which
// validate() catches: a failure is errRestoreDamaged, except a reader class it
// does not catch (errRestoreReaderRaised).
func (a *restoreArchive) skippedRecords(plan *importeffects.Plan) (celery.Dict, error) {
	counts := celery.Dict{}
	specsValue, present := importGet(a.manifest, "models_exported")
	var specs []any
	if present {
		switch v := specsValue.(type) {
		case []any:
			specs = v
		case string:
			if v != "" {
				return nil, errRestoreDamaged // spec.get on a str character
			}
		case domain.RequestObject:
			if len(v) > 0 {
				return nil, errRestoreDamaged // spec.get on a dict key
			}
		default:
			return nil, errRestoreDamaged // None/int/bool are not iterable
		}
	}
	for _, raw := range specs {
		spec, ok := raw.(domain.RequestObject)
		if !ok {
			return nil, errRestoreDamaged // spec.get on a non-dict
		}
		nameValue, _ := importGet(spec, "name")
		name, isText := nameValue.(string)
		if !isText {
			switch nameValue.(type) {
			case []any, domain.RequestObject:
				return nil, errRestoreDamaged // TypeError: unhashable name
			}
			continue
		}
		expected, deprecated := plan.DeprecatedSections[name]
		if !deprecated {
			continue
		}
		file, _ := importGet(spec, "file")
		duplicate := false
		for _, item := range counts {
			duplicate = duplicate || item.Key == name
		}
		if duplicate || !restoreEqual(file, expected) {
			return nil, errRestoreDamaged
		}
		if !a.zip.has(expected) {
			return nil, errRestoreDamaged // KeyError: Missing retired section
		}
		data, err := a.zip.read(expected)
		if err != nil {
			return nil, restoreReaderError(err)
		}
		decoded, err := restoreJSON(data)
		if err != nil {
			return nil, errRestoreDamaged
		}
		rows, ok := decoded.([]any)
		if !ok {
			return nil, errRestoreDamaged
		}
		for _, row := range rows {
			if _, ok := row.(domain.RequestObject); !ok {
				return nil, errRestoreDamaged
			}
		}
		count, _ := importGet(spec, "count")
		if importTypeName(count) != "int" || !restoreEqual(count, int64ToNumber(len(rows))) {
			return nil, errRestoreDamaged
		}
		counts = append(counts, celery.Item{Key: name, Value: int64(len(rows))})
	}
	return counts, nil
}

// normalizeLegacyTutors is _normalize_legacy_tutors: the duplicate active tax
// code RestoreError, the reassigned/ignored tutor bookkeeping and the tax-code
// overrides the preview reports.
func (a *restoreArchive) normalizeLegacyTutors(plan *importeffects.Plan) error {
	a.reassignedTutors = map[string]bool{}
	a.tutorReassignments = nil
	associates := a.records["Associate"]
	member := map[string]bool{}
	for _, row := range associates {
		association := importGetValue(row, "sport_association_id")
		id, _ := restoreStr(importGetValue(row, "associate_id"))
		if !importTruthy(association) {
			member[id] = true
			continue
		}
		text, _ := restoreStr(association)
		if text == a.associationID {
			member[id] = true
		}
	}
	tutors := map[string]bool{}
	for _, row := range a.records["AssociateTutorRelation"] {
		associate, _ := restoreStr(importGetValue(row, "associate_id"))
		if member[associate] {
			tutor, _ := restoreStr(importGetValue(row, "tutor_id"))
			tutors[tutor] = true
		}
	}
	referenced := map[string]bool{}
	for _, entry := range plan.Order {
		model := plan.Model(entry.Model)
		for _, row := range a.records[entry.Model] {
			for _, field := range model.Fields {
				if field.IsFK() && field.TargetModel == "Associate" && field.TargetApp == "application" {
					if value := importGetValue(row, field.Attname); importTruthy(value) {
						text, _ := restoreStr(value)
						referenced[text] = true
					}
				}
			}
			for _, field := range model.M2M {
				if field.TargetModel != "Associate" {
					continue
				}
				values, present := importGet(row, "_m2m_"+field.Name)
				if !present {
					continue
				}
				items, ok := restoreIterable(values)
				if !ok {
					return errRestoreDamaged // TypeError from `for value in values`
				}
				for _, value := range items {
					text, _ := restoreStr(value)
					referenced[text] = true
				}
			}
		}
	}
	active := map[string]string{}
	deletedOf := func(row domain.RequestObject) (bool, error) {
		value, present := importGet(row, "deleted")
		if !present {
			value = false
		}
		return restoreBool(value)
	}
	taxOf := func(row domain.RequestObject) (string, bool, error) {
		value, present := importGet(row, "tax_code")
		if !present || value == nil {
			return "", false, nil
		}
		return restoreUpper(dto.PythonStr(value)), true, nil
	}
	for _, row := range associates {
		association := importGetValue(row, "sport_association_id")
		text, _ := restoreStr(association)
		if !importTruthy(association) || text != a.associationID {
			continue
		}
		deleted, err := deletedOf(row)
		if err != nil {
			return err
		}
		tax, has, err := taxOf(row)
		if err != nil {
			return err
		}
		if deleted || !has {
			continue
		}
		id, present := importGet(row, "associate_id")
		if !present {
			return errRestoreDamaged // KeyError in the duplicate message
		}
		idText, _ := restoreStr(id)
		if _, duplicate := active[tax]; duplicate {
			return restoreFail("Codice fiscale duplicato tra le anagrafiche attive: %s.", idText)
		}
		active[tax] = idText
	}
	type candidate struct {
		index          int
		hasAssociation bool
		id             string
	}
	others := []candidate{}
	for index, row := range associates {
		association := importGetValue(row, "sport_association_id")
		text, _ := restoreStr(association)
		if importTruthy(association) && text == a.associationID {
			continue
		}
		id, _ := restoreStr(importGetValue(row, "associate_id"))
		others = append(others, candidate{index: index, hasAssociation: importTruthy(association), id: id})
	}
	sort.SliceStable(others, func(i, j int) bool {
		if others[i].hasAssociation != others[j].hasAssociation {
			return !others[i].hasAssociation
		}
		return restorePyLess(others[i].id, others[j].id)
	})
	ignored := map[string]bool{}
	for _, item := range others {
		row := associates[item.index]
		association := importGetValue(row, "sport_association_id")
		if item.hasAssociation {
			if !referenced[item.id] {
				ignored[item.id] = true
				continue
			}
			if !tutors[item.id] {
				continue
			}
			text, _ := restoreStr(association)
			if _, err := restoreUUIDText(text); err != nil {
				return err // UUID(str(association)) raised
			}
		}
		overrides := domain.RequestObject{}
		if item.hasAssociation {
			overrides = append(overrides, domain.RequestField{Name: "sport_association_id", Value: a.associationID})
		}
		first, last := "", ""
		if value := importGetValue(row, "first_name"); importTruthy(value) {
			first, _ = restoreStr(value)
		}
		if value := importGetValue(row, "last_name"); importTruthy(value) {
			last, _ = restoreStr(value)
		}
		detail := domain.RequestObject{{Name: "associate_id", Value: item.id}}
		if item.hasAssociation {
			source, _ := restoreStr(association)
			detail = append(detail, domain.RequestField{Name: "source_association_id", Value: source})
		} else {
			detail = append(detail, domain.RequestField{Name: "source_association_id", Value: nil})
		}
		detail = append(detail, domain.RequestField{Name: "name", Value: dto.PythonStrip(first + " " + last)})
		taxValue, present := importGet(row, "tax_code")
		if present && taxValue != nil {
			taxCode := dto.PythonStr(taxValue)
			deleted, err := deletedOf(row)
			if err != nil {
				return err
			}
			if !deleted {
				if conflict, ok := active[restoreUpper(taxCode)]; ok {
					overrides = importSet(overrides, "tax_code", nil)
					detail = append(detail, domain.RequestField{Name: "original_tax_code", Value: taxCode},
						domain.RequestField{Name: "conflicting_associate_id", Value: conflict})
				} else {
					active[restoreUpper(taxCode)] = item.id
				}
			}
		}
		if !item.hasAssociation {
			if len(overrides) > 0 {
				updated := row
				for _, field := range overrides {
					updated = importSet(updated, field.Name, field.Value)
				}
				associates[item.index] = updated
				a.tutorReassignments = append(a.tutorReassignments, detail)
			}
			continue
		}
		updated := row
		for _, field := range overrides {
			updated = importSet(updated, field.Name, field.Value)
		}
		associates[item.index] = updated
		a.reassignedTutors[item.id] = true
		a.tutorReassignments = append(a.tutorReassignments, detail)
	}
	a.records["Associate"] = associates
	kept := make([]domain.RequestObject, 0, len(associates))
	for _, row := range associates {
		id, _ := restoreStr(importGetValue(row, "associate_id"))
		if !ignored[id] {
			kept = append(kept, row)
		}
	}
	a.records["Associate"] = kept
	a.ignoredTutors = len(associates) - len(kept)
	return nil
}

// normalized reports whether validate() rewrote the archive's records (legacy
// invoice links, tutor overrides or ignored tutors).
func (a *restoreArchive) normalized() bool {
	return a.legacyInvoices > 0 || len(a.reassignedTutors) > 0 || len(a.tutorReassignments) > 0 || a.ignoredTutors > 0
}

// restorePyLess is Python str ordering (code points).
func restorePyLess(a, b string) bool {
	ra, rb := []rune(a), []rune(b)
	for i := 0; i < len(ra) && i < len(rb); i++ {
		if ra[i] != rb[i] {
			return ra[i] < rb[i]
		}
	}
	return len(ra) < len(rb)
}

// restoreBool is BooleanField(null=False).to_python(value).
func restoreBool(value any) (bool, error) {
	switch v := value.(type) {
	case bool:
		return v, nil
	case json.Number:
		if dto.PythonEqual(v, true) {
			return true, nil
		}
		if dto.PythonEqual(v, false) {
			return false, nil
		}
	case string:
		switch v {
		case "t", "True", "1":
			return true, nil
		case "f", "False", "0":
			return false, nil
		}
	}
	return false, errRestoreDamaged
}

// restoreToPython is field.to_python(value) as _validate_graph calls it
// (the target field for a relation): nil when it returns, errRestoreDamaged
// when it raises one of the exceptions validate() catches, and
// errRestoreReaderRaised for OverflowError, which validate() does not catch.
// errRestorePlanKind is a field kind the generated import plan does not have.
var errRestorePlanKind = refusal.AppState("data restore: plan field kind outside the converted set")

func restoreToPython(kind, fieldType string, null bool, value any) error {
	if value == nil {
		if kind == "bool" && !null {
			return errRestoreDamaged
		}
		return nil
	}
	switch kind {
	case "text", "json":
		return nil
	case "uuid":
		switch v := value.(type) {
		case bool:
			return nil
		case json.Number:
			if strings.ContainsAny(string(v), ".eE") {
				return errRestoreDamaged
			}
			n, ok := dto.PythonIntText(string(v))
			if !ok || n.Sign() < 0 || n.BitLen() > 128 {
				return errRestoreDamaged
			}
			return nil
		case float64:
			return errRestoreDamaged
		case string:
			_, err := restoreUUIDText(v)
			return err
		}
		return errRestoreDamaged
	case "int":
		if special, ok := value.(float64); ok {
			if math.IsNaN(special) {
				return errRestoreDamaged
			}
			return errRestoreReaderRaised // OverflowError from int(inf)
		}
		_, result := dto.PythonInt(value)
		switch result {
		case dto.PythonIntOverflow:
			return errRestoreReaderRaised
		case dto.PythonIntOK:
			return nil
		}
		return errRestoreDamaged
	case "bool":
		if null {
			switch v := value.(type) {
			case string:
				if v == "" {
					return nil
				}
			case []any:
				if len(v) == 0 {
					return nil
				}
			case domain.RequestObject:
				if len(v) == 0 {
					return nil
				}
			}
		}
		_, err := restoreBool(value)
		return err
	case "decimal":
		switch v := value.(type) {
		case bool:
			return nil
		case float64:
			return errRestoreDamaged // DecimalField rejects a non-finite float
		case json.Number:
			if !strings.ContainsAny(string(v), ".eE") {
				return nil // Decimal(int) is always finite
			}
			if f, _ := strconv.ParseFloat(string(v), 64); math.IsNaN(f) || math.IsInf(f, 0) {
				return errRestoreDamaged
			}
			return nil
		case string:
			if parsed := dto.ParsePythonDecimal(v); parsed.Kind == "" || parsed.Kind == "inf" || parsed.Kind == "nan" {
				return errRestoreDamaged
			}
			return nil
		}
		return errRestoreDamaged
	case "datetime":
		text, ok := value.(string)
		if !ok {
			return errRestoreDamaged
		}
		if _, ok := dto.DjangoDateTimePrep(text); ok {
			return nil
		}
		// parse_datetime returned None: DateTimeField.to_python then tries
		// parse_date, which accepts Unicode decimal digits.
		if _, ok := dto.PythonParseDate(text); ok {
			return nil
		}
		return errRestoreDamaged
	case "date":
		text, ok := value.(string)
		if !ok {
			return errRestoreDamaged
		}
		if _, ok := dto.PythonParseDate(text); ok {
			return nil
		}
		return errRestoreDamaged
	case "time":
		text, ok := value.(string)
		if !ok {
			return errRestoreDamaged
		}
		if restoreValidTime(text) {
			return nil
		}
		return errRestoreDamaged
	}
	// plan.json lists only the kinds above (a foreign key takes its target's
	// kind): TestRestorePlanKindsConverted pins it.
	return errRestorePlanKind
}

// restoreTimePattern is Django's time_re (one- or two-digit components, a
// comma or dot fraction) followed by datetime.time's range checks.
var restoreTimePattern = regexp.MustCompile(`^(\p{Nd}{1,2}):(\p{Nd}{1,2})(?::(\p{Nd}{1,2})(?:[.,](\p{Nd}{1,6})\p{Nd}{0,6})?)?$`)

func restoreDecimalDigits(text string) int {
	value := 0
	for _, r := range text {
		digit, _ := restoreHexDigit(r)
		value = value*10 + digit
	}
	return value
}

// restoreValidTime reports whether parse_time accepts text:
// datetime.time.fromisoformat, then Django's time_re.
func restoreValidTime(text string) bool {
	body := strings.TrimPrefix(text, "T")
	if body != "" {
		if _, ok := pydatetime.FromISOFormat("2000-01-01T" + body); ok {
			return true
		}
	}
	match := restoreTimePattern.FindStringSubmatch(strings.TrimSuffix(text, "\n"))
	if match == nil {
		return false
	}
	return restoreDecimalDigits(match[1]) <= 23 && restoreDecimalDigits(match[2]) <= 59 &&
		(match[3] == "" || restoreDecimalDigits(match[3]) <= 59)
}

// restoreIntBase16 is int(text, 16) for the text uuid.UUID(hex=...) passes on:
// surrounding whitespace, a sign, an optional 0x prefix, underscores between
// digits (or right after 0x) and Unicode decimal digits.
func restoreIntBase16(text string) (*big.Int, bool) {
	text = dto.PythonStrip(text)
	runes := pystr.Runes(text)
	sign := 1
	if len(runes) > 0 && (runes[0] == '+' || runes[0] == '-') {
		if runes[0] == '-' {
			sign = -1
		}
		runes = runes[1:]
	}
	afterPrefix := false
	if len(runes) >= 2 && runes[0] == '0' && (runes[1] == 'x' || runes[1] == 'X') {
		runes = runes[2:]
		afterPrefix = true
	}
	var digits strings.Builder
	sawDigit, previousUnderscore := false, false
	for _, r := range runes {
		if r == '_' {
			if previousUnderscore || (!sawDigit && !afterPrefix) {
				return nil, false
			}
			previousUnderscore, afterPrefix = true, false
			continue
		}
		value, ok := restoreHexDigit(r)
		if !ok {
			return nil, false
		}
		digits.WriteByte("0123456789abcdef"[value])
		sawDigit, previousUnderscore, afterPrefix = true, false, false
	}
	if !sawDigit || previousUnderscore {
		return nil, false
	}
	number, ok := new(big.Int).SetString(digits.String(), 16)
	if !ok {
		return nil, false
	}
	if sign < 0 {
		number.Neg(number)
	}
	return number, true
}

// restoreHexDigit is one digit of int(text, 16): an ASCII hex digit or a
// Unicode Nd digit.
func restoreHexDigit(r rune) (int, bool) {
	switch {
	case r >= '0' && r <= '9':
		return int(r - '0'), true
	case r >= 'a' && r <= 'f':
		return int(r-'a') + 10, true
	case r >= 'A' && r <= 'F':
		return int(r-'A') + 10, true
	}
	if r > 127 && unicode.IsDigit(r) {
		// Nd digits come in runs of ten starting at zero.
		for _, span := range unicode.Nd.R16 {
			if r >= rune(span.Lo) && r <= rune(span.Hi) {
				return int(r-rune(span.Lo)) % 10, true
			}
		}
		for _, span := range unicode.Nd.R32 {
			if r >= rune(span.Lo) && r <= rune(span.Hi) {
				return int(r-rune(span.Lo)) % 10, true
			}
		}
	}
	return 0, false
}

// restoreUUIDCanonical is str(uuid.UUID(int=number)).
func restoreUUIDCanonical(number *big.Int) string {
	digits := number.Text(16)
	digits = strings.Repeat("0", 32-len(digits)) + digits
	return digits[0:8] + "-" + digits[8:12] + "-" + digits[12:16] + "-" + digits[16:20] + "-" + digits[20:32]
}

// restoreUUIDText is str(uuid.UUID(hex=text)): braces, a urn:uuid: prefix and
// hyphens removed, then int(text, 16) of 32 characters.
func restoreUUIDText(text string) (string, error) {
	hex := strings.ReplaceAll(strings.ReplaceAll(text, "urn:", ""), "uuid:", "")
	hex = strings.ReplaceAll(strings.Trim(hex, "{}"), "-", "")
	if len(pystr.Runes(hex)) != 32 {
		return "", errRestoreDamaged
	}
	number, ok := restoreIntBase16(hex)
	if !ok || number.Sign() < 0 || number.BitLen() > 128 {
		return "", errRestoreDamaged
	}
	return restoreUUIDCanonical(number), nil
}

// restoreIdentity is str(pk.to_python(value)).
func restoreIdentity(kind string, value any) (string, error) {
	switch kind {
	case "uuid":
		switch v := value.(type) {
		case string:
			return restoreUUIDText(v)
		case bool:
			if v {
				return restoreUUIDCanonical(big.NewInt(1)), nil
			}
			return restoreUUIDCanonical(big.NewInt(0)), nil
		case json.Number:
			if strings.ContainsAny(string(v), ".eE") {
				return "", errRestoreDamaged
			}
			number, ok := dto.PythonIntText(string(v))
			if !ok || number.Sign() < 0 || number.BitLen() > 128 {
				return "", errRestoreDamaged
			}
			return restoreUUIDCanonical(number), nil
		}
		return "", errRestoreDamaged
	case "int":
		if special, ok := value.(float64); ok {
			if math.IsNaN(special) {
				return "", errRestoreDamaged
			}
			return "", errRestoreReaderRaised // OverflowError from int(inf)
		}
		number, result := dto.PythonInt(value)
		if result == dto.PythonIntOverflow {
			return "", errRestoreReaderRaised
		}
		if result != dto.PythonIntOK {
			return "", errRestoreDamaged
		}
		return number.String(), nil
	}
	// plan.json's primary keys and foreign key targets are uuid or int:
	// TestRestorePlanKindsConverted pins it.
	return "", errRestorePlanKind
}

// restoreCharFields are CharField and its subclasses in the plan.
var restoreCharFields = map[string]bool{"CharField": true, "EmailField": true, "SlugField": true, "URLField": true}

// validateGraph is _validate_graph.
func (a *restoreArchive) validateGraph(plan *importeffects.Plan) error {
	a.identities = map[string]map[string]bool{}
	a.identityList = map[string][]string{}
	for _, entry := range plan.Order {
		model := plan.Model(entry.Model)
		identities := map[string]bool{}
		list := []string{}
		for _, row := range a.records[entry.Model] {
			value, present := importGet(row, model.PK.Name)
			if !present || value == nil || value == "" {
				return restoreFail("Identificativo mancante in %s.", entry.Model)
			}
			identity, err := restoreIdentity(model.PK.Kind, value)
			if err != nil {
				return err
			}
			if identities[identity] {
				return restoreFail("Identificativo duplicato in %s.", entry.Model)
			}
			identities[identity] = true
			list = append(list, identity)
		}
		a.identities[entry.Model] = identities
		a.identityList[entry.Model] = list
	}
	all := map[string]bool{}
	for _, entry := range plan.Order {
		for identity := range a.identities[entry.Model] {
			if all[identity] {
				return restoreFail("Il backup riutilizza un identificativo per tipi di dati differenti.")
			}
		}
		for identity := range a.identities[entry.Model] {
			all[identity] = true
		}
	}
	classes := map[string]bool{}
	for _, entry := range plan.Order {
		classes[entry.Model] = true
	}
	for _, entry := range plan.Order {
		name := entry.Model
		model := plan.Model(name)
		for _, row := range a.records[name] {
			if name != "SportAssociation" {
				association := importGetValue(row, "sport_association_id")
				if importTruthy(association) {
					text, _ := restoreStr(association)
					if text != a.associationID {
						return restoreFail("Il backup contiene riferimenti a un’altra associazione.")
					}
				}
			}
			for _, field := range model.Fields {
				value, present := importGet(row, field.Attname)
				if present {
					kind, fieldType := field.Kind, field.Type
					if field.IsFK() {
						kind, fieldType = field.TargetKind, field.TargetType
					}
					if err := restoreToPython(kind, fieldType, field.Null, value); err != nil {
						return err
					}
					if value == nil && !field.Null && field.Default.Kind == "none" && !field.AutoNow && !field.AutoNowAdd {
						return restoreFail("Valore obbligatorio mancante: %s.%s.", name, field.Name)
					}
					if text, ok := value.(string); ok && restoreCharFields[field.Type] && field.MaxLength != nil &&
						utf8.RuneCountInString(text) > *field.MaxLength {
						return restoreFail("Valore troppo lungo: %s.%s.", name, field.Name)
					}
				}
				if !field.IsFK() || !classes[field.TargetModel] {
					continue
				}
				if !importTruthy(value) {
					continue
				}
				text, _ := restoreStr(value)
				if !a.identities[field.TargetModel][text] {
					if !field.Null || name == "Folder" {
						return restoreFail("Relazione mancante: %s.%s.", name, field.Name)
					}
					record, _ := restoreStr(importGetValue(row, model.PK.Name))
					a.missingRelations = append(a.missingRelations, restoreMissingRelation{model: name, record: record, field: field.Attname,
						targetModel: field.TargetModel, target: text})
				}
			}
			for _, field := range model.M2M {
				if !classes[field.TargetModel] {
					continue
				}
				values, present := importGet(row, "_m2m_"+field.Name)
				if !present {
					continue
				}
				var items []any
				switch v := values.(type) {
				case []any:
					items = v
				case string:
					for _, r := range v {
						items = append(items, string(r))
					}
				case domain.RequestObject:
					for _, item := range v {
						items = append(items, item.Name)
					}
				default:
					return errRestoreDamaged
				}
				for _, item := range items {
					text, _ := restoreStr(item)
					if !a.identities[field.TargetModel][text] {
						return restoreFail("Relazione multipla mancante: %s.%s.", name, field.Name)
					}
				}
			}
		}
	}
	return nil
}

// restoreExportPlan is the export plan (EXPORT_ORDER file names) the
// validation compares the manifest with.
var restoreExportPlan = func() *associationexport.Plan {
	plan, err := associationexport.LoadPlan()
	if err != nil {
		panic(err)
	}
	return plan
}()

func restoreContains(list []string, item string) bool {
	for _, value := range list {
		if value == item {
			return true
		}
	}
	return false
}
