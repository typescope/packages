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
      <package-name>.jsonl   ← release history (append-only, CI-managed)
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
authorizes publishers for the package. This file is human-reviewed and changes
rarely.

```toml
name       = "greeter-pkg"
namespace  = "greeter.pkg"
repo       = "github.com/alice/greeter"
registered = "2026-03-25"
publishers = ["alice", "bob"]

[owner]
name  = "Alice Smith"
email = "alice@example.com"
url   = "https://alice.dev"
```

Fields:

| Field        | Description |
|---|---|
| `name`       | Package name (must match filename) |
| `namespace`  | Dot-separated namespace (top-level component determines shard) |
| `repo`       | Source repository |
| `registered` | Registration date |
| `publishers` | GitHub IDs authorized to publish new versions |
| `[owner]`    | Primary contact for the package |

To register a new package, open a PR adding the `.toml` file. Human review is
required for registration.

## Release record format

Each line in `releases/<shard>/<namespace>/<package-name>.jsonl` is one
published version. The file is append-only.

Required fields:

| Field     | Type   | Description |
|---|---|---|
| `version` | string | `MAJOR.MINOR.PATCH` |
| `url`     | string | Download URL of the `.joy` artifact |
| `sha512`  | string | Hex SHA-512 of the artifact |

Optional fields:

| Field            | Type    | Description |
|---|---|---|
| `deps`           | object  | Direct dependencies: map of package name to version constraint. Used by the resolver to avoid downloading artifacts during graph traversal. |
| `source_url`     | string  | Download URL of the source archive |
| `source_sha512`  | string  | Hex SHA-512 of the source archive |
| `yanked`         | boolean | If true, this version is withdrawn and must not be selected for new resolutions |
| `published`      | string  | Publication timestamp |

Example:

```jsonl
{"version":"1.0.0","url":"https://github.com/alice/greeter/releases/download/greeter-pkg-v1.0.0/greeter-pkg-v1.0.0.joy","sha512":"6f0d...","deps":{},"source_url":"...","source_sha512":"91bc..."}
{"version":"1.1.0","url":"https://github.com/alice/greeter/releases/download/greeter-pkg-v1.1.0/greeter-pkg-v1.1.0.joy","sha512":"7a21...","deps":{"math":"^1.0"},"source_url":"...","source_sha512":"af44..."}
```

### Immutability rules

- `version` must never be reused with different content
- `sha512` must never change for the same version
- To withdraw a release, set `yanked: true` — never delete or rewrite a line

## Publishing a new version

Run `jo publish` from the package directory. It will:

1. Build the `.joy` artifact (`jo package`)
2. Upload the artifact and source archive to GitHub Releases
3. Construct the full release line
4. Open a PR appending that line to `releases/<shard>/<namespace>/<package-name>.jsonl`

In practice the PR is opened by `jo publish` or an agent, not by the publisher
directly.

## CI validation

When a release PR is opened, CI automatically:

1. Reads `publishers` from the registration metadata
2. Verifies the PR author's GitHub ID is in the list
3. Verifies the PR appends exactly one line to exactly one release file
4. Verifies the version is not already present
5. Downloads the `.joy` artifact and re-derives the release line (sha512, deps from `meta.toml`)
6. Verifies the derived line matches the line in the PR
7. Merges the PR

If any check fails the PR is rejected. No human review is needed on the happy path.

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
