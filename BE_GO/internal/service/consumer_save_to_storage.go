package service

import (
	"context"
	"encoding/base64"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/aws/smithy-go"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// ConsumerSaveToStorageTask is docmanager.tasks.save_to_storage's Celery name.
const ConsumerSaveToStorageTask = "save_to_storage"

// consumerStorageMultipartThreshold is s3transfer's multipart_threshold
// (8 MiB): upload_fileobj splits content of that size or more into parts.
const consumerStorageMultipartThreshold = 8 << 20

// ConsumerStorageObjects is the S3 default storage the consumed tasks write:
// S3Storage.exists (HeadObject, 404 is absent), one PutObject with
// _get_write_parameters (ContentType/ContentEncoding from
// mimetypes.guess_type, AWS_S3_OBJECT_PARAMETERS' CacheControl,
// AWS_DEFAULT_ACL), and the GetObject read of S3File.
type ConsumerStorageObjects interface {
	Location() string
	Exists(ctx context.Context, key string) (bool, error)
	Put(ctx context.Context, key string, body []byte, contentType, contentEncoding string) error
	Read(ctx context.Context, key string) ([]byte, error)
}

// ConsumerSaveToStorage runs save_to_storage(file_path, pdf_base64):
//
//	content = base64.b64decode(pdf_base64)
//	default_storage.save(file_path, ContentFile(content))
//
// re-raising any exception (no retry policy). Handed off before any effect:
// non-string arguments, base64 text outside the subset below, names with
// backslashes, NUL or invalid UTF-8, content of 8 MiB or more (multipart
// upload), and a name that already exists without AWS_S3_FILE_OVERWRITE
// (get_available_name would store under a random alternative name).
type ConsumerSaveToStorage struct {
	objects   ConsumerStorageObjects
	overwrite bool
}

func NewConsumerSaveToStorage(objects ConsumerStorageObjects, overwrite bool) *ConsumerSaveToStorage {
	return &ConsumerSaveToStorage{objects: objects, overwrite: overwrite}
}

func (s *ConsumerSaveToStorage) Task() celeryworker.Task {
	return celeryworker.Task{MaxRetries: 3, Plan: s.plan, Run: s.run}
}

type consumerSaveCall struct {
	exc                 *celeryworker.Exception
	key, kind, encoding string
	content             []byte
}

// consumerB64Decode is base64.b64decode(text) (binascii.a2b_base64, not
// strict) for ASCII text: standard alphabet with correct padding decodes;
// alphabet-only text of the wrong length raises binascii.Error with
// binascii's message; ok=false is text outside that subset.
func consumerB64Decode(text string) (data []byte, exc *celeryworker.Exception, ok bool) {
	body := strings.TrimRight(text, "=")
	padding := len(text) - len(body)
	for _, r := range []byte(body) {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '+' || r == '/') {
			return nil, nil, false
		}
	}
	binascii := func(message string) *celeryworker.Exception {
		return &celeryworker.Exception{Type: "Error", Module: "binascii", Args: []any{message}}
	}
	if padding == 0 {
		switch len(body) % 4 {
		case 1:
			return nil, binascii("Invalid base64-encoded string: number of data characters (" + strconv.Itoa(len(body)) +
				") cannot be 1 more than a multiple of 4"), true
		case 2, 3:
			return nil, binascii("Incorrect padding"), true
		}
	} else if len(text)%4 != 0 || padding > 2 || len(body)%4 < 2 {
		return nil, nil, false
	}
	data, err := base64.StdEncoding.DecodeString(text)
	if err != nil {
		return nil, nil, false
	}
	return data, nil, true
}

