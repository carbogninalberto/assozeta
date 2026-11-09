package handler

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"math"
	"math/big"
	"strconv"
	"sync"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/wsproto"
)

// AgentWebSocket is application.chat.consumers.AgentConsumer's runtime.
type AgentWebSocket struct {
	Runtime *service.AgentRuntime
	Cache   *repository.AgentCache
}

const (
	agentWelcomeManualOnly = "Ciao! Posso cercare istruzioni verificate nel manuale di Assozeta. " +
		"Scrivi la tua domanda oppure [sfoglia il manuale d’uso](/#/manuale). " +
		"Se mancano informazioni verificate, te lo segnalo. " +
		"Le analisi dei dati e gli export richiedono la configurazione AI."
	agentWelcomeFull = "Ciao! Posso aiutarti a cercare informazioni nell’associazione " +
		"e a capire come usare Assozeta.\n\n" +
		"- **Cerca nei dati**: per esempio, «Quanti iscritti ho quest’anno?»\n" +
		"- **Trova una guida**: per esempio, «Come posso assegnare un tag?»\n" +
		"- **Prepara un export** dei dati che ti servono.\n\n" +
		"Quando cerchi istruzioni, ti indico la guida disponibile nel manuale. " +
		"Se mancano informazioni verificate, te lo segnalo.\n\n" +
		"[Sfoglia il manuale d’uso](/#/manuale) oppure scrivi qui la tua domanda."
	agentWelcomeData = "Ciao! Posso aiutarti a cercare informazioni nell’associazione " +
		"e a preparare export dei dati. Scrivi qui la tua domanda."
	agentNeedsAI           = "Questa funzione richiede la configurazione AI."
	agentBusyElaborating   = "Sto ancora elaborando la richiesta precedente. Attendi o annulla."
	agentConfigUnavailable = "Configurazione AI non disponibile. Contatta il proprietario dell’istanza."
	agentThrottled         = "Troppi messaggi. Attendi un momento prima di riprovare."
	agentTimeout           = "La richiesta ha impiegato troppo tempo. Prova con una richiesta più specifica."
	agentInternal          = "Si è verificato un errore interno. Riprova."
)

type agentConsumer struct {
	rt          *AgentWebSocket
	user        string
	association repository.AgentAssociation
	config      map[string]any
	agent       *service.Agent
	limit       any
	acquired    bool

	// mu orders the request task's frames with the receive loop's: the
	// task's state, its cancellation and its final frames.
	mu           sync.Mutex
	running      bool
	cancel       context.CancelFunc
	saveInfo     any
	saveFilename any
	welcome      string
}

func newAgentConsumer(rt *AgentWebSocket) *agentConsumer { return &agentConsumer{rt: rt} }

func (c *agentConsumer) event(*session, domain.RequestObject) error { return errCrash }

// refused ends the connection for an input the port does not reproduce.
func agentRefused(err error) error {
	if errors.Is(err, service.ErrAgentNotPorted) {
		slog.Warn("websocket agent input not ported", "error", err)
	}
	return err
}

func (c *agentConsumer) connect(s *session) (bool, error) {
	if s.user == nil {
		return false, nil // close(4001)
	}
	c.user = s.user.ID
	config, err := c.rt.Runtime.AIConfig(s.ctx)
	if errors.Is(err, service.ErrAgentConfigUnavailable) {
		return false, nil // close(4004)
	}
	if err != nil {
		return false, agentRefused(err)
	}
	c.config = config
	manual := c.rt.Runtime.ManualAvailable(s.ctx)
	enabled, present := config["enabled"]
	if !present {
		return false, agentRefused(service.ErrAgentNotPorted)
	}
	if !dto.PythonTruthy(enabled) && !manual {
		return false, nil // close(4004)
	}
	acquired, err := c.rt.Cache.GuardAcquire(s.ctx, c.user)
	if err != nil {
		return false, err
	}
	if !acquired {
		return false, nil // close(4002)
	}
	c.acquired = true
	association, ok, err := c.rt.Runtime.Association(s.ctx, s.user)
	if errors.Is(err, service.ErrAgentNotPorted) {
		return false, agentRefused(err)
	}
	if err != nil || !ok {
		c.release(s)
		return false, nil // close(4003)
	}
	c.association = association
	limit, present := config["ws_rate_limit"]
	if !present {
		return false, agentRefused(service.ErrAgentNotPorted)
	}
	c.limit = limit
	provider, err := c.rt.Runtime.Provider(config)
	if errors.Is(err, service.ErrAgentNotPorted) {
		return false, agentRefused(err)
	}
	if err != nil {
		c.release(s)
		return false, nil // close(4004)
	}
	c.agent = c.rt.Runtime.NewAgent(provider, &agentSocketCallback{c: c, s: s}, association, c.user, config, manual)
	switch {
	case !dto.PythonTruthy(enabled):
		c.welcome = agentWelcomeManualOnly
	case manual:
		c.welcome = agentWelcomeFull
	default:
		c.welcome = agentWelcomeData
	}
	return true, nil
}

