---
name: cve
description: >-
  Investigate and resolve individual HUM CVE tracker tickets. Covers
  showing ticket details, determining if Hummingbird is affected,
  backporting patches, setting Fixed in Build, and closing misfiled
  tickets. Use when the user says /cve, "let's look at HUM-XXXX"
  (a CVE tracker), or asks to investigate/fix a specific CVE.
  "/cve HUM-XXXX only" skips same-CVE sibling discovery.
---

# CVE Tracker Investigation

Hands-on per-ticket work. For bulk `cve-needs-attention` triage use
`/analyse-cve-needs-attention`. For an overview use `/cve-status`.

Do not run `cve_analysis.py --resolve` from this skill. Consume the
bot's `{noformat}` comment; write through `cve_helper.py`.

## Helper

```bash
python .agents/skills/cve/cve_helper.py deps
export HUMMINGBIRD_TOOLS_ROOT=/path/to/hummingbird/tools   # if not pip-installed
```

Auth is the existing rhjira file (`JIRA_TOKEN`, `JIRA_SERVER` → URL,
`JIRA_EMAIL` → Basic user). No second Jira token.

| Command | Use |
| --- | --- |
| `investigate HUM-XXXX [HUM-YYYY ...]` | Show + recap + bot-mrs + spec-deps + GitLab MR search. Takes one call with all tickets, not one call per ticket — it dedups per-package probes and runs a single combined GitLab MR search |
| `investigate HUM-XXXX [HUM-YYYY ...] --only` | Named tickets only; skip related-ticket discovery |
| `comment` / `next-release` / `set-fib` / `close-nab` | Jira writes. `--kind nab/fib/analysis/next-release` fills wiki markup from flags or `--from-json`; `--print-only` / `--json` to review |
| `create-task --blocks HUM-XXXX [--worktree] --summary "…"` | HUM Task — `--blocks` takes one ticket; link extras with `rhjira edit TASK --blocks HUM-XXXX --noeditor` |
| `open-mr --task HUM-YYYY --tracker HUM-XXXX --cve CVE-… --title "…" --message "…"` | Run the `%prep` check, then push the branch and open a `glab` MR; `--title` and `--message` are required. Title format: `HUM-<TASK>: <pkgs>: fix <CVEs>` (see MR title and MR description below). |
| `lookaside-cmd -f FILE -p PKG` | Print upload commands; do not upload |
| `version-check PKG` | Local NVR vs CVE range / FIB / analysis NVR |
| `upstream-fix-age` | Fix-commit date vs upstream tag/release date |
| `release-bump PKG` | Next spec `.N` from metadata + spec; `--apply` writes spec |
| `log-message HUM-XXXX FILE` | Attach log/transcript file to Jira ticket |
| `worktree HUM-YYYY` | Isolated checkout under `../worktrees/` |
| `bot-mrs` / `sbom` / `spec-deps` / `show` | Individual probes |

Need internet for Jira, GitLab, and Pulp. On login failure the helper
loads `~/.config/rhjira/agent.env`. Do not paste retry loops.

## Procedure

1. **Embargo.** If the summary starts with `EMBARGOED`, stop. Do not
   analyze or write.
2. **Investigate.** `cve_helper.py investigate HUM-XXXX [HUM-YYYY ...]`
   (add `--only` if the user said only those keys). Bare numbers are HUM
   tickets. Pass all named tickets as arguments to a single `investigate`
   call — do not call it once per ticket. The command dedups per-package
   probes (`bot-mrs`, `spec-deps`) across tickets that share a package and
   runs one combined GitLab MR search over every CVE ID, so a single call
   is both correct and cheaper than separate calls. Rename the chat with
   the printed `Suggested chat title` via
   `CallMcpTool: cursor-app-control / rename_chat`. If that MCP tool is not
   connected in this session (check the dynamic tool catalog once; do not
   search further or re-litigate this per ticket), skip the rename and
   just print the suggested title for the user to apply manually.
