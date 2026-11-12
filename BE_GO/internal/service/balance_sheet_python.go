package service

import (
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// The balance sheet data is a mutable Python value loaded with json.loads:
// dicts and lists by reference, int tokens as json.Number, floats as float64.
// errPython is any exception generate_balance_sheet raises (and the view
// swallows).
var errPython = errors.New("python exception")

type pyField struct {
	key   string
	value any
}

type pyDict struct{ fields []pyField }

type pyList struct{ items []any }

func (d *pyDict) get(key string) (any, bool) {
	for _, field := range d.fields {
		if field.key == key {
			return field.value, true
		}
	}
	return nil, false
}

// set is dict[key] = value: an existing key keeps its position.
func (d *pyDict) set(key string, value any) {
	for i := range d.fields {
		if d.fields[i].key == key {
			d.fields[i].value = value
			return
		}
	}
	d.fields = append(d.fields, pyField{key, value})
}

func pyFromJSON(value any) any {
	switch v := value.(type) {
	case domain.RequestObject:
		dict := &pyDict{}
		for _, field := range v {
			dict.set(field.Name, pyFromJSON(field.Value))
		}
		return dict
	case []any:
		list := &pyList{items: make([]any, len(v))}
		for i, item := range v {
			list.items[i] = pyFromJSON(item)
		}
		return list
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			number, _ := strconv.ParseFloat(string(v), 64)
			return number
		}
	}
	return value
}

func pyToJSON(value any) any {
	switch v := value.(type) {
	case *pyDict:
		object := domain.RequestObject{}
		for _, field := range v.fields {
			object = append(object, domain.RequestField{Name: field.key, Value: pyToJSON(field.value)})
		}
		return object
	case *pyList:
		items := make([]any, len(v.items))
		for i, item := range v.items {
			items[i] = pyToJSON(item)
		}
		return items
	case float64:
		if !math.IsInf(v, 0) && !math.IsNaN(v) {
			return json.Number(dto.PythonFloatStr(v))
		}
	}
	return value
}

// pyFinite reports whether json.dumps output is valid JSON (no NaN/Infinity).
func pyFinite(value any) bool {
	switch v := value.(type) {
	case float64:
		return !math.IsInf(v, 0) && !math.IsNaN(v)
	case *pyDict:
		for _, field := range v.fields {
			if !pyFinite(field.value) {
				return false
			}
		}
	case *pyList:
		for _, item := range v.items {
			if !pyFinite(item) {
				return false
			}
		}
	}
	return true
}

// pyIndex is value[key] with a str key: KeyError or TypeError otherwise.
func pyIndex(value any, key string) (any, error) {
	dict, ok := value.(*pyDict)
	if !ok {
		return nil, errPython
	}
	found, present := dict.get(key)
	if !present {
		return nil, errPython
	}
	return found, nil
}

// pyIterate is iter(value) for the containers json.loads produces: list
// items, dict keys, string characters.
func pyIterate(value any) ([]any, error) {
	switch v := value.(type) {
	case *pyList:
		return v.items, nil
	case *pyDict:
		keys := make([]any, len(v.fields))
		for i, field := range v.fields {
			keys[i] = field.key
		}
		return keys, nil
	case string:
		chars := []any{}
		for len(v) > 0 {
			_, size := utf8.DecodeRuneInString(v)
			chars = append(chars, v[:size])
			v = v[size:]
		}
		return chars, nil
	}
	return nil, errPython
}

// pyContains is `key in value`.
func pyContains(value any, key string) (bool, error) {
	switch v := value.(type) {
	case *pyDict:
		_, ok := v.get(key)
		return ok, nil
	case *pyList:
		for _, item := range v.items {
			if text, ok := item.(string); ok && text == key {
				return true, nil
			}
		}
		return false, nil
	case string:
		return strings.Contains(v, key), nil
	}
	return false, errPython
}

// pyFloat is float(value).
func pyFloat(value any) (float64, error) {
	if number, ok := value.(float64); ok {
		return number, nil
	}
	number, ok := dto.PythonFloatOf(value)
	if !ok {
		return 0, errPython
	}
	return number, nil
}

// pyAdd is current + delta for a float delta (int + float converts the int,
// raising OverflowError past the float range; other types raise TypeError).
func pyAdd(current any, delta float64) (any, error) {
	switch current.(type) {
	case bool, json.Number, float64:
		number, err := pyFloat(current)
		if err != nil {
			return nil, err
		}
		return number + delta, nil
	}
	return nil, errPython
}

