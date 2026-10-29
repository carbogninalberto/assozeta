package manual

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
)

var ErrReaderUnavailable = errors.New("verified manual bundle unavailable")
var ErrAssetNotFound = errors.New("verified manual asset not found")

type publicAsset struct {
	path, digest string
	valid        bool
}

// PublicBundle is a request-local view of an installed immutable bundle. It
// exposes no source evidence or cached JSON. Load a new view for each request:
// source hashes and live feature selection are intentionally not cached.
// This API does not implement HTTP authentication, search, preview or sync.
type PublicBundle struct {
	root   string
	ids    []string
	assets []publicAsset
}

func (b *PublicBundle) SectionIDs() []string { return append([]string{}, b.ids...) }

// LoadPublicBundle validates the existing image receipt and selects ordinary
// verified content. Even an instance owner receives only public sections.
func (l *Loader) LoadPublicBundle(ctx context.Context, features []string) (*PublicBundle, error) {
	if ctx == nil {
		return nil, ErrReaderUnavailable
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	result, err := l.publicBundle(ctx, features)
	if canceled := ctx.Err(); canceled != nil {
		return nil, canceled
	}
	if err != nil {
		return nil, ErrReaderUnavailable
	}
	return result, nil
}
func (l *Loader) publicBundle(ctx context.Context, features []string) (*PublicBundle, error) {
	index, err := l.loadBundle(ctx)
	if err != nil || index == nil {
		return nil, ErrReaderUnavailable
	}
	chunks, ok := index["chunks"].([]any)
	if !ok {
		return nil, ErrReaderUnavailable
	}
	enabled := map[string]bool{}
	for _, feature := range features {
		enabled[feature] = true
	}
	candidates := []any{}
	for _, raw := range chunks {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		chunk, ok := raw.(map[string]any)
		if !ok {
			return nil, ErrReaderUnavailable
		}
		status, present := chunk["status"]
		if !present {
			return nil, ErrReaderUnavailable
		}
		if status != "verified" {
			continue
		}
		audience, present := chunk["audience"]
		if !present {
			return nil, ErrReaderUnavailable
		}
		if audience != "public" {
			continue
		}
		if _, ok := chunk["id"].(string); !ok {
			return nil, ErrReaderUnavailable
		}
		required, present := chunk["features"]
		if !present {
			return nil, ErrReaderUnavailable
		}
		applicable, err := readerFeatures(required, enabled)
		if err != nil {
			return nil, err
		}
		if !applicable {
			continue
		}
		candidates = append(candidates, chunk)
	}
	stale, err := stalePublicSections(ctx, index, candidates, l.settings.SourceRoot)
	if err != nil {
		return nil, err
	}
	result := &PublicBundle{root: l.settings.BundlePath, ids: []string{}}
	if result.root == "" {
		result.root = "."
	}
	for _, raw := range candidates {
		chunk := raw.(map[string]any)
		id := chunk["id"].(string)
		if stale[id] {
			continue
		}
		result.ids = append(result.ids, id)
		// Malformed screenshot declarations affect asset reads, not section
		// applicability. Preserve their position: Django's first-match walk can
		// fail before reaching a later matching record.
		images, ok := chunk["screenshots"].([]any)
		if !ok {
			result.assets = append(result.assets, publicAsset{})
			continue
		}
		for _, raw := range images {
			image, ok := raw.(map[string]any)
			if !ok {
				result.assets = append(result.assets, publicAsset{})
				continue
			}
			path, present := image["path"]
			if !present {
				result.assets = append(result.assets, publicAsset{})
				continue
			}
			relative, isString := path.(string)
			if !isString {
				continue
			}
			result.assets = append(result.assets, publicAsset{relative, text(image["sha256"]), true})
		}
	}
	return result, nil
}
func readerFeatures(value any, enabled map[string]bool) (bool, error) {
	var items []any
	switch v := value.(type) {
	case []any:
		items = v
	case string:
		for _, r := range v {
			items = append(items, string(r))
		}
	case map[string]any:
		for k := range v {
			items = append(items, k)
		}
	default:
		return false, ErrReaderUnavailable
	}
	applicable := true
	for _, item := range items {
		switch item.(type) {
		case []any, map[string]any:
			return false, ErrReaderUnavailable
		}
		required, ok := item.(string)
		if !ok || !enabled[required] {
			applicable = false
		}
	}
	return applicable, nil
}

var readerSharedSources = map[string]bool{
	"BE/core/authentication.py": true, "BE/core/middleware.py": true, "BE/core/settings.py": true,
	"BE/application/permissions.py": true, "BE/application/permissions_registry.py": true,
	"BE/application/impersonation.py": true, "BE/application/impersonation_scope.py": true,
	"BE/application/urls.py": true, "UI/src/routes.js": true, "UI/src/utils/Permissions.js": true,
	"UI/src/utils/ApiMiddleware.js": true, "UI/src/utils/checkAuth.js": true,
}

func stalePublicSections(ctx context.Context, index map[string]any, chunks []any, sourceRoot string) (map[string]bool, error) {
	stale := map[string]bool{}
	if sourceRoot == "" {
		return stale, nil
	}
	shared := map[string]any{}
	deps, ok := index["dependencies"].(map[string]any)
	if !ok {
		return nil, ErrReaderUnavailable
	}
	for path, expected := range deps {
		if readerSharedSources[path] {
			shared[path] = expected
		}
	}
	checked := map[string]string{}
	for _, raw := range chunks {
		chunk, ok := raw.(map[string]any)
		if !ok {
			return nil, ErrReaderUnavailable
		}
		id, ok := chunk["id"].(string)
		if !ok {
			return nil, ErrReaderUnavailable
		}
		evidence, ok := chunk["evidence"].([]any)
		if !ok {
			return nil, ErrReaderUnavailable
		}
		bindings := map[string]any{}
		for _, raw := range evidence {
			record, ok := raw.(map[string]any)
			if !ok {
				return nil, ErrReaderUnavailable
			}
			path, ok := record["path"].(string)
			if !ok {
				return nil, ErrReaderUnavailable
			}
			expected, present := record["sha256"]
			if !present {
				return nil, ErrReaderUnavailable
			}
			bindings[path] = expected
		}
		if raw, present := chunk["runtime_dependencies"]; present {
			runtime, ok := raw.(map[string]any)
			if !ok {
				return nil, ErrReaderUnavailable
			}
			for path, expected := range runtime {
				bindings[path] = expected
			}
		}
		for path, expected := range shared {
			bindings[path] = expected
		}
		for relative, expected := range bindings {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			actual, found := checked[relative]
			if !found {
				path, err := owned(sourceRoot, relative)
				if err != nil {
					return nil, err
				}
				data, err := os.ReadFile(path)
				if err == nil {
					actual = hash(data)
				} // Missing/unreadable source makes only bound sections stale.
				checked[relative] = actual
			}
			if actual == "" || actual != text(expected) {
				stale[id] = true
			}
		}
	}
	return stale, nil
}

// ReadAsset reads once and returns an independent verified byte buffer. Runtime
// parity checks the resolved filename suffix and capture hash, not PNG decoding;
// CheckBundle remains the stricter installation gate. The first matching image
// in applicable corpus order controls the result, including duplicate paths.
func (b *PublicBundle) ReadAsset(ctx context.Context, relative string) ([]byte, error) {
	if ctx == nil {
		return nil, ErrAssetNotFound
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	expected := ""
	found := false
	for _, image := range b.assets {
		if !image.valid {
			return nil, ErrAssetNotFound
		}
		if image.path == relative {
			expected = image.digest
			found = true
			break
		}
	}
	if !found {
		return nil, ErrAssetNotFound
	}
	path, err := owned(b.root, relative)
	if err != nil {
		return nil, ErrAssetNotFound
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path)
	if canceled := ctx.Err(); canceled != nil {
		return nil, canceled
	}
	if err != nil || strings.ToLower(filepath.Ext(path)) != ".png" || hash(data) != expected {
		return nil, ErrAssetNotFound
	}
	return data, nil
}
