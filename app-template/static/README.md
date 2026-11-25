# __APP__

A static site on the AVK platform, created with `python -m avk new-app __APP__ --type static`. The files are served as they are by an
unprivileged nginx in a read-only container.

| | |
|---|---|
| Test copy (stg) | http://127.0.0.1:__STG_PORT__/ |
| Production | https://__HOST__ |
| Pipeline | pull request gates (`ci`), then on merge to `main` a signed release that waits for the owner's approval of `prod` |

## Work on it
Edit the files, open a pull request. The pipeline checks that every page parses and every local link and asset exists, scans for secrets,
vulnerable dependencies and licences, builds and scans the image, runs CodeQL on the JavaScript, and runs the browser checks in `e2e/`
(page loads, security headers, accessibility). Merging starts a release; nothing reaches prod without the owner's approval and a valid signature.

```
python -m avk stg up __APP__ --build     # from the platform repo: build the image and run it on stg
```

## What is in here
- `nginx.conf`: security headers and a Content-Security-Policy that allows only this site's own files, `/healthz` and `/readyz`, JSON access logs with a request id.
- `Dockerfile`, `.dockerignore`: what goes into the image (only the site and `nginx.conf`).
- `avk.yaml`: the contract with the platform. `.github/workflows`: the pipeline callers (stack `static`). `e2e/`: browser checks.
