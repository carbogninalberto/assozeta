package manual

import (
	"context"
	"sort"
	"strings"
)

func copyManualJSON(value any) any {
	switch v := value.(type) {
	case map[string]any:
		copy := make(map[string]any, len(v))
		for key, item := range v {
			copy[key] = copyManualJSON(item)
		}
		return copy
	case []any:
		copy := make([]any, len(v))
		for i, item := range v {
			copy[i] = copyManualJSON(item)
		}
		return copy
	default:
		return value
	}
}
func quoteManual(value string, slash bool) string {
	const hex = "0123456789ABCDEF"
	var b strings.Builder
	for i := 0; i < len(value); i++ {
		c := value[i]
		if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("-._~", rune(c)) || slash && c == '/' {
			b.WriteByte(c)
		} else {
			b.WriteByte('%')
			b.WriteByte(hex[c>>4])
			b.WriteByte(hex[c&15])
		}
	}
	return b.String()
}
func defaultManual(m map[string]any, key string, fallback any) any {
	if v, present := m[key]; present {
		return v
	}
	return fallback
}
func publicSection(chunk, index map[string]any, assetURLs bool) (map[string]any, error) {
	result := map[string]any{}
	for _, key := range []string{"id", "page", "section", "title", "text", "status", "url", "screenshots", "scenario_ids", "page_title", "language", "intent"} {
		value, present := chunk[key]
		if !present {
			return nil, ErrReaderUnavailable
		}
		result[key] = copyManualJSON(value)
	}
	id, err := stringField(chunk, "id")
	if err != nil {
		return nil, err
	}
	embedded := "/#/manuale?section=" + quoteManual(id, false)
	result["embedded_url"] = embedded
	result["url"] = embedded
	result["reader"] = copyManualJSON(defaultManual(chunk, "reader", []any{map[string]any{"title": "", "text": chunk["text"], "screenshots": []any{}}}))
	for _, key := range []string{"page_order", "section_order", "score"} {
		result[key] = copyManualJSON(defaultManual(chunk, key, nil))
	}
	result["page_description"] = copyManualJSON(defaultManual(chunk, "page_description", ""))
	images, ok := chunk["screenshots"].([]any)
	if !ok {
		return nil, ErrReaderUnavailable
	}
	projected := make([]any, len(images))
	for i, image := range images {
		if !assetURLs {
			projected[i] = copyManualJSON(image)
			continue
		}
		record, ok := image.(map[string]any)
		if !ok {
			return nil, ErrReaderUnavailable
		}
		path, err := stringField(record, "path")
		if err != nil {
			return nil, err
		}
		copy := copyManualJSON(record).(map[string]any)
		copy["url"] = "/api/manuale/assets/" + quoteManual(path, true)
		projected[i] = copy
	}
	result["screenshots"] = projected
	metadata, ok := index["metadata"].(map[string]any)
	if !ok {
		return nil, ErrReaderUnavailable
	}
	for output, input := range map[string]string{"version": "release", "application_revision": "application_revision", "manual_revision": "manual_revision"} {
		value, present := metadata[input]
		if !present {
			return nil, ErrReaderUnavailable
		}
		result[output] = copyManualJSON(value)
	}
	for _, key := range []string{"audience"} {
		value, present := chunk[key]
		if !present {
			return nil, ErrReaderUnavailable
		}
		result[key] = copyManualJSON(value)
	}
	features, present := chunk["features"]
	if !present {
		return nil, ErrReaderUnavailable
	}
	switch value := features.(type) {
	case []any:
		result["features"] = copyManualJSON(value)
	case string:
		items := []any{}
		for _, char := range value {
			items = append(items, string(char))
		}
		result["features"] = items
	case map[string]any:
		keys := make([]string, 0, len(value))
		for key := range value {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		items := make([]any, len(keys))
		for i, key := range keys {
			items[i] = key
		}
		result["features"] = items
	default:
		return nil, ErrReaderUnavailable
	}
	identity, present := index["identity"]
	if !present {
		return nil, ErrReaderUnavailable
	}
	result["corpus_identity"] = copyManualJSON(identity)
	result["code_state"] = copyManualJSON(defaultManual(metadata, "code_state", "committed"))
	result["publication_status"] = copyManualJSON(defaultManual(metadata, "publication_status", "published"))
	return result, nil
}

// readerSections accepts only the coordinator's validated immutable corpus and
// freshly applicable ordinary chunks. It neither selects sources nor grants
// authorization. Returned nested values are independent of the shared cache.
func readerSections(ctx context.Context, index map[string]any, applicable []map[string]any, assetURLs bool, query string) (map[string]any, error) {
	if ctx == nil {
		return nil, ErrReaderUnavailable
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	chunks := applicable
	if query != "" {
		var err error
		chunks, err = searchSections(ctx, index, applicable, query)
		if err != nil {
			return nil, err
		}
	}
	results := make([]any, 0, len(chunks))
	for _, chunk := range chunks {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		item, err := publicSection(chunk, index, assetURLs)
		if err != nil {
			return nil, err
		}
		results = append(results, item)
	}
	status, message := "verified", ""
	if len(results) == 0 {
		status = "no_evidence"
		message = "Non ho trovato istruzioni verificate per questa domanda."
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return map[string]any{"status": status, "results": results, "message": message}, nil
}
