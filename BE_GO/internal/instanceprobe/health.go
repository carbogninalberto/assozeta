package instanceprobe

import (
	"context"
	"io"
	"net/http"
	"time"
)

// healthBody is response.raw.read(8193, ...): one byte past an 8 KiB document.
const healthBody = 8193

// Health is diagnostic_probe.health_response(url, expected_version):
// requests.get(url, timeout=(3, 3), allow_redirects=False, stream=True) with
// requests' default headers; true only for a 200 whose first 8193 decoded
// body bytes are a JSON object with "status" == "ok" (and "version" ==
// expected when expected is not nil). Any exception inside (connection,
// timeout, decoding, a body that is not an object) is an error: the probe's
// generic failure. A document json.loads would read as UTF-16/UTF-32 is an
// error too (requests' health endpoints answer UTF-8).
func (c *Client) Health(ctx context.Context, target string, expected *string) (bool, error) {
	transport := c.transport(3*time.Second, 3*time.Second, true)
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return false, err
	}
	request.Header.Set("User-Agent", "python-requests/"+RequestsVersion)
	request.Header.Set("Accept-Encoding", "gzip, deflate")
	request.Header.Set("Accept", "*/*")
	request.Header.Set("Connection", "keep-alive")
	response, err := client.Do(request)
	if err != nil {
		return false, err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return false, nil
	}
	reader, err := decodedBody(response)
	if err != nil {
		return false, err
	}
	body, err := io.ReadAll(io.LimitReader(reader, healthBody))
	if err != nil {
		return false, err
	}
	value, err := LoadJSON(body)
	if err != nil {
		return false, err
	}
	data, ok := value.(map[string]any)
	if !ok {
		return false, errProbe // data.get raises AttributeError
	}
	if status, ok := data["status"].(string); !ok || status != "ok" {
		return false, nil
	}
	if expected == nil {
		return true, nil
	}
	version, isText := data["version"].(string)
	return isText && version == *expected, nil
}
