// migrate-subscription-signatures-to-s3 is the Go port of Django's
// migrate_subscription_signatures_to_s3 management command
// (BE/application/management/commands/migrate_subscription_signatures_to_s3.py):
// it moves values of the legacy application_subscription.signature column
// (kept by databases created before migration 0415) to
// signature_storage_key/signature_url, uploading base64 images to the
// configured storage. Options: --dry-run, --batch-size N (default 100), as
// "--name value" or "--name=value" or unique abbreviations; Django's
// --no-color, --force-color, --traceback, --skip-checks and --verbosity are
// accepted, --settings and --pythonpath are not. See
// docs/architecture/go-migration/subscription-signature-migration.md.
package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math/big"
	"os"
	"os/signal"
	"regexp"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3credentials"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3trust"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
	"golang.org/x/sys/unix"
)

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

const usage = "usage: manage.py migrate_subscription_signatures_to_s3 [-h] [--dry-run] [--batch-size BATCH_SIZE] [--version] [-v {0,1,2,3}] [--settings SETTINGS] [--pythonpath PYTHONPATH] [--traceback] [--no-color] [--force-color] [--skip-checks]"

type arguments struct {
	dryRun     bool
	batchSize  int64
	noColor    bool
	forceColor bool
}

// Django's parser: the command's options and BaseCommand's.
var optionNames = []string{"--dry-run", "--batch-size", "--help", "--version", "--verbosity", "--settings", "--pythonpath",
	"--traceback", "--no-color", "--force-color", "--skip-checks"}

var negativeNumber = regexp.MustCompile(`^-\d+$|^-\d*\.\d+$`)
var pythonInteger = regexp.MustCompile(`^[+-]?\d+(_\d+)*$`)

// resolve is argparse's long-option lookup with allow_abbrev.
func resolve(name string) (string, error) {
	var found []string
	for _, option := range optionNames {
		if option == name {
			return option, nil
		}
		if strings.HasPrefix(option, name) {
			found = append(found, option)
		}
	}
	if len(found) == 1 && len(name) > 2 {
		return found[0], nil
	}
	if len(found) > 1 && len(name) > 2 {
		return "", fmt.Errorf("ambiguous option: %s could match %s", name, strings.Join(found, ", "))
	}
	return "", fmt.Errorf("unrecognized arguments: %s", name)
}

// pythonInt is argparse's type=int: int(text) for ASCII input, within int64.
func pythonInt(text string) (int64, error) {
	trimmed := strings.Trim(text, " \t\n\r\v\f")
	if !pythonInteger.MatchString(trimmed) {
		return 0, fmt.Errorf("argument --batch-size: invalid int value: '%s'", text)
	}
	value, ok := new(big.Int).SetString(strings.ReplaceAll(strings.TrimPrefix(trimmed, "+"), "_", ""), 10)
	if !ok || !value.IsInt64() {
		return 0, errors.New("argument --batch-size: value outside the supported range")
	}
	return value.Int64(), nil
}

func parse(args []string) (arguments, error) {
	parsed := arguments{batchSize: 100}
	for index := 0; index < len(args); index++ {
		argument := args[index]
		if argument == "-v" {
			argument = "--verbosity"
		} else if strings.HasPrefix(argument, "-v") {
			argument = "--verbosity=" + strings.TrimPrefix(strings.TrimPrefix(argument, "-v"), "=")
		}
		if !strings.HasPrefix(argument, "--") {
			return parsed, fmt.Errorf("unrecognized arguments: %s", args[index])
		}
		name, value, inline := strings.Cut(argument, "=")
		option, err := resolve(name)
		if err != nil {
			return parsed, err
		}
		takesValue := option == "--batch-size" || option == "--verbosity" || option == "--settings" || option == "--pythonpath"
		if !takesValue && inline {
			return parsed, fmt.Errorf("argument %s: ignored explicit argument '%s'", option, value)
		}
		if takesValue && !inline {
			if index+1 >= len(args) || strings.HasPrefix(args[index+1], "-") && !negativeNumber.MatchString(args[index+1]) {
				return parsed, fmt.Errorf("argument %s: expected one argument", option)
			}
			index++
			value = args[index]
		}
		switch option {
		case "--dry-run":
			parsed.dryRun = true
		case "--batch-size":
			if parsed.batchSize, err = pythonInt(value); err != nil {
				return parsed, err
			}
		case "--verbosity":
			if value != "0" && value != "1" && value != "2" && value != "3" {
				return parsed, fmt.Errorf("argument -v/--verbosity: invalid choice: '%s'", value)
			}
		case "--no-color":
			parsed.noColor = true
		case "--force-color":
			parsed.forceColor = true
		case "--traceback", "--skip-checks":
		default:
			// --help, --version, --settings and --pythonpath are not ported.
			return parsed, fmt.Errorf("option %s is not supported by the Go command", option)
		}
	}
	return parsed, nil
}

