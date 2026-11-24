package service

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/aws/smithy-go"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/pymime"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// ConsumerSaveToStorageTask is docmanager.tasks.save_to_storage's Celery name.
const ConsumerSaveToStorageTask = "save_to_storage"

const consumerStorageMultipartThreshold = documentobjects.MultipartThreshold

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

type consumerMultipartObjects interface {
	RecoverMultipart(ctx context.Context, key, uploadID, taskID string) (bool, error)
	PutTaskMultipart(ctx context.Context, key string, body []byte, contentType, contentEncoding, taskID string,
		checkpoint func(string) error, partUploaded func()) error
}

// ConsumerSaveToStorage runs save_to_storage(file_path, pdf_base64):
//
//	content = base64.b64decode(pdf_base64)
//	default_storage.save(file_path, ContentFile(content))
//
// re-raising any exception (no retry policy). Handed off before any effect:
// non-string arguments, base64 text outside the subset below, names with
// backslashes, NUL or invalid UTF-8. With the handoff ledger, uploads support
// Django's alternate names and multipart uploads with a recovery checkpoint.
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
	coordinated := celeryworker.HasBrokerAttempt(ctx)
	if len(content) >= consumerStorageMultipartThreshold && !coordinated {
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
		// Resolve the available name before writing anything to storage.
		exists, err := s.objects.Exists(ctx, key)
		if err != nil {
			call.exc = ConsumerStorageException("HeadObject", err)
		} else if exists {
			if !coordinated {
				return celeryworker.ErrHandoff
			}
			available, err := documentkey.AvailableName(ctx, cleaned, func(ctx context.Context, candidate string) (bool, error) {
				candidateKey, err := documentkey.S3Key(candidate, s.objects.Location())
				if err != nil {
					return false, err
				}
				return s.objects.Exists(ctx, candidateKey)
			}, nil)
			if err == nil {
				call.key, err = documentkey.S3Key(available, s.objects.Location())
			}
			if err != nil {
				call.exc = ConsumerStorageException("HeadObject", err)
			}
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
	if len(call.content) >= consumerStorageMultipartThreshold {
		return nil, s.putMultipart(ctx, request.ID, call)
	}
	if err := s.objects.Put(ctx, call.key, call.content, call.kind, call.encoding); err != nil {
		return nil, ConsumerStorageException("PutObject", err)
	}
	return nil, nil
}

func (s *ConsumerSaveToStorage) putMultipart(ctx context.Context, taskID string, call consumerSaveCall) error {
	db, attempt := celeryworker.ActiveBrokerAttempt(ctx)
	objects, ok := s.objects.(consumerMultipartObjects)
	if !ok || db == nil {
		return errors.New("multipart storage is unavailable")
	}
	key := taskID + ":storage:multipart"
	var stored string
	err := db.QueryRowContext(ctx, `SELECT coalesce(
		(SELECT provider_ref FROM public.assozeta_handoff_effects WHERE effect_key=$1), '')`, key).Scan(&stored)
	if err != nil {
		return err
	}
	if stored != "" {
		var previous [2]string
		if err := json.Unmarshal([]byte(stored), &previous); err != nil || previous[0] == "" || previous[1] == "" {
			return errors.New("invalid multipart checkpoint")
		}
		complete, err := objects.RecoverMultipart(ctx, previous[0], previous[1], taskID)
		if err != nil {
			return ConsumerStorageException("AbortMultipartUpload", err)
		}
		if complete {
			return nil
		}
	}
	err = objects.PutTaskMultipart(ctx, call.key, call.content, call.kind, call.encoding, taskID, func(uploadID string) error {
		ref, err := json.Marshal([2]string{call.key, uploadID})
		if err != nil {
			return err
		}
		// This receipt confirms that S3 created the upload. The object's
		// task metadata is the receipt for completing it.
		_, err = db.ExecContext(ctx, `WITH fenced AS MATERIALIZED (SELECT public.assozeta_handoff_fence($1))
			INSERT INTO public.assozeta_handoff_effects (effect_key, task_id, attempt_id, kind, state, provider_ref)
			SELECT $2, $3, $1, 'storage_multipart', 'confirmed', $4 FROM fenced
			ON CONFLICT (effect_key) DO UPDATE SET attempt_id=EXCLUDED.attempt_id, provider_ref=EXCLUDED.provider_ref`,
			attempt, key, taskID, string(ref))
		return err
	}, func() { handoff.Barrier("storage:part_uploaded", taskID) })
	if err != nil {
		return ConsumerStorageException("UploadPart", err)
	}
	return nil
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
		if botocoreRetriedCodes[code] {
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
