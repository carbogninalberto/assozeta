package service

import (
	"context"
	"errors"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
)

var ErrManualQueryTooLong = errors.New("manual query exceeds 2000 characters")

type ManualFeatureReader interface {
	ManualFeatures(context.Context) ([]string, error)
}
type ManualSourceReader interface {
	PreviewSections(context.Context, string) (map[string]any, error)
	Sections(context.Context, []string, string) (map[string]any, error)
	ReadAsset(context.Context, []string, string) ([]byte, error)
}
type ManualReader struct {
	features ManualFeatureReader
	source   ManualSourceReader
}

func NewManualReader(features ManualFeatureReader, source ManualSourceReader) *ManualReader {
	return &ManualReader{features, source}
}
func manualReaderIdentity(ctx context.Context, identity domain.ResolvedIdentity) error {
	if ctx == nil {
		return domain.ErrInvalidScopedRequest
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if identity.Actor == nil || identity.Effective == nil || identity.ScopeUser == nil || identity.Effective.ID == "" {
		return domain.ErrInvalidScopedRequest
	}
	return nil
}
func manualReaderResponse(value map[string]any, err error) (*dto.ManualSections, error) {
	if errors.Is(err, manual.ErrReaderUnavailable) {
		return &dto.ManualSections{Status: "no_evidence", Results: []any{}, Message: "Il manuale verificato non è disponibile per questa versione o deve essere aggiornato."}, nil
	}
	if err != nil {
		return nil, err
	}
	status, a := value["status"].(string)
	results, b := value["results"].([]any)
	message, c := value["message"].(string)
	if !a || !b || !c {
		return nil, manual.ErrReaderUnavailable
	}
	return &dto.ManualSections{Status: status, Results: results, Message: message}, nil
}
func (s *ManualReader) Sections(ctx context.Context, identity domain.ResolvedIdentity, query string) (*dto.ManualSections, error) {
	if err := manualReaderIdentity(ctx, identity); err != nil {
		return nil, err
	}
	query = strings.TrimFunc(query, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
	if utf8.RuneCountInString(query) > 2000 {
		return nil, ErrManualQueryTooLong
	}
	preview, err := s.source.PreviewSections(ctx, query)
	if err != nil || preview != nil {
		return manualReaderResponse(preview, err)
	}
	features, err := s.features.ManualFeatures(ctx)
	if err != nil {
		return nil, err
	}
	return manualReaderResponse(s.source.Sections(ctx, features, query))
}
func (s *ManualReader) Asset(ctx context.Context, identity domain.ResolvedIdentity, path string) ([]byte, error) {
	if err := manualReaderIdentity(ctx, identity); err != nil {
		return nil, err
	}
	var features []string
	if !strings.HasPrefix(path, "preview/") {
		var err error
		features, err = s.features.ManualFeatures(ctx)
		if err != nil {
			return nil, err
		}
	}
	return s.source.ReadAsset(ctx, features, path)
}
