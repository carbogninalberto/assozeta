package service

import (
	"context"
	"errors"
	"math"
	"net"
	"net/url"
	"strings"
	"sync"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
)

// DiagnosticsView.post: claim_operation('diagnostics', 45), the ten probes of
// diagnostics.CHECKS through probe_runner.run_probe (Django runs each one in
// a `python -m instance.diagnostic_probe` subprocess, four at a time, with a
// 10 s deadline), then the locked save and snapshot(config). Go runs the
// same steps in-process: four goroutines at a time in CHECKS order, each
// probe under its own 10 s deadline (the timeout answer when it expires),
// any failure inside a probe answered with diagnostic_probe.main()'s generic
// result, and every result reduced to run_probe's sanitized
// {status, level, message, checked_at}.

// InstanceDiagnosticsProbes are the connections the probes use.
type InstanceDiagnosticsProbes interface {
	// Database is SELECT 1 on the default connection.
	Database(ctx context.Context) error
	// Health is health_response(url, expected_version).
	Health(ctx context.Context, target string, expected *string) (bool, error)
	// Storage is the S3 list_objects_v2 check.
	Storage(ctx context.Context) error
	// Ping is app.control.inspect(timeout=2).ping(): the reply bodies.
	Ping(ctx context.Context) ([]any, error)
	// Heartbeat is the raw Django cache value of 'instance:scheduler:heartbeat'.
	Heartbeat(ctx context.Context) ([]byte, bool, error)
}

// InstanceDiagnosticsSettings are the addresses the probes read.
type InstanceDiagnosticsSettings struct {
	// APIURL is the API the 'api' probe checks (Django: its own process at
	// http://127.0.0.1:8000; Go: this server, see composeInstanceOutbound).
	APIURL string
	// RendererHost and RendererPort are PUPPETEER_HOST and PUPPETEER_PORT.
	RendererHost, RendererPort string
}

// WithDiagnostics enables DiagnosticsView.post.
func (s *InstanceOutbound) WithDiagnostics(probes InstanceDiagnosticsProbes, settings InstanceDiagnosticsSettings) *InstanceOutbound {
	copied := *s
	copied.diagnostics, copied.diagnosticSettings = probes, settings
	return &copied
}

const (
	diagnosticsConcurrency = 4
	// probeRunnerFailure is run_probe's answer when the subprocess output is
	// not a valid result.
	probeRunnerFailure = "Verifica non riuscita. Controlla il servizio e riprova."
)

var diagnosticsBusy = &InstanceAdminResponse{Status: 429, RetryAfter: "45",
	Body: map[string]any{"error": "Diagnostica già in corso o appena terminata. Attendi fino a 45 secondi e riprova."}}

// RunDiagnostics is DiagnosticsView.post.
func (s *InstanceOutbound) RunDiagnostics(ctx context.Context) (map[string]any, error) {
	if s.diagnostics == nil {
		return nil, refused("diagnostic probes")
	}
	if s.admin.production() && s.admin.runner == nil {
		return nil, refused("update runner client")
	}
	// Stored shapes snapshot(config) does not interpret are refused before the lease.
	if _, err := s.admin.Diagnostics(ctx); err != nil {
		return nil, err
	}
	claimed, err := s.claim(ctx, "diagnostics", 45)
	if err != nil {
		return nil, err
	}
	if !claimed {
		return nil, diagnosticsBusy
	}
	// The view runs to its save even when the client goes away.
	ctx = context.WithoutCancel(ctx)
	row, err := s.admin.row(ctx)
	if err != nil {
		return nil, err
	}
	revision := row.EmailRevision
	results := make([]map[string]any, len(instanceAdminChecks))
	slots := make(chan struct{}, diagnosticsConcurrency)
	var wait sync.WaitGroup
	for index, check := range instanceAdminChecks {
		slots <- struct{}{}
		wait.Add(1)
		go func() {
			defer wait.Done()
			defer func() { <-slots }()
			results[index] = s.probeResult(s.runDiagnosticProbe(ctx, check.id))
		}()
	}
	wait.Wait()
	checks := make([]any, 0, len(results))
	for index, check := range instanceAdminChecks {
		value := results[index]
		value["id"], value["label"], value["core"], value["section"] = check.id, check.label, check.core, check.section
		checks = append(checks, value)
	}
	err = s.admin.store.Locked(ctx, func(locked *domain.InstanceAdminRow) (*domain.InstanceAdminUpdate, error) {
		if locked == nil {
			return nil, refused("no configuration")
		}
		saved := checks
		if locked.EmailRevision != revision {
			saved = make([]any, 0, len(checks))
			for _, check := range checks {
				if check.(map[string]any)["id"] != "email" {
					saved = append(saved, check)
				}
			}
		}
		stored, err := s.storedResults(locked)
		if err != nil {
			return nil, err
		}
		stored["checks"], stored["checked_at"] = saved, pythonISOFormat(s.now())
		raw, err := instanceAdminMarshal(stored)
		if err != nil {
			return nil, err
		}
		return &domain.InstanceAdminUpdate{DiagnosticResults: raw}, nil
	})
	if err != nil {
		return nil, err
	}
	return s.admin.Diagnostics(ctx)
}

