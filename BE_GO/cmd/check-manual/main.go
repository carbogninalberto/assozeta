// check-manual validates the image-owned manual without application credentials.
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
)

func run(args []string, output io.Writer) int {
	flags := flag.NewFlagSet("check-manual", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	root := flags.String("root", "", "bundle directory")
	revision := flags.String("revision", "", "application revision")
	release := flags.String("release", "", "release version")
	required := flags.String("required", "false", "true or false")
	if err := flags.Parse(args); err != nil {
		if err == flag.ErrHelp {
			fmt.Fprintln(output, "check-manual --root PATH --revision REVISION --release VERSION [--required true|false]")
			return 0
		}
		fmt.Fprintln(output, "invalid manual check arguments")
		return 2
	}
	seen := map[string]bool{}
	flags.Visit(func(f *flag.Flag) { seen[f.Name] = true })
	if !seen["root"] || !seen["revision"] || !seen["release"] || flags.NArg() != 0 || (*required != "true" && *required != "false") {
		fmt.Fprintln(output, "invalid manual check arguments")
		return 2
	}
	settings := manual.Settings{BundlePath: *root, BundleRequired: *required == "true", ApplicationRevision: *revision, Release: *release}
	if err := manual.CheckBundle(context.Background(), settings); err != nil {
		fmt.Fprintln(output, "manual bundle validation failed")
		return 1
	}
	return 0
}
func main() { os.Exit(run(os.Args[1:], os.Stderr)) }
