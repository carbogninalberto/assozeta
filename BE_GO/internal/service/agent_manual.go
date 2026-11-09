package service

import (
	"context"
	"encoding/json"
	"math/big"
	"sort"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
)

// The answer half of BE/application/manuale/answers.py over the manual tool
// results (parsed back from their JSON text, as the agent does), and the
// manual tool functions' results in Python's key order.

func agentGet(value any, key string) any {
	if object, ok := value.(domain.RequestObject); ok {
		return lookupValue(object, key)
	}
	return nil
}

func agentGetDefault(object domain.RequestObject, key string, fallback any) any {
	if value, present := object.Lookup(key); present {
		return value
	}
	return fallback
}

func agentReaderStrings(value any, out *[]string) {
	switch typed := value.(type) {
	case string:
		*out = append(*out, typed)
	case []any:
		for _, item := range typed {
			agentReaderStrings(item, out)
		}
	case domain.RequestObject:
		for _, field := range typed {
			switch field.Name {
			case "text", "markdown", "title", "alt", "content", "cards":
				agentReaderStrings(field.Value, out)
			}
		}
	}
}

func agentStatusIs(value any, names ...string) bool {
	text, ok := value.(string)
	if !ok {
		return false
	}
	for _, name := range names {
		if text == name {
			return true
		}
	}
	return false
}

func agentHits(result domain.RequestObject) ([]any, error) {
	hits := agentGetDefault(result, "results", []any{})
	list, ok := hits.([]any)
	if !ok {
		return nil, ErrAgentNotPorted
	}
	for _, hit := range list {
		if _, ok := hit.(domain.RequestObject); !ok {
			return nil, ErrAgentNotPorted
		}
	}
	return list, nil
}

// agentEvidenceProblem is manual_evidence_problem.
func agentEvidenceProblem(result domain.RequestObject) (string, error) {
	hits, err := agentHits(result)
	if err != nil {
		return "", err
	}
	if agentStatusIs(lookupValue(result, "status"), "conflicting", "conflict") {
		return "conflicting", nil
	}
	for _, hit := range hits {
		if agentStatusIs(lookupValue(hit.(domain.RequestObject), "status"), "conflicting", "conflict") {
			return "conflicting", nil
		}
	}
	if len(hits) > 0 {
		verified := func(object domain.RequestObject) bool {
			status := agentGetDefault(object, "status", "verified")
			return agentStatusIs(status, "verified")
		}
		unverified := !verified(result)
		for _, hit := range hits {
			unverified = unverified || !verified(hit.(domain.RequestObject))
		}
		if unverified {
			return "unverified", nil
		}
	}
	seen := map[string]domain.RequestObject{}
	for _, item := range hits {
		hit := item.(domain.RequestObject)
		identifier, _ := lookupValue(hit, "id").(string)
		if raw := lookupValue(hit, "id"); raw != nil && identifier == "" && dto.PythonTruthy(raw) {
			return "", ErrAgentNotPorted
		}
		if previous, ok := seen[identifier]; ok && identifier != "" {
			if !dto.PythonEqual(lookupValue(previous, "text"), lookupValue(hit, "text")) ||
				(dto.PythonTruthy(lookupValue(previous, "content_sha256")) && dto.PythonTruthy(lookupValue(hit, "content_sha256")) &&
					!dto.PythonEqual(lookupValue(previous, "content_sha256"), lookupValue(hit, "content_sha256"))) ||
				(lookupValue(previous, "reader") != nil && lookupValue(hit, "reader") != nil &&
					!dto.PythonEqual(lookupValue(previous, "reader"), lookupValue(hit, "reader"))) {
				return "conflicting", nil
			}
		}
		if identifier != "" {
			seen[identifier] = hit
		}
		var prose []string
		for _, key := range []string{"text", "title"} {
			value := agentGetDefault(hit, key, "")
			text, ok := value.(string)
			if !ok {
				return "", ErrAgentNotPorted
			}
			prose = append(prose, text)
		}
		agentReaderStrings(agentGetDefault(hit, "reader", []any{}), &prose)
		for _, text := range prose {
			if agentAssistantInstruction.search(text) {
				return "irrelevant_instructions", nil
			}
		}
	}
	return "", nil
}

