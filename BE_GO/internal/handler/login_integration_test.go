//go:build logintransportintegration

package handler

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/labstack/echo/v4"
)

type forbiddenLoginStage struct{ calls atomic.Int64 }

func (s *forbiddenLoginStage) fail() error {
	s.calls.Add(1)
	return errors.New("pre-credential contract reached a later stage")
}
func (s *forbiddenLoginStage) ByID(context.Context, string) (domain.Account, error) {
	return domain.Account{}, s.fail()
}
func (s *forbiddenLoginStage) Resolve(context.Context, *domain.Account, domain.IdentityRequest) (domain.ResolvedIdentity, error) {
	return domain.ResolvedIdentity{}, s.fail()
}
func (s *forbiddenLoginStage) LoginRequest(context.Context, dto.LoginRequest, domain.LoginAuditContext) (*dto.LoginResponse, error) {
	return nil, s.fail()
}
func TestLoginTransportPortableHTTP(t *testing.T) {
	python := os.Getenv("ASSOZETA_CONTRACT_PYTHON")
	if python == "" {
		t.Fatal("run accounts adapter")
	}
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	privateDER, err := x509.MarshalPKCS8PrivateKey(private)
	if err != nil {
		t.Fatal(err)
	}
	publicDER, err := x509.MarshalPKIXPublicKey(public)
	if err != nil {
		t.Fatal(err)
	}
	manager, err := jwt.New(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateDER}), pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: publicDER}), time.Now)
	if err != nil {
		t.Fatal(err)
	}
	forbidden := &forbiddenLoginStage{}
	audit := func(*http.Request) (domain.LoginAuditContext, error) {
		return domain.LoginAuditContext{}, forbidden.fail()
	}
	h := NewLoginHandler(forbidden, service.NewActorAuthentication(forbidden, manager), forbidden, audit)
	e := echo.New()
	e.Any("/oauth2/login", h.Login)
	server := httptest.NewServer(e)
	defer server.Close()
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, "-m", "unittest", "TESTS.contracts.test_login_transport", "-v")
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "TESTS_BASE_URL="+server.URL)
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("portable login transport failed: %v\n%s", err, output)
	}
	t.Logf("%s", output)
	if forbidden.calls.Load() != 0 {
		t.Fatal("pre-credential contract reached account, identity, audit or session work")
	}
}