3. **Verify applicability.** Read the recap, then gather evidence for every
   named ticket before asking the user what to do: published Hummingbird NVR
   and artifacts, component version and binary presence, target architecture
   and configuration, and whether the vulnerable path is reachable. For
   product-mismatch CVEs, follow the `spec-deps` and SBOM rules below first.
   Report the evidence-backed affected/not-affected assessment per ticket.
   Each `resolution_hint` and `recommended_next` is a suggestion, not a
   decision.
4. **Choose scope and resolution.** User-named tickets are in-scope.
   Discovered same-CVE siblings require a scope choice: ask whether to
   include all related tickets or only the named tickets. Ask before any
   closure. If FIB and a task/MR already exist, leave for advisory automation.
   If FIB is set with no task/MR, ask whether to create the task.
5. **Act** with the matching helper (below). Comment before state
   changes. Ask before any close. After acting, rename the chat again the
   same way (`cursor-app-control / rename_chat`, or print-and-skip if
   unavailable), with the resolution's **Chat prefix** (below) prepended —
   get the exact string via
   `cve_helper.py show HUM-XXXX --title-only --title-prefix "<prefix>"`.

In Cursor, `rename_chat` is documented to fire only when the user explicitly
asks in that turn; a compliant agent will not call it proactively there even
though the steps above say to. That is a known Cursor-side restriction, not
a missing dependency — print the suggested title and move on instead of
retrying or searching for a workaround.

For product-mismatch CVEs, search `spec-deps` and the SBOM using the tracker’s
**Upstream Affected Component** field, not the CVE-reported product. If the
component is not in the spec, treat an SBOM hit as a false
positive and close as NAB (`Component not Present`) without further
SBOM work. If the spec has the component, inspect the source for the
vulnerable pattern; if that path is not used, close as NAB
(`Vulnerable Code not Present`).

**Fast-path NAB (skip `version-check`):** When the bot's recap shows
`mismatch_sbom_miss` (or the SBOM check is a miss) **and** `spec-deps
hits = 0`, propose NAB (Component not Present) closure immediately
without running `version-check`.

**IMPORTANT:** Before concluding `spec-deps hits = 0`, ensure you check each component
individually if multiple are listed (run `spec-deps` for each). If the spec file has a
`Requires` or `BuildRequires` for the component, the product relies on the vulnerable
component, and you must **NOT** fast-path NAB. In this case, the package is affected/waiting
for upstream.

The three signals together are definitive proof of absence only if the component is not required:

| Signal | Value | Meaning |
| --- | --- | --- |
| Bot reason code | `mismatch_sbom_miss` | Product mismatch + SBOM miss |
| `spec-deps` hits | `0` | Component not referenced in spec (including Requires) |
| SBOM check | miss | Component not in shipped binaries |

Do not ask the user to run `version-check` first when all three agree and
no `Requires` exist. (With nothing in the spec and nothing in the SBOM,
`version-check` will always return `non_comparable`; the three signals are
already definitive.)
Present the NAB closure directly and ask for confirmation.

### Resolution commands

**Already fixed (3a):** `version-check PKG --ticket HUM-XXXX` then
`upstream-fix-age --commit URL --tag TAG` (or `--commit-date` /
`--tag-date`). `comment --kind fib --package PKG --nvr NVR` (add
`--from-json` / `--commit` / `--tag` when you have them) then
`set-fib HUM-XXXX NVR --package PKG`.
Pulp must list the NVR unless the user confirms `--force`. Do not close
Done-Errata; advisory automation does that. Chat prefix `FIB`.

**Not affected (3b):** SBOM evidence for component-absence, unless
`spec-deps` already shows no bundled reference. Preview with
`comment --kind nab --print-only`, then
`close-nab HUM-XXXX --vex "Component not Present"|"Vulnerable Code not Present"`
with the same `--package` / `--component` / SBOM flags (or `--from-json`).
Chat prefix `NAB`.

**Duplicate versioned ticket (3c):** ask, then close as Duplicate
(still ask first).