func agentFloat(value any) (float64, bool) {
	switch typed := value.(type) {
	case json.Number:
		number, err := strconv.ParseFloat(string(typed), 64)
		return number, err == nil
	case float64:
		return typed, true
	}
	return 0, false
}

// agentSelectedSection is selected_manual_section.
func agentSelectedSection(result domain.RequestObject) (domain.RequestObject, error) {
	problem, err := agentEvidenceProblem(result)
	if err != nil || problem != "" {
		return nil, err
	}
	hits, _ := agentHits(result)
	if len(hits) == 0 {
		return nil, nil
	}
	first := hits[0].(domain.RequestObject)
	if len(hits) > 1 {
		second := hits[1].(domain.RequestObject)
		intent := lookupValue(first, "intent")
		same := dto.PythonTruthy(intent) && dto.PythonEqual(intent, lookupValue(second, "intent"))
		if !same && !dto.PythonEqual(lookupValue(first, "page"), lookupValue(second, "page")) {
			left, okLeft := agentFloat(agentGetDefault(first, "score", json.Number("0")))
			right, okRight := agentFloat(agentGetDefault(second, "score", json.Number("0")))
			if !okLeft || !okRight {
				return nil, ErrAgentNotPorted
			}
			if left-right < .025 {
				return nil, nil
			}
		}
	}
	return first, nil
}

// agentManualAttachment is manual_section_attachment.
func agentManualAttachment(result domain.RequestObject) (any, error) {
	section, err := agentSelectedSection(result)
	if err != nil {
		return nil, err
	}
	if !agentStatusIs(lookupValue(result, "status"), "verified") || section == nil || !agentStatusIs(lookupValue(section, "status"), "verified") {
		return nil, nil
	}
	if !dto.PythonTruthy(lookupValue(section, "reader")) || !dto.PythonTruthy(lookupValue(section, "embedded_url")) {
		return nil, nil
	}
	attachment := domain.RequestObject{}
	for _, key := range []string{"id", "page", "title", "page_title", "reader", "screenshots", "embedded_url"} {
		value, present := section.Lookup(key)
		if !present {
			return nil, ErrAgentNotPorted // KeyError
		}
		attachment = append(attachment, domain.RequestField{Name: key, Value: value})
	}
	return attachment, nil
}

// agentGroundedAnswer is grounded_answer.
func agentGroundedAnswer(result domain.RequestObject) (string, error) {
	problem, err := agentEvidenceProblem(result)
	if err != nil {
		return "", err
	}
	switch problem {
	case "conflicting":
		return "Le indicazioni recuperate sono in conflitto. Non posso indicare una procedura verificata finché il manuale non viene corretto.", nil
	case "":
	default:
		return "Non ho trovato istruzioni verificate utilizzabili per questa domanda.", nil
	}
	hits, _ := agentHits(result)
	if len(hits) == 0 {
		if message := lookupValue(result, "message"); dto.PythonTruthy(message) {
			text, ok := message.(string)
			if !ok {
				return "", ErrAgentNotPorted
			}
			return text, nil
		}
		return "Non ho trovato istruzioni verificate. Quale operazione desideri eseguire?", nil
	}
	first, err := agentSelectedSection(result)
	if err != nil {
		return "", err
	}
	if first == nil {
		var options []string
		for i, item := range hits {
			if i == 3 {
				break
			}
			hit := item.(domain.RequestObject)
			link := lookupValue(hit, "embedded_url")
			if !dto.PythonTruthy(link) {
				var present bool
				if link, present = hit.Lookup("url"); !present {
					return "", ErrAgentNotPorted
				}
			}
			title, present := hit.Lookup("title")
			if !present {
				return "", ErrAgentNotPorted
			}
			options = append(options, "- ["+dto.PythonStr(title)+"]("+dto.PythonStr(link)+")")
		}
		return "Quale di queste operazioni desideri approfondire?\n\n" + strings.Join(options, "\n"), nil
	}
	text, okText := first.Lookup("text")
	link, okURL := first.Lookup("url")
	if !okText || !okURL {
		return "", ErrAgentNotPorted
	}
	return dto.PythonStr(text) + "\n\n[Leggi nel manuale](" + dto.PythonStr(link) + ")", nil
}

