package manual

import (
	"context"
	"os"
	"path/filepath"
	"strings"
)

// ReaderSnapshot owns request-local selection over an immutable, validated
// index. It is not an authorization boundary; HTTP callers must resolve identity
// and live instance features before requesting ordinary public documentation.
type ReaderSnapshot struct {
	index            map[string]any
	applicable       []map[string]any
	assetRoot        string
	contentAddressed bool
	preview          map[string]any
}

func (s *ReaderSnapshot) IsPreview() bool { return s != nil && s.preview != nil }
func (s *ReaderSnapshot) SectionIDs() []string {
	ids := []string{}
	if s == nil {
		return ids
	}
	if s.preview != nil {
		rows, _ := s.preview["sections"].([]any)
		for _, v := range rows {
			m, _ := v.(map[string]any)
			ids = append(ids, text(m["id"]))
		}
		return ids
	}
	for _, m := range s.applicable {
		ids = append(ids, text(m["id"]))
	}
	return ids
}
func readerError(ctx context.Context, err error) error {
	if ctx != nil && ctx.Err() != nil {
		return ctx.Err()
	}
	if err != nil {
		return ErrReaderUnavailable
	}
	return nil
}

// LoadReader follows the sections endpoint's source precedence. Availability
// intentionally has a different precedence and never calls this method.
func (l *Loader) LoadReader(ctx context.Context, features []string) (*ReaderSnapshot, error) {
	if ctx == nil {
		return nil, ErrReaderUnavailable
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if l.developmentEnabled() {
		s, err := l.loadPreview(ctx)
		if err != nil {
			return nil, readerError(ctx, err)
		}
		if s != nil {
			return s, nil
		}
	}
	s, err := l.loadReader(ctx, features)
	if err = readerError(ctx, err); err != nil {
		return nil, err
	}
	return s, nil
}
func (l *Loader) developmentEnabled() bool {
	return l.settings.DeploymentMode == "development" && l.settings.RunID == "" && l.settings.ApplicationRevision == ""
}
func (l *Loader) loadPreview(ctx context.Context) (*ReaderSnapshot, error) {
	root := filepath.Join(l.settings.DevelopmentRoot, "preview")
	found, err := exists(filepath.Join(root, "active.json"))
	if err != nil || !found {
		return nil, err
	}
	pointer, err := readObject(filepath.Join(root, "active.json"))
	if err != nil {
		return nil, err
	}
	identity := text(pointer["identity"])
	if !hex64.MatchString(identity) {
		return nil, errEvidence
	}
	root = filepath.Join(root, identity)
	raw, err := os.ReadFile(filepath.Join(root, "index.json"))
	if err != nil || hash(raw) != identity {
		return nil, errEvidence
	}
	decoded, err := decodeJSON(raw)
	if err != nil {
		return nil, err
	}
	value, err := object(decoded)
	if err != nil {
		return nil, err
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	return &ReaderSnapshot{preview: value, assetRoot: root}, nil
}
func (l *Loader) loadReader(ctx context.Context, features []string) (*ReaderSnapshot, error) {
	s := l.settings
	result := &ReaderSnapshot{}
	revision := s.ApplicationRevision
	if l.developmentEnabled() {
		found, err := exists(filepath.Join(s.DevelopmentRoot, "active.json"))
		if err != nil {
			return nil, err
		}
		if found {
			pointer, err := readObject(filepath.Join(s.DevelopmentRoot, "active.json"))
			if err != nil {
				return nil, err
			}
			id := text(pointer["corpus_identity"])
			if !one(pointer["format"]) || !hex64.MatchString(id) || s.SourceRoot == "" {
				return nil, errEvidence
			}
			result.assetRoot, err = owned(s.DevelopmentRoot, "packages/"+id)
			if err != nil {
				return nil, err
			}
			result.index, _, err = l.index(ctx, filepath.Join(result.assetRoot, "index.json"))
			if err != nil {
				return nil, err
			}
			meta, err := object(result.index["metadata"])
			if err != nil {
				return nil, err
			}
			if result.index["identity"] != id || meta["publication_status"] != "development-local" || !hex64.MatchString(text(meta["verified_corpus_identity"])) {
				return nil, errEvidence
			}
			if raw, present := meta["development_deleted_files"]; present {
				files, ok := raw.([]any)
				if !ok {
					return nil, errEvidence
				}
				for _, entry := range files {
					relative, ok := entry.(string)
					if !ok {
						return nil, errEvidence
					}
					path, err := owned(s.SourceRoot, relative)
					if err != nil {
						return nil, err
					}
					present, err := exists(path)
					if err != nil {
						return nil, err
					}
					if present {
						return nil, errEvidence
					}
				}
			}
			revision = text(meta["application_revision"])
		}
	}
	if result.index == nil {
		var err error
		result.index, err = l.loadBundle(ctx)
		if err != nil {
			return nil, err
		}
		if result.index != nil {
			result.assetRoot = s.BundlePath
			if result.assetRoot == "" {
				result.assetRoot = "."
			}
		} else {
			// Refresh failure retains the last compatible local corpus. Caller
			// cancellation remains observable and must never trigger fallback work.
			if err = l.refreshIfDue(ctx); err != nil {
				return nil, err
			}
			result.index, _, err = l.index(ctx, s.IndexPath)
			if err != nil {
				return nil, err
			}
			result.assetRoot = s.AssetRoot
			result.contentAddressed = result.assetRoot == ""
			if result.contentAddressed {
				result.assetRoot = filepath.Join(filepath.Dir(s.IndexPath), "assets")
			}
			meta, err := object(result.index["metadata"])
			if err != nil {
				return nil, err
			}
			if s.RunID == "" && (fallback(meta, "publication_status", "published") != "published" || fallback(meta, "code_state", "committed") != "committed") {
				return nil, errEvidence
			}
		}
	}
	meta, err := object(result.index["metadata"])
	if err != nil {
		return nil, err
	}
	check := s
	check.ApplicationRevision = revision
	if !version(meta, check) {
		return nil, errEvidence
	}
	result.applicable, err = readerApplicable(ctx, result.index, features, s.SourceRoot)
	if err != nil {
		return nil, err
	}
	return result, nil
}
func readerApplicable(ctx context.Context, index map[string]any, features []string, source string) ([]map[string]any, error) {
	chunks, ok := index["chunks"].([]any)
	if !ok {
		return nil, errEvidence
	}
	stale, err := stalePublicSections(ctx, index, chunks, source)
	if err != nil {
		return nil, err
	}
	enabled := map[string]bool{}
	for _, f := range features {
		enabled[f] = true
	}
	result := []map[string]any{}
	for _, raw := range chunks {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		m, err := object(raw)
		if err != nil {
			return nil, err
		}
		status, present := m["status"]
		if !present {
			return nil, errEvidence
		}
		if status != "verified" {
			continue
		}
		audience, present := m["audience"]
		if !present {
			return nil, errEvidence
		}
		if audience != "public" {
			continue
		}
		id, ok := m["id"].(string)
		if !ok {
			return nil, errEvidence
		}
		if stale[id] {
			continue
		}
		ok, err = readerFeatures(m["features"], enabled)
		if err != nil {
			return nil, err
		}
		if ok {
			result = append(result, m)
		}
	}
	return result, nil
}

// ReadAsset preserves the separate preview-prefix branch: an active preview
// never replaces the ordinary installed/runtime source for unprefixed assets.
func (l *Loader) ReadAsset(ctx context.Context, features []string, relative string) ([]byte, error) {
	if ctx == nil {
		return nil, ErrAssetNotFound
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	var s *ReaderSnapshot
	var err error
	preview := strings.HasPrefix(relative, "preview/")
	if preview {
		if !l.developmentEnabled() {
			return nil, ErrAssetNotFound
		}
		s, err = l.loadPreview(ctx)
		relative = strings.TrimPrefix(relative, "preview/")
	} else {
		s, err = l.loadReader(ctx, features)
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if err != nil || s == nil {
		return nil, ErrAssetNotFound
	}
	data, err := s.readAsset(ctx, relative)
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if err != nil {
		return nil, ErrAssetNotFound
	}
	return data, nil
}
func (s *ReaderSnapshot) readAsset(ctx context.Context, relative string) ([]byte, error) {
	if s.assetRoot == "" {
		return nil, ErrAssetNotFound
	}
	chunks := []any{}
	if s.preview != nil {
		var ok bool
		chunks, ok = s.preview["sections"].([]any)
		if !ok {
			return nil, ErrAssetNotFound
		}
	} else {
		for _, c := range s.applicable {
			chunks = append(chunks, c)
		}
	}
	var match map[string]any
outer:
	for _, raw := range chunks {
		c, ok := raw.(map[string]any)
		if !ok {
			return nil, ErrAssetNotFound
		}
		images, ok := c["screenshots"].([]any)
		if !ok {
			return nil, ErrAssetNotFound
		}
		for _, raw := range images {
			image, ok := raw.(map[string]any)
			if !ok {
				return nil, ErrAssetNotFound
			}
			p, present := image["path"]
			if !present {
				return nil, ErrAssetNotFound
			}
			if p == relative {
				match = image
				break outer
			}
		}
	}
	if match == nil {
		return nil, ErrAssetNotFound
	}
	expected := text(match["sha256"])
	target := relative
	if s.contentAddressed {
		if !hex64.MatchString(expected) {
			return nil, ErrAssetNotFound
		}
		target = expected + ".png"
	}
	path, err := owned(s.assetRoot, target)
	if err != nil {
		return nil, err
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	validSuffix := strings.ToLower(filepath.Ext(path)) == ".png"
	if s.preview != nil {
		validSuffix = strings.HasSuffix(relative, ".png")
	}
	if !validSuffix || hash(data) != expected {
		return nil, ErrAssetNotFound
	}
	return data, nil
}
