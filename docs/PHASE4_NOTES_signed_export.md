# Signed live exports and provider provenance

Closes two items left open under R3 of the 8160559 recheck.

## Keyring verification for live bundles

`verify_bundle(bundle, keyring)` and `python -m voice_harness verify-bundle --public-key` already applied to any bundle; the gap was that no test exercised a live bundle. `tests/test_signed_live_export.py` builds a signed live run (live binding, caller audio, native events, assurance) and checks: offline verification with the public keyring (dict and JSON), CLI exit codes, rejection of a tampered provider section (including with `manifest_hash` recomputed, which only the signature catches), a tampered event, a wrong keyring and a revoked key id, and refusal when no trusted key is supplied.

## Endpoint, region and SDK versions

- Adapters expose `connection_info()`; `live_providers.connection_binding()` sanitises it (host must match `[A-Za-z0-9.-]`, region `[a-z0-9-]`; versions truncated) and `session_metadata()` adds `endpoint_host`, `region`, `sdk_versions` to the `provider` object of `run.live_binding`.
- OpenAI: host parsed from the configured URL (userinfo, port, path and query dropped); version of the `websockets` package.
- Nova: region of the transport; host derived as `bedrock-runtime.<region>.amazonaws.com` (derived, not observed); version of `@aws-sdk/client-bedrock-runtime` read from the bridge's `node_modules`.
- The manifest `provider` section (`endpoint`, `region`, `sdk_versions`, `connection_recorded`) is derived from the ledger event only, so it is covered by the signature. Runs bound before this change show nulls and `connection_recorded: false`.
- `sdk_version` (singular) stays null for live runs; `sdk_versions` is a map because Nova involves a Node package and OpenAI a Python one.
- The `provider` field is a free object in the event contract, so the schema is unchanged; the example was updated.

## Still open

- Node runtime version and the provider's server-side version are not recorded.
- Endpoint is reported by the adapter, not observed from the connection (for example a proxy or redirect).
