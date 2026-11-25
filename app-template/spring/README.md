# __APP__

An app on the AVK platform, created with `python -m avk new-app __APP__`. Spring Boot 4 on Java 21.

| | |
|---|---|
| Test copy (stg) | http://127.0.0.1:__STG_PORT__/ |
| Production | https://__HOST__ |
| Pipeline | pull request gates (`ci`), then on merge to `main` a signed release that waits for the owner's approval of `prod` |

## Work on it
```
.\check.cmd                 # build, all tests, coverage gate (the same gate the pipeline runs)
python -m avk stg up __APP__ --build    # from the platform repo: run it on stg
```
Every change goes through a pull request: the pipeline scans for secrets, vulnerable dependencies and licences, builds and scans the image,
and runs CodeQL. Merging starts a release; nothing reaches prod without the owner's approval and a valid signature.

## What is in here
- `src/main/java/.../observability`: request ids, an access log line without personal data, metrics on a private port.
- `avk.yaml`: the contract with the platform (port, health paths, smoke checks, environment names). `.github/workflows`: the pipeline callers.
- `e2e/`: browser and API checks that run against the built image in the pipeline.
[[if web]]
- `legal` block in `avk.yaml` and the pages under `src/main/resources/static/legal` (DRAFT, not legal advice; run `python -m avk legal render __APP__`).
- Contact form with tickets; `redirects.csv`; `SunsetHeaders` for retiring API versions.
[[end]]
[[if api]]
- Bearer-token security (`auth/SecurityConfig`): callers send an access token from the shared login; `/api/v1/me` shows who they are.
[[end]]
