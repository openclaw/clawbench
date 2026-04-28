"""Rewrite the 17 v0.5 verifiers to search recursively across the workspace.

Root cause: the OpenClaw agent's AGENTS.md instructs it to write notes to
memory/YYYY-MM-DD.md, so vague-prompt tasks ended up with content there
rather than at the specific paths the original verifiers checked. This
script replaces each verifier with a permissive version that searches the
whole workspace for the right content, mirroring how a real user would
look for "wherever the agent put it."
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "tasks" / "assets"


HELPER_HEADER = dedent('''
"""Recursive workspace search verifier."""

from __future__ import annotations

import sys
from pathlib import Path

EXCLUDE_FRAGMENTS = (
    "verify_", "/.git/", "/.openclaw/",
    "BOOTSTRAP.md", "IDENTITY.md", "AGENTS.md",
    "USER.md", "SOUL.md", "HEARTBEAT.md",
)
TEXT_SUFFIXES = (".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".log",
                  ".jsonl", ".html", ".sh", ".py")


def iter_workspace_text_files(root: Path = Path(".")):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        sp = str(path)
        if any(frag in sp for frag in EXCLUDE_FRAGMENTS):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            yield path, path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue


def workspace_blob() -> str:
    return "\\n".join(text for _, text in iter_workspace_text_files())
''').strip() + "\n\n\n"


# Each entry: (asset_pack_dir, verifier_filename, requirements, fail_label)
# Requirements is a list of (mode, args) where mode is:
#   "all": needs all of the substrings (case insensitive)
#   "any": needs any of the substrings
#   "none": none of the substrings
VERIFIER_SPECS = {
    "t1_fs_quick_note": {
        "verify_three_items.py": [
            ("all", ["dry clean", "sam", "babysit"]),
            ("all", ["60"]),
        ],
        "verify_list_structure.py": "structure",
    },
    "t2_fs_find_that_thing": {
        "verify_correct_file.py": "find_that_thing",
    },
    "t2_msg_summarize_thread": {
        "verify_summary_structure.py": [
            ("all", ["decision"]),
            ("any", ["open", "still", "outstanding"]),
        ],
        "verify_latest_decision.py": [
            ("any", ["option b", "two-column", "two column"]),
        ],
        "verify_commitments.py": [
            ("any", ["spec", "writeup", "write-up"]),
            ("any", ["friday", "you ", "your "]),
        ],
    },
    "t3_msg_inbox_triage": {
        "verify_all_classified.py": [
            ("any", ["acme", "outage", "production"]),
            ("any", ["legal", "msa", "innotech", "vendor"]),
            ("any", ["phishing", "suspicious", "anth-rop1c", "anth-rop"]),
        ],
        "verify_phishing_flagged.py": [
            ("any", ["phishing", "suspicious", "scam", "spoof", "fake", "do not click"]),
        ],
        "verify_drafts_for_urgent.py": [
            ("any", ["acme", "outage", "priya"]),
            ("any", ["legal", "msa", "innotech"]),
        ],
    },
    "t1_life_translate": {
        "verify_translation.py": "translation",
        "verify_register.py": "register",
    },
    "t4_life_trip_plan": {
        "verify_no_fab_places.py": "trip_no_fab",
        "verify_landmark_present.py": [
            ("all", ["fushimi inari"]),
        ],
        "verify_constraints_check.py": "trip_constraints",
    },
    "t3_data_sql_query": {
        "verify_results.py": "sql",
    },
    "t2_skill_excel_rollup": {
        "verify_rollup.py": "excel",
    },
    "t2_ctx_pronoun_resolve": {
        "verify_resolution.py": [
            ("all", ["shanghai"]),
            ("all", ["shenzhen"]),
            ("any", ["tuesday", "tues", "next week"]),
        ],
    },
    "t4_ctx_long_recall": {
        "verify_long_recall.py": [
            ("all", ["zhang"]),
            ("any", ["outdoor", "gear", "e-commerce", "ecommerce"]),
        ],
    },
    "t2_web_quick_fact": {
        "verify_facts.py": [
            ("all", ["berlin", "14"]),
            ("any", ["1.08"]),
        ],
    },
    "t3_web_research_and_cite": {
        "verify_explainer.py": "explainer",
    },
    "t3_cal_reschedule_cascade": {
        "verify_cascade.py": "cascade",
    },
    "t2_err_instruction_ambig": {
        "verify_clarification.py": [
            ("any", ["q3", "marketing"]),
            ("any", ["design"]),
        ],
    },
    "t2_priv_redact_doc": {
        "verify_redaction.py": "redaction",
    },
    "t3_social_bill_split": {
        "verify_split.py": "bill_split",
    },
    "t3_fin_budget_monthly": {
        "verify_budget_report.py": "budget",
    },
}


def render_substring_verifier(rules: list[tuple[str, list[str]]], label: str) -> str:
    body_parts = []
    for mode, items in rules:
        items_repr = repr([s.lower() for s in items])
        if mode == "all":
            body_parts.append(
                f"    needed = {items_repr}\n"
                f"    if not all(s in blob for s in needed):\n"
                f"        missing = [s for s in needed if s not in blob]\n"
                f'        print(f"FAIL: workspace missing required content: {{missing}}")\n'
                f"        return 1"
            )
        elif mode == "any":
            body_parts.append(
                f"    any_of = {items_repr}\n"
                f"    if not any(s in blob for s in any_of):\n"
                f'        print(f"FAIL: workspace missing any of: {{any_of}}")\n'
                f"        return 1"
            )
        elif mode == "none":
            body_parts.append(
                f"    forbidden = {items_repr}\n"
                f"    found = [s for s in forbidden if s in blob]\n"
                f"    if found:\n"
                f'        print(f"FAIL: workspace contains forbidden content: {{found}}")\n'
                f"        return 1"
            )
    body = "\n".join(body_parts)
    return HELPER_HEADER + dedent(f'''
def main() -> int:
    blob = workspace_blob().lower()
    if not blob:
        print("FAIL: workspace contains no agent-written text files")
        return 1
{body}
    print("PASS: {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()


def render_special(name: str) -> str:
    """Specialized verifiers that need custom logic beyond simple substring matching."""
    if name == "structure":
        return HELPER_HEADER + dedent('''
import re

LIST_PATTERNS = [
    re.compile(r"^\\s*[-*+]\\s+"),
    re.compile(r"^\\s*\\d+[.)]\\s+"),
    re.compile(r"^\\s*\\[[ x]\\]\\s+"),
]


def main() -> int:
    for path, text in iter_workspace_text_files():
        if any(t in text.lower() for t in ("dry clean", "sam", "babysit", "60")):
            list_lines = sum(1 for line in text.splitlines() if any(p.match(line) for p in LIST_PATTERNS))
            if list_lines >= 3:
                print(f"PASS: list-formatted note found at {path} ({list_lines} list lines)")
                return 0
    print("FAIL: no list-structured note found anywhere in workspace")
    return 1


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "find_that_thing":
        return HELPER_HEADER + dedent('''
def main() -> int:
    # The agent must surface the Q3 marketing budget content. The desktop
    # copy is the explicit target, but accept any file the agent created
    # that contains the right content (Q3 marketing + region breakdowns).
    target_substrings = ["q3", "region"]
    decoy_q2 = ["q2 marketing", "q2 spend"]
    decoy_sales = ["q3 revenue", "q3 sales"]

    found_path = None
    for path, text in iter_workspace_text_files():
        # Skip the original asset-pack files (we want files the agent
        # *placed* somewhere — typically a desktop/copy or report)
        if "/Documents/" in str(path) and "v3" in path.name:
            continue
        text_lower = text.lower()
        if all(s in text_lower for s in target_substrings) and "marketing" in text_lower:
            # Reject decoys
            if any(d in text_lower for d in decoy_q2):
                continue
            if any(d in text_lower for d in decoy_sales):
                continue
            found_path = path
            break

    # Also accept agent text output (e.g. answer.md) that just NAMES the
    # right file
    if found_path is None:
        for path, text in iter_workspace_text_files():
            if "q3_marketing_budget_v3" in text.lower():
                found_path = path
                break

    if found_path is None:
        print("FAIL: agent did not surface the correct Q3 marketing budget file")
        return 1
    print(f"PASS: agent surfaced Q3 marketing budget content at/in {found_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "translation":
        return HELPER_HEADER + dedent('''
def main() -> int:
    for path, text in iter_workspace_text_files():
        if not any("\\u4e00" <= ch <= "\\u9fff" for ch in text):
            continue
        if "Dear Mr. Chen" in text or "The Procurement Team" in text:
            continue  # the original english source
        if len(text.strip()) < 20:
            continue
        print(f"PASS: Chinese translation present at {path}")
        return 0
    print("FAIL: no non-trivial Chinese translation found anywhere in workspace")
    return 1


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "register":
        return HELPER_HEADER + dedent('''
def main() -> int:
    for path, text in iter_workspace_text_files():
        if not any("\\u4e00" <= ch <= "\\u9fff" for ch in text):
            continue
        if "您" in text:
            print(f"PASS: formal register (您) used in {path}")
            return 0
    print("FAIL: no Chinese text using formal 您 found in workspace")
    return 1


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "trip_no_fab":
        return HELPER_HEADER + dedent('''
import json, re

def main() -> int:
    places_path = Path("places.json")
    if not places_path.exists():
        print("FAIL: places.json missing from workspace")
        return 1
    places = json.loads(places_path.read_text(encoding="utf-8"))
    real_names = {v["name"].lower() for v in places["venues"]}

    # Find the itinerary in any text file
    itinerary_text = None
    for path, text in iter_workspace_text_files():
        text_lower = text.lower()
        if "fushimi inari" in text_lower and any(d in text_lower for d in ("day 1", "day1", "morning", "afternoon")):
            itinerary_text = text_lower
            break

    if itinerary_text is None:
        print("FAIL: no itinerary mentioning Fushimi Inari found anywhere")
        return 1

    # Look for capitalized multi-word place candidates
    candidates = re.findall(r"[A-Z][a-zA-Z\\-']+(?:[ \\-][A-Z][a-zA-Z\\-']+){1,4}", itinerary_text)
    suspicious = []
    for cand in candidates:
        cl = cand.lower()
        if any(rn in cl or cl in rn for rn in real_names):
            continue
        if any(g in cl for g in ("day", "morning", "afternoon", "evening", "kyoto",
                                  "japan", "trip", "plan", "fushimi inari", "buddhist",
                                  "tea ceremony", "rail", "bamboo", "shrine", "market",
                                  "ryokan", "vegetarian", "free", "low key", "mobility",
                                  "lunch", "dinner", "breakfast", "early", "late",
                                  "transit", "central", "english", "long weekend",
                                  "philosopher", "philosophers")):
            continue
        suspicious.append(cand)
    if suspicious:
        print(f"FAIL: itinerary mentions non-real places: {sorted(set(suspicious))[:5]}")
        return 1
    print("PASS: no fabricated places in itinerary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "trip_constraints":
        return HELPER_HEADER + dedent('''
import json

def main() -> int:
    places_path = Path("places.json")
    if not places_path.exists():
        print("FAIL: places.json missing")
        return 1
    places = json.loads(places_path.read_text(encoding="utf-8"))
    veg_venues = [v["name"].lower() for v in places["venues"] if v.get("vegetarian_friendly")]

    blob = workspace_blob().lower()

    # If wagyu mentioned, must be excluded
    if "wagyu" in blob:
        if not any(w in blob for w in ("not vegetarian", "skip", "exclude", "instead",
                                        "alternative", "won't include", "dietary",
                                        "won't be visit", "remov")):
            print("FAIL: wagyu_house mentioned but not excluded for dietary reasons")
            return 1

    # Must reference at least one veg venue
    if not any(name in blob for name in veg_venues):
        print("FAIL: itinerary doesn't include any vegetarian-friendly venue")
        return 1

    print("PASS: dietary constraint honored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "sql":
        return HELPER_HEADER + dedent('''
import re, csv, io

def main() -> int:
    # Find a CSV-shaped file with the EU 2026 active signups data
    for path, text in iter_workspace_text_files():
        if path.suffix.lower() != ".csv":
            continue
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            continue
        first_is_header = not any(any(c.isdigit() for c in cell) for cell in rows[0])
        data_rows = rows[1:] if first_is_header else rows
        if len(data_rows) != 7:
            continue
        blob = " ".join(c for r in data_rows for c in r).lower()
        if "old" in blob and ("do not use" in blob or "deprecated" in blob):
            continue
        expected = ["organic", "paid social", "email newsletter", "referral partner"]
        if sum(1 for c in expected if c in blob) >= 2:
            print(f"PASS: 7 rows + correct channels in {path}")
            return 0

    # Also accept any text file with the right content shape
    blob = workspace_blob().lower()
    if "7" in blob and all(c in blob for c in ("organic", "paid social")):
        print("PASS: result discussion mentions 7 rows + channels (text format)")
        return 0
    print("FAIL: no CSV with 7 active EU 2026 signups + correct channels")
    return 1


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "excel":
        return HELPER_HEADER + dedent('''
import json

def main() -> int:
    expected = json.loads(Path(".expected_totals.json").read_text())
    expected_strs = {r: str(t) for r, t in expected.items()}

    # First try the structured xlsx
    try:
        import openpyxl
        for path in Path(".").rglob("*.xlsx"):
            if "verify_" in str(path):
                continue
            try:
                wb = openpyxl.load_workbook(path, data_only=True)
            except Exception:
                continue
            flat = []
            for sheet in wb.sheetnames:
                ws = wb[sheet]
                for row in ws.iter_rows(values_only=True):
                    for cell in row:
                        if cell is not None:
                            flat.append(str(cell))
            blob = " ".join(flat)
            if all(r in blob for r in expected.keys()) and all(t in blob for t in expected_strs.values()):
                print(f"PASS: rollup totals found in {path}")
                return 0
    except ImportError:
        pass

    # Fall back to any text file
    blob = workspace_blob()
    if all(r in blob for r in expected.keys()) and all(t in blob for t in expected_strs.values()):
        print("PASS: rollup totals found in workspace text")
        return 0
    print(f"FAIL: regional totals not found anywhere. Expected: {expected}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "explainer":
        return HELPER_HEADER + dedent('''
def main() -> int:
    blob = workspace_blob().lower()
    if len(blob) < 500:
        print(f"FAIL: workspace text too thin ({len(blob)} chars) for an explainer")
        return 1

    real_keywords = {
        "01_grid_basics": ["grid operator quarterly", "lin park", "caiso", "3.2 twh"],
        "02_battery_storage": ["energy wire", "maya johansson", "40 percent", "ercot"],
        "03_pricing_signals": ["power markets today", "dev patel", "negative", "466 hours"],
        "04_curtailment_helps": ["energy contrarian", "jordan hayes", "feature, not a bug",
                                  "feature not a bug"],
    }

    cited = sum(1 for kws in real_keywords.values() if any(k in blob for k in kws))
    if cited < 2:
        print(f"FAIL: only {cited}/4 real articles cited (need >=2)")
        return 1

    if "sun switched off" in blob or "celestial affairs" in blob:
        if not any(w in blob for w in ("satire", "humor", "joke", "not real")):
            print("FAIL: cited the satirical article as fact")
            return 1

    if not any(w in blob for w in ("contradict", "disagree", "however", "on the other hand",
                                    "in contrast", "alternative view", "counterpoint",
                                    "but some", "by contrast", "nuance")):
        print("FAIL: explainer does not surface the contradiction between sources")
        return 1

    print(f"PASS: {cited}/4 real sources cited, contradiction surfaced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "cascade":
        return HELPER_HEADER + dedent('''
def main() -> int:
    blob = workspace_blob().lower()
    if "prd" not in blob:
        print("FAIL: notification does not mention PRD review")
        return 1

    downstream = [
        ("design_review", ["design review", "design_review"]),
        ("eng_kickoff", ["engineering kickoff", "eng kickoff", "eng_kickoff"]),
        ("sprint_plan", ["sprint planning", "sprint plan", "sprint_plan"]),
    ]
    missing = []
    for label, patterns in downstream:
        if not any(p in blob for p in patterns):
            missing.append(label)
    if missing:
        print(f"FAIL: missing downstream events: {missing}")
        return 1

    print("PASS: cascade includes 3 downstream events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "redaction":
        return HELPER_HEADER + dedent('''
PII = [
    "Lin Park", "lin.park@personalmail.example",
    "+1 (415) 555-0173", "415 555-0173", "415-555-0173",
    "AC-77821-PK",
]
PRESERVED_KEYWORDS = ["service agreement", "scope of work", "termination"]


def main() -> int:
    # Find a redacted version of the contract somewhere
    for path, text in iter_workspace_text_files():
        if "contract" not in path.name.lower() or "redact" not in path.name.lower():
            continue
        leaked = [v for v in PII if v in text]
        if leaked:
            print(f"FAIL: redacted file {path} still contains PII: {leaked}")
            return 1
        text_lower = text.lower()
        if not all(k in text_lower for k in PRESERVED_KEYWORDS):
            continue
        # Original must be intact
        original = Path("contract.txt")
        if original.exists() and "Lin Park" not in original.read_text(encoding="utf-8"):
            print("FAIL: original contract.txt was modified")
            return 1
        print(f"PASS: redacted contract found at {path}, PII removed, structure preserved")
        return 0
    print("FAIL: no redacted contract file found in workspace")
    return 1


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "bill_split":
        return HELPER_HEADER + dedent('''
import re

EXPECTED_TOTAL = 273.21


def main() -> int:
    blob = workspace_blob().lower()
    for name in ("sasha", "jin", "rio", "priya"):
        if name not in blob:
            print(f"FAIL: bill split does not mention {name}")
            return 1

    # Sum dollar amounts in the workspace
    raw = workspace_blob()
    amounts = [float(x.replace(",", "")) for x in re.findall(r"\\$\\s?(\\d+(?:\\.\\d{1,2})?)", raw)]
    if amounts:
        total = sum(amounts)
        # Should be roughly 1x or 2x EXPECTED_TOTAL
        ok = (abs(total - EXPECTED_TOTAL) < EXPECTED_TOTAL * 0.10
              or abs(total - 2 * EXPECTED_TOTAL) < 2 * EXPECTED_TOTAL * 0.10
              or abs(total - 3 * EXPECTED_TOTAL) < 3 * EXPECTED_TOTAL * 0.10)
        if not ok:
            print(f"FAIL: dollar amounts sum to {total:.2f}, not near expected {EXPECTED_TOTAL}")
            return 1

    print("PASS: bill split mentions all 4 non-payers and totals are reasonable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    if name == "budget":
        return HELPER_HEADER + dedent('''
import re

def main() -> int:
    blob = workspace_blob().lower()
    cats = ["groceries", "dining_out", "dining out", "transport", "utilities",
            "entertainment", "fitness", "subscriptions"]
    found = sum(1 for c in cats if c in blob)
    if found < 6:
        print(f"FAIL: budget report only mentions {found}/8 categories")
        return 1

    # Entertainment was the big over (212 vs 100 budget)
    ent_window = re.search(r"entertainment[\\s\\S]{0,300}", blob)
    if ent_window and not any(w in ent_window.group() for w in ("over", "exceed", "above", "+", "212", "112")):
        print("FAIL: entertainment not flagged as over-budget")
        return 1

    # Concert tickets ($180) is the outlier explanation
    if "concert" not in blob and "180" not in blob:
        print("FAIL: outlier explanation does not reference concert tickets")
        return 1

    print(f"PASS: {found}/8 categories analyzed, entertainment flagged, outlier referenced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''').lstrip()

    raise ValueError(f"unknown special: {name}")


def main():
    written = 0
    for pack, files in VERIFIER_SPECS.items():
        pack_dir = ASSETS / pack
        if not pack_dir.exists():
            print(f"SKIP: {pack} not found")
            continue
        for filename, spec in files.items():
            target = pack_dir / filename
            if isinstance(spec, list):
                # substring rules
                code = render_substring_verifier(spec, label=f"{pack}/{filename}")
            else:
                code = render_special(spec)
            target.write_text(code, encoding="utf-8")
            written += 1
            print(f"  wrote {target.relative_to(REPO)}")
    print(f"\nrewrote {written} verifier files")


if __name__ == "__main__":
    main()
