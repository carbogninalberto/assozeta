package dto

import (
	"errors"
	"testing"
)

func TestTestimonialInputMatchesPython(t *testing.T) {
	parse := func(raw string) any {
		value, err := ParseMessageMutationsJSON([]byte(raw))
		if err != nil {
			t.Fatal(raw, err)
		}
		return value
	}
	for raw, want := range map[string]TestimonialInput{
		`{"text": "Ottimo", "score": 5}`:                                   {"Ottimo", 5},
		`{"score": true, "text": 123}`:                                     {"123", 1},
		`{"text": 1e16, "score": 1.0}`:                                     {"1e+16", 1},
		`{"text": 1e400, "score": 2}`:                                      {"inf", 2},
		`{"text": [0.1, -0.0], "score": 3}`:                                {"[0.1, -0.0]", 3},
		`{"text": {"a": [1, "b'c", null, false], "z": "x\n"}, "score": 3}`: {`{'a': [1, "b'c", None, False], 'z': 'x\n'}`, 3},
		`{"text": ["it's \"q\"", " è\u0007\ud800"], "score": 4}`:           {`['it\'s "q"', '\xa0è\x07\ud800']`, 4},
		`{"text": "a", "text": "b", "score": 2}`:                           {"b", 2},
	} {
		got, err := ParseTestimonialInput(parse(raw))
		if err != nil || got != want {
			t.Errorf("%s = %#v %v, want %#v", raw, got, err, want)
		}
	}
	for raw, want := range map[string]error{
		`{"score": 3}`: ErrTestimonialMissing, `[]`: ErrTestimonialMissing, `"textscor"`: ErrTestimonialMissing,
		`{"text": "x", "score": 0}`: ErrTestimonialScore, `{"text": "x", "score": false}`: ErrTestimonialScore,
		`{"text": "x", "score": 5.0001}`: ErrTestimonialScore, `{"text": "x", "score": 1000000000000000000000000000000}`: ErrTestimonialScore,
		`{"text": "x", "score": -1e400}`: ErrTestimonialScore,
		`{"text": "x", "score": "3"}`:    ErrTestimonialServer, `{"text": null, "score": 3}`: ErrTestimonialServer,
		`["text", "score"]`: ErrTestimonialServer, `"textscore"`: ErrTestimonialServer, `5`: ErrTestimonialServer,
		`null`: ErrTestimonialServer, `{"text": "x", "score": [3]}`: ErrTestimonialServer,
	} {
		if _, err := ParseTestimonialInput(parse(raw)); !errors.Is(err, want) {
			t.Errorf("%s = %v, want %v", raw, err, want)
		}
	}
}

func TestTestimonialWebhookMatchesPython(t *testing.T) {
	parse := func(raw string) any {
		value, err := ParseMessageMutationsJSON([]byte(raw))
		if err != nil {
			t.Fatal(raw, err)
		}
		return value
	}
	// Expected values are CPython json.dumps(json.loads(raw), separators=(',', ':')).
	for raw, want := range map[string]string{
		`{"z": "Grazie è 😀", "n": 1.50, "e": 1e5, "z": "ultimo", "l": [true, null, -0.0, 10000000000000000000000]}`: `{"z":"ultimo","n":1.5,"e":100000.0,"l":[true,null,-0.0,10000000000000000000000]}`,
		`"type"`: `"type"`, `null`: `null`, `[]`: `[]`, `{"a": 1e400}`: `{"a":Infinity}`,
	} {
		if got := string(PythonJSONDumpsCompact(parse(raw))); got != want {
			t.Errorf("%s = %s, want %s", raw, got, want)
		}
	}
	created := `{"type": "testimonial_created", "data": {"new": {"endorser": {"custom_data": {"sport-association-id": "x"}}}}}`
	if got, err := ParseTestimonialWebhook(parse(created)); err != nil || !got.Created || got.Malformed || got.Association != "x" {
		t.Fatalf("created %#v %v", got, err)
	}
	for raw, want := range map[string]TestimonialWebhook{
		`{"type": "other"}`: {}, `{"type": 1}`: {}, `{"type": "testimonial_created", "data": []}`: {Created: true, Malformed: true},
		`{"type": "testimonial_created", "data": {"new": "x"}}`: {Created: true, Malformed: true},
	} {
		if got, err := ParseTestimonialWebhook(parse(raw)); err != nil || got != want {
			t.Errorf("%s = %#v %v", raw, got, err)
		}
	}
	for raw, want := range map[string]error{`{"data": {}}`: ErrTestimonialWebhookType, `[]`: ErrTestimonialWebhookType,
		`["type"]`: ErrTestimonialServer, `"type"`: ErrTestimonialServer, `null`: ErrTestimonialServer, `3`: ErrTestimonialServer} {
		if _, err := ParseTestimonialWebhook(parse(raw)); !errors.Is(err, want) {
			t.Errorf("%s = %v, want %v", raw, err, want)
		}
	}
}
