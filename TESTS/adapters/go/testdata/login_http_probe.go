// Standalone HTTP probe for the shipped Go image without database/cache services.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"reflect"
	"strings"
	"time"
)

func main() {
	client := &http.Client{Timeout: 2 * time.Second}
	deadline := time.Now().Add(15 * time.Second)
	for {
		r, err := client.Get("http://127.0.0.1:8000/healthz")
		if err == nil {
			r.Body.Close()
			if r.StatusCode == 200 {
				break
			}
		}
		if time.Now().After(deadline) {
			panic("shipped server failed to start")
		}
		time.Sleep(100 * time.Millisecond)
	}
	cases := []struct {
		name, method, path, body string
		headers                  map[string]string
		status                   int
		expected                 any
		cookieVary               bool
	}{
		{"health", "GET", "/healthz", "", nil, 200, map[string]any{"status": "ok", "version": "v0.0.0-contract"}, false},
		{"missing credentials", "POST", "/oauth2/login", "{}", map[string]string{"Content-Type": "application/json"}, 400, map[string]any{"msg": "Invalid request."}, true},
		{"method", "GET", "/oauth2/login", "", map[string]string{"Accept-Language": "en"}, 405, map[string]any{"detail": "Method \"GET\" not allowed."}, true},
		{"bad bearer", "POST", "/oauth2/login", "{", map[string]string{"Authorization": "Bearer", "Content-Type": "application/json"}, 401, map[string]any{"detail": "Authorization header must contain two space-delimited values", "code": "bad_authorization_header"}, true},
		{"preflight", "OPTIONS", "/oauth2/login", "{", map[string]string{"Origin": "https://allowed.example.test", "Access-Control-Request-Method": "POST", "Authorization": "Bearer invalid", "Cookie": "sessionid=unavailable-cache"}, 200, "", false},
	}
	for _, tc := range cases {
		request, err := http.NewRequest(tc.method, "http://127.0.0.1:8000"+tc.path, strings.NewReader(tc.body))
		if err != nil {
			panic(err)
		}
		for k, v := range tc.headers {
			request.Header.Set(k, v)
		}
		response, err := client.Do(request)
		if err != nil {
			panic(tc.name + ": request failed")
		}
		data, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
		response.Body.Close()
		if err != nil || response.StatusCode != tc.status {
			panic(tc.name + ": status differs")
		}
		var body any
		if len(data) == 0 {
			body = ""
		} else if json.Unmarshal(data, &body) != nil {
			panic(tc.name + ": invalid response JSON")
		}
		if !reflect.DeepEqual(body, tc.expected) {
			panic(tc.name + ": response differs")
		}
		if response.Header.Get("Set-Cookie") != "" || strings.Contains(strings.ToLower(response.Header.Get("Vary")), "cookie") != tc.cookieVary {
			panic(tc.name + ": session response differs")
		}
		if tc.name == "preflight" && response.Header.Get("Access-Control-Allow-Origin") != "https://allowed.example.test" {
			panic("preflight policy differs")
		}
	}
	fmt.Println(`{"success":true,"http_cases":5,"scope":"shipped root without PostgreSQL/Redis; no successful-login parity claim"}`)
}
