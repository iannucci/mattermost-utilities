#!/usr/bin/env python3
"""
Mattermost Boards (Focalboard) API v2 end-to-end demo (tested on 10.9.1)

What it does when run:
  1) GET /plugins/focalboard/api/v2/clientConfig  (sanity/CSRF header path)
  2) Lists Boards "teams" (workspaces)
  3) Lists boards in the first workspace (or uses MM_BOARD_ID if provided)
  4) Lists card blocks on the chosen board
  5) Creates a new card (block) with a unique title
  6) Extracts properties:
       - for that created card (by id)
       - for all cards as {card_id: properties}
       - by searching card by title (exact and substring forms)

Environment:
  export MM_BASE_URL="http://localhost:80"   # or your host; default shown
  export MM_PAT="..."                        # Personal Access Token (required)
  # optional:
  export MM_BOARD_ID="..."                   # to target a specific board

Requires:
  pip install requests
"""

import os
import sys
import time
import uuid
import json
from typing import Any, Dict, List, Optional, Iterable, Tuple

import requests  # has to be installed via pip
import json

file_path = 'config.json'
config_data = {}
try:
	with open(file_path, 'r') as f:
		config_data = json.load(f)
	print("Configuration data loaded successfully:")
except FileNotFoundError:
	print(f"Error: The file '{file_path}' was not found.")
except json.JSONDecodeError:
	print(f"Error: Could not decode JSON from '{file_path}'. Check file format.")
except Exception as e:
	print(f"An unexpected error occurred: {e}")

# --- Configuration via env ---
BASE_URL = config_data['base_url']
PAT = config_data['pat']
DEFAULT_BOARD_ID = "" #config_data['default_board_id']

# --- Common headers for plugin API ---
# Use PAT and legacy "X-Requested-With" header (plugin-side CSRF bypass on 10.x)
HEADERS = {
    "Authorization": f"Bearer {PAT}" if PAT else "",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}

TIMEOUT = 20  # seconds


# --------------------- Utilities ---------------------

def _check_pat():
    if not PAT:
        print("ERROR: environment variable MM_PAT is not set.", file=sys.stderr)
        sys.exit(1)


def _url(path: str) -> str:
    path = path if path.startswith("/") else f"/{path}"
    return f"{BASE_URL}{path}"


def _request(method: str, path: str, json_body: Optional[Any] = None) -> requests.Response:
    # Use single-shot requests (not Session) to avoid cookie persistence.
    return requests.request(
        method=method.upper(),
        url=_url(path),
        headers=HEADERS,
        json=json_body,
        timeout=TIMEOUT,
    )


def _raise_for_status(resp: requests.Response, label: str):
    if 200 <= resp.status_code < 300:
        return
    msg = f"{label} failed: HTTP {resp.status_code}"
    try:
        j = resp.json()
        msg += f" | server says: {json.dumps(j, ensure_ascii=False)}"
    except Exception:
        body = resp.text.strip()
        if body:
            msg += f" | body: {body[:800]}"
    raise RuntimeError(msg)


def _print_json(obj: Any, title: Optional[str] = None, max_chars: int = 0):
    if title:
        print(f"\n== {title} ==")
    s = json.dumps(obj, indent=2, ensure_ascii=False)
    if max_chars and len(s) > max_chars:
        print(s[:max_chars] + "\n... [truncated]")
    else:
        print(s)


# --------------------- Core API (Boards plugin v2) ---------------------

def get_client_config() -> Dict[str, Any]:
    r = _request("GET", "/plugins/focalboard/api/v2/clientConfig")
    _raise_for_status(r, "GET clientConfig")
    return r.json()


def list_boards_teams() -> List[Dict[str, Any]]:
    r = _request("GET", "/plugins/focalboard/api/v2/teams")
    _raise_for_status(r, "GET teams")
    return r.json()


def list_boards(team_id: str) -> List[Dict[str, Any]]:
    r = _request("GET", f"/plugins/focalboard/api/v2/teams/{team_id}/boards")
    _raise_for_status(r, f"GET boards for team {team_id}")
    return r.json()


def list_blocks(board_id: str) -> List[Dict[str, Any]]:
    r = _request("GET", f"/plugins/focalboard/api/v2/boards/{board_id}/blocks")
    _raise_for_status(r, f"GET blocks for board {board_id}")
    return r.json()


