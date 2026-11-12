package service

import (
	"archive/zip"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/associationexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
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
}

func (a *restoreArchive) Close() {
	if a.zip != nil {
		a.zip.Close()
		a.zip = nil
	}
}

// restoreStr is str(value) for a decoded JSON scalar; containers are unsupported.
func restoreStr(value any) (string, error) { return importStr(value) }

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
// *restoreError for the RestoreErrors Django raises, errConsumerImportUnsupported
// for inputs the port hands to Django, or another error (read failures).
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
		return nil, err
	}
	archive.zip = zf
	if err := archive.validate(run, limits); err != nil {
		archive.Close()
		return nil, err
	}
	return archive, nil
}

func (a *restoreArchive) entries() []*zip.File {
	out := make([]*zip.File, len(a.zip.names))
	for i, name := range a.zip.names {
		out[i] = a.zip.byName[name]
	}
	return out
}

func (a *restoreArchive) json(name string) (any, error) {
	data, err := a.zip.read(name)
	if err != nil {
		return nil, errConsumerImportUnsupported
	}
	return consumerImportJSON(data)
}

func (a *restoreArchive) validate(run *consumerImportRun, limits restoreLimits) error {
	entries := a.entries()
	if len(entries) > 50000 {
		return restoreFail("Il backup contiene più di 50.000 file.")
	}
	var expanded uint64
	for _, entry := range entries {
		name := entry.Name
		if strings.HasPrefix(name, "/") || restoreContains(restorePosixParts(name), "..") || restoreWindowsDrive(name) {
			return restoreFail("Percorso non consentito nel backup: %s.", name)
		}
		if (entry.ExternalAttrs>>16)&0o170000 == 0o120000 {
			return restoreFail("Il backup contiene un collegamento simbolico non consentito: %s.", name)
		}
		isJSON := strings.HasSuffix(strings.ToLower(name), ".json")
		if !restoreASCII(name) {
			isJSON = strings.HasSuffix(name, ".json")
			if strings.ToLower(name) != name && strings.HasSuffix(strings.ToLower(name), ".json") != isJSON {
				return errConsumerImportUnsupported
			}
		}
		limit, setting := limits.File, "DATA_RESTORE_MAX_FILE_BYTES"
		if isJSON {
			limit, setting = limits.JSON, "DATA_RESTORE_MAX_JSON_BYTES"
		}
		if entry.UncompressedSize64 > math.MaxInt64/2 {
			return errConsumerImportUnsupported
		}
		if int64(entry.UncompressedSize64) > limit {
			return restoreFail("Il file %s è troppo grande: %s non compressi; limite %s (%s).", name,
				restoreFormatSize(int64(entry.UncompressedSize64)), restoreFormatSize(limit), setting)
		}
		expanded += entry.UncompressedSize64
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
	if !dto.PythonEqual(format, "bakney_sport_export_v1") || !dto.PythonEqual(version, "1.0.0") {
		return restoreFail("Formato non supportato. Carica un export ZIP Bakney versione 1.0.0.")
	}
	statistics, err := restoreGetDict(manifest, "statistics")
	if err != nil {
		return err
	}
	manifestErrors, _ := importGet(manifest, "errors")
	failed, _ := importGet(statistics, "files_failed")
	if importTruthy(manifestErrors) || importTruthy(failed) {
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
	if _, err := run.skippedRecords(manifest); err != nil {
		var python *consumerImportPyError
		if errors.As(err, &python) {
			return errRestoreDamaged
		}
		return errConsumerImportUnsupported
	}
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
		nameText, err := restoreStr(nameValue)
		if err != nil {
			return errConsumerImportUnsupported
		}
		name, isText := nameValue.(string)
		file, fileIsText := fileValue.(string)
		if !isText || expected[name] == "" || seen[name] || !fileIsText || file != expected[name] {
			if _, isNumber := nameValue.(json.Number); isNumber {
				return errConsumerImportUnsupported // a float or int equal to no key; its str() is reproduced but rare
			}
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
			if !dto.PythonEqual(int64ToNumber(len(rows)), count) {
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
	if a.associationID, err = restoreStr(idValue); err != nil {
		return errConsumerImportUnsupported
	}
	if a.ownerID, err = restoreStr(ownerValue); err != nil {
		return errConsumerImportUnsupported
	}
	owners := 0
	for _, user := range a.records["User"] {
		text, err := restoreStr(importGetValue(user, "user_id"))
		if err != nil {
			return errConsumerImportUnsupported
		}
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
	infoID, err := restoreStr(importGetValue(info, "sport_association_id"))
	if err != nil {
		return errConsumerImportUnsupported
	}
	if infoID != a.associationID || !dto.PythonEqual(importGetValue(info, "denomination"), importGetValue(association, "denomination")) {
		return restoreFail("Il manifest non corrisponde all’associazione esportata.")
	}
	if invoices := a.records["SportAssociationInvoices"]; len(invoices) > 0 {
		// Legacy platform invoices become association documents; not reproduced.
		return errConsumerImportUnsupported
	}
	delete(a.records, "SportAssociationInvoices")
	if err := a.checkTutors(plan); err != nil {
		return err
	}
	if err := a.validateGraph(plan); err != nil {
		return err
	}
	for _, entry := range entries {
		name := entry.Name
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
	if present && !dto.PythonEqual(exported, int64ToNumber(len(a.media))) {
		return restoreFail("Il numero degli allegati non corrisponde al manifest.")
	}
	for _, name := range a.zip.names {
		member := a.zip.byName[name]
		reader, err := member.Open()
		if err != nil {
			return errConsumerImportUnsupported
		}
		_, err = io.Copy(io.Discard, reader)
		reader.Close()
		if errors.Is(err, zip.ErrChecksum) {
			return restoreFail("Il backup contiene un file danneggiato.")
		}
		if err != nil {
			return errConsumerImportUnsupported
		}
	}
	for _, row := range a.records["Document"] {
		id, err := restoreStr(importGetValue(row, "document_id"))
		if err != nil {
			return errConsumerImportUnsupported
		}
		if _, staged := a.media["Document:"+id]; importTruthy(importGetValue(row, "filepath")) && !staged {
			a.missingBinary++
		}
	}
	urlOnly := 0
	for _, row := range a.records["Subscription"] {
		id, err := restoreStr(importGetValue(row, "subscription_id"))
		if err != nil {
			return errConsumerImportUnsupported
		}
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

func restoreASCII(text string) bool {
	for i := 0; i < len(text); i++ {
		if text[i] >= 0x80 {
			return false
		}
	}
	return true
}

// restoreWindowsDrive is PureWindowsPath(name).drive for names without a
// backslash (the archive reader refuses those): "X:" prefixes and UNC
// "//server/share" paths (already refused as absolute).
func restoreWindowsDrive(name string) bool {
	return len(name) >= 2 && name[1] == ':' && (name[0]|0x20 >= 'a' && name[0]|0x20 <= 'z')
}

// checkTutors is _normalize_legacy_tutors without its rewrites: the duplicate
// active tax code RestoreError is reproduced; an archive whose tutors would be
// reassigned, cleared or ignored is handed off.
func (a *restoreArchive) checkTutors(plan *importeffects.Plan) error {
	associates := a.records["Associate"]
	member := map[string]bool{}
	for _, row := range associates {
		association := importGetValue(row, "sport_association_id")
		id, err := restoreStr(importGetValue(row, "associate_id"))
		if err != nil {
			return errConsumerImportUnsupported
		}
		text, err := restoreStr(association)
		if err != nil {
			return errConsumerImportUnsupported
		}
		if !importTruthy(association) || text == a.associationID {
			member[id] = true
		}
	}
	tutors := map[string]bool{}
	for _, row := range a.records["AssociateTutorRelation"] {
		associate, err := restoreStr(importGetValue(row, "associate_id"))
		if err != nil {
			return errConsumerImportUnsupported
		}
		if member[associate] {
			tutor, err := restoreStr(importGetValue(row, "tutor_id"))
			if err != nil {
				return errConsumerImportUnsupported
			}
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
						text, err := restoreStr(value)
						if err != nil {
							return errConsumerImportUnsupported
						}
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
				list, ok := values.([]any)
				if !ok {
					return errConsumerImportUnsupported
				}
				for _, value := range list {
					text, err := restoreStr(value)
					if err != nil {
						return errConsumerImportUnsupported
					}
					referenced[text] = true
				}
			}
		}
	}
	active := map[string]string{}
	deletedOf := func(row domain.RequestObject) (bool, error) {
		value, present := importGet(row, "deleted")
		if !present {
			return false, nil
		}
		return restoreBool(value)
	}
	taxOf := func(row domain.RequestObject) (string, bool, error) {
		value, present := importGet(row, "tax_code")
		if !present || value == nil {
			return "", false, nil
		}
		text, ok := value.(string)
		if !ok || !restoreASCII(text) {
			return "", false, errConsumerImportUnsupported
		}
		return strings.ToUpper(text), true, nil
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
			return errRestoreDamaged
		}
		idText, err := restoreStr(id)
		if err != nil {
			return errConsumerImportUnsupported
		}
		if _, duplicate := active[tax]; duplicate {
			return restoreFail("Codice fiscale duplicato tra le anagrafiche attive: %s.", idText)
		}
		active[tax] = idText
	}
	type candidate struct {
		hasAssociation bool
		id             string
		row            domain.RequestObject
	}
	others := []candidate{}
	for _, row := range associates {
		association := importGetValue(row, "sport_association_id")
		text, _ := restoreStr(association)
		if importTruthy(association) && text == a.associationID {
			continue
		}
		id, _ := restoreStr(importGetValue(row, "associate_id"))
		others = append(others, candidate{hasAssociation: importTruthy(association), id: id, row: row})
	}
	sort.SliceStable(others, func(i, j int) bool {
		if others[i].hasAssociation != others[j].hasAssociation {
			return !others[i].hasAssociation
		}
		return restorePyLess(others[i].id, others[j].id)
	})
	for _, item := range others {
		if item.hasAssociation {
			if !referenced[item.id] || tutors[item.id] {
				return errConsumerImportUnsupported // ignored or reassigned tutor records
			}
			continue
		}
		tax, has, err := taxOf(item.row)
		if err != nil {
			return err
		}
		deleted, err := deletedOf(item.row)
		if err != nil {
			return err
		}
		if has && !deleted {
			if _, conflict := active[tax]; conflict {
				return errConsumerImportUnsupported // the tax code would be cleared
			}
			active[tax] = item.id
		}
	}
	return nil
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

var (
	restoreDateTimeText = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?(Z|[+-]\d{2}:\d{2})?$`)
	restoreDateText     = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})$`)
	restoreTimeText     = regexp.MustCompile(`^(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?$`)
	restoreDecimalText  = regexp.MustCompile(`^[+-]?(\d(_?\d)*(\.(\d(_?\d)*)?)?|\.\d(_?\d)*)([eE][+-]?\d(_?\d)*)?$`)
)

func restoreValidDate(year, month, day int) bool {
	if year < 1 || month < 1 || month > 12 || day < 1 {
		return false
	}
	days := [...]int{31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}[month-1]
	if month == 2 && (year%4 == 0 && (year%100 != 0 || year%400 == 0)) {
		days = 29
	}
	return day <= days
}

func restoreAtoi(text string) int { n, _ := strconv.Atoi(text); return n }

// restoreToPython is field.to_python(value) as _validate_graph calls it
// (the target field for a relation): nil when it returns, errRestoreDamaged
// when it raises one of the exceptions validate() catches, and
// errConsumerImportUnsupported for inputs whose outcome is not reproduced.
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
			if !ok {
				return errConsumerImportUnsupported
			}
			if n.Sign() < 0 || n.BitLen() > 128 {
				return errRestoreDamaged
			}
			return nil
		case string:
			_, err := restoreUUIDText(v)
			return err
		}
		return errRestoreDamaged
	case "int":
		number, result := dto.PythonInt(value)
		switch {
		case result == dto.PythonIntOverflow:
			return errConsumerImportUnsupported
		case result != dto.PythonIntOK:
			if text, ok := value.(string); ok && !restoreASCII(text) {
				return errConsumerImportUnsupported
			}
			return errRestoreDamaged
		}
		_ = number
		return nil
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
		case json.Number:
			if _, err := strconv.ParseFloat(string(v), 64); err != nil {
				return errConsumerImportUnsupported
			}
			return nil
		case string:
			text := dto.PythonStrip(v)
			if !restoreASCII(v) {
				return errConsumerImportUnsupported
			}
			lower := strings.ToLower(strings.TrimLeft(text, "+-"))
			if restoreDecimalText.MatchString(text) || lower == "inf" || lower == "infinity" || lower == "nan" || lower == "snan" {
				return nil
			}
			if strings.HasPrefix(lower, "nan") || strings.HasPrefix(lower, "snan") {
				return errConsumerImportUnsupported
			}
			return errRestoreDamaged
		}
		return errRestoreDamaged
	case "datetime":
		text, ok := value.(string)
		if !ok {
			return errRestoreDamaged
		}
		m := restoreDateTimeText.FindStringSubmatch(text)
		if m == nil {
			if d := restoreDateText.FindStringSubmatch(text); d != nil {
				if !restoreValidDate(restoreAtoi(d[1]), restoreAtoi(d[2]), restoreAtoi(d[3])) {
					return errRestoreDamaged
				}
				return nil
			}
			return errConsumerImportUnsupported
		}
		if !restoreValidDate(restoreAtoi(m[1]), restoreAtoi(m[2]), restoreAtoi(m[3])) || restoreAtoi(m[4]) > 23 ||
			restoreAtoi(m[5]) > 59 || (m[6] != "" && restoreAtoi(m[6]) > 59) {
			return errRestoreDamaged
		}
		if zone := m[8]; len(zone) == 6 && (restoreAtoi(zone[1:3]) > 23 || restoreAtoi(zone[4:6]) > 59) {
			return errConsumerImportUnsupported
		}
		return nil
	case "date":
		text, ok := value.(string)
		if !ok {
			return errRestoreDamaged
		}
		m := restoreDateText.FindStringSubmatch(text)
		if m == nil {
			return errConsumerImportUnsupported
		}
		if !restoreValidDate(restoreAtoi(m[1]), restoreAtoi(m[2]), restoreAtoi(m[3])) {
			return errRestoreDamaged
		}
		return nil
	case "time":
		text, ok := value.(string)
		if !ok {
			return errRestoreDamaged
		}
		m := restoreTimeText.FindStringSubmatch(text)
		if m == nil {
			return errConsumerImportUnsupported
		}
		if restoreAtoi(m[1]) > 23 || restoreAtoi(m[2]) > 59 || (m[3] != "" && restoreAtoi(m[3]) > 59) {
			return errRestoreDamaged
		}
		return nil
	}
	return errConsumerImportUnsupported
}

// restoreUUIDText is str(uuid.UUID(hex=text)) for ASCII text: braces, a
// urn:uuid: prefix and hyphens removed, then int(text, 16) of 32 characters.
func restoreUUIDText(text string) (string, error) {
	if !restoreASCII(text) {
		return "", errConsumerImportUnsupported
	}
	hex := strings.ReplaceAll(strings.ReplaceAll(text, "urn:", ""), "uuid:", "")
	hex = strings.ReplaceAll(strings.Trim(hex, "{}"), "-", "")
	if len(hex) != 32 {
		return "", errRestoreDamaged
	}
	plain := true
	for i := 0; i < len(hex); i++ {
		c := hex[i] | 0x20
		if !(hex[i] >= '0' && hex[i] <= '9') && !(c >= 'a' && c <= 'f') {
			plain = false
			if !strings.ContainsRune("xX_+ \t\n\r\v\f", rune(hex[i])) {
				return "", errRestoreDamaged
			}
		}
	}
	if !plain {
		// int(hex, 16) also takes whitespace, a sign, 0x and underscores.
		return "", errConsumerImportUnsupported
	}
	lower := strings.ToLower(hex)
	return lower[0:8] + "-" + lower[8:12] + "-" + lower[12:16] + "-" + lower[16:20] + "-" + lower[20:32], nil
}

// restoreIdentity is str(pk.to_python(value)).
func restoreIdentity(kind string, value any) (string, error) {
	switch kind {
	case "uuid":
		switch v := value.(type) {
		case string:
			return restoreUUIDText(v)
		case json.Number, bool:
			return "", errConsumerImportUnsupported
		}
		return "", errRestoreDamaged
	case "int":
		number, result := dto.PythonInt(value)
		if result == dto.PythonIntOverflow {
			return "", errConsumerImportUnsupported
		}
		if result != dto.PythonIntOK {
			if text, ok := value.(string); ok && !restoreASCII(text) {
				return "", errConsumerImportUnsupported
			}
			return "", errRestoreDamaged
		}
		return number.String(), nil
	}
	return "", errConsumerImportUnsupported
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
					text, err := restoreStr(association)
					if err != nil {
						return errConsumerImportUnsupported
					}
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
				text, err := restoreStr(value)
				if err != nil {
					return errConsumerImportUnsupported
				}
				if !a.identities[field.TargetModel][text] {
					if !field.Null || name == "Folder" {
						return restoreFail("Relazione mancante: %s.%s.", name, field.Name)
					}
					record, err := restoreStr(importGetValue(row, model.PK.Name))
					if err != nil {
						return errConsumerImportUnsupported
					}
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
					text, err := restoreStr(item)
					if err != nil {
						return errConsumerImportUnsupported
					}
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
