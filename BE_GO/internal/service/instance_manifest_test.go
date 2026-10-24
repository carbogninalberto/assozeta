package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type manifestStoreFunc func(context.Context) (*domain.InstanceManifestBranding, error)

func (f manifestStoreFunc) FirstManifestBranding(ctx context.Context) (*domain.InstanceManifestBranding, error) {
	return f(ctx)
}

func TestManifestContextAndReadFailure(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	store := manifestStoreFunc(func(received context.Context) (*domain.InstanceManifestBranding, error) {
		if received != ctx {
			t.Fatal("caller context replaced")
		}
		return nil, received.Err()
	})
	value, err := NewInstanceManifest(store).Read(ctx)
	if value != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("database failure became default branding", value, err)
	}
}

func TestManifestUnicodeAndOpaqueLogo(t *testing.T) {
	branding := &domain.InstanceManifestBranding{Name: "😀é漢字abcdefghijklm", LogoPath: "/logo.svg?v=1", PrimaryColor: ""}
	value, err := NewInstanceManifest(manifestStoreFunc(func(context.Context) (*domain.InstanceManifestBranding, error) {
		return branding, nil
	})).Read(context.Background())
	if err != nil || value.ShortName != "😀é漢字abcdefgh" || value.ThemeColor != "" {
		t.Fatal(value, err)
	}
	if len(value.Icons) != 2 || value.Icons[0].Source != "/logo.svg?v=1" || value.Icons[0].Type != "image/png" || value.Icons[1].Sizes != "512x512" {
		t.Fatal("manifest changed stored logo or inferred suffix through query", value.Icons)
	}
	if branding.Abbreviation != "" || branding.LogoPath != "/logo.svg?v=1" {
		t.Fatal("read changed stored branding")
	}
}