// findCategoryIndex is balance_sheet_utils.find_category_index.
func findCategoryIndex(list any, id string) (int, error) {
	items, err := pyIterate(list)
	if err != nil {
		return -1, err
	}
	for i, item := range items {
		dict, ok := item.(*pyDict)
		if !ok {
			return -1, errPython // item['id'] on a non-dict: TypeError
		}
		value, present := dict.get("id")
		if !present {
			continue // KeyError is caught
		}
		if text, ok := value.(string); ok && text == id {
			return i, nil
		}
	}
	return -1, nil
}

// pyListAt is default_bs[direction][key] re-read for each statement.
func pyListAt(data any, direction, key string) (any, error) {
	section, err := pyIndex(data, direction)
	if err != nil {
		return nil, err
	}
	return pyIndex(section, key)
}

func pyAppend(list any, value any) error {
	target, ok := list.(*pyList)
	if !ok {
		return errPython
	}
	target.items = append(target.items, value)
	return nil
}

// metaCategoryID is the lookup value of PaymentCategory.objects.filter(
// payment_category_id=value): UUIDField.to_python's int or hex form. nil means
// the isnull lookup (no row); a ValidationError is errPython.
func metaCategoryID(value any) (*string, error) {
	switch v := value.(type) {
	case nil:
		return nil, nil
	case string:
		id, err := paymentInfoParseUUID(v)
		if err != nil {
			return nil, errPython
		}
		text := id.String()
		return &text, nil
	case bool, json.Number:
		if number, ok := v.(json.Number); ok {
			integer, valid := new(big.Int).SetString(string(number), 10)
			if !valid || integer.Sign() < 0 || integer.BitLen() > 128 {
				return nil, errPython
			}
		}
		text, err := dto.DjangoUUIDFromInt(v)
		if err != nil {
			return nil, errPython
		}
		return &text, nil
	}
	return nil, errPython
}

// pyStr is str() of a Python value.
func pyStr(value any) string { return dto.PythonStr(pyToJSON(value)) }

type balanceSheetMeta struct {
	key      string
	category *domain.BalanceSheetCategory
	amount   float64
}

type balanceSheetGenerator struct {
	data     any
	category func(id string) (*domain.BalanceSheetCategory, error)
	failure  error // a lookup error that is not a Python exception
}

// metas walks payment.meta_payment_categories, yielding the entries with a
// found category and a non-zero amount (the loop's `continue`s).
func (g *balanceSheetGenerator) metas(raw any, visit func(balanceSheetMeta) error) error {
	if raw == nil {
		return nil
	}
	switch raw.(type) {
	case *pyList, *pyDict, string:
	default:
		return errPython // len() of a number or bool
	}
	items, _ := pyIterate(raw)
	for _, meta := range items {
		var value any = "senza_causale"
		for _, name := range []string{"payment_category_id", "payment_category"} {
			present, err := pyContains(meta, name)
			if err != nil {
				return err
			}
			if present {
				if value, err = pyIndex(meta, name); err != nil {
					return err
				}
				break
			}
		}
		id, err := metaCategoryID(value)
		if err != nil {
			return err
		}
		var category *domain.BalanceSheetCategory
		if id != nil {
			if category, err = g.category(*id); err != nil {
				g.failure = err
				return err
			}
		}
		if category == nil {
			continue // amount stays 0
		}
		amount := 0.0
		for _, name := range []string{"amount", "fee"} {
			present, _ := pyContains(meta, name)
			if present {
				field, _ := pyIndex(meta, name)
				if amount, err = pyFloat(field); err != nil {
					return err
				}
				break
			}
		}
		if amount == 0 {
			continue
		}
		if err = visit(balanceSheetMeta{key: pyStr(value), category: category, amount: amount}); err != nil {
			return err
		}
	}
	return nil
}

type balanceSheetItem struct {
	direction, key, typeKey, categoryID string
	commercial                          bool
	amount                              float64
	meta                                any
	payment                             domain.BalanceSheetPayment
}

