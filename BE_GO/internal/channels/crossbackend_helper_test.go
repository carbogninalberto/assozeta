package channels

// Cross-backend WebSocket harness entry point.
//
// TESTS/test_ws_cross_backend.py starts a Django Channels server and a Go
// WebSocket server on the same private Redis, then needs to publish a group
// message from Go without a business endpoint. This test is that tiny helper:
// it is skipped in every normal `go test` run and only acts when the harness
// sets WS_CROSS_BACKEND_GROUP_SEND=1 with the group, message file and private
// Redis connection in the environment.

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/redis/go-redis/v9"
)

func TestCrossBackendGroupSend(t *testing.T) {
	if os.Getenv("WS_CROSS_BACKEND_GROUP_SEND") != "1" {
		t.Skip("cross-backend group-send helper; driven by TESTS/test_ws_cross_backend.py")
	}
	group := os.Getenv("WS_CROSS_BACKEND_GROUP")
	messagePath := os.Getenv("WS_CROSS_BACKEND_MESSAGE_FILE")
	port := os.Getenv("REDIS_PORT")
	password := os.Getenv("REDIS_PASSWORD")
	if group == "" || messagePath == "" || port == "" {
		t.Fatal("WS_CROSS_BACKEND_GROUP, WS_CROSS_BACKEND_MESSAGE_FILE and REDIS_PORT are required")
	}
	raw, err := os.ReadFile(messagePath)
	if err != nil {
		t.Fatal(err)
	}
	message, err := crossBackendMessage(string(raw))
	if err != nil {
		t.Fatal(err)
	}
	db := 0
	if value := os.Getenv("WS_CROSS_BACKEND_REDIS_DB"); value != "" {
		db, err = strconv.Atoi(value)
		if err != nil {
			t.Fatalf("WS_CROSS_BACKEND_REDIS_DB: %v", err)
		}
	}
	client := redis.NewClient(&redis.Options{
		Addr:        "127.0.0.1:" + port,
		Password:    password,
		DB:          db,
		MaxRetries:  -1,
		DialTimeout: 2 * time.Second,
		ReadTimeout: 2 * time.Second, WriteTimeout: 2 * time.Second,
		ContextTimeoutEnabled: true,
	})
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := NewLayer(client, nil, nil, nil).GroupSend(ctx, group, message); err != nil {
		t.Fatal(err)
	}
	fmt.Fprintf(os.Stdout, "group-send %s\n", group)
}

// crossBackendMessage is Python json.loads followed by Dict(message), keeping
// the authored key order so the consumer renders the same field order in Go
// and Django.
func crossBackendMessage(text string) (Dict, error) {
	value, err := dto.ParsePythonJSON(text)
	if err != nil {
		return nil, err
	}
	converted, ok := crossBackendValue(value).(Dict)
	if !ok {
		return nil, fmt.Errorf("message must be a JSON object")
	}
	return converted, nil
}

func crossBackendValue(value any) any {
	switch v := value.(type) {
	case domain.RequestObject:
		dict := make(Dict, 0, len(v))
		for _, field := range v {
			dict = append(dict, Item{Key: field.Name, Value: crossBackendValue(field.Value)})
		}
		return dict
	case []any:
		list := make([]any, len(v))
		for index, item := range v {
			list[index] = crossBackendValue(item)
		}
		return list
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			if number, err := strconv.ParseInt(text, 10, 64); err == nil {
				return number
			}
			if number, err := strconv.ParseUint(text, 10, 64); err == nil {
				return number
			}
		}
		return v
	default:
		return v
	}
}
