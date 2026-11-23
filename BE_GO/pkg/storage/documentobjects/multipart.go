package documentobjects

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	smithyhttp "github.com/aws/smithy-go/transport/http"
)

const MultipartThreshold = 8 << 20
const multipartTaskMetadata = "assozeta-task-id"

// RecoverMultipart recognizes a completed upload of this task, or aborts
// exactly the upload recorded by the previous attempt.
func (s *S3) RecoverMultipart(ctx context.Context, key, uploadID, taskID string) (bool, error) {
	client, err := s.client(ctx)
	if err != nil {
		return false, err
	}
	bucket := aws.String(s.options.Bucket)
	output, err := client.HeadObject(ctx, &s3.HeadObjectInput{Bucket: bucket, Key: &key})
	if err == nil && output.Metadata[multipartTaskMetadata] == taskID {
		return true, nil
	}
	var response *smithyhttp.ResponseError
	if err != nil && (!errors.As(err, &response) || response.HTTPStatusCode() != http.StatusNotFound) {
		return false, err
	}
	return false, abortMultipart(ctx, client, bucket, &key, &uploadID)
}

func (s *S3) PutMultipart(ctx context.Context, key string, body []byte, upload Upload, taskID string,
	checkpoint func(string) error, partUploaded func()) error {
	client, err := s.client(ctx)
	if err != nil {
		return err
	}
	bucket := aws.String(s.options.Bucket)
	input := &s3.CreateMultipartUploadInput{Bucket: bucket, Key: &key, ContentType: &upload.ContentType,
		Metadata: map[string]string{multipartTaskMetadata: taskID}, ACL: types.ObjectCannedACL(upload.ACL)}
	if upload.CacheControl != "" {
		input.CacheControl = &upload.CacheControl
	}
	if upload.ContentEncoding != "" {
		input.ContentEncoding = &upload.ContentEncoding
	}
	started, err := client.CreateMultipartUpload(ctx, input)
	if err != nil {
		return err
	}
	completed := false
	defer func() {
		if !completed {
			cleanup, cancel := context.WithTimeout(context.WithoutCancel(ctx), 30*time.Second)
			defer cancel()
			_ = abortMultipart(cleanup, client, bucket, &key, started.UploadId)
		}
	}()
	if err := checkpoint(aws.ToString(started.UploadId)); err != nil {
		return err
	}
	parts := make([]types.CompletedPart, 0, (len(body)+MultipartThreshold-1)/MultipartThreshold)
	for start := 0; start < len(body); start += MultipartThreshold {
		part := body[start:min(start+MultipartThreshold, len(body))]
		number := int32(len(parts) + 1)
		output, err := client.UploadPart(ctx, &s3.UploadPartInput{Bucket: bucket, Key: &key, UploadId: started.UploadId,
			PartNumber: &number, Body: bytes.NewReader(part), ContentLength: aws.Int64(int64(len(part)))})
		if err != nil {
			return err
		}
		parts = append(parts, types.CompletedPart{PartNumber: &number, ETag: output.ETag})
		if number == 1 {
			partUploaded()
		}
	}
	_, err = client.CompleteMultipartUpload(ctx, &s3.CompleteMultipartUploadInput{Bucket: bucket, Key: &key,
		UploadId: started.UploadId, MultipartUpload: &types.CompletedMultipartUpload{Parts: parts}})
	completed = err == nil
	return err
}

func abortMultipart(ctx context.Context, client *s3.Client, bucket, key, uploadID *string) error {
	_, err := client.AbortMultipartUpload(ctx, &s3.AbortMultipartUploadInput{Bucket: bucket, Key: key, UploadId: uploadID})
	var missing *types.NoSuchUpload
	if errors.As(err, &missing) {
		return nil
	}
	return err
}
