package manual

import (
	"context"
	"sort"
	"strings"
	"unicode"

	"golang.org/x/text/cases"
)

// PreviewSections returns nil when the ordinary reader must load instance
// features and select a verified source. Preview has no instance query in Django.
func (l *Loader) PreviewSections(ctx context.Context, query string) (map[string]any, error) {
	if ctx == nil {
		return nil, ErrReaderUnavailable
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if !l.developmentEnabled() {
		return nil, nil
	}
	snapshot, err := l.loadPreview(ctx)
	if err != nil {
		return nil, readerError(ctx, err)
	}
	if snapshot == nil {
		return nil, nil
	}
	return snapshot.Sections(ctx, query)
}

func (l *Loader) Sections(ctx context.Context, features []string, query string) (map[string]any, error) {
	snapshot, err := l.LoadReader(ctx, features)
	if err != nil {
		return nil, err
	}
	return snapshot.Sections(ctx, query)
}

// Sections projects request-local data. The caller owns query trimming, length
// admission and authenticated context; these are not filesystem permissions.
func (s *ReaderSnapshot) Sections(ctx context.Context, query string) (map[string]any, error) {
	if ctx == nil || s == nil {
		return nil, ErrReaderUnavailable
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if s.preview == nil {
		return readerSections(ctx, s.index, s.applicable, s.assetRoot != "", query)
	}
	rows, ok := s.preview["sections"].([]any)
	if !ok {
		return nil, ErrReaderUnavailable
	}
	type match struct {
		row       any
		titleHits int
	}
	matches := make([]match, 0, len(rows))
	fold := cases.Fold()
	terms := strings.FieldsFunc(fold.String(query), func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
	for _, raw := range rows {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		hits := 0
		if query != "" {
			row, ok := raw.(map[string]any)
			if !ok {
				return nil, ErrReaderUnavailable
			}
			page, a := row["page_title"].(string)
			title, b := row["title"].(string)
			body, c := row["text"].(string)
			if !a || !b || !c {
				return nil, ErrReaderUnavailable
			}
			haystack, heading := fold.String(page+" "+title+" "+body), fold.String(title)
			found := true
			for _, term := range terms {
				if !strings.Contains(haystack, term) {
					found = false
					break
				}
				if strings.Contains(heading, term) {
					hits++
				}
			}
			if !found {
				continue
			}
		}
		matches = append(matches, match{raw, hits})
	}
	if query != "" {
		sort.SliceStable(matches, func(i, j int) bool { return matches[i].titleHits > matches[j].titleHits })
		if len(matches) > 10 {
			matches = matches[:10]
		}
	}
	results := make([]any, len(matches))
	for i, match := range matches {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		results[i] = copyManualJSON(match.row)
	}
	message := ""
	if len(results) == 0 {
		message = "Nessuna sezione trovata per questa ricerca."
	}
	return map[string]any{"status": "development_preview", "results": results, "message": message}, ctx.Err()
}
