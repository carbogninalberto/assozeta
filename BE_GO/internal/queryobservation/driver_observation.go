package queryobservation

import "context"

// Driver operation names form a closed vocabulary. They match the optional
// database.DriverObserver interface without introducing a database dependency.
const (
	DriverOperationConnect = "connect"
	DriverOperationReset   = "reset"
	DriverOperationPing    = "ping"
)

type driverKey struct{}
type driverKind uint8

const (
	driverUnknown driverKind = iota
	driverConnect
	driverReset
	driverPing
)

func parseDriverKind(kind string) driverKind {
	switch kind {
	case DriverOperationConnect:
		return driverConnect
	case DriverOperationReset:
		return driverReset
	case DriverOperationPing:
		return driverPing
	default:
		return driverUnknown
	}
}

type driverCounters struct {
	DriverStarts        uint64 `json:"driver_starts"`
	DriverEnds          uint64 `json:"driver_ends"`
	DriverErrors        uint64 `json:"driver_errors"`
	DriverUnboundStarts uint64 `json:"driver_unbound_starts"`
	DriverUnboundEnds   uint64 `json:"driver_unbound_ends"`
	ConnectStarts       uint64 `json:"connect_starts"`
	ConnectEnds         uint64 `json:"connect_ends"`
	ConnectErrors       uint64 `json:"connect_errors"`
	ResetStarts         uint64 `json:"reset_starts"`
	ResetEnds           uint64 `json:"reset_ends"`
	ResetErrors         uint64 `json:"reset_errors"`
	PingStarts          uint64 `json:"ping_starts"`
	PingEnds            uint64 `json:"ping_ends"`
	PingErrors          uint64 `json:"ping_errors"`
	ResetProbes         uint64 `json:"reset_probes"`
	DriverViolations    uint64 `json:"driver_violations"`
	UnknownDriverKinds  uint64 `json:"unknown_driver_kinds"`
	InvalidResetProbes  uint64 `json:"invalid_reset_probes"`
}

func (c driverCounters) clean() bool {
	return c.DriverStarts == c.DriverEnds && c.DriverErrors == 0 &&
		c.DriverUnboundStarts == 0 && c.DriverUnboundEnds == 0 &&
		c.ConnectStarts == c.ConnectEnds && c.ConnectErrors == 0 &&
		c.ResetStarts == c.ResetEnds && c.ResetErrors == 0 &&
		c.PingStarts == c.PingEnds && c.PingErrors == 0 &&
		c.ResetProbes <= c.ResetStarts && c.DriverViolations == 0 &&
		c.UnknownDriverKinds == 0 && c.InvalidResetProbes == 0
}

func (c *driverCounters) start(kind driverKind) {
	c.DriverStarts++
	switch kind {
	case driverConnect:
		c.ConnectStarts++
	case driverReset:
		c.ResetStarts++
	case driverPing:
		c.PingStarts++
	}
}

func (c *driverCounters) end(kind driverKind, err error) {
	c.DriverEnds++
	if err != nil {
		c.DriverErrors++
	}
	switch kind {
	case driverConnect:
		c.ConnectEnds++
		if err != nil {
			c.ConnectErrors++
		}
	case driverReset:
		c.ResetEnds++
		if err != nil {
			c.ResetErrors++
		}
	case driverPing:
		c.PingEnds++
		if err != nil {
			c.PingErrors++
		}
	}
}

type driverOperation struct {
	owner   *Observer
	request *request
	kind    driverKind
	ended   bool
	probed  bool
}