// agentManualDiagnosticAnswer is manual_diagnostic_answer.
func agentManualDiagnosticAnswer(result domain.RequestObject) (string, error) {
	if !agentStatusIs(lookupValue(result, "status"), "diagnostic") {
		if message := lookupValue(result, "message"); dto.PythonTruthy(message) {
			text, ok := message.(string)
			if !ok {
				return "", ErrAgentNotPorted
			}
			return text, nil
		}
		return "La diagnostica del manuale non è disponibile.", nil
	}
	summary, ok := lookupValue(result, "summary").(domain.RequestObject)
	if !ok {
		return "", ErrAgentNotPorted
	}
	need := func(object domain.RequestObject, key string) (string, error) {
		value, present := object.Lookup(key)
		if !present {
			return "", ErrAgentNotPorted
		}
		return dto.PythonStr(value), nil
	}
	verified, err := need(summary, "verified_sections")
	if err != nil {
		return "", err
	}
	var text string
	if agentStatusIs(lookupValue(result, "coverage_status"), "complete_inventory") {
		pages, err := need(summary, "navigation_pages")
		if err != nil {
			return "", err
		}
		sections, err := need(summary, "sections")
		if err != nil {
			return "", err
		}
		text = "Manuale: " + pages + " pagine inventariate, " + verified + " sezioni verificate su " + sections + "."
	} else {
		text = "Manuale: " + verified + " sezioni verificate. L'inventario delle lacune è parziale."
	}
	if agentStatusIs(lookupValue(result, "snapshot_status"), "stale") {
		text += "\nIl pacchetto riguarda una versione diversa o sorgenti che sono cambiate. Deve essere verificato nuovamente."
	}
	gaps, ok := lookupValue(result, "gaps").([]any)
	if !ok {
		return "", ErrAgentNotPorted
	}
	if len(gaps) > 0 {
		labels := map[string]string{"pending": "da verificare", "stale": "da aggiornare", "unsupported": "non supportata",
			"conflicting": "indicazioni in conflitto", "needs_external_verification": "richiede verifiche esterne"}
		pages, ok := lookupValue(result, "pages").([]any)
		if !ok {
			return "", ErrAgentNotPorted
		}
		byPath := map[string]domain.RequestObject{}
		for _, item := range pages {
			page, ok := item.(domain.RequestObject)
			path, okPath := lookupValue(page, "path").(string)
			if !ok || !okPath {
				return "", ErrAgentNotPorted
			}
			byPath[path] = page
		}
		var examples []string
		for i, item := range gaps {
			if i == 10 {
				break
			}
			gap, ok := item.(domain.RequestObject)
			pageKey, okPage := lookupValue(gap, "page").(string)
			sectionKey, okSection := lookupValue(gap, "section").(string)
			status, okStatus := lookupValue(gap, "status").(string)
			if !ok || !okPage || !okSection || !okStatus {
				return "", ErrAgentNotPorted
			}
			page := byPath[pageKey]
			var section domain.RequestObject
			if sections, ok := agentGetDefault(page, "sections", []any{}).([]any); ok {
				for _, candidate := range sections {
					if object, ok := candidate.(domain.RequestObject); ok && dto.PythonEqual(lookupValue(object, "id"), sectionKey) {
						section = object
						break
					}
				}
			}
			label, known := labels[status]
			if !known {
				label = status
			}
			examples = append(examples, "- "+dto.PythonStr(agentGetDefault(page, "title", pageKey))+" — "+
				dto.PythonStr(agentGetDefault(section, "title", sectionKey))+": "+label)
		}
		text += "\n\nSezioni che richiedono attenzione:\n" + strings.Join(examples, "\n")
		if len(gaps) > len(examples) {
			text += "\nAltre " + strconv.Itoa(len(gaps)-len(examples)) + " sezioni sono elencate nella diagnostica completa."
		}
	}
	return text, nil
}

