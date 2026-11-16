// Command refusal-reach is a development static-analysis tool. It lists every
// "not ported" refusal the Go backend can still return, and the HTTP route,
// WebSocket consumer, worker job or command from which the return site is
// reachable. It is not part of the deployed image: BE_GO/Dockerfile builds an
// explicit list of commands and does not include this one.
//
// The analysis loads every package of the module, builds SSA, builds a CHA
// call graph seeded with the roots below and refines it with VTA, then walks
// the call graph from each root to the functions that can return a refusal
// sentinel (a global initialized by refusal.New, or a direct refusal.New call).
//
// Roots:
//   - every handler registered with e.Any in cmd/server (method ANY, path
//     literal; loop-built and DefaultRouter/RouterStock paths are expanded);
//   - every method of every WebSocket consumer implementation;
//   - every handler registered in a JobDefinition map in cmd/worker;
//   - the main function of every other command under cmd/.
//
// Output is deterministic JSON on stdout: rows sorted by route and sentinel,
// plus the sentinels that no root can reach.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/constant"
	"go/token"
	"go/types"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"golang.org/x/tools/go/callgraph"
	"golang.org/x/tools/go/callgraph/cha"
	"golang.org/x/tools/go/callgraph/vta"
	"golang.org/x/tools/go/packages"
	"golang.org/x/tools/go/ssa"
	"golang.org/x/tools/go/ssa/ssautil"
)

// row is one (route, sentinel) pair reached from one root.
type row struct {
	Route    string   `json:"route"`
	Method   string   `json:"method,omitempty"`
	RootKind string   `json:"root_kind"`
	Sentinel string   `json:"sentinel"`
	Reason   string   `json:"reason"`
	Site     string   `json:"site"`
	Chain    []string `json:"chain"`
}

// orphan is a sentinel no root can reach.
type orphan struct {
	Sentinel string `json:"sentinel"`
	Reason   string `json:"reason"`
	Site     string `json:"site"`
}

type report struct {
	Rows   []row    `json:"rows"`
	NoRoot []orphan `json:"no_root"`
}

// sentinel is one refusal value: a package-level global initialized by
// refusal.New (possibly through an alias), or a direct call to refusal.New.
type sentinel struct {
	Key    string
	Reason string
	Site   token.Position
}

type root struct {
	Fn     *ssa.Function
	Kind   string
	Route  string
	Method string
}

type analyzer struct {
	dir        string
	fset       *token.FileSet
	prog       *ssa.Program
	pkgs       []*packages.Package
	pkgByPath  map[string]*packages.Package
	functions  map[*ssa.Function]bool
	modulePath string
	errorType  *types.Interface

	// sentinels maps a package-qualified global name to its refusal value.
	sentinels map[string]*sentinel
	// every sentinel seen, including direct refusal.New calls.
	allSentinels []*sentinel
}

