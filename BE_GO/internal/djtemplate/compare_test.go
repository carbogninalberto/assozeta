package djtemplate

import (
	"math"
	"testing"
)

// Python 3.13:
//
//	>>> [eval('float("%s") %s 0' % (v, o)) for v in ('inf', '-inf', 'nan') for o in ('<', '>', '<=', '>=')]
//	[False, True, False, True, True, False, True, False, False, False, False, False]
//	>>> [eval('0 %s float("%s")' % (o, v)) for v in ('inf', '-inf', 'nan') for o in ('<', '>', '<=', '>=')]
//	[True, False, True, False, False, True, False, True, False, False, False, False]
//	>>> (float('inf') > 1e300, float('-inf') < -1e300, float('inf') <= float('inf'),
//	...  float('inf') < float('inf'), float('inf') < float('-inf'))
//	(True, True, True, False, False)
func TestPythonCompareNonFinite(t *testing.T) {
	comparison := func(op string, left, right any, want bool) {
		t.Helper()
		got, err := pythonCompare(op, left, right)
		if err != nil {
			t.Fatalf("%v %s %v: %v", left, op, right, err)
		}
		if got != want {
			t.Errorf("%v %s %v = %v, want %v", left, op, right, got, want)
		}
	}
	inf, negInf, nan := math.Inf(1), math.Inf(-1), math.NaN()
	for _, op := range []string{"<", ">", "<=", ">="} {
		comparison(op, inf, int64(0), op == ">" || op == ">=")
		comparison(op, negInf, int64(0), op == "<" || op == "<=")
		comparison(op, nan, int64(0), false)
		comparison(op, int64(0), inf, op == "<" || op == "<=")
		comparison(op, int64(0), negInf, op == ">" || op == ">=")
		comparison(op, int64(0), nan, false)
	}
	comparison(">", inf, 1e300, true)
	comparison("<", negInf, -1e300, true)
	comparison("<=", inf, inf, true)
	comparison(">=", inf, inf, true)
	comparison("<", inf, inf, false)
	comparison("<", inf, negInf, false)
	if _, err := pythonCompare("<", nan, "x"); err == nil {
		t.Fatal("nan < str accepted")
	}
}
