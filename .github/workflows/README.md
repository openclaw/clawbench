# GitHub Actions

## `ci.yml` — run tests on every push / PR

Runs the repository test suite automatically on:

- every `push` to any branch
- every `pull_request`
- manual dispatch from the Actions tab

It uses Python 3.11 and 3.12, installs the package with
`pip install -e .[dev]`, runs full Ruff lint plus `python -m pytest -q`,
then builds a wheel and checks that runtime data such as `tasks-public/`,
`tasks-domain/`, `profiles/`, and `baselines/` are included. Runs under the
`openclaw` organization use the Blacksmith Ubuntu runner; forks fall back to
GitHub-hosted `ubuntu-latest`.

## `ci-check-testbox.yml` — Blacksmith Testbox warmup

This workflow exists for the Blacksmith CLI:

```bash
blacksmith testbox warmup ci-check-testbox.yml --ref main --idle-timeout 90
blacksmith testbox run --id <tbx_id> "python -m pytest -q"
```

It installs ClawBench, hydrates provider/HF secrets into
`~/.clawbench-testbox-live.profile`, restores optional Codex/Claude/Gemini
dotfiles from repo or org secrets, and installs
`~/.local/bin/clawbench-testbox-env` for commands that need that live auth.

## `sync-to-hf-space.yml` — auto-mirror main to the HF Space

Mirrors every push to `main` into the HF Space git remote so
[huggingface.co/spaces/openclaw/clawbench](https://huggingface.co/spaces/openclaw/clawbench)
always tracks GitHub `main`. GitHub becomes the single source of truth;
the HF Space is a pure deploy target.

## One-time setup (required before the workflow can succeed)

The workflow needs one repository secret. It can also use an optional
fallback username secret.

### 1. Get a Hugging Face access token

1. Go to <https://huggingface.co/settings/tokens>
2. Click **"New token"**
3. Name it something like `clawbench-github-actions`
4. Token type: **"Write"** (read-only will NOT work — the workflow
   needs to push commits to the Space git repo)
5. Click **"Generate a token"** and copy it (you'll only see it once)

### 2. Add the secrets to this repo

1. Go to <https://github.com/openclaw/clawbench/settings/secrets/actions>
2. Click **"New repository secret"** and add:

   | Name          | Value                                                      |
   |---------------|------------------------------------------------------------|
   | `HF_TOKEN`    | The write-scoped HF token you created in step 1            |
   | `HF_USERNAME` | Optional fallback if token introspection fails             |

3. Save both.

### 3. Verify

Either push any commit to `main`, or trigger the workflow manually:

1. Go to the **Actions** tab → **"Sync main to HF Space"**
2. Click **"Run workflow"** → `main` branch → **"Run workflow"**
3. Watch it run. Green check = mirror is live.

After the first successful run, every push to `main` automatically
mirrors to the Space with no further action. You can watch the sync
status under the Actions tab for any commit.

## How the workflow behaves

- **Trigger:** push to `main`, or manual dispatch from the Actions tab.
- **Concurrency:** serialized via `group: sync-to-hf-space` so two
  pushes cannot race into a non-fast-forward rejection.
- **Force:** the push uses `git push --force`. This is intentional —
  anything committed directly on the Space side (e.g. via the HF web
  UI file editor) gets overwritten on the next sync. If you want to
  make a change to the Space, make it on GitHub main and let the
  workflow mirror it.
- **Failure modes:**
  - **Missing secrets** → the `Verify required secrets` step fails with
    a clear error message telling you to add `HF_TOKEN`.
  - **Revoked token** → push fails with a 401; check that `HF_TOKEN`
    still has Write scope on <https://huggingface.co/settings/tokens>.
  - **Missing Space** → the workflow creates the Docker Space before
    pushing, using `HF_SPACE_ID` or the default `openclaw/clawbench`.

## Optional: change the target Space

If you ever mirror to a different Space (e.g. a staging copy), set a
repository variable (not a secret) named `HF_SPACE_ID` to the new
Space ID, for example `yourname/clawbench-staging`. The workflow
defaults to `openclaw/clawbench` when the variable is unset.

## Why `--force`?

The contract is: **GitHub is the source of truth for the HF Space's
git history.** The workflow's single job is to make the Space match
GitHub, no matter what. If you want to edit the Space directly (via
the HF file editor), don't — make the change on GitHub and let it
mirror. This avoids the dual-maintainer problem where the two remotes
drift apart over time, which is exactly the situation this workflow
was written to fix.
