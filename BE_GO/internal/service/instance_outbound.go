package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
)

// InstanceOutbound ports the owner-only instance operations that contact
// other services: InstanceReleasesView (GitHub release feed and the
// catalog in Django's cache), EmailTestView (SMTP connection and test
// message) and IntegrationTestView (provider checks). Django runs each
// probe in a subprocess (probe_runner.run_probe, 10 s); Go runs the same
// steps in-process under the same deadline.
type InstanceOutbound struct {
	admin  *InstanceAdmin
	leases InstanceLeaseStore
	cache  InstanceReleaseCache
	probes InstanceProbes
	now    func() time.Time
	// localName is DNS_NAME.get_fqdn() (computed once, like Django's cache).
	localName func() string
}

// InstanceLeaseStore is claim_operation's select_for_update(nowait=True).
type InstanceLeaseStore interface {
	// ClaimLease locks the row without waiting and saves the results apply
	// returns (update_fields=['diagnostic_results']); false when the row
	// is missing or locked, or any database error occurs.
	ClaimLease(ctx context.Context, apply func(results json.RawMessage) (json.RawMessage, error)) (bool, error)
}

// InstanceReleaseCache is the catalog in Django's cache.
type InstanceReleaseCache interface {
	Catalog(context.Context) ([]instanceprobe.Release, bool, error)
	SetCatalog(context.Context, []instanceprobe.Release) error
	AddRefresh(context.Context) (bool, error)
}

// InstanceProbes are the outbound requests.
type InstanceProbes interface {
	FetchReleases(context.Context) ([]instanceprobe.Release, error)
	VerifyIntegration(ctx context.Context, provider string, value map[string]any) (*instanceprobe.Result, error)
	SMTP(host string) smtpsend.Server
}

// SMTPTLS adapts an instanceprobe.Client to InstanceProbes.
type SMTPTLS struct{ *instanceprobe.Client }

// SMTP is Django's SMTP backend connection settings for host with
// ssl.create_default_context().
func (c SMTPTLS) SMTP(host string) smtpsend.Server {
	return smtpsend.Server{Host: host, TLSConfig: c.TLSConfig(host), Timeout: 5 * time.Second}
}

func NewInstanceOutbound(admin *InstanceAdmin, leases InstanceLeaseStore, cache InstanceReleaseCache, probes InstanceProbes, now func() time.Time) *InstanceOutbound {
	return &InstanceOutbound{admin: admin, leases: leases, cache: cache, probes: probes, now: now,
		localName: sync.OnceValue(func() string {
			host, _ := os.Hostname()
			return smtpsend.FQDN(host)
		})}
}

const probeDeadline = 10 * time.Second

// ---------------------------------------------------------------------------
// admin/releases
// ---------------------------------------------------------------------------

// Releases is InstanceReleasesView.get. page and refresh are the last query
// values (present reports page's presence).
func (s *InstanceOutbound) Releases(ctx context.Context, page string, present bool, refresh string) (map[string]any, error) {
	if !present {
		page = "1"
	}
	// int() refuses more than 4300 digits (sys.int_info.default_max_str_digits).
	number, ok := dto.PythonIntText(page)
	if !ok || number.Sign() < 1 || len(new(big.Int).Abs(number).String()) > 4300 {
		return nil, &dto.InstanceAdminInvalid{Body: []string{"Invalid release history page."}}
	}
	releases, cached, err := s.cache.Catalog(ctx)
	if err != nil {
		return nil, refusedErr("cached release catalog", err)
	}
	fetch := !cached
	if cached && refresh == "1" {
		if fetch, err = s.cache.AddRefresh(ctx); err != nil {
			return nil, err
		}
	}
	if fetch {
		fetched, err := s.probes.FetchReleases(ctx)
		var failure *instanceprobe.ReleaseError
		switch {
		case errors.As(err, &failure):
			return nil, instanceAdminError(failure.Message, 503)
		case errors.Is(err, instanceprobe.ErrReleaseNotPorted), errors.Is(err, instanceprobe.ErrNotPorted):
			return nil, refusedErr("release feed", err)
		case err != nil:
			return nil, err
		}
		releases = fetched
		if err = s.cache.SetCatalog(ctx, releases); err != nil {
			return nil, err
		}
	}
	return instanceprobe.Summary(releases, s.admin.env.Version, number), nil
}

