package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/whatsapp"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

type usageError struct {
	usage, message string
}

func (e *usageError) Error() string {
	return "usage: whatsapp-templates " + e.usage + "\nwhatsapp-templates: error: " + e.message
}

func validateArgs(args []string) error {
	const actions = "{list,register,get} ..."
	if len(args) == 0 {
		return &usageError{actions, "the following arguments are required: action"}
	}
	required := 1
	switch args[0] {
	case "get":
		required = 3
		if len(args) < required {
			names := []string{"name", "language"}
			return &usageError{"get name language", "the following arguments are required: " + strings.Join(names[len(args)-1:], ", ")}
		}
	case "list", "register":
	default:
		return &usageError{actions, "argument action: invalid choice: " + dto.PythonStringRepr(args[0]) + " (choose from 'list', 'register', 'get')"}
	}
	if len(args) > required {
		return &usageError{actions, "unrecognized arguments: " + strings.Join(args[required:], " ")}
	}
	return nil
}

func run(args []string) error {
	if err := validateArgs(args); err != nil {
		return err
	}
	ctx := context.Background()
	settings := config.WhatsAppFromEnv(os.Getenv)
	if os.Getenv("DBHOST") != "" {
		cfg, err := config.Load("")
		if err != nil {
			return errors.New("invalid instance configuration")
		}
		pg, err := pgx.ParseConfig(cfg.DatabaseURL())
		if err != nil {
			return errors.New("invalid database configuration")
		}
		db := sqlx.NewDb(database.OpenDB(*pg), "pgx")
		defer db.Close()
		admin := service.NewInstanceAdmin(repository.NewInstanceAdmin(db), nil, service.InstanceAdminEnvironment{
			WhatsApp: cfg.WhatsApp, SecretKeys: []string{cfg.SessionSecret},
		}, time.Now)
		settings, err = admin.WhatsAppSettings(ctx)
		if err != nil {
			return errors.New("cannot read WhatsApp instance configuration")
		}
	}
	provider := whatsapp.NewCloudAPI(settings)
	var result any
	var err error
	switch args[0] {
	case "list":
		result, err = provider.ListTemplateDocuments(ctx)
	case "get":
		result, err = provider.GetTemplateDocument(ctx, args[1], args[2])
	case "register":
		raw, readErr := io.ReadAll(os.Stdin)
		value, parseErr := dto.ParsePythonJSON(string(raw))
		definition, ok := value.(domain.RequestObject)
		if readErr != nil || parseErr != nil || !ok {
			return errors.New("Expected a JSON template object on stdin")
		}
		result, err = provider.RegisterTemplateDocument(ctx, definition)
	}
	if err != nil {
		return err
	}
	output, err := (dto.AgentJSON{Item: ", ", Key: ": "}).Dumps(result)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(os.Stdout, output)
	return err
}

func main() {
	if err := run(os.Args[1:]); err != nil {
		var usage *usageError
		if errors.As(err, &usage) {
			fmt.Fprintln(os.Stderr, usage)
			os.Exit(2)
		}
		fmt.Fprintln(os.Stderr, "CommandError:", err)
		os.Exit(1)
	}
}
