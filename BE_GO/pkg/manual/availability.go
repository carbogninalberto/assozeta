// Package manual validates existing evidence packages and provides installed
// asset reads and sealed-model retrieval. HTTP admission is composed separately.
package manual

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"sync"
)

var errEvidence = errors.New("invalid manual evidence")
var errShape = errors.New("manual metadata has incompatible structure")

type Settings struct {
	BundlePath          string
	BundleRequired      bool
	IndexPath           string
	CorpusBaseURL       string
	DeploymentMode      string
	ApplicationRevision string
	Release             string
	RunID               string
	SourceRoot          string
	DevelopmentRoot     string
}

func (Settings) String() string     { return "Manual availability settings (paths and source redacted)" }
func (s Settings) GoString() string { return s.String() }

type Loader struct {
	settings Settings
	mu       sync.Mutex
	indexes  []cachedIndex
}
type cachedIndex struct {
	path   string
	stamp  fileStamp
	value  map[string]any
	digest string
}
type fileStamp struct {
	info    os.FileInfo
	changed string
}

func stamp(path string) (fileStamp, error) {
	info, err := os.Stat(path)
	if err != nil {
		return fileStamp{}, err
	}
	changed := ""
	raw := reflect.ValueOf(info.Sys())
	if raw.Kind() == reflect.Pointer {
		raw = raw.Elem()
	}
	if raw.IsValid() && raw.Kind() == reflect.Struct {
		for _, name := range []string{"Ctim", "Ctimespec"} {
			f := raw.FieldByName(name)
			if f.IsValid() {
				changed = fmt.Sprint(f.Interface())
				break
			}
		}
	}
	return fileStamp{info, changed}, nil
}
func (s fileStamp) equal(other fileStamp) bool {
	return os.SameFile(s.info, other.info) && s.info.Size() == other.info.Size() && s.info.ModTime().Equal(other.info.ModTime()) && s.changed == other.changed
}
func New(settings Settings) *Loader { return &Loader{settings: settings} }

// Available follows Django's precedence, including fail-closed caught evidence
// errors. Context errors and unexpected shape failures remain errors; diagnostics
// never include local paths, publisher URLs or artifact contents.
func (l *Loader) Available(ctx context.Context) (bool, error) {
	if ctx == nil {
		return false, errors.New("manual availability requires context")
	}
	if err := ctx.Err(); err != nil {
		return false, err
	}
	available, err := l.available(ctx)
	if cancelled := ctx.Err(); cancelled != nil {
		return false, cancelled
	}
	if err == nil {
		return available, nil
	}
	if errors.Is(err, errShape) {
		return false, errShape
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false, err
	}
	return false, nil
}
func (l *Loader) available(ctx context.Context) (bool, error) {
	s := l.settings
	found, err := l.bundle(ctx)
	if err != nil || found {
		return found, err
	}
	if strings.TrimSpace(s.CorpusBaseURL) != "" {
		return true, nil
	}
	if s.DeploymentMode == "development" && s.RunID == "" && s.ApplicationRevision == "" {
		found, err = l.preview(ctx)
		if err != nil || found {
			return found, err
		}
		found, err = l.development(ctx)
		if err != nil || found {
			return found, err
		}
	}
	index, _, err := l.index(ctx, s.IndexPath)
	if err != nil {
		return false, err
	}
	meta, err := object(index["metadata"])
	if err != nil {
		return false, err
	}
	if !version(meta, s) {
		return false, errEvidence
	}
	if s.RunID != "" {
		return true, nil
	}
	return fallback(meta, "publication_status", "published") == "published" && fallback(meta, "code_state", "committed") == "committed", nil
}

var hex64 = regexp.MustCompile(`^[0-9a-f]{64}$`)
var hex40 = regexp.MustCompile(`^[0-9a-f]{40}$`)

func text(value any) string { v, _ := value.(string); return v }
func fallback(o map[string]any, k, def string) string {
	v, ok := o[k]
	if !ok {
		return def
	}
	return text(v)
}
func object(value any) (map[string]any, error) {
	o, ok := value.(map[string]any)
	if !ok {
		return nil, errEvidence
	}
	return o, nil
}
func one(value any) bool {
	switch v := value.(type) {
	case bool:
		return v
	case json.Number:
		n, e := v.Float64()
		return e == nil && n == 1
	}
	return false
}
func version(meta map[string]any, s Settings) bool {
	return s.ApplicationRevision != "" && s.Release != "" && meta["application_revision"] == s.ApplicationRevision && meta["release"] == s.Release
}
func hash(raw []byte) string { v := sha256.Sum256(raw); return hex.EncodeToString(v[:]) }
func readObject(path string) (map[string]any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	value, err := decodeJSON(raw)
	if err != nil {
		return nil, err
	}
	return object(value)
}
func exists(path string) (bool, error) {
	_, err := os.Stat(path)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	return err == nil, err
}
func owned(root, relative string) (string, error) {
	base, err := resolveMissing(root)
	if err != nil {
		return "", err
	}
	target := base + string(filepath.Separator) + relative
	if filepath.IsAbs(relative) {
		target = relative
	}
	path, err := resolveMissing(target)
	if err != nil {
		return "", err
	}
	rel, err := filepath.Rel(base, path)
	if err != nil || rel == "." || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", errEvidence
	}
	return path, nil
}

