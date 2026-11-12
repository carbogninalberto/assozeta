package handler

import (
	"reflect"
	"testing"
)

// parse_qs('token=&token=a+b%20c&x&impersonation=%E2%82%AC%ZZ&=v&k=%') on CPython 3.13.16.
func TestParseQS(t *testing.T) {
	got := parseQS("token=&token=a+b%20c&x&impersonation=%E2%82%AC%ZZ&=v&k=%")
	want := map[string][]string{"token": {"a b c"}, "impersonation": {"€%ZZ"}, "": {"v"}, "k": {"%"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v", got)
	}
}
