"""Generate Shagun_Task_Tracker.xlsx — Shagun's (Track B) task list and work log.

Regenerate with:  python build_task_tracker.py [--open]

`--open` launches the sheet in the default application afterwards. Run it that
way at the end of every task: update the TASKS / LOG lists below, re-run, and
the sheet opens showing what changed.

Sheet 1 "Task List"  — every Track B / Sprint-0 task, its POA reference, status,
                       completion date and a "What I did" column filled in as
                       each task lands.
Sheet 2 "Work Log"   — dated, one row per session/commit, so the tracker shows
                       *what was actually done* rather than only a status flag.
Sheet 3 "Reference"  — track split, shared-file rules, open questions.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

OUT = "Shagun_Task_Tracker.xlsx"

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
TITLE_FONT = Font(bold=True, size=14, color="1F3864")
DONE_FILL = PatternFill("solid", fgColor="C6EFCE")
PROG_FILL = PatternFill("solid", fgColor="FFEB9C")
TODO_FILL = PatternFill("solid", fgColor="F2F2F2")
BLOCK_FILL = PatternFill("solid", fgColor="FFC7CE")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

REVERT_FILL = PatternFill("solid", fgColor="E4DFEC")

STATUS_FILL = {
    "DONE": DONE_FILL,
    "In progress": PROG_FILL,
    "Not started": TODO_FILL,
    "Blocked": BLOCK_FILL,
    "Reverted": REVERT_FILL,
}

# --------------------------------------------------------------------------- #
# Sheet 1 — Task List
# --------------------------------------------------------------------------- #
TASK_COLUMNS = [
    ("Task ID", 10),
    ("Task", 46),
    ("POA reference", 24),
    ("Phase", 14),
    ("Status", 13),
    ("Started", 12),
    ("Completed", 12),
    ("What I did (fill in on completion)", 74),
    ("Files / evidence", 46),
]

TASKS = [
    # --- Sprint 0: shared test-dataset backlog (POA/16 §16) ----------------- #
    ("S3", "Conversation-intent scenarios + scripted trees (17 intents)",
     "POA/16 §16.4, §16.6", "Sprint 0", "DONE", "2026-09-01", "2026-09-01",
     "Built IntentScenarioComposer with one scripted tree per mandated intent — depth varied by "
     "structural distinctness (single-turn lookup, multi-turn slot filling, out-of-scope refusal, "
     "ambiguity->clarify, and CV-17 mid-conversation requirement change as a real branching tree). "
     "Shipped BOTH halves of the §16.6 Phase-2 seam: ReplySource protocol (ScriptedReplySource for "
     "Phase 1) and Evaluator protocol (ExactExpectationEvaluator, judges a transcript not the script "
     "so it survives LLM-generated replies). All claims grounded in the same World the booking-API "
     "mock reads. Split delivered_excludes (claim WRONG vs live data, must never be delivered) from "
     "superseded_tokens (claim CORRECT when said, then obsoleted — must not reach the final "
     "confirmation); conflating them made one of the two cases silently stop testing anything.",
     "generator/intents.py (new), models.py, pipeline.py, cli.py, "
     "tests/test_conversation_intents.py — 73 tests green. Reverted then restored "
     "2026-09-01; in the tree and green. Local only, not pushed."),

    ("S4", "PII redaction fixtures — obvious + embedded PII",
     "POA/16 §16.5", "Sprint 0", "Not started", "", "", "", ""),

    ("S6", "Future-client-compat layer — repository interface, field_map.yaml, lenient DTO",
     "POA/16 §16 item 6", "Sprint 0", "Not started", "", "", "", ""),

    # --- Track B: service modules ------------------------------------------ #
    ("B1", "Admin Console & Trigger Configuration — persistence + CRUD",
     "POA/13", "Track B", "Not started", "", "", "",
     "NOTE: the config CONTRACT already exists (TriggerConfig, RoutingRule, FrequencyCap, Deferred "
     "in generator/models.py + defaults in fixtures.py). This task owes persistence + admin CRUD, "
     "not the schema. RoutingRule.match/.route/.sla are bare dicts to tighten."),

    ("B2", "Conversation Orchestrator (context + personalisation)",
     "POA/08", "Track B", "Not started", "", "", "", ""),

    ("B3", "LLM Integration & Fallback Service",
     "POA/09", "Track B", "Not started", "", "", "", ""),

    ("B4", "Claim Verification Service",
     "POA/10", "Track B", "Not started", "", "", "",
     "booking-API mock already built — ready to start any time."),

    ("B5", "Chatbot UI Integration (HS-103) + admin UI",
     "POA/11", "Track B", "Not started", "", "", "",
     "HS-103 mock already built."),

    ("B6", "Customer Response & Multi-turn Conversation Manager",
     "POA/12", "Track B", "Not started", "", "", "",
     "Consumes the S3 trees. Must honour delivered_excludes vs superseded_tokens as two "
     "different rules."),

    ("B7", "Audit, Reporting & Analytics",
     "POA/14", "Track B", "Not started", "", "", "", ""),

    # --- Process ------------------------------------------------------------ #
    ("P1", "Two-person work split + daily git workflow (POA/18)",
     "POA/18", "Process", "DONE", "2026-08-31", "2026-09-01",
     "Wrote POA/18: split the module dependency graph into two tracks so no module gets built "
     "twice, mapped cross-track contract points, and defined the daily push/merge routine. "
     "Corrected an early error in it — B1 does NOT block Prasad's A5/A6/A8, because the M13 "
     "contract already exists. Confirmed track ownership after Prasad accepted Track A.",
     "POA/18_Team_Work_Split_and_Git_Workflow.md + POA/00 index row. Reverted then restored "
     "2026-09-01; in the tree. Local only, not pushed."),

    ("P2", "Agree git workflow (POA/18 §6) with Prasad",
     "POA/18 §6", "Process", "Blocked", "", "",
     "", "Waiting on Prasad. §6 still describes per-person branches while we push to main."),

    ("P3", "Agree service-code repo layout (services/*) with Prasad",
     "POA/18 §8.2", "Process", "Blocked", "", "",
     "", "Waiting on Prasad. Blocks B1 scaffolding. No POA specifies a layout yet."),
]

# --------------------------------------------------------------------------- #
# Sheet 2 — Work Log
# --------------------------------------------------------------------------- #
LOG_COLUMNS = [
    ("Date", 12),
    ("Task ID", 10),
    ("What I did", 96),
    ("Outcome / proof", 44),
    ("Pushed", 12),
]

LOG = [
    ("2026-08-31", "P1",
     "Read the whole repo and all 18 POA files. Wrote POA/18: cut the module dependency graph into "
     "two tracks that live in separate directories, so parallel work merges additively instead of "
     "colliding. Added Sprint-0 file-level split, cross-track contract points and a status tracker.",
     "POA/18 created, indexed in POA/00", "d1f6987"),

    ("2026-09-01", "P1",
     "Prasad accepted Track A. Marked ownership CONFIRMED in POA/18 and closed open question 1.",
     "POA/18 v1.1", "3f444b8"),

    ("2026-09-01", "S3",
     "Built the 17 scripted conversation trees, the ReplySource seam, world-grounded claims, and "
     "the pipeline/CLI wiring to write them to test_data/conversations/.",
     "23 new tests, 66 total green", "9840cb0"),

    ("2026-09-01", "S3",
     "Fixed a contract bug found in review: delivered_excludes was carrying two incompatible "
     "meanings. Split out superseded_tokens, added the Evaluator half of the §16.6 Phase-2 seam, "
     "promoted path()/paths()/all_turns() so there is only one definition of a path, and extended "
     "the tree-wide invariants to cover branch turns (they were escaping world-grounding).",
     "73 tests green", "78aedf0"),

    ("2026-09-01", "—",
     "Reverted all five commits (POA/18 + S3 + tracker) at Shagun's request. Used git revert "
     "rather than a force-push so history stays intact and Prasad's clone is unaffected. Working "
     "tree verified byte-identical to 67b977c; 43 tests green. New standing rule from here: all "
     "work stays LOCAL, nothing goes to GitHub unless explicitly asked.",
     "Tree == 67b977c, 43 tests green", "local only"),

    ("2026-09-01", "S3 / P1",
     "Restored both reverted deliverables locally at Shagun's request — S3 first, then POA/18. "
     "Restored at file level from 78aedf0 / b54edb8 rather than by reverting the revert, so each "
     "came back independently and history stays readable. Restoring POA/18 also re-validated the "
     "'see POA/18 §4' pointer in generator/models.py, which was dangling in between.",
     "Working tree == b54edb8 (plus tracker improvements); 73 tests green", "local only"),
]

# --------------------------------------------------------------------------- #
# Sheet 3 — Reference
# --------------------------------------------------------------------------- #
REFERENCE = [
    ("THE SPLIT", ""),
    ("Prasad — Track A", "Event & trigger pipeline + platform: POA 01-07, 15. Sprint 0: S1, S2, S5."),
    ("Shagun — Track B", "Conversation, config & reporting: POA 08-14. Sprint 0: S3, S4, S6."),
    ("", ""),
    ("SHARED FILES — announce before pushing", ""),
    ("generator/models.py", "Both tracks need it. Append one bounded contiguous block; never rename "
                            "or remove a field the other track uses."),
    ("generator/pipeline.py", "Both add writers. Keep the diff to contiguous appended lines."),
    ("tests/conftest.py", "Keep new fixtures in your own test module unless genuinely shared."),
    ("", ""),
    ("OPEN — waiting on Prasad", ""),
    ("Git workflow (POA/18 §6)", "Doc says per-person branches; we are pushing to main. Needs a decision."),
    ("Repo layout", "services/platform, services/event_pipeline, services/conversation — unconfirmed."),
    ("", ""),
    ("OPEN — needs the client (POA/00 §7)", ""),
    ("LLM provider & data residency", "Which model, which region, Hertz constraints."),
    ("Booking API", "One service or several? Latency SLA?"),
    ("HS-103 surface", "REST, websocket or embedded widget SDK?"),
    ("Support/agent queue", "Zendesk, Salesforce or in-house?"),
    ("", ""),
    ("WORKING RULES", ""),
    ("Everything stays local", "No pushes to GitHub. Commit locally only, unless Shagun explicitly "
                              "asks in that moment. origin/main is still at b54edb8; the revert "
                              "commit is local and un-pushed."),
    ("Sheet updates automatically", "After every completed task: update this sheet and open it. "
                                    "No need to ask."),
    ("", ""),
    ("HOW TO USE THIS FILE", ""),
    ("On starting a task", "Set Status to 'In progress' and fill Started."),
    ("On finishing", "Set Status 'DONE', fill Completed, and write What I did + Files/evidence."),
    ("Every session", "Add a Work Log row — the log is what shows effort, the status is only a flag."),
    ("Regenerate", "python build_task_tracker.py  (overwrites this file — edit the script, not just "
                   "the sheet, if you want changes to survive)"),
]


def _style_header(ws, columns, row=1):
    for i, (name, width) in enumerate(columns, start=1):
        c = ws.cell(row=row, column=i, value=name)
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(vertical="center", horizontal="left", wrap_text=True)
        c.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[row].height = 28


def build() -> None:
    wb = Workbook()

    # ---- Sheet 1: Task List ---------------------------------------------- #
    ws = wb.active
    ws.title = "Task List"
    ws["A1"] = "Shagun — Track B task list (HFB Proactive AI Chatbot)"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A1:I1")
    ws["A2"] = ("Track B = conversation, config & reporting. Prasad owns Track A (event/trigger "
                "pipeline + platform). See POA/18 for the full split.")
    ws["A2"].font = Font(italic=True, size=9, color="595959")
    ws.merge_cells("A2:I2")

    _style_header(ws, TASK_COLUMNS, row=4)
    for r, row in enumerate(TASKS, start=5):
        for i, val in enumerate(row, start=1):
            c = ws.cell(row=r, column=i, value=val)
            c.alignment = Alignment(vertical="top", wrap_text=True)
            c.border = BORDER
        ws.cell(row=r, column=5).fill = STATUS_FILL.get(row[4], TODO_FILL)
        ws.cell(row=r, column=5).font = Font(bold=True)
        ws.row_dimensions[r].height = 92 if row[4] == "DONE" else 34

    dv = DataValidation(
        type="list", formula1='"Not started,In progress,Blocked,DONE,Reverted"', allow_blank=False
    )
    ws.add_data_validation(dv)
    dv.add(f"E5:E{4 + len(TASKS)}")

    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:I{4 + len(TASKS)}"

    # ---- Sheet 2: Work Log ------------------------------------------------ #
    ws2 = wb.create_sheet("Work Log")
    ws2["A1"] = "Work log — one row per working session"
    ws2["A1"].font = TITLE_FONT
    ws2.merge_cells("A1:E1")
    ws2["A2"] = "Add a row every day. This is what shows the effort; Status on sheet 1 is only a flag."
    ws2["A2"].font = Font(italic=True, size=9, color="595959")
    ws2.merge_cells("A2:E2")

    _style_header(ws2, LOG_COLUMNS, row=4)
    for r, row in enumerate(LOG, start=5):
        for i, val in enumerate(row, start=1):
            c = ws2.cell(row=r, column=i, value=val)
            c.alignment = Alignment(vertical="top", wrap_text=True)
            c.border = BORDER
        ws2.row_dimensions[r].height = 56
    ws2.freeze_panes = "A5"

    # ---- Sheet 3: Reference ----------------------------------------------- #
    ws3 = wb.create_sheet("Reference")
    ws3["A1"] = "Reference — split, shared files, open questions"
    ws3["A1"].font = TITLE_FONT
    ws3.merge_cells("A1:B1")
    ws3.column_dimensions["A"].width = 36
    ws3.column_dimensions["B"].width = 104
    for r, (k, v) in enumerate(REFERENCE, start=3):
        a = ws3.cell(row=r, column=1, value=k)
        b = ws3.cell(row=r, column=2, value=v)
        a.alignment = Alignment(vertical="top", wrap_text=True)
        b.alignment = Alignment(vertical="top", wrap_text=True)
        if v == "" and k:                       # section heading
            a.font = Font(bold=True, color="1F3864")
            a.fill = PatternFill("solid", fgColor="D9E2F3")
            ws3.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        else:
            a.font = Font(bold=True)

    wb.save(OUT)
    print(f"wrote {OUT}: {len(TASKS)} tasks, {len(LOG)} log rows")


def open_sheet() -> None:
    """Open the sheet in the default application (Windows/macOS/Linux)."""
    p = Path(OUT).resolve()
    try:
        if sys.platform == "win32":
            os.startfile(p)  # noqa: S606 — intended: hand the file to Excel
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)], check=False)
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
        print(f"opened {p}")
    except OSError as exc:                       # no GUI / no handler registered
        print(f"could not open {p}: {exc}")


if __name__ == "__main__":
    build()
    if "--open" in sys.argv:
        open_sheet()
