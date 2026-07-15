"""
GLPI Ticket -> QA Artifacts Agent
Takes a ticket (from GLPI or from a local file) and generates:
user_story, acceptance_criteria, test_cases, regression_tests (as JSON).

Usage:
    python dev-agent.py <ticket_id>          # fetch from GLPI
    python dev-agent.py <ticket_id> --push   # fetch from GLPI + add result as followup
    python dev-agent.py --file ticket.json   # read from a local file
    python dev-agent.py --file ticket.txt

File formats accepted with --file:
  .json : {"title": "...", "description": "...", "followups": ["...", ...]}
          (only "description" is required; a bare {"content": "..."} also works)
  .txt/.md : plain text; first line = title, rest = description

Environment variables (in .env or exported):
    MISTRAL_API_KEY     always required
    GLPI_URL            required only for GLPI mode (must end in /apirest.php)
    GLPI_APP_TOKEN      required only for GLPI mode
    GLPI_USER_TOKEN     required only for GLPI mode
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
# from anthropic import Anthropic
from openai import OpenAI

load_dotenv()


# ---------------------------------------------------------------------------
# 1. GLPI REST API helpers (Legacy REST API, apirest.php)
# ---------------------------------------------------------------------------

def glpi_config() -> tuple[str, str, str]:
    """Read GLPI env vars, with a clear error if one is missing."""
    try:
        url = os.environ["GLPI_URL"].rstrip("/")
        app = os.environ["GLPI_APP_TOKEN"]
        user = os.environ["GLPI_USER_TOKEN"]
    except KeyError as e:
        sys.exit(f"Missing environment variable {e} (required for GLPI mode).")
    if "apirest.php" not in url:
        sys.exit(
            f"GLPI_URL should point to the Legacy REST API (.../apirest.php), got: {url}"
        )
    return url, app, user


def glpi_init_session(url: str, app_token: str, user_token: str) -> str:
    resp = requests.get(
        f"{url}/initSession",
        headers={
            "App-Token": app_token,
            "Authorization": f"user_token {user_token}",
        },
        timeout=30,
    )
    try:
        data = resp.json()
    except requests.exceptions.JSONDecodeError:
        sys.exit(
            f"GLPI did not return JSON (status {resp.status_code}).\n"
            f"URL called: {resp.url}\n"
            f"First 300 chars of response:\n{resp.text[:300]}"
        )
    if "session_token" not in data:
        sys.exit(f"GLPI error: {data}")
    return data["session_token"]


def glpi_kill_session(url: str, app_token: str, session_token: str) -> None:
    requests.get(
        f"{url}/killSession",
        headers={"App-Token": app_token, "Session-Token": session_token},
        timeout=30,
    )


def strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def fetch_ticket_from_glpi(ticket_id: int) -> dict:
    url, app_token, user_token = glpi_config()
    session = glpi_init_session(url, app_token, user_token)
    headers = {"App-Token": app_token, "Session-Token": session}
    try:
        resp = requests.get(f"{url}/Ticket/{ticket_id}", headers=headers, timeout=30)
        resp.raise_for_status()
        ticket = resp.json()

        followups = []
        fu_resp = requests.get(
            f"{url}/Ticket/{ticket_id}/ITILFollowup", headers=headers, timeout=30
        )
        if fu_resp.ok:
            followups = [strip_html(f.get("content", "")) for f in fu_resp.json()]
    finally:
        glpi_kill_session(url, app_token, session)

    return {
        "id": ticket.get("id"),
        "title": ticket.get("name", ""),
        "description": strip_html(ticket.get("content", "")),
        "followups": followups,
    }


def post_followup_to_glpi(ticket_id: int, artifacts: dict) -> None:
    """Add the generated artifacts as a followup on the GLPI ticket."""
    url, app_token, user_token = glpi_config()
    session = glpi_init_session(url, app_token, user_token)
    try:
        def ul(items):
            return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"

        content = (
            "<p><b>🤖 Generated QA artifacts</b></p>"
            f"<p><b>User story:</b> {artifacts['user_story']}</p>"
            f"<p><b>Acceptance criteria:</b></p>{ul(artifacts['acceptance_criteria'])}"
            f"<p><b>Test cases:</b></p>{ul(artifacts['test_cases'])}"
            f"<p><b>Regression tests:</b></p>{ul(artifacts['regression_tests'])}"
        )
        resp = requests.post(
            f"{url}/Ticket/{ticket_id}/ITILFollowup",
            headers={
                "App-Token": app_token,
                "Session-Token": session,
                "Content-Type": "application/json",
            },
            json={"input": {
                "itemtype": "Ticket",
                "items_id": ticket_id,
                "content": content,
            }},
            timeout=30,
        )
        if not resp.ok:
            sys.exit(f"Failed to add followup ({resp.status_code}): {resp.text[:300]}")
        print(f"Followup added to ticket #{ticket_id} \u2714", file=sys.stderr)
    finally:
        glpi_kill_session(url, app_token, session)


# ---------------------------------------------------------------------------
# 2. Local file input
# ---------------------------------------------------------------------------

def load_ticket_from_file(path_str: str) -> dict:
    path = Path(path_str)
    if not path.exists():
        sys.exit(f"File not found: {path}")

    text = path.read_text(encoding="utf-8")

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            sys.exit(f"Invalid JSON in {path}: {e}")
        return {
            "id": data.get("id") or data.get("ticket_id") or path.stem,
            "title": data.get("title") or data.get("name", path.stem),
            "description": strip_html(
                data.get("description") or data.get("content", "")
            ),
            "followups": [strip_html(f) for f in data.get("followups", [])],
        }

    # Plain text / markdown: first non-empty line = title, rest = description
    lines = text.strip().splitlines()
    if not lines:
        sys.exit(f"File is empty: {path}")
    return {
        "id": path.stem,
        "title": lines[0].strip().lstrip("# "),
        "description": "\n".join(lines[1:]).strip(),
        "followups": [],
    }


# ---------------------------------------------------------------------------
# 3. LLM: turn the ticket into QA artifacts
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """You are a QA/Business analyst. Based on the ticket below,
produce a JSON object with EXACTLY this structure:

