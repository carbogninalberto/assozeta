package instancelogo

import (
	"bytes"
	"context"
	"errors"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	smithyhttp "github.com/aws/smithy-go/transport/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
)

// WriteParameters are S3Storage._get_write_parameters for an upload:
// AWS_S3_OBJECT_PARAMETERS' CacheControl, the uploaded file's content type
// and AWS_DEFAULT_ACL when set.
type WriteParameters struct {
	ContentType, CacheControl string
	ACL                       *string
}

// Delete is S3Storage.delete: a missing object is not an error.
func (s *S3) Delete(ctx context.Context, name string) error {
	release, err := s.admission.acquire(ctx)
	if err != nil {
		return err
	}
	defer release()
	key, err := s.key(name)
	if err != nil {
		return err
	}
	client, err := s.client(ctx)
	if err != nil {
		return err
	}
	handoff.MarkEffect(ctx, "object storage write")
	_, err = client.DeleteObject(ctx, &s3.DeleteObjectInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key)})
	var response *smithyhttp.ResponseError
	if err != nil && errors.As(err, &response) && response.HTTPStatusCode() == 404 {
		return nil
	}
	return err
}

// Save is S3Storage._save for a body already read: one PutObject with the
// write parameters (uploads below the 8 MiB multipart threshold).
func (s *S3) Save(ctx context.Context, name string, body []byte, parameters WriteParameters) error {
	release, err := s.admission.acquire(ctx)
	if err != nil {
		return err
	}
	defer release()
	key, err := s.key(name)
	if err != nil {
		return err
	}
	client, err := s.client(ctx)
	if err != nil {
		return err
	}
	input := &s3.PutObjectInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key), Body: bytes.NewReader(body),
		ContentLength: aws.Int64(int64(len(body))), ContentType: aws.String(parameters.ContentType)}
	if parameters.CacheControl != "" {
		input.CacheControl = aws.String(parameters.CacheControl)
	}
	if parameters.ACL != nil {
		input.ACL = types.ObjectCannedACL(*parameters.ACL)
	}
	handoff.MarkEffect(ctx, "object storage write")
	_, err = client.PutObject(ctx, input)
	return err
}
