package manual

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

const maxCorpusBytes int64 = 64 * 1024 * 1024
const maxManualImageBytes int64 = 16 * 1024 * 1024

// The process gate mirrors Django's single refresh mutex; the on-volume flock
// also coordinates Go workers, Django workers and the existing operator command.
var manualRefreshGate = make(chan struct{}, 1)
var manualRefreshAttempts = map[[4]string]time.Time{}

func (l *Loader) refreshIfDue(ctx context.Context) error {
	s := l.settings
	if s.CorpusBaseURL == "" {
		return ctx.Err()
	}
	select {
	case manualRefreshGate <- struct{}{}:
	case <-ctx.Done():
		return ctx.Err()
	}
	defer func() { <-manualRefreshGate }()
	key := [4]string{s.CorpusBaseURL, s.ApplicationRevision, s.Release, s.IndexPath}
	now := time.Now()
	if then, ok := manualRefreshAttempts[key]; ok && now.Sub(then) < 300*time.Second {
		return ctx.Err()
	}
	manualRefreshAttempts[key] = now
	if err := l.synchronize(ctx); err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		slog.Warn("Manual corpus refresh failed; retaining the last validated local corpus.")
	}
	return ctx.Err()
}
func (l *Loader) validateDownloadURL(raw string) (*url.URL, error) {
	u, err := url.Parse(raw)
	if err != nil {
		return nil, errEvidence
	}
	host := strings.ToLower(u.Hostname())
	if (u.Scheme != "https" && !(l.settings.RunID != "" && u.Scheme == "http" && (host == "127.0.0.1" || host == "localhost"))) || u.Host == "" || u.User != nil || u.Fragment != "" {
		return nil, errEvidence
	}
	return u, nil
}
func (l *Loader) corpusURL() (string, error) {
	s := l.settings
	base := strings.TrimRight(s.CorpusBaseURL, "/")
	if base == "" {
		return "", errEvidence
	}
	u, err := l.validateDownloadURL(base)
	if err != nil || u.RawQuery != "" || !hex40.MatchString(s.ApplicationRevision) || s.Release == "" {
		return "", errEvidence
	}
	return base + "/" + s.ApplicationRevision + "/" + strings.ReplaceAll(url.QueryEscape(s.Release), "+", "%20") + ".json", nil
}
func (l *Loader) download(ctx context.Context, rawURL string, limit int64, timeout time.Duration) ([]byte, error) {
	if _, err := l.validateDownloadURL(rawURL); err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, errEvidence
	}
	client := http.Client{Timeout: timeout, CheckRedirect: func(req *http.Request, via []*http.Request) error {
		if len(via) >= 10 {
			return errEvidence
		}
		_, err := l.validateDownloadURL(req.URL.String())
		return err
	}}
	response, err := client.Do(request)
	if err != nil {
		return nil, errEvidence
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, errEvidence
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil || int64(len(data)) > limit {
		return nil, errEvidence
	}
	return data, nil
}
func manualFileLock(ctx context.Context, file *os.File) error {
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return nil
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			return err
		}
		timer := time.NewTimer(20 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}