func main() {
	dir := flag.String("dir", ".", "module root to analyze")
	flag.Parse()
	if flag.NArg() != 0 {
		fail("unexpected arguments")
	}
	out, err := run(*dir)
	if err != nil {
		fail(err.Error())
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(out); err != nil {
		fail(err.Error())
	}
}

func fail(message string) {
	fmt.Fprintln(os.Stderr, "refusal-reach: "+message)
	os.Exit(1)
}

func run(dir string) (*report, error) {
	absolute, err := filepath.Abs(dir)
	if err != nil {
		return nil, err
	}
	a := &analyzer{dir: absolute}

	cfg := &packages.Config{
		Dir: absolute,
		Mode: packages.NeedName | packages.NeedFiles | packages.NeedCompiledGoFiles |
			packages.NeedImports | packages.NeedDeps | packages.NeedTypes |
			packages.NeedSyntax | packages.NeedTypesInfo | packages.NeedTypesSizes |
			packages.NeedModule,
		Tests: false,
	}
	pkgs, err := packages.Load(cfg, "./...")
	if err != nil {
		return nil, err
	}
	var loadErrors []string
	packages.Visit(pkgs, nil, func(pkg *packages.Package) {
		for _, e := range pkg.Errors {
			loadErrors = append(loadErrors, e.Error())
		}
	})
	if len(loadErrors) > 0 {
		sort.Strings(loadErrors)
		return nil, fmt.Errorf("load errors:\n%s", strings.Join(loadErrors, "\n"))
	}
	if len(pkgs) == 0 {
		return nil, fmt.Errorf("no packages under %s", absolute)
	}
	sort.Slice(pkgs, func(i, j int) bool { return pkgs[i].PkgPath < pkgs[j].PkgPath })
	a.pkgs = pkgs
	a.pkgByPath = map[string]*packages.Package{}
	for _, pkg := range pkgs {
		if pkg.Module != nil && pkg.Module.Path != "" {
			a.modulePath = pkg.Module.Path
		}
	}
	if a.modulePath == "" {
		return nil, fmt.Errorf("cannot determine the module path")
	}
	// Include dependency packages, which go/packages adds to their Imports,
	// so lookups by import path work for the handler and refusal packages.
	for _, pkg := range pkgs {
		a.indexPackages(pkg)
	}
	a.errorType = types.Universe.Lookup("error").Type().Underlying().(*types.Interface)

	prog, _ := ssautil.AllPackages(pkgs, ssa.InstantiateGenerics)
	prog.Build()
	a.prog = prog
	a.fset = prog.Fset
	a.functions = ssautil.AllFunctions(prog)

	if err := a.collectSentinels(); err != nil {
		return nil, err
	}
	targets, err := a.collectTargets()
	if err != nil {
		return nil, err
	}
	roots, err := a.collectRoots()
	if err != nil {
		return nil, err
	}
	return a.report(roots, targets)
}

func (a *analyzer) indexPackages(pkg *packages.Package) {
	if pkg == nil {
		return
	}
	if _, ok := a.pkgByPath[pkg.PkgPath]; ok {
		return
	}
	a.pkgByPath[pkg.PkgPath] = pkg
	for _, imported := range pkg.Imports {
		a.indexPackages(imported)
	}
}

func (a *analyzer) relPath(importPath string) string {
	return strings.TrimPrefix(strings.TrimPrefix(importPath, a.modulePath), "/")
}

// collectSentinels finds package-level globals initialized by refusal.New and
// aliases of them, then records every direct refusal.New call site.
func (a *analyzer) collectSentinels() error {
	a.sentinels = map[string]*sentinel{}
	// raw holds the initializer shape of each candidate global.
	type candidate struct {
		global *types.Var
		isNew  bool
		reason string
		pos    token.Position
		alias  string
	}
	var candidates []candidate
	for _, pkg := range a.pkgs {
		if pkg.TypesInfo == nil {
			continue
		}
		for _, file := range pkg.Syntax {
			ast.Inspect(file, func(node ast.Node) bool {
				decl, ok := node.(*ast.GenDecl)
				if !ok || decl.Tok != token.VAR {
					return true
				}
				for _, spec := range decl.Specs {
					value, ok := spec.(*ast.ValueSpec)
					if !ok {
						continue
					}
					for i, name := range value.Names {
						if i >= len(value.Values) {
							continue
						}
						obj, ok := pkg.TypesInfo.Defs[name].(*types.Var)
						if !ok {
							continue
						}
						expr := value.Values[i]
						c := candidate{global: obj, pos: a.position(expr.Pos())}
						if a.isRefusalNew(pkg, expr) {
							c.isNew = true
							c.reason = a.reasonOf(pkg, expr)
						} else if alias := a.sentinelAlias(pkg, expr); alias != "" {
							c.alias = alias
						} else {
							continue
						}
						candidates = append(candidates, c)
					}
				}
				return true
			})
		}
	}
	// Resolve refusal.New globals first, then aliases by fixed point.
	for _, c := range candidates {
		if !c.isNew {
			continue
		}
		key := c.global.Pkg().Path() + "." + c.global.Name()
		a.sentinels[key] = &sentinel{Key: a.keyFor(c.global), Reason: c.reason, Site: c.pos}
	}
	for again := true; again; {
		again = false
		for _, c := range candidates {
			if c.isNew {
				continue
			}
			source, ok := a.sentinels[c.alias]
			if !ok {
				continue
			}
			key := c.global.Pkg().Path() + "." + c.global.Name()
			if _, exists := a.sentinels[key]; exists {
				continue
			}
			a.sentinels[key] = source
			again = true
		}
	}
	// Direct refusal.New calls outside a package-level variable declaration.
	for _, pkg := range a.pkgs {
		if pkg.TypesInfo == nil {
			continue
		}
		for _, file := range pkg.Syntax {
			ast.Inspect(file, func(node ast.Node) bool {
				call, ok := node.(*ast.CallExpr)
				if !ok {
					return true
				}
				if !a.isRefusalNew(pkg, call) {
					return true
				}
				pos := a.position(call.Lparen)
				key := inlineSentinelKey(a.relPath(pkg.PkgPath), pos)
				// Package-level declarations were already collected above.
				if _, exists := a.findGlobalSentinelAt(pos); exists {
					return true
				}
				a.allSentinels = append(a.allSentinels, &sentinel{Key: key, Reason: a.reasonOf(pkg, call), Site: a.position(call.Pos())})
				return true
			})
		}
	}
	for _, s := range a.sentinels {
		a.allSentinels = append(a.allSentinels, s)
	}
	sort.Slice(a.allSentinels, func(i, j int) bool {
		if a.allSentinels[i].Site.Filename != a.allSentinels[j].Site.Filename {
			return a.allSentinels[i].Site.Filename < a.allSentinels[j].Site.Filename
		}
		return a.allSentinels[i].Site.Line < a.allSentinels[j].Site.Line
	})
	return nil
}

func (a *analyzer) findGlobalSentinelAt(pos token.Position) (*sentinel, bool) {
	for _, s := range a.sentinels {
		if s.Site.Filename == pos.Filename && s.Site.Line == pos.Line {
			return s, true
		}
	}
	return nil, false
}

// inlineSentinelKey names a direct refusal.New call. The line (not the
// column) keeps the AST and SSA views of the same call aligned.
func inlineSentinelKey(packagePath string, pos token.Position) string {
	return fmt.Sprintf("%s.refusal.New@%s:%d", packagePath, pos.Filename, pos.Line)
}

// fileLine renders a position as file:line, which is the stable form used in
// the JSON report.
func fileLine(pos token.Position) string {
	return fmt.Sprintf("%s:%d", pos.Filename, pos.Line)
}

func (a *analyzer) keyFor(v *types.Var) string {
	prefix := a.relPath(v.Pkg().Path())
	if prefix == "" {
		return v.Name()
	}
	return prefix + "." + v.Name()
}

func (a *analyzer) position(pos token.Pos) token.Position {
	position := a.fset.Position(pos)
	relative, err := filepath.Rel(a.dir, position.Filename)
	if err == nil && !strings.HasPrefix(relative, "..") {
		position.Filename = filepath.ToSlash(relative)
	}
	return position
}

func (a *analyzer) isRefusalNew(pkg *packages.Package, expr ast.Expr) bool {
	call, ok := expr.(*ast.CallExpr)
	if !ok {
		return false
	}
	selector, ok := call.Fun.(*ast.SelectorExpr)
	if !ok {
		return false
	}
	obj, ok := pkg.TypesInfo.Uses[selector.Sel].(*types.Func)
	if !ok || obj.Name() != "New" || obj.Pkg() == nil {
		return false
	}
	return obj.Pkg().Path() == a.modulePath+"/internal/refusal"
}

// reasonOf evaluates the constant argument of refusal.New.
func (a *analyzer) reasonOf(pkg *packages.Package, expr ast.Expr) string {
	call, ok := expr.(*ast.CallExpr)
	if !ok || len(call.Args) == 0 {
		return "<non-constant>"
	}
	if tv, ok := pkg.TypesInfo.Types[call.Args[0]]; ok && tv.Value != nil && tv.Value.Kind() == constant.String {
		return constant.StringVal(tv.Value)
	}
	return "<non-constant>"
}

// sentinelAlias returns the package-qualified name of a global an initializer
// refers to, so aliases such as service.errCalendarNotPorted are followed.
func (a *analyzer) sentinelAlias(pkg *packages.Package, expr ast.Expr) string {
	selector, ok := expr.(*ast.SelectorExpr)
	if !ok {
		return ""
	}
	obj, ok := pkg.TypesInfo.Uses[selector.Sel].(*types.Var)
	if !ok {
		return ""
	}
	return obj.Pkg().Path() + "." + obj.Name()
}

// target is one function that can return one sentinel.
type target struct {
	Fn       *ssa.Function
	Sentinel *sentinel
	At       token.Position
}

// collectTargets records, per function, every refusal sentinel the function
// can return. A load used only as an argument of errors.Is or errors.As does
// not reach a return value and is therefore not a target.
func (a *analyzer) collectTargets() (map[*ssa.Function]map[*sentinel]token.Position, error) {
	result := map[*ssa.Function]map[*sentinel]token.Position{}
	for fn := range a.functions {
		if fn.Blocks == nil {
			continue
		}
		returned := a.returnedSentinels(fn)
		if len(returned) == 0 {
			continue
		}
		result[fn] = returned
	}
	return result, nil
}

// returnedSentinels runs a forward taint analysis over one SSA function and
// reports the sentinels that flow into a Return instruction.
func (a *analyzer) returnedSentinels(fn *ssa.Function) map[*sentinel]token.Position {
	values := map[ssa.Value]map[*sentinel]bool{}
	memory := map[ssa.Value]map[*sentinel]bool{}
	add := func(dst map[*sentinel]bool, s *sentinel) bool {
		if s == nil {
			return false
		}
		if dst[s] {
			return false
		}
		dst[s] = true
		return true
	}
	merge := func(dst map[*sentinel]bool, src map[*sentinel]bool) bool {
		changed := false
		for s := range src {
			changed = add(dst, s) || changed
		}
		return changed
	}
	taint := func(v ssa.Value) map[*sentinel]bool {
		if v == nil {
			return nil
		}
		if values[v] == nil {
			values[v] = map[*sentinel]bool{}
		}
		return values[v]
	}
	memoryOf := func(v ssa.Value) map[*sentinel]bool {
		if v == nil {
			return nil
		}
		if memory[v] == nil {
			memory[v] = map[*sentinel]bool{}
		}
		return memory[v]
	}
	base := func() func(ssa.Value) []ssa.Value {
		var walk func(ssa.Value) []ssa.Value
		walk = func(v ssa.Value) []ssa.Value {
			switch value := v.(type) {
			case *ssa.Alloc:
				return []ssa.Value{value}
			case *ssa.FieldAddr:
				return walk(value.X)
			case *ssa.IndexAddr:
				return walk(value.X)
			case *ssa.Slice:
				return walk(value.X)
			case *ssa.UnOp:
				if value.Op == token.MUL {
					return walk(value.X)
				}
			}
			return nil
		}
		return walk
	}()

	for changed := true; changed; {
		changed = false
		for _, block := range fn.Blocks {
			for _, instruction := range block.Instrs {
				switch ins := instruction.(type) {
				case *ssa.UnOp:
					if ins.Op != token.MUL {
						continue
					}
					if global, ok := ins.X.(*ssa.Global); ok {
						if s := a.sentinelOfGlobal(global); s != nil {
							changed = add(taint(ins), s) || changed
						}
					}
					for _, baseValue := range base(ins.X) {
						changed = merge(taint(ins), memory[baseValue]) || changed
						if ins.X != baseValue {
							changed = merge(taint(ins), values[baseValue]) || changed
						}
					}
				case *ssa.Phi:
					for _, edge := range ins.Edges {
						changed = merge(taint(ins), values[edge]) || changed
					}
				case *ssa.MakeInterface:
					changed = merge(taint(ins), values[ins.X]) || changed
				case *ssa.ChangeInterface:
					changed = merge(taint(ins), values[ins.X]) || changed
				case *ssa.ChangeType:
					changed = merge(taint(ins), values[ins.X]) || changed
				case *ssa.Convert:
					changed = merge(taint(ins), values[ins.X]) || changed
				case *ssa.Slice:
					changed = merge(taint(ins), values[ins.X]) || changed
				case *ssa.Extract:
					if a.implementsError(ins.Type()) && values[ins.Tuple] != nil {
						changed = merge(taint(ins), values[ins.Tuple]) || changed
					}
				case *ssa.Call:
					if s := a.sentinelOfCall(ins); s != nil {
						changed = add(taint(ins), s) || changed
					}
					if builtin, ok := ins.Common().Value.(*ssa.Builtin); ok && builtin.Name() == "append" {
						// append([]any{sentinel}, args...) carries the slices'
						// elements, so a later fmt.Errorf("%w", that...) wraps it.
						for _, argument := range ins.Common().Args {
							changed = merge(taint(ins), values[argument]) || changed
						}
						continue
					}
					if !a.implementsError(ins.Type()) {
						continue
					}
					for _, argument := range ins.Common().Args {
						changed = merge(taint(ins), values[argument]) || changed
					}
				case *ssa.Store:
					source := values[ins.Val]
					if len(source) == 0 {
						continue
					}
					for _, baseValue := range base(ins.Addr) {
						changed = merge(memoryOf(baseValue), source) || changed
						// A returned pointer to a value holding a refusal is
						// itself a refusal carrier.
						changed = merge(taint(baseValue), source) || changed
					}
				}
			}
		}
	}

	returned := map[*sentinel]token.Position{}
	for _, block := range fn.Blocks {
		for _, instruction := range block.Instrs {
			ret, ok := instruction.(*ssa.Return)
			if !ok {
				continue
			}
			for _, result := range ret.Results {
				for s := range values[result] {
					pos := a.position(instruction.Pos())
					if previous, exists := returned[s]; !exists || positionLess(pos, previous) {
						returned[s] = pos
					}
				}
			}
		}
	}
	return returned
}

func positionLess(a, b token.Position) bool {
	if a.Filename != b.Filename {
		return a.Filename < b.Filename
	}
	return a.Line < b.Line
}

func (a *analyzer) implementsError(t types.Type) bool {
	if tuple, ok := t.(*types.Tuple); ok {
		for i := 0; i < tuple.Len(); i++ {
			if a.implementsError(tuple.At(i).Type()) {
				return true
			}
		}
		return false
	}
	if t == nil {
		return false
	}
	return types.Implements(t, a.errorType)
}

func (a *analyzer) sentinelOfGlobal(global *ssa.Global) *sentinel {
	if global == nil || global.Pkg == nil {
		return nil
	}
	return a.sentinels[global.Pkg.Pkg.Path()+"."+global.Name()]
}

// sentinelOfCall reports whether call is a direct refusal.New call.
func (a *analyzer) sentinelOfCall(call *ssa.Call) *sentinel {
	callee := call.Common().StaticCallee()
	if callee == nil || callee.Object() == nil {
		return nil
	}
	obj, ok := callee.Object().(*types.Func)
	if !ok || obj.Name() != "New" || obj.Pkg() == nil {
		return nil
	}
	if obj.Pkg().Path() != a.modulePath+"/internal/refusal" {
		return nil
	}
	parent := call.Parent()
	if parent == nil || parent.Pkg == nil {
		return nil
	}
	pos := a.position(call.Pos())
	if s, ok := a.findGlobalSentinelAt(pos); ok {
		return s
	}
	key := inlineSentinelKey(a.relPath(parent.Pkg.Pkg.Path()), pos)
	for _, s := range a.allSentinels {
		if s.Key == key {
			return s
		}
	}
	reason := "<non-constant>"
	if argument, ok := call.Common().Args[0].(*ssa.Const); ok && argument.Value != nil && argument.Value.Kind() == constant.String {
		reason = constant.StringVal(argument.Value)
	}
	s := &sentinel{Key: key, Reason: reason, Site: pos}
	a.allSentinels = append(a.allSentinels, s)
	return s
}

// ---------------------------------------------------------------------------
// roots
// ---------------------------------------------------------------------------

func (a *analyzer) collectRoots() ([]root, error) {
	var roots []root
	httpRoots, err := a.httpRoots()
	if err != nil {
		return nil, err
	}
	roots = append(roots, httpRoots...)
	websocketRoots, err := a.websocketRoots()
	if err != nil {
		return nil, err
	}
	roots = append(roots, websocketRoots...)
	workerRoots, err := a.workerRoots()
	if err != nil {
		return nil, err
	}
	roots = append(roots, workerRoots...)
	roots = append(roots, a.commandRoots()...)
	sort.SliceStable(roots, func(i, j int) bool {
		if roots[i].Kind != roots[j].Kind {
			return roots[i].Kind < roots[j].Kind
		}
		if roots[i].Route != roots[j].Route {
			return roots[i].Route < roots[j].Route
		}
		return functionKey(roots[i].Fn) < functionKey(roots[j].Fn)
	})
	return roots, nil
}

func (a *analyzer) serverPackage() *packages.Package {
	return a.pkgByPath[a.modulePath+"/cmd/server"]
}

func (a *analyzer) handlerPackage() *packages.Package {
	return a.pkgByPath[a.modulePath+"/internal/handler"]
}

// httpRoots pairs each e.Any call instruction with its path argument and
// handler method value.
func (a *analyzer) httpRoots() ([]root, error) {
	server := a.serverPackage()
	if server == nil {
		return nil, nil
	}
	ssaPackage := a.prog.Package(server.Types)
	if ssaPackage == nil {
		return nil, fmt.Errorf("cannot build SSA for package cmd/server")
	}
	paths := a.routePaths(server)
	var roots []root
	seen := map[string]bool{}
	for fn := range a.functions {
		if fn.Blocks == nil || fn.Pkg != ssaPackage {
			continue
		}
		for _, block := range fn.Blocks {
			for _, instruction := range block.Instrs {
				call, ok := instruction.(*ssa.Call)
				if !ok {
					continue
				}
				callee := call.Common().StaticCallee()
				if callee == nil || callee.Object() == nil {
					continue
				}
				obj, ok := callee.Object().(*types.Func)
				if !ok || obj.Name() != "Any" || obj.Pkg() == nil || obj.Pkg().Path() != "github.com/labstack/echo/v4" {
					continue
				}
				arguments := call.Common().Args
				offset := 0
				if signature := call.Common().Signature(); signature != nil && signature.Recv() != nil {
					offset = 1
				}
				if len(arguments) < offset+2 {
					continue
				}
				pathArgument := arguments[offset]
				handlerArgument := arguments[offset+1]
				routePaths, ok := paths[call.Pos()]
				if !ok || len(routePaths) == 0 {
					// Fallback for a literal path: the SSA value is a constant.
					if constantPath, ok := pathArgument.(*ssa.Const); ok && constantPath.Value != nil {
						routePaths = []string{constant.StringVal(constantPath.Value)}
					} else {
						return nil, fmt.Errorf("%s: cannot determine the path of an e.Any call", a.describe(call))
					}
				}
				handlers := a.resolveFunctions(handlerArgument, map[ssa.Value]bool{})
				if len(handlers) == 0 {
					var dump []string
					for _, argument := range call.Common().Args {
						dump = append(dump, fmt.Sprintf("%T %v", argument, argument))
					}
					return nil, fmt.Errorf("%s: cannot resolve the handler of route %s (args %s)", a.describe(call), routePaths[0], strings.Join(dump, ", "))
				}
				for _, path := range routePaths {
					pathHandlers := handlers
					if len(handlers) == 1 && isRouterStockServe(handlers[0]) {
						// The DefaultRouter path serves every viewset action:
						// root the actions registered for this prefix so the
						// report is per route, not a cross-product of every
						// DefaultRouter viewset.
						if actions := a.routerStockActions(server)[routerStockPrefixOf(path)]; len(actions) > 0 {
							pathHandlers = actions
						}
					}
					for _, handler := range pathHandlers {
						key := path + "\x00" + functionKey(handler)
						if seen[key] {
							continue
						}
						seen[key] = true
						roots = append(roots, root{Fn: handler, Kind: "http", Route: path, Method: "ANY"})
					}
				}
			}
		}
	}
	if len(roots) == 0 {
		return nil, fmt.Errorf("no e.Any routes found in cmd/server")
	}
	return roots, nil
}

func (a *analyzer) describe(value ssa.Value) string {
	if fn := value.Parent(); fn != nil && fn.Package() != nil {
		return a.position(value.Pos()).String() + " in " + fn.String()
	}
	return a.position(value.Pos()).String()
}

// routePaths walks the AST of the server package and evaluates each e.Any path
// expression, expanding range loops over string literals and over
// RouterStockHandler.Routes().
func (a *analyzer) routePaths(server *packages.Package) map[token.Pos][]string {
	result := map[token.Pos][]string{}
	prefixes := a.routerStockPrefixes(server)
	expandRoutes := func() []string {
		paths := []string{"/", "/.*"}
		for _, prefix := range prefixes {
			paths = append(paths, "/"+prefix+"/", "/"+prefix+"/*", "/"+prefix+".*")
		}
		return paths
	}
	for _, file := range server.Syntax {
		env := map[string][]string{}
		a.walkNode(file, env, expandRoutes, func(call *ast.CallExpr) {
			if len(call.Args) == 0 {
				return
			}
			values, ok := a.evalStrings(server, call.Args[0], env, expandRoutes)
			if !ok || len(values) == 0 {
				return
			}
			result[call.Lparen] = values
		})
	}
	return result
}

// routerStockPrefixes returns the prefixes of the viewsets registered on
// routerStock in the server's main file, in registration order.
func (a *analyzer) routerStockPrefixes(server *packages.Package) []string {
	var prefixes []string
	handler := a.handlerPackage()
	if handler == nil {
		return nil
	}
	for _, register := range a.routerStockRegisters(server) {
		decl := a.funcDecl(handler, register)
		if decl == nil || decl.Body == nil {
			continue
		}
		ast.Inspect(decl.Body, func(node ast.Node) bool {
			literal, ok := node.(*ast.CompositeLit)
			if !ok || !isRouterStockViewset(handler, literal) {
				return true
			}
			if prefix := literalPrefix(handler, literal); prefix != "" {
				prefixes = append(prefixes, prefix)
			}
			return true
		})
	}
	return prefixes
}

// routerStockRegisters returns the Register* methods called on routerStock in
// the server's main file, in registration order.
func (a *analyzer) routerStockRegisters(server *packages.Package) []*types.Func {
	var registers []*types.Func
	for _, file := range server.Syntax {
		ast.Inspect(file, func(node ast.Node) bool {
			call, ok := node.(*ast.CallExpr)
			if !ok {
				return true
			}
			selector, ok := call.Fun.(*ast.SelectorExpr)
			if !ok {
				return true
			}
			receiver, ok := selector.X.(*ast.Ident)
			if !ok || receiver.Name != "routerStock" {
				return true
			}
			obj, ok := server.TypesInfo.Uses[selector.Sel].(*types.Func)
			if !ok || !strings.HasPrefix(obj.Name(), "Register") {
				return true
			}
			registers = append(registers, obj)
			return true
		})
	}
	return registers
}

// routerStockActions maps each registered viewset prefix to the handler
// functions of its DefaultRouter actions.
func (a *analyzer) routerStockActions(server *packages.Package) map[string][]*ssa.Function {
	result := map[string][]*ssa.Function{}
	handler := a.handlerPackage()
	if handler == nil {
		return result
	}
	for _, register := range a.routerStockRegisters(server) {
		decl := a.funcDecl(handler, register)
		if decl == nil || decl.Body == nil {
			continue
		}
		ast.Inspect(decl.Body, func(node ast.Node) bool {
			literal, ok := node.(*ast.CompositeLit)
			if !ok || !isRouterStockViewset(handler, literal) {
				return true
			}
			prefix := literalPrefix(handler, literal)
			if prefix == "" {
				return true
			}
			seen := map[*ssa.Function]bool{}
			for _, field := range literal.Elts {
				pair, ok := field.(*ast.KeyValueExpr)
				if !ok {
					continue
				}
				if _, ok := handler.TypesInfo.TypeOf(pair.Value).Underlying().(*types.Signature); !ok {
					continue
				}
				for _, fn := range a.expressionFunctions(handler, pair.Value) {
					if fn == nil || seen[fn] {
						continue
					}
					seen[fn] = true
					result[prefix] = append(result[prefix], fn)
				}
			}
			return true
		})
	}
	return result
}

func isRouterStockViewset(handler *packages.Package, literal *ast.CompositeLit) bool {
	return namedTypeName(handler.TypesInfo.TypeOf(literal)) == "routerStockViewset"
}

func literalPrefix(handler *packages.Package, literal *ast.CompositeLit) string {
	prefix := ""
	for _, field := range literal.Elts {
		pair, ok := field.(*ast.KeyValueExpr)
		if !ok {
			continue
		}
		key, ok := pair.Key.(*ast.Ident)
		if !ok || key.Name != "prefix" {
			continue
		}
		prefix = constantString(handler, pair.Value)
	}
	return prefix
}

// routerStockPrefixOf maps one RouterStockHandler.Routes() path back to the
// viewset prefix it belongs to.
func routerStockPrefixOf(path string) string {
	trimmed := strings.TrimPrefix(path, "/")
	for _, suffix := range []string{"/*", ".*", "/"} {
		trimmed = strings.TrimSuffix(trimmed, suffix)
	}
	return trimmed
}

// isRouterStockServe reports whether fn is RouterStockHandler.Serve.
func isRouterStockServe(fn *ssa.Function) bool {
	if fn == nil {
		return false
	}
	object, ok := fn.Object().(*types.Func)
	if !ok || object.Name() != "Serve" || object.Pkg() == nil {
		return false
	}
	signature, ok := object.Type().(*types.Signature)
	if !ok || signature.Recv() == nil {
		return false
	}
	return namedTypeName(signature.Recv().Type()) == "RouterStockHandler"
}

func (a *analyzer) funcDecl(pkg *packages.Package, fn *types.Func) *ast.FuncDecl {
	for _, file := range pkg.Syntax {
		for _, decl := range file.Decls {
			funcDecl, ok := decl.(*ast.FuncDecl)
			if !ok || funcDecl.Name == nil {
				continue
			}
			if pkg.TypesInfo.Defs[funcDecl.Name] == fn {
				return funcDecl
			}
		}
	}
	return nil
}

// walkNode visits statements in source order while carrying the loop-variable
// environment used to evaluate path expressions.
func (a *analyzer) walkNode(node ast.Node, env map[string][]string, expandRoutes func() []string,
	visitCall func(*ast.CallExpr)) {
	switch value := node.(type) {
	case *ast.File:
		for _, decl := range value.Decls {
			a.walkNode(decl, env, expandRoutes, visitCall)
		}
	case *ast.FuncDecl:
		if value.Body != nil {
			a.walkNode(value.Body, env, expandRoutes, visitCall)
		}
	case *ast.GenDecl, *ast.ImportSpec, *ast.ValueSpec:
		// No route registrations in declarations.
	case *ast.BlockStmt:
		for _, statement := range value.List {
			a.walkNode(statement, env, expandRoutes, visitCall)
		}
	case *ast.ExprStmt:
		a.walkExpr(value.X, expandRoutes, visitCall)
	case *ast.AssignStmt:
		a.walkExpr(value.Rhs[0], expandRoutes, visitCall)
	case *ast.ReturnStmt:
		for _, expression := range value.Results {
			a.walkExpr(expression, expandRoutes, visitCall)
		}
	case *ast.IfStmt:
		if value.Init != nil {
			a.walkNode(value.Init, env, expandRoutes, visitCall)
		}
		a.walkNode(value.Body, env, expandRoutes, visitCall)
		if value.Else != nil {
			a.walkNode(value.Else, env, expandRoutes, visitCall)
		}
	case *ast.ForStmt:
		if value.Init != nil {
			a.walkNode(value.Init, env, expandRoutes, visitCall)
		}
		a.walkNode(value.Body, env, expandRoutes, visitCall)
	case *ast.RangeStmt:
		values, ok := a.evalRange(value.X, env, expandRoutes)
		if ok {
			bind := func(expr ast.Expr, bound []string) func() {
				ident, ok := expr.(*ast.Ident)
				if !ok || ident.Name == "_" {
					return func() {}
				}
				previous, existed := env[ident.Name]
				env[ident.Name] = bound
				return func() {
					if existed {
						env[ident.Name] = previous
					} else {
						delete(env, ident.Name)
					}
				}
			}
			restoreKey := bind(value.Key, []string{})
			restoreValue := bind(value.Value, values)
			a.walkNode(value.Body, env, expandRoutes, visitCall)
			restoreValue()
			restoreKey()
			return
		}
		a.walkNode(value.Body, env, expandRoutes, visitCall)
	case *ast.SwitchStmt:
		a.walkNode(value.Body, env, expandRoutes, visitCall)
	case *ast.CaseClause:
		for _, statement := range value.Body {
			a.walkNode(statement, env, expandRoutes, visitCall)
		}
	case *ast.TypeSwitchStmt:
		a.walkNode(value.Body, env, expandRoutes, visitCall)
	case *ast.SelectStmt:
		a.walkNode(value.Body, env, expandRoutes, visitCall)
	case *ast.CommClause:
		for _, statement := range value.Body {
			a.walkNode(statement, env, expandRoutes, visitCall)
		}
	case *ast.LabeledStmt:
		a.walkNode(value.Stmt, env, expandRoutes, visitCall)
	case *ast.GoStmt:
		a.walkExpr(value.Call, expandRoutes, visitCall)
	case *ast.DeferStmt:
		a.walkExpr(value.Call, expandRoutes, visitCall)
	}
}

func (a *analyzer) walkExpr(expr ast.Expr, expandRoutes func() []string, visitCall func(*ast.CallExpr)) {
	ast.Inspect(expr, func(node ast.Node) bool {
		switch value := node.(type) {
		case *ast.FuncLit:
			return false
		case *ast.CallExpr:
			visitCall(value)
		}
		return true
	})
}

func (a *analyzer) evalRange(expr ast.Expr, env map[string][]string, expandRoutes func() []string) ([]string, bool) {
	return a.evalStrings(a.serverPackage(), expr, env, expandRoutes)
}

// evalStrings evaluates a path expression to the set of string values it can
// take. It handles string literals, concatenation, loop variables and
// RouterStockHandler.Routes().
func (a *analyzer) evalStrings(pkg *packages.Package, expr ast.Expr, env map[string][]string,
	expandRoutes func() []string) ([]string, bool) {
	switch value := expr.(type) {
	case *ast.BasicLit:
		if value.Kind != token.STRING {
			return nil, false
		}
		text, err := strconv.Unquote(value.Value)
		if err != nil {
			return nil, false
		}
		return []string{text}, true
	case *ast.ParenExpr:
		return a.evalStrings(pkg, value.X, env, expandRoutes)
	case *ast.Ident:
		values, ok := env[value.Name]
		if !ok {
			return nil, false
		}
		return append([]string(nil), values...), true
	case *ast.CompositeLit:
		var values []string
		for _, element := range value.Elts {
			item, ok := a.evalStrings(pkg, element, env, expandRoutes)
			if !ok {
				return nil, false
			}
			values = append(values, item...)
		}
		return values, true
	case *ast.CallExpr:
		selector, ok := value.Fun.(*ast.SelectorExpr)
		if ok && selector.Sel.Name == "Routes" {
			return expandRoutes(), true
		}
		return nil, false
	case *ast.BinaryExpr:
		if value.Op != token.ADD {
			return nil, false
		}
		left, ok := a.evalStrings(pkg, value.X, env, expandRoutes)
		if !ok {
			return nil, false
		}
		right, ok := a.evalStrings(pkg, value.Y, env, expandRoutes)
		if !ok {
			return nil, false
		}
		values := make([]string, 0, len(left)*len(right))
		for _, prefix := range left {
			for _, suffix := range right {
				values = append(values, prefix+suffix)
			}
		}
		return values, true
	}
	return nil, false
}

// trustedCallSite reports whether VTA's callees at an edge's call site are
// specific enough to keep. Dynamic calls through a parameter, a free variable
// or a value loaded from foreign memory are resolved by function type alone,
// so VTA returns every function of that signature in the module.
func (a *analyzer) trustedCallSite(site ssa.CallInstruction) bool {
	if site == nil {
		return true
	}
	common := site.Common()
	if common.IsInvoke() || common.StaticCallee() != nil {
		return true
	}
	if len(a.resolveLocalFunctions(common.Value, map[ssa.Value]bool{})) > 0 {
		return true
	}
	if call, ok := common.Value.(*ssa.Call); ok && call.Common().StaticCallee() != nil {
		// A closure returned by a statically known constructor.
		return true
	}
	return false
}

// resolveLocalFunctions resolves function values whose origin is local to the
// caller: literals, bound methods, phis and loads from local allocs.
func (a *analyzer) resolveLocalFunctions(value ssa.Value, seen map[ssa.Value]bool) []*ssa.Function {
	if value == nil || seen[value] {
		return nil
	}
	seen[value] = true
	switch v := value.(type) {
	case *ssa.Function:
		return []*ssa.Function{v}
	case *ssa.MakeClosure:
		if fn, ok := v.Fn.(*ssa.Function); ok {
			return []*ssa.Function{fn}
		}
		return a.resolveLocalFunctions(v.Fn, seen)
	case *ssa.Phi:
		var result []*ssa.Function
		for _, edge := range v.Edges {
			result = append(result, a.resolveLocalFunctions(edge, seen)...)
		}
		return result
	case *ssa.UnOp:
		if v.Op == token.MUL {
			var result []*ssa.Function
			if alloc, ok := v.X.(*ssa.Alloc); ok {
				for _, referrer := range *alloc.Referrers() {
					if store, ok := referrer.(*ssa.Store); ok {
						result = append(result, a.resolveLocalFunctions(store.Val, seen)...)
					}
				}
			}
			return result
		}
	case *ssa.ChangeType:
		return a.resolveLocalFunctions(v.X, seen)
	case *ssa.ChangeInterface:
		return a.resolveLocalFunctions(v.X, seen)
	case *ssa.Convert:
		return a.resolveLocalFunctions(v.X, seen)
	case *ssa.MakeInterface:
		return a.resolveLocalFunctions(v.X, seen)
	case *ssa.Extract:
		return a.resolveLocalFunctions(v.Tuple, seen)
	}
	return nil
}

// resolveFunctions follows a handler SSA value to its function(s).
func (a *analyzer) resolveFunctions(value ssa.Value, seen map[ssa.Value]bool) []*ssa.Function {
	if value == nil || seen[value] {
		return nil
	}
	seen[value] = true
	switch v := value.(type) {
	case *ssa.Function:
		return []*ssa.Function{v}
	case *ssa.MakeClosure:
		if fn, ok := v.Fn.(*ssa.Function); ok {
			return []*ssa.Function{fn}
		}
		return a.resolveFunctions(v.Fn, seen)
	case *ssa.Phi:
		var result []*ssa.Function
		for _, edge := range v.Edges {
			result = append(result, a.resolveFunctions(edge, seen)...)
		}
		return result
	case *ssa.UnOp:
		if v.Op == token.MUL {
			var result []*ssa.Function
			if alloc, ok := v.X.(*ssa.Alloc); ok {
				for _, referrer := range *alloc.Referrers() {
					if store, ok := referrer.(*ssa.Store); ok {
						result = append(result, a.resolveFunctions(store.Val, seen)...)
					}
				}
			}
			return result
		}
	case *ssa.ChangeType:
		return a.resolveFunctions(v.X, seen)
	case *ssa.ChangeInterface:
		return a.resolveFunctions(v.X, seen)
	case *ssa.Convert:
		return a.resolveFunctions(v.X, seen)
	case *ssa.MakeInterface:
		return a.resolveFunctions(v.X, seen)
	case *ssa.Extract:
		return a.resolveFunctions(v.Tuple, seen)
	case *ssa.Call:
		// A middleware constructor returns a closure, so the useful root is
		// the handler function passed to it.
		var result []*ssa.Function
		for _, argument := range v.Common().Args {
			if a.isFunctionValue(argument) {
				result = append(result, a.resolveFunctions(argument, seen)...)
			}
		}
		return result
	}
	return nil
}

func (a *analyzer) isFunctionValue(value ssa.Value) bool {
	if value == nil {
		return false
	}
	_, ok := value.Type().Underlying().(*types.Signature)
	return ok
}

// websocketRoots returns every method of every consumer implementation and
// the ws path that selects it.
func (a *analyzer) websocketRoots() ([]root, error) {
	handler := a.handlerPackage()
	if handler == nil {
		return nil, nil
	}
	object, ok := handler.Types.Scope().Lookup("consumer").(*types.TypeName)
	if !ok {
		return nil, nil
	}
	iface, ok := object.Type().Underlying().(*types.Interface)
	if !ok {
		return nil, nil
	}
	paths := a.websocketPaths(handler)
	var roots []root
	seen := map[string]bool{}
	for _, name := range handler.Types.Scope().Names() {
		typeName, ok := handler.Types.Scope().Lookup(name).(*types.TypeName)
		if !ok {
			continue
		}
		typ := typeName.Type()
		var candidates []types.Type
		if types.Implements(typ, iface) {
			candidates = append(candidates, typ)
		}
		if types.Implements(types.NewPointer(typ), iface) {
			candidates = append(candidates, types.NewPointer(typ))
		}
		if len(candidates) == 0 {
			continue
		}
		route, ok := paths[name]
		if !ok {
			route = "*"
		}
		for i := 0; i < iface.NumMethods(); i++ {
			method := iface.Method(i)
			obj, _, _ := types.LookupFieldOrMethod(candidates[0], true, handler.Types, method.Name())
			fn, ok := obj.(*types.Func)
			if !ok {
				continue
			}
			implementation := a.prog.FuncValue(fn)
			if implementation == nil {
				continue
			}
			key := route + "\x00" + functionKey(implementation)
			if seen[key] {
				continue
			}
			seen[key] = true
			roots = append(roots, root{Fn: implementation, Kind: "websocket", Route: route, Method: "GET"})
		}
	}
	return roots, nil
}

// websocketPaths maps a consumer type name to the switch case path that
// selects it.
func (a *analyzer) websocketPaths(handler *packages.Package) map[string]string {
	result := map[string]string{}
	for _, file := range handler.Syntax {
		ast.Inspect(file, func(node ast.Node) bool {
			switchStatement, ok := node.(*ast.SwitchStmt)
			if !ok || switchStatement.Tag == nil {
				return true
			}
			selector, ok := switchStatement.Tag.(*ast.SelectorExpr)
			if !ok || selector.Sel.Name != "path" {
				return true
			}
			for _, clause := range switchStatement.Body.List {
				caseClause, ok := clause.(*ast.CaseClause)
				if !ok {
					return true
				}
				path := ""
				for _, expression := range caseClause.List {
					if literal, ok := expression.(*ast.BasicLit); ok && literal.Kind == token.STRING {
						if text, err := strconv.Unquote(literal.Value); err == nil {
							path = text
						}
					}
				}
				if path == "" {
					continue
				}
				for _, statement := range caseClause.Body {
					ast.Inspect(statement, func(inner ast.Node) bool {
						assign, ok := inner.(*ast.AssignStmt)
						if !ok || len(assign.Lhs) != 1 || len(assign.Rhs) != 1 {
							return true
						}
						target, ok := assign.Lhs[0].(*ast.SelectorExpr)
						if !ok || target.Sel.Name != "consumer" {
							return true
						}
						if name := namedTypeName(handler.TypesInfo.TypeOf(assign.Rhs[0])); name != "" {
							result[name] = path
						}
						return true
					})
				}
			}
			return true
		})
	}
	return result
}

func namedTypeName(t types.Type) string {
	if t == nil {
		return ""
	}
	if pointer, ok := t.(*types.Pointer); ok {
		t = pointer.Elem()
	}
	if named, ok := t.(*types.Named); ok {
		return named.Obj().Name()
	}
	return ""
}

// workerRoots returns every handler registered in a JobDefinition map in
// cmd/worker, labelled with the job name.
func (a *analyzer) workerRoots() ([]root, error) {
	worker := a.pkgByPath[a.modulePath+"/cmd/worker"]
	if worker == nil {
		return nil, nil
	}
	var roots []root
	seen := map[string]bool{}
	for _, file := range worker.Syntax {
		ast.Inspect(file, func(node ast.Node) bool {
			literal, ok := node.(*ast.CompositeLit)
			if !ok || !isJobDefinitionMap(literal.Type) {
				return true
			}
			for _, element := range literal.Elts {
				pair, ok := element.(*ast.KeyValueExpr)
				if !ok {
					continue
				}
				jobName := constantString(worker, pair.Key)
				if jobName == "" {
					jobName = expressionText(pair.Key)
				}
				value, ok := pair.Value.(*ast.CompositeLit)
				if !ok {
					continue
				}
				for _, field := range value.Elts {
					fieldPair, ok := field.(*ast.KeyValueExpr)
					if !ok {
						continue
					}
					key, ok := fieldPair.Key.(*ast.Ident)
					if !ok || key.Name != "Run" {
						continue
					}
					for _, fn := range a.expressionFunctions(worker, fieldPair.Value) {
						keyName := jobName + "\x00" + functionKey(fn)
						if seen[keyName] {
							continue
						}
						seen[keyName] = true
						roots = append(roots, root{Fn: fn, Kind: "worker", Route: jobName})
					}
				}
			}
			return true
		})
	}
	return roots, nil
}

func isJobDefinitionMap(expr ast.Expr) bool {
	mapType, ok := expr.(*ast.MapType)
	if !ok {
		return false
	}
	selector, ok := mapType.Value.(*ast.SelectorExpr)
	return ok && selector.Sel.Name == "JobDefinition"
}

func constantString(pkg *packages.Package, expr ast.Expr) string {
	tv, ok := pkg.TypesInfo.Types[expr]
	if !ok || tv.Value == nil || tv.Value.Kind() != constant.String {
		return ""
	}
	return constant.StringVal(tv.Value)
}

func expressionText(expr ast.Expr) string {
	switch value := expr.(type) {
	case *ast.Ident:
		return value.Name
	case *ast.SelectorExpr:
		return expressionText(value.X) + "." + value.Sel.Name
	case *ast.BasicLit:
		return value.Value
	}
	return "<expression>"
}

func (a *analyzer) expressionFunctions(pkg *packages.Package, expr ast.Expr) []*ssa.Function {
	switch value := expr.(type) {
	case *ast.SelectorExpr:
		var obj *types.Func
		if selection, ok := pkg.TypesInfo.Selections[value]; ok {
			obj, _ = selection.Obj().(*types.Func)
		} else if used, ok := pkg.TypesInfo.Uses[value.Sel].(*types.Func); ok {
			obj = used
		}
		if obj != nil {
			if fn := a.prog.FuncValue(obj); fn != nil {
				return []*ssa.Function{fn}
			}
		}
	case *ast.Ident:
		if obj, ok := pkg.TypesInfo.Uses[value].(*types.Func); ok {
			if fn := a.prog.FuncValue(obj); fn != nil {
				return []*ssa.Function{fn}
			}
		}
	case *ast.FuncLit:
		for fn := range a.functions {
			if fn.Pos() == value.Pos() {
				return []*ssa.Function{fn}
			}
		}
	}
	return nil
}

// commandRoots returns the main function of every command other than the
// server and the worker.
func (a *analyzer) commandRoots() []root {
	var roots []root
	for _, pkg := range a.pkgs {
		if pkg.Name != "main" || pkg.Types == nil {
			continue
		}
		prefix := a.modulePath + "/cmd/"
		if !strings.HasPrefix(pkg.PkgPath, prefix) {
			continue
		}
		if pkg.PkgPath == a.modulePath+"/cmd/server" || pkg.PkgPath == a.modulePath+"/cmd/worker" {
			continue
		}
		fn := a.prog.Package(pkg.Types).Func("main")
		if fn == nil {
			continue
		}
		roots = append(roots, root{Fn: fn, Kind: "command", Route: strings.TrimPrefix(pkg.PkgPath, prefix)})
	}
	return roots
}

// ---------------------------------------------------------------------------
// call graph and report
// ---------------------------------------------------------------------------

func functionKey(fn *ssa.Function) string {
	if fn == nil {
		return ""
	}
	return fn.String()
}

// isModuleFunction reports whether fn belongs to this module. Synthetic
// wrappers (for example (*T).M$bound) carry no package, but their object does.
func (a *analyzer) isModuleFunction(fn *ssa.Function) bool {
	if fn == nil {
		return false
	}
	path := ""
	if fn.Package() != nil {
		path = fn.Package().Pkg.Path()
	} else if object := fn.Object(); object != nil && object.Pkg() != nil {
		path = object.Pkg().Path()
	}
	if path == "" {
		return false
	}
	return path == a.modulePath || strings.HasPrefix(path, a.modulePath+"/")
}

func (a *analyzer) report(roots []root, targets map[*ssa.Function]map[*sentinel]token.Position) (*report, error) {
	chaGraph := cha.CallGraph(a.prog)
	// Analyze with VTA only the functions CHA can reach from the roots; the
	// reduced set keeps the type propagation bounded and is still complete
	// for reachability because CHA over-approximates VTA's edges.
	reachable := map[*ssa.Function]bool{}
	for _, r := range roots {
		chaReach(chaGraph, r.Fn, reachable)
	}
	graph := vta.CallGraph(reachable, chaGraph)

	adjacency := map[*ssa.Function][]*ssa.Function{}
	addEdge := func(from, to *ssa.Function) {
		if to == nil || !a.isModuleFunction(to) {
			return
		}
		adjacency[from] = append(adjacency[from], to)
	}
	for fn, node := range graph.Nodes {
		for _, edge := range node.Out {
			// Stop at the module boundary: replaying through net/http, echo or
			// pgx callbacks would only add framework re-entry, never a module
			// refusal return site.
			if edge.Callee == nil || edge.Callee.Func == nil || !a.isModuleFunction(edge.Callee.Func) {
				continue
			}
			// A dynamic call through a parameter, free variable or foreign
			// field is resolved by VTA by function type alone, so it can reach
			// every function of that signature in the module (including other
			// route handlers). The callback and stored-value edges added below
			// keep the real caller-to-function relationships without that
			// cross-product.
			if edge.Site != nil && !a.trustedCallSite(edge.Site) {
				continue
			}
			addEdge(fn, edge.Callee.Func)
		}
		if fn.Blocks == nil {
			continue
		}
		for _, block := range fn.Blocks {
			for _, instruction := range block.Instrs {
				switch ins := instruction.(type) {
				case *ssa.Call:
					// Function values passed as arguments are callbacks:
					// connect the caller to them directly.
					for _, argument := range ins.Common().Args {
						for _, callback := range a.resolveFunctions(argument, map[ssa.Value]bool{}) {
							addEdge(fn, callback)
						}
					}
				case *ssa.Store:
					// Function values stored in a struct field, slice or map
					// element are handlers registered for later dispatch.
					for _, stored := range a.resolveLocalFunctions(ins.Val, map[ssa.Value]bool{}) {
						addEdge(fn, stored)
					}
				case *ssa.MapUpdate:
					for _, stored := range a.resolveLocalFunctions(ins.Value, map[ssa.Value]bool{}) {
						addEdge(fn, stored)
					}
				case *ssa.Send:
					for _, stored := range a.resolveLocalFunctions(ins.X, map[ssa.Value]bool{}) {
						addEdge(fn, stored)
					}
				case *ssa.Return:
					for _, result := range ins.Results {
						for _, stored := range a.resolveLocalFunctions(result, map[ssa.Value]bool{}) {
							addEdge(fn, stored)
						}
					}
				}
			}
		}
	}
	for fn := range adjacency {
		callees := adjacency[fn]
		sort.Slice(callees, func(i, j int) bool { return functionKey(callees[i]) < functionKey(callees[j]) })
		adjacency[fn] = callees
	}

	rows := map[string]row{}
	reached := map[*sentinel]bool{}
	for _, r := range roots {
		if r.Fn == nil {
			continue
		}
		chains := shortestChains(adjacency, r.Fn)
		for fn, chain := range chains {
			sites, ok := targets[fn]
			if !ok {
				continue
			}
			for sentinel, _ := range sites {
				reached[sentinel] = true
				key := r.Kind + "\x00" + r.Route + "\x00" + sentinel.Key
				candidate := row{
					Route:    r.Route,
					Method:   r.Method,
					RootKind: r.Kind,
					Sentinel: sentinel.Key,
					Reason:   sentinel.Reason,
					Site:     fileLine(sentinel.Site),
					Chain:    chain,
				}
				existing, exists := rows[key]
				if !exists || betterChain(candidate.Chain, existing.Chain) {
					rows[key] = candidate
				}
			}
		}
	}

	output := &report{Rows: []row{}}
	for _, value := range rows {
		output.Rows = append(output.Rows, value)
	}
	sort.Slice(output.Rows, func(i, j int) bool {
		if output.Rows[i].Route != output.Rows[j].Route {
			return output.Rows[i].Route < output.Rows[j].Route
		}
		if output.Rows[i].Sentinel != output.Rows[j].Sentinel {
			return output.Rows[i].Sentinel < output.Rows[j].Sentinel
		}
		return output.Rows[i].RootKind < output.Rows[j].RootKind
	})
	output.NoRoot = []orphan{}
	for _, sentinel := range a.allSentinels {
		if reached[sentinel] {
			continue
		}
		output.NoRoot = append(output.NoRoot, orphan{Sentinel: sentinel.Key, Reason: sentinel.Reason,
			Site: fileLine(sentinel.Site)})
	}
	sort.Slice(output.NoRoot, func(i, j int) bool {
		if output.NoRoot[i].Site != output.NoRoot[j].Site {
			return output.NoRoot[i].Site < output.NoRoot[j].Site
		}
		return output.NoRoot[i].Sentinel < output.NoRoot[j].Sentinel
	})
	return output, nil
}

// chaReach marks every function reachable from fn in the CHA graph.
func chaReach(graph *callgraph.Graph, fn *ssa.Function, reachable map[*ssa.Function]bool) {
	if fn == nil || reachable[fn] {
		return
	}
	stack := []*ssa.Function{fn}
	for len(stack) > 0 {
		current := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if reachable[current] {
			continue
		}
		reachable[current] = true
		node := graph.Nodes[current]
		if node == nil {
			continue
		}
		for _, edge := range node.Out {
			if edge.Callee != nil && edge.Callee.Func != nil && !reachable[edge.Callee.Func] {
				stack = append(stack, edge.Callee.Func)
			}
		}
	}
}

// shortestChains returns one shortest call chain from root to every reachable
// function. Neighbours are visited in sorted order, so ties are deterministic.
func shortestChains(adjacency map[*ssa.Function][]*ssa.Function, root *ssa.Function) map[*ssa.Function][]string {
	chains := map[*ssa.Function][]string{root: {functionKey(root)}}
	queue := []*ssa.Function{root}
	for len(queue) > 0 {
		current := queue[0]
		queue = queue[1:]
		for _, next := range adjacency[current] {
			if _, seen := chains[next]; seen {
				continue
			}
			chains[next] = append(append([]string(nil), chains[current]...), functionKey(next))
			queue = append(queue, next)
		}
	}
	return chains
}

func betterChain(candidate, existing []string) bool {
	if len(candidate) != len(existing) {
		return len(candidate) < len(existing)
	}
	return strings.Join(candidate, "\x00") < strings.Join(existing, "\x00")
}