func (c *agentConsumer) afterAccept(s *session) error {
	return s.send(object("type", "message", "content", c.welcome))
}

func (c *agentConsumer) release(s *session) {
	if !c.acquired {
		return
	}
	c.acquired = false
	ctx, cancel := context.WithTimeout(context.WithoutCancel(s.ctx), 5*time.Second)
	defer cancel()
	if err := c.rt.Cache.GuardRelease(ctx, c.user); err != nil {
		slog.Warn("websocket agent concurrency slot not released", "error", err)
	}
}

func (c *agentConsumer) disconnect(s *session) {
	c.mu.Lock()
	if c.running && c.cancel != nil {
		c.cancel()
	}
	c.mu.Unlock()
	c.release(s)
}

func agentErrorFrame(message string) domain.RequestObject {
	return object("type", "error", "message", message)
}

func (c *agentConsumer) receive(s *session, message *wsproto.Message) error {
	content, err := jsonContent(message)
	if err != nil {
		return err
	}
	kind, err := messageType(content)
	if err != nil {
		return err
	}
	switch kind.(type) {
	case domain.RequestObject, []any:
		return errCrash // unhashable in `msg_type in {...}`
	}
	if isString(kind, "save_report") || isString(kind, "delete_report") || isString(kind, "list_reports") {
		current, err := c.rt.Runtime.AIConfig(s.ctx)
		if err != nil {
			return agentRefused(err)
		}
		if !dto.PythonTruthy(current["enabled"]) {
			return s.send(agentErrorFrame(agentNeedsAI))
		}
	}
	switch {
	case isString(kind, "ping"):
		timestamp, _ := dto.PythonGet(content, "timestamp")
		return s.send(object("type", "pong", "timestamp", timestamp))
	case isString(kind, "user_message"):
		return c.userMessage(s, content)
	case isString(kind, "cancel"):
		c.mu.Lock()
		defer c.mu.Unlock()
		if c.running {
			c.cancel()
			return s.send(object("type", "status", "status", "cancelling"))
		}
		return s.send(object("type", "status", "status", "idle"))
	case isString(kind, "clear_history"):
		if c.agent != nil {
			c.agent.ClearHistory()
		}
		return s.send(object("type", "status", "status", "history_cleared"))
	case isString(kind, "save_report"):
		return c.saveReport(s, content)
	case isString(kind, "delete_report"):
		return c.deleteReport(s, content)
	case isString(kind, "list_reports"):
		return c.listReports(s)
	}
	return s.send(agentErrorFrame("Tipo di messaggio sconosciuto: " + dto.PythonStr(kind)))
}

// stringField is content.get(key, ”).strip(): a non-string value raises.
func stringField(content any, key string) (string, error) {
	object, ok := content.(domain.RequestObject)
	if !ok {
		return "", errCrash
	}
	value, present := object.Lookup(key)
	if !present {
		return "", nil
	}
	text, ok := value.(string)
	if !ok {
		return "", errCrash
	}
	return dto.PythonStrip(text), nil
}

// agentLessEqual is `count <= limit` for an integer count.
func agentLessEqual(count int64, limit any) (bool, error) {
	var bound *big.Rat
	switch typed := limit.(type) {
	case json.Number:
		var ok bool
		if bound, ok = new(big.Rat).SetString(string(typed)); !ok {
			return false, service.ErrAgentNotPorted
		}
	case int64:
		bound = big.NewRat(typed, 1)
	case bool:
		bound = big.NewRat(map[bool]int64{true: 1, false: 0}[typed], 1)
	default:
		return false, service.ErrAgentNotPorted
	}
	return big.NewRat(count, 1).Cmp(bound) <= 0, nil
}

