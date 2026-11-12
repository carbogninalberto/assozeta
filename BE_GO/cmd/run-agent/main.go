// Command run-agent is Django's run_agent management command
// (BE/application/management/commands/run_agent.py): an interactive CLI chat
// with the AI agent for one --association-id, printing CLICallback's output.
package main

import (
	"bufio"
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler/businessmcp"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr))
}

const disabled = "Il bot AI è disattivato per questa istanza."

// run parses --association-id (argparse usage and error texts, and Django's
// global command options, are not reproduced: invalid invocations exit 2).
func run(arguments []string, stdin io.Reader, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("run_agent", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	association := flags.String("association-id", "", "Sport association UUID")
	envFile := flags.String("env-file", "", "optional existing deployment .env (Go only)")
	present := false
	flags.Visit(func(*flag.Flag) {})
	if err := flags.Parse(arguments); err != nil || flags.NArg() != 0 {
		fmt.Fprintln(stderr, "run_agent: invalid arguments")
		return 2
	}
	flags.Visit(func(f *flag.Flag) { present = present || f.Name == "association-id" })
	if !present {
		fmt.Fprintln(stderr, "run_agent: the following arguments are required: --association-id")
		return 2
	}
	if err := chat(*association, *envFile, stdin, stdout, stderr); err != nil {
		// Django prints the exception's traceback here; it is not reproduced.
		fmt.Fprintln(stderr, "run_agent: "+err.Error())
		return 1
	}
	return 0
}

func chat(associationID, envFile string, stdin io.Reader, stdout, stderr io.Writer) error {
	cfg, err := config.Load(envFile)
	if err != nil {
		return errors.New("invalid environment configuration")
	}
	database, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		return errors.New("invalid database configuration")
	}
	database.DefaultQueryExecMode = pgx.QueryExecModeExec
	database.ConnectTimeout = 5 * time.Second
	if _, set := database.RuntimeParams["application_name"]; !set {
		database.RuntimeParams["application_name"] = "assozeta-go-run-agent"
	}
	db := sqlx.NewDb(ownedpgx.OpenDB(*database), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	runtime := newRuntime(cfg, db)
	id, valid := service.AgentUUID(associationID)
	if !valid {
		return errors.New("invalid association id") // ValidationError
	}
	association, found, err := runtime.Store.AssociationByID(ctx, id)
	if err != nil {
		return err
	}
	if !found {
		fmt.Fprintf(stderr, "Sport association %s not found\n", associationID)
		return nil
	}
	settings, err := runtime.AIConfig(ctx)
	if err != nil {
		return err
	}
	if !dto.PythonTruthy(settings["enabled"]) {
		fmt.Fprintln(stderr, disabled)
		return nil
	}
	provider, err := runtime.Provider(settings)
	if err != nil {
		return err
	}
	callback := &cliCallback{stdout: stdout, stderr: stderr}
	agent := runtime.NewAgent(provider, callback, association, "", settings, runtime.ManualAvailable(ctx))
	fmt.Fprintf(stdout, "\nAgent CLI per \"%s\"\n", association.Denomination)
	fmt.Fprint(stdout, "Scrivi un messaggio o \"exit\" per uscire.\n")
	reader := bufio.NewReader(stdin)
	for {
		fmt.Fprint(stdout, "\n> ")
		line, ok := readLine(reader)
		if !ok {
			break
		}
		command := strings.ToLower(dto.PythonStrip(line))
		if command == "exit" || command == "quit" || command == "esci" {
			break
		}
		if command == "clear" || command == "reset" || command == "pulisci" {
			agent.ClearHistory()
			fmt.Fprint(stdout, "Cronologia cancellata.\n")
			continue
		}
		if dto.PythonStrip(line) == "" {
			continue
		}
		settings, err := runtime.AIConfig(ctx)
		if err != nil {
			return err
		}
		if !dto.PythonTruthy(settings["enabled"]) {
			fmt.Fprintln(stderr, disabled)
			continue
		}
		provider, err := runtime.Provider(settings)
		if err != nil {
			return err
		}
		for _, key := range []string{"max_iterations", "history_cap"} {
			if _, present := settings[key]; !present {
				return service.ErrAgentNotPorted
			}
		}
		agent.Provider, agent.MaxIterations, agent.HistoryCap = provider, settings["max_iterations"], settings["history_cap"]
		if err := agent.ProcessMessage(ctx, line); err != nil {
			return err
		}
	}
	return nil
}

// readLine is input(): one line without its newline (universal newlines);
// false at end of input.
func readLine(reader *bufio.Reader) (string, bool) {
	var line strings.Builder
	for {
		r, _, err := reader.ReadRune()
		if err != nil {
			if line.Len() == 0 {
				return "", false
			}
			return line.String(), true
		}
		switch r {
		case '\n':
			return line.String(), true
		case '\r':
			if next, _, err := reader.ReadRune(); err == nil && next != '\n' {
				_ = reader.UnreadRune()
			}
			return line.String(), true
		}
		line.WriteRune(r)
	}
}

func newRuntime(cfg config.Config, db *sqlx.DB) *service.AgentRuntime {
	settings := cfg.InstanceAdmin
	manualSource := manual.New(cfg.Manual)
	admin := service.NewInstanceAdmin(repository.NewInstanceAdmin(db), nil, service.InstanceAdminEnvironment{
		AIAPIKey: settings.AIAPIKey, AIModel: settings.AIModel, AICheapModel: settings.AICheapModel, AIBaseURL: settings.AIBaseURL,
		AIIntegers: settings.AIIntegers, AIIntegersInvalid: settings.AIIntegersInvalid, SecretKeys: []string{cfg.SessionSecret},
	}, time.Now)
	return &service.AgentRuntime{
		Admin: admin, Manual: manualSource, ManualTools: service.NewManualTools(repository.NewManualToolContext(db), manualSource),
		Store: repository.NewAgentStore(db), Business: repository.NewBusinessMCPTools(db),
		Coordination: service.AgentCoordination{Lock: repository.NewRestoreFileLock(cfg.RestoreLockPath),
			States: repository.NewRestoreStates(db), Configured: cfg.RestoreLockPath != ""},
		Environment: service.AgentEnvironment{AIAPIKey: settings.AIAPIKey, AIAPIKeySet: settings.AIAPIKey != "",
			AIModel: settings.AIModel, AIBaseURL: settings.AIBaseURL},
		DefaultIterations: settings.AIIntegers[0], DefaultHistoryCap: settings.AIIntegers[5],
		ToolSchemas: businessmcp.AgentToolSchemas(), Now: time.Now,
	}
}

const (
	grey   = "\033[90m"
	cyan   = "\033[36m"
	green  = "\033[32m"
	yellow = "\033[33m"
	red    = "\033[31m"
	reset  = "\033[0m"
)

// cliCallback is application.agent.callbacks.CLICallback.
type cliCallback struct{ stdout, stderr io.Writer }

func (c *cliCallback) OnStatus(_ context.Context, status string) error {
	_, err := fmt.Fprintf(c.stdout, "%s[%s]%s\n", grey, status, reset)
	return err
}

func (c *cliCallback) OnToolCall(_ context.Context, name, arguments any) error {
	text, err := dto.AgentJSON{Indent: 2}.Dumps(arguments)
	if err != nil {
		return err
	}
	if points := dto.AgentCodePoints(text); len(points) > 500 {
		text = dto.AgentFromCodePoints(points[:500]) + "..."
	}
	_, err = fmt.Fprintf(c.stdout, "%s>> %s%s\n%s%s%s\n", cyan, dto.PythonStr(name), reset, grey, text, reset)
	return err
}

func (c *cliCallback) OnMessageChunk(_ context.Context, chunk string) error {
	_, err := fmt.Fprintf(c.stdout, "%s%s%s", green, chunk, reset)
	return err
}

func (c *cliCallback) OnMessageEnd(context.Context) error {
	_, err := fmt.Fprint(c.stdout, "\n")
	return err
}

func (c *cliCallback) OnManualSection(context.Context, any) error { return nil }

func (c *cliCallback) OnExportReady(_ context.Context, export domain.RequestObject) error {
	filename, rows := any("export"), any("0")
	if value, present := export.Lookup("filename"); present {
		filename = value
	}
	if value, present := export.Lookup("row_count"); present {
		rows = value
	}
	_, err := fmt.Fprintf(c.stdout, "%s[Export] %s (%s righe)%s\n", yellow, dto.PythonStr(filename), dto.PythonStr(rows), reset)
	return err
}

func (c *cliCallback) OnReportSaved(_ context.Context, report domain.RequestObject) error {
	name := any("report")
	if value, present := report.Lookup("name"); present {
		name = value
	}
	_, err := fmt.Fprintf(c.stdout, "%s[Report salvato] %s%s\n", yellow, dto.PythonStr(name), reset)
	return err
}

func (c *cliCallback) OnError(_ context.Context, message string) error {
	_, err := fmt.Fprintf(c.stderr, "%s[Errore] %s%s\n", red, message, reset)
	return err
}

func (c *cliCallback) OnDone(context.Context) error { return nil }
