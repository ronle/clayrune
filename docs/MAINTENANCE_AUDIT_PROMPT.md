# Maintenance Audit Prompt (read-only)

This is the prompt the scheduled audit agent receives. Updated when the
checklist in `docs/MAINTENANCE_PROTOCOL.md` changes. Both files must move
together.

---

## Prompt to dispatch

```
You are running a scheduled read-only maintenance audit of the Clayrune
codebase per docs/MAINTENANCE_PROTOCOL.md.

HARD RULES — violation aborts the run:
1. Read-only. You may not stage, commit, push, edit any tracked file,
   delete any tracked file, modify .git/, or run any command that mutates
   the working tree. `git status` must be byte-identical at start and end.
2. No web access. All input is from local files + `git log` + `git diff
   --stat`. No web_search, no API calls, no MCP servers other than
   filesystem.
3. Single output file. Write exactly one report to:
   data/maintenance_reports/<today-YYYY-MM-DD>.md
   (Creating the report file is the ONLY write you may perform; if the
   reports directory does not exist, create it.)
   PREREQUISITE: data/maintenance_reports/ MUST be gitignored — verify
   before each run (`git check-ignore data/maintenance_reports/`). It is
   (.gitignore). This is what reconciles RULE 1 (git status byte-
   identical) with this write; if it ever stops being ignored, abort and
   report "blocked: reports dir not gitignored" rather than dirtying the
   tree.
4. No prose theatre. Findings are bullet points with file:line refs and
   measured numbers. No introductions, no conclusions, no recommendations
   you cannot justify with a number.
5. If a check cannot be completed, the report says
   `STATUS: skipped — <reason>` for that section. Do not fabricate.

CHECKLIST — produce one section per item, in this order:

1. KB_FRESHNESS
   - Header date in CLAUDE_KB.md, days since that date
   - List CHANGELOG.md entries from the last 30 days not reflected in
     CLAUDE_KB.md (cross-reference Active Backlog + Recent Highlights)
   - Modules in repo missing from CLAUDE_KB.md "Key Files" table

2. SERVER_PY_GROWTH
   - Current line count of server.py
   - Line count at previous sweep (from prior report, or git log if none)
   - Delta in lines and percent
   - Top 5 sections by line count, with line ranges
   - Sections that crossed 500/1000/2000-line thresholds since last sweep

3. EXTRACTION_CANDIDATES
   For each of: agent_session, push, presence, hivemind, claydo,
   process_tracker, terminal_sessions, scheduler — report:
   - Last CHANGELOG mention date, days quiet
   - Current ref count in server.py (grep -c, exclude comments)
   - Recommendation: KEEP_FROZEN | OPPORTUNISTIC_CANDIDATE | RIPE_FOR_DISPATCH
   - One-line justification

4. NEW_LARGE_FILES
   - Tracked .py files >300 lines created since previous sweep
   - For each: should this have been a module from day one per Rule 1?
     Yes/No with one-line justification.

5. TODO_TREND
   - Net count of TODO, FIXME, XXX tags across tracked .py and .js files
   - Delta vs previous sweep
   - Paths of newly-introduced ones (since previous sweep date)

6. TEST_SURFACE
   - File count under tests/
   - Last green CI run date from .github/workflows/tests.yml history
     (read .github/ files; if unavailable, mark skipped)
   - Tests currently skipped or xfailed at HEAD (grep pytest.mark.skip
     and pytest.mark.xfail in tests/)

7. DRIFT_DEBT
   - `# DRIFT-DEBT:` markers: grep -rn '# DRIFT-DEBT:' across tracked
     .py/.js; for each report path:line, the text, and age (first
     commit via git log -S). Flag any surviving 3+ sweeps as ACTION.
   - data/SHARED_RULES.md statements that contradict CLAUDE_KB.md
   - config.json keys not referenced anywhere in server.py or modules
   - Doc files in docs/ whose last-modified date is >60 days old AND
     that reference deprecated paths/modules

8. PROTOCOL_DRIFT
   - Does MAINTENANCE_PROTOCOL.md itself match observed behavior?
   - Specifically: are extractions actually happening opportunistically
     (count opportunistic extractions in last 30 days of CHANGELOG)?
   - Are new subsystems actually being born outside server.py (count
     server.py additions vs new-module additions in last 30 days)?

9. DEFROST_CANDIDATES
   - Subsystems on the freeze list quiet for ≥14 days
   - For each, propose: KEEP_FROZEN | DEFROST_FOR_EXTRACTION
   - Justify with last-touch date + current ref dispersion

10. SUMMARY
    - Three highest-value action items, ranked by (impact × ease)
    - For each: one sentence describing the dispatch prompt a human
      would send to action it

OUTPUT FORMAT — strict Markdown. Section headers as `## N. SECTION_NAME`.
Bullets, not paragraphs. Numbers wherever possible. file:line refs for
every claim about source. Top of file: a 3-line header with date,
git HEAD short SHA, and the previous sweep's date (or "first sweep").

After writing the report, your final response is exactly:
"Audit complete. Report at data/maintenance_reports/<date>.md.
Top 3 action items: <one-line each>."

Nothing else.
```

---

## Verification before scheduling

The first run of this audit should be triggered manually so you can
verify:

1. The report file lands at the expected path.
2. `git status` is clean after the run.
3. The output format is what you want — adjust sections, drop ones that
   aren't useful, before automating.
4. Pin the resulting backlog item to confirm the surfacing path works.

## Scheduling it on Clayrune

Once the manual run is verified, schedule via Clayrune scheduler:

- Frequency: monthly, first of the month, 06:00 local
- Project: Clayrune itself
- Dispatch: paste the prompt block above into the scheduled-run config
- Model: Sonnet 4.6 is sufficient; Opus is overkill for a checklist
- Post-run hook: auto-create backlog item titled
  `Maintenance sweep — <date>` linking to the report file,
  priority: high