// BeginDriverOperation derives an operation context preserving all parent values,
// deadlines and cancellation. Call finish exactly once with the underlying
// driver's error, including failures before any query is traced. Caller strings
// are reduced to a fixed enum; unknown names invalidate the observation and are
// never retained or serialized. The finish closure owns its original token, so
// it cannot release a different operation by receiving a different context.
func (o *Observer) BeginDriverOperation(ctx context.Context, kind string) (context.Context, func(error)) {
	o.mu.Lock()
	defer o.mu.Unlock()
	op := &driverOperation{owner: o, kind: parseDriverKind(kind)}
	o.counts.start(op.kind)
	if o.closed {
		o.counts.DriverViolations++
		o.afterCloseLocked()
	} else {
		r, _ := ctx.Value(requestKey{}).(*request)
		switch {
		case r == nil:
			o.counts.DriverUnboundStarts++
			o.driverViolationLocked(nil, func(c *counters) { c.MissingContexts++ })
		case r.owner != o:
			o.counts.DriverUnboundStarts++
			o.driverViolationLocked(nil, func(c *counters) { c.ForeignContexts++ })
		default:
			op.request = r
			r.counts.start(op.kind)
			r.activeDriver++
			if r.finished {
				o.driverViolationLocked(r, func(c *counters) { c.LateStarts++ })
			}
		}
		if op.kind == driverUnknown {
			o.driverViolationLocked(op.request, func(c *counters) { c.UnknownDriverKinds++ })
		}
		if old, _ := ctx.Value(driverKey{}).(*driverOperation); old != nil {
			if old.owner == o {
				o.driverViolationLocked(op.request, func(c *counters) { c.ReusedContexts++ })
			} else {
				o.driverViolationLocked(op.request, func(c *counters) { c.ForeignContexts++ })
			}
		}
		o.activeDriverOperations++
	}
	return context.WithValue(ctx, driverKey{}, op), func(err error) { o.finishDriver(op, err) }
}

func (o *Observer) finishDriver(op *driverOperation, err error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.counts.end(op.kind, err)
	if o.closed {
		o.counts.DriverViolations++
		o.afterCloseLocked()
		return
	}
	r := op.request
	if r == nil {
		o.counts.DriverUnboundEnds++
	} else {
		r.counts.end(op.kind, err)
		if r.finished {
			o.driverViolationLocked(r, func(c *counters) { c.LateEnds++ })
		}
	}
	if op.ended {
		o.driverViolationLocked(r, func(c *counters) { c.DuplicateEnds++ })
		return
	}
	op.ended = true
	o.activeDriverOperations--
	if r != nil {
		r.activeDriver--
	}
}

// ObserveResetProbe records the pinned stdlib ShouldPing decision immediately
// before its attempted PgConn.Ping. It is not confirmation of a wire statement.
// Each active reset token allows at most one probe, in its original request
// context. Invalid callbacks remain visible and make acceptance fail closed.
func (o *Observer) ObserveResetProbe(ctx context.Context) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.counts.ResetProbes++
	if o.closed {
		o.counts.DriverViolations++
		o.counts.InvalidResetProbes++
		o.afterCloseLocked()
		return
	}
	op, _ := ctx.Value(driverKey{}).(*driverOperation)
	current, _ := ctx.Value(requestKey{}).(*request)
	var r *request
	if op != nil && op.owner == o {
		r = op.request
	} else if current != nil && current.owner == o {
		r = current
	}
	if r != nil {
		r.counts.ResetProbes++
	}
	invalid := func(add func(*counters)) {
		o.driverViolationLocked(r, func(c *counters) { c.InvalidResetProbes++; add(c) })
	}
	if r != nil && r.finished {
		invalid(func(c *counters) { c.LateEnds++ })
		return
	}
	switch {
	case op == nil:
		invalid(func(c *counters) { c.MissingContexts++ })
	case op.owner != o:
		invalid(func(c *counters) { c.ForeignContexts++ })
	case r == nil:
		invalid(func(c *counters) { c.MissingContexts++ })
	case current != r:
		invalid(func(c *counters) { c.ContextMismatches++ })
	case op.kind != driverReset:
		invalid(func(c *counters) {})
	case op.ended:
		invalid(func(c *counters) { c.LateEnds++ })
	case op.probed:
		invalid(func(c *counters) { c.DuplicateEnds++ })
	default:
		op.probed = true
	}
}

func (o *Observer) driverViolationLocked(r *request, add func(*counters)) {
	o.violationLocked(r, func(c *counters) { c.DriverViolations++; add(c) })
}