def create_card(board_id: str, title: str, properties: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Create a card by POSTing an ARRAY of blocks to /boards/{board_id}/blocks.

    The block shape mirrors what the plugin expects on 10.x:
      - schema: 1
      - id: UUID (lowercase)
      - boardId: <board_id>
      - parentId: <board_id>   (top-level card belongs to board)
      - type: "card"
      - title: <title>
      - fields: { isTemplate, contentOrder, properties }
      - timestamps in milliseconds
      - limited/deleteAt flags
    """
    block_id = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)
    body = [
        {
            "schema": 1,
            "id": block_id,
            "boardId": board_id,
            "parentId": board_id,
            "type": "card",
            "title": title,
            "fields": {
                "isTemplate": False,
                "contentOrder": [],
                "properties": properties or {},
            },
            "createAt": now_ms,
            "updateAt": now_ms,
            "deleteAt": 0,
            "limited": False,
        }
    ]

    r = _request("POST", f"/plugins/focalboard/api/v2/boards/{board_id}/blocks", json_body=body)
    _raise_for_status(r, f"POST create card on board {board_id}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else data


# --------------------- Helpers: cards & properties ---------------------

def list_card_blocks(board_id: str) -> List[Dict[str, Any]]:
    """
    Return all non-deleted card blocks on a board.
    """
    blocks = list_blocks(board_id)
    return [
        b for b in blocks
        if isinstance(b, dict)
        and b.get("type") == "card"
        and int(b.get("deleteAt", 0) or 0) == 0
    ]


def cards_properties_map(board_id: str) -> Dict[str, Dict]:
    """
    Build {card_id: properties_dict} for all cards on the board.
    """
    cards = list_card_blocks(board_id)
    out: Dict[str, Dict] = {}
    for c in cards:
        props = (c.get("fields") or {}).get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        out[c.get("id", "")] = props
    return out


def find_cards_by_title(
    board_id: str,
    name: str,
    *,
    exact: bool = True,
    case_insensitive: bool = True,
) -> List[Dict[str, Any]]:
    """
    Find cards by their visible title. Returns a list of full card blocks.
    - exact=True: title must match exactly (after normalization)
    - exact=False: substring/contains match
    """
    key = name.strip()

    def norm(s: str) -> str:
        s = (s or "").strip()
        return s.lower() if case_insensitive else s

    nk = norm(key)
    cards = list_card_blocks(board_id)
    results: List[Dict[str, Any]] = []
    for c in cards:
        title = c.get("title", "")
        nt = norm(title)
        if exact and nt == nk:
            results.append(c)
        elif not exact and (nk in nt):
            results.append(c)
    return results


def get_card_properties(board_id: str, card_id: str) -> Dict:
    """
    Fetch a single card block by id and return its properties dict.
    """
    r = _request("GET", f"/plugins/focalboard/api/v2/boards/{board_id}/blocks/{card_id}")
    _raise_for_status(r, f"GET block {card_id} on board {board_id}")
    block = r.json()
    props = (block.get("fields") or {}).get("properties") or {}
    return props if isinstance(props, dict) else {}


def get_card_properties_by_title(
    board_id: str,
    name: str,
    *,
    exact: bool = True,
    case_insensitive: bool = True,
    on_ambiguous: str = "error",   # "error" | "first" | "all"
):
    """
    Find by title, then return properties.
    - If multiple matches:
        - on_ambiguous="error": raises RuntimeError
        - on_ambiguous="first": returns the first match's properties
        - on_ambiguous="all": returns a list of (card_id, properties) for all matches
    """
    matches = find_cards_by_title(board_id, name, exact=exact, case_insensitive=case_insensitive)
    if not matches:
        raise RuntimeError(f"No card found with title{'' if exact else ' containing'} '{name}'")

    if len(matches) == 1:
        m = matches[0]
        props = (m.get("fields") or {}).get("properties") or {}
        return props if isinstance(props, dict) else {}

    if on_ambiguous == "first":
        m = matches[0]
        props = (m.get("fields") or {}).get("properties") or {}
        return props if isinstance(props, dict) else {}

    if on_ambiguous == "all":
        out: List[Tuple[str, Dict]] = []
        for m in matches:
            pid = m.get("id", "")
            props = (m.get("fields") or {}).get("properties") or {}
            out.append((pid, props if isinstance(props, dict) else {}))
        return out

    titles = [m.get("title", "") for m in matches]
    raise RuntimeError(f"Multiple cards matched title '{name}': {titles}")


# --------------------- Demo runner ---------------------

def demo():
    _check_pat()
    print(f"Base URL: {BASE_URL}")

    # 1) clientConfig (also validates our CSRF header approach)
    cfg = get_client_config()
    _print_json(cfg, "clientConfig", max_chars=1200)

    # pick or discover a board
    board_id = DEFAULT_BOARD_ID
    team_id = None

    if not board_id:
        teams = list_boards_teams()
        _print_json(teams, "Boards teams/workspaces")
        if not teams:
            print("No Boards teams/workspaces found; create one in the UI first.")
            return
        team_id = teams[0]["id"]
        print(f"Using team/workspace: {team_id}")

        boards = list_boards(team_id)
        _print_json(boards, "Boards in workspace")
        if not boards:
            print("No boards found; create one in the UI or set MM_BOARD_ID.")
            return
        board_id = boards[0]["id"]
        print(f"Using board: {board_id}")

    # 4) list card blocks (preview)
    cards = list_card_blocks(board_id)
    print(f"\nCard count on board {board_id}: {len(cards)}")
    if cards:
        _print_json(cards[:3], "First 3 cards (preview)")

    # 5) create a new card with a unique title
    unique_title = f"Test card from API (Python) @ {time.strftime('%Y-%m-%d %H:%M:%S')}"
    created = create_card(board_id, title=unique_title)
    _print_json(created, "Created card")
    created_id = created.get("id")
    print(f"Created card id: {created_id}")

    # 6a) extract properties by ID
    props_by_id = get_card_properties(board_id, created_id)
    _print_json(props_by_id, "Properties of created card (by id)")

    # 6b) build {card_id: properties} for all cards
    all_props = cards_properties_map(board_id)
    # print just a preview
    preview_map = {k: all_props[k] for k in list(all_props.keys())[:5]}
    _print_json(preview_map, "Properties map preview (first 5)")

    # 6c) find by title (exact)
    props_by_title_exact = get_card_properties_by_title(board_id, unique_title, exact=True)
    _print_json(props_by_title_exact, "Properties (found by exact title)")

    # 6d) find by title (substring)
    hits = get_card_properties_by_title(
        board_id,
        "test card",
        exact=False,
        on_ambiguous="all",
    )
    # hits is a list[(card_id, properties)] — show only the first few
    _print_json(hits[:5], "Substring title search results (first 5)")

    print("\nDone.")


if __name__ == "__main__":
    try:
        demo()
    except Exception as e:
        print(f"\nERROR: {e}\n", file=sys.stderr)
        sys.exit(2)
