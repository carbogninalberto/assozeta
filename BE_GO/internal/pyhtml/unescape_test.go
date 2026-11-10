package pyhtml

import "testing"

// Vectors are Python 3.13 html.unescape outputs.
func TestUnescapeMatchesPython(t *testing.T) {
	for _, item := range [][2]string{
		{"Rossi & Bianchi.pdf", "Rossi & Bianchi.pdf"},
		{"a&amp;b", "a&b"},
		{"a&ampb", "a&b"},
		{"x&#38;y", "x&y"},
		{"&#x26;", "&"},
		{"&#0;", "\U0000fffd"},
		{"&#13;x", "\U0000000dx"},
		{"&#128;", "\U000020ac"},
		{"&#x110000;", "\U0000fffd"},
		{"&#99999999999999999999;", "\U0000fffd"},
		{"&#xD800;", "\U0000fffd"},
		{"&#1;", ""},
		{"&#xFFFE;", ""},
		{"&notit;", "\U000000acit;"},
		{"&notin;", "\U00002209"},
		{"&#", "&#"},
		{"&#x", "&#x"},
		{"&#xg", "&#xg"},
		{"&", "&"},
		{"&&amp;", "&&"},
		{"&lt&gt", "<>"},
		{"&AElig", "\U000000c6"},
		{"&abcdefghijklmnopqrstuvwxyzabcdefghij;", "&abcdefghijklmnopqrstuvwxyzabcdefghij;"},
		{"&\U000000e9;", "&\U000000e9;"},
		{"&\U000000c9bc", "&\U000000c9bc"},
		{"& ;", "& ;"},
		{"&;", "&;"},
		{"tab&\U00000009x", "tab&\U00000009x"},
		{"&sol;etc/passwd", "/etc/passwd"},
		{"&NewLine;", "\U0000000a"},
		{"&fjlig;", "fj"},
		{"&acE;", "\U0000223e\U00000333"},
		{"ok", "ok"},
	} {
		if got := Unescape(item[0]); got != item[1] {
			t.Errorf("Unescape(%q) = %q, want %q", item[0], got, item[1])
		}
	}
}