// output is BaseCommand.stdout: Django's style only on a terminal (or
// --force-color), never with --no-color or DJANGO_COLORS=nocolor.
type output struct {
	writer io.Writer
	colour bool
}

// line is OutputWrapper.write: the (already styled) text, then a newline unless it ends with one.
func (o output) line(text string) {
	if !strings.HasSuffix(text, "\n") {
		text += "\n"
	}
	fmt.Fprint(o.writer, text)
}

func (o output) styled(code, text string) {
	if o.colour {
		text = "\x1b[" + code + "m" + text + "\x1b[0m"
	}
	o.line(text)
}

func (o output) Write(text string)   { o.line(text) }
func (o output) Success(text string) { o.styled("32;1", text) }
func (o output) Warning(text string) { o.styled("33;1", text) }
func (o output) Error(text string)   { o.styled("31;1", text) }

func terminal(stream *os.File) bool {
	_, err := unix.IoctlGetTermios(int(stream.Fd()), getTermios)
	return err == nil
}

// uploadObjects is S3Storage._save's write parameters on the document storage.
type uploadObjects struct {
	*documentobjects.S3
	cacheControl, acl string
}

func (o uploadObjects) Put(ctx context.Context, key string, body []byte, contentType, contentEncoding string) error {
	return o.S3.Put(ctx, key, body, documentobjects.Upload{ContentType: contentType, ContentEncoding: contentEncoding,
		CacheControl: o.cacheControl, ACL: o.acl})
}

func composeObjects(cfg config.Config) uploadObjects {
	settings := cfg.Storage
	optional := func(value *string) string {
		if value == nil {
			return ""
		}
		return *value
	}
	objects := uploadObjects{S3: documentobjects.New(documentobjects.Options{Endpoint: settings.Endpoint, Region: settings.Region,
		Bucket: settings.Bucket, Location: settings.Location,
		Trust: func(ctx context.Context) (s3trust.Policy, error) {
			return s3credentials.LoadTrust(ctx, optional(settings.SessionProfile))
		},
		Credentials: func(ctx context.Context) (aws.CredentialsProvider, error) {
			if err := settings.ValidateBackend(); err != nil {
				return nil, err
			}
			return s3credentials.New(ctx, s3credentials.Options{AccessKey: settings.AccessKey, SecretKey: settings.SecretKey,
				SessionToken: optional(settings.SessionToken), Profile: optional(settings.SessionProfile)}, nil)
		}}), cacheControl: settings.CacheControl}
	if settings.DefaultACL != nil {
		objects.acl = *settings.DefaultACL
	}
	return objects
}

func run(args []string, stdout, stderr *os.File) int {
	parsed, err := parse(args)
	if err != nil {
		fmt.Fprintln(stderr, usage)
		fmt.Fprintln(stderr, "manage.py migrate_subscription_signatures_to_s3: error: "+err.Error())
		return 2
	}
	if parsed.noColor && parsed.forceColor {
		fmt.Fprintln(stderr, "CommandError: The --no-color and --force-color options can't be used together.")
		return 1
	}
	cfg, err := config.Load("")
	if err != nil {
		fmt.Fprintln(stderr, "invalid environment configuration")
		return 1
	}
	database, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		fmt.Fprintln(stderr, "invalid database configuration")
		return 1
	}
	database.DefaultQueryExecMode = pgx.QueryExecModeExec
	database.ConnectTimeout = 5 * time.Second
	database.RuntimeParams["application_name"] = "assozeta-go-migrate-subscription-signatures"
	db := sqlx.NewDb(ownedpgx.OpenDB(*database), "pgx")
	defer db.Close()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	colour := parsed.forceColor || !parsed.noColor && os.Getenv("DJANGO_COLORS") != "nocolor" && terminal(stdout)
	migrations := service.NewSubscriptionSignatureMigrations(repository.NewSubscriptionSignatureMigrations(db), composeObjects(cfg),
		service.SubscriptionSignSettings{UseObjectACL: cfg.Storage.UseObjectACL, PublicBaseURL: cfg.Storage.PublicBaseURL}, time.Now, nil)
	err = migrations.Run(ctx, service.SubscriptionSignatureMigrationOptions{DryRun: parsed.dryRun, BatchSize: parsed.batchSize},
		output{writer: stdout, colour: colour})
	var fatal *service.SubscriptionSignatureMigrationFatal
	switch {
	case err == nil:
		return 0
	case errors.As(err, &fatal):
		fmt.Fprintln(stderr, fatal.Message)
	case errors.Is(err, service.ErrSubscriptionSignatureMigrationNotPorted):
		fmt.Fprintln(stderr, "migrate-subscription-signatures-to-s3: refused: a generated storage key already exists (not ported)")
	default:
		// Database and storage errors may contain private values; Django prints a traceback.
		fmt.Fprintln(stderr, "migrate-subscription-signatures-to-s3 failed")
	}
	return 1
}
