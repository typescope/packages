# Integration Testing

This document describes the integration test scenarios for the registry CI
validation workflows. Each scenario can be re-run by opening a PR with the
described content.

## Release sync (`sync.py`)

The primary path for publishing releases. Run periodically from an up-to-date
clone of this repository:

```
python3 sync.py [--dry-run]
```

The script scans every registration file under `registry/`, queries the
configured publication source (GitHub Releases), downloads new `.joy` artifacts,
verifies sha512, and appends canonical entries to `releases/.../pkg.jsonl`.
Updated release files are then committed and pushed to main.

## Registration validation (`validate-registration.yml`)

These tests exercise `.github/scripts/validate_registration.py`.

### Happy path

| # | Description | Expected result |
|---|---|---|
| R1 | Register a new package under an unclaimed namespace | CI passes |
| R2 | Register a second package under an already-claimed namespace, correct owner | CI passes |

### Namespace ownership

| # | Description | Expected result |
|---|---|---|
| R3 | Register under a claimed namespace with wrong `owner.name` | CI fails: `owner.name must match the existing owner of namespace '...'` |
| R4 | Register under a claimed namespace with wrong `owner.email` | CI fails: `owner.email must match the existing owner of namespace '...'` |

### Authorization

| # | Description | Expected result |
|---|---|---|
| R5 | New registration with `publish.github` pointing to a non-existent repo | CI fails: `GitHub repository '...' does not exist or is not accessible` |
| R6 | PR author is not the owner of `publish.github` repo (personal repo) | CI fails: `'<author>' is not authorized to modify this registration` |
| R7 | PR author is not a public member of the owning org (org repo) | CI fails: `'<author>' is not authorized to modify this registration` |

### Structural checks

| # | Description | Expected result |
|---|---|---|
| R8 | Registration file where `name` does not match filename | CI fails: `name '...' does not match filename '...'` |
| R9 | Registration file missing a required field (e.g. `repo` or `runtime`) | CI fails: `missing required field '...'` |
| R9b | Registration file with invalid `runtime` value (e.g. `js`) | CI fails: `invalid runtime '...'; must be one of ('pure', 'python', 'ruby')` |
| R10 | PR touches files outside `registry/` | CI fails: `registration PR must only touch files under registry/` |
| R11 | PR changes `name` of an existing registration | CI fails: `'name' is immutable and cannot be changed` |
| R12 | PR changes `namespace` of an existing registration | CI fails: `'namespace' is immutable and cannot be changed` |
| R13 | PR changes `registered` of an existing registration | CI fails: `'registered' is immutable and cannot be changed` |
| R14 | Registration missing `[publish]` section | CI fails: `missing required field 'publish'` |
| R15 | Registration has `[publish]` but no `github` key | CI fails: `[publish] must have a 'github' key` |
| R16 | Registration has unsupported publish source (e.g. `gitlab`) | CI fails: `unsupported publish source(s)` |

## Manual release update (`validate-release.yml`)

A fallback path for publishing a release manually, without waiting for the
sync script. Open a PR that appends exactly one line to a release JSONL file.
CI validates the line and auto-merges on success.

These tests exercise `.github/scripts/validate_release.py`.

### Happy path

| # | Description | Expected result |
|---|---|---|
| P1 | Append a valid release line for a registered package | CI passes and auto-merges |

### Authorization

| # | Description | Expected result |
|---|---|---|
| P2 | PR author is not the owner of `publish.github` repo (personal repo) | CI fails: `'<author>' is not authorized to publish` |
| P3 | No registration metadata found for the package | CI fails: `no registration metadata found` |

### Release line integrity

| # | Description | Expected result |
|---|---|---|
| P4 | Release line has wrong `sha512` | CI fails: `sha512 mismatch` |
| P5 | Release line has wrong `deps` | CI fails: `deps mismatch` |
| P6 | Version already present in the release file | CI fails: `version '...' is already present` |
| P7 | PR adds more than one line | CI fails: `got N removed and M added` |
| P8 | PR touches more than one release file | CI fails: `release PR must touch exactly one release file` |
| P9 | Release line is not valid JSON | CI fails: `new release line is not valid JSON` |
| P10 | Release line missing a required field (`version`, `url`, or `sha512`) | CI fails: `release line missing required field` |
| P11 | `version` field has invalid format (e.g. `1.2.0-rc-1`, `1.2`, `abc`) | CI fails: `invalid version format '...'` |

### Yank

| # | Description | Expected result |
|---|---|---|
| P12 | Valid yank: modify existing line to add `yanked: true` | CI passes |
| P13 | Yank PR that also changes another field (e.g. `url`) | CI fails: `yank PR must only add 'yanked: true'` |
| P14 | Yank PR that removes a field instead of adding `yanked: true` | CI fails: `yank PR must only add 'yanked: true'` |

## Test status

| Test | Status | PR / Notes |
|---|---|---|
| R1 | PASS (CI accepted) | #1 — `jo-runtime-ruby` registered |
| R2 | PASS (CI accepted) | main — `jo-runtime-ruby` follows `jo-library` under `jo` namespace |
| R3 | PASS (CI rejected) | #3 — wrong `owner.name` |
| R4 | PASS (CI rejected) | #4 — wrong `owner.email` |
| R5 | — | Not yet tested |
| R6 | — | Requires a second GitHub account; not yet tested |
| R7 | — | Requires an org repo setup; not yet tested |
| R8 | PASS (CI rejected) | #5 — name `wrong-name` did not match filename `test-pkg` |
| R9 | PASS (CI rejected) | #6 — missing required field `repo` |
| R10 | PASS (CI rejected) | #7 — PR touched `stray-file.txt` outside `registry/` |
| P1 | — | Requires a real artifact; not yet tested |
| P2 | — | Requires a second GitHub account; not yet tested |
| P3 | PASS (CI rejected) | #9 — no `registry/fa/fake/fake-pkg.toml` found |
| P4 | — | Requires a real artifact; not yet tested |
| P5 | — | Requires a real artifact; not yet tested |
| P6 | — | Requires an existing release to duplicate; not yet tested |
| P7 | PASS (CI rejected) | #10 — two lines added instead of one |
| P8 | PASS (CI rejected) | #11 — two release files touched |
| P9 | PASS (CI rejected) | #12 — `this is not json` is not valid JSON |
| P10 | PASS (CI rejected) | #13 — missing required field `sha512` |
