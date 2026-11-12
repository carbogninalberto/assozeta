// manuale ports the manual management commands that run outside the Django
// manual build:
//
//	manuale sync-corpus [--env-file PATH]
//	    sync_manuale_corpus: download the published corpus for the configured
//	    revision/release (MANUAL_CORPUS_BASE_URL, MANUAL_APPLICATION_REVISION or
//	    CODE_REVISION, RUNNING_VERSION or VERSION, MANUAL_INDEX_PATH,
//	    MANUAL_SOURCE_ROOT, ASSOZETA_MANUAL_RUN_ID), install its screenshots and
//	    atomically promote it under the shared .sync.lock.
//	manuale compare-captures --run RUN --output OUTPUT
//	    compare_manuale_captures: measure screenshot drift between
//	    RUN/capture-baseline and RUN and write the variance report.
//	manuale evaluate --association-id ID --output OUTPUT [--query QUERY] [--env-file PATH] [--mcp-server PATH]
//	    evaluate_manuale: check the manual tools through the Go MCP server
//	    (default: the mcp-server binary next to this one) and the Go agent
//	    without a model, then write the evaluation report (evaluate.go).
//
// Option values may be given as "--name value" or "--name=value". Django's
// global management options, argparse prefix abbreviations and -h are not
// accepted. Failures Django reports as CommandError print the same
// "CommandError: ..." line with exit 1; failures Django reports with a
// traceback print one line naming the command and exit 1.
package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"regexp"
	"strings"
	"syscall"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
)

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

const usage = "usage: manuale {sync-corpus [--env-file PATH] | compare-captures --run RUN --output OUTPUT | evaluate --association-id ID --output OUTPUT [--query QUERY]}"

// argparse treats a value that looks like a negative number as a value.
var negativeNumber = regexp.MustCompile(`^-\d+$|^-\d*\.\d+$`)

// parse reproduces the argparse messages for the command's own options.
func parse(args []string, names []string, required []string) (map[string]string, error) {
	values := map[string]string{}
	extras := []string{}
	known := map[string]bool{}
	for _, name := range names {
		known[name] = true
	}
	for index := 0; index < len(args); index++ {
		name, value, inline := strings.Cut(args[index], "=")
		if !strings.HasPrefix(args[index], "--") || !known[name] {
			extras = append(extras, args[index])
			continue
		}
		if !inline {
			if index+1 >= len(args) || strings.HasPrefix(args[index+1], "-") && !negativeNumber.MatchString(args[index+1]) {
				return nil, fmt.Errorf("argument %s: expected one argument", name)
			}
			index++
			value = args[index]
		}
		values[name] = value
	}
	missing := []string{}
	for _, name := range required {
		if _, ok := values[name]; !ok {
			missing = append(missing, name)
		}
	}
	if len(missing) > 0 {
		return nil, errors.New("the following arguments are required: " + strings.Join(missing, ", "))
	}
	if len(extras) > 0 {
		return nil, errors.New("unrecognized arguments: " + strings.Join(extras, " "))
	}
	return values, nil
}

func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, usage)
		return 2
	}
	command, args := args[0], args[1:]
	switch command {
	case "sync-corpus":
		values, err := parse(args, []string{"--env-file"}, nil)
		if err != nil {
			fmt.Fprintln(stderr, usage)
			fmt.Fprintln(stderr, "manuale sync-corpus: error: "+err.Error())
			return 2
		}
		return syncCorpus(values["--env-file"], stdout, stderr)
	case "compare-captures":
		values, err := parse(args, []string{"--run", "--output"}, []string{"--run", "--output"})
		if err != nil {
			fmt.Fprintln(stderr, usage)
			fmt.Fprintln(stderr, "manuale compare-captures: error: "+err.Error())
			return 2
		}
		return compareCaptures(values["--run"], values["--output"], stdout, stderr)
	case "evaluate":
		return evaluateMain(args, stdout, stderr)
	}
	fmt.Fprintln(stderr, usage)
	return 2
}

func syncCorpus(envFile string, stdout, stderr io.Writer) int {
	cfg, err := config.Load(envFile)
	if err != nil {
		fmt.Fprintln(stderr, "invalid environment configuration")
		return 1
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	identity, err := manual.New(cfg.Manual).Synchronize(ctx)
	if err != nil {
		// Django maps every download, validation and filesystem failure to one message.
		fmt.Fprintln(stderr, "CommandError: Published corpus unavailable or incompatible; previous index retained.")
		return 1
	}
	fmt.Fprintln(stdout, "Published manual corpus updated: "+identity)
	return 0
}

func compareCaptures(run, output string, stdout, stderr io.Writer) int {
	count, err := manual.CompareCaptures(run, output)
	var commandError manual.CaptureCommandError
	switch {
	case errors.As(err, &commandError):
		fmt.Fprintln(stderr, "CommandError: "+commandError.Message)
		return 1
	case errors.Is(err, manual.ErrCaptureImageUnsupported):
		fmt.Fprintln(stderr, "compare-captures failed: "+err.Error())
		return 1
	case err != nil:
		// Django ends with a traceback; paths and report values are not echoed.
		fmt.Fprintln(stderr, "compare-captures failed")
		return 1
	}
	fmt.Fprintf(stdout, "Capture variance measured for %d checkpoints.\n", count)
	return 0
}