func refusedErr(reason string, err error) error {
	if errors.Is(err, domain.ErrInstanceAdminRefused) {
		return err
	}
	return refused(reason + ": " + err.Error())
}

// ---------------------------------------------------------------------------
// claim_operation
// ---------------------------------------------------------------------------

// pythonTimestamp is timezone.now().timestamp(): microseconds / 10**6.
func pythonTimestamp(t time.Time) float64 {
	return float64(t.UnixMicro()) / 1e6
}

// pythonFloatJSON is json.dumps of a float (repr).
func pythonFloatJSON(f float64) json.Number {
	text := strconv.FormatFloat(f, 'f', -1, 64)
	if !strings.ContainsAny(text, ".") {
		text += ".0"
	}
	return json.Number(text)
}

// claim is claim_operation(name, seconds).
func (s *InstanceOutbound) claim(ctx context.Context, name string, seconds int64) (bool, error) {
	return s.leases.ClaimLease(ctx, func(raw json.RawMessage) (json.RawMessage, error) {
		value, err := instanceAdminDecode(raw)
		if err != nil {
			return nil, err
		}
		results, err := instanceAdminObject(value)
		if err != nil {
			return nil, err
		}
		results = instanceAdminCopy(results)
		leases := map[string]any{}
		if stored, present := results["operation_leases"]; present {
			object, ok := stored.(map[string]any)
			if !ok {
				return nil, refused("stored leases are not an object")
			}
			leases = instanceAdminCopy(object)
		}
		now := pythonTimestamp(s.now())
		lease, present := leases[name]
		if !present {
			lease = json.Number("0")
		}
		until, ok := instanceAdminNumber(lease)
		if !ok {
			return nil, refused("stored lease is not a number")
		}
		if until.Cmp(new(big.Rat).SetFloat64(now)) > 0 {
			return nil, nil
		}
		leases[name] = pythonFloatJSON(now + float64(seconds))
		results["operation_leases"] = leases
		return instanceAdminMarshal(results)
	})
}

// probeResult is run_probe's answer: the probe result and checked_at.
func (s *InstanceOutbound) probeResult(result instanceprobe.Result) map[string]any {
	return map[string]any{"status": result.Status, "level": result.Level, "message": result.Message,
		"checked_at": pythonISOFormat(s.now())}
}

func timeoutResult(send bool) instanceprobe.Result {
	message := "Tempo massimo superato. Controlla la disponibilità del servizio e riprova."
	if send {
		message = "Tempo massimo superato: invio non confermato. Verifica il destinatario prima di ripetere il test."
	}
	return instanceprobe.Result{Status: "failed", Level: "connectivity", Message: message}
}

// ---------------------------------------------------------------------------
// admin/email/test
// ---------------------------------------------------------------------------

const (
	emailTestSubject = "=?utf-8?q?Assozeta_=E2=80=94_verifica_email?=" // Header('Assozeta — verifica email', 'utf-8').encode()
	emailTestBody    = "Questo messaggio è stato richiesto dal proprietario dell’istanza per verificare la configurazione email."
)

// emailPlan is effective_email(config) as the probe reads it, or the probe
// answer that needs no connection.
type emailPlan struct {
	answer   *instanceprobe.Result
	server   smtpsend.Server
	from     string
	sender   string
	revision any
}

func emailText(settings map[string]any, key string) (string, error) {
	text, ok := settings[key].(string)
	if !ok {
		return "", refused("stored email setting " + key)
	}
	return text, nil
}

