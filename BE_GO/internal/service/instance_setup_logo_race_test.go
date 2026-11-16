package service

import (
	"context"
	"regexp"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/instancelogo"
)

type logoRaceStore struct {
	InstanceSetupStore
	setLogo []string
}

func (s *logoRaceStore) First(context.Context) (*domain.InstanceSetupConfiguration, error) {
	return nil, nil
}

func (s *logoRaceStore) SetLogo(_ context.Context, _ int64, logo string, _ time.Time) error {
	s.setLogo = append(s.setLogo, logo)
	return nil
}

type logoRaceBootstrap struct{ InstanceAdminBootstrap }

func (logoRaceBootstrap) Config(context.Context) (*dto.InstanceConfig, error) {
	return &dto.InstanceConfig{}, nil
}

type logoRacePasswords struct{ InstanceSetupPasswords }

func (logoRacePasswords) Encode(context.Context, string) (string, error) { return "", nil }

// logoRaceStorage keeps instance/logo.png taken even after the view's deletes:
// a concurrent request saved a logo there again between the deletes and the
// save.
type logoRaceStorage struct {
	taken   map[string]bool
	deletes []string
	saves   []string
}

func (s *logoRaceStorage) Exists(_ context.Context, name string) (bool, error) {
	return s.taken[name], nil
}

func (s *logoRaceStorage) Delete(_ context.Context, name string) error {
	s.deletes = append(s.deletes, name)
	return nil
}

func (s *logoRaceStorage) Save(_ context.Context, name string, _ []byte, _ instancelogo.WriteParameters) error {
	s.saves = append(s.saves, name)
	return nil
}

// TestUploadLogoSavesUnderAvailableName pins default_storage.get_available_name
// with file_overwrite off: a name taken again after the deletes is saved under
// file_root_<7 random characters><ext> (Django's Storage.save returns that
// name), never refused.
func TestUploadLogoSavesUnderAvailableName(t *testing.T) {
	storage := &logoRaceStorage{taken: map[string]bool{"instance/logo.png": true}}
	store := &logoRaceStore{}
	setup := NewInstanceSetup(store, logoRaceBootstrap{}, storage, logoRacePasswords{},
		InstanceSetupSettings{FileOverwrite: false}, func() time.Time { return time.Date(2026, 4, 1, 9, 0, 0, 0, time.UTC) })
	reply, err := setup.UploadLogo(context.Background(), &InstanceSetupUpload{ContentType: "image/png", Size: 12, Content: []byte("png")})
	if err != nil {
		t.Fatalf("a taken logo name is saved under an alternative, not refused: %v", err)
	}
	if len(storage.saves) != 1 {
		t.Fatalf("saves %#v", storage.saves)
	}
	alternative := regexp.MustCompile(`^instance/logo_[A-Za-z0-9]{7}\.png$`)
	if !alternative.MatchString(storage.saves[0]) {
		t.Fatalf("saved name %q, want instance/logo_<7 characters>.png", storage.saves[0])
	}
	if len(store.setLogo) != 0 {
		t.Fatalf("without a configuration the view stores nothing: %#v", store.setLogo)
	}
	if reply == nil || reply.Status != 200 || reply.Body.(map[string]any)["logo_url"] != instanceCanonicalLogo {
		t.Fatalf("reply %+v", reply)
	}
}