func (c *agentConsumer) userMessage(s *session, content any) error {
	message, err := stringField(content, "message")
	if err != nil {
		return err
	}
	if message == "" {
		return nil
	}
	c.mu.Lock()
	busy := c.running
	c.mu.Unlock()
	if busy {
		return s.send(agentErrorFrame(agentBusyElaborating))
	}
	current, err := c.rt.Runtime.AIConfig(s.ctx)
	if errors.Is(err, service.ErrAgentNotPorted) {
		return agentRefused(err)
	}
	if err == nil && !service.AgentConfigEqual(current, c.config) {
		var provider service.AgentProvider
		provider, err = c.rt.Runtime.Provider(current)
		if errors.Is(err, service.ErrAgentNotPorted) {
			return agentRefused(err)
		}
		if err == nil {
			for _, key := range []string{"max_iterations", "history_cap", "ws_rate_limit"} {
				if _, present := current[key]; !present {
					return agentRefused(service.ErrAgentNotPorted)
				}
			}
			c.agent.Provider = provider
			c.agent.MaxIterations = current["max_iterations"]
			c.agent.HistoryCap = current["history_cap"]
			c.limit = current["ws_rate_limit"]
			c.config = current
		}
	}
	if err != nil {
		return s.send(agentErrorFrame(agentConfigUnavailable))
	}
	var compare error
	allowed, err := c.rt.Cache.ThrottleAllowed(s.ctx, c.user, func(count int64) bool {
		ok, err := agentLessEqual(count, c.limit)
		compare = err
		return ok
	})
	if err == nil {
		err = compare
	}
	if err != nil {
		return agentRefused(err)
	}
	if !allowed {
		return s.send(agentErrorFrame(agentThrottled))
	}
	seconds, ok := agentSeconds(c.config["ws_timeout"])
	if !ok {
		return agentRefused(service.ErrAgentNotPorted)
	}
	ctx, cancel := context.WithCancel(s.ctx)
	c.mu.Lock()
	c.running, c.cancel = true, cancel
	c.mu.Unlock()
	go c.run(s, ctx, cancel, message, seconds)
	return nil
}

func agentSeconds(value any) (time.Duration, bool) {
	number, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	seconds, err := strconv.ParseFloat(string(number), 64)
	if err != nil || math.IsNaN(seconds) || seconds <= 0 || seconds > 1e6 {
		return 0, false
	}
	return time.Duration(seconds * float64(time.Second)), true
}

// finish sends the task's final frames and marks it done for the receive loop.
func (c *agentConsumer) finish(s *session, frames ...domain.RequestObject) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, frame := range frames {
		_ = s.send(frame)
	}
	c.running = false
}

// run is _run_agent_with_timeout under coordinated_async.
func (c *agentConsumer) run(s *session, ctx context.Context, cancel context.CancelFunc, message string, timeout time.Duration) {
	defer cancel()
	release, busy, err := c.rt.Runtime.Coordination.Acquire(ctx)
	if err != nil {
		c.finish(s)
		return
	}
	if busy != "" {
		c.finish(s, agentErrorFrame(busy), object("type", "done"))
		return
	}
	defer release()
	work, stop := context.WithTimeout(ctx, timeout)
	defer stop()
	err = c.agent.ProcessMessage(work, message)
	switch {
	case err == nil:
		c.finish(s)
	case errors.Is(err, service.ErrAgentNotPorted):
		_ = agentRefused(err)
		c.finish(s)
		s.conn.Abort()
	case ctx.Err() != nil:
		c.finish(s, object("type", "status", "status", "cancelled"), object("type", "done"))
	case work.Err() != nil:
		c.finish(s, agentErrorFrame(agentTimeout), object("type", "done"))
	default:
		slog.Warn("websocket agent processing error", "error", err)
		c.finish(s, agentErrorFrame(agentInternal), object("type", "done"))
	}
}

// coordinated is coordinated_async for a handler: Busy answers error + done.
func (c *agentConsumer) coordinated(s *session, handler func() error) error {
	release, busy, err := c.rt.Runtime.Coordination.Acquire(s.ctx)
	if err != nil {
		return err
	}
	if busy != "" {
		if err := s.send(agentErrorFrame(busy)); err != nil {
			return err
		}
		return s.send(object("type", "done"))
	}
	defer release()
	return handler()
}

