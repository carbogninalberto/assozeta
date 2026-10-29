package s3credentials

import (
	"context"
	"strings"

	"github.com/aws/smithy-go/middleware"
	smithyhttp "github.com/aws/smithy-go/transport/http"
)

type metadataPathKey struct{}
type metadataPath struct{ path, escaped string }

// The pinned IMDS serializer replaces the endpoint path with /latest/..., while
// Botocore appends to the configured base URL. Preserve that base for metadata
// and token requests only; STS and other provider requests retain SDK behavior.
func preserveMetadataPath(stack *middleware.Stack) error {
	if stack.ID() != "GetMetadata" && stack.ID() != "getToken" {
		return nil
	}
	err := stack.Serialize.Insert(middleware.SerializeMiddlewareFunc("CaptureMetadataBasePath", func(ctx context.Context, in middleware.SerializeInput, next middleware.SerializeHandler) (middleware.SerializeOutput, middleware.Metadata, error) {
		request, ok := in.Request.(*smithyhttp.Request)
		if !ok {
			return middleware.SerializeOutput{}, middleware.Metadata{}, ErrCredentials
		}
		base := metadataPath{path: request.URL.Path, escaped: request.URL.EscapedPath()}
		return next.HandleSerialize(context.WithValue(ctx, metadataPathKey{}, base), in)
	}), "OperationSerializer", middleware.Before)
	if err != nil {
		return err
	}
	return stack.Build.Add(middleware.BuildMiddlewareFunc("RestoreMetadataBasePath", func(ctx context.Context, in middleware.BuildInput, next middleware.BuildHandler) (middleware.BuildOutput, middleware.Metadata, error) {
		base, ok := ctx.Value(metadataPathKey{}).(metadataPath)
		if ok && base.path != "" {
			request, ok := in.Request.(*smithyhttp.Request)
			if !ok {
				return middleware.BuildOutput{}, middleware.Metadata{}, ErrCredentials
			}
			escaped := strings.TrimSuffix(base.escaped, "/") + request.URL.EscapedPath()
			request.URL.Path = strings.TrimSuffix(base.path, "/") + request.URL.Path
			request.URL.RawPath = escaped
		}
		return next.HandleBuild(ctx, in)
	}), middleware.Before)
}