func (s *ConsumerSaveToStorage) plan(ctx context.Context, request *celeryworker.Request) error {
	values, err := consumerPrintBind([]string{"file_path", "pdf_base64"}, 2, request)
	if err != nil {
		return err
	}
	name, nameOK := values["file_path"].(string)
	text, textOK := values["pdf_base64"].(string)
	if !nameOK || !textOK {
		return celeryworker.ErrHandoff
	}
	content, exc, ok := consumerB64Decode(text)
	if !ok {
		return celeryworker.ErrHandoff
	}
	if exc != nil {
		request.State = consumerSaveCall{exc: exc}
		return nil
	}
	if len(content) >= consumerStorageMultipartThreshold {
		return celeryworker.ErrHandoff
	}
	cleaned, err := documentkey.SaveName(name)
	var suspicious *documentkey.SuspiciousFileError
	if errors.As(err, &suspicious) {
		request.State = consumerSaveCall{exc: &celeryworker.Exception{Type: "SuspiciousFileOperation", Module: "django.core.exceptions",
			Args: []any{suspicious.Message}}}
		return nil
	}
	if err != nil {
		return celeryworker.ErrHandoff
	}
	key, err := documentkey.S3Key(cleaned, s.objects.Location())
	if err != nil {
		return celeryworker.ErrHandoff
	}
	kind, known, encoding, err := pymime.GuessType(key)
	if err != nil {
		return celeryworker.ErrHandoff
	}
	if !known {
		kind = "application/octet-stream"
	}
	call := consumerSaveCall{key: key, kind: kind, encoding: encoding, content: content}
	if !s.overwrite {
		// get_available_name's exists check is a read: done here so that a taken
		// name is handed off before any effect.
		exists, err := s.objects.Exists(ctx, key)
		if err != nil {
			call.exc = ConsumerStorageException("HeadObject", err)
		} else if exists {
			return celeryworker.ErrHandoff
		}
	}
	request.State = call
	return nil
}

func (s *ConsumerSaveToStorage) run(ctx context.Context, request *celeryworker.Request) (any, error) {
	call, _ := request.State.(consumerSaveCall)
	if call.exc != nil {
		return nil, call.exc
	}
	if err := s.objects.Put(ctx, call.key, call.content, call.kind, call.encoding); err != nil {
		return nil, ConsumerStorageException("PutObject", err)
	}
	return nil, nil
}

// consumerBotocoreModeled are the S3 error codes botocore's error factory
// models as classes (botocore.errorfactory.<Code>); other codes raise
// botocore.exceptions.ClientError.
var consumerBotocoreModeled = map[string]bool{"BucketAlreadyExists": true, "BucketAlreadyOwnedByYou": true, "EncryptionTypeMismatch": true,
	"IdempotencyParameterMismatch": true, "InvalidObjectState": true, "InvalidRequest": true, "InvalidWriteOffset": true, "NoSuchBucket": true,
	"NoSuchKey": true, "NoSuchUpload": true, "ObjectAlreadyInActiveTierError": true, "ObjectNotInActiveTierError": true, "TooManyParts": true}

// consumerStorageFailure stands for a storage error the port does not
// reproduce exactly (network errors, 5xx and throttling responses, which
// botocore retries and reports with the retry count): the text is fixed.
var consumerStorageFailure = &celeryworker.Exception{Type: "ClientError", Module: "botocore.exceptions", Args: []any{"storage error"}}

// ConsumerStorageException is the botocore exception boto3 raises for an S3
// client error response that is not retried (4xx other than throttling and
// request timeouts): "An error occurred (<code>) when calling the <operation>
// operation: <message>". A HEAD response has no body, so botocore's code is
// the status and its message the reason phrase.
func ConsumerStorageException(operation string, err error) *celeryworker.Exception {
	var head *documentobjects.HeadStatusError
	if operation == "HeadObject" && errors.As(err, &head) {
		// Exists already turned the client-error answer into botocore's text.
		return &celeryworker.Exception{Type: "ClientError", Module: "botocore.exceptions", Args: []any{head.Error()}}
	}
	var response *smithyhttp.ResponseError
	if !errors.As(err, &response) {
		return consumerStorageFailure
	}
	status := response.HTTPStatusCode()
	if status < 400 || status > 499 || status == 408 || status == 429 {
		return consumerStorageFailure
	}
	code, message := strconv.Itoa(status), http.StatusText(status)
	if operation != "HeadObject" {
		var api smithy.APIError
		if !errors.As(err, &api) || api.ErrorCode() == "" {
			return consumerStorageFailure
		}
		code, message = api.ErrorCode(), api.ErrorMessage()
		switch code {
		case "RequestTimeout", "RequestTimeoutException", "Throttling", "ThrottlingException", "SlowDown", "RequestLimitExceeded",
			"TooManyRequestsException", "ProvisionedThroughputExceededException", "TransactionInProgressException", "PriorRequestNotComplete",
			"BandwidthLimitExceeded", "EC2ThrottledException", "RequestThrottled", "RequestThrottledException":
			return consumerStorageFailure
		}
	}
	if message == "" {
		message = "Unknown"
	}
	class := &celeryworker.Exception{Type: "ClientError", Module: "botocore.exceptions",
		Args: []any{"An error occurred (" + code + ") when calling the " + operation + " operation: " + message}}
	if operation != "HeadObject" && consumerBotocoreModeled[code] {
		class.Type, class.Module = code, "botocore.errorfactory"
	}
	return class
}
