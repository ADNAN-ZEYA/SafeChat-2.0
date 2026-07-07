# SafeChat 2.0 — Repository Governance

This document defines the contribution rules for the SafeChat 2.0 repository.
It is enforced automatically by the workflows in `.github/workflows/`
(`governance-guard.yml`, `protect-main.yml`, `pr-approval-check.yml`,
`auto-assign-reviewers.yml`) together with `.github/CODEOWNERS`.

> **Note:** This file is the single source of truth for contribution rules.
> `CLAUDE.md` serves an unrelated purpose (AI agent instructions) and is not a
> governance document. All governance tooling references `GOVERNANCE.md`.

**Repository owner:** [@ADNAN-ZEYA](https://github.com/ADNAN-ZEYA)

---

## 1. Branch Rules

| Actor | Direct push to `main` | Merge PRs | Approve governance changes |
|---|---|---|---|
| **@ADNAN-ZEYA** (owner) | ✅ Allowed | ✅ May self-merge | ✅ |
| All other contributors | ❌ Prohibited | ❌ Requires CODEOWNER approval | ❌ |

- Only **@ADNAN-ZEYA** may push directly to `main`.
- Everyone else must work from **feature branches** and open **Pull Requests**.
- Direct commits to `main` by anyone other than @ADNAN-ZEYA are automatically
  reverted by `protect-main.yml`, and an Issue is opened explaining how to
  recover the work (see §7).

### Branch naming (recommended)

```
feature/<short-description>
fix/<short-description>
docs/<short-description>
```

---

## 2. Issue Referencing

Every Pull Request **must reference at least one Issue** using the
non-closing form:

```
Refs #123
```

The following closing keywords are **prohibited** in PR bodies and commit
messages:

```
Closes #123
Fixes #123
Resolves #123
```

**Why:** GitHub's closing keywords automatically close the referenced Issue
the moment the PR merges — *before* the reviewer has verified the fix on
`main`. Under this repository's governance, Issue closure is a deliberate
reviewer action (see §4), so auto-closing keywords violate policy.
`governance-guard.yml` fails any PR that uses them, and fails any PR that
contains no `Refs #<number>` reference at all.

---

## 3. Architecture Changes

If a code change affects the system architecture — for example anything under:

```
src/server/
src/api/
backend/
database/
infrastructure/
```

— the contributor should update the corresponding documentation under
`docs/architecture/`, **if that directory exists** in the repository.

`governance-guard.yml` reports a blocking violation when architecture paths
change without a matching `docs/architecture/` update (the check is skipped
automatically while `docs/architecture/` does not exist).

---

## 4. Issue Closing Policy

- Issues are closed **only after the referencing PR has been merged**, and
  only by the **CODEOWNER reviewer**.
- The **PR author must never close the Issue** their PR references.
- `governance-guard.yml` watches Issue-close events. If an Issue is closed
  without a merged PR referencing it (or by the PR author), the workflow:
  1. reopens the Issue,
  2. applies the `governance-violation` label,
  3. comments with an explanation citing this document.
- As repository owner, @ADNAN-ZEYA is exempt from automatic reopening (the
  owner is the CODEOWNER reviewer of record).

---

## 5. Pull Request Approval

- `.github/CODEOWNERS` determines who must approve a PR. Approval state is
  published as the `approval-check` commit status by
  `pr-approval-check.yml`:
  - **success** — an owning CODEOWNER (who is not the PR author) approved.
  - **pending** — waiting for CODEOWNER approval.
  - **failure** — a CODEOWNER has requested changes.
- **Self-merge exception:** when @ADNAN-ZEYA is the *only* CODEOWNER covering
  every changed file, @ADNAN-ZEYA may self-merge — there is no other eligible
  reviewer.
- Reviewers are requested automatically by `auto-assign-reviewers.yml` based
  on CODEOWNERS; the PR author is never requested as a reviewer.

### Protected governance files

Changes to the following paths require @ADNAN-ZEYA's approval (enforced via
CODEOWNERS):

```
.github/workflows/
.github/CODEOWNERS
GOVERNANCE.md
```

---

## 6. Onboarding a New Developer

Onboarding requires **only** a CODEOWNERS edit — no workflow changes.

1. **Add them as a GitHub collaborator**
   Repository → Settings → Collaborators → *Add people*.
2. **Add them to `.github/CODEOWNERS`**
   Uncomment / copy one of the placeholder lines at the bottom of the file
   and map their username to the folder(s) they own.
3. **Assign ownership for appropriate folders**
   Example — giving a new frontend developer ownership of `frontend/`:

   ```
   /frontend/ @ADNAN-ZEYA @new-frontend-dev
   ```

   From then on, `auto-assign-reviewers.yml` requests their review on
   matching PRs and `pr-approval-check.yml` accepts their approval for those
   paths automatically.

---

## 7. Recovering From a Reverted Direct Push

If you pushed to `main` directly and `protect-main.yml` reverted your
commits, your work is **not lost** — the original commits still exist in the
repository history. Recover them onto a feature branch:

```bash
# 1. Create a feature branch from the current main
git fetch origin
git switch -c feature/my-change origin/main

# 2. Cherry-pick your original commit(s) — SHAs are listed in the
#    auto-created Issue
git cherry-pick <sha-of-your-commit>

# 3. Push the branch and open a Pull Request
git push -u origin feature/my-change
```

Then open a PR referencing the relevant Issue (`Refs #<number>`).

---

## 8. Enforcement Summary

| Rule | Enforced by |
|---|---|
| No direct pushes to `main` (except owner) | `protect-main.yml` + branch ruleset |
| `Refs #` required, closing keywords banned | `governance-guard.yml` |
| Architecture docs updated with architecture code | `governance-guard.yml` |
| Issues closed only via merged PR + CODEOWNER | `governance-guard.yml` |
| CODEOWNER approval before merge | `pr-approval-check.yml` + CODEOWNERS |
| Reviewer auto-assignment | `auto-assign-reviewers.yml` |
| Lint/tests on every PR | `ai-review.yml` |
