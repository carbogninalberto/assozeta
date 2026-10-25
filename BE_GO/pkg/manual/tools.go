package manual

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// ToolContext is trusted service input, never a tool argument. The service
// must resolve live instance-owner authority and features before invoking it.
type ToolContext struct {
	Features   []string
	Maintainer bool
}

var ErrUnknownTool = errors.New("unknown manual tool")
var toolPage = regexp.MustCompile(`^(docs|faq|tutorials)/[a-z0-9-]+\.mdx$`)

func toolUnavailable() map[string]any {
	return map[string]any{"status": "no_evidence", "results": []any{}, "message": "Il manuale verificato non è disponibile per questa versione o deve essere aggiornato."}
}

// ExecuteTool keeps verified knowledge separate from the HTTP reader's local
// preview. Authorization must be established by the calling service. Values in
// arguments cannot change caller authority, features, revision or source paths.
func (l *Loader) ExecuteTool(ctx context.Context, caller ToolContext, name string, arguments map[string]any) (map[string]any, error) {
	if ctx == nil {
		return nil, ErrReaderUnavailable
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	result, err := l.executeTool(ctx, caller, name, arguments)
	if canceled := ctx.Err(); canceled != nil {
		return nil, canceled
	}
	if errors.Is(err, ErrUnknownTool) {
		return nil, err
	}
	if err != nil {
		return toolUnavailable(), nil
	}
	return result, nil
}

func (l *Loader) executeTool(ctx context.Context, caller ToolContext, name string, args map[string]any) (map[string]any, error) {
	switch name {
	case "search_manual":
		query, ok := args["query"].(string)
		query = strings.TrimFunc(query, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
		if !ok || query == "" || utf8.RuneCountInString(query) > 2000 {
			return map[string]any{"status": "invalid_query", "results": []any{}, "message": "Specifica una domanda sul manuale."}, nil
		}
	case "get_manual_gaps":
		if !caller.Maintainer {
			return map[string]any{"status": "forbidden", "message": "Le lacune del manuale sono riservate al proprietario dell’istanza."}, nil
		}
		if page := args["page"]; page != nil {
			value, ok := page.(string)
			if !ok || !toolPage.MatchString(value) {
				return map[string]any{"status": "invalid_query", "message": "Specifica il percorso di una pagina del manuale."}, nil
			}
		}
	case "get_manual_section", "get_manual_evidence":
	default:
		return nil, ErrUnknownTool
	}
	snapshot, err := l.loadKnowledge(ctx, caller.Features, caller.Maintainer, name != "get_manual_gaps", "")
	if err != nil {
		return nil, err
	}
	if name == "get_manual_gaps" {
		return l.toolGaps(ctx, snapshot, args["page"])
	}
	if name == "get_manual_evidence" && !caller.Maintainer {
		return map[string]any{"status": "forbidden", "message": "Le evidenze tecniche sono riservate al proprietario dell’istanza."}, nil
	}
	if name == "search_manual" {
		query := args["query"].(string)
		chunks, err := searchSections(ctx, snapshot.index, snapshot.applicable, query)
		if err != nil {
			return nil, err
		}
		// Python evaluates int(limit) after ranking, except for the two early
		// empty-query/model exits. Preserve that distinction for invalid limits.
		evaluateLimit := len(manualTokens(query)) != 0
		if evaluateLimit {
			model, ok := snapshot.index["semantics"].(map[string]any)
			if !ok {
				return nil, errShape
			}
			vocabulary, ok := model["vocabulary"].([]any)
			if !ok {
				return nil, errShape
			}
			evaluateLimit = len(vocabulary) != 0
		}
		limit := 5
		if evaluateLimit {
			if value, present := args["limit"]; present {
				limit, err = toolLimit(value)
				if err != nil {
					return nil, err
				}
			}
		}
		if len(chunks) > limit {
			chunks = chunks[:limit]
		}
		results := make([]any, 0, len(chunks))
		for _, chunk := range chunks {
			item, err := toolSection(chunk, snapshot)
			if err != nil {
				return nil, err
			}
			results = append(results, item)
		}
		status, message := "verified", ""
		if len(results) == 0 {
			status, message = "no_evidence", "Non ho trovato istruzioni verificate per questa domanda."
		}
		return map[string]any{"status": status, "results": results, "message": message}, nil
	}
	id, valid := args["section_id"].(string)
	for _, chunk := range snapshot.applicable {
		if !valid || chunk["id"] != id {
			continue
		}
		section, err := toolSection(chunk, snapshot)
		if err != nil {
			return nil, err
		}
		result := map[string]any{"status": "verified", "section": section}
		if name == "get_manual_evidence" {
			for _, key := range []string{"evidence", "scenario_ids"} {
				value, present := chunk[key]
				if !present {
					return nil, errShape
				}
				result[key] = copyManualJSON(value)
			}
			result["capture_provenance"] = copyManualJSON(defaultManual(chunk, "capture_provenance", []any{}))
			result["provenance"] = copyManualJSON(snapshot.index["metadata"])
		}
		return result, nil
	}
	result := map[string]any{"status": "no_evidence"}
	if name == "get_manual_section" {
		result["section"] = nil
	}
	return result, nil
}

func toolSection(chunk map[string]any, snapshot *ReaderSnapshot) (map[string]any, error) {
	result, err := publicSection(chunk, snapshot.index, snapshot.assetRoot != "")
	if err == nil {
		result["url"] = copyManualJSON(chunk["url"])
	}
	return result, err
}

// toolLimit implements Python int for JSON values, then clamps to [1,10].
// Arbitrary-size integer input cannot overflow a machine integer.
func toolLimit(value any) (int, error) {
	clamp := func(f float64) (int, error) {
		if math.IsNaN(f) || math.IsInf(f, 0) {
			return 0, errShape
		}
		if f < 1 {
			return 1, nil
		}
		if f >= 10 {
			return 10, nil
		}
		return int(f), nil
	}
	switch v := value.(type) {
	case bool:
		return 1, nil
	case float64:
		return clamp(v)
	case int:
		return clamp(float64(v))
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			return toolInteger(string(v))
		}
		f, err := strconv.ParseFloat(string(v), 64)
		if err != nil {
			return 0, errShape
		}
		return clamp(f)
	case string:
		return toolInteger(v)
	default:
		return 0, errShape
	}
}

func toolInteger(raw string) (int, error) {
	value := strings.TrimSpace(raw)
	var b strings.Builder
	digits, previousDigit := 0, false
	for i, r := range value {
		if i == 0 && (r == '+' || r == '-') {
			b.WriteRune(r)
			continue
		}
		if r == '_' {
			if !previousDigit {
				return 0, errShape
			}
			previousDigit = false
			continue
		}
		digit := -1
		for _, block := range unicode.Digit.R16 {
			if uint32(r) >= uint32(block.Lo) && uint32(r) <= uint32(block.Hi) && (uint32(r)-uint32(block.Lo))%uint32(block.Stride) == 0 {
				digit = int((uint32(r)-uint32(block.Lo))/uint32(block.Stride)) % 10
				break
			}
		}
		if digit < 0 {
			for _, block := range unicode.Digit.R32 {
				if uint32(r) >= block.Lo && uint32(r) <= block.Hi && (uint32(r)-block.Lo)%block.Stride == 0 {
					digit = int((uint32(r)-block.Lo)/block.Stride) % 10
					break
				}
			}
		}
		if digit < 0 {
			return 0, errShape
		}
		b.WriteByte(byte('0' + digit))
		digits++
		previousDigit = true
	}
	if !previousDigit || digits > 4300 {
		return 0, errShape
	}
	n, ok := new(big.Int).SetString(b.String(), 10)
	if !ok {
		return 0, errShape
	}
	if n.Sign() <= 0 {
		return 1, nil
	}
	if n.Cmp(big.NewInt(10)) >= 0 {
		return 10, nil
	}
	return int(n.Int64()), nil
}

func (l *Loader) toolGaps(ctx context.Context, snapshot *ReaderSnapshot, page any) (map[string]any, error) {
	index := snapshot.index
	meta, ok := index["metadata"].(map[string]any)
	if !ok {
		return nil, errShape
	}
	catalog, _ := index["catalog"].(map[string]any)
	filter := func(raw any, key string) ([]any, error) {
		rows, ok := raw.([]any)
		if !ok {
			return nil, errShape
		}
		result := []any{}
		for _, raw := range rows {
			row, ok := raw.(map[string]any)
			if !ok {
				return nil, errShape
			}
			if page == nil || row[key] == page {
				result = append(result, copyManualJSON(row))
			}
		}
		return result, nil
	}
	gaps, err := filter(index["gaps"], "page")
	if err != nil {
		return nil, err
	}
	stale := []string{}
	if l.settings.SourceRoot != "" {
		inputs := map[string]any{}
		if len(catalog) > 0 {
			files, ok := catalog["files"].(map[string]any)
			if !ok {
				return nil, errShape
			}
			for relative, raw := range files {
				record, ok := raw.(map[string]any)
				if !ok {
					return nil, errShape
				}
				inputs[relative] = record["sha256"]
			}
		}
		dependencies, ok := index["dependencies"].(map[string]any)
		if !ok {
			return nil, errShape
		}
		for relative, expected := range dependencies {
			inputs[relative] = expected
		}
		for relative, expected := range inputs {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			path, err := owned(l.settings.SourceRoot, relative)
			if err != nil {
				return nil, err
			}
			data, err := os.ReadFile(path)
			if err != nil || hash(data) != expected {
				stale = append(stale, relative)
			}
		}
		deleted, ok := defaultManual(meta, "development_deleted_files", []any{}).([]any)
		if !ok {
			return nil, errShape
		}
		for _, raw := range deleted {
			relative, ok := raw.(string)
			if !ok {
				return nil, errShape
			}
			path, err := owned(l.settings.SourceRoot, relative)
			if err != nil {
				return nil, err
			}
			present, err := exists(path)
			if err != nil {
				return nil, err
			}
			if present {
				stale = append(stale, relative)
			}
		}
	}
	pages, backlog := []any{}, []any{}
	domains := map[string]any{}
	coverage := "section_gaps_only"
	chunks, ok := index["chunks"].([]any)
	if !ok {
		return nil, errShape
	}
	allGaps, ok := index["gaps"].([]any)
	if !ok {
		return nil, errShape
	}
	var summary any = map[string]any{"verified_sections": len(chunks), "pending_sections": len(allGaps)}
	if len(catalog) > 0 {
		pages, err = filter(catalog["pages"], "path")
		if err != nil {
			return nil, err
		}
		if page != nil && len(pages) == 0 {
			return map[string]any{"status": "no_evidence", "message": "La pagina non è presente nel catalogo verificato."}, nil
		}
		backlog, err = filter(catalog["screenshot_backlog"], "page")
		if err != nil {
			return nil, err
		}
		allDomains, ok := catalog["domains"].(map[string]any)
		if !ok {
			return nil, errShape
		}
		for _, raw := range pages {
			row := raw.(map[string]any)
			names, ok := row["domains"].([]any)
			if !ok {
				return nil, errShape
			}
			for _, raw := range names {
				name, ok := raw.(string)
				if !ok {
					return nil, errShape
				}
				value, present := allDomains[name]
				if !present {
					return nil, errShape
				}
				domains[name] = copyManualJSON(value)
			}
		}
		summary, ok = catalog["summary"]
		if !ok {
			return nil, errShape
		}
		summary = copyManualJSON(summary)
		coverage = "complete_inventory"
	}
	compatible := snapshot.revision == meta["application_revision"] && l.settings.Release == meta["release"]
	versionStatus, snapshotStatus := "compatible", "not_checked"
	if l.settings.SourceRoot != "" {
		snapshotStatus = "checked"
	}
	if !compatible {
		versionStatus = "incompatible"
	}
	if len(stale) > 0 || !compatible {
		snapshotStatus = "stale"
	}
	sort.Strings(stale)
	return map[string]any{"status": "diagnostic", "coverage_status": coverage, "snapshot_status": snapshotStatus, "version_status": versionStatus,
		"application_revision": copyManualJSON(meta["application_revision"]), "manual_revision": copyManualJSON(meta["manual_revision"]),
		"version": copyManualJSON(meta["release"]), "corpus_identity": copyManualJSON(index["identity"]), "summary": summary,
		"gaps": gaps, "pages": pages, "domains": domains, "screenshot_backlog": backlog, "stale_sources": stale}, nil
}
