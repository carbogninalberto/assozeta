package handler

import (
	"io"
	"mime/multipart"
	"net/http/httptest"
	"net/textproto"
	"os"
	"strings"
	"testing"

	"github.com/labstack/echo/v4"
)

// Use a sparse file so the test never allocates an archive-sized byte slice.
func TestRestoreStagesLargeMultipartOnDisk(t *testing.T) {
	const size int64 = 65 << 20
	source, err := os.CreateTemp(t.TempDir(), "multipart")
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	form := multipart.NewWriter(source)
	part, err := form.CreateFormFile("file", "archive.zip")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.WriteString(part, "PK"); err != nil {
		t.Fatal(err)
	}
	if _, err := source.Seek(size-3, io.SeekCurrent); err != nil {
		t.Fatal(err)
	}
	if _, err := io.WriteString(part, "!"); err != nil {
		t.Fatal(err)
	}
	if err := form.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := source.Seek(0, io.SeekStart); err != nil {
		t.Fatal(err)
	}

	req := httptest.NewRequest("POST", "/instance/admin/data-restore", source)
	req.Header.Set("Content-Type", form.FormDataContentType())
	c := echo.New().NewContext(req, httptest.NewRecorder())
	path, err := (&DataRestoreHandler{}).uploadedFile(c, t.TempDir(), size)
	if err != nil {
		t.Fatal(err)
	}
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || info.Size() != size {
		t.Fatalf("staged size: %v, %v", info, err)
	}
	var tail [1]byte
	if _, err := file.ReadAt(tail[:], size-1); err != nil || tail[0] != '!' {
		t.Fatalf("archive tail: %q, %v", tail, err)
	}
}

func TestRestoreMultipartKeepsLastCompleteFileWithinCombinedLimit(t *testing.T) {
	var body strings.Builder
	form := multipart.NewWriter(&body)
	for _, data := range []string{"YQ==", "Yg==", "Y2M="} {
		part, err := form.CreatePart(textproto.MIMEHeader{
			"Content-Disposition":       {`form-data; name="file"; filename="encoded.zip"`},
			"Content-Transfer-Encoding": {"base64"},
		})
		if err != nil {
			t.Fatal(err)
		}
		if _, err := io.WriteString(part, data); err != nil {
			t.Fatal(err)
		}
	}
	if err := form.Close(); err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest("POST", "/instance/admin/data-restore", strings.NewReader(body.String()))
	req.Header.Set("Content-Type", form.FormDataContentType())
	path, err := (&DataRestoreHandler{}).uploadedFile(echo.New().NewContext(req, httptest.NewRecorder()), t.TempDir(), 3)
	if err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(path)
	if err != nil || string(got) != "b" {
		t.Fatalf("staged %q, %v", got, err)
	}
}
