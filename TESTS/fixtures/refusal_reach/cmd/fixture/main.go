// Command fixture is the analyzer root: an "other main" that calls every case.
package main

import "fixture/refusalreach/cases"

func main() {
	_ = cases.Direct()
	_ = cases.Wrapped()
	_ = cases.Dispatch(cases.Impl())
	_ = cases.Check(nil)
}