func (l *Loader) synchronize(ctx context.Context) error {
	if ctx == nil {
		return errEvidence
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	url, err := l.corpusURL()
	if err != nil {
		return err
	}
	root := filepath.Dir(l.settings.IndexPath)
	if err = os.MkdirAll(root, 0777); err != nil {
		return err
	}
	lock, err := os.OpenFile(filepath.Join(root, ".sync.lock"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0666)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err = manualFileLock(ctx, lock); err != nil {
		return err
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	payload, err := l.download(ctx, url, maxCorpusBytes, 5*time.Second)
	if err != nil {
		return err
	}
	// Validate through the same sealed-index parser as installed/runtime reads.
	// This private candidate is never an active index and is removed on all exits.
	candidate, err := os.CreateTemp(root, ".manual-candidate-")
	if err != nil {
		return err
	}
	name := candidate.Name()
	defer os.Remove(name)
	if _, err = candidate.Write(payload); err != nil {
		candidate.Close()
		return err
	}
	if err = candidate.Close(); err != nil {
		return err
	}
	index, _, err := l.index(ctx, name)
	if err != nil {
		return err
	}
	meta, err := object(index["metadata"])
	if err != nil {
		return err
	}
	if meta["publication_status"] != "published" || meta["code_state"] != "committed" || meta["manual_state"] != "committed" || !hex40.MatchString(text(meta["manual_revision"])) || !version(meta, l.settings) {
		return errEvidence
	}
	chunks, ok := index["chunks"].([]any)
	if !ok {
		return errEvidence
	}
	stale, err := stalePublicSections(ctx, index, chunks, l.settings.SourceRoot)
	if err != nil || len(stale) > 0 {
		return errEvidence
	}
	images := []map[string]any{}
	hashes := map[string]bool{}
	for _, raw := range chunks {
		chunk, err := object(raw)
		if err != nil {
			return err
		}
		records, ok := chunk["screenshots"].([]any)
		if !ok {
			return errEvidence
		}
		for _, raw := range records {
			image, err := object(raw)
			if err != nil {
				return err
			}
			digest := text(image["sha256"])
			if !hex64.MatchString(digest) {
				return errEvidence
			}
			if _, err = l.validateDownloadURL(text(image["url"])); err != nil {
				return err
			}
			if !hashes[digest] {
				hashes[digest] = true
				images = append(images, image)
			}
		}
	}
	// Await every submitted worker before releasing the file lease. Failure stops
	// later batches, and no partial batch can publish the new active index.
	for start := 0; start < len(images); start += 8 {
		end := start + 8
		if end > len(images) {
			end = len(images)
		}
		var wg sync.WaitGroup
		failures := make(chan error, end-start)
		for _, image := range images[start:end] {
			wg.Add(1)
			go func(image map[string]any) { defer wg.Done(); failures <- l.installImage(ctx, image) }(image)
		}
		wg.Wait()
		close(failures)
		for err := range failures {
			if err != nil {
				return err
			}
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
	}
	if err = l.retirePreviousAssets(ctx, hashes); err != nil {
		return err
	}
	data, err := canonical(index)
	if err != nil {
		return err
	}
	if err = promoteManualBytes(ctx, l.settings.IndexPath, data); err != nil {
		return err
	}
	l.pruneAssets(hashes)
	return ctx.Err()
}
func pngHeader(data []byte) bool {
	return len(data) >= 24 && bytes.Equal(data[:8], []byte("\x89PNG\r\n\x1a\n")) && string(data[12:16]) == "IHDR"
}
func (l *Loader) installImage(ctx context.Context, image map[string]any) error {
	digest := text(image["sha256"])
	if !hex64.MatchString(digest) || !strings.HasSuffix(text(image["path"]), ".png") {
		return errEvidence
	}
	if _, err := l.validateDownloadURL(text(image["url"])); err != nil {
		return err
	}
	target, err := owned(filepath.Join(filepath.Dir(l.settings.IndexPath), "assets"), digest+".png")
	if err != nil {
		return err
	}
	if present, err := exists(target); err != nil {
		return err
	} else if present {
		contents, err := os.ReadFile(target)
		if err != nil {
			return err
		}
		if hash(contents) == digest {
			if !pngHeader(contents) {
				return errEvidence
			}
			return ctx.Err()
		}
	}
	contents, err := l.download(ctx, text(image["url"]), maxManualImageBytes, 10*time.Second)
	if err != nil {
		return err
	}
	if hash(contents) != digest || !pngHeader(contents) {
		return errEvidence
	}
	if err = os.MkdirAll(filepath.Dir(target), 0777); err != nil {
		return err
	}
	return promoteManualBytes(ctx, target, contents)
}
func promoteManualBytes(ctx context.Context, path string, data []byte) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".manual-promote-")
	if err != nil {
		return err
	}
	name := file.Name()
	defer os.Remove(name)
	if _, err = file.Write(data); err != nil {
		file.Close()
		return err
	}
	if err = file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err = file.Close(); err != nil {
		return err
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return os.Rename(name, path)
}
func (l *Loader) retirePreviousAssets(ctx context.Context, current map[string]bool) error {
	previous, _, err := l.index(ctx, l.settings.IndexPath)
	if err != nil {
		return ctx.Err()
	}
	chunks, ok := previous["chunks"].([]any)
	if !ok {
		return errEvidence
	}
	now := time.Now()
	root := filepath.Join(filepath.Dir(l.settings.IndexPath), "assets")
	for _, raw := range chunks {
		chunk, err := object(raw)
		if err != nil {
			return err
		}
		images, ok := chunk["screenshots"].([]any)
		if !ok {
			return errEvidence
		}
		for _, raw := range images {
			image, err := object(raw)
			if err != nil {
				return err
			}
			digest := text(image["sha256"])
			if !hex64.MatchString(digest) || current[digest] {
				continue
			}
			path := filepath.Join(root, digest+".png")
			info, err := os.Lstat(path)
			if os.IsNotExist(err) {
				continue
			}
			if err != nil {
				return err
			}
			if info.Mode().IsRegular() {
				stamp := unix.NsecToTimespec(now.UnixNano())
				if err = unix.UtimesNanoAt(unix.AT_FDCWD, path, []unix.Timespec{stamp, stamp}, unix.AT_SYMLINK_NOFOLLOW); err != nil {
					return err
				}
			}
		}
	}
	return ctx.Err()
}
func (l *Loader) pruneAssets(current map[string]bool) {
	root := filepath.Join(filepath.Dir(l.settings.IndexPath), "assets")
	entries, err := os.ReadDir(root)
	if os.IsNotExist(err) {
		return
	}
	if err != nil {
		slog.Warn("Manual screenshot cleanup deferred; active corpus retained.")
		return
	}
	cutoff := time.Now().Add(-24 * time.Hour)
	for _, entry := range entries {
		name := entry.Name()
		if !strings.HasSuffix(name, ".png") {
			continue
		}
		digest := strings.TrimSuffix(name, ".png")
		if !hex64.MatchString(digest) || current[digest] {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			slog.Warn("Manual screenshot cleanup deferred; active corpus retained.")
			continue
		}
		if info.Mode().IsRegular() && info.ModTime().Before(cutoff) {
			if err = os.Remove(filepath.Join(root, name)); err != nil {
				slog.Warn("Manual screenshot cleanup deferred; active corpus retained.")
			}
		}
	}
}
