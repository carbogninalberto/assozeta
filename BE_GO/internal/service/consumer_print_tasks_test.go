package service

import (
	"errors"
	"net/http"
	"testing"

	"github.com/aws/smithy-go"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/google/uuid"
)

func consumerPrintRequest(args []any, kwargs celery.Dict) *celeryworker.Request {
	return &celeryworker.Request{Message: &celery.Message{Args: args, Kwargs: kwargs}}
}

func TestConsumerPrintPlan(t *testing.T) {
	p := NewConsumerPrint("http://renderer.test")
	id := uuid.MustParse("c6000065-0000-4000-8000-000000000000")
	cases := []struct {
		name    string
		task    string
		args    []any
		kwargs  celery.Dict
		path    string
		auth    any // nil: no header; string: header; false: no request
		handoff bool
	}{
		{"positional", ConsumerPrintSubscriptionTask, []any{"abc-1", "Bearer t"}, nil, "/document/subscription/abc-1", "Bearer t", false},
		{"uuid id", ConsumerPrintCompensationTask, []any{id, "Bearer t"}, nil, "/document/compensation/" + id.String(), "Bearer t", false},
		{"default flag", ConsumerPrintInvoiceTask, []any{"7", "x"}, nil, "/document/invoice/7?send_receipt_email=False", "x", false},
		{"keyword flag", ConsumerPrintInvoiceTask, nil, celery.Dict{{Key: "invoice_id", Value: "7"}, {Key: "auth_token", Value: "x"},
			{Key: "send_receipt_email", Value: true}}, "/document/invoice/7?send_receipt_email=True", "x", false},
		{"none flag", ConsumerPrintInvoiceTask, []any{"7", "x", nil}, nil, "/document/invoice/7?send_receipt_email=None", "x", false},
		{"no token", ConsumerPrintMedicalAppointmentTask, []any{"m", nil}, nil, "/document/medical-appointment/m", nil, false},
		{"empty token", ConsumerPrintCustomerInvoiceTask, []any{"c", ""}, nil, "/document/einvoice/c", "", false},
		{"numeric token raises", ConsumerPrintCompensationTask, []any{"c", celery.Number("5")}, nil, "", false, false},
		{"leading blank raises", ConsumerPrintCompensationTask, []any{"c", " x"}, nil, "", false, false},
		{"trailing blank handed off", ConsumerPrintCompensationTask, []any{"c", "x "}, nil, "", nil, true},
		{"non-ASCII token handed off", ConsumerPrintCompensationTask, []any{"c", "è"}, nil, "", nil, true},
		{"newline token handed off", ConsumerPrintCompensationTask, []any{"c", "x\n"}, nil, "", nil, true},
		{"quoted id handed off", ConsumerPrintCompensationTask, []any{"a b", "x"}, nil, "", nil, true},
		{"dot segment handed off", ConsumerPrintCompensationTask, []any{"..", "x"}, nil, "", nil, true},
		{"integer id handed off", ConsumerPrintCompensationTask, []any{celery.Number("5"), "x"}, nil, "", nil, true},
		{"extra argument handed off", ConsumerPrintSubscriptionTask, []any{"a", "x", "y"}, nil, "", nil, true},
		{"missing argument handed off", ConsumerPrintSubscriptionTask, []any{"a"}, nil, "", nil, true},
		{"duplicate argument handed off", ConsumerPrintSubscriptionTask, []any{"a", "x"}, celery.Dict{{Key: "auth_token", Value: "y"}}, "", nil, true},
		{"unknown keyword handed off", ConsumerPrintSubscriptionTask, []any{"a", "x"}, celery.Dict{{Key: "other", Value: "y"}}, "", nil, true},
	}
	tasks := p.Tasks()
	if len(tasks) != 5 {
		t.Fatalf("tasks: %d", len(tasks))
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			request := consumerPrintRequest(c.args, c.kwargs)
			err := tasks[c.task].Plan(t.Context(), request)
			if c.handoff {
				if !errors.Is(err, celeryworker.ErrHandoff) {
					t.Fatalf("want handoff, got %v", err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			call := request.State.(consumerPrintCall)
			if c.auth == false {
				if call.send {
					t.Fatalf("want no request: %+v", call)
				}
				return
			}
			if !call.send || call.path != c.path {
				t.Fatalf("call %+v", call)
			}
			switch want := c.auth.(type) {
			case nil:
				if call.authorization != nil {
					t.Fatalf("want no Authorization, got %q", *call.authorization)
				}
			case string:
				if call.authorization == nil || *call.authorization != want {
					t.Fatalf("Authorization %v", call.authorization)
				}
			}
		})
	}
}

func TestConsumerB64Decode(t *testing.T) {
	cases := []struct {
		text, data, exc string
		ok              bool
	}{
		{"QUJD", "ABC", "", true}, {"QUI=", "AB", "", true}, {"QQ==", "A", "", true}, {"", "", "", true},
		{"QUJDR", "", "Invalid base64-encoded string: number of data characters (5) cannot be 1 more than a multiple of 4", true},
		{"QUJDRA", "", "Incorrect padding", true}, {"QUJDREU", "", "Incorrect padding", true},
		{"QQ=", "", "", false}, {"Q===", "", "", false}, {"QU JD", "", "", false}, {"QQ==QQ==", "", "", false}, {"QUJD\n", "", "", false},
	}
	for _, c := range cases {
		data, exc, ok := consumerB64Decode(c.text)
		if ok != c.ok {
			t.Fatalf("%q: ok %v", c.text, ok)
		}
		if !ok {
			continue
		}
		if c.exc != "" {
			if exc == nil || exc.Module != "binascii" || exc.Type != "Error" || exc.Args[0] != c.exc {
				t.Fatalf("%q: exc %+v", c.text, exc)
			}
			continue
		}
		if exc != nil || string(data) != c.data {
			t.Fatalf("%q: %q %+v", c.text, data, exc)
		}
	}
}

func TestConsumerPythonUUID(t *testing.T) {
	want := uuid.MustParse("12345678-1234-5678-1234-567812345678")
	for _, text := range []string{"12345678-1234-5678-1234-567812345678", "12345678123456781234567812345678", "{12345678-1234-5678-1234-567812345678}",
		"urn:uuid:12345678-1234-5678-1234-567812345678", "1234-5678-1234-5678-1234-5678-1234-5678", "uuid:12345678123456781234567812345678"} {
		id, ok, err := consumerPythonUUID(text)
		if err != nil || !ok || id != want {
			t.Fatalf("%q: %v %v %v", text, id, ok, err)
		}
	}
	for _, text := range []string{"", "not-a-uuid", "1234567812345678123456781234567", "123456781234567812345678123456789"} {
		if _, ok, err := consumerPythonUUID(text); ok || err != nil {
			t.Fatalf("%q should be invalid", text)
		}
	}
	if _, _, err := consumerPythonUUID(" 2345678123456781234567812345678"); !errors.Is(err, celeryworker.ErrHandoff) {
		t.Fatalf("int()-only form should be handed off: %v", err)
	}
}

type consumerTestAPIError struct{ code, message string }

func (e consumerTestAPIError) Error() string                 { return e.code }
func (e consumerTestAPIError) ErrorCode() string             { return e.code }
func (e consumerTestAPIError) ErrorMessage() string          { return e.message }
func (e consumerTestAPIError) ErrorFault() smithy.ErrorFault { return smithy.FaultClient }

func consumerTestResponseError(status int, err error) error {
	return &smithyhttp.ResponseError{Response: &smithyhttp.Response{Response: &http.Response{StatusCode: status}}, Err: err}
}

func TestConsumerStorageException(t *testing.T) {
	head := ConsumerStorageException("HeadObject", consumerTestResponseError(403, consumerTestAPIError{"Forbidden", ""}))
	if head.Type != "ClientError" || head.Module != "botocore.exceptions" ||
		head.Args[0] != "An error occurred (403) when calling the HeadObject operation: Forbidden" {
		t.Fatalf("head: %+v", head)
	}
	put := ConsumerStorageException("PutObject", consumerTestResponseError(404,
		consumerTestAPIError{"NoSuchBucket", "The specified bucket does not exist"}))
	if put.Type != "NoSuchBucket" || put.Module != "botocore.errorfactory" ||
		put.Args[0] != "An error occurred (NoSuchBucket) when calling the PutObject operation: The specified bucket does not exist" {
		t.Fatalf("put: %+v", put)
	}
	denied := ConsumerStorageException("PutObject", consumerTestResponseError(403, consumerTestAPIError{"AccessDenied", ""}))
	if denied.Type != "ClientError" || denied.Args[0] != "An error occurred (AccessDenied) when calling the PutObject operation: Unknown" {
		t.Fatalf("denied: %+v", denied)
	}
	for _, err := range []error{errors.New("dial"), consumerTestResponseError(503, consumerTestAPIError{"SlowDown", "x"}),
		consumerTestResponseError(400, consumerTestAPIError{"RequestTimeout", "x"})} {
		if ConsumerStorageException("PutObject", err) != consumerStorageFailure {
			t.Fatalf("%v should be the fixed storage failure", err)
		}
	}
}