{{
  "user_story": "As a <role>, I want <feature>, so that <benefit>",
  "acceptance_criteria": ["...", "..."],
  "test_cases": ["...", "..."],
  "regression_tests": ["...", "..."]
}}

Rules:
- Respond with ONLY the JSON object. No preamble, no markdown fences, no comments.
- acceptance_criteria: concrete, verifiable conditions (Given/When/Then style welcome).
- test_cases: step-by-step scenarios that validate the new behavior.
- regression_tests: existing behaviors to re-check that this change could break.
- Write in the same language as the ticket.

TICKET
------
Title: {title}
Description:
{description}

Followups:
{followups}
"""


# def generate_artifacts(ticket: dict) -> dict:
#     prompt = PROMPT_TEMPLATE.format(
#         title=ticket["title"],
#         description=ticket["description"],
#         followups="\n---\n".join(ticket["followups"]) or "(none)",
#     )
#     client = Anthropic()  # reads ANTHROPIC_API_KEY automatically
#     resp = client.messages.create(
#         model="claude-sonnet-4-6",
#         max_tokens=2000,
#         system="You output strictly valid JSON and nothing else.",
#         messages=[{"role": "user", "content": prompt}],
#     )
#     raw = resp.content[0].text
#     raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
#     return json.loads(raw)

def generate_artifacts(ticket: dict) -> dict:
    prompt = PROMPT_TEMPLATE.format(
        title=ticket["title"],
        description=ticket["description"],
        followups="\n---\n".join(ticket["followups"]) or "(none)",
    )
    # client = OpenAI(
    #     api_key=os.environ["XAI_API_KEY"],
    #     base_url="https://api.x.ai/v1",
    # )
    client = OpenAI(
        api_key=os.environ["MISTRAL_API_KEY"],
        base_url="https://api.mistral.ai/v1",
    )
    resp = client.chat.completions.create(
        # model="gemini-2.5-flash",
        model="mistral-small-latest",
        messages=[
            {"role": "system", "content": "You output strictly valid JSON and nothing else."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=2000,
    )
    raw = resp.choices[0].message.content
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# 4. Main
# ---------------------------------------------------------------------------

USAGE = (
    "Usage:\n"
    "  python dev-agent.py <ticket_id>          # fetch from GLPI\n"
    "  python dev-agent.py <ticket_id> --push   # fetch from GLPI + add followup\n"
    "  python dev-agent.py --file <path>        # read ticket from a local file"
)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit(USAGE)

    push = "--push" in args
    args = [a for a in args if a != "--push"]
    if not args:
        sys.exit(USAGE)

    if args[0] in ("--file", "-f"):
        if len(args) < 2:
            sys.exit(USAGE)
        ticket = load_ticket_from_file(args[1])
        source = f"file {args[1]}"
    else:
        try:
            ticket_id = int(args[0])
        except ValueError:
            sys.exit(USAGE)
        ticket = fetch_ticket_from_glpi(ticket_id)
        source = "GLPI"

    print(f"Loaded ticket #{ticket['id']} from {source}: {ticket['title']}\n",
          file=sys.stderr)

    artifacts = generate_artifacts(ticket)
    print(json.dumps(artifacts, indent=2, ensure_ascii=False))

    if push:
        if not str(ticket["id"]).isdigit():
            sys.exit("--push requires a real GLPI ticket (numeric ID), not a file-only ticket.")
        post_followup_to_glpi(int(ticket["id"]), artifacts)


if __name__ == "__main__":
    main()