// runDiagnosticProbe is run_probe(name) without checked_at: the probe under
// its 10 s deadline, its sanitized answer.
func (s *InstanceOutbound) runDiagnosticProbe(parent context.Context, name string) instanceprobe.Result {
	ctx, cancel := context.WithTimeout(parent, probeDeadline)
	defer cancel()
	answer := make(chan instanceprobe.Result, 1)
	go func() {
		defer func() {
			if recover() != nil {
				answer <- instanceprobe.Generic
			}
		}()
		result, err := s.diagnosticProbe(ctx, name)
		switch {
		case err != nil:
			answer <- instanceprobe.Generic
		case !sanitizedResult(result):
			answer <- instanceprobe.Result{Status: "failed", Level: "connectivity", Message: probeRunnerFailure}
		default:
			answer <- result
		}
	}()
	select {
	case result := <-answer:
		if ctx.Err() != nil {
			// The subprocess was killed at the deadline before it printed.
			return timeoutResult(false)
		}
		return result
	case <-ctx.Done():
		return timeoutResult(false)
	}
}

// sanitizedResult is run_probe's acceptance of the probe's JSON line.
func sanitizedResult(result instanceprobe.Result) bool {
	switch result.Status {
	case "passed", "warning", "failed", "not_configured", "not_applicable", "not_checked":
	default:
		return false
	}
	switch result.Level {
	case "configuration", "connectivity", "functional":
	default:
		return false
	}
	return utf8.ValidString(result.Message) && !instanceprobe.HasSurrogate(result.Message) && utf8.RuneCountInString(result.Message) <= 700
}

func diagnosticResult(status, message, level string) (instanceprobe.Result, error) {
	return instanceprobe.Result{Status: status, Level: level, Message: message}, nil
}

var errDiagnosticProbe = errors.New("diagnostic probe raised")

// diagnosticProbe is diagnostic_probe.probe(name, {}) for the CHECKS names;
// an error is an exception inside it (main()'s generic answer).
func (s *InstanceOutbound) diagnosticProbe(ctx context.Context, name string) (instanceprobe.Result, error) {
	probes, settings, env := s.diagnostics, s.diagnosticSettings, s.admin.env
	switch name {
	case "database":
		if err := probes.Database(ctx); err != nil {
			return instanceprobe.Result{}, err
		}
		return diagnosticResult("passed", "Il database risponde a una query di lettura. Nessun dato è stato modificato.", "connectivity")
	case "public_url", "api":
		target := env.AppURL
		if name == "api" {
			target = settings.APIURL
		}
		parsed, valid := diagnosticURL(target)
		if !valid {
			return diagnosticResult("failed", "Configura un URL pubblico valido in APP_URL.", "configuration")
		}
		path := "/api/healthz"
		if name == "api" {
			path = "/healthz"
		}
		version := env.Version
		healthy, err := probes.Health(ctx, strings.TrimRight(target, "/")+path, &version)
		if err != nil {
			return instanceprobe.Result{}, err
		}
		if !healthy {
			return diagnosticResult("failed", "L’endpoint di salute non risponde con la versione attesa. Verifica URL, rete e reverse proxy.", "connectivity")
		}
		if name == "public_url" && parsed.Scheme != "https" {
			return diagnosticResult("warning", "L’URL risponde, ma HTTPS non è attivo. Configura dominio e certificato prima dell’uso pubblico.", "connectivity")
		}
		if name == "public_url" {
			return diagnosticResult("passed", "URL pubblico raggiungibile con certificato HTTPS valido.", "connectivity")
		}
		return diagnosticResult("passed", "L’API risponde con la versione in esecuzione.", "connectivity")
	case "storage":
		if err := probes.Storage(ctx); err != nil {
			return instanceprobe.Result{}, err
		}
		return diagnosticResult("passed", "Archivio accessibile in lettura. Scrittura e caricamento file non sono stati provati.", "connectivity")
	case "worker":
		replies, err := probes.Ping(ctx)
		if err != nil {
			return instanceprobe.Result{}, err
		}
		pong, err := diagnosticsPong(replies)
		if err != nil {
			return instanceprobe.Result{}, err
		}
		if pong {
			return diagnosticResult("passed", "Un worker risponde tramite il broker. Non sono stati eseguiti task applicativi.", "connectivity")
		}
		return diagnosticResult("failed", "Nessun worker risponde. Verifica worker e broker e riprova.", "connectivity")
	case "scheduler":
		raw, found, err := probes.Heartbeat(ctx)
		if err != nil {
			return instanceprobe.Result{}, err
		}
		fresh := false
		if found {
			seconds, number, err := djangoCacheNumber(raw)
			if err != nil {
				return instanceprobe.Result{}, err
			}
			if number {
				age := float64(s.now().UnixNano())/1e9 - seconds
				fresh = age >= 0 && age <= 90
			}
		}
		if fresh {
			return diagnosticResult("passed", "Il ciclo dello scheduler è attivo (segnale negli ultimi 90 secondi). L’esito dei task non è verificato.", "functional")
		}
		return diagnosticResult("failed", "Segnale dello scheduler assente o scaduto. Avvia o riavvia il servizio beat e ripeti dopo un minuto.", "connectivity")
	case "renderer":
		healthy, err := probes.Health(ctx, "http://"+settings.RendererHost+":"+settings.RendererPort+"/healthz", nil)
		if err != nil {
			return instanceprobe.Result{}, err
		}
		if healthy {
			return diagnosticResult("passed", "Il renderer apre e chiude una pagina nel browser. La generazione di un documento PDF non è stata provata.", "connectivity")
		}
		return diagnosticResult("failed", "Il renderer non supera il controllo di salute. Verifica il servizio PDF.", "connectivity")
	case "email":
		return s.emailDiagnostic(ctx)
	case "updater", "backups":
		return s.runnerDiagnostic(ctx, name)
	}
	return instanceprobe.Result{}, errDiagnosticProbe
}

