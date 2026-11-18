package apischema

import "strings"

type messageChoices map[string]int

type lineTemplate struct {
	Messages []string `json:"messages"`
	Rendered []string `json:"rendered"`
}

type composition struct {
	DefaultKey   string                      `json:"default_key"`
	Messages     map[string][]string         `json:"messages"`
	Translations []map[string]messageChoices `json:"translations"`
	Templates    map[Format][]lineTemplate   `json:"templates"`
	Slots        map[Format][][3]int         `json:"slots"`
}

func (c composition) patches(format Format, key string) ([]patch, bool) {
	selected := make(messageChoices, len(c.Messages))
	if !c.lookup(key, selected) {
		return nil, false
	}
	templates := c.Templates[format]
	patches := make([]patch, 0, len(c.Slots[format]))
	for _, slot := range c.Slots[format] {
		template := templates[slot[2]]
		choice := 0
		// The generator stores the Cartesian product in message order. Choice
		// zero for a missing translation is the original msgid.
		for _, message := range template.Messages {
			choice = choice*len(c.Messages[message]) + selected[message]
		}
		patches = append(patches, patch{slot[0], slot[1], []string{template.Rendered[choice]}})
	}
	return patches, true
}

func (c composition) lookup(key string, selected messageChoices) bool {
	flag, lists, _ := strings.Cut(key, "|")
	if flag != "0" && flag != "1" {
		return false
	}
	directories := strings.Split(lists, "|")
	if len(directories) != len(c.Translations) {
		return false
	}
	primary := make([]messageChoices, 0, len(directories))
	var fallbacks []messageChoices
	for i, list := range directories {
		if list == "" {
			continue
		}
		for position, name := range strings.Split(list, ",") {
			catalog, ok := c.Translations[i][name]
			if !ok {
				return false
			}
			if position == 0 {
				primary = append(primary, catalog)
			} else {
				fallbacks = append(fallbacks, catalog)
			}
		}
	}
	merge := func(catalog messageChoices) {
		for message, choice := range catalog {
			if _, found := selected[message]; !found {
				selected[message] = choice
			}
		}
	}
	// Django merges directory primaries with later catalogs taking precedence,
	// then searches the remaining catalogs in their original directory order.
	for i := len(primary) - 1; i >= 0; i-- {
		merge(primary[i])
	}
	for _, catalog := range fallbacks {
		merge(catalog)
	}
	return flag == "0" || c.lookup(c.DefaultKey, selected)
}
