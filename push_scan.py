#!/usr/bin/env python3
"""
push_scan.py — MeritQuant Scan Bridge
Reads the latest alpha_terminal_scan.json written by Cowork
and pushes it to the meritquant GitHub repo so GitHub Actions
trader.py can read it from data/alpha_terminal_scan.json.

Usage (called automatically after each Cowork scan):
    python3 push_scan.py /path/to/alpha_terminal_scan.json

Requirements:
    GITHUB_PAT environment variable must be set.
    pip install requests
"""

import os
import sys
import json
import base64
import requests
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.environ.get("GITHUB_PAT", "")
REPO         = "Shrivathsav123/meritquant"
REPO_PATH    = "data/alpha_terminal_scan.json"
API_URL      = f"https://api.github.com/repos/{REPO}/contents/{REPO_PATH}"
HEADERS      = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept":        "application/vnd.github.v3+json",
    "User-Agent":    "MeritQuant-ScanBridge/1.0",
}

# ── Fallback scan paths (checked in order if no arg passed) ──────────────────
FALLBACK_PATHS = [
    os.path.expanduser("~/Desktop/alpha_terminal_scan.json"),
    # Cowork outputs folder (session-independent via symlink if set up)
    os.path.expanduser("~/alpha_terminal_scan.json"),
]


def find_scan_file(arg_path: str = None) -> str:
    """Return path to scan JSON, checking arg → fallbacks."""
    if arg_path and os.path.exists(arg_path):
        return arg_path
    for p in FALLBACK_PATHS:
        if os.path.exists(p):
            return p
    return None


def get_current_sha() -> str:
    """Get current file SHA from GitHub (needed to update existing file)."""
    r = requests.get(API_URL, headers=HEADERS, timeout=10)
    if r.status_code == 200:
        return r.json().get("sha", "")
    return ""  # File doesn't exist yet — first push


def push_scan(scan_path: str) -> bool:
    """Read scan JSON and commit it to GitHub repo."""
    if not GITHUB_TOKEN:
        print("[push_scan] ERROR: GITHUB_PAT environment variable not set.")
        print("  Run: export GITHUB_PAT=your_token_here")
        return False

    # Read and validate the scan file
    try:
        with open(scan_path, "r") as f:
            scan_data = json.load(f)
    except Exception as e:
        print(f"[push_scan] ERROR reading {scan_path}: {e}")
        return False

    # Check scan freshness
    scan_time_str = scan_data.get("scan_time", "")
    if scan_time_str:
        try:
            scan_time = datetime.fromisoformat(scan_time_str.replace("Z", "+00:00"))
            age_mins  = (datetime.now(timezone.utc) - scan_time).total_seconds() / 60
            if age_mins > 30:
                print(f"[push_scan] WARNING: Scan is {age_mins:.0f} minutes old — pushing anyway.")
            else:
                print(f"[push_scan] Scan age: {age_mins:.1f} minutes — fresh.")
        except Exception:
            pass

    # Encode content for GitHub API
    content_bytes   = json.dumps(scan_data, indent=2).encode("utf-8")
    content_b64     = base64.b64encode(content_bytes).decode("utf-8")
    now_str         = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    scan_type       = scan_data.get("scan_type", "SCAN")
    commit_message  = f"scan: {scan_type} {now_str}"

    # Get current SHA (required if file already exists)
    sha = get_current_sha()

    payload = {
        "message": commit_message,
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha  # Required to update an existing file

    # Push to GitHub
    r = requests.put(API_URL, headers=HEADERS, json=payload, timeout=15)

    if r.status_code in (200, 201):
        action = "Updated" if r.status_code == 200 else "Created"
        print(f"[push_scan] ✅ {action} data/alpha_terminal_scan.json in {REPO}")
        print(f"  Scan: {scan_type} | Regime: {scan_data.get('macro', {}).get('regime', '?')}")
        setups = scan_data.get("setups", [])
        if setups:
            print(f"  Top setup: {setups[0]['symbol']} {setups[0]['direction']} "
                  f"| Entry ${setups[0]['entry_price']} | Gates {setups[0]['gates_cleared']}/9")
        return True
    else:
        print(f"[push_scan] ERROR: GitHub API returned {r.status_code}")
        print(f"  Response: {r.text[:300]}")
        return False


if __name__ == "__main__":
    scan_path = sys.argv[1] if len(sys.argv) > 1 else None
    found     = find_scan_file(scan_path)

    if not found:
        print("[push_scan] ERROR: No scan file found.")
        print("  Checked:")
        if scan_path:
            print(f"    {scan_path}")
        for p in FALLBACK_PATHS:
            print(f"    {p}")
        sys.exit(1)

    print(f"[push_scan] Reading scan from: {found}")
    success = push_scan(found)
    sys.exit(0 if success else 1)