// emailProbePlan follows diagnostic_probe.probe('email_<action>') up to the
// SMTP connection.
func (s *InstanceOutbound) emailProbePlan(row *domain.InstanceAdminRow, revision *big.Int) (*emailPlan, error) {
	settings, instance, err := emailSettings(row)
	if err != nil {
		return nil, err
	}
	plan := &emailPlan{}
	var host, username, password, security string
	var port int64
	if instance {
		plain, err := s.admin.decrypt(instanceEmailCipher, row.EmailPassword)
		if errors.Is(err, errInstanceAdminCredential) {
			plan.answer = &instanceprobe.Generic
			return plan, nil
		}
		if err != nil {
			return nil, err
		}
		values := map[string]*string{"host": &host, "username": &username, "security": &security, "from_email": &plan.from, "sender_name": &plan.sender}
		for key, target := range values {
			if *target, err = emailText(settings, key); err != nil {
				return nil, err
			}
		}
		number, ok := instanceAdminInteger(settings["port"])
		if !ok || !number.IsInt64() {
			return nil, refused("stored email port")
		}
		password, port, plan.revision = plain, number.Int64(), row.EmailRevision
	} else {
		env := s.admin.env
		if env.EmailPortInvalid {
			plan.answer = &instanceprobe.Generic // int(EMAIL_PORT) raises in the probe
			return plan, nil
		}
		value, err := s.admin.environmentEmail(row.EmailRevision)
		if err != nil {
			return nil, err
		}
		host, port, username, password = env.EmailHost, env.EmailPort, env.EmailUsername, env.EmailPassword
		security, plan.from, plan.sender, plan.revision = value["security"].(string), env.FromAddress, env.FromName, row.EmailRevision
	}
	answer := func(status, message, level string) (*emailPlan, error) {
		plan.answer = &instanceprobe.Result{Status: status, Level: level, Message: message}
		return plan, nil
	}
	switch {
	case host == "" || plan.from == "":
		return answer("not_configured", "Configura server SMTP e indirizzo mittente nella sezione Email.", "configuration")
	case security == "invalid":
		return answer("failed", "TLS e SSL sono entrambi attivi nell’ambiente. Scegli una sola modalità nella configurazione email.", "configuration")
	case security == "none" && (username != "" || password != ""):
		return answer("warning", "SMTP senza cifratura. Configura TLS o SSL prima di inviare credenziali o messaggi.", "configuration")
	case (username != "") != (password != ""):
		return answer("warning", "Nome utente e password SMTP incompleti. Completa entrambe le credenziali oppure rimuovile per un relay senza autenticazione.", "configuration")
	case !pythonEqual(revision, plan.revision):
		return answer("not_checked", "Configurazione cambiata. Ricarica e ripeti il test.", "configuration")
	}
	if security != "tls" && security != "ssl" && security != "none" {
		return answer(instanceprobe.Generic.Status, instanceprobe.Generic.Message, instanceprobe.Generic.Level)
	}
	plan.server = s.probes.SMTP(host)
	plan.server.Port, plan.server.User, plan.server.Password = int(port), username, password
	plan.server.Encryption = map[string]string{"tls": "TLS", "ssl": "SSL", "none": ""}[security]
	plan.server.LocalHostname = s.localName()
	return plan, nil
}

// emailMessage renders the probe's EmailMessage, or ErrUnsupported.
func (s *InstanceOutbound) emailMessage(plan *emailPlan, recipient string) ([]byte, error) {
	if err := smtpsend.CheckEnvelope(plan.from, []string{recipient}); err != nil {
		return nil, err
	}
	now := s.now()
	return smtpsend.RenderDjangoText(smtpsend.DjangoText{EncodedSubject: emailTestSubject, Body: emailTestBody, SenderName: plan.sender,
		FromEmail: plan.from, To: recipient, Date: now, MessageID: smtpsend.MessageID(now, s.localName())})
}

// EmailTest is EmailTestView.post.
func (s *InstanceOutbound) EmailTest(ctx context.Context, body any) (map[string]any, error) {
	input, err := dto.InstanceAdminEmailTest(body)
	if err != nil {
		return nil, err
	}
	row, err := s.admin.row(ctx)
	if err != nil {
		return nil, err
	}
	if !pythonEqual(input.Revision, row.EmailRevision) {
		return nil, instanceAdminError("La configurazione è cambiata. Ricarica prima di eseguire il test.", 409)
	}
	recipient := ""
	if input.Recipient != nil {
		recipient = *input.Recipient
	}
	// Inputs this port does not send are refused before the lease.
	plan, err := s.emailProbePlan(row, input.Revision)
	if err != nil {
		return nil, err
	}
	if plan.answer == nil && input.Action == "send" {
		if _, err := s.emailMessage(plan, recipient); err != nil {
			return nil, refused("test message outside the rendered subset")
		}
	}
	if _, err := s.storedResults(row); err != nil {
		return nil, err
	}
	claimed, err := s.claim(ctx, "email_test", 20)
	if err != nil {
		return nil, err
	}
	if !claimed {
		return nil, &InstanceAdminResponse{Status: 429, RetryAfter: "20",
			Body: map[string]any{"error": "Un test è già in corso o è appena terminato. Attendi fino a 20 secondi e riprova."}}
	}
	result, err := s.runEmailProbe(ctx, input.Action, input.Revision, recipient)
	if err != nil {
		return nil, err
	}
	response := s.probeResult(result)
	err = s.admin.store.Locked(ctx, func(locked *domain.InstanceAdminRow) (*domain.InstanceAdminUpdate, error) {
		if locked == nil {
			return nil, refused("no configuration")
		}
		if !pythonEqual(input.Revision, locked.EmailRevision) {
			message := "Configurazione cambiata durante il test. Ricarica le impostazioni."
			if input.Action == "send" {
				message += " Il messaggio potrebbe essere stato inviato: verifica il destinatario prima di riprovare."
			}
			return nil, instanceAdminError(message, 409)
		}
		results, err := s.storedResults(locked)
		if err != nil {
			return nil, err
		}
		stored := instanceAdminCopy(response)
		stored["revision"], stored["action"] = locked.EmailRevision, input.Action
		results["email_test"] = stored
		raw, err := instanceAdminMarshal(results)
		if err != nil {
			return nil, err
		}
		return &domain.InstanceAdminUpdate{DiagnosticResults: raw}, nil
	})
	if err != nil {
		return nil, err
	}
	return response, nil
}

