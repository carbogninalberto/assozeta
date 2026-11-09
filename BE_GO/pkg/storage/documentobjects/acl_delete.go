package documentobjects

import (
	"context"
	"errors"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	smithyhttp "github.com/aws/smithy-go/transport/http"
)

// PutACL is boto3's put_object_acl(Bucket, Key, ACL=acl): one PutObjectAcl
// with a canned ACL (Subscription.set_signature_from_base64 with
// AWS_S3_USE_OBJECT_ACL).
func (s *S3) PutACL(ctx context.Context, key, acl string) error {
	client, err := s.client(ctx)
	if err != nil {
		return err
	}
	_, err = client.PutObjectAcl(ctx, &s3.PutObjectAclInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key),
		ACL: types.ObjectCannedACL(acl)})
	return err
}

// Delete is S3Storage.delete(name) for the key of name: one DeleteObject;
// an HTTP 404 is success, like django-storages.
func (s *S3) Delete(ctx context.Context, key string) error {
	client, err := s.client(ctx)
	if err != nil {
		return err
	}
	_, err = client.DeleteObject(ctx, &s3.DeleteObjectInput{Bucket: aws.String(s.options.Bucket), Key: aws.String(key)})
	var response *smithyhttp.ResponseError
	if err != nil && ctx.Err() == nil && errors.As(err, &response) && response.HTTPStatusCode() == 404 {
		return nil
	}
	return err
}