func balanceSheetItemOf(payment domain.BalanceSheetPayment) (balanceSheetItem, error) {
	item := balanceSheetItem{direction: "incoming", key: "generalIncome", categoryID: "senza_causale", payment: payment}
	if payment.Expense {
		item.direction, item.key = "outgoing", "generalExpenses"
	}
	if payment.CategoryID != nil {
		item.categoryID = *payment.CategoryID
		item.commercial = payment.CategoryType != nil && *payment.CategoryType == 2
	}
	item.typeKey = "institutional"
	if item.commercial {
		item.typeKey = "commercial"
	}
	amount, err := strconv.ParseFloat(payment.Amount, 64)
	if err != nil {
		return item, err
	}
	item.amount = amount
	if payment.Meta != nil {
		parsed, err := dto.ParseProfileTablesJSON([]byte(*payment.Meta))
		if err != nil {
			return item, err
		}
		item.meta = pyFromJSON(parsed)
	}
	return item, nil
}

func typeKeyOf(commercial bool) string {
	if commercial {
		return "commercial"
	}
	return "institutional"
}

func setItemKey(list any, index int, key string, value any) error {
	items := list.(*pyList).items
	dict, ok := items[index].(*pyDict)
	if !ok {
		return errPython
	}
	dict.set(key, value)
	return nil
}

func addItemKey(list any, index int, key string, delta float64) error {
	target, ok := list.(*pyList)
	if !ok {
		return errPython
	}
	dict, ok := target.items[index].(*pyDict)
	if !ok {
		return errPython
	}
	current, present := dict.get(key)
	if !present {
		return errPython
	}
	updated, err := pyAdd(current, delta)
	if err != nil {
		return err
	}
	dict.set(key, updated)
	return nil
}

// reset is reset_balance_sheet_to_zero.
func (g *balanceSheetGenerator) reset(items []balanceSheetItem) error {
	for _, item := range items {
		list, err := pyListAt(g.data, item.direction, item.key)
		if err != nil {
			return err
		}
		index, err := findCategoryIndex(list, item.categoryID)
		if err != nil {
			return err
		}
		if index == -1 {
			continue
		}
		if err = setItemKey(list, index, item.typeKey, json.Number("0")); err != nil {
			return err
		}
		err = g.metas(item.meta, func(meta balanceSheetMeta) error {
			list, err := pyListAt(g.data, item.direction, item.key)
			if err != nil {
				return err
			}
			index, err := findCategoryIndex(list, meta.key)
			if err != nil || index == -1 {
				return err
			}
			return setItemKey(list, index, typeKeyOf(meta.category.Type == 2), json.Number("0"))
		})
		if err != nil {
			return err
		}
	}
	return nil
}

func balanceSheetEntry(description string, amount float64, commercial bool, id string) *pyDict {
	var institutional, commercialValue any = amount, json.Number("0")
	if commercial {
		institutional, commercialValue = json.Number("0"), amount
	}
	return &pyDict{fields: []pyField{{"description", description}, {"institutional", institutional}, {"commercial", commercialValue},
		{"id", id}, {"editable", false}, {"default", true}}}
}

// generate is generate_balance_sheet after the payment query: any error is
// the exception the view swallows, the data keeping what was done so far.
func (g *balanceSheetGenerator) generate(items []balanceSheetItem) error {
	if err := g.reset(items); err != nil {
		return err
	}
	for _, item := range items {
		list, err := pyListAt(g.data, item.direction, item.key)
		if err != nil {
			return err
		}
		index, err := findCategoryIndex(list, item.categoryID)
		if err != nil {
			return err
		}
		if index == -1 {
			description := "Senza causale"
			if item.payment.CategoryName != nil {
				description = *item.payment.CategoryName
			}
			if err = pyAppend(list, balanceSheetEntry(description, item.amount, item.commercial, item.categoryID)); err != nil {
				return err
			}
			index = len(list.(*pyList).items) - 1
		} else if err = addItemKey(list, index, item.typeKey, item.amount); err != nil {
			return err
		}
		err = g.metas(item.meta, func(meta balanceSheetMeta) error {
			list, err := pyListAt(g.data, item.direction, item.key)
			if err != nil {
				return err
			}
			metaIndex, err := findCategoryIndex(list, meta.key)
			if err != nil {
				return err
			}
			commercial := meta.category.Type == 2
			if metaIndex == -1 {
				err = pyAppend(list, balanceSheetEntry(meta.category.Name, meta.amount, commercial, meta.category.ID))
			} else {
				err = addItemKey(list, metaIndex, typeKeyOf(commercial), meta.amount)
			}
			if err != nil {
				return err
			}
			list, err = pyListAt(g.data, item.direction, item.key)
			if err != nil {
				return err
			}
			return addItemKey(list, index, item.typeKey, -meta.amount)
		})
		if err != nil {
			return err
		}
	}
	return nil
}
