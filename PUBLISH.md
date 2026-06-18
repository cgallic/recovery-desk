# Publish checklist — one-shot public release

> **Status: PUBLISHED.** Live at https://github.com/cgallic/recovery-desk (public,
> tagged `v1.0`). A clean clone has been re-verified: `pip install -e .` installs
> all deps, the full test suite passes (incl. the live-path test), the headless
> loop self-fails then ships at 100/100, and `demo/recovery-desk-demo.mp4` is
> present in the clone. The commands below are kept as the reproducible record.

This directory is the complete repository for **The Recovery Desk**.
It runs offline on a clean clone (`make demo` / `make test`, no API key).

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

## Post-publish (one URL left to paste)

1. Clean clone confirmed (done):
   ```bash
   git clone https://github.com/cgallic/recovery-desk /tmp/rd && cd /tmp/rd \
     && python -m venv .venv && .venv/bin/pip install -e . \
     && make test && make run
   ```
2. The demo VIDEO is already rendered and committed at `demo/recovery-desk-demo.mp4`
   (re-render any time with `make video`). Upload that exact file to YouTube and set
   it public — that is the only remaining manual step, because the lablab form's
   video field wants a hosted watch URL.
3. Paste the YouTube watch URL into `submission/form-answers.md` and `config.json`
   (`demo.video_url`). The repo URL is already live.
