-- Transactional realtime outbox. Go appends an event in the same transaction as
-- the business write, so rolled-back writes publish nothing and committed ones
-- are never lost. The runtime owning WebSocket delivery claims and forwards
-- events through its own transport; the envelope, not a Channels encoding, is
-- the cross-runtime protocol. Delivery is at least once; events only invalidate.
CREATE TABLE public.assozeta_go_realtime_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    envelope_version smallint NOT NULL CHECK (envelope_version = 1),
    kind text NOT NULL CHECK (kind ~ '^[a-z][a-z_]{0,63}$'),
    target_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    delivered_at timestamptz,
    failed_code text,
    CHECK (delivered_at IS NULL OR failed_code IS NULL)
);
CREATE INDEX assozeta_go_realtime_events_pending ON public.assozeta_go_realtime_events (id)
    WHERE delivered_at IS NULL AND failed_code IS NULL;