func (s *InstanceOutbound) storedResults(row *domain.InstanceAdminRow) (map[string]any, error) {
	results, err := s.admin.diagnosticResults(row)
	if err != nil {
		return nil, err
	}
	return instanceAdminCopy(results), nil
}

// runEmailProbe is run_probe('email_' + action, value): the probe reads the
// configuration again.
func (s *InstanceOutbound) runEmailProbe(ctx context.Context, action string, revision *big.Int, recipient string) (instanceprobe.Result, error) {
	ctx, cancel := context.WithTimeout(ctx, probeDeadline)
	defer cancel()
	row, err := s.admin.row(ctx)
	if err != nil {
		return instanceprobe.Generic, nil
	}
	plan, err := s.emailProbePlan(row, revision)
	if err != nil {
		return instanceprobe.Result{}, err
	}
	if plan.answer != nil {
		return *plan.answer, nil
	}
	var message []byte
	if action == "send" {
		if message, err = s.emailMessage(plan, recipient); err != nil {
			return instanceprobe.Result{}, refused("test message outside the rendered subset")
		}
	}
	backend, err := smtpsend.OpenBackend(ctx, plan.server)
	if err == nil && action == "send" {
		err = backend.SendMail(plan.from, []string{recipient}, message)
	}
	if backend != nil {
		backend.Close()
	}
	switch {
	case ctx.Err() != nil:
		return timeoutResult(action == "send"), nil
	case err != nil:
		return instanceprobe.Generic, nil
	case action == "connect":
		return instanceprobe.Result{Status: "passed", Level: "connectivity",
			Message: "Connessione SMTP e autenticazione (se configurata) riuscite. Nessun messaggio inviato."}, nil
	}
	return instanceprobe.Result{Status: "passed", Level: "functional",
		Message: "Il server SMTP ha accettato il messaggio. Verifica la casella del destinatario e lo spam; la consegna non è garantita."}, nil
}

// ---------------------------------------------------------------------------
// admin/integrations/<provider>/test
// ---------------------------------------------------------------------------

// probeValue converts the stored JSON (json.Number) to instanceprobe values.
func probeValue(value any) any {
	switch v := value.(type) {
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			f, _ := strconv.ParseFloat(string(v), 64)
			return f
		}
		return instanceprobe.Int(v)
	case map[string]any:
		out := make(map[string]any, len(v))
		for key, item := range v {
			out[key] = probeValue(item)
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = probeValue(item)
		}
		return out
	}
	return value
}

// integrationPlan is the decrypted effective_integration the probe reads, or
// the probe's answer without a request.
func (s *InstanceOutbound) integrationPlan(provider string, row *domain.InstanceAdminRow, revision *big.Int) (map[string]any, *instanceprobe.Result, error) {
	current, err := s.admin.effective(provider, row, true)
	if errors.Is(err, errInstanceAdminCredential) {
		return nil, &instanceprobe.Generic, nil
	}
	if err != nil {
		return nil, nil, err
	}
	if !pythonEqual(current.revision, revision) {
		return nil, &instanceprobe.Result{Status: "not_checked", Level: "configuration",
			Message: "Configurazione cambiata. Ricarica e ripeti la verifica."}, nil
	}
	enabled, present := current.value["enabled"]
	if !present {
		return nil, &instanceprobe.Generic, nil
	}
	if !instanceTruthy(enabled) {
		return nil, &instanceprobe.Result{Status: "not_applicable", Level: "configuration",
			Message: "Integrazione disattivata. Abilitala e salva prima di verificarla."}, nil
	}
	value, _ := probeValue(current.value).(map[string]any)
	return value, nil, nil
}

