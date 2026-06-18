# Publish checklist — one-shot public release

This directory is the complete, push-ready repository for **The Recovery Desk**.
It runs offline on a clean clone (`make demo` / `make test`, no API key). Publishing
is a single mechanical step — nothing else needs to change.

## What is already verified

- `pip install -e .` installs all deps (pyyaml, fastapi, uvicorn, mcp[cli], anthropic) on a fresh venv.
- `make test` passes (grader, blackboard, end-to-end loop, fail-hint-driven reviser, second-domain self-fail).
- `make run` closes the loop headless on all three rubrics.
- `make demo` serves the live split-screen ledger UI; `make demo-replay` writes the standalone playable demos in `demo/`.
- `bookability-mcp` (the MCP server) imports and every tool is callable.
- No secrets, db files, or caches are tracked (see `.gitignore`).

## Publish (run from this directory)

```bash
git init
git add -A
git commit -m "The Recovery Desk v1.0 — autonomous self-grading recovery agent + bookability-mcp"
gh repo create cgallic/recovery-desk --public --source=. --remote=origin --push
git tag v1.0
git push origin v1.0
```

## Post-publish (paste two URLs)

1. Confirm a clean clone runs:
   ```bash
   git clone https://github.com/cgallic/recovery-desk /tmp/rd && cd /tmp/rd \
     && python -m venv .venv && .venv/bin/pip install -e . \
     && make test && make run
   ```
2. Record the 80s demo per `../demo/video-script.md` against `make demo` (offline so the 66→100 beat is identical), upload to YouTube, set public.
3. Paste the repo URL and the video URL into `submission/form-answers.md` and `config.json`.