**Version, dependency update, or backport (3d/3e):** Prefer a package version
update. For Rust, Go, and Node dependencies, prefer a compatible fixed
dependency update when scanner-visible metadata would otherwise keep reporting
the vendored vulnerable version. Update direct requirements, the lockfile, and
the matching generated vendor/cache artifact together. Generate that artifact
from the checkout containing the updated lockfile, then inspect it to confirm
the fixed version is present; never pair an updated Source0 with a vendor/cache
archive generated from the old lockfile. Backport only when no compatible fixed
dependency release is available or the update is unsuitable.

**If this is a version bump (package update, not a backport):** before starting
any update work, send a Slack warning to `#hummingbird-pipeline-notifications`.
Use the `user-slack` MCP tools:

1. Read `.git/slackid` (in the repo root) to get the user's Slack member ID:

   ```bash
   cat .git/slackid
   ```

   If the file is missing or empty, **stop and tell the user:**
   > `.git/slackid` is not set. To create it:
   > 1. In Slack, click your profile picture → **Profile**
   > 2. Click the three-dot menu (⋯) → **Copy member ID**
   > 3. Run: `echo 'UXXXXXXXXX' > .git/slackid` (replace with your actual ID)
   >
   > Then re-run this step.

2. The channel ID for `#hummingbird-pipeline-notifications` is `C0AFWEGK8CR`.

3. Call `post_message` with `channel_id: C0AFWEGK8CR` and the text:
   `:warning: <@SLACK_ID> cve skill is updating <package_name> to <version>`
   where `SLACK_ID` is the ID read from `.git/slackid`, `package_name` is
   the RPM package being updated, and `version` is the target version.

Then `create-task --worktree`, move the agent into that worktree, then follow
[Rebuilding Packages](../../../documentation/operating/rebuilding-packages.md)
and [package-metadata-fields.md](../../../documentation/operating/package-metadata-fields.md).
`release-bump PKG` (or `release-bump PKG --apply`) prints/applies the next spec `.N`;
do not change metadata
`release` for a backport. Do not add `%changelog`. `lookaside-cmd` prints
copy/upload commands and waits. `open-mr` uses `Closes:` for the **task**,
`Ref:` for trackers, `CVE:` for IDs. Chat prefix `!${MR_IID}`.

**MR title:** pass `--title` in exactly this form, with no extra words:
`HUM-<TASK TICKET ID>: <packages>: fix <CVEs>`
where the ID is the HUM **task** created by `create-task` (not the tracker).
List up to 5 packages and up to 5 CVEs, comma-separated; above 5 in either
field use a count for that field instead:

- `HUM-1276: nodejs24: fix CVE-2026-120423`
- `HUM-1276: pkg1, pkg2: fix CVE-A, CVE-B`
- `HUM-1276: 8 packages: fix CVE-A, CVE-B`
- `HUM-1276: nodejs24: fix 7 CVEs`
- `HUM-1276: 8 packages: fix 7 CVEs`

Whenever a count replaces a list, the MR description **must** list every
package and every CVE ID explicitly.

**MR description:** pass `--message` using this structure:

```text
**Scope**

Jira Trackers: HUM-1234, HUM-1235
Packages: pkg1, pkg2
CVEs: CVE-A, CVE-B

**Fix**
<What changed: version/dependency update or backport, and why.>

**Verification**
<Checks done and their results, and checks not run and why not (if any).>
```

`open-mr` appends the `Closes:`, `Ref:`, and `CVE:` trailers itself; do not
include them in `--message`.

For each package backport:

1. Put provenance in the patch header. Use `Upstream: <direct commit URL>`. Add
   `Origin: <original commit URL>` only when the direct commit is a backport or adaptation.
2. Inspect the exact packaged source archive before choosing patch paths or
   strip levels. Use `make dist-git ARGS='ls-sources PKG'` (or the documented
   Lima fallback) to inspect Source0/Source1, then test the exact `%patch`
   command with `--dry-run --batch --fuzz=0`. For vendor archives, verify the
   actual crate directory instead of inferring a `name-version` path. Confirm
   that any adaptation changes only context, not the security fix or its tests.
   Generate adaptations with `git diff --binary` against the packaged source;
   do not hand-edit diff hunk headers or counts.