// IntegrationTest is IntegrationTestView.post (after the provider check).
func (s *InstanceOutbound) IntegrationTest(ctx context.Context, provider string, body any) (map[string]any, error) {
	revision, err := requestRevision(body)
	if err != nil {
		return nil, err
	}
	row, err := s.admin.row(ctx)
	if err != nil {
		return nil, err
	}
	current, err := s.admin.effective(provider, row, false)
	if err != nil {
		return nil, err
	}
	if !pythonEqual(current.revision, revision) {
		return nil, instanceAdminError("Configurazione cambiata. Ricarica prima di verificare.", 409)
	}
	enabled, present := current.value["enabled"]
	if !present {
		return nil, refused("integration settings without enabled")
	}
	if !instanceTruthy(enabled) {
		return nil, instanceAdminError("Abilita e salva l’integrazione prima di verificarla.", 400)
	}
	// Inputs this port does not send are refused before the lease.
	if value, answer, err := s.integrationPlan(provider, row, revision); err != nil {
		return nil, err
	} else if answer == nil {
		if _, _, err := instanceprobe.IntegrationTarget(provider, value); err != nil {
			return nil, refused("integration probe input")
		}
	}
	results, err := s.storedResults(row)
	if err != nil {
		return nil, err
	}
	if tests, present := results["integration_tests"]; present {
		if _, ok := tests.(map[string]any); !ok {
			return nil, refused("stored integration tests are not an object")
		}
	}
	claimed, err := s.claim(ctx, "integration_"+provider, 20)
	if err != nil {
		return nil, err
	}
	if !claimed {
		return nil, &InstanceAdminResponse{Status: 429, RetryAfter: "20",
			Body: map[string]any{"error": "Verifica già in corso o appena terminata. Attendi fino a 20 secondi."}}
	}
	result, err := s.runIntegrationProbe(ctx, provider, revision)
	if err != nil {
		return nil, err
	}
	response := s.probeResult(result)
	err = s.admin.store.Locked(ctx, func(locked *domain.InstanceAdminRow) (*domain.InstanceAdminUpdate, error) {
		if locked == nil {
			return nil, refused("no configuration")
		}
		latest, err := s.admin.effective(provider, locked, false)
		if err != nil {
			return nil, err
		}
		if !pythonEqual(latest.revision, revision) {
			return nil, instanceAdminError("Configurazione cambiata durante la verifica. Ricarica e riprova.", 409)
		}
		results, err := s.storedResults(locked)
		if err != nil {
			return nil, err
		}
		tests := map[string]any{}
		if stored, present := results["integration_tests"]; present {
			object, ok := stored.(map[string]any)
			if !ok {
				return nil, refused("stored integration tests are not an object")
			}
			tests = instanceAdminCopy(object)
		}
		entry := instanceAdminCopy(response)
		entry["revision"] = revision
		tests[provider] = entry
		results["integration_tests"] = tests
		raw, err := instanceAdminMarshal(results)
		if err != nil {
			return nil, err
		}
		return &domain.InstanceAdminUpdate{DiagnosticResults: raw}, nil
	})
	if err != nil {
		return nil, err
	}
	return response, nil
}

// runIntegrationProbe is run_probe('integration_' + provider, {'revision': revision}).
func (s *InstanceOutbound) runIntegrationProbe(ctx context.Context, provider string, revision *big.Int) (instanceprobe.Result, error) {
	ctx, cancel := context.WithTimeout(ctx, probeDeadline)
	defer cancel()
	row, err := s.admin.row(ctx)
	if err != nil {
		return instanceprobe.Generic, nil
	}
	value, answer, err := s.integrationPlan(provider, row, revision)
	if err != nil {
		return instanceprobe.Result{}, err
	}
	if answer != nil {
		return *answer, nil
	}
	result, err := s.probes.VerifyIntegration(ctx, provider, value)
	switch {
	case ctx.Err() != nil:
		return timeoutResult(false), nil
	case errors.Is(err, instanceprobe.ErrProbeNotPorted):
		return instanceprobe.Result{}, refused("integration probe response")
	case err != nil:
		return instanceprobe.Generic, nil
	}
	return *result, nil
}
