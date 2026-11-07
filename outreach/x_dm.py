"""Send X DMs through the paid API, one at a time, from the founder's account.

The founder approves the recipient file and the text before a send. Each send is
logged, so no person gets the same DM twice. Any error from X that is not a
closed inbox stops the run. The tool does not try to get past a limit.

The token is an OAuth 2.0 user token. It expires after about 2 hours. With
X_CLIENT_ID (and X_CLIENT_SECRET for a confidential app) in data/x.env, the
sender renews it with the refresh token and writes the new pair back.
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .config import ROOT
from .messages import first_name, timing
from .x_api import DNC_PATH, ENV_PATH, load_env

API = "https://api.x.com/2"
LOG_PATH = os.path.join(ROOT, "data", "x_dm_log.jsonl")
RECIPIENTS_PATH = os.path.join(ROOT, "data", "x_dm_recipients.csv")


class DMStop(Exception):
    """X refused in a way that must stop the run."""


def dm_text(recipient: dict, cfg: dict, today: Optional[dt.date] = None) -> str:
    """The founder's own DM text. A CMU alum gets the "fellow Tartan" intro."""
    name = (recipient.get("greeting") or "").strip() or first_name(recipient.get("name", ""))
    alum = str(recipient.get("cmu_link", "")).strip().lower() in ("1", "true", "yes")
    intro = "i'm also a fellow tartan" if alum else "i'm a carnegie mellon student"
    text = (f"hey {name}, {intro} building something in gpu financing. i'd love to have a "
            f"quick 15 minute chat to learn more about this space. {timing(cfg, today)[0]}")
    return text.lower()


def load_recipients(path: Optional[str] = None) -> list[dict]:
    with open(path or RECIPIENTS_PATH, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("handle") or "").strip()]


def sent_handles(path: Optional[str] = None) -> set[str]:
    """Handles that already got a DM, or that X said cannot get one."""
    path = path or LOG_PATH
    if not os.path.exists(path):
        return set()
    done = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("status") in ("sent", "dm_closed", "not_found"):
                done.add(row["handle"].lower())
    return done


def dnc_names(path: str = DNC_PATH) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["name"].strip().lower() for r in csv.DictReader(fh) if r.get("name")}


def _log(row: dict, path: Optional[str] = None) -> None:
    path = path or LOG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"), **row}) + "\n")


def _http(method: str, url: str, headers: dict, body: Optional[bytes] = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw or b"{}")
        except ValueError:
            return err.code, {"detail": raw[:300].decode("utf-8", "replace")}


