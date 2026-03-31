# Jo Package Registry

This repository is the canonical source of truth for published Jo packages.

## Structure

```
registry/
  <shard>/
    <namespace>/
      <package-name>.toml    ← registration metadata (human-reviewed)
releases/
  <shard>/
    <namespace>/
      <package-name>.jsonl   ← release history (append-only, daemon-managed)
```

`shard` is the first two letters of the top-level namespace.

Example:

```
registry/
  jo/
    jo/
      jo-http.toml
  pa/
    parsing/
      parsing-lexer.toml
releases/
  jo/
    jo/
      jo-http.jsonl
  pa/
    parsing/
      parsing-lexer.jsonl
```

## Registration metadata

Each `registry/<shard>/<namespace>/<package-name>.toml` records ownership and
the publication source for the package. This file is human-reviewed and changes
rarely.

```toml
name        = "greeter-pkg"
namespace   = "greeter.pkg"
description = "A friendly greeter library"
url         = "https://greeter.example.com"
repo        = "https://github.com/alice/greeter"
registered  = "2026-03-25"
runtime     = "pure"

[owner]
name  = "Alice Smith"
email = "alice@example.com"

[publish]
github = "alice/greeter"
```

Fields:

| Field         | Mutable? | Description |
|---|---|---|
| `name`        | No  | Package name (must match filename) |
| `namespace`   | No  | Dot-separated namespace (top-level component determines shard) |
| `registered`  | No  | Registration date — set once at registration |
| `runtime`     | No  | Target runtime: `pure` (no FFI), `ruby`, or `python` |
| `description` | Yes | Short human-readable description of the package |
| `url`         | Yes | Package homepage or documentation site (must be `https://`) |
| `repo`        | Yes | Source repository URL (must be `https://`; may differ from `publish.github`) |
| `[owner]`     | Yes | Primary contact for the package |
| `[publish]`   | Yes | Publication source — currently only `github = "owner/repo"` is supported |

To register a new package, open a PR adding the `.toml` file. Human review is
required for registration.

## Authorization for registry changes

CI automatically checks that the PR author is authorized to make the change.
For GitHub-hosted packages (`publish.github = "owner/repo"`), the PR author
must be `owner` (personal repo) or a public member of `owner` (org repo),
verified against the GitHub API with no extra credentials required.

If the GitHub API is unreachable (rate limit, server error), CI prints a note
and defers to human review rather than blocking the PR.

Namespace ownership is always enforced: all registrations under a namespace
must share the same `owner.name` and `owner.email`.

## Publishing a new version

Upload the `.joy` artifact and its `.sha512` file to a GitHub Release:

```
greeter-pkg-v1.2.0.joy
greeter-pkg-v1.2.0.joy.sha512
greeter-pkg-v1.2.0-sources.zip          (optional)
greeter-pkg-v1.2.0-sources.zip.sha512   (required if sources archive is present)
```

Pre-release versions follow the same convention with a modifier suffix:

```
greeter-pkg-v1.2.0-rc1.joy
greeter-pkg-v1.2.0-rc1.joy.sha512
```

The modifier must be alphanumeric with no dashes (e.g. `rc1`, `beta2`, `alpha1`).

The registry daemon (`sync.py`) periodically scans registered packages,
discovers new artifacts by name, verifies each artifact against its `.sha512`
file, and appends a canonical entry to the release log. No PR or manual action
is needed to publish.

The release tag is ignored — versions are derived from artifact names. A single
GitHub repository can host multiple packages at independent versions.

## Release record format

Each line in `releases/<shard>/<namespace>/<package-name>.jsonl` is one
published version. The file is append-only and written only by the daemon.

Required fields:

| Field     | Type   | Description |
|---|---|---|
| `version` | string | `MAJOR.MINOR.PATCH` or `MAJOR.MINOR.PATCH-modifier` (e.g. `1.2.0-rc1`) |
| `url`     | string | Download URL of the `.joy` artifact |
| `sha512`  | string | Hex SHA-512 of the artifact |
| `runtime` | string | Target runtime, copied from registry: `pure`, `ruby`, or `python` |

Optional fields:

| Field           | Type    | Description |
|---|---|---|
| `deps`          | object  | Direct dependencies: map of package name to version constraint |
| `source_url`    | string  | Download URL of the source archive |
| `source_sha512` | string  | Hex SHA-512 of the source archive |
| `yanked`        | boolean | If true, this version is withdrawn and must not be selected for new resolutions |

Example:

```jsonl
{"version":"1.0.0","url":"https://github.com/alice/greeter/releases/download/v1.0.0/greeter-pkg-v1.0.0.joy","sha512":"6f0d...","deps":{},"source_url":"...","source_sha512":"91bc..."}
{"version":"1.1.0","url":"https://github.com/alice/greeter/releases/download/v1.1.0/greeter-pkg-v1.1.0.joy","sha512":"7a21...","deps":{"math":"^1.0"},"source_url":"...","source_sha512":"af44..."}
```

### Immutability rules

- `version` must never be reused with different content
- `sha512` must never change for the same version
- To withdraw a release, set `yanked: true` — never delete or rewrite a line

## Client resolution

The release index is served flat at:

```
https://pkg.jo-lang.org/<package-name>.jsonl
```

Clients resolve by exact package name. The repository layout is namespace-oriented
for human maintenance; the served layout is flat for efficient resolution.

The resolver uses the `deps` field to traverse the dependency graph with JSONL
fetches only, then downloads artifacts for selected versions. If `deps` is absent,
it falls back to reading `meta.toml` from the downloaded artifact.

## Deployment

The flat registry is built and served via Cloudflare Pages.

**Build command**: `python3 build.py`
**Output directory**: `dist`

`build.py` flattens `releases/<shard>/<namespace>/<package>.jsonl` into
`dist/<package>.jsonl`. It fails if two packages under different namespaces
share the same name.

To build locally:

```sh
python3 build.py
```

The `_headers` file configures Cloudflare to serve `.jsonl` files with the
correct MIME type (`application/x-ndjson`) and a short cache TTL (60s).
