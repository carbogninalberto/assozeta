package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/whatsapp"
)

func run(args []string) error {
	ctx := context.Background()
	provider := whatsapp.NewCloudAPI(config.WhatsAppFromEnv(os.Getenv))
	var result any
	var err error
	switch {
	case len(args) == 1 && args[0] == "list":
		result, err = provider.ListTemplates(ctx)
	case len(args) == 3 && args[0] == "get":
		result, err = provider.GetTemplate(ctx, args[1], args[2])
	case len(args) == 1 && args[0] == "register":
		var definition whatsapp.Object
		if json.NewDecoder(os.Stdin).Decode(&definition) != nil || definition == nil {
			return errors.New("expected a JSON template object on stdin")
		}
		result, err = provider.RegisterTemplate(ctx, definition)
	default:
		return errors.New("usage: whatsapp-templates list | get NAME LANGUAGE | register < definition.json")
	}
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(result)
}
func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
