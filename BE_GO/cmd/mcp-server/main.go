// Command mcp-server is Django's run_mcp_server management command
// (BE/application/management/commands/run_mcp_server.py): the MCP server of
// create_mcp_server over stdio for one --association-id.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler/businessmcp"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler/mcpstdio"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))
}

type options struct {
	transport, host, association, envFile string
	port                                  int
}

// parse reads the command's options (Django's argparse usage and error
// texts are not reproduced; invalid invocations exit 2 like argparse).
func parse(arguments []string, stderr io.Writer) (options, bool) {
	var opts options
	flags := flag.NewFlagSet("run_mcp_server", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	flags.StringVar(&opts.transport, "transport", "stdio", "Transport type (default: stdio)")
	flags.StringVar(&opts.host, "host", "0.0.0.0", "Host for SSE transport (default: 0.0.0.0)")
	port := flags.String("port", "8081", "Port for SSE transport (default: 8081)")
	flags.StringVar(&opts.envFile, "env-file", "", "optional existing deployment .env (Go only)")
	present := false
	flags.Func("association-id", "Sport association UUID to scope queries to", func(value string) error {
		opts.association, present = value, true
		return nil
	})
	if err := flags.Parse(arguments); err != nil || flags.NArg() != 0 {
		fmt.Fprintln(stderr, "run_mcp_server: invalid arguments")
		return opts, false
	}
	number, err := strconv.Atoi(*port)
	if err != nil || (opts.transport != "stdio" && opts.transport != "sse") {
		fmt.Fprintln(stderr, "run_mcp_server: invalid arguments")
		return opts, false
	}
	if !present {
		fmt.Fprintln(stderr, "run_mcp_server: the following arguments are required: --association-id")
		return opts, false
	}
	opts.port = number
	return opts, true
}

func run(arguments []string, stdin io.ReadCloser, stdout, stderr io.Writer) int {
	opts, ok := parse(arguments, stderr)
	if !ok {
		return 2
	}
	if opts.transport == "sse" {
		fmt.Fprintln(stdout, "SSE transport: use the MCP server directly with an SSE adapter. For now, use stdio transport.")
		return 0
	}
	if err := serve(opts, stdin, stdout, stderr); err != nil {
		fmt.Fprintln(stderr, err.Error())
		return 1
	}
	return 0
}

func serve(opts options, stdin io.ReadCloser, stdout, stderr io.Writer) error {
	cfg, err := config.Load(opts.envFile)
	if err != nil {
		return errors.New("invalid MCP server environment configuration")
	}
	database, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		return errors.New("invalid MCP server database configuration")
	}
	database.DefaultQueryExecMode = pgx.QueryExecModeExec
	database.ConnectTimeout = 5 * time.Second
	if _, set := database.RuntimeParams["application_name"]; !set {
		database.RuntimeParams["application_name"] = "assozeta-go-mcp-server"
	}
	db := sqlx.NewDb(ownedpgx.OpenDB(*database), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(2)
	db.SetConnMaxLifetime(30 * time.Minute)

	// In stdio mode stdout is the JSON-RPC channel: diagnostics go to stderr.
	fmt.Fprintf(stderr, "Starting MCP server (stdio) for association %s\n", opts.association)
	business := service.NewBusinessMCPTools(repository.NewBusinessMCPTools(db), opts.association, time.Now)
	manualTools := service.NewManualTools(repository.NewManualToolContext(db), manual.New(cfg.Manual))
	calls := businessmcp.NewCalls(business, manualTools, opts.association)
	server, err := mcpstdio.New(businessmcp.Tools(), nil, nil, mcpstdio.Options{
		ServerName:    businessmcp.ServerName,
		ServerVersion: businessmcp.SDKVersion,
		ValidateJSON:  businessmcp.Validate,
		OrderedJSON:   true,
		ExecuteRaw:    calls.Execute,
	})
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err = server.Serve(ctx, stdin, stdout); err != nil && !errors.Is(err, io.EOF) && !errors.Is(err, context.Canceled) {
		return err
	}
	return nil
}
