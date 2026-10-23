package service

import (
	"context"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type InstanceManifest struct{ store domain.InstanceManifestStore }

func NewInstanceManifest(store domain.InstanceManifestStore) *InstanceManifest {
	return &InstanceManifest{store: store}
}

func (s *InstanceManifest) Read(ctx context.Context) (*dto.InstanceManifest, error) {
	branding, err := s.store.FirstManifestBranding(ctx)
	if err != nil {
		return nil, err
	}
	value := &dto.InstanceManifest{
		Name: "Bakney Sport", ShortName: "Bakney", Description: "Gestionale per associazioni sportive",
		StartURL: "/", Display: "standalone", BackgroundColor: "#ffffff", ThemeColor: "#351DC2",
	}
	icon := "/oem/assozeta/brand/logo.svg"
	if branding != nil {
		value.Name, value.ShortName, value.ThemeColor = branding.Name, branding.Abbreviation, branding.PrimaryColor
		if value.ShortName == "" {
			// Django slices Unicode code points, not UTF-8 bytes or graphemes.
			runes := []rune(branding.Name)
			if len(runes) > 12 {
				runes = runes[:12]
			}
			value.ShortName = string(runes)
		}
		if branding.Description != "" {
			value.Description = branding.Description
		}
		if branding.LogoPath != "" {
			icon = branding.LogoPath
		}
	}
	iconType := "image/png"
	switch {
	case strings.HasSuffix(icon, ".svg"):
		iconType = "image/svg+xml"
	case strings.HasSuffix(icon, ".jpg"), strings.HasSuffix(icon, ".jpeg"):
		iconType = "image/jpeg"
	case strings.HasSuffix(icon, ".webp"):
		iconType = "image/webp"
	}
	value.Icons = []dto.InstanceManifestIcon{{Source: icon, Sizes: "192x192", Type: iconType}, {Source: icon, Sizes: "512x512", Type: iconType}}
	return value, nil
}
