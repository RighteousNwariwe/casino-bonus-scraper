#!/usr/bin/env python3
"""Casino bonus scraper.

Pulls welcome bonus data for 3 casinos from one public comparison page
(BetProof) and writes it to site/casinos.json.

Pipeline:
  1. Check robots.txt, then fetch the page once (no retries against blocks).
  2. Parse the comparison table with BeautifulSoup. This part is deterministic.
  3. Send each casino's free-text cells to a language model, which returns
     structured fields through a forced tool call.
  4. Validate the model output against the raw text, then write the JSON.

Run:  python scrape.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://betproof.co.za/casino-bonuses-south-africa/"
USER_AGENT = "casino-bonus-screening-task/1.0 (+https://github.com/RighteousNwariwe)"
TARGET_CASINOS = ["Hollywoodbets", "PlayaBets", "Springbok Casino"]
OUTPUT_PATH = Path(__file__).parent / "site" / "casinos.json"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
REQUEST_TIMEOUT = 20
MAX_LLM_ATTEMPTS = 3


class SourceBlocked(Exception):
    """The source refused us (robots.txt, 403, 429, CAPTCHA). Switch source."""


class ParseError(Exception):
    """The page layout is not what the parser expects."""


# --------------------------------------------------------------------------
# Step 1: fetch politely
# --------------------------------------------------------------------------

def robots_allows(url: str, user_agent: str = USER_AGENT) -> bool:
    """Return True if robots.txt allows this user agent to fetch the url."""
    parts = urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    resp = requests.get(robots_url, headers={"User-Agent": user_agent}, timeout=REQUEST_TIMEOUT)

    parser = RobotFileParser()
    if resp.status_code in (401, 403):
        return False  # same rule Python's own parser uses: treat as "disallow all"
    if resp.status_code >= 500:
        raise SourceBlocked(f"robots.txt returned {resp.status_code}; not guessing, try later")
    if resp.status_code >= 400:
        parser.parse([])  # no robots.txt means no restrictions
    else:
        parser.parse(resp.text.splitlines())
    return parser.can_fetch(user_agent, url)


def fetch_html(url: str) -> str:
    if not robots_allows(url):
        raise SourceBlocked(f"robots.txt disallows fetching {url}")
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (403, 429):
        raise SourceBlocked(f"{url} returned {resp.status_code}")
    resp.raise_for_status()
    if "captcha" in resp.text.lower():
        raise SourceBlocked(f"{url} is serving a CAPTCHA")
    return resp.text


# --------------------------------------------------------------------------
# Step 2: parse the comparison table
# --------------------------------------------------------------------------

def parse_table(html: str, source_url: str = SOURCE_URL) -> list[dict[str, str]]:
    """Return one dict of raw cell text per casino row."""
    soup = BeautifulSoup(html, "html.parser")
    needed = ("casino", "bonus", "wagering", "min deposit")

    for table in soup.find_all("table"):
        first_row = table.find("tr")
        if first_row is None:
            continue
        headers = [c.get_text(" ", strip=True).lower() for c in first_row.find_all(["th", "td"])]
        if all(name in headers for name in needed):
            break
    else:
        raise ParseError("Could not find the comparison table (casino, bonus, wagering, min deposit)")

    col = {name: headers.index(name) for name in needed}
    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td")
        if len(cells) < len(headers):
            continue
        link = cells[col["casino"]].find("a", href=True)
        if link is None:
            continue
        rows.append({
            "name": link.get_text(strip=True),
            "review_url": urljoin(source_url, link["href"]),
            "bonus_text": cells[col["bonus"]].get_text(" ", strip=True),
            "wagering_text": cells[col["wagering"]].get_text(" ", strip=True),
            "min_deposit_text": cells[col["min deposit"]].get_text(" ", strip=True),
        })
    return rows


def pick_targets(rows: list[dict[str, str]], names: list[str]) -> list[dict[str, str]]:
    by_name = {r["name"].lower(): r for r in rows}
    missing = [n for n in names if n.lower() not in by_name]
    if missing:
        raise ParseError(f"Casinos not found in table: {', '.join(missing)}")
    return [by_name[n.lower()] for n in names]


# --------------------------------------------------------------------------
# Step 3: language model turns free text into structured fields
# --------------------------------------------------------------------------

BONUS_TOOL = {
    "name": "record_bonus_terms",
    "description": "Record the structured welcome bonus terms for one casino.",
    "input_schema": {
        "type": "object",
        "properties": {
            "welcome_bonus": {
                "type": "string",
                "description": "Short headline of the welcome bonus, for example '100% up to R3,000'. "
                               "Keep the original currency symbol and amounts.",
            },
            "wagering_multiplier": {
                "type": ["number", "null"],
                "description": "Wagering requirement as a plain number (6 for '6x'). If several "
                               "multipliers are listed, use the one for the first bonus listed. "
                               "Null if the text gives none.",
            },
            "wagering_note": {
                "type": "string",
                "description": "One short sentence saying what the multiplier applies to.",
            },
            "min_deposit_amount": {
                "type": ["number", "null"],
                "description": "Minimum deposit as a number, or null if not stated.",
            },
            "min_deposit_currency": {
                "type": ["string", "null"],
                "description": "ISO 4217 code. 'R' means South African rand (ZAR).",
            },
        },
        "required": [
            "welcome_bonus",
            "wagering_multiplier",
            "wagering_note",
            "min_deposit_amount",
            "min_deposit_currency",
        ],
    },
}

SYSTEM_PROMPT = (
    "You extract casino welcome bonus terms from short pieces of text. "
    "Use only what the text says. Never guess or fill gaps from general knowledge. "
    "Always answer by calling the record_bonus_terms tool."
)


def numbers_in(text: str) -> set[float]:
    """All numbers in a string, with thousands separators removed."""
    cleaned = text.replace(",", "")
    return {float(n) for n in re.findall(r"\d+(?:\.\d+)?", cleaned)}


def validate_extraction(data: dict[str, Any], row: dict[str, str]) -> None:
    """Reject answers whose numbers do not appear in the source text."""
    mult = data.get("wagering_multiplier")
    if mult is not None and float(mult) not in numbers_in(row["wagering_text"]):
        raise ValueError(f"wagering {mult} not found in '{row['wagering_text']}'")

    amount = data.get("min_deposit_amount")
    if amount is not None and float(amount) not in numbers_in(row["min_deposit_text"]):
        raise ValueError(f"min deposit {amount} not found in '{row['min_deposit_text']}'")

    if not str(data.get("welcome_bonus", "")).strip():
        raise ValueError("empty welcome_bonus")


def extract_with_llm(client: Any, row: dict[str, str]) -> dict[str, Any]:
    prompt = (
        f"Casino: {row['name']}\n"
        f"Bonus text: {row['bonus_text']}\n"
        f"Wagering text: {row['wagering_text']}\n"
        f"Minimum deposit text: {row['min_deposit_text']}"
    )
    last_error: Exception | None = None
    for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=500,
                system=SYSTEM_PROMPT,
                tools=[BONUS_TOOL],
                tool_choice={"type": "tool", "name": BONUS_TOOL["name"]},
                messages=[{"role": "user", "content": prompt}],
            )
            block = next(b for b in response.content if b.type == "tool_use")
            data = dict(block.input)
            validate_extraction(data, row)
            return data
        except Exception as exc:  # API hiccup or failed validation: try again
            last_error = exc
            print(f"  attempt {attempt} failed for {row['name']}: {exc}", file=sys.stderr)
            time.sleep(attempt)
    raise RuntimeError(f"Could not extract terms for {row['name']}") from last_error


# --------------------------------------------------------------------------
# Step 4: build and write the output
# --------------------------------------------------------------------------

def build_record(row: dict[str, str], data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": row["name"],
        "welcome_bonus": data["welcome_bonus"].strip(),
        "wagering_multiplier": data["wagering_multiplier"],
        "wagering_note": data["wagering_note"].strip(),
        "min_deposit": {
            "amount": data["min_deposit_amount"],
            "currency": data["min_deposit_currency"],
        },
        "source_url": SOURCE_URL,
        "review_url": row["review_url"],
        "raw": {
            "bonus_text": row["bonus_text"],
            "wagering_text": row["wagering_text"],
            "min_deposit_text": row["min_deposit_text"],
        },
    }


def write_output(records: list[dict[str, Any]], path: Path = OUTPUT_PATH) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE_URL,
        "model": MODEL,
        "casinos": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)  # atomic swap so a crash never leaves half a file


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY first (see README).", file=sys.stderr)
        return 1

    import anthropic  # imported here so tests can run without the SDK installed

    client = anthropic.Anthropic()

    print(f"Fetching {SOURCE_URL}")
    html = fetch_html(SOURCE_URL)
    rows = pick_targets(parse_table(html), TARGET_CASINOS)

    records = []
    for row in rows:
        print(f"Extracting terms for {row['name']}")
        records.append(build_record(row, extract_with_llm(client, row)))

    write_output(records)
    print(f"Wrote {len(records)} casinos to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (SourceBlocked, ParseError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        sys.exit(2)