// ---------------------------------------------------------------------------
// Manual tool results in Python's key order
// ---------------------------------------------------------------------------

// The sealed runtime index is canonical JSON (sorted keys), so values copied
// from it keep sorted keys; the dicts the tool functions build keep their
// code order.
var agentPublicOrder = []string{"id", "page", "section", "title", "text", "status", "url", "screenshots", "scenario_ids", "page_title",
	"language", "intent", "reader", "page_description", "page_order", "section_order", "embedded_url", "version",
	"application_revision", "manual_revision", "features", "audience", "corpus_identity", "code_state", "publication_status", "score"}

func agentSortedValue(value any) (any, error) {
	switch typed := value.(type) {
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		result := make(domain.RequestObject, 0, len(keys))
		for _, key := range keys {
			converted, err := agentSortedValue(typed[key])
			if err != nil {
				return nil, err
			}
			result = append(result, domain.RequestField{Name: key, Value: converted})
		}
		return result, nil
	case []any:
		result := make([]any, len(typed))
		for i, item := range typed {
			converted, err := agentSortedValue(item)
			if err != nil {
				return nil, err
			}
			result[i] = converted
		}
		return result, nil
	case []map[string]any:
		result := make([]any, len(typed))
		for i, item := range typed {
			converted, err := agentSortedValue(item)
			if err != nil {
				return nil, err
			}
			result[i] = converted
		}
		return result, nil
	case []string:
		result := make([]any, len(typed))
		for i, item := range typed {
			result[i] = item
		}
		return result, nil
	case nil, string, bool, json.Number:
		return typed, nil
	case float64:
		return typed, nil
	case int:
		return json.Number(strconv.Itoa(typed)), nil
	case int64:
		return json.Number(strconv.FormatInt(typed, 10)), nil
	case *big.Int:
		return json.Number(typed.String()), nil
	}
	return nil, ErrAgentNotPorted
}

func agentOrdered(object map[string]any, order []string, nested func(string, any) (any, error)) (domain.RequestObject, error) {
	result := domain.RequestObject{}
	used := map[string]bool{}
	for _, key := range order {
		value, present := object[key]
		if !present {
			continue
		}
		used[key] = true
		var converted any
		var err error
		if nested != nil {
			converted, err = nested(key, value)
		} else {
			converted, err = agentSortedValue(value)
		}
		if err != nil {
			return nil, err
		}
		result = append(result, domain.RequestField{Name: key, Value: converted})
	}
	for key := range object {
		if !used[key] {
			return nil, ErrAgentNotPorted
		}
	}
	return result, nil
}