func (c *agentConsumer) saveReport(s *session, content any) error {
	return c.coordinated(s, func() error {
		name, err := stringField(content, "name")
		if err != nil {
			return err
		}
		if name == "" {
			return s.send(agentErrorFrame("Il nome del report è obbligatorio."))
		}
		c.mu.Lock()
		saveInfo, filename := c.saveInfo, c.saveFilename
		c.mu.Unlock()
		if !dto.PythonTruthy(saveInfo) {
			cached, err := c.rt.Cache.SaveInfo(s.ctx, c.user)
			if err != nil {
				return agentRefused(service.ErrAgentNotPorted)
			}
			if dto.PythonTruthy(cached) {
				object, ok := cached.(domain.RequestObject)
				if !ok {
					return errCrash
				}
				value, present := object.Lookup("save_info")
				if !present {
					return errCrash
				}
				saveInfo, filename = value, "export"
				if value, present := object.Lookup("filename"); present {
					filename = value
				}
			}
		}
		if !dto.PythonTruthy(saveInfo) {
			return s.send(agentErrorFrame("Nessun export disponibile da salvare. Genera prima un export."))
		}
		info, ok := saveInfo.(domain.RequestObject)
		if !ok {
			return errCrash
		}
		requested, err := stringField(content, "description")
		if err != nil {
			return err
		}
		var description any = requested
		if requested == "" {
			description = lookupInfo(info, "description", nil)
			if !dto.PythonTruthy(description) {
				description = dto.PythonStr(filename) + " (" + dto.PythonStr(lookupInfo(info, "description_hint", "")) + ")"
			}
		}
		arguments := object("name", name, "tool_name", lookupInfo(info, "tool_name", nil),
			"params", lookupInfo(info, "params", domain.RequestObject{}), "description", description)
		if config := lookupInfo(info, "ui_config", nil); config != nil {
			arguments = append(arguments, domain.RequestField{Name: "ui_config", Value: config})
		}
		arguments = append(arguments, domain.RequestField{Name: "user_id", Value: c.user})
		failed := func() error { return s.send(agentErrorFrame("Errore nel salvataggio del report. Riprova.")) }
		release, busy, err := c.rt.Runtime.Coordination.Acquire(s.ctx)
		if err != nil || busy != "" {
			return failed()
		}
		result, err := c.rt.Runtime.BusinessTools(c.association.ID).Execute(s.ctx, "save_report", arguments)
		release()
		if errors.Is(err, service.ErrBusinessMCPNotPorted) {
			return agentRefused(service.ErrAgentNotPorted)
		}
		if err != nil {
			return failed()
		}
		values := result.(domain.RequestObject)
		if message, present := values.Lookup("error"); present {
			return s.send(agentErrorFrame(dto.PythonStr(message)))
		}
		return s.send(object("type", "report_saved", "saved_report_id", lookupInfo(values, "saved_report_id", ""),
			"name", lookupInfo(values, "name", name), "message", lookupInfo(values, "message", "Report salvato.")))
	})
}

func lookupInfo(object domain.RequestObject, key string, fallback any) any {
	if value, present := object.Lookup(key); present {
		return value
	}
	return fallback
}

func (c *agentConsumer) deleteReport(s *session, content any) error {
	return c.coordinated(s, func() error {
		identifier, err := stringField(content, "saved_report_id")
		if err != nil {
			return err
		}
		if identifier == "" {
			return s.send(agentErrorFrame("ID report mancante."))
		}
		failed := func() error { return s.send(agentErrorFrame("Errore nella cancellazione del report. Riprova.")) }
		release, busy, err := c.rt.Runtime.Coordination.Acquire(s.ctx)
		if err != nil || busy != "" {
			return failed()
		}
		defer release()
		id, valid := service.AgentUUID(identifier)
		if !valid {
			return failed()
		}
		deleted, err := c.rt.Runtime.Store.DeleteSavedReport(s.ctx, id, c.association.ID, c.user)
		if err != nil {
			return failed()
		}
		if deleted == 0 {
			return s.send(agentErrorFrame("Report non trovato."))
		}
		return s.send(object("type", "report_deleted", "saved_report_id", identifier))
	})
}

