package handoff

import (
	"context"
	"regexp"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

// QueryTracer records every SQL statement that may write as an effect of the
// request whose context runs it. It is conservative: a write inside a
// transaction that is later rolled back still counts, so a refusal after it is
// not handed off.
type QueryTracer struct{}

var (
	_ pgx.QueryTracer    = QueryTracer{}
	_ pgx.BatchTracer    = QueryTracer{}
	_ pgx.CopyFromTracer = QueryTracer{}
)

func (QueryTracer) TraceQueryStart(ctx context.Context, _ *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	s := fromContext(ctx)
	if s == nil {
		return ctx
	}
	if SQLWrites(data.SQL) {
		MarkEffect(ctx, "database write")
	}
	if delta := transactionDelta(data.SQL); delta != 0 {
		s.mu.Lock()
		s.transactions = max(0, s.transactions+delta)
		s.mu.Unlock()
	}
	return ctx
}

func (QueryTracer) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func (QueryTracer) TraceBatchStart(ctx context.Context, _ *pgx.Conn, _ pgx.TraceBatchStartData) context.Context {
	return ctx
}

func (QueryTracer) TraceBatchQuery(ctx context.Context, _ *pgx.Conn, data pgx.TraceBatchQueryData) {
	if fromContext(ctx) != nil && SQLWrites(data.SQL) {
		MarkEffect(ctx, "database write")
	}
}

func (QueryTracer) TraceBatchEnd(context.Context, *pgx.Conn, pgx.TraceBatchEndData) {}

func (QueryTracer) TraceCopyFromStart(ctx context.Context, _ *pgx.Conn, _ pgx.TraceCopyFromStartData) context.Context {
	MarkEffect(ctx, "database write")
	return ctx
}

func (QueryTracer) TraceCopyFromEnd(context.Context, *pgx.Conn, pgx.TraceCopyFromEndData) {}

// readStatements start statements that change no data (transaction control,
// session settings, locks and cursors included).
var readStatements = map[string]bool{
	"select": true, "show": true, "values": true, "table": true, "explain": true, "begin": true, "start": true,
	"commit": true, "end": true, "rollback": true, "abort": true, "savepoint": true, "release": true, "set": true,
	"reset": true, "lock": true, "declare": true, "fetch": true, "move": true, "close": true, "deallocate": true,
	"discard": true, "prepare": true,
}

var (
	sqlComment = regexp.MustCompile(`(?s)^(\s+|--[^\n]*\n?|/\*.*?\*/|\()`)
	sqlWord    = regexp.MustCompile(`^[A-Za-z_]+`)
	// A data-modifying CTE, or a sequence advanced by a read statement.
	sqlWriteInRead = regexp.MustCompile(`(?i)\b(insert\s+into|update\s+[\w".]+\s+set|delete\s+from|merge\s+into|nextval\s*\(|setval\s*\(|lo_import|lo_unlink|pg_notify\s*\()`)
)

// transactionDelta is +1 for a statement opening a transaction and -1 for one
// ending it. Django's replay would wait on the row locks of a transaction the
// request still holds, so Serve does not hand off while one is open.
func transactionDelta(sql string) int {
	words := strings.Fields(strings.ToLower(stripSQLPrefix(sql)))
	if len(words) == 0 {
		return 0
	}
	switch words[0] {
	case "begin":
		return 1
	case "start":
		if len(words) > 1 && strings.HasPrefix(words[1], "transaction") {
			return 1
		}
	case "commit", "end", "abort":
		return -1
	case "rollback":
		if len(words) > 1 && (words[1] == "to" || strings.HasPrefix(words[1], "prepared")) {
			return 0
		}
		return -1
	}
	return 0
}

func stripSQLPrefix(sql string) string {
	for {
		match := sqlComment.FindString(sql)
		if match == "" {
			return sql
		}
		sql = sql[len(match):]
	}
}

// SQLWrites reports whether a statement may change data. Unknown statements
// count as writes.
func SQLWrites(sql string) bool {
	rest := stripSQLPrefix(sql)
	word := strings.ToLower(sqlWord.FindString(rest))
	if word == "with" || readStatements[word] {
		return sqlWriteInRead.MatchString(rest)
	}
	return true
}

// redisReads are the Redis commands Go sends that change nothing.
var redisReads = map[string]bool{
	"get": true, "mget": true, "getrange": true, "strlen": true, "exists": true, "ttl": true, "pttl": true,
	"expiretime": true, "pexpiretime": true, "type": true, "keys": true, "scan": true, "randomkey": true, "dbsize": true,
	"hget": true, "hmget": true, "hgetall": true, "hexists": true, "hlen": true, "hkeys": true, "hvals": true,
	"hscan": true, "hstrlen": true, "lrange": true, "llen": true, "lindex": true, "lpos": true, "smembers": true,
	"sismember": true, "smismember": true, "scard": true, "sscan": true, "srandmember": true, "zrange": true,
	"zrangebyscore": true, "zrevrange": true, "zrevrangebyscore": true, "zcard": true, "zcount": true, "zscore": true,
	"zmscore": true, "zrank": true, "zrevrank": true, "zscan": true, "xrange": true, "xrevrange": true, "xlen": true,
	"getbit": true, "bitcount": true, "ping": true, "echo": true, "time": true, "info": true, "select": true,
	"auth": true, "hello": true, "client": true, "command": true, "readonly": true, "eval_ro": true,
	"evalsha_ro": true, "memory": true, "object": true, "dump": true, "pubsub": true,
}

// RedisHook records Redis commands that may write as effects of the request.
type RedisHook struct{}

var _ redis.Hook = RedisHook{}

func (RedisHook) DialHook(next redis.DialHook) redis.DialHook { return next }

func (RedisHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		if fromContext(ctx) != nil && !redisReads[strings.ToLower(cmd.Name())] {
			MarkEffect(ctx, "redis "+strings.ToLower(cmd.Name()))
		}
		return next(ctx, cmd)
	}
}

func (RedisHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(ctx context.Context, cmds []redis.Cmder) error {
		if fromContext(ctx) != nil {
			for _, cmd := range cmds {
				if name := strings.ToLower(cmd.Name()); !redisReads[name] && name != "multi" && name != "exec" {
					MarkEffect(ctx, "redis "+name)
					break
				}
			}
		}
		return next(ctx, cmds)
	}
}
