-- Baculus control plane — raw artifact identity vs logical market-data identity.
--
-- A raw SHA-256 change alone must NOT mean a vendor restatement. Vendor
-- responses carry volatile non-market metadata (request ids, generated
-- timestamps, trace ids, pagination/transport metadata) that can change between
-- requests while the underlying market observations are identical.
--
-- Two identities are therefore tracked per raw artifact:
--   * sha256               = SHA-256 of the exact vendor bytes (raw provenance,
--                            content-addressed storage). Already present.
--   * logical_data_sha256  = deterministic hash of the canonical market
--                            observations, EXCLUDING volatile request/transport
--                            metadata. Drives economic vintaging/restatement.
--
-- Restatement + a new economic `vintage` occur only when logical_data_sha256
-- changes for a logical_key. A raw-bytes-only change with identical logical data
-- is retained as a new raw observation/artifact (provenance) but is NOT a
-- restatement and does NOT create a new economic vintage.

set search_path = baculus, public;

alter table raw_artifacts
    add column if not exists logical_data_sha256 text;

-- Multiple raw artifacts may now share one economic vintage (same logical data,
-- different raw bytes), so (logical_key, vintage) is no longer unique. Raw
-- identity remains unique via (source_id, sha256).
alter table raw_artifacts
    drop constraint if exists raw_artifacts_logical_key_vintage_key;

create index if not exists raw_artifacts_logical_data_idx
    on raw_artifacts (logical_key, logical_data_sha256);
