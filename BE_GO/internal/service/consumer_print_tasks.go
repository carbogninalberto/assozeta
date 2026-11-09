package service

import (
	"context"
	"errors"
	"io"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/google/uuid"
)

// Celery names of the consumed print tasks (BE/application/tasks.py and
// BE/application/printing_tasks.py).
const (
	ConsumerPrintSubscriptionTask       = "print_document_subscription"
	ConsumerPrintInvoiceTask            = "print_document_invoice"
	ConsumerPrintCustomerInvoiceTask    = "print_document_customer_invoice"
	ConsumerPrintCompensationTask       = "print_document_compensation"
	ConsumerPrintMedicalAppointmentTask = "print_document_medical_appointment"
)

// ConsumerPrint runs the print tasks consumed from the Go queue. Each body is
//
//	try:
//	    requests.get(f"{CURRENT_HOST}/document/<kind>/<id>[?send_receipt_email=<flag>]",
//	                 headers={'Authorization': auth_token})
//	except Exception: print(...)
//
// so the task always succeeds with None: the response status, a refused
// connection and an invalid header value are all swallowed. The document is
// rendered by the route that answers the GET, not by the task.
type ConsumerPrint struct {
	host   string
	client *http.Client
}

func NewConsumerPrint(host string) *ConsumerPrint {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.DisableCompression = true
	client := &http.Client{Transport: transport, CheckRedirect: func(request *http.Request, via []*http.Request) error {
		// requests follows at most 30 redirects (Session.max_redirects) and sends no Referer.
		if len(via) > 30 {
			return errors.New("too many redirects")
		}
		request.Header.Del("Referer")
		return nil
	}}
	return &ConsumerPrint{host: host, client: client}
}

// consumerPrintSpec is one task signature: the positional parameters, how
// many are required, and the URL path built from the bound values.
type consumerPrintSpec struct {
	params   []string
	required int
	kind     string
}

var consumerPrintSpecs = map[string]consumerPrintSpec{
	ConsumerPrintSubscriptionTask:       {params: []string{"subscription_id", "auth_token"}, required: 2, kind: "subscription"},
	ConsumerPrintInvoiceTask:            {params: []string{"invoice_id", "auth_token", "send_receipt_email"}, required: 2, kind: "invoice"},
	ConsumerPrintCustomerInvoiceTask:    {params: []string{"customer_invoice_id", "auth_token"}, required: 2, kind: "einvoice"},
	ConsumerPrintCompensationTask:       {params: []string{"payment_id", "auth_token"}, required: 2, kind: "compensation"},
	ConsumerPrintMedicalAppointmentTask: {params: []string{"medical_appointment_id", "auth_token"}, required: 2, kind: "medical-appointment"},
}

// consumerPrintCall is what Plan prepared: the request to send, or none when
// requests would raise before connecting (InvalidHeader).
type consumerPrintCall struct {
	path          string
	authorization *string
	send          bool
}

// Tasks registers the five print tasks (no retry policy of their own; the
// Celery default max_retries 3 is never reached).
func (p *ConsumerPrint) Tasks() map[string]celeryworker.Task {
	tasks := map[string]celeryworker.Task{}
	for name, spec := range consumerPrintSpecs {
		spec := spec
		tasks[name] = celeryworker.Task{MaxRetries: 3,
			Plan: func(_ context.Context, request *celeryworker.Request) error { return p.plan(spec, request) },
			Run:  p.run}
	}
	return tasks
}

// consumerPrintBind applies a Python signature without **kwargs: positional
// arguments first, then keywords, no duplicates or unknown names, every
// required parameter present. Anything else (Celery's TypeError) is handed off.
func consumerPrintBind(params []string, required int, request *celeryworker.Request) (map[string]any, error) {
	if len(request.Args) > len(params) {
		return nil, celeryworker.ErrHandoff
	}
	values := map[string]any{}
	for i, value := range request.Args {
		values[params[i]] = value
	}
	for _, item := range request.Kwargs {
		if _, duplicate := values[item.Key]; duplicate || !containsString(params, item.Key) {
			return nil, celeryworker.ErrHandoff
		}
		values[item.Key] = item.Value
	}
	for _, name := range params[:required] {
		if _, present := values[name]; !present {
			return nil, celeryworker.ErrHandoff
		}
	}
	return values, nil
}