// agentPublicSection orders one _public(chunk, index) dict.
func agentPublicSection(value any) (any, error) {
	if value == nil {
		return nil, nil
	}
	section, ok := value.(map[string]any)
	if !ok {
		return nil, ErrAgentNotPorted
	}
	text := section["text"]
	return agentOrdered(section, agentPublicOrder, func(key string, item any) (any, error) {
		switch key {
		case "screenshots":
			// {**image, 'url': ...}: the image's sorted keys, then the URL.
			images, ok := item.([]any)
			if !ok {
				return agentSortedValue(item)
			}
			result := make([]any, len(images))
			for i, image := range images {
				record, ok := image.(map[string]any)
				if !ok {
					converted, err := agentSortedValue(image)
					if err != nil {
						return nil, err
					}
					result[i] = converted
					continue
				}
				url, hasURL := record["url"]
				rest := map[string]any{}
				for k, v := range record {
					if k != "url" {
						rest[k] = v
					}
				}
				converted, err := agentSortedValue(rest)
				if err != nil {
					return nil, err
				}
				object := converted.(domain.RequestObject)
				if hasURL {
					object = append(object, domain.RequestField{Name: "url", Value: url})
				}
				result[i] = object
			}
			return result, nil
		case "reader":
			// The code default [{'title': '', 'text': text, 'screenshots': []}].
			if blocks, ok := item.([]any); ok && len(blocks) == 1 {
				if block, ok := blocks[0].(map[string]any); ok && len(block) == 3 && block["title"] == "" && block["text"] == text {
					if shots, ok := block["screenshots"].([]any); ok && len(shots) == 0 {
						return []any{agentObject("title", "", "text", text, "screenshots", []any{})}, nil
					}
				}
			}
		}
		return agentSortedValue(item)
	})
}

// agentManualOrdered orders a manual tool function's result.
func agentManualOrdered(name string, result map[string]any) (any, error) {
	status, _ := result["status"].(string)
	switch {
	case name == "search_manual" && result["results"] != nil:
		return agentOrdered(result, []string{"status", "results", "message"}, func(key string, value any) (any, error) {
			if key != "results" {
				return agentSortedValue(value)
			}
			hits, ok := value.([]any)
			if !ok {
				return nil, ErrAgentNotPorted
			}
			ordered := make([]any, len(hits))
			for i, hit := range hits {
				section, err := agentPublicSection(hit)
				if err != nil {
					return nil, err
				}
				ordered[i] = section
			}
			return ordered, nil
		})
	case status == "no_evidence" && result["results"] != nil:
		return agentOrdered(result, []string{"status", "results", "message"}, nil)
	case name == "get_manual_gaps" && status == "diagnostic":
		summary, _ := result["summary"].(map[string]any)
		return agentOrdered(result, []string{"status", "coverage_status", "snapshot_status", "version_status", "application_revision",
			"manual_revision", "version", "corpus_identity", "summary", "gaps", "pages", "domains", "screenshot_backlog", "stale_sources"},
			func(key string, value any) (any, error) {
				if key == "summary" && result["coverage_status"] == "section_gaps_only" && len(summary) == 2 {
					return agentOrdered(summary, []string{"verified_sections", "pending_sections"}, nil)
				}
				return agentSortedValue(value)
			})
	}
	return agentOrdered(result, []string{"status", "section", "evidence", "scenario_ids", "capture_provenance", "provenance", "results", "message"},
		func(key string, value any) (any, error) {
			if key == "section" {
				return agentPublicSection(value)
			}
			return agentSortedValue(value)
		})
}

// ExecuteForUser is a manual tool function called with the agent's identity:
// user_id is the authenticated agent user ("" is None), never a tool argument.
func (s *ManualTools) ExecuteForUser(ctx context.Context, userID, associationID, name string, arguments map[string]any) (map[string]any, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	parameters, known := agentManualParameters[name]
	if !known {
		return nil, manual.ErrUnknownTool
	}
	snapshot, err := s.context.ManualToolContext(ctx, userID)
	if err != nil {
		return nil, err
	}
	caller := manual.ToolContext{Features: []string{}}
	if snapshot.SelfHosted {
		caller.Features = []string{"self_hosted"}
	}
	caller.Maintainer = snapshot.SelfHosted && userID != "" && snapshot.ActiveUserID == userID && snapshot.PrimaryOwnerID == userID &&
		associationID != "" && snapshot.PrimaryAssociationID == associationID
	args := make(map[string]any, len(parameters))
	for _, key := range parameters {
		if value, present := arguments[key]; present {
			args[key] = value
		}
	}
	return s.source.ExecuteTool(ctx, caller, name, args)
}