// diagnosticURL is urlsplit(url)'s checks: an http(s) scheme, a host name
// and no user information.
func diagnosticURL(target string) (*url.URL, bool) {
	scheme, rest, found := strings.Cut(target, ":")
	if !found || !strings.HasPrefix(rest, "//") {
		return nil, false
	}
	scheme = strings.ToLower(scheme)
	if scheme != "http" && scheme != "https" {
		return nil, false
	}
	authority := rest[2:]
	if end := strings.IndexAny(authority, "/?#"); end >= 0 {
		authority = authority[:end]
	}
	if strings.Contains(authority, "@") {
		return nil, false
	}
	host := authority
	if strings.HasPrefix(host, "[") {
		end := strings.Index(host, "]")
		if end < 0 {
			return nil, false
		}
		host = host[1:end]
	} else if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	} else if i := strings.LastIndex(host, ":"); i >= 0 {
		host = host[:i]
	}
	if host == "" {
		return nil, false
	}
	return &url.URL{Scheme: scheme, Host: authority}, true
}

// diagnosticsPong is `replies and any(reply.get('ok') == 'pong' for reply in
// replies.values())` on inspect()._prepare(replies): the reply bodies are
// merged in order (flatten_reply, a later node replaces an earlier one in
// place); a body or node answer that is not a dict raises.
func diagnosticsPong(replies []any) (bool, error) {
	var nodes []diagnosticsItem
	for _, reply := range replies {
		items, ok := celeryDict(reply)
		if !ok {
			return false, errDiagnosticProbe
		}
		for _, item := range items {
			replaced := false
			for i := range nodes {
				if nodes[i].name == item.name {
					nodes[i].value, replaced = item.value, true
				}
			}
			if !replaced {
				nodes = append(nodes, item)
			}
		}
	}
	for _, n := range nodes {
		answer, ok := celeryDict(n.value)
		if !ok {
			return false, errDiagnosticProbe
		}
		for _, item := range answer {
			if item.name == "ok" {
				if text, ok := item.value.(string); ok && text == "pong" {
					return true, nil
				}
			}
		}
	}
	return false, nil
}

// djangoCacheNumber is django_redis's decode of the heartbeat value, then
// isinstance(value, (float, int)): the plain integer form, or a pickled
// float, int or bool (a pickled value of another supported type is not a
// number; anything else this port cannot read is an error, the probe's
// generic answer).
func djangoCacheNumber(raw []byte) (float64, bool, error) {
	if number, ok := pythonIntBytes(raw); ok {
		return number, true, nil
	}
	return unpickleNumber(raw)
}