3. Run `release-bump PKG --apply`. Do not use `dist_git.py rebuild`; its commit message describes a
   no-change rebuild.
4. For a clean Fedora-imported package receiving a CVE backport, run
   `make dist-git ARGS='mark-modified PKG --modified --reason "Backport CVE-ID"'`.
   Use a concise CVE-based reason such as `Backport CVE-ID`; no vulnerability
   description is needed. For an already modified package, retain an adequate
   existing reason; update it only if it no longer explains why automatic
   updates are blocked (see [Package Metadata Fields](../../../documentation/operating/package-metadata-fields.md)).
   Leave independent packages unchanged and without a reason.
5. Make one commit per package. Include any changed metadata with the patch and
   spec in that commit. Use `Backport CVE-ID fix to PKG` as the subject. Do not
   create a separate metadata commit.

If `dist_git.py update` reports *"upstream branch rawhide is retired"*,
retry with `--branch f44` (or `--branch f43`). The `--dry-run` flag is
top-level: `./ci/dist_git.py --dry-run update PKG`.

Run spec, source, vendor, and `release-bump` operations through the helper in
the active worktree. Before editing a gorget source-pipeline YAML, check the
installed `gorget --program-version`, its supported schema, and whether vendor
generation runs before or after transforms; do not assume current docs match
the installed tool.

Always invoke `open-mr` from the **main repo**'s helper even when the agent is
inside a worktree:
`python /path/to/rpms/.agents/skills/cve/cve_helper.py open-mr …`

`open-mr` uses the current branch's upstream remote, then `origin` if the branch has no upstream.
Use `--push-remote NAME` to override this choice. If the current branch differs
from the task key, pass `--source-branch "$(git branch --show-current)"`. Do not
create or add a remote.

**Pre-MR check:** Do not add `%changelog` entries to spec files. CI rejects
them because they create merge-conflict risks with Fedora updates.

**%prep gate:** `open-mr` runs `ci/build_rpms.sh --prep-only PKG` for every
package changed under `rpms/` relative to the target `main`. It runs in the
current checkout, so run `open-mr` from inside the task worktree. This builds
the SRPM, which catches missing Source/Patch files and sources not yet uploaded
to the lookaside cache. It then runs `%prep`, which catches broken tarballs and
patches that do not apply. The MR is opened only if every package passes.
`open-mr` refuses to run if the source branch is not checked out or `rpms/` has
uncommitted changes. Each package's full output is saved to
`builds/PKG/prep-check.log`. If the check fails, fix the package, amend the
package commit, and rerun `open-mr`. Do not work around the failure. Use
`--skip-prep-check` only when the user explicitly asks for it, and say so in the
MR summary.

**Multi-CVE same-package pattern:** when multiple CVEs share the same
package and fix version, use **one task and one MR** for all of them.
Pass the first tracker to `create-task --blocks`, then link the rest
with `rhjira edit TASK --blocks HUM-XXXX --noeditor`. Pass all tracker
keys and CVE IDs to `open-mr --tracker` and `--cve`. Attach conversation logs
at the end to each tracker using `cve_helper.py log-message HUM-XXXX /path/to/log`.

**One CVE across versioned packages:** use one task and one MR. Link every tracker to the task.
Create one commit per package so each release bump, patch, and metadata update stay together.

**No upstream fix (3f):** `next-release HUM-XXXX --package PKG` (or
`--kind next-release` with `--cve` / `-m`). Leaves In Progress.
Chat prefix `next-rel`.

### Attaching Conversation Logs at the End

Attach once your work for this ticket is done for the session (writes/MR
sent, nothing left to do) — not after MR merge. Do it automatically, no
need to ask:

```bash
cve_helper.py log-message HUM-XXXX /path/to/log
```

**Secret redaction:** before attaching, `log-message` scans the file and
replaces likely passwords, tokens, API keys, and private keys with
`[SECRET]` (see `lib/redact.py`). This is best-effort pattern matching, not
a guarantee — do not rely on it as the only safeguard; avoid pasting real
credentials into a session in the first place. If anything is redacted, a
scratch copy (never the original file) is attached and then deleted; the
command reports how many secrets were redacted. `--print-only` never
writes a file — it just reports the count of what would be redacted. Check
the printed message to confirm before assuming a ticket comment thread is
safe to share further.

