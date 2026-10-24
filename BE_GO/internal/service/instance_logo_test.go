package service

import (
	"context"
	"errors"
	"io"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type logoStoreStub struct {
	calls     []string
	exists    map[string]bool
	existsErr error
	openErr   map[string]error
	cancel    context.CancelFunc
}

func (s *logoStoreStub) Exists(ctx context.Context, key string) (bool, error) {
	s.calls = append(s.calls, "exists:"+key)
	return s.exists[key], s.existsErr
}
func (s *logoStoreStub) Open(ctx context.Context, key string) (domain.InstanceLogoFile, error) {
	s.calls = append(s.calls, "open:"+key)
	if s.cancel != nil {
		s.cancel()
		return domain.InstanceLogoFile{}, ctx.Err()
	}
	if err := s.openErr[key]; err != nil {
		return domain.InstanceLogoFile{}, err
	}
	return domain.InstanceLogoFile{Body: io.NopCloser(strings.NewReader(key)), Size: int64(len(key))}, nil
}
func TestInstanceLogoSelectionAndFailureBoundary(t *testing.T) {
	fault := errors.New("private storage failure")
	tests := []struct {
		name     string
		store    logoStoreStub
		filename string
		err      error
		calls    []string
	}{
		{"priority", logoStoreStub{exists: map[string]bool{"instance/logo.png": true, "instance/logo.jpg": true}}, "logo.png", nil, []string{"exists:instance/logo.png", "open:instance/logo.png"}},
		{"open failure advances", logoStoreStub{exists: map[string]bool{"instance/logo.png": true, "instance/logo.jpg": true}, openErr: map[string]error{"instance/logo.png": fault}}, "logo.jpg", nil, []string{"exists:instance/logo.png", "open:instance/logo.png", "exists:instance/logo.jpg", "open:instance/logo.jpg"}},
		{"exists failure aborts", logoStoreStub{existsErr: fault}, "", fault, []string{"exists:instance/logo.png"}},
		{"missing", logoStoreStub{}, "", domain.ErrInstanceLogoMissing, []string{"exists:instance/logo.png", "exists:instance/logo.jpg", "exists:instance/logo.svg", "exists:instance/logo.webp"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := NewInstanceLogo(&tt.store).Read(context.Background())
			if !errors.Is(err, tt.err) {
				t.Fatalf("error %v, want %v", err, tt.err)
			}
			if got != nil {
				defer got.Body.Close()
				if got.Filename != tt.filename {
					t.Fatal(got.Filename)
				}
			}
			if strings.Join(tt.store.calls, ";") != strings.Join(tt.calls, ";") {
				t.Fatal(tt.store.calls)
			}
		})
	}
}
func TestInstanceLogoCancellationDoesNotTryOtherCandidates(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	store := logoStoreStub{exists: map[string]bool{"instance/logo.png": true}, cancel: cancel}
	if _, err := NewInstanceLogo(&store).Read(ctx); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	if len(store.calls) != 2 {
		t.Fatal(store.calls)
	}
}
