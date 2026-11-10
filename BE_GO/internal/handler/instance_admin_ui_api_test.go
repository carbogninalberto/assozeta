package handler

import (
	"net/http/httptest"
	"reflect"
	"testing"
)

func TestInstanceAdminChoiceMessages(t *testing.T) {
	for language, want := range map[string]string{
		"en": `"v3" is not a valid choice.`,
		"it": `"v3" non è una scelta valida.`,
	} {
		request := httptest.NewRequest("PUT", "/instance/admin", nil)
		request.Header.Set("Accept-Language", language)
		got := instanceAdminMessages(request, map[string]any{"uiApiVersion": []string{"choice:v3"}})
		if !reflect.DeepEqual(got, map[string]any{"uiApiVersion": []string{want}}) {
			t.Fatalf("%s: %#v", language, got)
		}
	}
}