def _save_env(updates: dict, path: str = ENV_PATH) -> None:
    """Replace some X_* lines in the private file. Never print the values."""
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.partition("=")[0] not in updates]
    lines += [f"{k}={v}" for k, v in updates.items()]
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class DMClient:
    def __init__(self, http: Callable = _http, env_path: str = ENV_PATH):
        self.http = http
        self.env_path = env_path
        load_env(env_path)
        self.token = os.environ.get("X_ACCESS_TOKEN", "")
        # The console gave a refresh token in the slot that once held the OAuth 1.0a secret.
        self.refresh_token = (os.environ.get("X_REFRESH_TOKEN")
                              or os.environ.get("X_ACCESS_TOKEN_SECRET", ""))
        self.calls = 0

    def _call(self, method: str, path: str, payload: Optional[dict] = None,
              retry: bool = True) -> tuple[int, dict]:
        headers = {"Authorization": f"Bearer {self.token}"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        self.calls += 1
        status, data = self.http(method, API + path, headers, body)
        if status == 401 and retry and self.refresh():
            return self._call(method, path, payload, retry=False)
        return status, data

    def refresh(self) -> bool:
        client_id = os.environ.get("X_CLIENT_ID", "")
        if not (client_id and self.refresh_token):
            return False
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        secret = os.environ.get("X_CLIENT_SECRET", "")
        if secret:
            pair = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
            headers["Authorization"] = f"Basic {pair}"
        body = urllib.parse.urlencode({"grant_type": "refresh_token",
                                       "refresh_token": self.refresh_token,
                                       "client_id": client_id}).encode()
        status, data = self.http("POST", API + "/oauth2/token", headers, body)
        if status != 200 or not data.get("access_token"):
            return False
        self.token = data["access_token"]
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        _save_env({"X_ACCESS_TOKEN": self.token, "X_REFRESH_TOKEN": self.refresh_token},
                  self.env_path)
        print("Renewed the X token.", flush=True)
        return True

    def user_id(self, handle: str) -> Optional[str]:
        status, data = self._call("GET", f"/users/by/username/{urllib.parse.quote(handle)}")
        if status == 200 and data.get("data"):
            return data["data"]["id"]
        if status in (200, 404):
            return None
        raise DMStop(f"user lookup for @{handle}: HTTP {status} {_detail(data)}")

    def send(self, user_id: str, text: str) -> tuple[int, dict]:
        return self._call("POST", f"/dm_conversations/with/{user_id}/messages", {"text": text})


def _detail(data: dict) -> str:
    errs = data.get("errors") or []
    parts = [data.get("title", ""), data.get("detail", "")] + [
        e.get("message", "") or e.get("detail", "") for e in errs if isinstance(e, dict)]
    return " | ".join(p for p in parts if p)[:300]


def run(recipients: list[dict], cfg: dict, send: bool = False, gap_seconds: int = 300,
        limit: Optional[int] = None, client: Optional[DMClient] = None,
        sleep: Callable[[float], None] = time.sleep, log_path: Optional[str] = None) -> dict:
    """Preview every DM, or send them with a fixed gap. Stop at the first hard error."""
    done = sent_handles(log_path)
    blocked = dnc_names()
    queue = []
    for r in recipients:
        handle = r["handle"].lstrip("@").strip()
        if handle.lower() in done:
            print(f"skip @{handle}: already in the DM log")
            continue
        if (r.get("name") or "").strip().lower() in blocked:
            print(f"skip @{handle}: on the do-not-contact list")
            continue
        queue.append((handle, dm_text(r, cfg)))
    if limit is not None:
        queue = queue[:limit]

    if not send:
        for handle, text in queue:
            print(f"@{handle} ({len(text)} chars)\n  {text}\n")
        print(f"Preview only. {len(queue)} DMs. Add --send to send them.")
        return {"sent": 0, "queued": len(queue)}

    client = client or DMClient()
    sent = closed = 0
    stopped = None
    for i, (handle, text) in enumerate(queue):
        if i:
            sleep(gap_seconds)
        try:
            uid = client.user_id(handle)
            if not uid:
                _log({"handle": handle, "status": "not_found"}, log_path)
                print(f"[{i + 1}/{len(queue)}] @{handle}: no such account, skipped", flush=True)
                continue
            status, data = client.send(uid, text)
        except DMStop as err:
            stopped = str(err)
            break
        if status in (200, 201):
            sent += 1
            _log({"handle": handle, "status": "sent", "text": text,
                  "event": (data.get("data") or {}).get("dm_event_id")}, log_path)
            print(f"[{i + 1}/{len(queue)}] @{handle}: sent", flush=True)
            continue
        detail = _detail(data)
        # A 403 after a success means this one inbox is closed. Before any success,
        # a 403 can mean the token has no DM permission, so stop and report it.
        # This text means the person accepts DMs only from people they follow or
        # from verified accounts. It names the inbox, not the token.
        inbox = "permission to dm" in detail.lower()
        if status == 403 and (inbox or sent or done):
            closed += 1
            _log({"handle": handle, "status": "dm_closed", "detail": detail}, log_path)
            print(f"[{i + 1}/{len(queue)}] @{handle}: X refused (inbox closed?) {detail}",
                  flush=True)
            continue
        _log({"handle": handle, "status": "error", "http": status, "detail": detail}, log_path)
        stopped = f"@{handle}: HTTP {status} {detail}"
        break
    print(f"Sent: {sent}. Closed inboxes: {closed}. API calls: {client.calls}.")
    if stopped:
        print(f"Stopped: {stopped}")
    return {"sent": sent, "closed": closed, "stopped": stopped}