// consumerPrintSegment is str(value) inside the f-string URL, for the values
// whose text requests sends unchanged: UUIDs and strings of unreserved URL
// characters (requests re-quotes others and urllib3 removes dot segments).
func consumerPrintSegment(value any) (string, bool) {
	switch v := value.(type) {
	case uuid.UUID:
		return v.String(), true
	case string:
		if v == "." || v == ".." {
			return "", false
		}
		for _, r := range []byte(v) {
			if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '-' || r == '.' || r == '_' || r == '~') {
				return "", false
			}
		}
		return v, true
	}
	return "", false
}

// consumerPrintFlag is str(send_receipt_email) in the query string.
func consumerPrintFlag(value any) (string, bool) {
	switch v := value.(type) {
	case nil:
		return "None", true
	case bool:
		if v {
			return "True", true
		}
		return "False", true
	}
	return consumerPrintSegment(value)
}

// consumerPrintAuthorization classifies the Authorization header value:
// requests drops a None header; a non-string or a string failing
// ^\S[^\r\n]*$|^$ raises InvalidHeader before any connection (no request);
// printable ASCII values without trailing blanks are sent unchanged. Values
// Go's HTTP client would alter (control characters, non-ASCII, trailing
// blanks) are handed off.
func consumerPrintAuthorization(value any) (header *string, send bool, err error) {
	if value == nil {
		return nil, true, nil
	}
	text, ok := value.(string)
	if !ok {
		return nil, false, nil
	}
	for _, r := range []byte(text) {
		if r != '\t' && (r < 0x20 || r > 0x7e) {
			return nil, false, celeryworker.ErrHandoff
		}
	}
	if text == "" {
		return &text, true, nil
	}
	if text[0] == ' ' || text[0] == '\t' {
		return nil, false, nil
	}
	if last := text[len(text)-1]; last == ' ' || last == '\t' {
		return nil, false, celeryworker.ErrHandoff
	}
	return &text, true, nil
}

func (p *ConsumerPrint) plan(spec consumerPrintSpec, request *celeryworker.Request) error {
	values, err := consumerPrintBind(spec.params, spec.required, request)
	if err != nil {
		return err
	}
	segment, ok := consumerPrintSegment(values[spec.params[0]])
	if !ok {
		return celeryworker.ErrHandoff
	}
	path := "/document/" + spec.kind + "/" + segment
	if len(spec.params) == 3 {
		flag, present := values[spec.params[2]]
		if !present {
			flag = false
		}
		text, ok := consumerPrintFlag(flag)
		if !ok {
			return celeryworker.ErrHandoff
		}
		path += "?send_receipt_email=" + text
	}
	authorization, send, err := consumerPrintAuthorization(values["auth_token"])
	if err != nil {
		return err
	}
	request.State = consumerPrintCall{path: path, authorization: authorization, send: send}
	return nil
}

// run sends the GET with requests' default headers and reads the body;
// every error is swallowed and the task returns None.
func (p *ConsumerPrint) run(ctx context.Context, request *celeryworker.Request) (any, error) {
	call, _ := request.State.(consumerPrintCall)
	if !call.send {
		return nil, nil
	}
	outbound, err := http.NewRequestWithContext(context.WithoutCancel(ctx), http.MethodGet, p.host+call.path, nil)
	if err != nil {
		return nil, nil
	}
	outbound.Header = http.Header{"User-Agent": {invoiceSyncUserAgent}, "Accept-Encoding": {"gzip, deflate"}, "Accept": {"*/*"},
		"Connection": {"keep-alive"}}
	if call.authorization != nil {
		outbound.Header["Authorization"] = []string{*call.authorization}
	}
	response, err := p.client.Do(outbound)
	if err != nil {
		return nil, nil
	}
	_, _ = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	return nil, nil
}
