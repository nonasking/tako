# tako

Create a Jira ticket with one shell line. Used on top of Claude Code slash commands, it auto-drafts the title and body from session context. The backend is the Atlassian Cloud REST API v3.

> Difference from a Jira MCP (e.g. Atlassian's official Remote MCP): tako lets the LLM draft *only the body*, while authentication, payload, ADF conversion, and the REST call are handled by deterministic code — so it connects straight to Jira with no intermediate server, keeping dependencies and tokens light. The trade-off: fields and issue types must be registered in config directly, unlike the MCP's runtime lookup.

## Prerequisites

- macOS / Linux
- Python ≥ 3.10
- An Atlassian Cloud account + API token ([id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens))
- Optional: [Claude Code](https://docs.anthropic.com/claude-code) — only if you use session-context mode

## Install

```bash
git clone <repo-url> tako && cd tako
pip install -e .
./install.sh        # register slash commands (optional)
```

## First run

```bash
tako init
```

Enter 5 items (site domain / default project / default issue type / email / API token) and two files are created:

- `~/.config/tako/config.yaml` — site·project·issue types
- `~/.config/tako/credentials.json` — email·token (chmod 0600)

If the files exist, it asks before overwriting. Skip with `--force`. To write them by hand, copy [`config.example.yaml`](./config.example.yaml) and edit.

## Usage — two modes

### A) Directly from the shell

```bash
# interactive (recommended) — prompts in order, like this
tako new
#  Project key [WL]:
#  Issue type (Task / 기능변경 / 버그수정) [기능변경]:
#  Summary: ...
#  Body (markdown) (Ctrl+D to end): ...
#  Parent (alias/key, Enter for none):
#  Assignee (me / email / accountId, Enter for none):
#  Story points (integer, Enter for none):
#  Due date YYYY-MM-DD (Enter for none):
#  Tickets to link (KEY[:TYPE], comma-separated, Enter for none):
#  → preview → create in Jira? (Y/n)

# pre-specify some of it
tako new --project WL --issue-type 기능변경 --assignee me

# create a sub-task — parent key + the site's sub-task issue-type name
tako new --project WL --issue-type 하위작업 --parent WL-9058 \
  --summary "add test for the payment-refund virtual-account case" --description "..."

# all args + skip the confirmation step
tako new \
  --project WL \
  --issue-type 기능변경 \
  --summary "sprint board sorting is broken" \
  --description "## Repro
1. open the board
2. click sort

## Expected
sort is applied" \
  --assignee jy@example.com \
  --story-points 3 \
  --duedate 2026-06-15 \
  --link WL-100 \
  --link "WL-200:Blocks" \
  --yes
```

`--assignee` / `--story-points` / `--duedate` / `--link` are optional. In interactive mode, leaving the input blank also skips them.

`--assignee` accepts `me` (yourself, one `/myself` call), an email (one `/user/search` call, only an exact single match is allowed), or an accountId directly. Korean names/nicknames are unsupported in v1.x. Email search may be blocked depending on the site's GDPR settings — work around it by entering the accountId directly. Setting `jira.default_assignee` in config to 'me'/email/accountId applies it as the default in interactive blank-input / auto mode where `--assignee` is omitted.

`--link KEY[:TYPE]` is repeatable. When TYPE is omitted, `Relates` is applied. Common TYPEs: `Blocks` / `Relates` / `Duplicates` / `Causes` / `Clones` (varies per site). Check your site's link types:

```bash
curl -u "email:token" "https://<site>/rest/api/3/issueLinkType" | jq '.issueLinkTypes[].name'
```

The link call is a *separate REST request after issue creation*. If issue creation succeeds but some links fail, the *ticket stays*, only the failed links are reported, and it exits with code 1. To *actually include story points in the payload*, config's `jira.fields.story_points` must hold your environment's customfield ID (without it, a warning is printed and the issue is created with only SP excluded). Two ways:

```bash
# Option 1) auto — find a candidate in Jira and register it in one line
tako fields detect story_points --save

# Option 2) if you already know the ID, register it directly
tako fields set story_points customfield_10016
```

`tako fields detect <name>` without `--save` only prints the result and doesn't touch config (auto-writing config could lose comments). Supported names: `story_points` (v1.x).

Flow: input → preview → Y/n → REST → key + links. No Claude Code needed.

Right after creation, the ticket URL is auto-copied to the system clipboard (macOS `pbcopy` / Linux `xclip` or `xsel`). Turn it off with `jira.auto_copy_url: false` in config. In environments without those tools it's silently skipped — creation itself is unaffected.

### B) Inside Claude Code (using session context)

```
/tako file a ticket for the sort bug I just found. WL project, parent is infra
/tako cut this as a sub-task of WL-9058    # → issue type auto-set to the site's sub-task type, --parent WL-9058
```

The LLM summarizes the session → preview → after confirmation calls `tako new`. With no session context, mode A is lighter.

The body candidate is designed to *always include two sections at the very top* — `[내가 한 일]` (what I did) and `[현재 상태/결론]` (current state/conclusion). Optional sections like background/impact go below as needed. If the self-check step finds either section missing, the preview warns (it doesn't auto-fix — the user decides).

> Creating a sub-task requires *the site's sub-task type name to be registered* under `issue_types` in your `~/.config/tako/config.yaml` (e.g. `하위작업`, `Sub-task`, `서브태스크`). Without it, `tako new` rejects it as a "disallowed issue type".

### C) Review an existing ticket against session work (`/tako-check`)

```
/tako-check WL-8876
```

The LLM cross-checks how well the work done in the session satisfies that ticket's spec and reports. To just fetch from the shell:

```bash
tako show WL-8876                  # human-friendly text
tako show WL-8876 --json           # raw JSON (for automation / LLMs)
tako show https://<site>/browse/WL-8876   # a URL is fine too
tako show WL-8876 --max-comments 0 # exclude comments
```

`tako show` handles ADF→markdown conversion, authentication, and the REST call.

> Sensitive-data caution: the ticket body is exposed to the session, so use carefully with tickets containing tokens/passwords (no auto-filtering in v1.x).

### D) Update an existing ticket's title/body (`/tako-update`)

```
/tako-update WL-8876
```

*Appends* the session's work to the ticket body (default). The LLM turns session context → an auto-written section → preview → Y/n → REST. Directly from the shell:

```bash
# default append — adds a '## Update (YYYY-MM-DD)' section at the end of the body
tako update WL-8876 --body "$(cat <<'BODY'
- work item 1
- work item 2
BODY
)" --yes

# name the section
tako update WL-8876 --section "Progress" --body "..."

# replace the whole body (dangerous — review carefully in the preview)
tako update WL-8876 --mode overwrite --body "..."

# change only the title (body untouched)
tako update WL-8876 --summary "replace with a new title"

# change title + body together
tako update WL-8876 --summary "new title" --body "..." --mode overwrite
```

At least *one* of `--summary` and `--body` is required. `--mode` affects *only the body* — the title is always replaced.

> Both body and title are *permanent records*, so beware of sensitive data and mistakes. Always review at the preview step.

### E) List/filter tickets (`tako list` / `/tako-list`)

```bash
# my tickets (config.default_project + yourself, automatically)
tako list --assignee me

# common combos
tako list --assignee me --status 진행중 --updated 7d
tako list --type 에픽 --limit 50
tako list --parent WL-9200          # child issues
tako list --label backend --query 정렬
tako list --project WL --project ABC --assignee me   # multiple projects at once

# advanced — raw JQL (ignores other args)
tako list --jql "project = WL AND assignee = currentUser() AND duedate < now()"

# JSON for automation / LLMs
tako list --assignee me --json

# to Excel (UTF-8 BOM CSV — opens in Excel on double-click)
tako list --assignee me --csv --output my-issues.csv
tako list --assignee me --csv > my-issues.csv   # stdout redirect also works
```

Supported args: `--assignee` (me / email / accountId), `--project` (repeatable, query multiple projects at once), `--status` (repeatable), `--type` (repeatable), `--parent`, `--label` (repeatable), `--updated` / `--created` (`7d`/`1w`/`YYYY-MM-DD` / comparisons like `<=YYYY-MM-DD` / `YYYY-MM-DD..YYYY-MM-DD` range), `--due` (`overdue` / `none` / `set` / `YYYY-MM-DD` / `<=YYYY-MM-DD` etc. / range), `--sp` (integer / `>=N` / `<=N` / `none` / `set`), `--query`, `--jql`, `--limit` (default 20), `--all` (auto-paginate), `--json`, `--csv`, `--output / -o`, `--wizard / -i` (interactive input).

The *range* form for `--updated` / `--created` / `--due` is `YYYY-MM-DD..YYYY-MM-DD` or `YYYY-MM-DD~YYYY-MM-DD` (alias), both endpoints inclusive. It can't be mixed with shorthand (`7d`). If the start is later than the end, it's rejected.

```bash
tako list --updated 2026-05-01..2026-05-15     # updated between 5/1 and 5/15
tako list --created 2026-03-01~2026-03-31      # created during the month of March
tako list --due 2026-06-01..2026-06-30         # due in June
```

When the filter gets long for one line, use `tako list --wizard` (or `-i`) — it asks per item and skips blank input. It composes with CLI args (e.g. `tako list -i --assignee me` skips the assignee prompt and asks the rest). Right after the output, it prints a *one-line shell command* that reproduces the same query to stderr as a hint, so you can save it as an alias if you like it.

**`전체` / `all` / `*` keyword** — usable on any filter (both interactive and CLI):

- Status / type / label / assignee: `전체` (all) = same as blank input (that condition isn't applied).
- Project: `전체` = ignore `default_project` too + drop the `project` clause from the JQL entirely → all projects on the site.
- Max results (`--limit` / interactive limit step): `전체` = auto `--all` + 100 per page to the end.

```bash
tako list --project 전체 --assignee me --updated 7d   # my tickets this week across all site projects
tako list -i  # answering "Project: 전체", "Max: 전체" in interactive mode does the same
```

> Note: specifying only `--project 전체` with no other condition would mean *every issue on the entire site* and is rejected. Give at least one other condition with it.

`--all` auto-repeats every page (100 max per page). Beware large result sets — 943 issues span about 10 pages.

Default columns: `key, status, type, assignee, created, updated, duedate, summary, parent, url`. If your config has a `jira.fields.story_points` mapping, a `story_points` column is auto-added right after `type`. Without the mapping, SP filter/column are disabled with a notice.

```bash
# due / SP filter examples
tako list --due overdue                       # overdue
tako list --due "<=2026-06-15"                # through June 15
tako list --sp ">=3" --assignee me            # my issues with SP ≥ 3
tako list --sp none --status 진행중           # in-progress with no SP set

# find stale tickets
tako list --assignee me --updated "<=2026-04-01"   # my tickets untouched since April 1

# full fetch + CSV
tako list --created 2026-03-01 --all --csv -o issues-since-march.csv
```

Claude Code slash commands do *natural language → arg mapping*:
```
/tako-list what I worked on this week
/tako-list epics among WL-9200's children
/tako-list in-progress in the last month + label backend
```

> Assignee by *Korean name* is unsupported in v1.x. Only `me` / email / accountId.

### F) Customize the body-writing guide (`tako guide` / `/tako-guide`)

The *rules* by which `/tako` and `/tako-update` write the title and body are set by a single **personal guide file** — `~/.config/tako/body_guide.md`. Title format, required sections, writing tone (plain language a non-engineer PM can follow, no pasted code, etc.), and self-check items all live in this file, and the slash commands read it before writing the body and follow it exactly.

If the file doesn't exist, a **default guide** (bundled with the package) applies. Create a personal file only when you want your own team's style.

```bash
tako guide show      # print the currently applied guide (default if no personal file)
tako guide path      # print the personal guide path
tako guide init      # create the personal file from the default → edit in your editor
tako guide reset     # revert the personal guide to the default
```

Inside Claude Code, edit it conversationally (preview → save after confirmation):

```
/tako-guide                          # view the current guide
/tako-guide allow code examples       # tweak part of the rules
/tako-guide write shorter bodies      # adjust tone
/tako-guide revert to default         # reset
```

> The guide is *fully customizable* — even quality guards like required sections and "no unverified claims" are yours to change. Removing quality guards can make handover/traceability harder, so `/tako-guide` flags such a change once.
> To use a different path, set the `TAKO_GUIDE_PATH` environment variable.

## Partial invocation (debugging / automation)

```bash
# preview only
echo '{"summary":"x","description":"y"}' | tako preview

# payload JSON only
echo '{"summary":"x","description":"y"}' | tako build

# TTY interactive → payload JSON
tako interactive
```

Only `new` / `fields detect` make actual REST calls. The rest is local processing.

## Design — why a CLI + thin skill instead of the Jira MCP

(The detailed version of the quote at the top. A shared design principle with oobs · nacho — and tako actually scrapped an MCP-backend decision early in v1 and switched to a direct REST connection; see the CLAUDE.md change history.)

MCP's context cost comes not from calls but from **residency**. Attach Atlassian's official MCP and dozens of tool schemas ride in the system prompt of *every* session — taking thousands to tens of thousands of tokens *even in sessions that never touch Jira*. tako converts that residency cost into a per-call cost:

- **Residency cost**: just one line of slash-command description (tens of tokens). Usage loads only at the moment `/tako` is invoked.
- Per-call cost is similar to MCP — the savings are entirely in the resident schemas.
- **Direct shell calls outside a session = 0 tokens** + authentication, payload, and ADF conversion are guaranteed by deterministic code.

Honest trade-offs:

- Recent Claude Code lazy-loads MCP tools (ToolSearch), so the residency gap is smaller than it used to be.
- Where MCP wins — typed schemas reduce malformed calls, the server manages auth, and **vendor maintenance**: when the Jira REST API changes, tako has to be fixed by hand. Registering fields and issue types directly in config is also a manual cost versus the MCP's runtime lookup (same as the top quote).

## Environment assumptions

- The target Jira project is team-managed. All parent-child relations are expressed by the single `parent` field — a regular issue under an Epic (`parent` = Epic key) and a *sub-task* under a regular issue (`parent` = regular issue key) take the same form. Classic projects are unverified in v1.
- The description is taken as markdown and converted to ADF via [`md-to-adf`](https://pypi.org/project/md-to-adf/) before sending.
- v1 assumes single-user use. Shared team config overrides / multi-site / per-user field customization are v1.1+ extension points.

## Directory

```
tako/
├── commands/
│   ├── tako.md             /tako slash command (create)
│   ├── tako-update.md      /tako-update slash command (edit title/body)
│   ├── tako-check.md       /tako-check slash command (review)
│   ├── tako-list.md        /tako-list slash command (list)
│   └── tako-guide.md       /tako-guide slash command (customize body guide)
├── tako/                   Python package
│   ├── auth.py              credentials loader
│   ├── jira_client.py       REST + ADF conversion entry point
│   ├── adf_to_md.py         ADF → markdown
│   ├── issue_draft.py       payload builder + preview
│   ├── fields.py            custom field mapping helper
│   ├── prompts.py           interactive input
│   ├── config.py            settings + init wizard
│   ├── guide.py             body-guide load/create
│   ├── templates/           bundled resources (default guide, etc.)
│   └── main.py              entry point
├── config.example.yaml     example config
└── install.sh              register slash commands (optional)
```

## Troubleshooting

- `tako: command not found` — not on PATH. `pip install -e .` didn't finish, or a different venv. As a fallback, `python -m tako ...` behaves identically.
- `설정 파일이 없습니다` (no config file) — `tako init`. For a different path, use the `TAKO_CONFIG_PATH` environment variable.
- `creds 없음` (no creds) — same as above.
- `허용 안 된 이슈 타입` (disallowed issue type) — add it under `issue_types` in `~/.config/tako/config.yaml`.
- `401 인증 실패` (401 auth failed) — token expired. Re-enter with `tako init --force`.
- `403 권한 없음` (403 no permission) — check you have issue-create permission on that project.
- `400/422 입력 거부` (400/422 input rejected) — check the response body. Common cause: the issue-type name isn't defined in that project.
- `story_points 값을 받았지만 ... 페이로드에서 제외함` (got a story_points value but excluded it from the payload) — auto-register in one line with `tako fields detect story_points --save`.
```
