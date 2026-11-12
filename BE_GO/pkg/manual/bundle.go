package manual

import (
	"bytes"
	"context"
	"errors"
	"os"
	"strings"
)

// CheckBundle implements the explicit image gate, including Django's
// verify_assets=True PNG path/hash/header checks. It never consults a publisher,
// runtime index or development fallback. Optional absent/placeholder packages
// pass; any present invalid package fails even when optional.
func CheckBundle(ctx context.Context, settings Settings) error {
	if ctx == nil {
		return errors.New("manual bundle check requires context")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	loader := New(settings)
	index, err := loader.loadBundle(ctx)
	if err != nil {
		return bundleError(ctx)
	}
	if index == nil {
		return nil
	}
	images := map[string]string{}
	for _, raw := range index["chunks"].([]any) {
		chunk, err := object(raw)
		if err != nil {
			return bundleError(ctx)
		}
		screenshots, ok := chunk["screenshots"].([]any)
		if !ok {
			return bundleError(ctx)
		}
		for _, rawImage := range screenshots {
			image, err := object(rawImage)
			if err != nil {
				return bundleError(ctx)
			}
			path, pathOK := image["path"].(string)
			digest, digestOK := image["sha256"].(string)
			if !pathOK || !digestOK {
				return bundleError(ctx)
			}
			images[path] = digest
		}
	}
	for relative, expected := range images {
		if err := ctx.Err(); err != nil {
			return err
		}
		path, err := owned(settings.BundlePath, relative)
		if err != nil {
			return bundleError(ctx)
		}
		if !strings.HasSuffix(relative, ".png") {
			return bundleError(ctx)
		}
		contents, err := os.ReadFile(path)
		if err != nil || hash(contents) != expected || len(contents) < 24 || !bytes.Equal(contents[:8], []byte("\x89PNG\r\n\x1a\n")) || string(contents[12:16]) != "IHDR" {
			return bundleError(ctx)
		}
	}
	return ctx.Err()
}
func bundleError(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	return errors.New("manual bundle validation failed")
}
