package service

import (
	"encoding/json"
	"sort"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// This file ports what BE/instance/views.py _link_imported_instance answers for
// a FAILURE row of the django-db result backend. AsyncResult.result is
// backend.exception_to_python(stored) (celery/backends/base.py), the view
// raises ValueError(f"Import task failed: {result.result}") and its caller
// catches Exception and answers 500 {"success": false, "error": str(e)}. A
// rebuild that raises (and a str() that raises) never reaches the ValueError,
// so the answer is that exception's text.
//
// The stored value is the decoded "result" column: a dict prepare_exception
// wrote ({"exc_type", "exc_message", "exc_module"}) or anything else a row
// holds. Its keys are resolved against the modules the web process loads; the
// table below is the classes whose str() or constructor differs from
// BaseException's. Anything else behaves like celery's create_exception_cls, a
// plain Exception subclass: reproducing Python's whole module namespace is out
// of reach, so a module the web process happens to load later with a class
// that overrides __str__ (or a non-exception attribute not listed here) is the
// one residual difference. Rendering a JSON object uses the keys' sorted
// order; Python keeps insertion order, which the decoded value no longer has.

// celeryExceptionText is _link_imported_instance's 500 body error for a
// FAILURE row.
func celeryExceptionText(stored any) string {
	text, rebuilt := celeryExceptionValue(stored)
	if rebuilt {
		return "Import task failed: " + text
	}
	return text
}

// celeryExceptionValue is exception_to_python(stored) followed by str():
// (text, true) is the rebuilt exception's text, (text, false) the text of the
// exception the rebuild or its str() raised.
func celeryExceptionValue(stored any) (string, bool) {
	if !celeryTruthy(stored) {
		// if not exc: return None
		return "None", true
	}
	object, isDict := stored.(map[string]any)
	if !isDict {
		// dict(exc), which only reports a TypeError as the documented text.
		converted, raised, ok := celeryStoredObject(stored)
		if !ok {
			return raised, false
		}
		object = converted
	}
	excModule, present := object["exc_module"]
	if !present {
		excModule = nil
	}
	excType, present := object["exc_type"]
	if !present {
		return "Exception information must include the exception type", false
	}
	excMsg, present := object["exc_message"]
	if !present {
		// exc.get('exc_message', '')
		excMsg = ""
	}
	class, raised, ok := celeryExceptionClass(excModule, excType, excMsg)
	if !ok {
		return raised, false
	}
	// if isinstance(exc_msg, (tuple, list)): cls(*exc_msg) else cls(exc_msg)
	args := []any{excMsg}
	if list, sequenced := excMsg.([]any); sequenced {
		args = list
	}
	switch text, result := class.format(class, args); result {
	case celeryPrinted:
		return text, true
	case celeryStrFailed:
		return text, false
	}
	// except Exception: exc = Exception(f'{cls}({exc_msg})')
	return "<class '" + class.reference() + "'>(" + celeryStr(excMsg) + ")", true
}

// celeryClass is a resolved exception class: the module and qualname its repr()
// shows and the __str__ (and constructor arity) of its class.
type celeryClass struct {
	module string
	name   string
	format func(celeryClass, []any) (string, celeryResult)
}

// celeryResult is what a class's constructor and __str__ did with the arguments.
type celeryResult int

const (
	// celeryPrinted: the constructor accepted the arguments and str() answered.
	celeryPrinted celeryResult = iota
	// celeryStrFailed: the exception was built, but its __str__ raised (or
	// answered something that is not a string), so str() raises.
	celeryStrFailed
	// celeryNotConstructed: the constructor raised, so exception_to_python
	// answers the plain Exception(f'{cls}({exc_msg})').
	celeryNotConstructed
)

// reference is type.__qualname__ as repr() writes it: builtins classes carry no
// module.
func (c celeryClass) reference() string {
	if c.module == "" {
		return c.name
	}
	return c.module + "." + c.name
}

// celeryExceptionClass resolves sys.modules[exc_module] then getattr per dotted
// name, answering the SecurityError text for the non-exception attributes of
// the modules the process loads and create_exception_cls' TypeError for a name
// that is not a string. A null exc_module is
// create_exception_cls(exc_type, 'celery.backends.base'); a module or name
// Celery cannot resolve, create_exception_cls(exc_type, 'celery.exceptions').
func celeryExceptionClass(module, name, message any) (celeryClass, string, bool) {
	if module == nil {
		return celeryCreatedClass(name, "celery.backends.base")
	}
	text, ok := module.(string)
	if !ok {
		// sys.modules[value]: a list or a dict is unhashable, other values are
		// a KeyError.
		switch module.(type) {
		case []any:
			return celeryClass{}, "unhashable type: 'list'", false
		case map[string]any:
			return celeryClass{}, "unhashable type: 'dict'", false
		}
		return celeryCreatedClass(name, "celery.exceptions")
	}
	path, ok := name.(string)
	if !ok {
		// exc_type.split('.') is an AttributeError.
		return celeryCreatedClass(name, "celery.exceptions")
	}
	key := text + "." + path
	if class, found := celeryExceptionClasses[key]; found {
		return class, "", true
	}
	if celeryNonExceptionAttributes[key] {
		return celeryClass{}, "Expected an exception class, got " + key + " with payload " +
			celeryStr(message), false
	}
	return celeryCreatedClass(name, "celery.exceptions")
}

// celeryCreatedClass is create_exception_cls(name, module): a plain Exception
// subclass, or type.__new__'s TypeError when the name is not a string.
func celeryCreatedClass(name any, module string) (celeryClass, string, bool) {
	text, ok := name.(string)
	if !ok {
		return celeryClass{}, "type.__new__() argument 1 must be str, not " + celeryTypeName(name), false
	}
	return celeryClass{module: module, name: text, format: celeryFormatBase}, "", true
}

// celeryExceptionClasses are the classes of loaded modules whose __str__ or
// constructor differs from BaseException's. Their names are the attribute path
// the stored exc_type uses, so a dotted name resolves too.
var celeryExceptionClasses = map[string]celeryClass{
	// OSError.__str__ ([Errno N] strerror: 'filename' -> 'filename2').
	"builtins.OSError":           {module: "", name: "OSError", format: celeryFormatOSError},
	"builtins.EnvironmentError":  {module: "", name: "OSError", format: celeryFormatOSError},
	"builtins.IOError":           {module: "", name: "OSError", format: celeryFormatOSError},
	"builtins.BlockingIOError":   {module: "", name: "BlockingIOError", format: celeryFormatOSError},
	"builtins.ChildProcessError": {module: "", name: "ChildProcessError", format: celeryFormatOSError},
	"builtins.ConnectionError":   {module: "", name: "ConnectionError", format: celeryFormatOSError},
	"builtins.ConnectionAbortedError": {module: "", name: "ConnectionAbortedError",
		format: celeryFormatOSError},
	"builtins.ConnectionRefusedError": {module: "", name: "ConnectionRefusedError",
		format: celeryFormatOSError},
	"builtins.ConnectionResetError": {module: "", name: "ConnectionResetError", format: celeryFormatOSError},
	"builtins.BrokenPipeError":      {module: "", name: "BrokenPipeError", format: celeryFormatOSError},
	"builtins.FileExistsError":      {module: "", name: "FileExistsError", format: celeryFormatOSError},
	"builtins.FileNotFoundError":    {module: "", name: "FileNotFoundError", format: celeryFormatOSError},
	"builtins.InterruptedError":     {module: "", name: "InterruptedError", format: celeryFormatOSError},
	"builtins.IsADirectoryError":    {module: "", name: "IsADirectoryError", format: celeryFormatOSError},
	"builtins.NotADirectoryError":   {module: "", name: "NotADirectoryError", format: celeryFormatOSError},
	"builtins.PermissionError":      {module: "", name: "PermissionError", format: celeryFormatOSError},
	"builtins.ProcessLookupError":   {module: "", name: "ProcessLookupError", format: celeryFormatOSError},
	"builtins.TimeoutError":         {module: "", name: "TimeoutError", format: celeryFormatOSError},
	"os.error":                      {module: "", name: "OSError", format: celeryFormatOSError},
	"socket.error":                  {module: "", name: "OSError", format: celeryFormatOSError},
	"socket.timeout":                {module: "", name: "TimeoutError", format: celeryFormatOSError},
	"socket.gaierror":               {module: "socket", name: "gaierror", format: celeryFormatOSError},
	"socket.herror":                 {module: "socket", name: "herror", format: celeryFormatOSError},
	// smtplib's classes derive from OSError.
	"smtplib.SMTPException": {module: "smtplib", name: "SMTPException", format: celeryFormatOSError},
	"smtplib.SMTPServerDisconnected": {module: "smtplib", name: "SMTPServerDisconnected",
		format: celeryFormatOSError},
	"smtplib.SMTPNotSupportedError": {module: "smtplib", name: "SMTPNotSupportedError",
		format: celeryFormatOSError},
	"smtplib.SMTPRecipientsRefused": {module: "smtplib", name: "SMTPRecipientsRefused",
		format: celeryFormatOneArgument},
	// KeyError.__str__ is repr(args[0]); celery's NotRegistered derives from it.
	"builtins.KeyError": {module: "", name: "KeyError", format: celeryFormatKeyError},
	"celery.exceptions.NotRegistered": {module: "celery.exceptions", name: "NotRegistered",
		format: celeryFormatKeyError},
	// The Unicode errors build a message from five (four) arguments.
	"builtins.UnicodeEncodeError":    {module: "", name: "UnicodeEncodeError", format: celeryFormatEncode},
	"builtins.UnicodeTranslateError": {module: "", name: "UnicodeTranslateError", format: celeryFormatTranslate},
	"builtins.UnicodeDecodeError":    {module: "", name: "UnicodeDecodeError", format: celeryFormatUnsupported},
	// SyntaxError prints its message and "(filename, line N)".
	"builtins.SyntaxError":      {module: "", name: "SyntaxError", format: celeryFormatSyntaxError},
	"builtins.IndentationError": {module: "", name: "IndentationError", format: celeryFormatSyntaxError},
	"builtins.TabError":         {module: "", name: "TabError", format: celeryFormatSyntaxError},
	"builtins._IncompleteInputError": {module: "", name: "_IncompleteInputError",
		format: celeryFormatSyntaxError},
	// The exception groups need exception members, which a stored value has not.
	"builtins.ExceptionGroup":     {module: "", name: "ExceptionGroup", format: celeryFormatUnsupported},
	"builtins.BaseExceptionGroup": {module: "", name: "BaseExceptionGroup", format: celeryFormatUnsupported},
	// json's decoder names the line and column of the position.
	"json.decoder.JSONDecodeError": {module: "json.decoder", name: "JSONDecodeError",
		format: celeryFormatJSONDecodeError},
	// ssl.SSLError.__str__ is its strerror, else str(args).
	"ssl.SSLError": {module: "ssl", name: "SSLError", format: celeryFormatSSLError},
	// billiard's time limit errors prefix str(self.args).
	"billiard.exceptions.TimeLimitExceeded": {module: "billiard.exceptions", name: "TimeLimitExceeded",
		format: celeryFormatNamed},
	"billiard.exceptions.SoftTimeLimitExceeded": {module: "billiard.exceptions",
		name: "SoftTimeLimitExceeded", format: celeryFormatNamed},
	// celery's Retry prints its message (which may not be a string) and Reject
	// keeps (reason, requeue) as its arguments.
	"celery.exceptions.Retry":  {module: "celery.exceptions", name: "Retry", format: celeryFormatRetry},
	"celery.exceptions.Reject": {module: "celery.exceptions", name: "Reject", format: celeryFormatReject},
}

// celeryNonExceptionAttributes are attribute paths of loaded modules that are
// not exception classes: exception_to_python refuses them (the SecurityError
// it raises for a stored result that names a callable). Other non-exception
// attributes answer like create_exception_cls.
var celeryNonExceptionAttributes = map[string]bool{
	"builtins.len": true, "builtins.int": true, "builtins.str": true, "builtins.type": true,
	"builtins.eval": true, "builtins.exec": true, "builtins.open": true, "builtins.print": true,
	"builtins.getattr": true, "builtins.setattr": true, "builtins.compile": true, "builtins.__import__": true,
	"os.system": true, "os.popen": true, "os.remove": true, "os.unlink": true, "os.kill": true,
	"os.execv": true, "os.spawnv": true, "os.path.join": true,
	"posix.system": true, "posix.popen": true, "posix.remove": true, "posix.kill": true,
	"subprocess.call": true, "subprocess.run": true, "subprocess.Popen": true,
}

// celeryFormatBase is BaseException.__str__: no argument is empty, one is
// str(arg), more are str(tuple).
func celeryFormatBase(_ celeryClass, args []any) (string, celeryResult) {
	switch len(args) {
	case 0:
		return "", celeryPrinted
	case 1:
		return celeryStr(args[0]), celeryPrinted
	}
	return celeryTupleRepr(args), celeryPrinted
}

func celeryFormatKeyError(cls celeryClass, args []any) (string, celeryResult) {
	if len(args) == 1 {
		return celeryRepr(args[0]), celeryPrinted
	}
	return celeryFormatBase(cls, args)
}

// celeryFormatOSError is OSError.__str__ with errno, strerror, filename and
// filename2; the winerror argument is not rendered.
func celeryFormatOSError(cls celeryClass, args []any) (string, celeryResult) {
	switch len(args) {
	case 0, 1:
		return celeryFormatBase(cls, args)
	case 2:
		return "[Errno " + celeryStr(args[0]) + "] " + celeryStr(args[1]), celeryPrinted
	case 3, 4:
		text := "[Errno " + celeryStr(args[0]) + "] " + celeryStr(args[1])
		if args[2] != nil {
			text += ": " + celeryRepr(args[2])
		}
		return text, celeryPrinted
	case 5:
		if args[2] == nil {
			return "[Errno " + celeryStr(args[0]) + "] " + celeryStr(args[1]), celeryPrinted
		}
		text := "[Errno " + celeryStr(args[0]) + "] " + celeryStr(args[1]) + ": " + celeryRepr(args[2])
		if args[4] != nil {
			text += " -> " + celeryRepr(args[4])
		}
		return text, celeryPrinted
	}
	// More arguments leave errno unset, so BaseException.__str__ answers.
	return celeryFormatBase(cls, args)
}

// celeryFormatOneArgument is a class whose constructor takes exactly one
// positional argument and prints it with BaseException.__str__.
func celeryFormatOneArgument(cls celeryClass, args []any) (string, celeryResult) {
	if len(args) != 1 {
		return "", celeryNotConstructed
	}
	return celeryFormatBase(cls, args)
}

func celeryFormatSSLError(cls celeryClass, args []any) (string, celeryResult) {
	if len(args) >= 2 && args[1] != nil {
		text, ok := args[1].(string)
		if !ok {
			// __str__ returned non-string (type int)
			return "__str__ returned non-string (type " + celeryTypeName(args[1]) + ")", celeryStrFailed
		}
		return text, celeryPrinted
	}
	return celeryTupleRepr(args), celeryPrinted
}

// celeryFormatEncode is UnicodeEncodeError.__str__ for its five arguments. The
// offending object is a str here: a stored result cannot carry the bytes
// UnicodeDecodeError needs, and the encode error was written by this process.
func celeryFormatEncode(_ celeryClass, args []any) (string, celeryResult) {
	if len(args) != 5 {
		return "", celeryNotConstructed
	}
	encoding, ok := args[0].(string)
	if !ok {
		return "", celeryNotConstructed
	}
	object, ok := args[1].(string)
	if !ok {
		return "", celeryNotConstructed
	}
	start, ok := celeryInt(args[2])
	if !ok {
		return "", celeryNotConstructed
	}
	end, ok := celeryInt(args[3])
	if !ok {
		return "", celeryNotConstructed
	}
	reason, ok := args[4].(string)
	if !ok {
		return "", celeryNotConstructed
	}
	points := []rune(object)
	if start == end-1 && start >= 0 && start < int64(len(points)) {
		return "'" + encoding + "' codec can't encode character '" + celeryUnicodeEscape(points[start]) +
			"' in position " + strconv.FormatInt(start, 10) + ": " + reason, celeryPrinted
	}
	return "'" + encoding + "' codec can't encode characters in position " + strconv.FormatInt(start, 10) +
		"-" + strconv.FormatInt(end-1, 10) + ": " + reason, celeryPrinted
}

// celeryFormatTranslate is UnicodeTranslateError.__str__ for its four
// arguments.
func celeryFormatTranslate(_ celeryClass, args []any) (string, celeryResult) {
	if len(args) != 4 {
		return "", celeryNotConstructed
	}
	object, ok := args[0].(string)
	if !ok {
		return "", celeryNotConstructed
	}
	start, ok := celeryInt(args[1])
	if !ok {
		return "", celeryNotConstructed
	}
	end, ok := celeryInt(args[2])
	if !ok {
		return "", celeryNotConstructed
	}
	reason, ok := args[3].(string)
	if !ok {
		return "", celeryNotConstructed
	}
	points := []rune(object)
	if start == end-1 && start >= 0 && start < int64(len(points)) {
		return "can't translate character '" + celeryUnicodeEscape(points[start]) + "' in position " +
			strconv.FormatInt(start, 10) + ": " + reason, celeryPrinted
	}
	return "can't translate characters in position " + strconv.FormatInt(start, 10) + "-" +
		strconv.FormatInt(end-1, 10) + ": " + reason, celeryPrinted
}

// celeryFormatUnsupported is a class whose constructor refuses every stored
// value (UnicodeDecodeError wants bytes, an exception group real exceptions).
func celeryFormatUnsupported(_ celeryClass, _ []any) (string, celeryResult) {
	return "", celeryNotConstructed
}

// celeryFormatSyntaxError is SyntaxError.__str__: the message, then
// " (filename, line N)" from the second argument's four items.
func celeryFormatSyntaxError(_ celeryClass, args []any) (string, celeryResult) {
	switch len(args) {
	case 0:
		// The constructor stores msg=None, which str() renders as "None".
		return "None", celeryPrinted
	case 1:
		return celeryStr(args[0]), celeryPrinted
	case 2:
		details, ok := args[1].([]any)
		if !ok || len(details) != 4 {
			return "", celeryNotConstructed
		}
		text := celeryStr(args[0])
		filename, named := details[0].(string)
		line, numbered := celeryInt(details[1])
		switch {
		case named && numbered:
			return text + " (" + filename + ", line " + strconv.FormatInt(line, 10) + ")", celeryPrinted
		case named:
			return text + " (" + filename + ")", celeryPrinted
		case numbered:
			return text + " (line " + strconv.FormatInt(line, 10) + ")", celeryPrinted
		}
		return text, celeryPrinted
	}
	// Three or more arguments keep the first one as the message.
	return celeryStr(args[0]), celeryPrinted
}

// celeryFormatJSONDecodeError is JSONDecodeError.__str__: the message, the line
// and column of pos, and pos itself.
func celeryFormatJSONDecodeError(_ celeryClass, args []any) (string, celeryResult) {
	if len(args) != 3 {
		return "", celeryNotConstructed
	}
	doc, ok := args[1].(string)
	if !ok {
		// doc.count is an AttributeError.
		return "", celeryNotConstructed
	}
	pos, ok := celeryInt(args[2])
	if !ok {
		return "", celeryNotConstructed
	}
	points := []rune(doc)
	end := pos
	if end < 0 {
		if end += int64(len(points)); end < 0 {
			end = 0
		}
	}
	if end > int64(len(points)) {
		end = int64(len(points))
	}
	line, lastBreak := int64(1), int64(-1)
	for index := int64(0); index < end; index++ {
		if points[index] == '\n' {
			line++
			lastBreak = index
		}
	}
	column := pos - lastBreak
	return celeryStr(args[0]) + ": line " + strconv.FormatInt(line, 10) + " column " +
		strconv.FormatInt(column, 10) + " (char " + strconv.FormatInt(pos, 10) + ")", celeryPrinted
}

// celeryFormatNamed is billiard's TimeLimitExceeded.__str__: the class name
// followed by repr(args).
func celeryFormatNamed(cls celeryClass, args []any) (string, celeryResult) {
	return cls.name + celeryTupleRepr(args), celeryPrinted
}

// celeryFormatRetry is celery's Retry.__str__: the message when it is truthy,
// else the retry time and the exception that caused the retry.
func celeryFormatRetry(_ celeryClass, args []any) (string, celeryResult) {
	var message any
	if len(args) > 0 {
		message = args[0]
	}
	if celeryTruthy(message) {
		text, ok := message.(string)
		if !ok {
			// __str__ returned a value that is not a string.
			return "__str__ returned non-string (type " + celeryTypeName(message) + ")", celeryStrFailed
		}
		return text, celeryPrinted
	}
	var when any
	if len(args) > 2 {
		when = args[2]
	}
	humanized := "at " + celeryStr(when)
	if celeryIsNumber(when) {
		humanized = "in " + celeryStr(when) + "s"
	}
	var exc any
	if len(args) > 1 {
		exc = args[1]
	}
	cause := ""
	if text, ok := exc.(string); ok {
		cause = text
	} else if celeryTruthy(exc) {
		cause = celerySafeRepr(exc)
	}
	if cause != "" {
		return "Retry " + humanized + ": " + cause, celeryPrinted
	}
	return "Retry " + humanized, celeryPrinted
}

// celeryFormatReject is celery's Reject: its arguments are (reason, requeue).
func celeryFormatReject(cls celeryClass, args []any) (string, celeryResult) {
	reason, requeue := any(nil), any(false)
	if len(args) > 0 {
		reason = args[0]
	}
	if len(args) > 1 {
		requeue = args[1]
	}
	return celeryFormatBase(cls, []any{reason, requeue})
}

// celeryStoredObject is dict(stored) for a value that is not a dict: an
// iterable of key/value pairs. ok is false when the conversion raises, with
// the exception's text.
func celeryStoredObject(value any) (map[string]any, string, bool) {
	object := map[string]any{}
	elements, ok := celerySequence(value)
	if !ok {
		return nil, celeryNotADictionaryText(value), false
	}
	for index, element := range elements {
		items, ok := celerySequence(element)
		if !ok {
			return nil, celeryNotADictionaryText(value), false
		}
		if len(items) != 2 {
			return nil, "dictionary update sequence element #" + strconv.Itoa(index) + " has length " +
				strconv.Itoa(len(items)) + "; 2 is required", false
		}
		switch items[0].(type) {
		case []any:
			return nil, "unhashable type: 'list'", false
		case map[string]any:
			return nil, "unhashable type: 'dict'", false
		}
		if key, ok := items[0].(string); ok {
			object[key] = items[1]
		}
	}
	return object, "", true
}

// celerySequence is the iteration of dict(): a list, the characters of a
// string, the keys of a dict.
func celerySequence(value any) ([]any, bool) {
	switch typed := value.(type) {
	case []any:
		return typed, true
	case string:
		items := make([]any, 0, len([]rune(typed)))
		for _, point := range typed {
			items = append(items, string(point))
		}
		return items, true
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		items := make([]any, len(keys))
		for index, key := range keys {
			items[index] = key
		}
		return items, true
	}
	return nil, false
}

// celeryNotADictionaryText is the TypeError the dict() fallback raises.
func celeryNotADictionaryText(value any) string {
	return "If the stored exception isn't an instance of BaseException, it must be a dictionary.\n" +
		"Instead got: " + celeryStr(value)
}

// celeryStr is str() of a decoded result value: a string is itself, anything
// else its repr.
func celeryStr(value any) string {
	if text, ok := value.(string); ok {
		return text
	}
	return celeryRepr(value)
}

func celeryRepr(value any) string {
	switch typed := value.(type) {
	case nil:
		return "None"
	case bool:
		if typed {
			return "True"
		}
		return "False"
	case string:
		return dto.PythonStringRepr(typed)
	case json.Number, float64:
		return dto.PythonStr(typed)
	case []any:
		var out strings.Builder
		out.WriteByte('[')
		for index, item := range typed {
			if index > 0 {
				out.WriteString(", ")
			}
			out.WriteString(celeryRepr(item))
		}
		out.WriteByte(']')
		return out.String()
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		var out strings.Builder
		out.WriteByte('{')
		for index, key := range keys {
			if index > 0 {
				out.WriteString(", ")
			}
			out.WriteString(dto.PythonStringRepr(key))
			out.WriteString(": ")
			out.WriteString(celeryRepr(typed[key]))
		}
		out.WriteByte('}')
		return out.String()
	}
	return dto.PythonStr(value)
}

// celeryTupleRepr is repr() of the argument tuple: "(a,)" for one element.
func celeryTupleRepr(args []any) string {
	var out strings.Builder
	out.WriteByte('(')
	for index, arg := range args {
		if index > 0 {
			out.WriteString(", ")
		}
		out.WriteString(celeryRepr(arg))
	}
	if len(args) == 1 {
		out.WriteByte(',')
	}
	out.WriteByte(')')
	return out.String()
}

// celerySafeRepr is kombu's safe_repr: repr() cut to 100 characters.
func celerySafeRepr(value any) string {
	text := celeryRepr(value)
	if len(text) > 100 {
		return text[:97] + "..."
	}
	return text
}

// celeryUnicodeEscape is the unicode_escape rendering CPython uses for the
// character a Unicode error names: always \xNN, \uNNNN or \UNNNNNNNN.
func celeryUnicodeEscape(point rune) string {
	switch {
	case point <= 0xff:
		return "\\x" + leftPadHex(int64(point), 2)
	case point <= 0xffff:
		return "\\u" + leftPadHex(int64(point), 4)
	}
	return "\\U" + leftPadHex(int64(point), 8)
}

func leftPadHex(value int64, width int) string {
	text := strconv.FormatInt(value, 16)
	for len(text) < width {
		text = "0" + text
	}
	return text
}

// celeryInt is a Python int argument; booleans count, floats do not.
func celeryInt(value any) (int64, bool) {
	switch typed := value.(type) {
	case bool:
		if typed {
			return 1, true
		}
		return 0, true
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			return 0, false
		}
		number, err := strconv.ParseInt(string(typed), 10, 64)
		if err != nil {
			return 0, false
		}
		return number, true
	}
	return 0, false
}

// celeryIsNumber is isinstance(value, numbers.Number) for a decoded value.
func celeryIsNumber(value any) bool {
	switch value.(type) {
	case bool, json.Number, float64:
		return true
	}
	return false
}

// celeryTypeName is type(value).__name__ for the texts that name a type.
func celeryTypeName(value any) string {
	switch typed := value.(type) {
	case nil:
		return "None"
	case bool:
		return "bool"
	case string:
		return "str"
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			return "float"
		}
		return "int"
	case float64:
		return "float"
	case []any:
		return "list"
	case map[string]any:
		return "dict"
	}
	return "object"
}

// celeryTruthy is bool() of a decoded JSON value.
func celeryTruthy(value any) bool {
	switch typed := value.(type) {
	case nil:
		return false
	case bool:
		return typed
	case string:
		return typed != ""
	case json.Number:
		number, err := strconv.ParseFloat(string(typed), 64)
		if err != nil {
			return string(typed) != ""
		}
		return number != 0
	case float64:
		return typed != 0
	case []any:
		return len(typed) > 0
	case map[string]any:
		return len(typed) > 0
	}
	return true
}
