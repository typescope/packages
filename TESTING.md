# Integration Testing

This document describes the integration test scenarios for the registry CI
validation workflows. Each scenario can be re-run by opening a PR with the
described content.

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
| R5 | Register under a claimed namespace, PR author not in publishers | CI fails: `'<author>' is not in its publishers list` |

### Structural checks

| # | Description | Expected result |
|---|---|---|
| R6 | Registration file where `name` does not match filename | CI fails: `name '...' does not match filename '...'` |
| R7 | Registration file missing a required field (e.g. `repo`) | CI fails: `missing required field '...'` |
| R8 | PR touches files outside `registry/` | CI fails: `registration PR must only touch files under registry/` |
| R9 | `publishers` is an empty list | CI fails: `publishers must be a non-empty list of GitHub IDs` |

## Release validation (`validate-release.yml`)

These tests exercise `.github/scripts/validate_release.py`.

### Happy path

| # | Description | Expected result |
|---|---|---|
| P1 | Append a valid release line for a registered package | CI passes and auto-merges |

### Authorization

| # | Description | Expected result |
|---|---|---|
| P2 | PR author not in `publishers` for the package | CI fails: `'<author>' is not authorized to publish` |
| P3 | No registration metadata found for the package | CI fails: `no registration metadata found` |

### Release line integrity

| # | Description | Expected result |
|---|---|---|
| P4 | Release line has wrong `sha512` | CI fails: `sha512 mismatch` |
| P5 | Release line has wrong `deps` | CI fails: `deps mismatch` |
| P6 | Version already present in the release file | CI fails: `version '...' is already present` |
| P7 | PR adds more than one line | CI fails: `PR must add exactly one line` |
| P8 | PR touches more than one release file | CI fails: `release PR must touch exactly one release file` |
| P9 | Release line is not valid JSON | CI fails: `new release line is not valid JSON` |
| P10 | Release line missing a required field (`version`, `url`, or `sha512`) | CI fails: `release line missing required field` |

## Test status

| Test | Status | PR / Notes |
|---|---|---|
| R3 | PASS (CI rejected) | #3 — wrong `owner.name` |
| R4 | PASS (CI rejected) | #4 — wrong `owner.email` |

Add a row here each time a scenario is verified.