// Resolve components in filesystem order: cleaning "symlink/.." first changes
// ownership. Missing components are allowed, matching pathlib strict=False.
func resolveMissing(path string) (string, error) {
	if !filepath.IsAbs(path) {
		cwd, err := os.Getwd()
		if err != nil {
			return "", err
		}
		path = cwd + string(filepath.Separator) + path
	}
	parts := strings.Split(path, string(filepath.Separator))
	current := string(filepath.Separator)
	links := 0
	for len(parts) > 0 {
		part := parts[0]
		parts = parts[1:]
		if part == "" || part == "." {
			continue
		}
		if part == ".." {
			current = filepath.Dir(current)
			continue
		}
		candidate := filepath.Join(current, part)
		info, err := os.Lstat(candidate)
		if errors.Is(err, os.ErrNotExist) {
			current = candidate
			continue
		}
		if err != nil {
			return "", err
		}
		if info.Mode()&os.ModeSymlink == 0 {
			current = candidate
			continue
		}
		links++
		if links > 40 {
			return "", errEvidence
		}
		target, err := os.Readlink(candidate)
		if err != nil {
			return "", err
		}
		if filepath.IsAbs(target) {
			current = string(filepath.Separator)
		}
		parts = append(strings.Split(target, string(filepath.Separator)), parts...)
	}
	return current, nil
}
func (l *Loader) index(ctx context.Context, path string) (map[string]any, string, error) {
	path, err := filepath.Abs(path)
	if err != nil {
		return nil, "", err
	}
	path, err = filepath.EvalSymlinks(path)
	if err != nil {
		return nil, "", err
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	before, err := stamp(path)
	if err != nil {
		return nil, "", err
	}
	for i, cached := range l.indexes {
		if cached.path == path && cached.stamp.equal(before) {
			l.indexes = append(append(l.indexes[:i:i], l.indexes[i+1:]...), cached)
			return cached.value, cached.digest, nil
		}
	}
	if err := ctx.Err(); err != nil {
		return nil, "", err
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, "", err
	}
	decoded, err := decodeSealedJSON(raw)
	if err != nil {
		return nil, "", err
	}
	value, err := object(decoded)
	if err != nil {
		return nil, "", err
	}
	identity := value["identity"]
	delete(value, "identity")
	canonical, err := canonical(value)
	if err != nil {
		return nil, "", err
	}
	if !one(value["format"]) || value["embedding"] != "italian-tfidf-lsa-v5" || identity != hash(canonical) {
		return nil, "", errEvidence
	}
	deps, err := object(value["dependencies"])
	if err != nil {
		return nil, "", err
	}
	bindings := map[string]any{}
	for k, v := range deps {
		bindings[k] = v
	}
	chunks, ok := value["chunks"].([]any)
	if !ok {
		return nil, "", errEvidence
	}
	for _, rawChunk := range chunks {
		chunk, err := object(rawChunk)
		if err != nil {
			return nil, "", errShape
		}
		var evidence []any
		if v, ok := chunk["evidence"]; ok {
			var good bool
			evidence, good = v.([]any)
			if !good {
				return nil, "", errEvidence
			}
		}
		declarations := map[string]any{}
		for _, rawSource := range evidence {
			source, err := object(rawSource)
			if err != nil {
				return nil, "", err
			}
			p, ok := source["path"].(string)
			if !ok {
				return nil, "", errEvidence
			}
			expected, present := source["sha256"]
			if !present {
				return nil, "", errEvidence
			}
			if previous, ok := bindings[p]; ok && !reflect.DeepEqual(previous, expected) {
				return nil, "", errEvidence
			}
			bindings[p] = expected
		}
		if rawDeps, present := chunk["runtime_dependencies"]; present {
			declarations, err = object(rawDeps)
			if err != nil {
				return nil, "", errShape
			}
		}
		for p, expected := range declarations {
			if previous, ok := bindings[p]; ok && !reflect.DeepEqual(previous, expected) {
				return nil, "", errEvidence
			}
			bindings[p] = expected
		}
	}
	after, err := stamp(path)
	if err != nil || !before.equal(after) {
		return nil, "", errEvidence
	}
	value["identity"] = identity
	cached := cachedIndex{path, before, value, hash(raw)}
	l.indexes = append(l.indexes, cached)
	if len(l.indexes) > 2 {
		l.indexes = l.indexes[len(l.indexes)-2:]
	}
	return value, cached.digest, nil
}
func (l *Loader) bundle(ctx context.Context) (bool, error) {
	index, err := l.loadBundle(ctx)
	return index != nil, err
}
func (l *Loader) loadBundle(ctx context.Context) (map[string]any, error) {
	s := l.settings
	// pathlib.Path("") denotes the working directory, not a missing path.
	if s.BundlePath == "" {
		s.BundlePath = "."
	}
	present, err := exists(s.BundlePath)
	if err != nil {
		return nil, err
	}
	if !present {
		if s.BundleRequired {
			return nil, errEvidence
		}
		return nil, nil
	}
	if !s.BundleRequired {
		files, err := os.ReadDir(s.BundlePath)
		if err != nil {
			return nil, err
		}
		nonempty := false
		for _, f := range files {
			if f.Name() != ".gitkeep" {
				nonempty = true
				break
			}
		}
		if !nonempty {
			return nil, nil
		}
	}
	indexPath, err := owned(s.BundlePath, "index.json")
	if err != nil {
		return nil, err
	}
	receiptPath, err := owned(s.BundlePath, "bundle.json")
	if err != nil {
		return nil, err
	}
	receipt, err := readObject(receiptPath)
	if err != nil {
		return nil, err
	}
	index, indexHash, err := l.index(ctx, indexPath)
	if err != nil {
		return nil, err
	}
	meta, err := object(index["metadata"])
	if err != nil {
		return nil, err
	}
	verification, err := object(meta["verification"])
	if err != nil {
		return nil, err
	}
	chunks, _ := index["chunks"].([]any)
	if !version(meta, s) || !one(receipt["format"]) || receipt["distribution"] != "immutable-backend-image" || receipt["index_sha256"] != indexHash || receipt["corpus_identity"] != index["identity"] || receipt["application_revision"] != s.ApplicationRevision || receipt["release"] != s.Release || !reflect.DeepEqual(receipt["manual_revision"], meta["manual_revision"]) || !hex40.MatchString(text(meta["manual_revision"])) || meta["publication_status"] != "release-bundled" || meta["code_state"] != "committed" || meta["manual_state"] != "generated-from-committed" || !reflect.DeepEqual(receipt["verified_corpus_identity"], verification["corpus_identity"]) || !hex64.MatchString(text(receipt["verified_corpus_identity"])) || len(chunks) == 0 {
		return nil, errEvidence
	}
	return index, nil
}
func (l *Loader) preview(ctx context.Context) (bool, error) {
	root := filepath.Join(l.settings.DevelopmentRoot, "preview")
	p := filepath.Join(root, "active.json")
	present, err := exists(p)
	if err != nil || !present {
		return false, err
	}
	pointer, err := readObject(p)
	if err != nil {
		return false, err
	}
	identity := text(pointer["identity"])
	if !hex64.MatchString(identity) {
		return false, errEvidence
	}
	if err := ctx.Err(); err != nil {
		return false, err
	}
	raw, err := os.ReadFile(filepath.Join(root, identity, "index.json"))
	if err != nil {
		return false, err
	}
	if hash(raw) != identity {
		return false, errEvidence
	}
	_, err = decodeJSON(raw)
	return err == nil, err
}
func (l *Loader) development(ctx context.Context) (bool, error) {
	s := l.settings
	p := filepath.Join(s.DevelopmentRoot, "active.json")
	present, err := exists(p)
	if err != nil || !present {
		return false, err
	}
	pointer, err := readObject(p)
	if err != nil {
		return false, err
	}
	identity := text(pointer["corpus_identity"])
	if !one(pointer["format"]) || !hex64.MatchString(identity) || s.SourceRoot == "" {
		return false, errEvidence
	}
	pkg, err := owned(s.DevelopmentRoot, "packages/"+identity)
	if err != nil {
		return false, err
	}
	index, _, err := l.index(ctx, filepath.Join(pkg, "index.json"))
	if err != nil {
		return false, err
	}
	meta, err := object(index["metadata"])
	if err != nil {
		return false, err
	}
	if index["identity"] != identity || meta["publication_status"] != "development-local" || !hex64.MatchString(text(meta["verified_corpus_identity"])) {
		return false, errEvidence
	}
	if raw, present := meta["development_deleted_files"]; present {
		files, ok := raw.([]any)
		if !ok {
			return false, errEvidence
		}
		for _, entry := range files {
			relative, ok := entry.(string)
			if !ok {
				return false, errEvidence
			}
			path, err := owned(s.SourceRoot, relative)
			if err != nil {
				return false, err
			}
			present, err := exists(path)
			if err != nil {
				return false, err
			}
			if present {
				return false, errEvidence
			}
		}
	}
	return true, nil
}
