package service

import (
	"errors"

	"github.com/aws/aws-sdk-go-v2/aws/retry"
	"github.com/aws/smithy-go"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// botocoreRetriedCodes are the S3 error codes botocore retries (legacy retry
// mode): its answer then carries " (reached max retries: N)", which the port
// does not reproduce, as it does not reproduce a transport error either. The
// S3 region redirector retries the location codes in the same way.
var botocoreRetriedCodes = map[string]bool{"RequestTimeout": true, "RequestTimeoutException": true, "Throttling": true,
	"ThrottlingException": true, "ThrottledException": true, "SlowDown": true, "RequestLimitExceeded": true,
	"TooManyRequestsException": true, "ProvisionedThroughputExceededException": true, "TransactionInProgressException": true,
	"PriorRequestNotComplete": true, "BandwidthLimitExceeded": true, "EC2ThrottledException": true,
	"RequestThrottled": true, "RequestThrottledException": true, "BadDigest": true,
	"AuthorizationHeaderMalformed": true, "PermanentRedirect": true, "IllegalLocationConstraintException": true}

// botocoreClientErrorText is str() of the ClientError botocore raises for a
// storage answer neither client retries: "An error occurred (<code>) when
// calling the <operation> operation: <message>". ok=false for the rest
// (transport errors, retried answers, cancelled contexts), which stay with
// Django.
func botocoreClientErrorText(err error) (string, bool) {
	var head *documentobjects.HeadStatusError
	if errors.As(err, &head) {
		// Exists already turned the client-error answer into botocore's text.
		return head.Error(), true
	}
	var op *smithy.OperationError
	if !errors.As(err, &op) || op.OperationName == "HeadObject" {
		// A raw HeadObject error is not reproduced: only HeadStatusError is.
		return "", false
	}
	var maxAttempts *retry.MaxAttemptsError
	if errors.As(err, &maxAttempts) {
		return "", false
	}
	var response *smithyhttp.ResponseError
	if !errors.As(err, &response) {
		return "", false
	}
	if status := response.HTTPStatusCode(); status < 400 || status > 499 || status == 408 || status == 429 {
		return "", false
	}
	var api smithy.APIError
	if !errors.As(err, &api) || api.ErrorCode() == "" || botocoreRetriedCodes[api.ErrorCode()] {
		return "", false
	}
	message := api.ErrorMessage()
	if message == "" {
		message = "Unknown"
	}
	return "An error occurred (" + api.ErrorCode() + ") when calling the " + op.OperationName + " operation: " + message, true
}

// documentSaveFailureText is the text of default_storage.save's exception for
// content of size bytes. Content of multipart_threshold bytes and up goes
// through s3transfer's multipart operations, which boto3 names in the text:
// Go stores it in one PutObject, so those answers stay with Django.
func documentSaveFailureText(err error, size int) (string, bool) {
	if size >= consumerStorageMultipartThreshold {
		var head *documentobjects.HeadStatusError
		if !errors.As(err, &head) {
			return "", false
		}
	}
	return botocoreClientErrorText(err)
}