func (c *agentConsumer) listReports(s *session) error {
	result, err := c.rt.Runtime.BusinessTools(c.association.ID).Execute(s.ctx, "list_reports", object("user_id", c.user))
	if errors.Is(err, service.ErrBusinessMCPNotPorted) {
		return agentRefused(service.ErrAgentNotPorted)
	}
	if err != nil {
		return s.send(agentErrorFrame("Errore nel recupero dei report. Riprova."))
	}
	values := result.(domain.RequestObject)
	if message, present := values.Lookup("error"); present {
		return s.send(agentErrorFrame(dto.PythonStr(message)))
	}
	return s.send(object("type", "report_list", "reports", lookupInfo(values, "reports", []any{}),
		"count", lookupInfo(values, "count", jsonInt(0))))
}

// agentSocketCallback is WebSocketAgentCallback.
type agentSocketCallback struct {
	c *agentConsumer
	s *session
}

func (b *agentSocketCallback) send(ctx context.Context, frame domain.RequestObject) error {
	b.c.mu.Lock()
	defer b.c.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	return b.s.send(frame)
}

func (b *agentSocketCallback) OnStatus(ctx context.Context, status string) error {
	return b.send(ctx, object("type", "status", "status", status))
}

func (b *agentSocketCallback) OnToolCall(ctx context.Context, name, arguments any) error {
	return b.send(ctx, object("type", "tool_call", "tool", name, "arguments", arguments))
}

func (b *agentSocketCallback) OnMessageChunk(ctx context.Context, chunk string) error {
	return b.send(ctx, object("type", "message_chunk", "content", chunk))
}

func (b *agentSocketCallback) OnMessageEnd(ctx context.Context) error {
	return b.send(ctx, object("type", "message_end"))
}

func (b *agentSocketCallback) OnManualSection(ctx context.Context, section any) error {
	return b.send(ctx, object("type", "manual_section", "section", section))
}

func (b *agentSocketCallback) OnExportReady(ctx context.Context, export domain.RequestObject) error {
	info, hasInfo := export.Lookup("save_info")
	if hasInfo {
		filename := lookupInfo(export, "filename", "export")
		b.c.mu.Lock()
		b.c.saveInfo, b.c.saveFilename = info, filename
		b.c.mu.Unlock()
		if err := b.c.rt.Cache.SetSaveInfo(ctx, b.c.user, info, filename); err != nil {
			if errors.Is(err, repository.ErrAgentCacheShape) {
				return service.ErrAgentNotPorted
			}
			return err
		}
	}
	payload := object("type", lookupInfo(export, "type", "export_ready"), "filename", lookupInfo(export, "filename", "export"),
		"content_type", lookupInfo(export, "content_type", ""), "data_base64", lookupInfo(export, "data_base64", ""),
		"row_count", lookupInfo(export, "row_count", jsonInt(0)), "can_save", hasInfo)
	for _, key := range []string{"total_count", "total_dates", "sheet_count"} {
		if value, present := export.Lookup(key); present {
			payload = append(payload, domain.RequestField{Name: key, Value: value})
		}
	}
	if hasInfo {
		saveInfo, ok := info.(domain.RequestObject)
		if !ok {
			return service.ErrAgentInternal
		}
		payload = append(payload, domain.RequestField{Name: "default_name", Value: lookupInfo(saveInfo, "name", "")},
			domain.RequestField{Name: "default_description", Value: lookupInfo(saveInfo, "description", "")},
			domain.RequestField{Name: "description_hint", Value: lookupInfo(saveInfo, "description_hint", "")})
	}
	return b.send(ctx, payload)
}

func (b *agentSocketCallback) OnReportSaved(ctx context.Context, report domain.RequestObject) error {
	return b.send(ctx, object("type", "report_saved", "saved_report_id", lookupInfo(report, "saved_report_id", ""),
		"name", lookupInfo(report, "name", "")))
}

func (b *agentSocketCallback) OnError(ctx context.Context, message string) error {
	return b.send(ctx, agentErrorFrame(message))
}

// OnDone sends done and marks the request finished in one step, so a frame
// the client sends after done never meets a running request.
func (b *agentSocketCallback) OnDone(ctx context.Context) error {
	b.c.mu.Lock()
	defer b.c.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	err := b.s.send(object("type", "done"))
	b.c.running = false
	return err
}
