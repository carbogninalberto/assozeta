# Transaction protocol query costs

The Go server-drain protocol adds one acknowledged shared-lock statement per
owned transaction. This table records the exact before/after expectations in the
PostgreSQL regressions. Historical Django fixture counts are unchanged. The
metric column distinguishes total statements from counters excluding transaction
control; business-method call counters are not total SQL. See the
[drain protocol](transaction-drain.md) for the snapshot and lifecycle boundaries.

This table isolates the server-fence increment. The later associate-age worker
adds one early ownership/lease admission statement in each body transaction:
its current successful/no-op budget is **six**, after the five shown below.
The separate [age task contract](../../../BE_GO/internal/service/associate_age_update.md)
records that additional fixed cost and its rejection/rollback cases.

| Component | Case | Before | With server fence | Metric |
|---|---|---|---|---|
| Jobs.Claim | valid mutation | 1 | 4 | all SQL statements: BEGIN, shared fence, effect, COMMIT |
| Jobs.Renew | valid mutation | 1 | 4 | all SQL statements: BEGIN, shared fence, effect, COMMIT |
| Jobs.Complete | valid mutation | 1 | 4 | all SQL statements: BEGIN, shared fence, effect, COMMIT |
| Jobs.Fail | valid mutation | 1 | 4 | all SQL statements: BEGIN, shared fence, effect, COMMIT |
| Jobs.CancelPending | valid mutation | 1 | 4 | all SQL statements: BEGIN, shared fence, effect, COMMIT |
| Jobs.CancelPending | missing/conflict fallback | 2 | 5 | all SQL statements: BEGIN, shared fence, UPDATE, same-transaction lookup, ROLLBACK |
| Jobs | invalid input before transaction | 0 | 0 | all SQL statements |
| pager | 16 successful publications from new rotation | 178 | 194 | all SQL statements: 2 discovery + 16*(11 prior +1 fence); replaces former ceiling 194 with exact 194 |
| email balance reset | valid task with 0..N affected rows | 5 | 6 | all SQL statements including transaction control |
| associate age update | valid task with 0..N affected rows | 4 | 5 | all SQL statements including transaction control |
| audit retention | valid task with 0..N affected rows | 4 | 5 | all SQL statements including transaction control |
| subscription token cleanup | valid task with 0..N affected rows | 4 | 5 | all SQL statements including transaction control |
| subscription auto archive | unlinked N rows | 2*ceil(N/100)+4 | 2*ceil(N/100)+5 | all SQL statements |
| subscription auto archive | linked N rows | 6*ceil(N/100)+4 | 6*ceil(N/100)+5 | all SQL statements |
| all task handlers | validation rejection before transaction | 0 | 0 | all SQL statements |
| login profile | each fixture outcome | Django fixture Queries | Django fixture Queries +1 | SQL statements excluding BEGIN/COMMIT |
| login profile | 32 concurrent reads | 64 | 96 | SQL statements excluding BEGIN/COMMIT |
| association profile | each fixture outcome | Django fixture Queries | Django fixture Queries +1 | SQL statements excluding BEGIN/COMMIT |
| association profile | 32 concurrent reads | 64 | 96 | SQL statements excluding BEGIN/COMMIT |
| association profile | required group permission denied | 2 | 3 | SQL statements excluding BEGIN/COMMIT |
| login finalization | unchanged deletion | 2 | 3 | SQL statements excluding BEGIN/COMMIT |
| login finalization | clear deletion noassociation | 4 | 5 | SQL statements excluding BEGIN/COMMIT |
| login finalization | clear deletion association | 5 | 6 | SQL statements excluding BEGIN/COMMIT |
| login finalization | 32 concurrent finalizations | 67 | 99 | SQL statements excluding BEGIN/COMMIT |
| login preparation | changed username | 5 | 6 | SQL statements excluding BEGIN/COMMIT |
| login preparation | 32 noops | 32 | 64 | SQL statements excluding BEGIN/COMMIT |
| login preparation | 32 concurrent normalization | 36 | 68 | SQL statements excluding BEGIN/COMMIT |
| membership card configuration | existing configuration | 2 | 3 | SQL statements excluding BEGIN/COMMIT |
| membership card configuration | missing association | 1 | 2 | SQL statements excluding BEGIN/COMMIT |
| membership card configuration | new configuration | 5 | 6 | SQL statements excluding BEGIN/COMMIT |
| membership card configuration | admin/athlete bypass | 0 | 0 | SQL statements excluding BEGIN/COMMIT |
| membership card configuration | 32 concurrent creation | 67 | 99 | SQL statements excluding BEGIN/COMMIT |
| two-factor update | Nrequests, one actual transition | 2*N+2 | 3*N+2 | SQL statements excluding BEGIN/COMMIT |
| profile update | changed | [3, 2, 4] | [4, 2, 4] | SELECT/UPDATE/INSERT tuple |
| profile update | noop | [2, 2, 0] | [3, 2, 0] | SELECT/UPDATE/INSERT tuple |
| profile update | 24 concurrent | [49, 48, 4] | [73, 48, 4] | SELECT/UPDATE/INSERT tuple |
| profile update | cancel at person | [1, 0, 0] | [2, 0, 0] | SELECT/UPDATE/INSERT tuple |
| profile update | cancel at association | [3, 1, 2] | [4, 1, 2] | SELECT/UPDATE/INSERT tuple |
| profile tables | changed | [2, 1, 2] | [3, 1, 2] | SELECT/UPDATE/INSERT tuple |
| profile tables | noop | [1, 1, 0] | [2, 1, 0] | SELECT/UPDATE/INSERT tuple |
| profile tables | absent value validation | [0, 0, 0] | [0, 0, 0] | SELECT/UPDATE/INSERT tuple |
| profile tables | 24 concurrent | [25, 24, 2] | [49, 24, 2] | SELECT/UPDATE/INSERT tuple |
| profile tables | cancel at row | [1, 0, 0] | [2, 0, 0] | SELECT/UPDATE/INSERT tuple |
| profile tables | cancel at audit | [2, 1, 1] | [3, 1, 1] | SELECT/UPDATE/INSERT tuple |
| profile password | changed | [1, 1] | [2, 1] | SELECT/UPDATE tuple |
| profile password | wrong password | [1, 0] | [2, 0] | SELECT/UPDATE tuple |
| profile password | 24 concurrent | [24, 1] | [48, 1] | SELECT/UPDATE tuple |
| login response | unsupported role | 0 | 0 | SQL statements excluding BEGIN/COMMIT |
| login response | profile-only/early association failure | 1 | 2 | SQL statements excluding BEGIN/COMMIT |
| login response | collaborator missing association | 2 | 4 | SQL statements excluding BEGIN/COMMIT |
| login response | complete existing card | 7 | 11 | SQL statements excluding BEGIN/COMMIT |
| login response | complete created card | 10 | 14 | SQL statements excluding BEGIN/COMMIT |
| login response | image failure before card | 5 | 8 | SQL statements excluding BEGIN/COMMIT |
| login response | 32 concurrent complete responses | 224 | 352 | SQL statements excluding BEGIN/COMMIT |
| login response | mixed 32 image cases | sum(fixtureQueries with existingcard=7) | same sum + sum(protocolFences 1/2/4 by case) | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | athlete complete session | unchanged fixture Queries | fixture Queries +4 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | association creates default card | unchanged fixture Queries | fixture Queries +7 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | association existing card | unchanged fixture Queries | fixture Queries +7 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | collaborator owner association | unchanged fixture Queries | fixture Queries +7 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | administrator avoids association | unchanged fixture Queries | fixture Queries +4 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | valid OTP | unchanged fixture Queries | fixture Queries +4 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | missing OTP retains normalization | unchanged fixture Queries | fixture Queries +2 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | wrong OTP retains normalization | unchanged fixture Queries | fixture Queries +2 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | wrong password prevents normalization | unchanged fixture Queries | fixture Queries +1 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | disabled account | unchanged fixture Queries | fixture Queries +1 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | deleted account | unchanged fixture Queries | fixture Queries +1 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | unsupported role retains normalization | unchanged fixture Queries | fixture Queries +2 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | missing association retains normalization | unchanged fixture Queries | fixture Queries +3 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | future deletion clears after response | unchanged fixture Queries | fixture Queries +4 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | today deletion clears after response | unchanged fixture Queries | fixture Queries +4 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | past deletion retained | unchanged fixture Queries | fixture Queries +4 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | historical zero role | unchanged fixture Queries | fixture Queries +4 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | final database write fails after card creation | unchanged fixture Queries | fixture Queries +7 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | social Google athlete session | unchanged fixture Queries | fixture Queries +3 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | social Apple creates default card | unchanged fixture Queries | fixture Queries +6 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | social Google collaborator session | unchanged fixture Queries | fixture Queries +6 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | social missing OTP retains normalization | unchanged fixture Queries | fixture Queries +1 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | social wrong OTP retains normalization | unchanged fixture Queries | fixture Queries +1 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | social token denial prevents normalization | unchanged fixture Queries | fixture Queries +0 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | social Apple administrator session | unchanged fixture Queries | fixture Queries +3 | SQL statements excluding BEGIN/COMMIT |
| password/social complete session | social finalization fails after card creation | unchanged fixture Queries | fixture Queries +6 | SQL statements excluding BEGIN/COMMIT |
| profile info | owner | 6 | 8 | all traced SELECTs, including identity reads and acknowledged server fences |
| profile info | collaborator | 6 | 8 | all traced SELECTs, including identity reads and acknowledged server fences |
| profile info | athlete | 2 | 3 | all traced SELECTs, including identity reads and acknowledged server fences |
| profile info | administrator | 2 | 3 | all traced SELECTs, including identity reads and acknowledged server fences |
| profile info | 32 mixed concurrent profiles | 128 | 176 | all traced SELECTs, including identity reads and acknowledged server fences |
| profile info | owner with 1/16/128/512 nested groups | 6 | 8 | all traced SELECTs, including identity reads and acknowledged server fences |

These bounded fixture counts do not establish whole-application latency or memory
improvements. Required evidence is recorded in `server-fence/query-costs.json` and
the full account regression under ignored `quality-reports/go-migration/`.

Social selection itself remains a direct read. Composed social admission calls
`LoginPreparation` after selection and before OTP, so both successful admission
and OTP denial include one shared server fence. Provider or selection rejection
never reaches that transaction; the original Django fixture counts are retained.

| Component | Case | Before | With server fence | Metric |
|---|---|---|---|---|
| social admission | unchanged normalization including OTP outcomes | 2 | 3 | SQL statements excluding BEGIN/COMMIT |
| social admission | changed normalization including OTP outcomes | 5 | 6 | SQL statements excluding BEGIN/COMMIT |
| social admission | trusted actor audit | 6 | 7 | SQL statements excluding BEGIN/COMMIT |
| social admission | selection/provider failure before preparation | 1 | 1 | SQL statements excluding BEGIN/COMMIT |
| social selection only | 1/16/128/512 candidates | 1 | 1 | SQL statements excluding BEGIN/COMMIT |
| social selection only | 32 concurrent selections | 32 | 32 | SQL statements excluding BEGIN/COMMIT |
