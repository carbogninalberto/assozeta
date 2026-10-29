//go:build manualmcpintegration

package mcpstdio_test

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler/manualmcp"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler/mcpstdio"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

const (
	manualMCPProcessEnv    = "ASSOZETA_MANUAL_MCP_GO_PROCESS"
	manualMCPFixtureEnv    = "ASSOZETA_MANUAL_MCP_GO_FIXTURE"
	manualMCPDSNEnv        = "ASSOZETA_MANUAL_MCP_GO_DSN"
	manualMCPMaxMessage    = 8 << 20
	manualMCPConcurrency   = 16
	manualMCPServerName    = "bakney-sport-data"
	manualMCPServerVersion = "go-manual-fixture"
)

type manualMCPProcessFixture struct {
	Settings      manual.Settings `json:"Settings"`
	AssociationID string          `json:"AssociationID"`
	ServerVersion string          `json:"ServerVersion"`
}

func TestMain(m *testing.M) {
	if os.Getenv(manualMCPProcessEnv) == "1" {
		if err := runManualMCPProcess(); err != nil {
			fmt.Fprintln(os.Stderr, "manual MCP integration fixture failed:", err)
			os.Exit(1)
		}
		os.Exit(0)
	}
	os.Exit(m.Run())
}

// runManualMCPProcess composes the real transport, authored validation and
// database-backed manual service. A zero identity makes the stdio surface
// public; the association remains an explicit caller-owned value.
func runManualMCPProcess() error {
	path := os.Getenv(manualMCPFixtureEnv)
	if path == "" || !strings.HasPrefix(filepath.Base(filepath.Dir(path)), "az-manual-http-") {
		return fmt.Errorf("private manual MCP fixture required")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var fixture manualMCPProcessFixture
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err = decoder.Decode(&fixture); err != nil {
		return err
	}
	if fixture.AssociationID == "" || fixture.ServerVersion != manualMCPServerVersion {
		return fmt.Errorf("invalid manual MCP fixture")
	}
	dsn := os.Getenv(manualMCPDSNEnv)
	if !strings.Contains(dsn, "dbname=assozeta_contract_manual_http") ||
		!strings.Contains(dsn, filepath.Dir(path)) ||
		!strings.Contains(dsn, "application_name=manual-mcp-go-contract") {
		return fmt.Errorf("owned PostgreSQL fixture required")
	}

	ctx := context.Background()
	db, err := sqlx.ConnectContext(ctx, "pgx", dsn)
	if err != nil {
		return err
	}
	defer db.Close()
	db.SetMaxOpenConns(manualMCPConcurrency)

	tools := service.NewManualTools(
		repository.NewManualToolContext(db),
		manual.New(fixture.Settings),
	)
	definitions := manualmcp.Definitions()
	public := make([]mcpstdio.Tool, 0, len(definitions))
	for _, definition := range definitions {
		public = append(public, mcpstdio.Tool{
			Name:        definition.Name,
			Description: definition.Description,
			InputSchema: definition.InputSchema,
		})
	}
	server, err := mcpstdio.New(
		public,
		func(ctx context.Context, name string, arguments map[string]any) (any, error) {
			return tools.Execute(ctx, domain.ResolvedIdentity{}, fixture.AssociationID, name, arguments)
		},
		manualmcp.Validate,
		mcpstdio.Options{
			ServerName:      manualMCPServerName,
			ServerVersion:   fixture.ServerVersion,
			MaxConcurrent:   manualMCPConcurrency,
			MaxMessageBytes: manualMCPMaxMessage,
			ValidateJSON:    manualmcp.ValidateJSON,
		},
	)
	if err != nil {
		return err
	}
	return server.Serve(ctx, os.Stdin, os.Stdout)
}