func (s *InstanceOutbound) emailDiagnostic(ctx context.Context) (instanceprobe.Result, error) {
	row, err := s.admin.row(ctx)
	if err != nil {
		return instanceprobe.Result{}, err
	}
	value, err := s.effectiveEmail(row)
	if err != nil {
		return instanceprobe.Result{}, err
	}
	field := func(key string) (any, bool) {
		item, present := value[key]
		return item, present // a missing key is a KeyError
	}
	host, ok := field("host")
	if !ok {
		return instanceprobe.Result{}, errDiagnosticProbe
	}
	if instanceTruthy(host) {
		from, ok := field("from_email")
		if !ok {
			return instanceprobe.Result{}, errDiagnosticProbe
		}
		host = from
	}
	if !instanceTruthy(host) {
		return diagnosticResult("not_configured", "Configura server SMTP e indirizzo mittente nella sezione Email.", "configuration")
	}
	security, ok := field("security")
	if !ok {
		return instanceprobe.Result{}, errDiagnosticProbe
	}
	if pythonEqual(security, "invalid") {
		return diagnosticResult("failed", "TLS e SSL sono entrambi attivi nell’ambiente. Scegli una sola modalità nella configurazione email.", "configuration")
	}
	if pythonEqual(security, "none") { // name == 'email'
		return diagnosticResult("warning", "SMTP senza cifratura. Configura TLS o SSL prima di inviare credenziali o messaggi.", "configuration")
	}
	username, ok := field("username")
	if !ok {
		return instanceprobe.Result{}, errDiagnosticProbe
	}
	password, ok := field("password")
	if !ok {
		return instanceprobe.Result{}, errDiagnosticProbe
	}
	if instanceTruthy(username) != instanceTruthy(password) {
		return diagnosticResult("warning", "Nome utente e password SMTP incompleti. Completa entrambe le credenziali oppure rimuovile per un relay senza autenticazione.", "configuration")
	}
	return diagnosticResult("passed", "Campi SMTP e mittente presenti. Connessione e invio non verificati: usa i test espliciti nella sezione Email.", "configuration")
}

// effectiveEmail is email_configuration.effective_email(config) with the
// decrypted password; any failure is an exception of the probe.
func (s *InstanceOutbound) effectiveEmail(row *domain.InstanceAdminRow) (map[string]any, error) {
	settings, instance, err := emailSettings(row)
	if err != nil {
		return nil, errDiagnosticProbe // {**email_settings} of a non-dict raises
	}
	if !instance {
		return s.admin.environmentEmail(row.EmailRevision)
	}
	plain, err := s.admin.decrypt(instanceEmailCipher, row.EmailPassword)
	if err != nil {
		return nil, errDiagnosticProbe // ImproperlyConfigured
	}
	value := instanceAdminCopy(settings)
	value["password"], value["source"], value["revision"] = plain, "instance", row.EmailRevision
	return value, nil
}

// runnerDiagnostic is the updater and backups probes.
func (s *InstanceOutbound) runnerDiagnostic(ctx context.Context, name string) (instanceprobe.Result, error) {
	if !s.admin.production() {
		return diagnosticResult("not_applicable", "Disponibile nelle installazioni self-hosted in modalità produzione.", "configuration")
	}
	path := "/status"
	if name == "backups" {
		path = "/diagnostics"
	}
	reply, err := s.admin.runner.Call(ctx, "GET", path)
	if err != nil {
		return instanceprobe.Result{}, err
	}
	value, err := instanceprobe.LoadJSON(reply.Body)
	if err != nil {
		return instanceprobe.Result{}, err // UpdaterUnavailable
	}
	body, isObject := value.(map[string]any)
	if reply.Status >= 400 || !isObject {
		return instanceprobe.Result{}, errDiagnosticProbe // UpdaterRejected, or .get/[] on a non-dict
	}
	if name == "backups" {
		backup, present := body["backup"]
		if !present {
			return instanceprobe.Result{}, errDiagnosticProbe // KeyError
		}
		// The probe prints value['backup']; run_probe keeps a valid result's three keys.
		candidate, ok := backup.(map[string]any)
		if !ok {
			return instanceprobe.Result{Status: "-"}, nil
		}
		status, _ := candidate["status"].(string)
		level, _ := candidate["level"].(string)
		message, isText := candidate["message"].(string)
		if !isText {
			return instanceprobe.Result{Status: "-"}, nil
		}
		return instanceprobe.Result{Status: status, Level: level, Message: message}, nil
	}
	if instanceprobe.Truthy(body["available"]) && diagnosticsEqualOne(body["protocol"]) {
		if canUpdate, ok := body["can_update"].(bool); ok && !canUpdate {
			return diagnosticResult("warning", "Il servizio risponde, ma richiede un ripristino. Consulta Aggiornamenti e backup.", "connectivity")
		}
		return diagnosticResult("passed", "Il servizio di aggiornamento risponde con un protocollo compatibile. Le immagini di una release non sono state scaricate.", "connectivity")
	}
	return diagnosticResult("failed", "Servizio di aggiornamento non disponibile o incompatibile. Esegui il normale aggiornamento dal server.", "connectivity")
}

