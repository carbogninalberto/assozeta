//go:build businessmcpintegration

package service_test

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

// TestBusinessMCPToolCall runs one tool call for TESTS/adapters/go/business_mcp_tools.py,
// which compares it with the Django tool function on the same private rows.
func TestBusinessMCPToolCall(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_BUSINESS_MCP_DSN")
	if !strings.Contains(dsn, "dbname=assozeta_contract_business_mcp") && !strings.Contains(dsn, "dbname=assozeta_contract_mcp_server") {
		t.Fatal("private business MCP database required")
	}
	db, err := sqlx.Open("pgx", dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	arguments, err := dto.ParseReferenceJSON([]byte(os.Getenv("ASSOZETA_BUSINESS_MCP_ARGUMENTS")))
	if err != nil {
		t.Fatal(err)
	}
	object, ok := arguments.(domain.RequestObject)
	if !ok {
		t.Fatal("arguments must be an object")
	}
	tools := service.NewBusinessMCPTools(repository.NewBusinessMCPTools(db), os.Getenv("ASSOZETA_BUSINESS_MCP_ASSOCIATION"), time.Now)
	result, err := tools.Execute(context.Background(), os.Getenv("ASSOZETA_BUSINESS_MCP_TOOL"), object)
	// The result as call_tool's json.dumps(result, default=str) writes it.
	var encoded []byte
	if errors.Is(err, service.ErrBusinessMCPNotPorted) {
		encoded, _ = json.Marshal(map[string]any{"refused": true})
	} else if err != nil {
		t.Fatal(err)
	} else {
		dumps := dto.PythonJSONDumps(result)
		encoded = append(append([]byte(`{"result": `), dumps...), '}')
	}
	if err = os.WriteFile(os.Getenv("ASSOZETA_BUSINESS_MCP_OUTPUT"), encoded, 0o600); err != nil {
		t.Fatal(err)
	}
}