**Finding the log file by environment:**

`cve_helper.py log-message` can auto-export and attach the current session
for Claude Code, OpenCode, and Cursor directly — use these instead of
hunting for a file path (see `lib/log.py` for the underlying format
handling):

- **Claude Code:** `cve_helper.py log-message HUM-XXXX --claude`
  (finds the most recently modified `~/.claude/projects/<cwd>/*.jsonl`
  session; pass an explicit `.jsonl` path as the value to target another one).
- **OpenCode:** `cve_helper.py log-message HUM-XXXX --opencode`. There is no
  per-session file — `~/.local/share/opencode/log/opencode.log` is one
  shared, unbounded log across all sessions/projects and must never be
  attached. Transcripts live in `~/.local/share/opencode/opencode.db`
  (SQLite: `session`, `message`, `part` tables); this defaults to the most
  recently updated session, or pass a specific `ses_...` id as the value.
- **Cursor:** `cve_helper.py log-message HUM-XXXX --cursor`. Best-effort —
  Cursor's on-disk chat format is undocumented and has changed across
  releases. Searches desktop `~/.config/Cursor/User` and remote
  `~/.cursor-server/data/User`, prefers the newer of Composer DB vs
  `~/.cursor/projects/<slug>/agent-transcripts/`, and accepts a composer id
  or agent-transcript uuid as the value. If it fails, omit this step rather
  than guessing at a manual log file.
- **Codex:** Check `~/.codex/sessions/`, `~/.codex/logs/`, or `$CODEX_LOG`.
- **CI / Worker Sandbox:** Use `$AGENT_LOG`, `$TRANSCRIPT_PATH`, or harness log output.

If the session log file cannot be located in the current environment, omit this step.

| Scenario | Resolution | VEX |
| --- | --- | --- |
| Different product / disputed | Not a Bug | Vulnerable Code not Present |
| Product not in Hummingbird | Not a Bug | Component not Present |
| SRPM / vendored dep / backport fixed | Done-Errata via FIB | (not set) |
| Duplicate of versioned ticket | Duplicate | (not set) |
| Affected, no upstream fix | In Progress | `cve-next-release` |

## Important rules

1. Never close Done-Errata yourself. Set FIB and leave the tracker open.
2. Always ask before closing any ticket.
3. Create HUM tasks for backport or update work; link them with blockers.
4. Use `cve_helper.py` for Jira writes (`jira_client` / `pulp` imports).
   Do not use the Atlassian MCP, `cve_analysis.py --resolve`,
   `advisory_handler`, or `gitlab_sync` for package MRs.
5. Do not paste retry helpers. Load `agent.env` once on auth failure.
6. Do not close mismatch tickets without SBOM evidence unless
   `spec-deps` shows no bundled reference (that is the evidence) or
   the user overrides.
7. Comment first, then change state.
8. Named tickets are the write set; `only` means named tickets only.
9. Package metadata semantics follow [ADR-0003](https://hummingbird-project.io/docs/adrs/0003-metadata-and-versioning/).
   Use the explicit CVE rules above for clean Fedora-imported and independent
   packages; consult [Package Metadata Fields](../../../documentation/operating/package-metadata-fields.md)
   for states not covered here rather than inferring metadata behavior.
10. Do not change the Fedora release baseline for local backports or
    FIB/advisory reasons alone; see [ADR-0003](https://hummingbird-project.io/docs/adrs/0003-metadata-and-versioning/).
11. When your work on a ticket is done for the session — not waiting on
    MR merge — attach the relevant session log following the retention rules above:
    `cve_helper.py log-message HUM-XXXX /path/to/log` (omit if not found, no need to ask).

## Worktree cleanup

After the MR merges, from the main repo:

```bash
git worktree remove "$(git rev-parse --show-toplevel)/../worktrees/HUM-YYYY"
```

Then `move_agent_to_root` back to the main checkout.