// diagnosticsEqualOne is `value == 1` for a json.loads value.
func diagnosticsEqualOne(value any) bool {
	switch v := value.(type) {
	case bool:
		return v
	case instanceprobe.Int:
		return v == "1"
	case float64:
		return v == 1
	}
	return false
}

// pythonIntBytes is int(value) for the bytes django_redis stores for an int:
// ASCII digits with an optional sign, single underscores between digits and
// surrounding whitespace.
func pythonIntBytes(raw []byte) (float64, bool) {
	text := strings.TrimFunc(string(raw), func(r rune) bool { return r == ' ' || (r >= '\t' && r <= '\r') })
	if !utf8.ValidString(text) {
		return 0, false
	}
	negative := false
	if strings.HasPrefix(text, "+") || strings.HasPrefix(text, "-") {
		negative, text = text[0] == '-', text[1:]
	}
	if text == "" || strings.HasPrefix(text, "_") || strings.HasSuffix(text, "_") || strings.Contains(text, "__") {
		return 0, false
	}
	value := 0.0
	for _, r := range text {
		switch {
		case r == '_':
		case r >= '0' && r <= '9':
			value = value*10 + float64(r-'0')
		default:
			return 0, false
		}
	}
	if negative {
		value = -value
	}
	return value, true
}

// unpickleNumber reads one pickled scalar (protocol 2 or later, optionally
// framed): a float, an int, a bool, None or a short string. Numbers report
// number=true; the others are values that are not numbers.
func unpickleNumber(raw []byte) (float64, bool, error) {
	if len(raw) < 3 || raw[0] != 0x80 || raw[1] < 2 || raw[1] > 5 {
		return 0, false, errDiagnosticProbe
	}
	data := raw[2:]
	if len(data) >= 9 && data[0] == 0x95 { // FRAME
		data = data[9:]
	}
	if len(data) < 2 {
		return 0, false, errDiagnosticProbe
	}
	var value float64
	number := true
	rest := data[1:]
	switch data[0] {
	case 'G': // BINFLOAT
		if len(rest) < 8 {
			return 0, false, errDiagnosticProbe
		}
		var bits uint64
		for _, b := range rest[:8] {
			bits = bits<<8 | uint64(b)
		}
		value, rest = math.Float64frombits(bits), rest[8:]
	case 'J': // BININT
		if len(rest) < 4 {
			return 0, false, errDiagnosticProbe
		}
		value, rest = float64(int32(uint32(rest[0])|uint32(rest[1])<<8|uint32(rest[2])<<16|uint32(rest[3])<<24)), rest[4:]
	case 'K': // BININT1
		value, rest = float64(rest[0]), rest[1:]
	case 'M': // BININT2
		if len(rest) < 2 {
			return 0, false, errDiagnosticProbe
		}
		value, rest = float64(uint16(rest[0])|uint16(rest[1])<<8), rest[2:]
	case 0x88: // NEWTRUE
		value = 1
	case 0x89: // NEWFALSE
		value = 0
	case 'N': // NONE
		number = false
	case 0x8c: // SHORT_BINUNICODE
		if len(rest) < 1 || len(rest) < 1+int(rest[0]) {
			return 0, false, errDiagnosticProbe
		}
		rest, number = rest[1+int(rest[0]):], false
		if len(rest) > 0 && rest[0] == 0x94 { // MEMOIZE
			rest = rest[1:]
		}
	default:
		return 0, false, errDiagnosticProbe
	}
	if len(rest) != 1 || rest[0] != '.' {
		return 0, false, errDiagnosticProbe
	}
	return value, number, nil
}

type diagnosticsItem struct {
	name  string
	value any
}

// celeryDict is a decoded JSON object of a control reply, in order.
func celeryDict(value any) ([]diagnosticsItem, bool) {
	object, ok := value.(celery.Dict)
	if !ok {
		return nil, false
	}
	items := make([]diagnosticsItem, len(object))
	for i, item := range object {
		items[i] = diagnosticsItem{item.Key, item.Value}
	}
	return items, true
}
