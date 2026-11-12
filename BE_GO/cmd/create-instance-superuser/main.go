// create-instance-superuser is the Go port of Django's
// create_instance_superuser management command
// (BE/application/management/commands/create_instance_superuser.py): it
// creates a Bakney administrator (staff, superuser, athlete role) after the
// same identity, field and password checks, with the same messages and exit
// codes. Options: --username --email --first-name --last-name --phone
// (each as "--name value" or "--name=value"), --noinput (password from
// DJANGO_SUPERUSER_PASSWORD), --allow-weak-password (development only) and
// --env-file PATH. Django's global management options and argparse prefix
// abbreviations are not accepted.
package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
	"golang.org/x/sys/unix"
)

func main() { os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)) }

type arguments struct {
	options service.InstanceSuperuserOptions
	envFile string
}

func parse(args []string) (arguments, error) {
	var parsed arguments
	values := map[string]**string{"--username": &parsed.options.Username, "--email": &parsed.options.Email,
		"--first-name": &parsed.options.FirstName, "--last-name": &parsed.options.LastName, "--phone": &parsed.options.Phone}
	for index := 0; index < len(args); index++ {
		name, value, inline := strings.Cut(args[index], "=")
		switch {
		case name == "--noinput" && !inline:
			parsed.options.NoInput = true
		case name == "--allow-weak-password" && !inline:
			parsed.options.AllowWeakPassword = true
		case values[name] != nil || name == "--env-file":
			if !inline {
				if index+1 >= len(args) || strings.HasPrefix(args[index+1], "-") && args[index+1] != "-" {
					return parsed, fmt.Errorf("argument %s: expected one argument", name)
				}
				index++
				value = args[index]
			}
			if name == "--env-file" {
				parsed.envFile = value
			} else {
				copied := value
				*values[name] = &copied
			}
		default:
			return parsed, fmt.Errorf("unrecognized arguments: %s", args[index])
		}
	}
	return parsed, nil
}

// colour is Django's OutputWrapper styling: only on a terminal.
func colour(stream *os.File, code, text string) string {
	if os.Getenv("DJANGO_COLORS") == "nocolor" {
		return text
	}
	if _, err := unix.IoctlGetTermios(int(stream.Fd()), getTermios); err != nil {
		return text
	}
	return "\x1b[" + code + "m" + text + "\x1b[0m"
}

func run(args []string, stdin *os.File, stdout, stderr *os.File) int {
	parsed, err := parse(args)
	if err != nil {
		fmt.Fprintln(stderr, "usage: create-instance-superuser [--username USERNAME] [--email EMAIL] [--first-name FIRST_NAME] [--last-name LAST_NAME] [--phone PHONE] [--noinput] [--allow-weak-password] [--env-file PATH]")
		fmt.Fprintln(stderr, "create-instance-superuser: error: "+err.Error())
		return 2
	}
	fail := func(message string) int {
		fmt.Fprintln(stderr, colour(stderr, "31;1", "CommandError: "+message))
		return 1
	}
	cfg, err := config.Load(parsed.envFile)
	if err != nil {
		fmt.Fprintln(stderr, "invalid environment configuration")
		return 1
	}
	parsed.options.DeploymentMode = cfg.InstanceAdmin.DeploymentMode
	parsed.options.EnvironmentPassword = os.Getenv("DJANGO_SUPERUSER_PASSWORD")
	database, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		fmt.Fprintln(stderr, "invalid database configuration")
		return 1
	}
	database.DefaultQueryExecMode = pgx.QueryExecModeExec
	database.ConnectTimeout = 5 * time.Second
	database.RuntimeParams["application_name"] = "assozeta-go-create-instance-superuser"
	db := sqlx.NewDb(ownedpgx.OpenDB(*database), "pgx")
	defer db.Close()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	prompts := &terminalPrompts{reader: bufio.NewReader(stdin), stdout: stdout, stderr: stderr, ctx: ctx}
	users := service.NewInstanceSuperusers(repository.NewInstanceSuperusers(db),
		passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()), time.Now)
	username, err := users.Create(ctx, parsed.options, prompts)
	var command *service.InstanceSuperuserError
	switch {
	case errors.As(err, &command):
		return fail(command.Message)
	case err != nil && ctx.Err() != nil:
		return fail("Account creation cancelled.")
	case err != nil:
		// Database errors may contain private values; Django prints a traceback.
		fmt.Fprintln(stderr, "create-instance-superuser failed")
		return 1
	}
	fmt.Fprintln(stdout, colour(stdout, "32;1", "Administrator "+username+" created. Sign in with the chosen username and password."))
	return 0
}

// terminalPrompts is input() on stdin/stdout and getpass.getpass on
// /dev/tty with echo disabled, falling back to stdin with a warning on
// stderr when there is no controlling terminal.
type terminalPrompts struct {
	reader         *bufio.Reader
	stdout, stderr io.Writer
	ctx            context.Context
}

func readLine(ctx context.Context, reader *bufio.Reader) (string, error) {
	type result struct {
		line string
		err  error
	}
	done := make(chan result, 1)
	go func() {
		line, err := reader.ReadString('\n')
		done <- result{line, err}
	}()
	select {
	case <-ctx.Done():
		return "", service.ErrInstanceSuperuserCancelled
	case read := <-done:
		if read.err != nil && (read.line == "" || !errors.Is(read.err, io.EOF)) {
			if errors.Is(read.err, io.EOF) {
				return "", io.EOF
			}
			return "", read.err
		}
		return strings.TrimSuffix(read.line, "\n"), nil
	}
}

func (p *terminalPrompts) Input(label string) (string, error) {
	fmt.Fprint(p.stdout, label)
	return readLine(p.ctx, p.reader)
}

func (p *terminalPrompts) Password(prompt string) (string, error) {
	tty, err := os.OpenFile("/dev/tty", os.O_RDWR, 0)
	if err != nil {
		fmt.Fprintln(p.stderr, "GetPassWarning: Can not control echo on the terminal.")
		fmt.Fprintln(p.stderr, "Warning: Password input may be echoed.")
		fmt.Fprint(p.stderr, prompt)
		return readLine(p.ctx, p.reader)
	}
	defer tty.Close()
	fd := int(tty.Fd())
	old, err := unix.IoctlGetTermios(fd, getTermios)
	if err != nil {
		return "", err
	}
	hidden := *old
	hidden.Lflag &^= unix.ECHO
	if err = unix.IoctlSetTermios(fd, setTermios, &hidden); err != nil {
		return "", err
	}
	defer func() {
		_ = unix.IoctlSetTermios(fd, setTermios, old)
		fmt.Fprint(tty, "\n")
	}()
	fmt.Fprint(tty, prompt)
	return readLine(p.ctx, bufio.NewReader(tty))
}
