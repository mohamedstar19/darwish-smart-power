#!/usr/bin/env python3
"""Darwish Smart Power - self-hosted controller for the MTTL-W01 4-outlet Wi-Fi power strip.

    python3 smartpower.py                       run the server (same as `serve`)
    python3 smartpower.py serve --token SECRET  require a token for the web UI / API
    python3 smartpower.py provision --server-ip 192.168.1.20 --ssid HomeWiFi --wifi-password PASS
    python3 smartpower.py selftest              offline check against a simulated strip

The strip opens a TCP connection to the server it was provisioned with (port 10086)
and talks in plain text lines. This server keeps that connection, remembers the
live state, and offers a web page plus a JSON API for the Android app.
Python 3.8+ standard library only.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
import re
import shutil
import socket
import struct
import sqlite3
import sys
import tempfile
import threading
import time
import io
import zipfile
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from html import escape as html_escape
from urllib.parse import parse_qs, quote, unquote, urlsplit

VERSION = "2.0.0"
STRIP_PORT = 10086                       # fixed in the strip firmware
SETUP_ADDR = ("192.168.1.1", 30300)      # the strip's own access point while in setup mode
OUTLETS = (1, 2, 3, 4)
POLL_EVERY = 5.0                         # seconds between state reads
DIAG_EVERY = 30.0                        # seconds between voltage / Wi-Fi signal reads
MAX_MISSED_POLLS = 3                     # unanswered reads before the connection is dropped
TIMER_GRACE = 600                        # drop a timer that could not run this long after it was due
MAX_BODY = 16 * 1024
OFFLINE_ALERT_AFTER = 90                 # seconds offline before an "offline" alert
POWER_BACK_KEEP = 12 * 3600              # how long "turn back on what was on" stays offered after a power cut
MAX_AFTER_POWER = 120                    # minutes an outlet may wait before turning back on after a power cut
ALERT_REPEAT = {"temp": 3600, "power": 1800, "trip": 1800}
TRIP_OVERLOAD, TRIP_OVERHEAT = 1, 2      # the strip's own protection cut an outlet off
MAX_EVENTS = 500
QUEUE_TTL = 24 * 3600                    # commands for an offline strip are kept this long
PIN_MAX_TRIES = 5                        # wrong PINs before a strip refuses PINs for a minute
# Protection for the strip port, which is open to the internet: anything can connect and claim to
# be a strip. Limits are per internet address and generous, because many homes share one address
# (carrier NAT). Home-network and loopback addresses are never limited or blocked.
HELLO_TIMEOUT = 30                       # seconds a new connection gets to introduce itself as a strip
PROBE_AFTER = 5                          # seconds of silence before the server speaks first
MAX_LINE = 4096                          # longest line accepted from a strip
# no limit on how many strips a customer or a home has: these only stop floods of connections
MAX_CONNECTIONS_PER_IP = 1000            # open strip connections from one address at the same time
MAX_CONNECTS_PER_MINUTE = 1000           # new strip connections per minute (all of them reconnect after a restart)
MAX_UNKNOWN_STRIPS = 500                 # strips nobody approved or announced, waiting at once, before more are refused
MAX_STRIKES = 3                          # misbehaving connections (10 minutes) before a block
BLOCK_SECONDS = 3600
EXPECT_SECONDS = 1800                    # a strip announced by the app is approved if it connects this soon
RESERVE_DAYS = 60                        # a strip the owner reserved for a customer (e.g. set up before shipping)
# Voice assistants link a customer's account with OAuth 2 (authorization code grant)
OAUTH_CLIENTS = ("alexa", "google")
OAUTH_REDIRECT_HOSTS = ("pitangui.amazon.com", "layla.amazon.com", "alexa.amazon.co.jp",
                        "oauth-redirect.googleusercontent.com", "oauth-redirect-sandbox.googleusercontent.com")
OAUTH_CODE_SECONDS = 300
OAUTH_TOKEN_SECONDS = 3600
LOGIN_FAILS = 10                         # different wrong passwords from one address (5 minutes) before a block
LOGIN_BLOCK_SECONDS = 900
ICONS = ("plug", "kettle", "router", "tv", "ac", "lamp", "heater", "fan", "fridge",
         "washer", "charger", "computer", "speaker", "camera", "microwave", "iron")
DEFAULT_SETTINGS = {"price_kwh": 1.5, "currency": "EGP", "max_temp_c": 60, "max_watts": 3000, "alexa": True,
                    "signup": True}
SIGNUPS_PER_HOUR = 5                     # new customer accounts from one internet address
SIGNUPS_PER_HOUR_ALL = 100               # new customer accounts per hour from everywhere together
MIN_PASSWORD = 8                         # characters in a new customer password (older accounts keep theirs)
ACCOUNT_FAILS = 10                       # wrong passwords for one account (15 minutes, from anywhere) before it pauses
ANNOUNCES_PER_HOUR = 100                 # strips one account may say it is setting up, per hour (floods only)
MAX_SESSIONS = 10                        # signed-in phones per customer account
MAX_HOUSEHOLD = 20                       # people one customer may invite into their home
# what a customer may set for their own account (the rest of the settings are the owner's)
SETTING_LIMITS = {"price_kwh": (0, 1000), "max_temp_c": (20, 150), "max_watts": (50, 100000)}
PASSWORD_ROUNDS = 200_000
SSDP_GROUP = "239.255.255.250"
SSDP_PORT = 1900
ALEXA_BASE_PORT = 52100                  # one small web server per outlet, like a real smart plug
HERE = Path(__file__).resolve().parent
# the Android app: a downloaded copy next to this file, or a local Gradle build
APK_CANDIDATES = (HERE / "darwish-smart-power.apk", HERE / "android/app/build/outputs/apk/debug/app-debug.apk")

APK_RELEASE_URL = "https://github.com/mohamedstar19/darwish-smart-power/releases/latest/download/darwish-smart-power.apk"
APK_RELEASE_API = "https://api.github.com/repos/mohamedstar19/darwish-smart-power/releases/latest"
APK_REFRESH_EVERY = 6 * 3600            # look for a newer app build this often
WEBSITE = HERE / "website"
# public pages and files of the website (no token): path -> (file in website/, content type)
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/welcome": ("index.html", "text/html; charset=utf-8"),
    "/panel": ("panel.html", "text/html; charset=utf-8"),
    "/delete-account": ("delete-account.html", "text/html; charset=utf-8"),
    "/privacy": ("privacy.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
}
ICON_TYPES = {".png": "image/png", ".svg": "image/svg+xml"}


def admin_manifest(owner_path: str) -> Dict[str, Any]:
    """Web app manifest for the owner page: installed apart from the customers' panel, with a shield badge."""
    return {
        "name": "لوحة الإدارة · Darwish Power", "short_name": "إدارة Darwish",
        "description": "لوحة إدارة سيرفر Darwish Smart Power", "id": owner_path, "start_url": owner_path,
        "scope": owner_path, "display": "standalone", "background_color": "#0d0907", "theme_color": "#1e130d",
        "lang": "ar", "dir": "rtl",
        "icons": [
            {"src": "/icons/icon-admin-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icons/icon-admin-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icons/maskable-admin-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            {"src": "/icons/icon-admin.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
        ],
    }


def find_apk() -> Optional[Path]:
    return next((p for p in APK_CANDIDATES if p.is_file()), None)


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# --------------------------------------------------------------------------- protocol

def clean_line(raw: bytes) -> str:
    """The firmware pads some frames with NUL bytes."""
    return raw.replace(b"\x00", b"").decode("utf-8", "replace").strip()


def is_mac(text: str) -> bool:
    return len(text) == 12 and all(c in "0123456789abcdefABCDEF" for c in text)


def code_matches(code: str, macs: List[str]) -> bool:
    """Is [code] (what follows TONLY_TAP_ in the setup Wi-Fi name) one of these MACs? Their end, or anywhere for 6+ characters."""
    return bool(code) and any(m and (m.endswith(code) or (len(code) >= 6 and code in m)) for m in macs)


def parse_bootinfo(line: str) -> Optional[Dict[str, str]]:
    """up:bootinfo:<model>;<mac>;<mac>;<firmware>;connect

    Other firmware versions may differ a little (letter case, one MAC only, more or fewer fields,
    something before it), so anything with "bootinfo:" and a MAC counts."""
    at = line.lower().find("bootinfo:")
    if at < 0:
        return None
    parts = [p.strip() for p in line[at + len("bootinfo:"):].split(";")]
    found = [i for i, p in enumerate(parts) if is_mac(p)]
    if not found:
        return None
    printable = lambda x: re.sub(r"[^\x20-\x7e]", "", x)[:40]
    rest = [p for p in parts[found[0] + 1:] if p and not is_mac(p) and p.lower() != "connect"]
    return {"model": printable(parts[0]) if found[0] > 0 else "", "mac": parts[found[0]].upper(),
            "mac2": parts[found[1]].upper() if len(found) > 1 else "", "fw": printable(rest[0]) if rest else ""}


def parse_getinfo(payload: str) -> Dict[int, Dict[str, Any]]:
    """'1:<fields>:2:<fields>:...' -> {outlet: reading}.

    Each <fields> block is 12 values separated by ';':
    countdown, relay on/off, standby threshold, overload ok, overheat ok, power (mW), energy (hex Wh),
    daily energy, energy budget, standby cutoff, event code (hex), temperature (C).
    The protection flags read "on" while healthy and "off" once tripped (MTTL protocol guide); event
    code 01 is an overload, 02 overheating.
    Channel 5 is the strip total and is skipped; totals are computed from the outlets.
    """
    tokens = payload.split(":")
    readings: Dict[int, Dict[str, Any]] = {}
    for i in range(0, len(tokens) - 1, 2):
        channel, fields = tokens[i].strip(), tokens[i + 1].split(";")
        if not channel.isdigit() or int(channel) not in OUTLETS or len(fields) < 12:
            continue
        try:
            readings[int(channel)] = {
                "on": fields[1].strip().lower() == "on",
                "watts": round(int(fields[5]) / 1000.0, 2),
                "kwh": round(int(fields[6], 16) / 1000.0, 3),
                "temp_c": int(fields[11]),
                "trip": TRIP_OVERLOAD if fields[3].strip().lower() == "off" or fields[10].strip() == "01" else
                        TRIP_OVERHEAT if fields[4].strip().lower() == "off" or fields[10].strip() == "02" else 0,
            }
        except ValueError:
            continue
    return readings


def onoff_command(outlet: int, on: bool) -> str:
    return "up:onoff:%d:%s" % (outlet, "on" if on else "off")


# --------------------------------------------------------------------------- saved settings

class Store:
    """Names, timers, schedules, rooms/icons and settings, kept in a small JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self.names: Dict[str, Dict[str, str]] = {}
        self.timers: Dict[str, Dict[str, Any]] = {}
        self.meta: Dict[str, Dict[str, Any]] = {}
        self.schedules: List[Dict[str, Any]] = []
        self.scenes: List[Dict[str, Any]] = []
        self.pending: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.alexa_ports: Dict[str, int] = {}
        # family members: {"id", "name", "role": "control"|"view", "strips": [MAC, ...] (empty = all),
        #                  "token_hash", "created"}; the server token itself is the owner.
        # customers: {"id", "name", "role": "customer", "login", "pw", "strips", "sessions", "settings"?}
        # a customer's household: {"id", "name", "role": "control"|"view", "parent": customer id, "token_hash",
        #                  "created"}; they see exactly the strips of the customer who invited them
        self.users: List[Dict[str, Any]] = []
        self.settings: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        # strips the owner accepted; others stay waiting. blocked ones are refused outright.
        self.approved: List[str] = []
        self.blocked_strips: List[str] = []
        # strips the app is setting up right now: setup-Wi-Fi code -> {"by": member id or "", "until"}
        self.expected: Dict[str, Dict[str, Any]] = {}
        # every strip the app said it was setting up (last 7 days): {"code", "by", "at", "ip"}, to tell whose a
        # strip waiting for approval probably is; and the strips waiting for approval, so a restart does not
        # hide them: {MAC: {"first", "last", "address", "mac2"}}
        self.announced: List[Dict[str, Any]] = []
        self.waiting: Dict[str, Dict[str, Any]] = {}
        # power cuts: {MAC: {"since", "on": [outlets on when it went], "back"?: when it came back}} and outlets
        # waiting to turn back on after one: {"MAC/outlet": when}
        self.outages: Dict[str, Dict[str, Any]] = {}
        self.restores: Dict[str, float] = {}
        # app downloads: total, per day, and today's (hashed) addresses so a retry is not counted twice
        self.stats: Dict[str, Any] = {}
        # the owner's admin page lives at a random, unguessable address instead of /admin
        self.admin_path = ""
        # voice assistant links: {"clients": {name: secret hash}, "codes": {...}, "access": {...}, "refresh": {...}}
        self.oauth: Dict[str, Dict[str, Any]] = {"clients": {}, "codes": {}, "access": {}, "refresh": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.names = dict(data.get("names", {}))
            self.timers = dict(data.get("timers", {}))
            self.meta = dict(data.get("meta", {}))
            self.schedules = [normalise_schedule(x) for x in data.get("schedules", [])]
            self.scenes = list(data.get("scenes", []))
            self.pending = dict(data.get("pending", {}))
            self.alexa_ports = dict(data.get("alexa_ports", {}))
            self.users = list(data.get("users", []))
            self.settings.update(data.get("settings", {}))
            self.blocked_strips = list(data.get("blocked_strips", []))
            self.expected = dict(data.get("expected", {}))
            self.announced = list(data.get("announced", []))
            self.waiting = dict(data.get("waiting", {}))
            self.outages = dict(data.get("outages", {}))
            self.restores = dict(data.get("restores", {}))
            for mac, at in data.get("first_seen", {}).items():     # kept by one earlier version
                self.waiting.setdefault(mac, {"first": at, "last": at, "address": "", "mac2": ""})
            self.stats = dict(data.get("stats", {}))
            self.admin_path = str(data.get("admin_path", ""))
            self.oauth.update({k: dict(v) for k, v in data.get("oauth", {}).items()})
            if "approved" in data:
                self.approved = list(data["approved"])
            else:                                   # before approvals existed: every strip in use counts
                self.approved = sorted(self.strips_in_use())
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as err:
            log("[store] could not read %s (%s), starting empty" % (path, err))

    def save(self) -> None:
        data = json.dumps({"names": self.names, "timers": self.timers, "meta": self.meta,
                           "schedules": self.schedules, "scenes": self.scenes, "pending": self.pending,
                           "alexa_ports": self.alexa_ports, "users": self.users,
                           "settings": self.settings, "approved": self.approved,
                           "blocked_strips": self.blocked_strips, "expected": self.expected,
                           "announced": self.announced, "waiting": self.waiting,
                           "outages": self.outages, "restores": self.restores,
                           "stats": self.stats, "admin_path": self.admin_path, "oauth": self.oauth},
                          ensure_ascii=False, indent=1)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".smartpower-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
            os.replace(tmp, str(self.path))
        except OSError as err:
            log("[store] could not save %s: %s" % (self.path, err))
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ---- voice assistant account linking (OAuth 2). Only hashes of secrets and tokens are kept.

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def oauth_new_secret(self, client: str) -> str:
        secret = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
        self.oauth["clients"][client] = self._digest(secret)
        self.save()
        return secret

    def oauth_client_ok(self, client: str, secret: str) -> bool:
        stored = self.oauth["clients"].get(client, "")
        return bool(stored) and hmac.compare_digest(stored, self._digest(secret or ""))

    def _oauth_prune(self, now: float) -> None:
        for kind in ("codes", "access", "refresh"):
            self.oauth[kind] = {k: v for k, v in self.oauth[kind].items() if v.get("until", 0) > now}

    def oauth_new_code(self, client: str, subject: str, redirect: str, now: float) -> str:
        code = base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("=")
        self._oauth_prune(now)
        self.oauth["codes"][self._digest(code)] = {"client": client, "subject": subject, "redirect": redirect,
                                                   "until": now + OAUTH_CODE_SECONDS}
        self.save()
        return code

    def oauth_tokens(self, client: str, subject: str, now: float) -> Dict[str, Any]:
        access = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
        refresh = base64.urlsafe_b64encode(os.urandom(30)).decode().rstrip("=")
        self.oauth["access"][self._digest(access)] = {"client": client, "subject": subject,
                                                      "until": now + OAUTH_TOKEN_SECONDS}
        self.oauth["refresh"][self._digest(refresh)] = {"client": client, "subject": subject,
                                                        "until": now + 3650 * 86400}
        self.save()
        return {"access_token": access, "token_type": "bearer", "expires_in": OAUTH_TOKEN_SECONDS,
                "refresh_token": refresh}

    def oauth_take_code(self, code: str, client: str, redirect: str, now: float) -> Optional[str]:
        entry = self.oauth["codes"].pop(self._digest(code or ""), None)
        self.save()
        if not entry or entry["until"] <= now or entry["client"] != client:
            return None
        if redirect and redirect != entry["redirect"]:
            return None
        return entry["subject"]

    def oauth_refresh(self, refresh: str, client: str, now: float) -> Optional[str]:
        entry = self.oauth["refresh"].get(self._digest(refresh or ""))
        if not entry or entry["until"] <= now or entry["client"] != client:
            return None
        return entry["subject"]

    def oauth_subject(self, access: str, now: float) -> Optional[str]:
        """"owner" or a customer/member id for a voice assistant's access token."""
        entry = self.oauth["access"].get(self._digest(access or ""))
        return entry["subject"] if entry and entry["until"] > now else None

    def owner_path(self) -> str:
        """"/owner-<random>": made once and kept, so the owner can bookmark it."""
        if not re.fullmatch(r"/owner-[A-Za-z0-9_-]{12,}", self.admin_path or ""):
            self.admin_path = "/owner-" + base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
            self.save()
        return self.admin_path

    def strips_in_use(self) -> set:
        """Strips something refers to: names, rooms/icons, schedules, scenes or a family member."""
        macs = {m for m, v in self.names.items() if v} | {m for m, v in self.meta.items() if v}
        macs |= {x.get("strip", "") for x in self.schedules}
        macs |= {a.get("strip", "") for x in self.scenes for a in x.get("actions", [])}
        macs |= {m for u in self.users for m in (u.get("strips") or [])}
        return {m for m in macs if m}

    def knows(self, mac: str) -> bool:
        """True for an approved strip, or one the owner has already named or set up."""
        return mac in self.approved or bool(self.names.get(mac)) or bool(self.meta.get(mac))

    def is_approved(self, mac: str) -> bool:
        return mac in self.approved

    def approve(self, mac: str, by: str = "") -> None:
        """Accept a strip; [by] is the family member who set it up, who then gets to see it."""
        if mac in self.blocked_strips:
            self.blocked_strips.remove(mac)
        if mac not in self.approved:
            self.approved.append(mac)
        self.waiting.pop(mac, None)
        user = next((u for u in self.users if u["id"] == by), None)
        if user and (user.get("role") == CUSTOMER or user.get("strips")) and mac not in (user.get("strips") or []):
            user["strips"] = sorted((user.get("strips") or []) + [mac])
        self.save()

    def expect(self, code: str, by: str, now: float, seconds: float = EXPECT_SECONDS) -> None:
        self.expected = {c: e for c, e in self.expected.items() if e.get("until", 0) > now}
        old = self.expected.get(code)
        entry = {"by": by, "until": max(now + seconds, old.get("until", 0) if old else 0)}
        if old and (old.get("conflict") or str(old.get("by", "")) != by):
            # two accounts claim the same strip (e.g. a neighbour who saw its Wi-Fi name): the owner decides
            entry["conflict"] = True
            log("[strip] TONLY_TAP_%s claimed by two accounts; it will wait for the owner" % code)
        self.expected[code] = entry
        self.save()

    def expects(self, macs: List[str], now: float) -> bool:
        """Did the app announce (or the owner reserve) this strip?"""
        return any(e.get("until", 0) > now and code_matches(c, macs) for c, e in self.expected.items())

    def claim_expected(self, macs: List[str], now: float) -> Optional[str]:
        """The member id ("" = owner) that announced this strip from the app, or None."""
        for code, entry in list(self.expected.items()):
            if entry.get("until", 0) > now and code_matches(code, macs):
                del self.expected[code]
                self.save()
                return None if entry.get("conflict") or entry.get("late") else str(entry.get("by", ""))
        return None

    def announce(self, code: str, by: str, ip: str, now: float) -> None:
        self.announced = [a for a in self.announced if now - a.get("at", 0) < 7 * 86400][-299:]
        self.announced.append({"code": code, "by": by, "at": int(now), "ip": ip})
        self.save()

    def note_waiting(self, mac: str, mac2: str, address: str, now: float) -> float:
        """Remembers a strip waiting for approval; returns when it first connected."""
        entry = self.waiting.get(mac)
        if entry is None:
            self.waiting = {m: e for m, e in self.waiting.items()
                            if m not in self.approved and now - e.get("last", 0) < 30 * 86400}
            entry = self.waiting[mac] = {"first": now}
        entry.update({"last": now, "address": address, "mac2": mac2})
        self.save()
        return entry["first"]

    def forget_strip(self, mac: str, block: bool) -> None:
        """Remove everything about a strip; [block] also refuses it from now on."""
        self.names.pop(mac, None)
        self.meta.pop(mac, None)
        self.pending.pop(mac, None)
        self.waiting.pop(mac, None)
        self.timers = {k: v for k, v in self.timers.items() if not k.startswith(mac + "/")}
        self.alexa_ports = {k: v for k, v in self.alexa_ports.items() if not k.startswith(mac + "/")}
        self.schedules = [x for x in self.schedules if x.get("strip") != mac]
        for scene in self.scenes:
            scene["actions"] = [a for a in scene.get("actions", []) if a.get("strip") != mac]
        self.scenes = [x for x in self.scenes if x.get("actions")]
        for user in self.users:
            if mac in (user.get("strips") or []):
                user["strips"] = [m for m in user["strips"] if m != mac]
        if mac in self.approved:
            self.approved.remove(mac)
        if block and mac not in self.blocked_strips:
            self.blocked_strips.append(mac)
        self.save()

    def name(self, mac: str, outlet: int) -> Optional[str]:
        return self.names.get(mac, {}).get(str(outlet))

    def rename(self, mac: str, outlet: int, name: str) -> None:
        names = self.names.setdefault(mac, {})
        if name:
            names[str(outlet)] = name
        else:
            names.pop(str(outlet), None)
        self.save()

    @staticmethod
    def timer_key(mac: str, outlet: int) -> str:
        return "%s/%d" % (mac, outlet)

    def timer(self, mac: str, outlet: int) -> Optional[Dict[str, Any]]:
        return self.timers.get(self.timer_key(mac, outlet))

    def set_timer(self, mac: str, outlet: int, on: bool, at: float) -> None:
        self.timers[self.timer_key(mac, outlet)] = {"on": on, "at": at}
        self.save()

    def clear_timer(self, mac: str, outlet: int) -> None:
        if self.timers.pop(self.timer_key(mac, outlet), None) is not None:
            self.save()

    # rooms, icons, favourites
    def room(self, mac: str) -> Optional[str]:
        return self.meta.get(mac, {}).get("room") or None

    def outlet_meta(self, mac: str, outlet: int) -> Dict[str, Any]:
        return self.meta.get(mac, {}).get("outlets", {}).get(str(outlet), {})

    def after_power(self, mac: str, outlet: int) -> Optional[int]:
        """Minutes after a power cut before this outlet goes back on (if it was on); None = stays off."""
        value = self.outlet_meta(mac, outlet).get("after_power")
        return int(value) if isinstance(value, (int, float)) and value >= 0 else None

    def set_meta(self, mac: str, outlet: int, room: Optional[str] = None,
                 icon: Optional[str] = None, favorite: Optional[bool] = None, after_power: Any = "keep") -> None:
        entry = self.meta.setdefault(mac, {})
        if room is not None:
            if room:
                entry["room"] = room
            else:
                entry.pop("room", None)
        if outlet and (icon is not None or favorite is not None or after_power != "keep"):
            o = entry.setdefault("outlets", {}).setdefault(str(outlet), {})
            if icon is not None:
                o["icon"] = icon
            if favorite is not None:
                o["fav"] = favorite
            if after_power is None:
                o.pop("after_power", None)
            elif after_power != "keep":
                o["after_power"] = int(after_power)
        self.save()

    # schedules, kind "time": {"id", "strip", "outlets", "on", "time": "HH:MM", "days": [0..6 = Mon..Sun], "enabled"}
    #            kind "cycle": {"id", "strip", "outlets", "on_minutes", "off_minutes", "started_at", "enabled"}
    #            kind "watch": {"id", "strip", "outlets", "metric": "power"|"temp", "above", "value", "seconds",
    #                           "action": "off"|"alert", "enabled"} - e.g. off once a charger draws under 3 W for 10 min
    def save_schedule(self, sched: Dict[str, Any]) -> None:
        self.schedules = [s for s in self.schedules if s.get("id") != sched["id"]] + [sched]
        self.schedules.sort(key=lambda s: (s.get("kind") != "time", s.get("time", ""), s["strip"]))
        self.save()

    def delete_schedule(self, sched_id: str) -> bool:
        before = len(self.schedules)
        self.schedules = [s for s in self.schedules if s.get("id") != sched_id]
        if len(self.schedules) != before:
            self.save()
            return True
        return False

    def user_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        digest = hashlib.sha256(token.encode()).hexdigest()
        for user in self.users:
            if hmac.compare_digest(user.get("token_hash", ""), digest):
                return user
            if any(hmac.compare_digest(x.get("hash", ""), digest) for x in user.get("sessions", [])):
                return user
        return None

    # ---- customer accounts

    def account_by_login(self, login: str) -> Optional[Dict[str, Any]]:
        return next((u for u in self.users if u.get("role") == CUSTOMER and u.get("login") == login), None)

    def new_session(self, user: Dict[str, Any]) -> str:
        """A fresh sign-in token for one phone; only its hash is kept."""
        token = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
        sessions = user.setdefault("sessions", [])
        sessions.append({"hash": hashlib.sha256(token.encode()).hexdigest(), "created": int(time.time())})
        del sessions[:-MAX_SESSIONS]
        self.save()
        return token

    def end_session(self, token: str) -> None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        for user in self.users:
            before = len(user.get("sessions", []))
            user["sessions"] = [x for x in user.get("sessions", []) if x.get("hash") != digest]
            if len(user["sessions"]) != before:
                self.save()
                return

    def count_download(self, ip: str, now: float) -> None:
        """Counts app downloads, once per address per day (a retry or refresh is not a new download)."""
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        stats = self.stats
        if stats.get("seen_day") != day:
            stats["seen_day"], stats["seen"] = day, []
        tag = hashlib.sha256(("%s|%s" % (day, ip)).encode()).hexdigest()[:12]
        if tag in stats["seen"]:
            return
        stats["seen"] = (stats["seen"] + [tag])[-5000:]
        stats["downloads"] = int(stats.get("downloads", 0)) + 1
        days = stats.setdefault("download_days", {})
        days[day] = int(days.get(day, 0)) + 1
        for old in sorted(days)[:-90]:            # keep three months of daily counts
            del days[old]
        self.save()

    # PIN locks: meta[mac]["pin"] = "salt$sha256(salt + pin)"
    def locked(self, mac: str) -> bool:
        return bool(self.meta.get(mac, {}).get("pin"))

    def pin_ok(self, mac: str, pin: str) -> bool:
        stored = self.meta.get(mac, {}).get("pin", "")
        salt, _, digest = stored.partition("$")
        return bool(stored) and hmac.compare_digest(hashlib.sha256((salt + pin).encode()).hexdigest(), digest)

    def set_pin(self, mac: str, pin: str) -> None:
        entry = self.meta.setdefault(mac, {})
        if pin:
            salt = os.urandom(8).hex()
            entry["pin"] = salt + "$" + hashlib.sha256((salt + pin).encode()).hexdigest()
        else:
            entry.pop("pin", None)
        self.save()

    # commands waiting for an offline strip: pending[mac][outlet] = {"on", "at"}
    def queue(self, mac: str, outlets: List[int], on: bool, now: float) -> None:
        waiting = self.pending.setdefault(mac, {})
        for n in outlets:
            waiting[str(n)] = {"on": on, "at": int(now)}
        self.save()

    def take_pending(self, mac: str, now: float) -> Dict[int, bool]:
        waiting = self.pending.pop(mac, {})
        if waiting:
            self.save()
        return {int(n): bool(c["on"]) for n, c in waiting.items() if now - c.get("at", 0) <= QUEUE_TTL}

    # scenes: {"id", "name", "icon", "actions": [{"strip", "outlet", "on"}]}
    def save_scene(self, scene: Dict[str, Any]) -> None:
        self.scenes = [x for x in self.scenes if x.get("id") != scene["id"]] + [scene]
        self.save()

    def delete_scene(self, scene_id: str) -> bool:
        before = len(self.scenes)
        self.scenes = [x for x in self.scenes if x.get("id") != scene_id]
        if len(self.scenes) != before:
            self.save()
            return True
        return False


def normalise_schedule(sched: Dict[str, Any]) -> Dict[str, Any]:
    """Older files stored one "outlet" per schedule; now it is a list."""
    sched = dict(sched)
    if "outlets" not in sched:
        sched["outlets"] = [int(sched.pop("outlet", 0))]
    sched.setdefault("kind", "time")
    return sched


OWNER = {"role": "owner", "name": None, "id": None, "strips": None, "account": None}
ROLES = ("control", "view")
CUSTOMER = "customer"                    # signs up in the app and sees only the strips they added
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalise_login(text: Any) -> Optional[str]:
    """A phone number (digits, optional +) or an e-mail address, in one canonical form; None if neither."""
    login = re.sub(r"[\s\-()]", "", str(text or "").translate(ARABIC_DIGITS)).lower()
    if re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,24}", login):
        return login
    if re.fullmatch(r"\+?\d{8,15}", login):
        return login
    return None


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ROUNDS)
    return "pbkdf2$%d$%s$%s" % (PASSWORD_ROUNDS, salt.hex(), digest.hex())


DUMMY_HASH = hash_password(os.urandom(16).hex())       # checked against when a login does not exist


def password_ok(stored: str, password: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        mine = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(mine.hex(), digest)
    except (ValueError, AttributeError):
        return False
OWNER_ONLY = ("/api/users", "/api/settings", "/api/lock", "/api/strips/approve", "/api/strips/remove", "/api/strips/assign",
              "/api/strips/reserve", "/api/strips/unreserve", "/api/admin", "/api/oauth/secret", "/api/customers/")

LINK_PAGE = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Darwish Smart Power</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font:15px/1.6 system-ui,Tahoma,sans-serif;color:#fff4ec;
background:linear-gradient(180deg,#1e130d,#0d0907)}form{width:min(380px,92vw);padding:24px;border-radius:22px;
background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.14);text-align:center}img{width:72px;border-radius:18px}
h1{font-size:19px;margin:10px 0 2px}p{color:#c9b8ac;margin:0 0 14px;font-size:13px}input{width:100%%;box-sizing:border-box;
margin:6px 0;padding:12px;border-radius:12px;border:1px solid rgba(255,255,255,.18);background:rgba(0,0,0,.3);color:inherit;
font:inherit;text-align:center}button{width:100%%;margin-top:10px;padding:12px;border:0;border-radius:12px;font:inherit;
font-weight:700;background:linear-gradient(135deg,#ff7a3d,#ffb347);color:#2a0e00}.err{color:#ff6b6b;min-height:22px}
small{display:block;margin-top:12px;color:#8a7a6f}</style></head><body>
<form method="post" action="/oauth/authorize"><img src="/icons/icon-192.png" alt="">
<h1>اربط حسابك بـ %(who)s</h1><p>ادخل برقم الموبايل أو الإيميل والباسورد بتوع تطبيق Darwish Smart Power</p>
%(hidden)s<input name="login" placeholder="رقم الموبايل أو الإيميل" dir="ltr" autocomplete="username" required>
<input name="password" type="password" placeholder="الباسورد أو كود الدعوة" dir="ltr" autocomplete="current-password" required>
<div class="err">%(error)s</div><button type="submit">اربط</button>
<small>Sign in with your Darwish Smart Power account to link it to %(who)s.</small></form></body></html>"""


def may_see(who: Dict[str, Any], mac: str) -> bool:
    """A member limited to some strips sees only those; the owner and unlimited members see all."""
    return who["strips"] is None or mac.upper() in who["strips"]


def outlet_list(req: Dict[str, Any]) -> List[int]:
    """"outlets": [1, 3] or "outlet": 2; 0 anywhere means all four."""
    raw = req.get("outlets")
    if raw is None:
        raw = [req.get("outlet", 0)]
    if not isinstance(raw, list) or not raw:
        raise ValueError("outlets must be a non-empty list")
    outlets = sorted({int(n) for n in raw})
    if any(n != 0 and n not in OUTLETS for n in outlets):
        raise ValueError("outlets must be 0 (all) or 1-4")
    return [0] if 0 in outlets else outlets


# --------------------------------------------------------------------------- energy history + alerts

class History:
    """Hourly energy per outlet and the alert log, in a small SQLite file.

    The strip reports a running energy counter per outlet (Wh). Each new reading adds the
    difference to the current hour, so energy used while this server was down is still
    counted (it lands in the hour the server comes back).
    """

    MAX_STEP_WH = 5000          # ignore impossible jumps (garbage readings)

    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS energy (mac TEXT, outlet INTEGER, hour INTEGER, wh REAL,
                                               PRIMARY KEY (mac, outlet, hour));
            CREATE TABLE IF NOT EXISTS counters (mac TEXT, outlet INTEGER, wh REAL,
                                                 PRIMARY KEY (mac, outlet));
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER,
                                               kind TEXT, mac TEXT, outlet INTEGER, value REAL);
        """)
        self.counters = {(m, o): wh for m, o, wh in self.db.execute("SELECT mac, outlet, wh FROM counters")}

    def record(self, mac: str, counters_wh: Dict[int, float], now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        hour = int(now // 3600 * 3600)
        for outlet, wh in counters_wh.items():
            last = self.counters.get((mac, outlet))
            self.counters[(mac, outlet)] = wh
            self.db.execute("INSERT OR REPLACE INTO counters VALUES (?, ?, ?)", (mac, outlet, wh))
            if last is None:
                continue
            step = wh - last if wh >= last else wh          # a smaller value means the counter was reset
            if 0 < step <= self.MAX_STEP_WH:
                self.db.execute("INSERT INTO energy VALUES (?, ?, ?, ?) ON CONFLICT (mac, outlet, hour) "
                                "DO UPDATE SET wh = wh + excluded.wh", (mac, outlet, hour, step))
        self.db.commit()

    def rows_since(self, start: float, mac: Optional[str] = None) -> List[Tuple[str, int, int, float]]:
        sql = "SELECT mac, outlet, hour, wh FROM energy WHERE hour >= ?"
        args: List[Any] = [int(start)]
        if mac:
            sql += " AND mac = ?"
            args.append(mac)
        return list(self.db.execute(sql, args))

    def add_event(self, kind: str, mac: str, outlet: int = 0, value: float = 0.0,
                  now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self.db.execute("INSERT INTO events (ts, kind, mac, outlet, value) VALUES (?, ?, ?, ?, ?)",
                        (int(now), kind, mac, outlet, value))
        self.db.execute("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?", (MAX_EVENTS,))
        self.db.commit()

    def events_after(self, after: int, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.db.execute("SELECT id, ts, kind, mac, outlet, value FROM events WHERE id > ? "
                               "ORDER BY id DESC LIMIT ?", (after, limit))
        return [{"id": i, "ts": ts, "kind": k, "strip": m, "outlet": o, "value": v} for i, ts, k, m, o, v in rows]

    def last_event_id(self) -> int:
        return self.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]


def xlsx_bytes(sheets: List[Tuple[str, List[List[Any]], List[int]]], rtl: bool = False) -> bytes:
    """A small Excel workbook (.xlsx) with no library: each sheet is (name, rows, column widths); the first
    row is a bold header that stays in view. Numbers stay numbers, everything else is text."""
    def cell_ref(col: int, row: int) -> str:
        letters = ""
        col += 1
        while col:
            col, rem = divmod(col - 1, 26)
            letters = chr(65 + rem) + letters
        return "%s%d" % (letters, row)

    def text(v: Any) -> str:
        return html_escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v)), quote=False)

    def sheet_xml(rows: List[List[Any]], widths: List[int]) -> str:
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<sheetViews><sheetView workbookViewId="0"%s><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
               'state="frozen"/></sheetView></sheetViews><cols>' % (' rightToLeft="1"' if rtl else "")]
        out += ['<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, w) for i, w in enumerate(widths)]
        out.append("</cols><sheetData>")
        for r, row in enumerate(rows, 1):
            out.append('<row r="%d">' % r)
            for c, v in enumerate(row):
                style = ' s="1"' if r == 1 else (' s="2"' if isinstance(v, float) else "")
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append('<c r="%s"%s><v>%s</v></c>' % (cell_ref(c, r), style, repr(v)))
                else:
                    out.append('<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                               % (cell_ref(c, r), style, text(v)))
            out.append("</row>")
        out.append("</sheetData></worksheet>")
        return "".join(out)

    names = [re.sub(r"[\[\]:*?/\\]", "", n)[:31] or "Sheet" for n, _, _ in sheets]
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-'
                      'officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1) for i in range(len(sheets)))
            + "</Types>",
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (text(n), i + 1, i + 1) for i, n in enumerate(names))
            + "</sheets></workbook>",
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                      'worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1) for i in range(len(sheets)))
            + '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
              'Target="styles.xml"/></Relationships>' % (len(sheets) + 1),
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.000"/></numFmts>'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFFFD9C2"/><bgColor indexed="64"/></patternFill></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
            '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            '</styleSheet>',
    }
    for i, (_, rows, widths) in enumerate(sheets):
        files["xl/worksheets/sheet%d.xml" % (i + 1)] = sheet_xml(rows, widths)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ["[Content_Types].xml", "_rels/.rels"] + [n for n in files if n.startswith("xl/")]:
            z.writestr(name, files[name])
    return buf.getvalue()


def local_midnight(now: float, days_back: int = 0) -> float:
    day = datetime.date.fromtimestamp(now) - datetime.timedelta(days=days_back)
    return time.mktime(day.timetuple())


# --------------------------------------------------------------------------- strips

class Outlet:
    def __init__(self, index: int):
        self.index = index
        self.on = False
        self.watts = 0.0
        self.kwh = 0.0
        self.temp_c: Optional[int] = None
        self.trip = 0                           # TRIP_OVERLOAD / TRIP_OVERHEAT while the strip's protection holds it off


class Strip:
    def __init__(self, mac: str):
        self.mac = mac
        self.mac2 = ""
        self.first_seen = 0.0                   # while waiting for approval: when it first connected
        self.model = ""
        self.fw = ""
        self.address = ""
        self.volts: Optional[float] = None
        self.rssi: Optional[int] = None
        self.last_seen = 0.0
        self.outlets = {n: Outlet(n) for n in OUTLETS}
        self.link: Optional["StripLink"] = None

    @property
    def online(self) -> bool:
        return self.link is not None and not self.link.closed

    def power_back(self, store: Store) -> Optional[Dict[str, Any]]:
        """After a power cut, while some outlet that was on is still off: when the power came back and
        which outlets were on, so the app can offer to turn them back on."""
        out = store.outages.get(self.mac)
        if not out or not out.get("back") or time.time() - out["back"] > POWER_BACK_KEEP or not self.online:
            return None
        off = [n for n in out.get("on", []) if n in self.outlets and not self.outlets[n].on]
        return {"at": int(out["back"]), "since": int(out.get("since", 0)), "on": off} if off else None

    def to_json(self, store: Store, today: Optional[Dict[Tuple[str, int], float]] = None) -> Dict[str, Any]:
        watts = round(sum(o.watts for o in self.outlets.values()), 2)
        all_timer = store.timer(self.mac, 0)
        today = today or {}
        return {
            "id": self.mac,
            "name": store.name(self.mac, 0),
            "room": store.room(self.mac),
            "locked": store.locked(self.mac),
            "pending": {n: c["on"] for n, c in store.pending.get(self.mac, {}).items()},
            "today_kwh": round(sum(today.get((self.mac, n), 0.0) for n in OUTLETS) / 1000.0, 3),
            "model": self.model,
            "fw": self.fw,
            "address": self.address,
            "online": self.online,
            "last_seen": int(self.last_seen),
            "watts": watts,
            "kwh": round(sum(o.kwh for o in self.outlets.values()), 3),
            "volts": self.volts,
            "amps": round(watts / self.volts, 2) if self.volts else None,
            "rssi": self.rssi,
            "timer": all_timer,
            "power_back": self.power_back(store),
            "outlets": [
                {
                    "index": o.index,
                    "name": store.name(self.mac, o.index),
                    "on": o.on,
                    "watts": o.watts,
                    "kwh": o.kwh,
                    "temp_c": o.temp_c,
                    "timer": store.timer(self.mac, o.index),
                    "icon": store.outlet_meta(self.mac, o.index).get("icon", "plug"),
                    "favorite": bool(store.outlet_meta(self.mac, o.index).get("fav", False)),
                    "after_power": store.after_power(self.mac, o.index),
                    "restore_at": int(store.restores.get("%s/%d" % (self.mac, o.index), 0)) or None,
                    "today_kwh": round(today.get((self.mac, o.index), 0.0) / 1000.0, 3),
                }
                for o in self.outlets.values()
            ],
        }


class StripLink:
    """One TCP connection from a strip. Commands that expect an answer run one at a time."""

    def __init__(self, hub: "Hub", reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.hub = hub
        self.reader = reader
        self.writer = writer
        peer = writer.get_extra_info("peername")
        self.address = peer[0] if peer else "?"
        self.strip: Optional[Strip] = None
        self.turn = asyncio.Lock()
        self.state_waiters: List[asyncio.Future] = []
        self.missed = 0
        self.closed = False
        self.before_hello = 0
        self.heard = ""                         # what came before the hello, to tell an odd strip from a scanner
        self.probed = False                     # asked "who are you?" after a silent start

    async def send(self, line: str) -> None:
        self.writer.write(line.encode("utf-8") + b"\r\n")
        await self.writer.drain()

    async def _read_state(self, timeout: float) -> bool:
        waiter = asyncio.get_running_loop().create_future()
        self.state_waiters.append(waiter)
        try:
            await self.send("up:getinfo:all")
            await asyncio.wait_for(waiter, timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            if waiter in self.state_waiters:
                self.state_waiters.remove(waiter)

    async def read_state(self, timeout: float = 4.0) -> bool:
        async with self.turn:
            ok = await self._read_state(timeout)
        self.missed = 0 if ok else self.missed + 1
        return ok

    async def switch(self, outlets: List[int], on: bool) -> bool:
        """Switch outlets and return True once a fresh state read shows the change."""
        async with self.turn:
            for n in outlets:                       # the protocol has no "all": send one per outlet
                await self.send(onoff_command(n, on))
            for _ in range(3):
                await asyncio.sleep(0.4)
                if await self._read_state(4.0) and self.strip and \
                        all(self.strip.outlets[n].on == on for n in outlets):
                    return True
            return False

    async def ask_diagnostics(self) -> None:
        async with self.turn:
            await self.send("up:power_report:1:vol")
            await self.send("up:query:wifirssi")

    def close(self) -> None:
        self.closed = True
        self.writer.close()

    def handle(self, line: str) -> None:
        boot = parse_bootinfo(line)
        if boot:
            strip = self.hub.attach(self, boot)
            if strip is None:
                self.close()
                return
            self.strip = strip
            asyncio.ensure_future(self.hub.came_online(self))
            return
        strip = self.strip
        if strip is None:
            self.before_hello += 1
            if len(self.heard) < 300:
                self.heard += line[:300 - len(self.heard)] + " | "
            if self.before_hello > 20:
                self.hub.guard.strike(self.address, "talks but never says it is a strip (sent %r)" % self.heard[:200])
                self.close()
            return
        strip.last_seen = time.time()
        kind, _, rest = line.partition(":")[2].partition(":")
        if kind == "getinfo":
            readings = parse_getinfo(rest)
            if len(readings) == len(OUTLETS):
                for n, r in readings.items():
                    o = strip.outlets[n]
                    o.on, o.watts, o.kwh, o.temp_c, o.trip = r["on"], r["watts"], r["kwh"], r["temp_c"], r["trip"]
                self.hub.record_energy(strip)
                for w in self.state_waiters:
                    if not w.done():
                        w.set_result(True)
        elif kind == "power_report":
            # up:power_report:<ch>:<value>; values of 50000+ are millivolts, smaller ones milliamps
            value = rest.rpartition(":")[2]
            if value.isdigit() and int(value) >= 50000:
                strip.volts = round(int(value) / 1000.0, 1)
        elif kind == "query":
            # up:query:<rssi>
            try:
                strip.rssi = int(rest)
            except ValueError:
                pass
        elif kind == "event":
            # up:event:onoff:<0-4>:on|off  - someone pressed a button on the strip
            parts = rest.split(":")
            if len(parts) == 3 and parts[0] == "onoff" and parts[1].isdigit():
                n, on = int(parts[1]), parts[2].lower() == "on"
                for o in strip.outlets.values():
                    if n == 0 or o.index == n:
                        o.on = on
                asyncio.ensure_future(self.read_state())

    async def read_hello(self) -> bytes:
        """Bytes before the hello. A connection still silent after a few seconds gets asked who it is, in
        case some firmware waits for the server to speak first; what it answers is logged if it is no hello."""
        if self.probed:
            return await asyncio.wait_for(self.reader.read(4096), HELLO_TIMEOUT)
        first = min(PROBE_AFTER, HELLO_TIMEOUT)
        try:
            return await asyncio.wait_for(self.reader.read(4096), first)
        except asyncio.TimeoutError:
            self.probed = True
            for line in ("up:bootinfo", "up:query:wifirssi", "up:getinfo:all"):
                await self.send(line)
            return await asyncio.wait_for(self.reader.read(4096), max(HELLO_TIMEOUT - first, 0.1))

    async def run(self) -> None:
        buffer = b""
        try:
            while not self.closed:
                if self.strip is None:              # a real strip says hello right away
                    chunk = await self.read_hello()
                else:
                    chunk = await self.reader.read(4096)
                if not chunk:
                    break
                # lines end in CR LF, but take a lone CR, LF or NUL as well
                *lines, buffer = re.split(rb"[\r\n\x00]", buffer + chunk)
                if len(buffer) > MAX_LINE:
                    raise ValueError("line too long")
                for raw in lines:
                    line = clean_line(raw)
                    if line:
                        self.handle(line)
                # a hello that never gets its line ending still counts once it is complete
                if self.strip is None and b"connect" in buffer.lower() and parse_bootinfo(clean_line(buffer)):
                    line, buffer = clean_line(buffer), b""
                    self.handle(line)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except asyncio.TimeoutError:
            # not a strike: an odd strip from a real home must not get that home blocked. Say what it sent,
            # so a strip that speaks a little differently can be recognised.
            heard = (self.heard + clean_line(buffer))[:200]
            self.hub.unknown_talker(self.address, heard)
        except ValueError as err:                   # absurdly long line
            log("[strip] dropping %s: %s" % (self.address, err))
            self.hub.guard.strike(self.address, "line too long")
        finally:
            self.closed = True
            self.hub.detach(self)
            self.writer.close()


def alexa_header(namespace: str, name: str, correlation: Optional[str] = None) -> Dict[str, Any]:
    header = {"namespace": namespace, "name": name, "payloadVersion": "3", "messageId": os.urandom(16).hex()}
    if correlation:
        header["correlationToken"] = correlation
    return header


def alexa_reply(header: Dict[str, Any], endpoint: Dict[str, Any], namespace: str, name: str,
                properties: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"event": {"header": alexa_header(namespace, name, header.get("correlationToken")),
                      "endpoint": {"scope": endpoint.get("scope", {}), "endpointId": endpoint.get("endpointId", "")},
                      "payload": {}},
            "context": {"properties": properties}}


def alexa_error(header: Dict[str, Any], endpoint: Dict[str, Any], kind: str, message: str) -> Dict[str, Any]:
    event: Dict[str, Any] = {"header": alexa_header("Alexa", "ErrorResponse", header.get("correlationToken")),
                             "payload": {"type": kind, "message": message}}
    if endpoint.get("endpointId"):
        event["endpoint"] = {"endpointId": endpoint["endpointId"]}
    return {"event": event}


def is_local_address(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


class Guard:
    """Keeps abusive internet addresses away from the strip port and the password check."""

    def __init__(self):
        self.blocked: Dict[str, Tuple[float, str]] = {}           # ip -> (until, reason)
        self.open: Dict[str, int] = {}                            # ip -> open strip connections
        self.connects: Dict[str, List[float]] = {}                # ip -> recent connection times
        self.strikes: Dict[str, List[float]] = {}                 # ip -> recent misbehaviour
        self.login_fails: Dict[str, Dict[str, float]] = {}        # ip -> {wrong password hash: when}
        self.signups: Dict[str, List[float]] = {}                 # ip -> times an account was made
        self.all_signups: List[float] = []                        # every account made, from anywhere
        self.account_fails: Dict[str, List[float]] = {}           # login -> wrong passwords, from any address
        self.refused = 0
        self.refusal_logged: Dict[str, float] = {}                # ip -> when "turned away" was last logged
        self.lock = threading.Lock()                              # the web side runs in other threads

    @staticmethod
    def _recent(times: List[float], window: float, now: float) -> List[float]:
        times[:] = [t for t in times if now - t < window]
        return times

    def is_blocked(self, ip: str, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        with self.lock:
            entry = self.blocked.get(ip)
            if entry and entry[0] > now:
                return True
            if entry:
                del self.blocked[ip]
            return False

    def block(self, ip: str, reason: str, seconds: float = BLOCK_SECONDS, now: Optional[float] = None) -> None:
        if is_local_address(ip):
            return
        now = time.time() if now is None else now
        if not self.is_blocked(ip, now):
            log("[guard] blocked %s for %d min: %s" % (ip, seconds // 60, reason))
        with self.lock:
            self.blocked[ip] = (now + seconds, reason)

    def strike(self, ip: str, reason: str, now: Optional[float] = None) -> None:
        """Something an honest strip never does; a few of these get the address blocked."""
        if is_local_address(ip):
            return
        now = time.time() if now is None else now
        log("[guard] %s: %s" % (ip, reason))
        if len(self._recent(self.strikes.setdefault(ip, []), 600, now) + [now]) >= MAX_STRIKES:
            self.block(ip, reason, now=now)
        self.strikes[ip].append(now)

    def connection_opened(self, ip: str, now: Optional[float] = None) -> bool:
        """False when this connection must be dropped straight away."""
        now = time.time() if now is None else now
        if self.is_blocked(ip, now):
            self.refused += 1
            with self.lock:
                entry = self.blocked.get(ip)
                if entry and now - self.refusal_logged.get(ip, 0) >= 600:
                    self.refusal_logged[ip] = now
                    log("[guard] turned away %s (blocked for %d more min: %s)" % (ip, (entry[0] - now) // 60 + 1, entry[1]))
            return False
        if is_local_address(ip):
            return True
        recent = self._recent(self.connects.setdefault(ip, []), 60, now)
        recent.append(now)
        if len(recent) > MAX_CONNECTS_PER_MINUTE:
            self.block(ip, "%d connections in a minute" % len(recent), now=now)
            self.refused += 1
            return False
        if self.open.get(ip, 0) >= MAX_CONNECTIONS_PER_IP:
            self.strike(ip, "too many open connections", now)
            self.refused += 1
            return False
        self.open[ip] = self.open.get(ip, 0) + 1
        return True

    def connection_closed(self, ip: str) -> None:
        if ip in self.open:
            self.open[ip] -= 1
            if self.open[ip] <= 0:
                del self.open[ip]

    def may_add_strip(self, ip: str, unknown_now: int, now: Optional[float] = None) -> bool:
        """Can [ip] bring in a strip nobody approved or announced? Only when the waiting list is absurdly
        long is that one strip refused, never the address: the home's own strips and app keep working."""
        if is_local_address(ip):
            return True
        if unknown_now >= MAX_UNKNOWN_STRIPS:
            log("[guard] new strip from %s refused: %d unknown strips waiting already" % (ip, unknown_now))
            return False
        return True

    def login_failed(self, ip: str, offered: List[str], now: Optional[float] = None) -> None:
        """Counts different wrong passwords: an app still sending an old one is not guessing."""
        if is_local_address(ip):
            return
        now = time.time() if now is None else now
        with self.lock:
            fails = self.login_fails.setdefault(ip, {})
            for key in [k for k, t in fails.items() if now - t >= 300]:
                del fails[key]
            for token in offered:
                if token:
                    fails[hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()[:16]] = now
            count = len(fails)
        if count >= LOGIN_FAILS:
            self.block(ip, "%d wrong passwords" % count, LOGIN_BLOCK_SECONDS, now)

    def may_sign_up(self, ip: str, now: Optional[float] = None) -> bool:
        if is_local_address(ip):
            return True
        now = time.time() if now is None else now
        with self.lock:
            recent = self._recent(self.signups.setdefault(ip, []), 3600, now)
            everyone = self._recent(self.all_signups, 3600, now)
            if len(recent) >= SIGNUPS_PER_HOUR or len(everyone) >= SIGNUPS_PER_HOUR_ALL:
                return False
            recent.append(now)
            everyone.append(now)
            return True

    def account_paused(self, login: str, now: Optional[float] = None) -> bool:
        """Too many wrong passwords for this account lately, from any number of addresses."""
        now = time.time() if now is None else now
        with self.lock:
            return len(self._recent(self.account_fails.get(login, []), 900, now)) >= ACCOUNT_FAILS

    def account_failed(self, login: str, now: Optional[float] = None) -> None:
        if not login:
            return
        now = time.time() if now is None else now
        with self.lock:
            if len(self.account_fails) > 5000:                  # someone trying endless made-up logins
                self.account_fails = {k: v for k, v in self.account_fails.items() if v and now - v[-1] < 900}
            fails = self._recent(self.account_fails.setdefault(login, []), 900, now)
            fails.append(now)
            if len(fails) == ACCOUNT_FAILS:
                log("[guard] account %s paused for 15 min: %d wrong passwords" % (login[:3] + "…", len(fails)))

    def report(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        with self.lock:
            blocked = sorted(self.blocked.items())
        return {
            "blocked": [{"ip": ip, "minutes_left": int((until - now) // 60) + 1, "reason": reason}
                        for ip, (until, reason) in blocked if until > now],
            "refused_connections": self.refused,
        }


class Hub:
    def __init__(self, store: Store, history: History):
        self.store = store
        self.history = history
        self.strips: Dict[str, Strip] = {}
        self.alerted_offline: Dict[str, bool] = {}
        self.last_alert: Dict[Tuple[str, str, int], float] = {}
        self.schedule_fired: Dict[str, str] = {}
        self.cycle_phase: Dict[str, bool] = {}
        # monitoring rules: (rule id, outlet) -> when its condition started holding; "fired" once acted on
        self.watch_since: Dict[Tuple[str, int], Any] = {}
        self.pin_failures: Dict[str, Tuple[int, float]] = {}
        self.user_seen: Dict[str, float] = {}
        self.user_ips: Dict[str, Dict[str, float]] = {}     # member id -> internet addresses their app used lately
        self.ips_lock = threading.Lock()                    # written from the web threads
        self.guard = Guard()
        self.talker_logged: Dict[str, float] = {}
        for mac, entry in store.waiting.items():        # strips waiting for approval stay listed after a restart
            if not store.is_approved(mac) and mac not in store.blocked_strips:
                strip = self.strips[mac] = Strip(mac)
                strip.mac2, strip.address = entry.get("mac2", ""), entry.get("address", "")
                strip.first_seen, strip.last_seen = entry.get("first", 0), entry.get("last", 0)

    def unknown_talker(self, ip: str, heard: str) -> None:
        """A connection to the strip port that never said it is a strip (logged at most every 10 minutes)."""
        now = time.time()
        if now - self.talker_logged.get(ip, 0) >= 600:
            self.talker_logged[ip] = now
            log("[strip] %s connected but never said it is a strip, even when asked; it sent: %s"
                % (ip, repr(heard) if heard else "nothing"))

    @staticmethod
    def label(strip: Strip) -> str:
        return "strip %s" % strip.mac[-6:]

    def attach(self, link: StripLink, boot: Dict[str, str]) -> Optional[Strip]:
        """The strip behind [link], or None when the connection is refused."""
        if boot["mac"] in self.store.blocked_strips:
            log("[strip] refused blocked strip %s from %s" % (boot["mac"][-6:], link.address))
            return None
        strip = self.strips.get(boot["mac"])
        if strip is not None and strip.online and strip.link is not link and strip.link.address != link.address:
            # an online strip does not move to another address; someone is impersonating it
            self.guard.strike(link.address, "claimed to be %s, which is online from another address" % self.label(strip))
            return None
        if strip is None:
            # only strips nobody approved or announced count towards the limits (all of a customer's strips
            # reconnect at once after a restart; one announced from the app is expected)
            macs = [boot["mac"], boot.get("mac2", "")]
            if not self.store.knows(boot["mac"]) and not self.store.expects(macs, time.time()):
                unknown = sum(1 for s in self.strips.values() if not self.store.knows(s.mac))
                if not self.guard.may_add_strip(link.address, unknown):
                    return None
            strip = self.strips[boot["mac"]] = Strip(boot["mac"])
        if strip.link is not None and strip.link is not link:
            strip.link.close()                      # the strip re-dialled; the old socket is dead
        strip.model, strip.fw, strip.address = boot["model"], boot["fw"], link.address
        strip.mac2 = boot.get("mac2", "")
        strip.link, strip.last_seen = link, time.time()
        log("[strip] %s online (model %s, firmware %s, from %s)" % (self.label(strip), strip.model, strip.fw, link.address))
        if not self.store.is_approved(strip.mac):
            by = self.store.claim_expected([strip.mac, boot.get("mac2", "")], time.time())
            if by is not None or is_local_address(link.address):
                self.store.approve(strip.mac, by or "")
                log("[strip] %s approved (%s)" % (self.label(strip),
                                                  "set up from the app" if by is not None else "home network"))
            else:
                strip.first_seen = self.store.note_waiting(strip.mac, strip.mac2, strip.address, time.time())
                log("[strip] %s waits for the owner's approval (MACs %s %s)"
                    % (self.label(strip), strip.mac, boot.get("mac2", "")))
        return strip

    def detach(self, link: StripLink) -> None:
        strip = link.strip
        if strip is not None and strip.link is link:
            strip.link = None
            log("[strip] %s offline" % self.label(strip))
            out = self.store.outages.get(strip.mac)
            if self.store.is_approved(strip.mac) and (out is None or out.get("back")):
                # remember what was on; if the power was cut, the strip comes back with every outlet off
                self.store.outages[strip.mac] = {"since": int(time.time()),
                                                 "on": [n for n, o in strip.outlets.items() if o.on]}
                self.store.save()
            entry = self.store.waiting.get(strip.mac)
            if entry is not None and not self.store.is_approved(strip.mac):
                entry["last"] = strip.last_seen
                self.store.save()

    async def accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else "?"
        if not self.guard.connection_opened(ip):
            writer.close()
            return
        try:
            await StripLink(self, reader, writer).run()
        finally:
            self.guard.connection_closed(ip)

    async def came_online(self, link: "StripLink") -> None:
        """Read the state, then run whatever was asked while the strip was offline."""
        state_read = await link.read_state()
        strip = link.strip
        if strip is None:
            return
        queued = self.store.take_pending(strip.mac, time.time())
        self.after_outage(strip, time.time(), list(queued), known=state_read)
        for on in (True, False):
            outlets = [n for n, want in queued.items() if want == on]
            if outlets and not link.closed:
                targets = list(OUTLETS) if 0 in outlets else outlets
                ok = await link.switch(targets, on)
                log("[queue] %s outlets %s -> %s (%s)" % (self.label(strip), targets, "on" if on else "off",
                                                          "done" if ok else "not confirmed"))

    def after_outage(self, strip: Strip, now: float, queued: Optional[List[int]] = None, known: bool = True) -> None:
        """Back after going offline. Outlets that were on and are now all off mean the power was cut (the strip
        starts with every outlet off): say the power is back, and line up the outlets set to turn back on."""
        out = self.store.outages.get(strip.mac)
        if not out or out.get("back"):
            return
        out["back"] = int(now)
        was_on = [n for n in out.get("on", []) if n in strip.outlets]
        if not known or not was_on or any(o.on for o in strip.outlets.values()):
            self.store.save()                       # only the network dropped: nothing to do
            return
        self.alerted_offline.pop(strip.mac, None)   # this message says it is back, not a separate "online"
        minutes_off = max(0.0, (now - out.get("since", now)) / 60.0)
        self.history.add_event("power_back", strip.mac, 0, round(minutes_off, 1), now=now)
        log("[power] %s: power back after %.0f min; outlets %s were on" % (self.label(strip), minutes_off, was_on))
        for n in was_on:
            delay = self.store.after_power(strip.mac, n)
            if delay is not None and not (queued and (n in queued or 0 in queued)):
                self.store.restores["%s/%d" % (strip.mac, n)] = now + delay * 60
        self.store.save()

    async def run_restores(self, now: Optional[float] = None) -> None:
        """Turns back on the outlets whose wait after a power cut is over."""
        now = time.time() if now is None else now
        for key, at in list(self.store.restores.items()):
            if at > now:
                continue
            mac, _, n = key.partition("/")
            strip = self.find(mac)
            if strip is None:
                self.store.restores.pop(key, None)
                self.store.save()
                continue
            if not strip.online:
                if now - at > POWER_BACK_KEEP:          # gone again for long: forget it
                    self.store.restores.pop(key, None)
                    self.store.save()
                continue
            self.store.restores.pop(key, None)
            self.store.save()
            try:
                code, body = await self.switch(mac, int(n), True, internal=True)
            except (ConnectionError, OSError) as err:
                body = {"error": str(err)}
            delay = self.store.after_power(mac, int(n)) or 0
            self.history.add_event("power_restore", mac, int(n), delay, now=now)
            log("[power] %s outlet %s back on (%s)" % (self.label(strip), n, body.get("error", "sent")))

    def cancel_restores(self, mac: str, outlets: List[int]) -> None:
        """Someone switched these outlets by hand: their own wait after a power cut no longer applies."""
        keys = [k for k in self.store.restores if k.partition("/")[0] == mac
                and (0 in outlets or int(k.partition("/")[2]) in outlets)]
        for k in keys:
            self.store.restores.pop(k, None)
        if keys:
            self.store.save()

    async def restore_after_power(self, strip_id: Any, pin: Any = None) -> Tuple[int, Dict[str, Any]]:
        """"Turn back on what was on": the outlets that were on before the power cut."""
        strip = self.find(strip_id)
        if strip is None:
            return 404, {"error": "unknown strip"}
        back = strip.power_back(self.store)
        if not back:
            return 409, {"error": "nothing to turn back on"}
        return await self.switch(strip.mac, 0, True, outlets=back["on"], pin=pin)

    def check_pin(self, strip: Strip, pin: Any) -> Optional[Tuple[int, Dict[str, Any]]]:
        """None when the strip is not locked or the PIN is right, else the error reply."""
        if not self.store.locked(strip.mac):
            return None
        tries, since = self.pin_failures.get(strip.mac, (0, 0.0))
        if tries >= PIN_MAX_TRIES and time.time() - since < 60:
            return 429, {"error": "too many wrong PINs, wait a minute", "locked": True}
        if pin is not None and self.store.pin_ok(strip.mac, str(pin)):
            self.pin_failures.pop(strip.mac, None)
            return None
        if pin is not None:
            self.pin_failures[strip.mac] = (tries + 1 if time.time() - since < 60 else 1, time.time())
            return 403, {"error": "wrong PIN", "locked": True}
        return 403, {"error": "PIN required", "locked": True}

    def record_energy(self, strip: Strip, now: Optional[float] = None) -> None:
        if not self.store.is_approved(strip.mac):
            return
        try:
            self.history.record(strip.mac, {o.index: round(o.kwh * 1000) for o in strip.outlets.values()}, now)
        except sqlite3.Error as err:
            log("[history] could not record: %s" % err)

    def strip_json(self, strip: Strip) -> Dict[str, Any]:
        return strip.to_json(self.store, self.today_by_outlet())

    def today_by_outlet(self, now: Optional[float] = None) -> Dict[Tuple[str, int], float]:
        totals: Dict[Tuple[str, int], float] = {}
        for mac, outlet, _, wh in self.history.rows_since(local_midnight(time.time() if now is None else now)):
            totals[(mac, outlet)] = totals.get((mac, outlet), 0.0) + wh
        return totals

    def cost(self, kwh: float, price: Optional[float] = None) -> float:
        if price is None:
            price = float(self.store.settings.get("price_kwh", 0))
        return round(kwh * price, 2)

    # ---- whose account a person acts for, and that account's own bill settings

    def customer(self, account_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not account_id:
            return None
        return next((u for u in self.store.users if u["id"] == account_id and u.get("role") == CUSTOMER), None)

    def person(self, user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """What a signed-in account may do: role, name, the customer account it acts for, and the strips
        it sees (None = all). Someone a customer invited sees exactly that customer's strips, never more."""
        strips = set(user.get("strips") or [])
        if user.get("parent"):
            parent = self.customer(user["parent"])
            if parent is None or user.get("role") not in ROLES:
                return None
            return {"role": user["role"], "name": user["name"], "id": user["id"], "login": "",
                    "account": parent["id"], "home": parent["name"], "strips": set(parent.get("strips") or [])}
        if user.get("role") == CUSTOMER:
            return {"role": CUSTOMER, "name": user["name"], "id": user["id"], "login": user.get("login", ""),
                    "account": user["id"], "strips": strips}
        return {"role": user["role"], "name": user["name"], "id": user["id"], "login": user.get("login", ""),
                "account": None, "strips": strips or None}

    def settings_for(self, account_id: Optional[str]) -> Dict[str, Any]:
        """The owner's settings, with a customer's own price and alert limits on top."""
        settings = dict(self.store.settings)
        user = self.customer(account_id)
        if user:
            settings.update({k: v for k, v in (user.get("settings") or {}).items() if k in SETTING_LIMITS})
        return settings

    def strip_limits(self) -> Dict[str, Dict[str, Any]]:
        """Alert limits per strip: its customer's own, where they set some."""
        out: Dict[str, Dict[str, Any]] = {}
        for u in self.store.users:
            if u.get("role") == CUSTOMER and u.get("settings"):
                for mac in u.get("strips") or []:
                    out.setdefault(mac, self.settings_for(u["id"]))
        return out

    async def update_my_settings(self, account_id: str, req: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """A customer sets their own price per kWh and alert limits; "reset" goes back to the owner's."""
        user = self.customer(account_id)
        if user is None:
            return 403, {"error": "only a customer account has its own settings"}
        mine = {} if req.get("reset") else dict(user.get("settings") or {})
        for key, (low, high) in SETTING_LIMITS.items():
            if key in req:
                value = float(req[key])
                if not low <= value <= high:
                    return 400, {"error": "%s must be between %s and %s" % (key, low, high)}
                mine[key] = value
        if mine:
            user["settings"] = mine
        else:
            user.pop("settings", None)
        self.store.save()
        return 200, {"ok": True, "settings": self.public_settings(account_id)}

    def public_settings(self, account_id: Optional[str]) -> Dict[str, Any]:
        settings = self.settings_for(account_id)
        out = {k: settings.get(k) for k in ("price_kwh", "currency", "max_temp_c", "max_watts")}
        out["own"] = bool((self.customer(account_id) or {}).get("settings"))
        return out

    # ---- a customer's household: people they invite, who see and control (or only see) their strips

    def member_json(self, user: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": user["id"], "name": user["name"], "role": user["role"], "created": user.get("created", 0),
                "last_seen": int(max(self.user_seen.get(user["id"], 0), user.get("last_seen", 0)))}

    async def household(self, account_id: str) -> Dict[str, Any]:
        return {"members": [self.member_json(u) for u in self.store.users if u.get("parent") == account_id]}

    async def household_add(self, account_id: str, req: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        if self.customer(account_id) is None:
            return 403, {"error": "only a customer account can invite people"}
        name = str(req.get("name") or "").strip()[:30]
        role = req.get("role", "control")
        if not name:
            return 400, {"error": "the person needs a name"}
        if role not in ROLES:
            return 400, {"error": "role must be control or view"}
        if sum(1 for u in self.store.users if u.get("parent") == account_id) >= MAX_HOUSEHOLD:
            return 400, {"error": "at most %d people per home" % MAX_HOUSEHOLD}
        token = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
        user = {"id": os.urandom(4).hex(), "name": name, "role": role, "parent": account_id,
                "token_hash": hashlib.sha256(token.encode()).hexdigest(), "created": int(time.time())}
        self.store.users.append(user)
        self.store.save()
        log("[account] customer %s invited %s (%s)" % (account_id, user["id"], role))
        # the invite code is shown this once; only its hash is kept
        return 200, {"ok": True, "member": self.member_json(user), "token": token, **(await self.household(account_id))}

    async def household_update(self, account_id: str, req: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        user = next((u for u in self.store.users if u["id"] == req.get("id") and u.get("parent") == account_id), None)
        if user is None:
            return 404, {"error": "no such person in your home"}
        reply: Dict[str, Any] = {"ok": True}
        if "name" in req:
            name = str(req.get("name") or "").strip()[:30]
            if not name:
                return 400, {"error": "the person needs a name"}
            user["name"] = name
        if "role" in req:
            if req["role"] not in ROLES:
                return 400, {"error": "role must be control or view"}
            user["role"] = req["role"]
        if req.get("new_token"):                    # a new invite code; the old one stops working
            token = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
            user["token_hash"] = hashlib.sha256(token.encode()).hexdigest()
            reply["token"] = token
        self.store.save()
        reply["member"] = self.member_json(user)
        reply.update(await self.household(account_id))
        return 200, reply

    async def household_delete(self, account_id: str, member_id: str) -> Tuple[int, Dict[str, Any]]:
        before = len(self.store.users)
        self.store.users = [u for u in self.store.users if not (u["id"] == member_id and u.get("parent") == account_id)]
        if len(self.store.users) == before:
            return 404, {"error": "no such person in your home"}
        self.store.save()
        return 200, {"ok": True, **(await self.household(account_id))}

    # ---- used by the web side (always on the event loop)

    async def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        today = self.today_by_outlet(now)
        midnight = local_midnight(now)
        hours = [0.0] * 24
        for _, _, hour, wh in self.history.rows_since(midnight):
            index = int((hour - midnight) // 3600)
            if 0 <= index < 24:
                hours[index] += wh / 1000.0
        month_start = time.mktime(datetime.date.fromtimestamp(now).replace(day=1).timetuple())
        month_kwh = sum(wh for *_, wh in self.history.rows_since(month_start)) / 1000.0
        today_kwh = sum(today.values()) / 1000.0
        strips = sorted((x for x in self.strips.values() if self.store.is_approved(x.mac)),
                        key=lambda s: ((self.store.room(s.mac) or "~").lower(), s.mac))
        waiting = [x for x in self.strips.values() if not self.store.is_approved(x.mac)]
        return {
            "strips": [s.to_json(self.store, today) for s in strips],
            "new_strips": [{"id": x.mac, "address": x.address, "online": x.online, "model": x.model,
                            "last_seen": int(x.last_seen)} for x in sorted(waiting, key=lambda s: s.mac)],
            "blocked_strips": list(self.store.blocked_strips),
            "schedules": self.schedules_json(now),
            "scenes": self.store.scenes,
            "settings": self.store.settings,
            "today": {"kwh": round(today_kwh, 3), "cost": self.cost(today_kwh),
                      "hours": [round(h, 3) for h in hours]},
            "month": {"kwh": round(month_kwh, 3), "cost": self.cost(month_kwh)},
            "last_event": self.history.last_event_id(),
        }

    async def history_report(self, rng: str, strip_id: Optional[str] = None, now: Optional[float] = None,
                             allowed: Optional[set] = None, account: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
        now = time.time() if now is None else now
        mac = None
        if strip_id:
            strip = self.find(strip_id)
            if strip is None:
                return 404, {"error": "unknown strip"}
            mac = strip.mac
        if rng == "day":
            start = local_midnight(now)
            labels = [int(start + h * 3600) for h in range(24)]
        elif rng in ("week", "month"):
            days = 7 if rng == "week" else 30
            start = local_midnight(now, days - 1)
            labels = [int(local_midnight(now, days - 1 - d)) for d in range(days)]
        else:
            return 400, {"error": "range must be day, week or month"}
        buckets = [0.0] * len(labels)
        by_outlet: Dict[Tuple[str, int], float] = {}
        first_day = datetime.date.fromtimestamp(start)
        for m, outlet, hour, wh in self.history.rows_since(start, mac):
            if allowed is not None and m not in allowed:
                continue
            if rng == "day":
                index = int((hour - start) // 3600)
            else:
                index = (datetime.date.fromtimestamp(hour) - first_day).days
            if 0 <= index < len(buckets):
                buckets[index] += wh / 1000.0
            by_outlet[(m, outlet)] = by_outlet.get((m, outlet), 0.0) + wh / 1000.0
        total = sum(buckets)
        settings = self.settings_for(account)
        price = float(settings.get("price_kwh", 0) or 0)
        return 200, {
            "range": rng,
            "buckets": [{"t": t, "kwh": round(v, 3)} for t, v in zip(labels, buckets)],
            "total_kwh": round(total, 3),
            "cost": self.cost(total, price),
            "currency": settings.get("currency", "EGP"),
            "price_kwh": price,
            "by_outlet": [{"strip": m, "outlet": o, "kwh": round(v, 3), "cost": self.cost(v, price)}
                          for (m, o), v in sorted(by_outlet.items(), key=lambda kv: -kv[1])],
        }

    async def events(self, after: int) -> Dict[str, Any]:
        return {"events": self.history.events_after(after), "last_id": self.history.last_event_id()}

    EXPORT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}

    async def history_xlsx(self, rng: str, strip_id: Optional[str] = None, lang: str = "ar",
                           allowed: Optional[set] = None, now: Optional[float] = None,
                           account: Optional[str] = None) -> Tuple[int, Any]:
        """The energy log as an Excel file: a summary, one row per day and outlet, and (up to a month)
        one row per hour and outlet. Labels in Arabic or English."""
        now = time.time() if now is None else now
        if rng not in self.EXPORT_DAYS:
            return 400, {"error": "range must be day, week, month or year"}
        mac = None
        if strip_id:
            strip = self.find(strip_id)
            if strip is None:
                return 404, {"error": "unknown strip"}
            mac = strip.mac
        ar = lang != "en"
        L = (lambda a, e: a if ar else e)
        start = local_midnight(now, self.EXPORT_DAYS[rng] - 1)
        rows = [r for r in self.history.rows_since(start, mac) if allowed is None or r[0] in allowed]
        rows.sort(key=lambda r: (r[2], r[0], r[1]))
        price = float(self.settings_for(account).get("price_kwh", 0) or 0)
        currency = self.store.settings.get("currency", "EGP")
        strip_name = lambda m: self.store.name(m, 0) or L("مشترك %s", "Strip %s") % m[-6:]
        outlet_name = lambda m, o: self.store.name(m, o) or L("مخرج %d", "Outlet %d") % o
        cost = L("التكلفة (%s)", "Cost (%s)") % currency

        daily: Dict[Tuple[str, str, int], float] = {}
        for m, o, hour, wh in rows:
            key = (time.strftime("%Y-%m-%d", time.localtime(hour)), m, o)
            daily[key] = daily.get(key, 0.0) + wh / 1000.0
        day_rows = [[L("اليوم", "Day"), L("المشترك", "Strip"), L("المخرج", "Outlet"), L("كيلووات ساعة", "kWh"), cost]]
        day_rows += [[d, strip_name(m), outlet_name(m, o), round(k, 3), round(k * price, 2)]
                     for (d, m, o), k in sorted(daily.items())]
        total = sum(daily.values())
        per_outlet: Dict[Tuple[str, int], float] = {}
        for (_, m, o), k in daily.items():
            per_outlet[(m, o)] = per_outlet.get((m, o), 0.0) + k
        summary = [[L("البند", "Item"), L("القيمة", "Value")],
                   [L("الفترة", "Period"), L({"day": "النهارده", "week": "آخر 7 أيام", "month": "آخر 30 يوم",
                                               "year": "آخر سنة"}[rng], {"day": "Today", "week": "Last 7 days",
                                               "month": "Last 30 days", "year": "Last year"}[rng])],
                   [L("من", "From"), time.strftime("%Y-%m-%d", time.localtime(start))],
                   [L("لحد", "To"), time.strftime("%Y-%m-%d %H:%M", time.localtime(now))],
                   [L("إجمالي الاستهلاك (كيلووات ساعة)", "Total energy (kWh)"), round(total, 3)],
                   [L("سعر الكيلووات", "Price per kWh"), price],
                   [cost, round(total * price, 2)], ["", ""],
                   [L("المخرج", "Outlet"), L("كيلووات ساعة", "kWh")]]
        summary += [["%s · %s" % (strip_name(m), outlet_name(m, o)), round(k, 3)]
                    for (m, o), k in sorted(per_outlet.items(), key=lambda kv: -kv[1])]
        sheets = [(L("ملخص", "Summary"), summary, [34, 22]), (L("يومي", "Daily"), day_rows, [14, 22, 18, 14, 14])]
        if rng != "year":
            hour_rows = [[L("اليوم", "Day"), L("الساعة", "Hour"), L("المشترك", "Strip"), L("المخرج", "Outlet"),
                          L("كيلووات ساعة", "kWh")]]
            hour_rows += [[time.strftime("%Y-%m-%d", time.localtime(h)), time.strftime("%H:00", time.localtime(h)),
                           strip_name(m), outlet_name(m, o), round(wh / 1000.0, 3)] for m, o, h, wh in rows]
            sheets.append((L("بالساعة", "Hourly"), hour_rows, [14, 9, 22, 18, 14]))
        return 200, xlsx_bytes(sheets, rtl=ar)

    # ---- family members

    def user_json(self, user: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": user["id"], "name": user["name"], "role": user["role"], "strips": user.get("strips", []),
                "created": user.get("created", 0), "last_seen": int(self.user_seen.get(user["id"], 0))}

    async def users(self) -> Dict[str, Any]:
        """Family members (customers are on the admin page)."""
        return {"users": [self.user_json(u) for u in self.store.users
                          if u.get("role") != CUSTOMER and not u.get("parent")]}

    def who_for(self, subject: str) -> Optional[Dict[str, Any]]:
        """The person behind a voice assistant link: "owner" or a member/customer id."""
        if subject == "owner":
            return OWNER
        user = next((u for u in self.store.users if u["id"] == subject), None)
        return self.person(user) if user is not None else None

    # ---- Alexa Smart Home (directives arrive through the skill's AWS Lambda, which only forwards them)

    async def alexa(self, request: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        directive = request.get("directive") or {}
        header = directive.get("header") or {}
        ns, name = header.get("namespace", ""), header.get("name", "")
        payload = directive.get("payload") or {}
        endpoint = directive.get("endpoint") or {}
        if ns == "Alexa.Authorization":
            token = (payload.get("grantee") or {}).get("token", "")
        else:
            token = (payload.get("scope") or endpoint.get("scope") or {}).get("token", "")
        subject = self.store.oauth_subject(token, now)
        who = self.who_for(subject) if subject else None
        if who is None:
            return alexa_error(header, endpoint, "INVALID_AUTHORIZATION_CREDENTIAL", "sign in to the skill again")
        if ns == "Alexa.Authorization" and name == "AcceptGrant":
            return {"event": {"header": alexa_header("Alexa.Authorization", "AcceptGrant.Response"), "payload": {}}}
        if ns == "Alexa.Discovery" and name == "Discover":
            return {"event": {"header": alexa_header("Alexa.Discovery", "Discover.Response"),
                              "payload": {"endpoints": self.alexa_endpoints(who)}}}
        mac, _, part = str(endpoint.get("endpointId", "")).partition("-")
        strip = self.find(mac)
        if strip is None or not may_see(who, strip.mac) or self.store.locked(strip.mac) \
                or not part.isdigit() or int(part) not in (0,) + OUTLETS:
            return alexa_error(header, endpoint, "NO_SUCH_ENDPOINT", "this outlet is not on your account")
        outlet = int(part)
        if ns == "Alexa.PowerController" and name in ("TurnOn", "TurnOff"):
            if who["role"] == "view":
                return alexa_error(header, endpoint, "INVALID_DIRECTIVE", "view-only access")
            if not strip.online:
                return alexa_error(header, endpoint, "ENDPOINT_UNREACHABLE", "the strip is offline")
            code, body = await self.switch(strip.mac, outlet, name == "TurnOn")
            if code >= 400:
                return alexa_error(header, endpoint, "INTERNAL_ERROR", str(body.get("error", "")))
            return alexa_reply(header, endpoint, "Alexa", "Response", self.alexa_properties(strip, outlet))
        if ns == "Alexa" and name == "ReportState":
            return alexa_reply(header, endpoint, "Alexa", "StateReport", self.alexa_properties(strip, outlet))
        return alexa_error(header, endpoint, "INVALID_DIRECTIVE", "not supported: %s.%s" % (ns, name))

    def alexa_properties(self, strip: Strip, outlet: int) -> List[Dict[str, Any]]:
        on = any(o.on for o in strip.outlets.values()) if outlet == 0 else strip.outlets[outlet].on
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.00Z")
        return [
            {"namespace": "Alexa.PowerController", "name": "powerState", "value": "ON" if on else "OFF",
             "timeOfSample": stamp, "uncertaintyInMilliseconds": 500},
            {"namespace": "Alexa.EndpointHealth", "name": "connectivity",
             "value": {"value": "OK" if strip.online else "UNREACHABLE"}, "timeOfSample": stamp,
             "uncertaintyInMilliseconds": 500},
        ]

    def alexa_endpoints(self, who: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Every outlet (and each whole strip) the person may see; strips locked with a PIN stay out."""
        out = []
        for strip in sorted(self.strips.values(), key=lambda x: x.mac):
            if not self.store.is_approved(strip.mac) or not may_see(who, strip.mac) or self.store.locked(strip.mac):
                continue
            strip_name = self.store.name(strip.mac, 0) or "مشترك %s" % strip.mac[-4:]
            for n in (0,) + OUTLETS:
                name = strip_name if n == 0 else (self.store.name(strip.mac, n) or "%s مخرج %d" % (strip_name, n))
                out.append({
                    "endpointId": "%s-%d" % (strip.mac, n),
                    "manufacturerName": "Darwish Tech",
                    "description": "Darwish Smart Power" + (" - all outlets" if n == 0 else " outlet"),
                    "friendlyName": name[:120],
                    "displayCategories": ["SMARTPLUG"],
                    "capabilities": [
                        {"type": "AlexaInterface", "interface": "Alexa", "version": "3"},
                        {"type": "AlexaInterface", "interface": "Alexa.PowerController", "version": "3",
                         "properties": {"supported": [{"name": "powerState"}], "proactivelyReported": False,
                                        "retrievable": True}},
                        {"type": "AlexaInterface", "interface": "Alexa.EndpointHealth", "version": "3.2",
                         "properties": {"supported": [{"name": "connectivity"}], "proactivelyReported": False,
                                        "retrievable": True}},
                    ],
                })
        return out

    def saw_user(self, user: Dict[str, Any], ip: str = "") -> None:
        now = time.time()
        self.user_seen[user["id"]] = now
        if ip and not is_local_address(ip):
            with self.ips_lock:
                ips = self.user_ips.setdefault(user["id"], {})
                ips[ip] = now
                for old in sorted(ips, key=ips.get)[:-8]:
                    del ips[old]
        if now - user.get("last_seen", 0) > 300:   # remembered across restarts, written at most every 5 minutes
            user["last_seen"] = int(now)
            self.store.save()

    async def sign_up(self, req: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        if not self.store.settings.get("signup", True):
            return 403, {"error": "new accounts are closed"}
        name = str(req.get("name") or "").strip()[:30]
        login = normalise_login(req.get("login"))
        password = str(req.get("password") or "")
        if not name:
            return 400, {"error": "name required", "field": "name"}
        if login is None:
            return 400, {"error": "enter a phone number or an e-mail address", "field": "login"}
        if not MIN_PASSWORD <= len(password) <= 128:
            return 400, {"error": "the password needs at least %d characters" % MIN_PASSWORD, "field": "password"}
        if self.store.account_by_login(login):
            return 409, {"error": "an account with this phone or e-mail exists; sign in instead", "field": "login"}
        user = {"id": os.urandom(4).hex(), "name": name, "role": CUSTOMER, "login": login,
                "pw": req.get("pw") or hash_password(password), "strips": [], "created": int(time.time()), "sessions": []}
        self.store.users.append(user)
        token = self.store.new_session(user)
        log("[account] new customer %s" % user["id"])
        return 200, {"ok": True, "token": token, "me": {"role": CUSTOMER, "name": name}}

    async def sign_in(self, user: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """A new session for a customer whose password was already checked."""
        if user not in self.store.users:
            return 401, {"error": "wrong phone/e-mail or password"}
        token = self.store.new_session(user)
        return 200, {"ok": True, "token": token, "me": {"role": CUSTOMER, "name": user["name"]}}

    async def set_customer_password(self, user_id: str, pw_hash: str) -> Tuple[int, Dict[str, Any]]:
        """The owner gives a customer who forgot theirs a new password; their phones are signed out."""
        user = next((u for u in self.store.users if u["id"] == user_id and u.get("role") == CUSTOMER), None)
        if user is None:
            return 404, {"error": "no such customer"}
        if not pw_hash:
            return 400, {"error": "the password needs at least %d characters" % MIN_PASSWORD}
        user["pw"], user["sessions"] = pw_hash, []
        self.guard.account_fails.pop(user.get("login", ""), None)   # a paused account can sign in at once
        self.store.save()
        log("[account] owner set a new password for customer %s" % user_id)
        return 200, {"ok": True}

    async def delete_account(self, user_id: str) -> Tuple[int, Dict[str, Any]]:
        """A customer closes their account; strips nobody else has are removed with it."""
        user = next((u for u in self.store.users if u["id"] == user_id and u.get("role") == CUSTOMER), None)
        if user is None:
            return 404, {"error": "no such account"}
        self.store.users = [u for u in self.store.users if u is not user and u.get("parent") != user_id]
        others = {m for u in self.store.users for m in (u.get("strips") or [])}
        for mac in user.get("strips") or []:
            if mac not in others:
                await self.remove_strip(mac, block=False)
        self.store.save()
        log("[account] customer %s deleted their account" % user_id)
        return 200, {"ok": True}

    async def admin_report(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Everything the owner's admin page shows."""
        now = time.time() if now is None else now
        store = self.store

        def seen(u: Dict[str, Any]) -> float:
            return max(self.user_seen.get(u["id"], 0), u.get("last_seen", 0))
        owners: Dict[str, List[str]] = {}
        customer_of: Dict[str, str] = {}
        for u in store.users:
            for mac in u.get("strips") or []:
                owners.setdefault(mac, []).append(u["name"])
                if u.get("role") == CUSTOMER:
                    customer_of[mac] = u["id"]
        customers = [u for u in store.users if u.get("role") == CUSTOMER]
        family = [u for u in store.users if u.get("role") != CUSTOMER and not u.get("parent")]
        approved = [m for m in store.approved if m not in store.blocked_strips]
        online = [m for m in approved if m in self.strips and self.strips[m].online]
        days = store.stats.get("download_days", {})
        last_days = [time.strftime("%Y-%m-%d", time.localtime(now - i * 86400)) for i in range(30)]
        return {
            "totals": {
                "downloads": int(store.stats.get("downloads", 0)),
                "downloads_today": int(days.get(last_days[0], 0)),
                "downloads_7d": sum(int(days.get(d, 0)) for d in last_days[:7]),
                "customers": len(customers),
                "new_customers_7d": sum(1 for u in customers if now - u.get("created", 0) < 7 * 86400),
                "active_1d": sum(1 for u in store.users if now - seen(u) < 86400),
                "active_7d": sum(1 for u in store.users if now - seen(u) < 7 * 86400),
                "family": len(family),
                "strips": len(approved),
                "online": len(online),
                "waiting": sum(1 for x in self.strips.values() if not store.is_approved(x.mac)),
                "blocked": len(store.blocked_strips),
            },
            "downloads_by_day": [{"day": d, "count": int(days.get(d, 0))} for d in reversed(last_days)],
            "customers": sorted(({
                "id": u["id"], "name": u["name"], "login": u.get("login", ""), "created": u.get("created", 0),
                "last_seen": int(seen(u)), "strips": len(u.get("strips") or []),
                "household": sum(1 for m in store.users if m.get("parent") == u["id"]),
                "own_price": (u.get("settings") or {}).get("price_kwh"),
                "online": sum(1 for m in u.get("strips") or [] if m in self.strips and self.strips[m].online),
            } for u in customers), key=lambda x: -x["last_seen"]),
            "strips": sorted(({
                "id": m, "name": store.name(m, 0) or "", "owners": owners.get(m, []), "customer": customer_of.get(m, ""),
                "online": m in online, "address": self.strips[m].address if m in self.strips else "",
                "last_seen": int(self.strips[m].last_seen) if m in self.strips else 0,
            } for m in approved), key=lambda x: (not x["online"], x["id"])),
            "new_strips": [{"id": x.mac, "mac2": x.mac2, "address": x.address, "online": x.online,
                            "first_seen": int(x.first_seen), "last_seen": int(x.last_seen), "guesses": self.owner_guesses(x, now)}
                           for x in self.strips.values() if not store.is_approved(x.mac)],
            "signup": bool(store.settings.get("signup", True)),
            "voice": {c: bool(store.oauth["clients"].get(c)) for c in OAUTH_CLIENTS},
            "reservations": sorted(({
                "code": code, "customer": next((u["name"] for u in customers if u["id"] == e.get("by")), "?"),
                "days_left": max(0, int((e.get("until", 0) - now) // 86400)),
            } for code, e in store.expected.items()
                if e.get("until", 0) > now and any(u["id"] == e.get("by") for u in customers)), key=lambda x: x["code"]),
        }

    def check_user_fields(self, req: Dict[str, Any], user: Dict[str, Any]) -> Optional[Tuple[int, Dict[str, Any]]]:
        if "name" in req:
            name = str(req.get("name") or "").strip()[:30]
            if not name:
                return 400, {"error": "the family member needs a name"}
            user["name"] = name
        if "role" in req:
            if req["role"] not in ROLES:
                return 400, {"error": "role must be control or view"}
            user["role"] = req["role"]
        if "strips" in req:
            strips = [str(x).upper() for x in (req.get("strips") or [])]
            unknown = [x for x in strips if not self.store.is_approved(x)]
            if unknown:
                return 400, {"error": "unknown strip %s" % unknown[0]}
            user["strips"] = sorted(set(strips))
        return None

    async def add_user(self, req: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        user: Dict[str, Any] = {"id": os.urandom(4).hex(), "name": "", "role": "control", "strips": [],
                                "created": int(time.time())}
        error = self.check_user_fields({"name": req.get("name", ""), **req}, user)
        if error:
            return error
        token = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
        user["token_hash"] = hashlib.sha256(token.encode()).hexdigest()
        self.store.users.append(user)
        self.store.save()
        # the token is shown this once; only its hash is kept
        return 200, {"ok": True, "user": self.user_json(user), "token": token, **(await self.users())}

    async def update_user(self, req: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        user = next((u for u in self.store.users if u["id"] == req.get("id")), None)
        if user is None:
            return 404, {"error": "unknown family member"}
        changed = dict(user)
        error = self.check_user_fields(req, changed)
        if error:
            return error
        reply: Dict[str, Any] = {"ok": True}
        if req.get("new_token"):
            token = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
            changed["token_hash"] = hashlib.sha256(token.encode()).hexdigest()
            reply["token"] = token
        user.update(changed)
        self.store.save()
        reply["user"] = self.user_json(user)
        reply.update(await self.users())
        return 200, reply

    async def delete_user(self, user_id: str) -> Tuple[int, Dict[str, Any]]:
        before = len(self.store.users)
        self.store.users = [u for u in self.store.users if u["id"] != user_id]
        if len(self.store.users) == before:
            return 404, {"error": "unknown family member"}
        self.store.save()
        return 200, {"ok": True, **(await self.users())}

    async def state_for(self, who: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        return self.filter_for(who, await self.snapshot(now), now)

    def filter_for(self, who: Dict[str, Any], snap: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
        """What a member limited to some strips may see of /api/state."""
        snap["me"] = {"role": who["role"], "name": who["name"], "strips": sorted(who["strips"]) if who["strips"] else [],
                      "login": who.get("login") or ""}
        if who.get("home"):                         # invited by a customer: whose home this is
            snap["me"]["home"] = who["home"]
        if who["role"] != "owner":                  # approving strips is the owner's job
            snap.pop("new_strips", None)
            snap.pop("blocked_strips", None)
            snap["settings"] = self.public_settings(who.get("account"))
        if who["strips"] is None:
            return snap
        snap["strips"] = [x for x in snap["strips"] if may_see(who, x["id"])]
        snap["schedules"] = [x for x in snap["schedules"] if may_see(who, x["strip"])]
        snap["scenes"] = [x for x in snap["scenes"] if all(may_see(who, a["strip"]) for a in x["actions"])]
        # today's totals for just their strips
        # today's hours and this month, for just their strips, at their own price
        now = time.time() if now is None else now
        price = float(self.settings_for(who.get("account")).get("price_kwh", 0) or 0)
        midnight = local_midnight(now)
        month_start = time.mktime(datetime.date.fromtimestamp(now).replace(day=1).timetuple())
        hours, month_wh = [0.0] * 24, 0.0
        for mac in sorted(who["strips"]):
            for _, _, hour, wh in self.history.rows_since(min(month_start, midnight), mac):
                if hour >= month_start:
                    month_wh += wh
                index = int((hour - midnight) // 3600)
                if 0 <= index < 24:
                    hours[index] += wh / 1000.0
        kwh = sum(x["today_kwh"] for x in snap["strips"])
        snap["today"] = {"kwh": round(kwh, 3), "cost": self.cost(kwh, price), "hours": [round(h, 3) for h in hours]}
        snap["month"] = {"kwh": round(month_wh / 1000.0, 3), "cost": self.cost(month_wh / 1000.0, price)}
        return snap

    def scene_strips(self, scene_id: str) -> List[str]:
        scene = next((x for x in self.store.scenes if x.get("id") == scene_id), None)
        return [a["strip"] for a in scene["actions"]] if scene else []

    def schedule_strip(self, sched_id: str) -> Optional[str]:
        sched = next((x for x in self.store.schedules if x.get("id") == sched_id), None)
        return sched["strip"] if sched else None

    def find(self, strip_id: Any) -> Optional[Strip]:
        strip = self.strips.get(str(strip_id).upper())
        return strip if strip is not None and self.store.is_approved(strip.mac) else None

    # ---- approving, removing and blocking strips

    async def expect_strip(self, code: Any, by: str, ip: str = "") -> Tuple[int, Dict[str, Any]]:
        code = str(code or "").strip().upper()
        if not re.fullmatch(r"[0-9A-Z]{4,16}", code):
            return 400, {"error": "code must be the 4-16 letters/digits after TONLY_TAP_"}
        now = time.time()
        if by:                                          # an account may not flood the list with codes
            recent = [a for a in self.store.announced if a.get("by") == by and now - a.get("at", 0) < 3600]
            if len(recent) >= ANNOUNCES_PER_HOUR:
                return 429, {"error": "too many strips set up in an hour, try again later"}
        self.store.expect(code, by, now)
        self.store.announce(code, by, ip, now)
        log("[strip] app is setting up TONLY_TAP_%s (%s, from %s)" % (code, "account " + by if by else "the owner", ip or "?"))
        # the strip may already be here, waiting (the app could not reach us before the setup, only after).
        # It is handed over at once only from the strip's own home network, so a neighbour who saw its
        # Wi-Fi name cannot take it; anyone else's claim waits for the owner.
        waiting = next((x for x in self.strips.values()
                        if not self.store.is_approved(x.mac) and code_matches(code, [x.mac, x.mac2])), None)
        entry = self.store.expected.get(code, {})
        if waiting is not None and not entry.get("conflict") and (not by or (ip and ip == waiting.address)):
            self.store.expected.pop(code, None)
            self.store.approve(waiting.mac, by)
            log("[strip] %s approved (set up from the app, announced after it connected)" % self.label(waiting))
            return 200, {"ok": True, "approved": True}
        if waiting is not None and code in self.store.expected:
            self.store.expected[code]["late"] = True    # nor when it reconnects later: the owner decides
            self.store.save()
        return 200, {"ok": True}

    def owner_guesses(self, strip: Strip, now: float) -> List[Dict[str, Any]]:
        """Who probably set up a strip that waits for approval, best first. Reasons: "code" their app set up a
        strip with this code; "app" their app set up a strip shortly before this one first connected;
        "network" their app is used from the same internet address as the strip."""
        users = {u["id"]: u for u in self.store.users}
        found: Dict[str, Dict[str, Any]] = {}

        def add(uid: str, why: str, points: int, announced: Optional[Dict[str, Any]] = None) -> None:
            if uid and uid not in users:
                return
            g = found.setdefault(uid, {"id": uid, "name": users[uid]["name"] if uid else "",
                                       "customer": bool(uid) and users[uid].get("role") == CUSTOMER,
                                       "why": [], "codes": [], "minutes": None, "points": 0})
            if why not in g["why"]:
                g["why"].append(why)
                g["points"] += points
            if announced is not None:
                if announced["code"] not in g["codes"]:
                    g["codes"].append(announced["code"])
                gap = int(abs((strip.first_seen or now) - announced.get("at", 0)) // 60)
                g["minutes"] = gap if g["minutes"] is None else min(g["minutes"], gap)

        first = strip.first_seen or now
        for a in self.store.announced:
            if code_matches(a.get("code", ""), [strip.mac, strip.mac2]):
                add(a.get("by", ""), "code", 100, a)
            elif -300 <= first - a.get("at", 0) <= 7200:
                add(a.get("by", ""), "app", 30 if first - a.get("at", 0) <= 900 else 10, a)
        if strip.address and not is_local_address(strip.address):
            with self.ips_lock:
                same = [uid for uid, ips in self.user_ips.items() if strip.address in ips]
            for uid in same:
                add(uid, "network", 40)
        out = sorted(found.values(), key=lambda g: -g["points"])[:3]
        for g in out:
            del g["points"]
        return out

    async def approve_strip(self, strip_id: Any) -> Tuple[int, Dict[str, Any]]:
        mac = str(strip_id or "").upper()
        if mac not in self.strips and mac not in self.store.blocked_strips:
            return 404, {"error": "unknown strip"}
        self.store.approve(mac)
        log("[strip] %s approved by the owner" % mac[-6:])
        return 200, {"ok": True}

    async def reserve_strip(self, code: Any, customer_id: Any) -> Tuple[int, Dict[str, Any]]:
        """The owner sets a strip up for a far-away customer (e.g. before shipping it): whenever a strip whose
        MAC ends in [code] connects within RESERVE_DAYS, it is approved and given to that customer."""
        code = re.sub(r"[^0-9A-Fa-f]", "", str(code or "").upper().replace("TONLY_TAP_", ""))
        if not 4 <= len(code) <= 12:
            return 400, {"error": "enter the letters/digits after TONLY_TAP_ (or the strip's MAC)"}
        customer = next((u for u in self.store.users if u["id"] == customer_id and u.get("role") == CUSTOMER), None)
        if customer is None:
            return 404, {"error": "unknown customer"}
        connected = next((x for x in self.strips.values() if x.mac.endswith(code)), None)
        if connected is not None:                    # it is already here: approve and hand it over now
            self.store.approve(connected.mac)
            return await self.assign_strip(connected.mac, customer["id"])
        self.store.expect(code, customer["id"], time.time(), RESERVE_DAYS * 86400)
        log("[strip] code %s reserved for customer %s" % (code, customer["id"]))
        return 200, {"ok": True, "waiting": True}

    async def unreserve_strip(self, code: Any) -> Tuple[int, Dict[str, Any]]:
        code = str(code or "").upper()
        if self.store.expected.pop(code, None) is None:
            return 404, {"error": "no such reservation"}
        self.store.save()
        return 200, {"ok": True}

    async def assign_strip(self, strip_id: Any, customer_id: Any) -> Tuple[int, Dict[str, Any]]:
        """Gives a strip to one customer (e.g. one the owner set up for an iPhone user); "" takes it from everyone."""
        mac = str(strip_id or "").upper()
        if not self.store.is_approved(mac):
            return 404, {"error": "unknown or unapproved strip"}
        customers = [u for u in self.store.users if u.get("role") == CUSTOMER]
        target = next((u for u in customers if u["id"] == customer_id), None)
        if customer_id and target is None:
            return 404, {"error": "unknown customer"}
        for user in customers:
            user["strips"] = [m for m in (user.get("strips") or []) if m != mac]
        if target is not None:
            target["strips"] = sorted(target["strips"] + [mac])
        self.store.save()
        log("[strip] %s %s" % (mac[-6:], "given to customer %s" % target["id"] if target else "taken from its customer"))
        return 200, {"ok": True}

    async def remove_strip(self, strip_id: Any, block: bool) -> Tuple[int, Dict[str, Any]]:
        mac = str(strip_id or "").upper()
        if mac not in self.strips and not self.store.knows(mac) and mac not in self.store.blocked_strips:
            return 404, {"error": "unknown strip"}
        self.store.forget_strip(mac, block)
        strip = self.strips.pop(mac, None)
        if strip is not None and strip.link is not None:
            strip.link.close()
        log("[strip] %s removed%s" % (mac[-6:], " and blocked" if block else ""))
        return 200, {"ok": True}

    async def switch(self, strip_id: Any, outlet: int, on: bool, outlets: Optional[List[int]] = None,
                     pin: Any = None, internal: bool = False) -> Tuple[int, Dict[str, Any]]:
        """Switch one outlet, several ([outlets]) or all (0). Timers, schedules and scenes the owner
        set up run as [internal] and skip the PIN. An offline strip gets the command queued."""
        strip = self.find(strip_id)
        if strip is None:
            return 404, {"error": "unknown strip"}
        if not internal:
            denied = self.check_pin(strip, pin)
            if denied:
                return denied
        wanted = outlets if outlets is not None else [outlet]
        targets = list(OUTLETS) if 0 in wanted else wanted
        if not internal:
            self.cancel_restores(strip.mac, targets)
        if not strip.online or strip.link is None:
            self.store.queue(strip.mac, [0] if 0 in wanted else targets, on, time.time())
            return 202, {"ok": True, "queued": True, "confirmed": False, "strip": self.strip_json(strip)}
        confirmed = await strip.link.switch(targets, on)
        return 200, {"ok": True, "confirmed": confirmed, "strip": self.strip_json(strip)}

    async def set_lock(self, strip_id: Any, req: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        strip = self.find(strip_id)
        if strip is None:
            return 404, {"error": "unknown strip"}
        if self.store.locked(strip.mac):
            denied = self.check_pin(strip, req.get("old_pin"))
            if denied:
                return denied
        pin = str(req.get("pin") or "")
        if pin and not (pin.isdigit() and 4 <= len(pin) <= 8):
            return 400, {"error": "the PIN must be 4 to 8 digits"}
        self.store.set_pin(strip.mac, pin)
        return 200, {"ok": True, "strip": self.strip_json(strip)}

    async def rename(self, strip_id: Any, outlet: int, name: str) -> Tuple[int, Dict[str, Any]]:
        strip = self.find(strip_id)
        if strip is None:
            return 404, {"error": "unknown strip"}
        self.store.rename(strip.mac, outlet, name)
        return 200, {"ok": True, "strip": self.strip_json(strip)}

    async def set_timer(self, strip_id: Any, outlet: int, minutes: float, on: bool,
                        outlets: Optional[List[int]] = None, pin: Any = None) -> Tuple[int, Dict[str, Any]]:
        strip = self.find(strip_id)
        if strip is None:
            return 404, {"error": "unknown strip"}
        denied = self.check_pin(strip, pin)
        if denied:
            return denied
        targets = outlets if outlets is not None else [outlet]
        if minutes <= 0 and 0 in targets:
            targets = [0] + list(OUTLETS)                # "cancel for all" clears every timer of the strip
        for n in targets:
            if minutes <= 0:
                self.store.clear_timer(strip.mac, n)
            else:
                self.store.set_timer(strip.mac, n, on, int(time.time() + minutes * 60))
        return 200, {"ok": True, "strip": self.strip_json(strip)}

    async def set_meta(self, strip_id: Any, outlet: int, req: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        strip = self.find(strip_id)
        if strip is None:
            return 404, {"error": "unknown strip"}
        icon = req.get("icon")
        if icon is not None and icon not in ICONS:
            return 400, {"error": "unknown icon"}
        room = req.get("room")
        if room is not None:
            room = str(room).strip()[:30]
        favorite = req.get("favorite")
        after_power: Any = "keep"
        if "after_power" in req:
            after_power = req["after_power"]
            if after_power is not None and (isinstance(after_power, bool) or not isinstance(after_power, (int, float))
                                            or not 0 <= after_power <= MAX_AFTER_POWER):
                return 400, {"error": "after_power must be null (stay off) or 0-%d minutes" % MAX_AFTER_POWER}
            if not outlet:
                return 400, {"error": "after_power is set per outlet"}
            if after_power is None:
                self.cancel_restores(strip.mac, [outlet])
        self.store.set_meta(strip.mac, outlet, room=room, icon=icon,
                            favorite=None if favorite is None else bool(favorite), after_power=after_power)
        return 200, {"ok": True, "strip": self.strip_json(strip)}

    async def save_schedule(self, req: Dict[str, Any], now: Optional[float] = None) -> Tuple[int, Dict[str, Any]]:
        now = time.time() if now is None else now
        strip = self.find(req.get("strip", ""))
        if strip is None:
            return 404, {"error": "unknown strip"}
        denied = self.check_pin(strip, req.get("pin"))
        if denied:
            return denied
        try:
            outlets = outlet_list(req)
        except (TypeError, ValueError) as err:
            return 400, {"error": str(err)}
        kind = req.get("kind", "time")
        sched: Dict[str, Any] = {"id": str(req.get("id") or os.urandom(4).hex()), "strip": strip.mac,
                                 "outlets": outlets, "kind": kind, "enabled": bool(req.get("enabled", True))}
        if kind == "time":
            hhmm = str(req.get("time", ""))
            try:
                hh, mm = (int(x) for x in hhmm.split(":"))
                if not (0 <= hh < 24 and 0 <= mm < 60):
                    raise ValueError
            except ValueError:
                return 400, {"error": "time must be HH:MM"}
            days = sorted({int(d) for d in req.get("days", []) if str(d).isdigit() and 0 <= int(d) <= 6})
            if not days:
                return 400, {"error": "pick at least one day (0 = Monday ... 6 = Sunday)"}
            sched.update({"on": bool(req.get("on")), "time": "%02d:%02d" % (hh, mm), "days": days})
        elif kind == "cycle":
            try:
                on_m, off_m = int(req.get("on_minutes", 0)), int(req.get("off_minutes", 0))
            except (TypeError, ValueError):
                return 400, {"error": "on_minutes and off_minutes must be numbers"}
            if not (1 <= on_m <= 1440 and 1 <= off_m <= 1440):
                return 400, {"error": "on and off times must be 1 to 1440 minutes"}
            sched.update({"on_minutes": on_m, "off_minutes": off_m, "started_at": int(now)})
            self.cycle_phase.pop(sched["id"], None)           # (re)start from the "on" part
        elif kind == "watch":
            metric, action = req.get("metric"), req.get("action", "off")
            try:
                value, seconds = float(req.get("value")), int(req.get("seconds", 0))
            except (TypeError, ValueError):
                return 400, {"error": "value and seconds must be numbers"}
            above = metric == "temp" or bool(req.get("above", True))      # only "too hot" makes sense for heat
            limits = {"power": (0.0, 4000.0), "temp": (20.0, 100.0)}
            if metric not in limits or action not in ("off", "alert"):
                return 400, {"error": "metric must be power or temp, action off or alert"}
            if not limits[metric][0] <= value <= limits[metric][1] or (not above and value <= 0):
                return 400, {"error": "the %s limit must be %g to %g" % ((metric,) + limits[metric])}
            if not 10 <= seconds <= 86400:
                return 400, {"error": "the condition must last 10 seconds to 24 hours"}
            sched.update({"metric": metric, "above": above, "value": round(value, 1), "seconds": seconds,
                          "action": action})
            self.watch_since = {k: v for k, v in self.watch_since.items() if k[0] != sched["id"]}
        else:
            return 400, {"error": "kind must be time, cycle or watch"}
        self.store.save_schedule(sched)
        return 200, {"ok": True, "schedule": sched, "schedules": self.schedules_json(now)}

    async def delete_schedule(self, sched_id: str, pin: Any = None) -> Tuple[int, Dict[str, Any]]:
        sched = next((x for x in self.store.schedules if x.get("id") == sched_id), None)
        if sched is None:
            return 404, {"error": "unknown schedule"}
        strip = self.find(sched["strip"])
        if strip is not None:
            denied = self.check_pin(strip, pin)
            if denied:
                return denied
        self.store.delete_schedule(sched_id)
        self.cycle_phase.pop(sched_id, None)
        self.watch_since = {k: v for k, v in self.watch_since.items() if k[0] != sched_id}
        return 200, {"ok": True, "schedules": self.schedules_json()}

    @staticmethod
    def cycle_state(sched: Dict[str, Any], now: float) -> Tuple[bool, float]:
        """(should the outlets be on now, when the next switch happens)."""
        on_s, off_s = sched["on_minutes"] * 60, sched["off_minutes"] * 60
        into = (now - sched["started_at"]) % (on_s + off_s)
        return (True, now + on_s - into) if into < on_s else (False, now + on_s + off_s - into)

    def schedules_json(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        now = time.time() if now is None else now
        out = []
        for sched in self.store.schedules:
            item = dict(sched)
            if sched.get("kind") == "cycle" and sched.get("enabled", True):
                on, change = self.cycle_state(sched, now)
                item.update({"phase_on": on, "next_change": int(change)})
            out.append(item)
        return out

    # ---- scenes

    async def save_scene(self, req: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        name = str(req.get("name") or "").strip()[:30]
        if not name:
            return 400, {"error": "the scene needs a name"}
        actions = []
        for a in req.get("actions", [])[:64]:
            strip = self.find(a.get("strip", "")) if isinstance(a, dict) else None
            if strip is None:
                return 400, {"error": "unknown strip in the scene"}
            try:
                outlet = int(a.get("outlet", 0))
            except (TypeError, ValueError):
                return 400, {"error": "bad outlet in the scene"}
            if outlet != 0 and outlet not in OUTLETS:
                return 400, {"error": "outlets must be 0 (all) or 1-4"}
            actions.append({"strip": strip.mac, "outlet": outlet, "on": bool(a.get("on"))})
        if not actions:
            return 400, {"error": "the scene needs at least one outlet"}
        scene = {"id": str(req.get("id") or os.urandom(4).hex()), "name": name,
                 "icon": str(req.get("icon") or "✨")[:4], "actions": actions}
        self.store.save_scene(scene)
        return 200, {"ok": True, "scene": scene, "scenes": self.store.scenes}

    async def delete_scene(self, scene_id: str) -> Tuple[int, Dict[str, Any]]:
        if not self.store.delete_scene(scene_id):
            return 404, {"error": "unknown scene"}
        return 200, {"ok": True, "scenes": self.store.scenes}

    async def run_scene(self, scene_id: str, pin: Any = None) -> Tuple[int, Dict[str, Any]]:
        scene = next((x for x in self.store.scenes if x.get("id") == scene_id), None)
        if scene is None:
            return 404, {"error": "unknown scene"}
        groups: Dict[Tuple[str, bool], List[int]] = {}
        for a in scene["actions"]:
            groups.setdefault((a["strip"], bool(a["on"])), []).append(int(a["outlet"]))
        for mac, _ in groups:                         # check every PIN before switching anything
            strip = self.find(mac)
            if strip is not None:
                denied = self.check_pin(strip, pin)
                if denied:
                    return denied
        results = []
        for (mac, on), outlets in groups.items():
            code, body = await self.switch(mac, 0, on, outlets=outlets, internal=True)
            results.append({"strip": mac, "on": on, "status": code,
                            "queued": bool(body.get("queued")), "confirmed": bool(body.get("confirmed"))})
        return 200, {"ok": True, "results": results}

    async def update_settings(self, req: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        new = dict(self.store.settings)
        for key, (low, high) in SETTING_LIMITS.items():
            if key in req:
                value = float(req[key])
                if not low <= value <= high:
                    return 400, {"error": "%s must be between %s and %s" % (key, low, high)}
                new[key] = value
        if "currency" in req:
            new["currency"] = str(req["currency"]).strip()[:8] or "EGP"
        if "alexa" in req:
            new["alexa"] = bool(req["alexa"])
        if "signup" in req:
            new["signup"] = bool(req["signup"])
        self.store.settings = new
        self.store.save()
        return 200, {"ok": True, "settings": new}

    # ---- background work

    def check_alerts(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        own = self.strip_limits()
        for strip in self.strips.values():
            mac = strip.mac
            if not self.store.is_approved(mac):
                continue
            settings = own.get(mac, self.store.settings)
            if not strip.online:
                if strip.last_seen and now - strip.last_seen >= OFFLINE_ALERT_AFTER \
                        and not self.alerted_offline.get(mac):
                    self.alerted_offline[mac] = True
                    self.history.add_event("offline", mac, now=now)
                continue
            out = self.store.outages.get(mac)
            if out and not out.get("back"):
                pass                                # just reconnected: came_online tells a power cut from a dropout
            elif self.alerted_offline.pop(mac, False):
                self.history.add_event("online", mac, now=now)
            for o in strip.outlets.values():
                if o.temp_c is not None and o.temp_c >= float(settings.get("max_temp_c", 60)):
                    self.alert_once("temp", mac, o.index, o.temp_c, now)
                if o.trip:
                    self.alert_once("trip", mac, o.index, o.trip, now)
            watts = sum(o.watts for o in strip.outlets.values())
            if watts >= float(settings.get("max_watts", 3000)):
                self.alert_once("power", mac, 0, watts, now)

    def alert_once(self, kind: str, mac: str, outlet: int, value: float, now: float) -> None:
        key = (kind, mac, outlet)
        if now - self.last_alert.get(key, 0.0) >= ALERT_REPEAT[kind]:
            self.last_alert[key] = now
            self.history.add_event(kind, mac, outlet, round(value, 1), now=now)

    async def run_due_schedules(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        lt = time.localtime(now)
        hhmm = "%02d:%02d" % (lt.tm_hour, lt.tm_min)
        minute_key = time.strftime("%Y-%m-%d %H:%M", lt)
        for sched in list(self.store.schedules):
            if not sched.get("enabled", True):
                continue
            if sched.get("kind") == "cycle":
                on, _ = self.cycle_state(sched, now)
                if self.cycle_phase.get(sched["id"]) == on:
                    continue
                self.cycle_phase[sched["id"]] = on
                code, body = await self.switch(sched["strip"], 0, on, outlets=sched["outlets"], internal=True)
                log("[cycle] %s %s -> %s (%s)" % (sched["strip"][-6:], sched["outlets"], "on" if on else "off",
                                                  "queued" if body.get("queued") else body.get("error", "sent")))
                continue
            if sched.get("time") != hhmm or lt.tm_wday not in sched.get("days", []):
                continue
            if self.schedule_fired.get(sched["id"]) == minute_key:
                continue
            self.schedule_fired[sched["id"]] = minute_key
            code, body = await self.switch(sched["strip"], 0, bool(sched["on"]), outlets=sched["outlets"], internal=True)
            log("[schedule] %s %s/%s -> %s (%s)" % (hhmm, sched["strip"][-6:], sched["outlets"],
                                                    "on" if sched["on"] else "off",
                                                    "done" if body.get("confirmed") else
                                                    "queued" if body.get("queued") else body.get("error", "not confirmed")))

    async def schedules_forever(self) -> None:
        while True:
            try:
                await self.run_due_schedules()
            except Exception as err:
                log("[schedule] error: %r" % (err,))
            await asyncio.sleep(10)

    async def poll_forever(self) -> None:
        last_diag = 0.0
        while True:
            diag = time.monotonic() - last_diag >= DIAG_EVERY
            if diag:
                last_diag = time.monotonic()
            for strip in list(self.strips.values()):
                link = strip.link
                if link is None or link.closed:
                    continue
                try:
                    if not await link.read_state():
                        log("[strip] %s did not answer (%d/%d)" % (self.label(strip), link.missed, MAX_MISSED_POLLS))
                        if link.missed >= MAX_MISSED_POLLS:
                            link.close()
                    elif diag:
                        await link.ask_diagnostics()
                except (ConnectionError, OSError) as err:
                    log("[strip] %s write failed: %s" % (self.label(strip), err))
                    link.close()
            try:
                self.check_alerts()
                await self.run_watches()
                await self.run_restores()
            except sqlite3.Error as err:
                log("[alerts] %s" % err)
            await asyncio.sleep(POLL_EVERY)

    async def run_watches(self, now: Optional[float] = None) -> None:
        """Monitoring rules: an outlet that is on and stays above (or below) a power or temperature limit for
        the rule's time is switched off, or just reported. It acts once until the condition clears again."""
        now = time.time() if now is None else now
        for rule in list(self.store.schedules):
            if rule.get("kind") != "watch" or not rule.get("enabled", True):
                continue
            strip = self.strips.get(rule["strip"])
            if strip is None or not strip.online or not self.store.is_approved(strip.mac):
                continue
            for n in (list(OUTLETS) if 0 in rule["outlets"] else rule["outlets"]):
                o, key = strip.outlets[n], (rule["id"], n)
                reading = o.watts if rule["metric"] == "power" else o.temp_c
                holds = o.on and reading is not None and (
                    reading > rule["value"] if rule["above"] else reading < rule["value"])
                if not holds:
                    self.watch_since.pop(key, None)
                    continue
                since = self.watch_since.setdefault(key, now)
                if since == "fired" or now - since < rule["seconds"]:
                    continue
                self.watch_since[key] = "fired"
                kind = "rule_" + rule["metric"] + ("_off" if rule["action"] == "off" else "")
                self.history.add_event(kind, strip.mac, n, round(float(reading), 1), now=now)
                if rule["action"] == "off":
                    code, body = await self.switch(strip.mac, n, False, internal=True)
                    log("[rule] %s outlet %d off: %s %.1f %s %.1f for %ds (%s)" % (
                        strip.mac[-6:], n, rule["metric"], reading, ">" if rule["above"] else "<", rule["value"],
                        rule["seconds"], "done" if body.get("confirmed") else body.get("error", "sent")))

    async def run_due_timers(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        for key, timer in list(self.store.timers.items()):
            if timer.get("at", 0) > now:
                continue
            mac, _, outlet = key.partition("/")
            strip = self.strips.get(mac)
            if strip is None or not strip.online:
                if now - timer.get("at", 0) > TIMER_GRACE:
                    log("[timer] %s skipped: strip offline" % key)
                    self.store.clear_timer(mac, int(outlet))
                continue
            self.store.clear_timer(mac, int(outlet))
            code, body = await self.switch(mac, int(outlet), bool(timer.get("on")), internal=True)
            log("[timer] %s -> %s (%s)" % (key, "on" if timer.get("on") else "off",
                                           "done" if code == 200 and body.get("confirmed") else "not confirmed"))

    async def timers_forever(self) -> None:
        while True:
            try:
                await self.run_due_timers()
            except Exception as err:            # a bad timer must never stop the loop
                log("[timer] error: %r" % (err,))
            await asyncio.sleep(1.0)


# --------------------------------------------------------------------------- web UI + API



class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, hub: Hub, loop: asyncio.AbstractEventLoop, token: str, public_ip: str):
        super().__init__(addr, WebHandler)
        self.hub, self.loop, self.token, self.public_ip = hub, loop, token, public_ip
        self.lan_ip = guess_home_ip()

    def strip_ip_for(self, phone_ip: str) -> str:
        """The address a strip set up by this phone should dial. In the server's own home (the phone comes
        from the server's internet address, or the home network) that is the server's address at home:
        most home routers do not loop a connection to their own internet address back inside."""
        if self.lan_ip != "127.0.0.1" and (phone_ip == self.public_ip or is_local_address(phone_ip)):
            return self.lan_ip
        return self.public_ip

    def run_on_loop(self, coro, timeout: float = 20.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def handle_error(self, request, client_address) -> None:
        # a phone or the tunnel hanging up in the middle of a reply (e.g. a cancelled app download)
        # is normal; only real errors deserve a traceback in the log
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",                           # no page of ours inside someone else's frame
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'none'; object-src 'none'",
    "Referrer-Policy": "no-referrer",                    # the owner page's address never leaks in a Referer
    "Strict-Transport-Security": "max-age=31536000",     # browsers keep to HTTPS (only counts over HTTPS)
}


class WebHandler(BaseHTTPRequestHandler):
    server: WebServer
    server_version = "DarwishSmartPower"                 # no version: nothing to look up for known holes
    timeout = 30                                         # a client that stalls mid-request is dropped

    def log_message(self, fmt, *args):
        pass

    # ---- helpers

    def token_ok(self) -> bool:
        return self.who() is not None

    def who(self) -> Optional[Dict[str, Any]]:
        """The owner (server token, or no token set), a family member (their own token), or None."""
        if not self.server.token:
            return OWNER
        offered = self.offered_tokens()
        if any(o and hmac.compare_digest(o.encode(), self.server.token.encode()) for o in offered):
            return OWNER
        for o in offered:
            user = self.server.hub.store.user_by_token(o) if o else None
            person = self.server.hub.person(user) if user else None
            if person:
                self.server.hub.saw_user(user, self.client_ip())
                return person
        return None

    def client_ip(self) -> str:
        """The caller's address; behind the Cloudflare tunnel (a local connection) it comes from a header."""
        peer = self.client_address[0]
        forwarded = self.headers.get("CF-Connecting-IP", "").strip()
        if forwarded and peer in ("127.0.0.1", "::1"):
            return forwarded
        return peer

    def signed_in(self) -> Optional[Dict[str, Any]]:
        """who(), after the brute-force checks; None means a reply was already sent."""
        guard, ip = self.server.hub.guard, self.client_ip()
        if guard.is_blocked(ip):
            self.reply_json(429, {"error": "too many wrong passwords, try again later"})
            return None
        who = self.who()
        if who is None:
            offered = self.offered_tokens()
            if any(offered):
                guard.login_failed(ip, offered)
            self.deny()
        return who

    def offered_tokens(self) -> List[str]:
        offered = [self.headers.get("X-Token", "")]
        auth = self.headers.get("Authorization", "")
        if auth[:7].lower() == "bearer ":
            offered.append(auth[7:].strip())
        elif auth[:6].lower() == "basic ":
            try:
                offered.append(base64.b64decode(auth[6:]).decode("utf-8").partition(":")[2])
            except (ValueError, UnicodeDecodeError):
                pass
        offered += parse_qs(urlsplit(self.path).query).get("token", [])
        return offered

    def reply(self, code: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        headers = dict(SECURITY_HEADERS, **{"Cache-Control": "no-store"})
        headers.update(extra or {})
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def reply_json(self, code: int, obj: Dict[str, Any]) -> None:
        self.reply(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def deny(self) -> None:
        # no WWW-Authenticate: the panel asks for the password itself (a browser pop-up would get in the way)
        self.reply_json(401, {"error": "token required"})

    def send_file(self, path: Path, ctype: str, filename: str) -> None:
        if not path.is_file():
            self.reply_json(404, {"error": "not built yet - see README (Android app)"})
            return
        # streamed in pieces: the app is several MB and may be downloaded by several phones at once
        with path.open("rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
            for k, v in dict(SECURITY_HEADERS, **{"Cache-Control": "no-store"}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                shutil.copyfileobj(fh, self.wfile, 64 * 1024)

    def send_static(self, path: str) -> bool:
        """Serve a public website file; False when [path] is not one."""
        if path.endswith("/") and len(path) > 1:
            path = path[:-1]
        if path in STATIC:
            name, ctype = STATIC[path]
            target = WEBSITE / name
        elif path == self.server.hub.store.owner_path():
            target, ctype = WEBSITE / "admin.html", "text/html; charset=utf-8"
        elif path == self.server.hub.store.owner_path() + ".webmanifest":
            # the owner page installs as its own app (with its own icon), opening straight on the secret address
            self.reply(200, json.dumps(admin_manifest(path[:-len(".webmanifest")]), ensure_ascii=False).encode("utf-8"),
                       "application/manifest+json", {"Cache-Control": "no-cache"})
            return True
        elif re.fullmatch(r"/icons/[a-z0-9-]+\.(png|svg)", path):
            target = WEBSITE / path.lstrip("/")
            ctype = ICON_TYPES[target.suffix]
        else:
            return False
        if not target.is_file():
            self.reply_json(404, {"error": "website file missing: %s" % target.name})
            return True
        extra = {"Cache-Control": "no-cache"}
        if path == "/sw.js":
            extra["Service-Worker-Allowed"] = "/"
        body = target.read_bytes()
        if target.name == "admin.html":                 # its app file lives next to the secret address
            body = body.replace(b"__OWNER_PATH__", path.encode("utf-8"))
        self.reply(200, body, ctype, extra)
        return True

    # ---- routes

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/smartpower.py":                 # public: lets a phone or laptop fetch the provisioner
            self.send_file(Path(__file__).resolve(), "text/x-python", "smartpower.py")
            return
        if path == "/app.apk":                      # public: install the Android app from the phone browser
            apk = find_apk()
            if self.command == "GET":
                self.server.hub.store.count_download(self.client_ip(), time.time())
            if apk:
                self.send_file(apk, "application/vnd.android.package-archive", "darwish-smart-power.apk")
            else:                                   # not downloaded yet: send the phone to the GitHub copy
                self.reply(302, b"", "text/plain", {"Location": APK_RELEASE_URL})
            return
        if self.send_static(path):                  # public: website, control panel (asks for the password), PWA files
            return
        if path == "/oauth/authorize":              # public: Alexa / Google account linking sign-in page
            self.oauth_authorize_page()
            return
        who = self.signed_in()
        if who is None:
            return
        hub = self.server.hub
        if path == "/api/state":
            snap = self.server.run_on_loop(hub.state_for(who))
            snap["server"] = {"strip_port": STRIP_PORT, "version": VERSION}
            if who["role"] != "view":               # the address new strips dial; only people who add strips need it
                snap["server"]["ip"] = self.server.public_ip
            self.reply_json(200, snap)
        elif path == "/api/me":
            self.reply_json(200, {"role": who["role"], "name": who["name"], "login": who.get("login") or ""})
        elif path == "/api/users":
            if who["role"] != "owner":
                self.reply_json(403, {"error": "only the owner manages family members"})
            else:
                self.reply_json(200, self.server.run_on_loop(hub.users()))
        elif path == "/api/household":
            if who["role"] != CUSTOMER:
                self.reply_json(403, {"error": "only the account holder manages their home"})
            else:
                self.reply_json(200, self.server.run_on_loop(hub.household(who["id"])))
        elif path == "/api/health":
            self.reply_json(200, {"ok": True, "app": "darwish-smart-power", "version": VERSION})
        elif path == "/api/admin":
            if who["role"] != "owner":
                self.reply_json(403, {"error": "only the owner sees this"})
            else:
                report = self.server.run_on_loop(hub.admin_report())
                report["security"] = hub.guard.report()
                self.reply_json(200, report)
        elif path == "/api/security":
            if who["role"] != "owner":
                self.reply_json(403, {"error": "only the owner sees this"})
            else:
                self.reply_json(200, hub.guard.report())
        elif path == "/api/history":
            query = parse_qs(urlsplit(self.path).query)
            strip = query.get("strip", [None])[0]
            if strip and not may_see(who, strip):
                self.reply_json(403, {"error": "this strip is not shared with you"})
                return
            code, body = self.server.run_on_loop(hub.history_report(
                query.get("range", ["day"])[0], strip, allowed=who["strips"], account=who.get("account")))
            self.reply_json(code, body)
        elif path == "/api/history.xlsx":
            query = parse_qs(urlsplit(self.path).query)
            strip = query.get("strip", [None])[0]
            if strip and not may_see(who, strip):
                self.reply_json(403, {"error": "this strip is not shared with you"})
                return
            rng = query.get("range", ["month"])[0]
            code, body = self.server.run_on_loop(hub.history_xlsx(
                rng, strip, query.get("lang", ["ar"])[0], allowed=who["strips"], account=who.get("account")))
            if code != 200:
                self.reply_json(code, body)
                return
            name = "darwish-power-%s-%s.xlsx" % (rng, time.strftime("%Y-%m-%d"))
            self.reply(200, body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       {"Content-Disposition": 'attachment; filename="%s"' % name})
        elif path == "/api/events":
            try:
                after = int(parse_qs(urlsplit(self.path).query).get("after", ["0"])[0])
            except ValueError:
                after = 0
            events = self.server.run_on_loop(hub.events(after))
            events["events"] = [e for e in events["events"] if may_see(who, e["strip"])]
            self.reply_json(200, events)
        else:
            self.reply_json(404, {"error": "not found"})

    def read_json(self) -> Optional[Dict[str, Any]]:
        """The request body as a JSON object, or None after replying with the error."""
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self.reply_json(415, {"error": "send JSON (Content-Type: application/json)"})
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("body size")
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(req, dict):
                raise ValueError("body must be an object")
            return req
        except (TypeError, ValueError) as err:
            self.reply_json(400, {"error": "bad request: %s" % err})
            return None

    # ---- voice assistant account linking (OAuth 2, authorization code grant)

    def oauth_params(self, values: Dict[str, List[str]]) -> Optional[Dict[str, str]]:
        """client_id, redirect_uri and state, if they belong to a configured voice assistant."""
        get = lambda k: (values.get(k) or [""])[0]
        client, redirect = get("client_id"), get("redirect_uri")
        parts = urlsplit(redirect)
        if client not in OAUTH_CLIENTS or not self.server.hub.store.oauth["clients"].get(client):
            return None
        if parts.scheme != "https" or parts.hostname not in OAUTH_REDIRECT_HOSTS:
            return None
        return {"client_id": client, "redirect_uri": redirect, "state": get("state")}

    def oauth_authorize_page(self, params: Optional[Dict[str, str]] = None, error: str = "") -> None:
        params = params or self.oauth_params(parse_qs(urlsplit(self.path).query))
        if params is None:
            self.reply(400, b"Unknown app or address.", "text/plain; charset=utf-8")
            return
        hidden = "".join('<input type="hidden" name="%s" value="%s">' % (k, html_escape(v)) for k, v in params.items())
        who = "Google" if params["client_id"] == "google" else "Alexa"
        page = LINK_PAGE % {"who": who, "hidden": hidden, "error": html_escape(error)}
        self.reply(200 if not error else 401, page.encode("utf-8"), "text/html; charset=utf-8",
                   {"Content-Security-Policy": "frame-ancestors 'none'"})

    def read_form(self) -> Dict[str, List[str]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_BODY:
            return {}
        return parse_qs(self.rfile.read(length).decode("utf-8", "replace"))

    def oauth_authorize_post(self) -> None:
        guard, ip, hub = self.server.hub.guard, self.client_ip(), self.server.hub
        form = self.read_form()
        params = self.oauth_params(form)
        if params is None:
            self.reply(400, b"Unknown app or address.", "text/plain; charset=utf-8")
            return
        if guard.is_blocked(ip):
            self.oauth_authorize_page(params, "محاولات كتير، جرّب بعد شوية. Too many attempts.")
            return
        login = (form.get("login") or [""])[0]
        password = (form.get("password") or [""])[0]
        subject = None
        normal = normalise_login(login) or ""
        if normal and guard.account_paused(normal):
            self.oauth_authorize_page(params, "محاولات كتير، جرّب بعد شوية. Too many attempts.")
            return
        account = hub.store.account_by_login(normal)
        if account and password_ok(account.get("pw", ""), password):
            subject = account["id"]
        elif self.server.token and hmac.compare_digest(password.strip().encode(), self.server.token.encode()):
            subject = "owner"                           # the owner signs in with the server password
        else:
            member = hub.store.user_by_token(password.strip()) if password.strip() else None
            subject = member["id"] if member else None  # a family member with their invite code
        if subject is None:
            guard.login_failed(ip, ["%s|%s" % (login, password)])
            guard.account_failed(normal)
            self.oauth_authorize_page(params, "الرقم أو الباسورد غلط. Wrong login or password.")
            return
        code = hub.store.oauth_new_code(params["client_id"], subject, params["redirect_uri"], time.time())
        sep = "&" if "?" in params["redirect_uri"] else "?"
        target = "%s%scode=%s&state=%s" % (params["redirect_uri"], sep, quote(code), quote(params["state"]))
        log("[oauth] %s linked by %s" % (params["client_id"], "the owner" if subject == "owner" else "account " + subject))
        self.reply(302, b"", "text/plain", {"Location": target})

    def oauth_token(self) -> None:
        store = self.server.hub.store
        form = self.read_form()
        get = lambda k: (form.get(k) or [""])[0]
        client, secret = get("client_id"), get("client_secret")
        auth = self.headers.get("Authorization", "")
        if auth[:6].lower() == "basic ":
            try:
                client, _, secret = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
                client, secret = unquote(client), unquote(secret)
            except (ValueError, UnicodeDecodeError):
                pass
        if not store.oauth_client_ok(client, secret):
            self.reply_json(401, {"error": "invalid_client"})
            return
        now = time.time()
        grant = get("grant_type")
        if grant == "authorization_code":
            subject = store.oauth_take_code(get("code"), client, get("redirect_uri"), now)
        elif grant == "refresh_token":
            subject = store.oauth_refresh(get("refresh_token"), client, now)
        else:
            self.reply_json(400, {"error": "unsupported_grant_type"})
            return
        if subject is None or self.server.hub.who_for(subject) is None:
            self.reply_json(400, {"error": "invalid_grant"})
            return
        self.reply_json(200, store.oauth_tokens(client, subject, now))

    def account_request(self, path: str) -> None:
        """Public: a customer creates an account or signs in from the app."""
        guard, ip = self.server.hub.guard, self.client_ip()
        if guard.is_blocked(ip):
            self.reply_json(429, {"error": "too many attempts, try again later"})
            return
        req = self.read_json()
        if req is None:
            return
        hub = self.server.hub
        if path == "/api/signup":
            if not guard.may_sign_up(ip):
                self.reply_json(429, {"error": "too many new accounts, try again later"})
                return
            # the slow password hash runs here, not on the loop that talks to the strips
            req["pw"] = hash_password(str(req.get("password") or "")) if MIN_PASSWORD <= len(str(req.get("password") or "")) <= 128 else ""
            code, body = self.server.run_on_loop(hub.sign_up(req))
            self.reply_json(code, body)
            return
        login = normalise_login(req.get("login")) or ""
        if guard.account_paused(login):
            self.reply_json(429, {"error": "too many wrong passwords for this account, try again in 15 minutes"})
            return
        user = hub.store.account_by_login(login) if login else None
        # an unknown login takes as long as a known one, so the time does not tell which accounts exist
        ok = password_ok(user.get("pw", "") if user else DUMMY_HASH, str(req.get("password") or "")) and user is not None
        if not ok:
            guard.login_failed(ip, ["%s|%s" % (req.get("login"), req.get("password"))])
            guard.account_failed(login)
            self.reply_json(401, {"error": "wrong phone/e-mail or password"})
            return
        code, body = self.server.run_on_loop(hub.sign_in(user))
        self.reply_json(code, body)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path in ("/api/signup", "/api/login"):
            self.account_request(path)
            return
        if path == "/oauth/authorize":
            self.oauth_authorize_post()
            return
        if path == "/oauth/token":
            self.oauth_token()
            return
        if path == "/api/alexa":
            req = self.read_json()
            if req is not None:
                self.reply_json(200, self.server.run_on_loop(self.server.hub.alexa(req)))
            return
        who = self.signed_in()
        if who is None:
            return
        if who["role"] == "view":
            self.reply_json(403, {"error": "view only", "view_only": True})
            return
        own_strip = path == "/api/strips/remove" and who["role"] == CUSTOMER
        if who["role"] != "owner" and path.startswith(OWNER_ONLY) and not own_strip:
            self.reply_json(403, {"error": "only the owner can change this"})
            return
        # JSON only: browsers cannot send this cross-site without a CORS preflight, which we never grant
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self.reply_json(415, {"error": "send JSON (Content-Type: application/json)"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("body size")
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(req, dict):
                raise ValueError("body must be an object")
            outlet = int(req.get("outlet", 0))
            if outlet != 0 and outlet not in OUTLETS:
                raise ValueError("outlet must be 0 (all) or 1-4")
        except (KeyError, TypeError, ValueError) as err:
            self.reply_json(400, {"error": "bad request: %s" % err})
            return

        hub = self.server.hub
        strip_id = str(req.get("strip", ""))
        pin = req.get("pin")
        # a member shared only some strips may touch only those
        if who["strips"] is not None:
            touched = [strip_id] if strip_id else []
            if path.startswith("/api/scenes/") and path != "/api/scenes/save":
                touched = hub.scene_strips(str(req.get("id", "")))
            elif path == "/api/scenes/save":
                touched = [str(a.get("strip", "")) for a in req.get("actions", []) if isinstance(a, dict)]
            elif path == "/api/schedules/delete":
                touched = [hub.schedule_strip(str(req.get("id", ""))) or ""]
            if not all(may_see(who, x) for x in touched):
                self.reply_json(403, {"error": "this strip is not shared with you"})
                return
        try:
            outlets = outlet_list(req) if "outlets" in req else None
            if path == "/api/strips/expect":
                code, body = self.server.run_on_loop(hub.expect_strip(req.get("code"), who.get("account") or who.get("id") or "",
                                                                      self.client_ip()))
                if code == 200:
                    body["strip_ip"] = self.server.strip_ip_for(self.client_ip())
            elif path == "/api/strips/approve":
                code, body = self.server.run_on_loop(hub.approve_strip(strip_id))
                if code == 200 and req.get("customer"):     # approve and give it to the customer who set it up
                    code, body = self.server.run_on_loop(hub.assign_strip(strip_id, str(req["customer"])))
            elif path == "/api/strips/remove":
                block = bool(req.get("block")) and who["role"] == "owner"
                code, body = self.server.run_on_loop(hub.remove_strip(strip_id, block))
            elif path == "/api/oauth/secret":
                client = str(req.get("client") or "")
                if client not in OAUTH_CLIENTS:
                    code, body = 400, {"error": "client must be alexa or google"}
                else:
                    code, body = 200, {"ok": True, "client_id": client, "client_secret": hub.store.oauth_new_secret(client)}
            elif path == "/api/strips/reserve":
                code, body = self.server.run_on_loop(hub.reserve_strip(req.get("code"), str(req.get("customer") or "")))
            elif path == "/api/strips/unreserve":
                code, body = self.server.run_on_loop(hub.unreserve_strip(req.get("code")))
            elif path == "/api/strips/assign":
                code, body = self.server.run_on_loop(hub.assign_strip(strip_id, str(req.get("customer") or "")))
            elif path == "/api/customers/password":
                password = str(req.get("password") or "")
                pw = hash_password(password) if MIN_PASSWORD <= len(password) <= 128 else ""   # slow hash off the loop
                code, body = self.server.run_on_loop(hub.set_customer_password(str(req.get("customer") or ""), pw))
            elif path == "/api/logout":
                for token in self.offered_tokens():
                    if token:
                        hub.store.end_session(token)
                code, body = 200, {"ok": True}
            elif path == "/api/account/delete":
                if who["role"] != CUSTOMER:
                    code, body = 400, {"error": "only customer accounts can be deleted here"}
                else:
                    code, body = self.server.run_on_loop(hub.delete_account(who["id"]))
            elif path.startswith("/api/household/") or path == "/api/my/settings":
                if who["role"] != CUSTOMER:             # the account holder only, not the people they invited
                    code, body = 403, {"error": "only the account holder can change this"}
                elif path == "/api/my/settings":
                    code, body = self.server.run_on_loop(hub.update_my_settings(who["id"], req))
                elif path == "/api/household/add":
                    code, body = self.server.run_on_loop(hub.household_add(who["id"], req))
                elif path == "/api/household/update":
                    code, body = self.server.run_on_loop(hub.household_update(who["id"], req))
                elif path == "/api/household/delete":
                    code, body = self.server.run_on_loop(hub.household_delete(who["id"], str(req.get("id", ""))))
                else:
                    code, body = 404, {"error": "not found"}
            elif path == "/api/users/add":
                code, body = self.server.run_on_loop(hub.add_user(req))
            elif path == "/api/users/update":
                code, body = self.server.run_on_loop(hub.update_user(req))
            elif path == "/api/users/delete":
                code, body = self.server.run_on_loop(hub.delete_user(str(req.get("id", ""))))
            elif path == "/api/meta":
                code, body = self.server.run_on_loop(hub.set_meta(strip_id, outlet, req))
            elif path == "/api/schedules/save":
                code, body = self.server.run_on_loop(hub.save_schedule(req))
            elif path == "/api/schedules/delete":
                code, body = self.server.run_on_loop(hub.delete_schedule(str(req.get("id", "")), pin))
            elif path == "/api/scenes/save":
                code, body = self.server.run_on_loop(hub.save_scene(req))
            elif path == "/api/scenes/delete":
                code, body = self.server.run_on_loop(hub.delete_scene(str(req.get("id", ""))))
            elif path == "/api/scenes/run":
                code, body = self.server.run_on_loop(hub.run_scene(str(req.get("id", "")), pin))
            elif path == "/api/strips/restore":
                code, body = self.server.run_on_loop(hub.restore_after_power(strip_id, pin))
            elif path == "/api/lock":
                code, body = self.server.run_on_loop(hub.set_lock(strip_id, req))
            elif path == "/api/settings":
                code, body = self.server.run_on_loop(hub.update_settings(req))
            elif path == "/api/switch":
                code, body = self.server.run_on_loop(hub.switch(strip_id, outlet, bool(req.get("on")),
                                                                outlets=outlets, pin=pin))
            elif path == "/api/rename":
                name = str(req.get("name") or "").strip()[:40]
                code, body = self.server.run_on_loop(hub.rename(strip_id, outlet, name))
            elif path == "/api/timer":
                minutes = float(req.get("minutes", 0))
                if not 0 <= minutes <= 7 * 24 * 60:
                    raise ValueError("minutes must be between 0 and 10080")
                code, body = self.server.run_on_loop(hub.set_timer(strip_id, outlet, minutes, bool(req.get("on")),
                                                                   outlets=outlets, pin=pin))
            else:
                code, body = 404, {"error": "not found"}
        except (TypeError, ValueError) as err:
            code, body = 400, {"error": "bad request: %s" % err}
        except Exception as err:
            code, body = 502, {"error": "command failed: %r" % (err,)}
        self.reply_json(code, body)


# --------------------------------------------------------------------------- commands

# --------------------------------------------------------------------------- Alexa (local, no cloud)

def xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


class Alexa:
    """Makes every outlet look like a Belkin WeMo smart plug on the home network.

    An Echo finds them with "Alexa, discover devices" (SSDP on UDP 1900), then switches them over
    plain HTTP, entirely inside the house. The device name Alexa hears is the outlet's name in the
    app, and each strip also appears as one device for all of its outlets. Strips locked with a PIN
    are left out, so the lock still means something.
    """

    def __init__(self, hub: "Hub", ip: str, ssdp_port: int = SSDP_PORT, base_port: int = ALEXA_BASE_PORT):
        self.hub = hub
        self.ip = ip
        self.ssdp_port = ssdp_port
        self.base_port = base_port
        self.servers: Dict[str, asyncio.AbstractServer] = {}
        self.transport: Optional[asyncio.DatagramTransport] = None

    # ---- which devices exist

    def devices(self) -> Dict[str, Dict[str, Any]]:
        """key "MAC/outlet" -> {"name", "port", "serial", "mac", "outlet"}; outlet 0 = the whole strip."""
        store = self.hub.store
        customers = {m for u in store.users if u.get("role") == CUSTOMER for m in (u.get("strips") or [])}
        # customers' strips are not offered to the Echo in the owner's house (they use the Alexa skill)
        strips = [x for x in self.hub.strips.values()
                  if store.is_approved(x.mac) and not store.locked(x.mac) and x.mac not in customers]
        out: Dict[str, Dict[str, Any]] = {}
        for strip in strips:
            strip_name = store.name(strip.mac, 0) or "مشترك %s" % strip.mac[-4:]
            for n in (0,) + OUTLETS:
                if n == 0:
                    name = strip_name
                else:
                    name = store.name(strip.mac, n) or "%s %d" % (strip_name, n)
                key = "%s/%d" % (strip.mac, n)
                out[key] = {"name": name, "mac": strip.mac, "outlet": n, "port": self.port_for(key),
                            "serial": hashlib.sha1(key.encode()).hexdigest()[:14].upper()}
        return out

    def port_for(self, key: str) -> int:
        ports = self.hub.store.alexa_ports
        if key not in ports:
            used = set(ports.values())
            port = self.base_port
            while port in used:
                port += 1
            ports[key] = port
            self.hub.store.save()
        return ports[key]

    def is_on(self, mac: str, outlet: int) -> bool:
        strip = self.hub.strips.get(mac)
        if strip is None:
            return False
        if outlet == 0:
            return any(o.on for o in strip.outlets.values())
        return strip.outlets[outlet].on

    # ---- discovery

    def answer(self, text: str, addr: Tuple[str, int]) -> None:
        if "M-SEARCH" not in text or not self.hub.store.settings.get("alexa", True):
            return
        if not is_local_address(addr[0]):           # home network only (and never a reflector for floods)
            return
        lowered = text.lower()
        if "urn:belkin:device:**" in lowered:
            st = "urn:Belkin:device:**"
        elif "ssdp:all" in lowered or "upnp:rootdevice" in lowered:
            st = "upnp:rootdevice"
        else:
            return
        for dev in self.devices().values():
            reply = ("HTTP/1.1 200 OK\r\n"
                     "CACHE-CONTROL: max-age=86400\r\n"
                     "DATE: %s\r\n"
                     "EXT:\r\n"
                     "LOCATION: http://%s:%d/setup.xml\r\n"
                     "OPT: \"http://schemas.upnp.org/upnp/1/0/\"; ns=01\r\n"
                     "01-NLS: %s\r\n"
                     "SERVER: Unspecified, UPnP/1.0, Unspecified\r\n"
                     "ST: %s\r\n"
                     "USN: uuid:Socket-1_0-%s::%s\r\n"
                     "X-User-Agent: redsonic\r\n\r\n") % (
                time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime()), self.ip, dev["port"],
                dev["serial"], st, dev["serial"], st)
            if self.transport is not None:
                self.transport.sendto(reply.encode("utf-8"), addr)

    async def start_discovery(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("", self.ssdp_port))
        try:
            group = struct.pack("4s4s", socket.inet_aton(SSDP_GROUP), socket.inet_aton(self.ip))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, group)
        except OSError as err:
            log("[alexa] could not join the discovery group (%s); Echo may not find the outlets" % err)
        sock.setblocking(False)
        alexa = self

        class Discovery(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
                alexa.answer(data.decode("utf-8", "replace"), addr)

        self.transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(Discovery, sock=sock)

    # ---- the per-outlet "smart plug"

    async def serve_device(self, key: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        if not peer or not is_local_address(peer[0]):  # the home network only
            writer.close()
            return
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("utf-8", "replace").split("\r\n")
            method, path = (lines[0].split(" ") + ["", ""])[:2]
            headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ":" in ln)}
            length = min(int(headers.get("content-length", "0") or 0), 64 * 1024)
            body = (await asyncio.wait_for(reader.readexactly(length), 10)).decode("utf-8", "replace") if length else ""
            status, ctype, payload = await self.device_reply(key, method, path, headers, body)
            data = payload.encode("utf-8")
            writer.write(("HTTP/1.1 %s\r\nCONTENT-TYPE: %s\r\nCONTENT-LENGTH: %d\r\nCONNECTION: close\r\n"
                          "SERVER: Unspecified, UPnP/1.0, Unspecified\r\n\r\n" % (status, ctype, len(data))).encode() + data)
            await writer.drain()
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, ValueError):
            pass
        finally:
            writer.close()

    async def device_reply(self, key: str, method: str, path: str, headers: Dict[str, str],
                           body: str) -> Tuple[str, str, str]:
        dev = self.devices().get(key)
        if dev is None or not self.hub.store.settings.get("alexa", True):
            return "404 Not Found", "text/plain", "gone"
        if method == "GET" and path.startswith("/setup.xml"):
            return "200 OK", 'text/xml; charset="utf-8"', (
                '<?xml version="1.0"?><root xmlns="urn:Belkin:device-1-0"><specVersion><major>1</major><minor>0</minor>'
                "</specVersion><device><deviceType>urn:Belkin:device:controllee:1</deviceType>"
                "<friendlyName>%s</friendlyName><manufacturer>Belkin International Inc.</manufacturer>"
                "<modelName>Socket</modelName><modelNumber>3.1415</modelNumber>"
                "<modelDescription>Belkin Plugin Socket 1.0</modelDescription><UDN>uuid:Socket-1_0-%s</UDN>"
                "<serialNumber>%s</serialNumber><binaryState>%d</binaryState><serviceList><service>"
                "<serviceType>urn:Belkin:service:basicevent:1</serviceType><serviceId>urn:Belkin:serviceId:basicevent1</serviceId>"
                "<controlURL>/upnp/control/basicevent1</controlURL><eventSubURL>/upnp/event/basicevent1</eventSubURL>"
                "<SCPDURL>/eventservice.xml</SCPDURL></service></serviceList></device></root>"
            ) % (xml_escape(dev["name"]), dev["serial"], dev["serial"], int(self.is_on(dev["mac"], dev["outlet"])))
        if method == "GET" and path.startswith("/eventservice.xml"):
            return "200 OK", 'text/xml; charset="utf-8"', (
                '<?xml version="1.0"?><scpd xmlns="urn:Belkin:service-1-0"><actionList>'
                "<action><name>SetBinaryState</name></action><action><name>GetBinaryState</name></action>"
                "</actionList></scpd>")
        if method == "POST" and path.startswith("/upnp/control/basicevent1"):
            action = headers.get("soapaction", "")
            if "SetBinaryState" in action:
                m = re.search(r"<BinaryState>\s*(\d)", body)
                on = bool(m and m.group(1) == "1")
                # answer at once (Alexa gives up quickly); the switch itself runs right after
                asyncio.ensure_future(self.hub.switch(dev["mac"], dev["outlet"], on, internal=True))
                log("[alexa] %s -> %s" % (dev["name"], "on" if on else "off"))
                return "200 OK", 'text/xml; charset="utf-8"', soap("SetBinaryState", int(on))
            return "200 OK", 'text/xml; charset="utf-8"', soap("GetBinaryState", int(self.is_on(dev["mac"], dev["outlet"])))
        return "404 Not Found", "text/plain", "not found"

    async def reconcile(self) -> None:
        """Start a small server for each new outlet and stop those that are gone."""
        wanted = self.devices() if self.hub.store.settings.get("alexa", True) else {}
        for key in list(self.servers):
            if key not in wanted:
                self.servers.pop(key).close()
        for key, dev in wanted.items():
            if key in self.servers:
                continue
            try:
                self.servers[key] = await asyncio.start_server(
                    lambda r, w, k=key: self.serve_device(k, r, w), "0.0.0.0", dev["port"])
            except OSError as err:
                log("[alexa] port %d busy for %s: %s" % (dev["port"], dev["name"], err))

    async def run_forever(self) -> None:
        try:
            await self.start_discovery()
        except OSError as err:
            log("[alexa] discovery not available (UDP %d: %s); Alexa will not find the outlets" % (self.ssdp_port, err))
        while True:
            try:
                await self.reconcile()
            except Exception as err:
                log("[alexa] %r" % (err,))
            await asyncio.sleep(15)


def soap(action: str, state: int) -> str:
    return ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            '<u:%sResponse xmlns:u="urn:Belkin:service:basicevent:1"><BinaryState>%d</BinaryState>'
            "</u:%sResponse></s:Body></s:Envelope>") % (action, state, action)


def refresh_apk() -> None:
    """Download the newest Android app from the GitHub release if it changed, so phones get it from here."""
    target = APK_CANDIDATES[0]
    marker = target.with_name(".apk-version")
    req = urllib.request.Request(APK_RELEASE_API, headers={"Accept": "application/vnd.github+json",
                                                           "User-Agent": "darwish-smart-power"})
    with urllib.request.urlopen(req, timeout=30) as r:
        release = json.loads(r.read())
    asset = next((a for a in release.get("assets", []) if a.get("name", "").endswith(".apk")), None)
    if asset is None:
        return
    version = "%s %s" % (asset.get("id"), asset.get("updated_at"))
    if target.is_file() and marker.is_file() and marker.read_text().strip() == version:
        return
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".apk-")
    try:
        with os.fdopen(fd, "wb") as fh, urllib.request.urlopen(asset["browser_download_url"], timeout=300) as r:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
        if os.path.getsize(tmp) < 100_000:
            raise OSError("download too small")
        os.replace(tmp, str(target))
        marker.write_text(version)
        log("[app] downloaded the Android app (%d KB)" % (target.stat().st_size // 1024))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


async def apk_refresh_forever() -> None:
    loop = asyncio.get_running_loop()
    while True:
        try:
            await loop.run_in_executor(None, refresh_apk)
        except Exception as err:                # no internet, GitHub down, disk full: try again later
            log("[app] could not update the Android app: %s" % (err,))
        await asyncio.sleep(APK_REFRESH_EVERY)


def guess_lan_ip() -> str:
    """The address other machines on this network would use to reach us."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))        # no packet is sent; this only picks a route
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def guess_home_ip() -> str:
    """The server's address on the home network, which strips in the server's own home dial. SP_LAN_IP sets it;
    otherwise it is the address on the way to the internet, if that is a home-network address (a server with
    a VPN, Tailscale or Docker has several addresses, and only that one is on the home Wi-Fi)."""
    forced = os.environ.get("SP_LAN_IP", "").strip()
    if forced:
        return forced
    for probe in ("8.8.8.8", "10.255.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 53))          # no packet is sent; this only picks a route
                ip = s.getsockname()[0]
        except OSError:
            continue
        if ipaddress.ip_address(ip).is_private and not ip.startswith("127."):
            return ip
    return "127.0.0.1"


async def serve(args) -> int:
    store = Store(Path(args.data).resolve())
    history = History(store.path.with_name(store.path.stem + "-history.db"))
    hub = Hub(store, history)
    strip_server = await asyncio.start_server(hub.accept, args.bind, STRIP_PORT, limit=MAX_LINE)
    ip = args.public_ip or guess_lan_ip()
    web = WebServer((args.bind, args.web_port), hub, asyncio.get_running_loop(), args.token, ip)
    threading.Thread(target=web.serve_forever, daemon=True).start()

    url = "http://%s:%d/" % (ip, args.web_port)
    print("Darwish Smart Power %s" % VERSION)
    print("  website          %s" % url)
    print("  control panel    %spanel   (installable app, asks for the password)" % url)
    print("  strips dial      %s:%d (in other homes), %s:%d (in this home; SP_LAN_IP to change)"
          % (ip, STRIP_PORT, web.lan_ip, STRIP_PORT))
    print("  strip port       TCP %d" % STRIP_PORT)
    print("  security         %s" % ("token required" if args.token else "NO TOKEN - only use on a home network you trust"))
    print("  saved settings   %s" % store.path)
    print("  owner page       %s%s   (keep this address private)" % (url.rstrip("/"), store.owner_path()))
    print("  Android app      %sapp.apk" % url)
    print("  provision with   python3 smartpower.py provision --server-ip %s --ssid WIFI --wifi-password PASS" % ip)
    print("  Ctrl+C to stop", flush=True)
    async with strip_server:
        tasks = [hub.poll_forever(), hub.timers_forever(), hub.schedules_forever()]
        if not args.no_alexa:
            tasks.append(Alexa(hub, guess_lan_ip()).run_forever())
        if not args.no_app_download:
            tasks.append(apk_refresh_forever())
        await asyncio.gather(*tasks)
    return 0


def setup_exchange(host: str, line: str, expect: str, timeout: float = 6.0) -> str:
    """Send one line to the strip's setup service and return its one-line answer."""
    with socket.create_connection((host, SETUP_ADDR[1]), timeout=timeout) as sock:
        sock.sendall(line.encode("utf-8") + b"\r\n")
        buf = b""
        end = time.monotonic() + timeout
        while b"\n" not in buf and time.monotonic() < end:
            try:
                chunk = sock.recv(512)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
    answer = clean_line(buf)
    if expect not in answer:
        raise SystemExit("the strip answered %r to %r (expected %r)" % (answer, line.split(":")[1], expect))
    return answer


def check_provision_values(server_ip: str, ssid: str, password: str) -> str:
    try:
        server_ip = str(ipaddress.IPv4Address(server_ip.strip()))
    except ValueError:
        raise SystemExit("--server-ip must be an IPv4 address like 192.168.1.20 (got %r)" % server_ip)
    for label, value in (("SSID", ssid), ("Wi-Fi password", password)):
        if not value or any(c in value for c in ":\r\n"):
            raise SystemExit("the %s must not be empty or contain ':' or line breaks (the strip cannot accept them)" % label)
    return server_ip


def provision(args) -> int:
    server_ip = check_provision_values(args.server_ip, args.ssid, args.wifi_password)
    host = args.setup_host
    print("Waiting for the strip's setup network at %s:%d ..." % (host, SETUP_ADDR[1]))
    deadline = time.monotonic() + args.wait
    while True:
        try:
            socket.create_connection((host, SETUP_ADDR[1]), timeout=2).close()
            break
        except OSError:
            if time.monotonic() >= deadline:
                print("Could not reach the strip. Check that:")
                print("  1. the strip is in setup mode (hold its main button ~10 s until the LED blinks fast)")
                print("  2. this device is joined to the strip's Wi-Fi 'TONLY_TAP_XXXXXXX'")
                print("     (its password is LGU_ followed by the same 7 characters)")
                return 1
            time.sleep(2)
    print("  server address -> %s" % setup_exchange(host, "up:ip:" + server_ip, "ip_ok"))
    print("  home Wi-Fi     -> %s" % setup_exchange(host, "up:connect:%s:%s" % (args.ssid, args.wifi_password), "connect_ok"))
    try:                                           # leave setup mode; the strip restarts without answering
        with socket.create_connection((host, SETUP_ADDR[1]), timeout=6) as conn:
            conn.sendall(b"up:reboot:0\r\n")
    except OSError:
        pass
    print("\nDone. Switch this device back to your normal Wi-Fi.")
    print("The strip will now connect to %s:%d by itself." % (server_ip, STRIP_PORT))
    return 0


# --------------------------------------------------------------------------- selftest

async def selftest() -> None:
    tmpdir = tempfile.mkdtemp(prefix="smartpower-test-")
    hub = Hub(Store(Path(tmpdir) / "data.json"), History(Path(tmpdir) / "history.db"))
    srv = await asyncio.start_server(hub.accept, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    relays = {1: False, 2: True, 3: False, 4: False}
    energy_wh = {n: 1500 * n for n in (1, 2, 3, 4, 5)}
    sent: List[str] = []

    def getinfo() -> str:
        blocks = []
        for n in (1, 2, 3, 4, 5):
            on = relays.get(n, any(relays.values()))
            mw = 60000 if (n == 2 and on) else 0
            blocks.append("%d:%d;%s;%d;0;0;%d;%08X;00000000;00000000;0;00;%d" % (n, n * 10, "on" if on else "off", on, mw, energy_wh[n], 30 + n))
        return "up:getinfo:" + ":".join(blocks)

    async def fake_strip(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            raw = await reader.readline()
            if not raw:
                return
            line = raw.decode().strip()
            sent.append(line)
            if line == "up:getinfo:all":
                writer.write(getinfo().encode() + b"\x00\x00\r\n")
            elif line.startswith("up:onoff:"):
                _, _, n, val = line.split(":")
                relays[int(n)] = val == "on"
                writer.write(line.encode() + b"\r\n")
            await writer.drain()

    strip_task = asyncio.ensure_future(fake_strip(reader, writer))
    writer.write(b"up:bootinfo:LGU+-TAP-HW002;a1b2c3d4e5f6;a1b2c3d4e5f7;0.1.54-1.0.66;connect\r\n")
    writer.write(b"up:power_report:1:229800\r\nup:power_report:2:270\r\nup:query:-61\r\n")
    await writer.drain()
    await asyncio.sleep(0.3)

    strip = hub.strips["A1B2C3D4E5F6"]
    assert strip.online and strip.fw == "0.1.54-1.0.66"
    assert strip.outlets[2].on and not strip.outlets[1].on
    assert strip.outlets[2].watts == 60.0 and strip.outlets[3].kwh == 4.5 and strip.outlets[4].temp_c == 34
    assert strip.volts == 229.8 and strip.rssi == -61
    snap = (await hub.snapshot())["strips"][0]
    assert snap["watts"] == 60.0 and snap["amps"] == 0.26 and snap["kwh"] == 15.0

    code, body = await hub.switch("a1b2c3d4e5f6", 0, True)
    assert code == 200 and body["confirmed"] and all(relays[n] for n in OUTLETS)
    assert [s for s in sent if s.startswith("up:onoff")] == ["up:onoff:%d:on" % n for n in OUTLETS]

    relays[3] = False                               # someone presses the outlet 3 button
    writer.write(b"up:event:onoff:3:off\r\n")
    await writer.drain()
    await asyncio.sleep(0.1)
    assert not strip.outlets[3].on

    await hub.rename("A1B2C3D4E5F6", 1, "Kettle")
    assert Store(Path(tmpdir) / "data.json").name("A1B2C3D4E5F6", 1) == "Kettle"
    await hub.set_timer("A1B2C3D4E5F6", 2, 1, False)
    await hub.run_due_timers(now=time.time() + 120)
    assert not relays[2] and hub.store.timer("A1B2C3D4E5F6", 2) is None

    # energy history: the strip's counters grow, the difference lands in the current hour
    energy_wh[2] += 50
    energy_wh[3] += 25
    assert await strip.link.read_state()
    code, report = await hub.history_report("day")
    assert code == 200 and report["total_kwh"] == 0.075 and len(report["buckets"]) == 24
    assert report["by_outlet"][0] == {"strip": "A1B2C3D4E5F6", "outlet": 2, "kwh": 0.05, "cost": 0.08}
    energy_wh[2] = 10                               # counter reset on the strip: counts as 10 Wh
    assert await strip.link.read_state()
    snap = await hub.snapshot()
    assert snap["today"]["kwh"] == 0.085 and snap["strips"][0]["outlets"][1]["today_kwh"] == 0.06
    assert (await hub.history_report("week"))[1]["total_kwh"] == 0.085
    assert len((await hub.history_report("month"))[1]["buckets"]) == 30

    # rooms, icons, favourites
    await hub.set_meta("A1B2C3D4E5F6", 0, {"room": "Kitchen"})
    code, body = await hub.set_meta("A1B2C3D4E5F6", 1, {"icon": "kettle", "favorite": True})
    assert code == 200 and body["strip"]["room"] == "Kitchen"
    assert body["strip"]["outlets"][0]["icon"] == "kettle" and body["strip"]["outlets"][0]["favorite"]
    assert (await hub.set_meta("A1B2C3D4E5F6", 1, {"icon": "spaceship"}))[0] == 400

    # schedules: one due right now fires once per minute
    now = time.time()
    lt = time.localtime(now)
    code, body = await hub.save_schedule({"strip": "a1b2c3d4e5f6", "outlet": 3, "on": True,
                                          "time": "%02d:%02d" % (lt.tm_hour, lt.tm_min), "days": [lt.tm_wday]})
    assert code == 200 and not relays[3]
    await hub.run_due_schedules(now)
    assert relays[3]
    relays[3] = False
    await hub.run_due_schedules(now)
    assert not relays[3], "a schedule must not fire twice in the same minute"
    assert (await hub.save_schedule({"strip": "A1B2C3D4E5F6", "time": "25:00", "days": [1]}))[0] == 400
    assert (await hub.save_schedule({"strip": "A1B2C3D4E5F6", "time": "07:00", "days": []}))[0] == 400
    assert (await hub.delete_schedule(body["schedule"]["id"]))[0] == 200 and not hub.store.schedules

    # several outlets at once
    code, body = await hub.switch("A1B2C3D4E5F6", 0, False, outlets=[1, 3])
    assert code == 200 and not relays[1] and not relays[3]

    # PIN lock: the server refuses without the right PIN; the owner's own timers still run
    assert (await hub.set_lock("A1B2C3D4E5F6", {"pin": "12a4"}))[0] == 400
    assert (await hub.set_lock("A1B2C3D4E5F6", {"pin": "2468"}))[0] == 200
    assert (await hub.switch("A1B2C3D4E5F6", 1, True))[0] == 403
    assert (await hub.switch("A1B2C3D4E5F6", 1, True, pin="0000"))[0] == 403
    code, body = await hub.switch("A1B2C3D4E5F6", 1, True, pin="2468")
    assert code == 200 and relays[1] and body["strip"]["locked"]
    assert (await hub.set_timer("A1B2C3D4E5F6", 1, 5, False))[0] == 403
    await hub.set_timer("A1B2C3D4E5F6", 1, 1, False, pin="2468")
    await hub.run_due_timers(now=time.time() + 120)
    assert not relays[1]
    assert (await hub.set_lock("A1B2C3D4E5F6", {"pin": ""}))[0] == 403          # removing needs the old PIN
    assert (await hub.set_lock("A1B2C3D4E5F6", {"pin": "", "old_pin": "2468"}))[0] == 200
    assert not hub.store.locked("A1B2C3D4E5F6")

    # scenes switch groups of outlets in one go
    code, body = await hub.save_scene({"name": "Movie night", "icon": "🎬", "actions": [
        {"strip": "A1B2C3D4E5F6", "outlet": 1, "on": True}, {"strip": "A1B2C3D4E5F6", "outlet": 4, "on": True},
        {"strip": "A1B2C3D4E5F6", "outlet": 2, "on": False}]})
    assert code == 200
    code, res = await hub.run_scene(body["scene"]["id"])
    assert code == 200 and relays[1] and relays[4] and not relays[2]
    assert (await hub.save_scene({"name": "Empty", "actions": []}))[0] == 400
    assert (await hub.delete_scene(body["scene"]["id"]))[0] == 200

    # cycle: 1 minute on, 1 minute off, repeating
    relays[3] = False
    code, body = await hub.save_schedule({"strip": "A1B2C3D4E5F6", "kind": "cycle", "outlets": [3],
                                          "on_minutes": 1, "off_minutes": 1}, now=now)
    assert code == 200 and body["schedules"][0]["phase_on"]
    await hub.run_due_schedules(now + 1)
    assert relays[3]
    await hub.run_due_schedules(now + 61)
    assert not relays[3]
    await hub.run_due_schedules(now + 121)
    assert relays[3]
    assert (await hub.save_schedule({"strip": "A1B2C3D4E5F6", "kind": "cycle", "outlets": [3],
                                     "on_minutes": 0, "off_minutes": 5}))[0] == 400
    await hub.delete_schedule(body["schedule"]["id"])

    # monitoring rules: a charger drawing under 3 W for a while is switched off; a too-big load only reported
    strip_a = hub.strips["A1B2C3D4E5F6"]
    assert (await hub.save_schedule({"strip": "A1B2C3D4E5F6", "kind": "watch", "outlets": [1], "metric": "power",
                                     "above": False, "value": 3, "seconds": 5}))[0] == 400     # too short
    assert (await hub.save_schedule({"strip": "A1B2C3D4E5F6", "kind": "watch", "outlets": [1], "metric": "volts",
                                     "value": 3, "seconds": 60}))[0] == 400
    code, body = await hub.save_schedule({"strip": "A1B2C3D4E5F6", "kind": "watch", "outlets": [1], "metric": "power",
                                          "above": False, "value": 3, "seconds": 600})
    assert code == 200
    rule_id = body["schedule"]["id"]
    code, body = await hub.save_schedule({"strip": "A1B2C3D4E5F6", "kind": "watch", "outlets": [2], "metric": "power",
                                          "above": True, "value": 1500, "seconds": 30, "action": "alert"})
    alert_id = body["schedule"]["id"]
    await hub.switch("A1B2C3D4E5F6", 1, True)
    strip_a.outlets[1].on, strip_a.outlets[1].watts = True, 1.2
    strip_a.outlets[2].on, strip_a.outlets[2].watts = True, 2000.0
    await hub.run_watches(now)
    await hub.run_watches(now + 300)
    assert relays[1]                                                        # not long enough yet
    strip_a.outlets[1].on, strip_a.outlets[1].watts = True, 1.2
    strip_a.outlets[2].on, strip_a.outlets[2].watts = True, 2000.0
    await hub.run_watches(now + 601)
    assert not relays[1]                                                    # charged: switched off
    kinds = [(e["kind"], e["outlet"]) for e in (await hub.events(0))["events"]]
    assert ("rule_power_off", 1) in kinds and ("rule_power", 2) in kinds
    strip_a.outlets[2].on, strip_a.outlets[2].watts = True, 2000.0
    await hub.run_watches(now + 700)                                        # acts once until it clears
    assert sum(1 for e in (await hub.events(0))["events"] if e["kind"] == "rule_power") == 1
    for rid in (rule_id, alert_id):
        await hub.delete_schedule(rid)
    hub.history.db.execute("DELETE FROM events")
    hub.history.db.commit()

    # alerts: hot outlet (thresholds come from the settings)
    assert (await hub.update_settings({"max_temp_c": 5}))[0] == 400
    await hub.update_settings({"max_temp_c": 34, "price_kwh": 2})
    hub.check_alerts(now)
    hub.check_alerts(now + 60)                      # repeated alerts are rate-limited
    events = (await hub.events(0))["events"]
    assert [(e["kind"], e["outlet"]) for e in events] == [("temp", 4)]
    assert hub.cost(1.5) == 3.0
    # the strip's own protection cut an outlet off: its flags read "off" once tripped, event 01/02
    tripped = parse_getinfo("1:0;off;3;off;on;0;00000000;00000000;00000000;off;01;30:2:0;off;3;on;off;0;00000000;"
                            "00000000;00000000;off;02;95:3:0;on;3;on;on;895;00000003;00000000;00000000;off;00;30:"
                            "4:0;off;3;on;on;0;00000000;00000000;00000000;off;00;30")
    assert [tripped[n]["trip"] for n in OUTLETS] == [TRIP_OVERLOAD, TRIP_OVERHEAT, 0, 0]
    hub.strips["A1B2C3D4E5F6"].outlets[1].trip = TRIP_OVERLOAD
    hub.check_alerts(now + 120)
    hub.strips["A1B2C3D4E5F6"].outlets[1].trip = 0
    trips = [e for e in (await hub.events(0))["events"] if e["kind"] == "trip"]
    assert [(e["outlet"], e["value"]) for e in trips] == [(1, TRIP_OVERLOAD)]

    # the energy log as an Excel file
    hub.history.record("C0FFEE0E0E0E", {2: 1000.0}, now=now - 3600)
    hub.history.record("C0FFEE0E0E0E", {2: 1250.0}, now=now)
    code, book = await hub.history_xlsx("week", now=now, allowed={"C0FFEE0E0E0E"})
    assert code == 200 and (await hub.history_xlsx("decade"))[0] == 400
    with zipfile.ZipFile(io.BytesIO(book)) as z:
        assert z.testzip() is None and "xl/worksheets/sheet3.xml" in z.namelist()
        daily = z.read("xl/worksheets/sheet2.xml").decode()
        assert "rightToLeft" in daily and "<v>0.25</v>" in daily and "المخرج" in daily
    assert "Daily" in zipfile.ZipFile(io.BytesIO((await hub.history_xlsx("year", lang="en", now=now))[1])).read(
        "xl/workbook.xml").decode()

    # web layer, called from a worker thread like a real request
    web = WebServer(("127.0.0.1", 0), hub, asyncio.get_running_loop(), "s3cret", "127.0.0.1")
    threading.Thread(target=web.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % web.server_address[1]

    def http(path: str, body: Optional[dict] = None, token: Optional[str] = "s3cret") -> Tuple[int, Any]:
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as err:
            return err.code, None

    loop = asyncio.get_running_loop()
    assert (await loop.run_in_executor(None, http, "/api/state", None, None))[0] == 401

    def page(path: str) -> str:                     # public website pages need no password
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.read().decode()
    assert "Privacy Policy" in await loop.run_in_executor(None, page, "/privacy")
    owner_page = hub.store.owner_path()
    assert owner_page.startswith("/owner-") and "<title>Admin" in await loop.run_in_executor(None, page, owner_page)
    assert hub.store.owner_path() == owner_page                               # stays the same
    admin_html = await loop.run_in_executor(None, page, owner_page)
    assert ('href="%s.webmanifest"' % owner_page) in admin_html and "__OWNER_PATH__" not in admin_html
    app = json.loads(await loop.run_in_executor(None, page, owner_page + ".webmanifest"))   # installs as its own app
    assert app["start_url"] == app["scope"] == owner_page and any(i["purpose"] == "maskable" for i in app["icons"])
    assert all((WEBSITE / i["src"].lstrip("/")).is_file() for i in app["icons"])
    try:
        await loop.run_in_executor(None, page, "/admin")
        raise AssertionError("/admin must not exist")
    except urllib.error.HTTPError as err:
        assert err.code == 401                                                # not a page: same as any unknown path

    # ---- protection: limits per internet address, impersonation, silent connections, password guessing
    g, t0 = Guard(), 1_000_000.0
    for _ in range(MAX_CONNECTS_PER_MINUTE):                                  # reconnecting is fine ...
        assert g.connection_opened("41.65.227.203", t0)
        g.connection_closed("41.65.227.203")
    assert not g.connection_opened("41.65.227.203", t0) and g.is_blocked("41.65.227.203", t0)  # ... a flood is not
    assert all(g.connection_opened("41.65.227.204", t0 + i) for i in range(MAX_CONNECTIONS_PER_IP))
    assert not g.connection_opened("41.65.227.204", t0 + 99)                   # too many open at once
    assert not g.is_blocked("41.65.227.203", t0 + BLOCK_SECONDS + 1)          # blocks run out
    assert all(g.connection_opened("192.168.1.50", t0) for _ in range(100))  # the home network is never limited
    assert all(g.may_add_strip("196.135.103.190", 0, t0) for _ in range(100))  # no limit per home
    assert not g.may_add_strip("196.135.103.191", MAX_UNKNOWN_STRIPS, t0)
    for _ in range(MAX_STRIKES):
        g.strike("196.135.103.192", "test", t0)
    assert g.is_blocked("196.135.103.192", t0) and not g.is_blocked("10.0.0.3", t0)
    for _ in range(30):                                                       # an app with an old password
        g.login_failed("196.135.103.193", ["old-password"], t0)
    assert not g.is_blocked("196.135.103.193", t0)
    for n in range(LOGIN_FAILS):                                              # someone guessing
        g.login_failed("196.135.103.193", ["guess-%d" % n], t0)
    assert g.is_blocked("196.135.103.193", t0) and g.report(t0)["blocked"]

    class Impostor:
        address, closed = "41.65.227.2046", False
    real = hub.strips["A1B2C3D4E5F6"]
    assert real.online and hub.attach(Impostor(), {"mac": "A1B2C3D4E5F6", "model": "x", "fw": "x"}) is None
    assert hub.strips["A1B2C3D4E5F6"].link is real.link                       # the real strip keeps its link

    global HELLO_TIMEOUT
    saved_timeout, HELLO_TIMEOUT = HELLO_TIMEOUT, 0.5
    silent_r, silent_w = await asyncio.open_connection("127.0.0.1", srv.sockets[0].getsockname()[1])
    assert (await asyncio.wait_for(silent_r.read(), 5)).startswith(b"up:bootinfo")  # asked, then dropped for saying nothing
    silent_w.close()
    HELLO_TIMEOUT = saved_timeout
    assert not hub.guard.is_blocked("127.0.0.1")
    # firmware that waits for the server to speak first is asked, then says hello
    saved_probe = PROBE_AFTER
    globals()["PROBE_AFTER"] = 0.2
    shy_r, shy_w = await asyncio.open_connection("127.0.0.1", srv.sockets[0].getsockname()[1])
    assert (await asyncio.wait_for(shy_r.readline(), 5)).strip() == b"up:bootinfo"
    shy_w.write(b"up:bootinfo:lgutap;C0FFEE0C0003;C0FFEE0C0003;1.0;connect\r\n")
    await shy_w.drain()
    for _ in range(100):
        if "C0FFEE0C0003" in hub.strips and hub.strips["C0FFEE0C0003"].online:
            break
        await asyncio.sleep(0.05)
    assert hub.strips["C0FFEE0C0003"].online
    shy_w.close()
    await hub.remove_strip("C0FFEE0C0003", block=False)
    globals()["PROBE_AFTER"] = saved_probe
    # other firmware may greet a little differently: a lone CR, no line end at all, other case, one MAC
    assert parse_bootinfo("UP:BOOTINFO:lgutap;88d0393a7b5d;connect")["mac"] == "88D0393A7B5D"
    assert parse_bootinfo("x up:bootinfo:lgutap;88D0393A7B5D;88D0393A7B5E;0.1.54;connect")["mac2"] == "88D0393A7B5E"
    assert parse_bootinfo("up:bootinfo:lgutap;nomac;connect") is None and parse_bootinfo("up:getinfo:all") is None
    for mac, hello in (("C0FFEE0C0001", b"up:bootinfo:lgutap;C0FFEE0C0001;C0FFEE0C0001;1.0;connect\r"),
                       ("C0FFEE0C0002", b"up:bootinfo:lgutap;C0FFEE0C0002;;1.0;connect")):
        odd_r, odd_w = await asyncio.open_connection("127.0.0.1", srv.sockets[0].getsockname()[1])
        odd_w.write(hello)
        await odd_w.drain()
        for _ in range(100):
            if mac in hub.strips and hub.strips[mac].online:
                break
            await asyncio.sleep(0.05)
        assert hub.strips[mac].online, mac
        odd_w.close()
        await hub.remove_strip(mac, block=False)

    def guess(token: str, ip: str = "156.200.1.77") -> int:
        req = urllib.request.Request(base + "/api/state", headers={"X-Token": token, "CF-Connecting-IP": ip})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as err:
            return err.code
    codes = [await loop.run_in_executor(None, guess, "wrong-%d" % n) for n in range(LOGIN_FAILS + 1)]
    assert codes[0] == 401 and codes[-1] == 429
    assert await loop.run_in_executor(None, guess, "s3cret") == 429           # blocked even with the password
    assert await loop.run_in_executor(None, guess, "s3cret", "156.200.1.78") == 200
    status, sec = await loop.run_in_executor(None, http, "/api/security")
    assert status == 200 and any(b["ip"] == "156.200.1.77" for b in sec["blocked"])

    # ---- approval: strips from the internet wait for the owner; strips the app announced get in
    class Remote:
        def __init__(self, address: str):
            self.address, self.closed = address, False

        def close(self) -> None:
            self.closed = True
    stranger = Remote("102.184.161.186")
    boot = {"mac": "C0FFEE0BF7E1", "mac2": "C0FFEE0BF7E2", "model": "lgutap", "fw": "x"}
    assert hub.attach(stranger, boot) is not None
    status, state = await loop.run_in_executor(None, http, "/api/state")
    assert all(x["id"] != "C0FFEE0BF7E1" for x in state["strips"])
    assert [x["id"] for x in state["new_strips"]] == ["C0FFEE0BF7E1"]
    assert (await loop.run_in_executor(None, http, "/api/switch", {"strip": "C0FFEE0BF7E1", "outlet": 1, "on": True}))[0] == 404
    status, _ = await loop.run_in_executor(None, http, "/api/strips/remove", {"strip": "C0FFEE0BF7E1", "block": True})
    assert status == 200 and stranger.closed and hub.attach(Remote("102.184.161.186"), boot) is None
    status, state = await loop.run_in_executor(None, http, "/api/state")
    assert state["blocked_strips"] == ["C0FFEE0BF7E1"] and not state["new_strips"]
    assert (await loop.run_in_executor(None, http, "/api/strips/approve", {"strip": "C0FFEE0BF7E1"}))[0] == 200
    assert hub.attach(Remote("102.184.161.186"), boot) is not None and hub.store.is_approved("C0FFEE0BF7E1")
    assert (await loop.run_in_executor(None, http, "/api/strips/expect", {"code": "zz"}))[0] == 400
    assert (await loop.run_in_executor(None, http, "/api/strips/expect", {"code": "bbddf2"}))[0] == 200
    announced = {"mac": "C0FFEEBBDDF1", "mac2": "C0FFEEBBDDF2", "model": "lgutap", "fw": "x"}
    assert hub.attach(Remote("154.176.120.174"), announced) is not None and hub.store.is_approved("C0FFEEBBDDF1")
    for mac in ("C0FFEE0BF7E1", "C0FFEEBBDDF1"):
        assert (await loop.run_in_executor(None, http, "/api/strips/remove", {"strip": mac}))[0] == 200
    assert "C0FFEE0BF7E1" not in hub.strips and not hub.store.blocked_strips

    # ---- customer accounts: sign up, own strips only, sign in again, delete; admin page and downloads
    def post(path: str, body: dict, token: Optional[str] = None, ip: str = "156.200.9.9") -> Tuple[int, Any]:
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "CF-Connecting-IP": ip,
                                              **({"X-Token": token} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")
    assert normalise_login(" ٠١٠ 1234-5678 ") == "01012345678" and normalise_login("A@B.co") == "a@b.co"
    assert normalise_login("hello") is None
    status, res = await loop.run_in_executor(None, post, "/api/signup", {"name": "Ali", "login": "01012345678", "password": "123"})
    assert status == 400 and res["field"] == "password"
    status, res = await loop.run_in_executor(None, post, "/api/signup", {"name": "Ali", "login": "010 1234 5678", "password": "secret12"})
    assert status == 200 and res["token"]
    ali = res["token"]
    assert (await loop.run_in_executor(None, post, "/api/signup", {"name": "X", "login": "01012345678", "password": "secret12"}))[0] == 409
    status, state = await loop.run_in_executor(None, http, "/api/state", None, ali)
    assert status == 200 and state["me"]["role"] == CUSTOMER and state["strips"] == [] and "new_strips" not in state
    web.public_ip, web.lan_ip = "41.38.141.215", "192.168.1.50"
    status, res = await loop.run_in_executor(None, post, "/api/strips/expect", {"code": "AA0001"}, ali)
    assert status == 200 and res["strip_ip"] == "41.38.141.215"          # a strip in another home dials the internet IP
    status, res = await loop.run_in_executor(None, post, "/api/strips/expect", {"code": "AA0001"}, ali, "41.38.141.215")
    assert status == 200 and res["strip_ip"] == "192.168.1.50"           # one in the server's own home dials it at home
    web.public_ip = "127.0.0.1"
    assert hub.attach(Remote("41.33.1.1"), {"mac": "C0FFEEAA0001", "mac2": "", "model": "lgutap", "fw": "x"}) is not None
    status, state = await loop.run_in_executor(None, http, "/api/state", None, ali)
    assert [x["id"] for x in state["strips"]] == ["C0FFEEAA0001"]       # the strip they set up, and nothing else
    assert (await loop.run_in_executor(None, post, "/api/switch", {"strip": "A1B2C3D4E5F6", "outlet": 1, "on": True}, ali))[0] == 403
    assert (await loop.run_in_executor(None, post, "/api/users/add", {"name": "Y", "role": "control"}, ali))[0] == 403
    # a customer's own price and alert limits; the owner's stay as they were
    status, res = await loop.run_in_executor(None, post, "/api/my/settings", {"price_kwh": 2.5, "max_watts": 1200}, ali)
    assert status == 200 and res["settings"]["price_kwh"] == 2.5 and res["settings"]["own"]
    assert (await loop.run_in_executor(None, post, "/api/my/settings", {"price_kwh": -1}, ali))[0] == 400
    status, state = await loop.run_in_executor(None, http, "/api/state", None, ali)
    assert state["settings"]["price_kwh"] == 2.5 and "signup" not in state["settings"] and "month" in state
    assert hub.store.settings["price_kwh"] != 2.5 and hub.strip_limits()["C0FFEEAA0001"]["max_watts"] == 1200
    assert (await loop.run_in_executor(None, http, "/api/my/settings", {"price_kwh": 2}))[0] == 403      # the owner has /api/settings
    status, report = await loop.run_in_executor(None, http, "/api/history?range=day", None, ali)
    assert status == 200 and report["price_kwh"] == 2.5
    # the people a customer invites see and control just that customer's strips, and manage nothing
    status, res = await loop.run_in_executor(None, post, "/api/household/add", {"name": "Mona", "role": "view"}, ali)
    assert status == 200 and len(res["token"]) >= 16 and [m["name"] for m in res["members"]] == ["Mona"]
    mona, mona_id = res["token"], res["member"]["id"]
    assert (await loop.run_in_executor(None, post, "/api/household/add", {"name": "X", "role": "owner"}, ali))[0] == 400
    status, state = await loop.run_in_executor(None, http, "/api/state", None, mona)
    assert status == 200 and state["me"]["role"] == "view" and state["me"]["home"] == "Ali"
    assert [x["id"] for x in state["strips"]] == ["C0FFEEAA0001"] and state["settings"]["price_kwh"] == 2.5
    assert (await loop.run_in_executor(None, post, "/api/switch", {"strip": "C0FFEEAA0001", "outlet": 1, "on": True}, mona))[0] == 403
    assert (await loop.run_in_executor(None, post, "/api/household/update", {"id": mona_id, "role": "control"}, ali))[0] == 200
    assert (await loop.run_in_executor(None, post, "/api/switch", {"strip": "A1B2C3D4E5F6", "outlet": 1, "on": True}, mona))[0] == 403
    for path in ("/api/household/add", "/api/household/delete", "/api/my/settings", "/api/strips/remove", "/api/users/add",
                 "/api/settings", "/api/account/delete"):
        assert (await loop.run_in_executor(None, post, path, {"name": "Z", "id": mona_id, "strip": "C0FFEEAA0001",
                                                                "price_kwh": 1}, mona))[0] in (400, 403), path
    assert (await loop.run_in_executor(None, http, "/api/household", None, mona))[0] == 403
    assert (await loop.run_in_executor(None, http, "/api/household", None, ali))[1]["members"][0]["role"] == "control"
    assert all(u["id"] != mona_id for u in (await hub.users())["users"])          # not in the owner's family
    assert (await hub.admin_report())["customers"][0]["household"] == 1
    assert (await loop.run_in_executor(None, post, "/api/strips/expect", {"code": "AB0002"}, mona))[0] == 200
    assert hub.store.expected["AB0002"]["by"] == hub.store.account_by_login("01012345678")["id"]   # set up for Ali's home
    hub.store.expected.pop("AB0002")
    status, res = await loop.run_in_executor(None, post, "/api/household/update", {"id": mona_id, "new_token": True}, ali)
    assert status == 200 and (await loop.run_in_executor(None, http, "/api/state", None, mona))[0] == 401
    mona = res["token"]
    assert (await loop.run_in_executor(None, http, "/api/state", None, mona))[0] == 200
    assert (await loop.run_in_executor(None, post, "/api/household/delete", {"id": mona_id}, ali))[0] == 200
    assert (await loop.run_in_executor(None, http, "/api/state", None, mona))[0] == 401
    hub.guard.login_fails.clear()
    assert (await loop.run_in_executor(None, post, "/api/my/settings", {"reset": True}, ali))[1]["settings"]["own"] is False
    assert (await loop.run_in_executor(None, post, "/api/login", {"login": "01012345678", "password": "nope"}))[0] == 401
    status, res = await loop.run_in_executor(None, post, "/api/login", {"login": "+01012345678".lstrip("+"), "password": "secret12"})
    assert status == 200 and res["token"] != ali
    assert all(u.get("role") != CUSTOMER for u in (await hub.users())["users"])     # not in the family list
    status, report = await loop.run_in_executor(None, http, "/api/admin")
    assert status == 200 and report["totals"]["customers"] == 1 and report["customers"][0]["strips"] == 1
    assert any(x["owners"] == ["Ali"] for x in report["strips"])
    assert (await loop.run_in_executor(None, http, "/api/admin", None, ali))[0] == 403
    status, _ = await loop.run_in_executor(None, http, "/api/strips/assign", {"strip": "A1B2C3D4E5F6", "customer": report["customers"][0]["id"]})
    assert status == 200 and "A1B2C3D4E5F6" in [x["id"] for x in (await loop.run_in_executor(None, http, "/api/state", None, ali))[1]["strips"]]
    assert (await loop.run_in_executor(None, http, "/api/strips/assign", {"strip": "A1B2C3D4E5F6", "customer": ""}))[0] == 200
    # a strip reserved for the customer before it was ever plugged in goes to them when it connects, from anywhere
    cust_id = report["customers"][0]["id"]
    status, _ = await loop.run_in_executor(None, http, "/api/strips/reserve", {"code": "TONLY_TAP_77AB12", "customer": cust_id})
    assert status == 200 and any(r["code"] == "77AB12" for r in (await hub.admin_report())["reservations"])
    assert hub.attach(Remote("73.10.20.30"), {"mac": "C0FFEE77AB12", "mac2": "", "model": "lgutap", "fw": "x"}) is not None
    assert "C0FFEE77AB12" in [x["id"] for x in (await loop.run_in_executor(None, http, "/api/state", None, ali))[1]["strips"]]
    assert (await loop.run_in_executor(None, http, "/api/strips/reserve", {"code": "zz", "customer": cust_id}))[0] == 400
    assert (await loop.run_in_executor(None, http, "/api/strips/reserve", {"code": "ABCDEF", "customer": cust_id}))[0] == 200
    assert (await loop.run_in_executor(None, http, "/api/strips/unreserve", {"code": "ABCDEF"}))[0] == 200
    assert not (await hub.admin_report())["reservations"]
    # a strip whose setup code is not in its MAC waits, but the admin page says whose it probably is
    assert (await loop.run_in_executor(None, post, "/api/strips/expect", {"code": "Q7X91C0"}, ali, "102.40.7.7"))[0] == 200
    assert hub.attach(Remote("102.40.7.7"), {"mac": "C0FFEE5A5A5A", "mac2": "C0FFEE5A5A5B", "model": "lgutap", "fw": "x"}) is not None
    assert not hub.store.is_approved("C0FFEE5A5A5A") and hub.store.waiting["C0FFEE5A5A5A"]["address"] == "102.40.7.7"
    restarted = Hub(Store(hub.store.path), hub.history)          # still listed after a restart, as offline
    listed = {x["id"]: x for x in (await restarted.admin_report())["new_strips"]}
    assert "C0FFEE5A5A5A" in listed and not listed["C0FFEE5A5A5A"]["online"] and listed["C0FFEE5A5A5A"]["guesses"]
    waiting = next(x for x in (await hub.admin_report())["new_strips"] if x["id"] == "C0FFEE5A5A5A")
    best = waiting["guesses"][0]
    assert best["id"] == cust_id and best["customer"] and set(best["why"]) == {"app", "network"} and "Q7X91C0" in best["codes"]
    status, _ = await loop.run_in_executor(None, http, "/api/strips/approve", {"strip": "C0FFEE5A5A5A", "customer": cust_id})
    assert status == 200 and "C0FFEE5A5A5A" not in hub.store.waiting
    assert "C0FFEE5A5A5A" in [x["id"] for x in (await loop.run_in_executor(None, http, "/api/state", None, ali))[1]["strips"]]
    # the app could only announce the strip after it had connected: from the strip's network it is handed over,
    # from anywhere else it keeps waiting for the owner
    assert hub.attach(Remote("102.60.6.6"), {"mac": "C0FFEE96BB29", "mac2": "", "model": "lgutap", "fw": "x"}) is not None
    assert (await loop.run_in_executor(None, post, "/api/strips/expect", {"code": "E96BB29"}, ali, "102.70.7.7"))[0] == 200
    assert not hub.store.is_approved("C0FFEE96BB29")
    assert hub.attach(Remote("102.60.6.6"), {"mac": "C0FFEE96BB29", "mac2": "", "model": "lgutap", "fw": "x"}) is not None
    assert not hub.store.is_approved("C0FFEE96BB29")                     # a reconnect does not hand it over either
    status, res2 = await loop.run_in_executor(None, post, "/api/strips/expect", {"code": "E96BB29"}, ali, "102.60.6.6")
    assert status == 200 and res2.get("approved") and hub.store.is_approved("C0FFEE96BB29")
    assert "C0FFEE96BB29" in [x["id"] for x in (await loop.run_in_executor(None, http, "/api/state", None, ali))[1]["strips"]]
    await hub.remove_strip("C0FFEE96BB29", block=False)
    # a home with many strips: approved ones reconnecting (after a restart) and announced ones never hit the limit
    for n in range(40):
        mac = "C0FFEE77%04d" % n
        hub.store.approve(mac)
        assert hub.attach(Remote("102.80.8.8"), {"mac": mac, "mac2": "", "model": "lgutap", "fw": "x"}) is not None
    for n in range(40):
        hub.store.expect("F%06d" % n, "", time.time())               # announced from the app
        assert hub.attach(Remote("102.80.8.8"), {"mac": "C0FFEF%06d" % n, "mac2": "", "model": "lgutap", "fw": "x"}) is not None
    assert not hub.guard.is_blocked("102.80.8.8")
    for n in range(40):
        await hub.remove_strip("C0FFEE77%04d" % n, block=False)
        await hub.remove_strip("C0FFEF%06d" % n, block=False)
    # two accounts claiming one setup code (a neighbour who saw its Wi-Fi name): it waits for the owner
    status, nres = await loop.run_in_executor(None, post, "/api/signup", {"name": "N", "login": "n@example.com", "password": "secret12"}, None, "102.50.1.1")
    assert status == 200
    nres_id = next(u["id"] for u in hub.store.users if u.get("login") == "n@example.com")
    assert (await loop.run_in_executor(None, post, "/api/strips/expect", {"code": "CC0001"}, ali, "102.40.7.7"))[0] == 200
    assert (await loop.run_in_executor(None, post, "/api/strips/expect", {"code": "CC0001"}, nres["token"], "102.50.1.1"))[0] == 200
    assert hub.attach(Remote("102.40.7.7"), {"mac": "C0FFEECC0001", "mac2": "", "model": "lgutap", "fw": "x"}) is not None
    assert not hub.store.is_approved("C0FFEECC0001")
    await hub.remove_strip("C0FFEECC0001", block=False)
    # an account may not flood the server with setup codes
    codes = [(await loop.run_in_executor(None, post, "/api/strips/expect", {"code": "DD%04d" % n}, nres["token"], "102.50.1.1"))[0]
             for n in range(ANNOUNCES_PER_HOUR + 1)]
    assert codes[-1] == 429 and 429 not in codes[:-2]
    # guessing one account's password from many addresses pauses that account, even for the right password
    for n in range(ACCOUNT_FAILS):
        assert (await loop.run_in_executor(None, post, "/api/login", {"login": "n@example.com", "password": "bad%d" % n}, None, "103.0.0.%d" % n))[0] == 401
    assert (await loop.run_in_executor(None, post, "/api/login", {"login": "n@example.com", "password": "secret12"}, None, "103.1.1.1"))[0] == 429
    assert (await loop.run_in_executor(None, post, "/api/login", {"login": "01012345678", "password": "secret12"}, None, "103.1.1.1"))[0] == 200
    # the customer forgot the password: the owner sets a new one, which also lets a paused account in again
    assert (await loop.run_in_executor(None, http, "/api/customers/password", {"customer": nres_id, "password": "short"}))[0] == 400
    assert (await loop.run_in_executor(None, post, "/api/customers/password", {"customer": nres_id, "password": "new-pass-9"}, ali))[0] == 403
    assert (await loop.run_in_executor(None, http, "/api/customers/password", {"customer": nres_id, "password": "new-pass-9"}))[0] == 200
    assert (await loop.run_in_executor(None, http, "/api/state", None, nres["token"]))[0] == 401       # old phones signed out
    assert (await loop.run_in_executor(None, post, "/api/login", {"login": "n@example.com", "password": "new-pass-9"}, None, "103.1.1.2"))[0] == 200
    assert (await loop.run_in_executor(None, post, "/api/signup", {"name": "S", "login": "s@example.com", "password": "seven77"}, None, "102.50.1.9"))[0] == 400
    nres = (await loop.run_in_executor(None, post, "/api/login", {"login": "n@example.com", "password": "new-pass-9"}, None, "103.1.1.2"))[1]
    # every reply carries the browser protections, and customers' strips stay off the owner's local Alexa
    with urllib.request.urlopen(base + "/privacy", timeout=10) as r:
        assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Referrer-Policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"] and VERSION not in r.headers["Server"]
    local = Alexa(hub, "127.0.0.1").devices()
    assert not any(k.startswith("C0FFEEAA0001/") for k in local)
    assert (await loop.run_in_executor(None, post, "/api/account/delete", {}, nres["token"], "102.50.1.1"))[0] == 200
    assert (await loop.run_in_executor(None, post, "/api/strips/assign", {"strip": "A1B2C3D4E5F6", "customer": ""}, ali))[0] == 403
    before = report["totals"]["downloads"]
    hub.store.count_download("41.33.1.1", time.time())
    hub.store.count_download("41.33.1.1", time.time())                          # same phone again: not counted
    hub.store.count_download("41.33.1.2", time.time())
    assert (await hub.admin_report())["totals"]["downloads"] == before + 2
    # ---- Alexa: account linking (OAuth) and smart home directives
    from http.client import HTTPConnection

    def raw(method: str, path: str, body: str = "", headers: Optional[dict] = None) -> Tuple[int, Dict[str, str], bytes]:
        conn = HTTPConnection("127.0.0.1", web.server_address[1], timeout=10)
        conn.request(method, path, body=body.encode(), headers=headers or {})
        resp = conn.getresponse()
        out = (resp.status, dict(resp.getheaders()), resp.read())
        conn.close()
        return out
    redirect = "https://pitangui.amazon.com/api/skill/link/M2X"
    query = "/oauth/authorize?client_id=alexa&response_type=code&state=st8&redirect_uri=" + quote(redirect, safe="")
    assert (await loop.run_in_executor(None, raw, "GET", query))[0] == 400            # no secret made yet
    status, made = await loop.run_in_executor(None, http, "/api/oauth/secret", {"client": "alexa"})
    assert status == 200 and len(made["client_secret"]) > 20
    secret = made["client_secret"]
    status, _, page_html = await loop.run_in_executor(None, raw, "GET", query)
    assert status == 200 and b'name="state" value="st8"' in page_html
    assert (await loop.run_in_executor(None, raw, "GET", query.replace("pitangui.amazon.com", "evil.example")))[0] == 400
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    fields = "client_id=alexa&state=st8&redirect_uri=%s" % quote(redirect, safe="")
    status, _, _ = await loop.run_in_executor(None, raw, "POST", "/oauth/authorize", fields + "&login=01012345678&password=bad", form)
    assert status == 401
    status, hdrs, _ = await loop.run_in_executor(None, raw, "POST", "/oauth/authorize",
                                                 fields + "&login=01012345678&password=secret12", form)
    assert status == 302 and hdrs["Location"].startswith(redirect + "?code=") and hdrs["Location"].endswith("&state=st8")
    code = parse_qs(urlsplit(hdrs["Location"]).query)["code"][0]
    basic = {"Authorization": "Basic " + base64.b64encode(("alexa:" + secret).encode()).decode(), **form}
    grant = "grant_type=authorization_code&code=%s&redirect_uri=%s" % (code, quote(redirect, safe=""))
    assert (await loop.run_in_executor(None, raw, "POST", "/oauth/token", grant,
                                       {**form, "Authorization": "Basic " + base64.b64encode(b"alexa:wrong").decode()}))[0] == 401
    status, _, tok = await loop.run_in_executor(None, raw, "POST", "/oauth/token", grant, basic)
    tokens = json.loads(tok)
    assert status == 200 and tokens["access_token"] and tokens["refresh_token"]
    assert (await loop.run_in_executor(None, raw, "POST", "/oauth/token", grant, basic))[0] == 400   # a code works once
    status, _, tok = await loop.run_in_executor(None, raw, "POST", "/oauth/token",
                                                "grant_type=refresh_token&refresh_token=" + tokens["refresh_token"], basic)
    assert status == 200 and json.loads(tok)["access_token"] != tokens["access_token"]

    def directive(ns: str, name: str, token: str, endpoint: Optional[str] = None) -> dict:
        d: Dict[str, Any] = {"header": {"namespace": ns, "name": name, "payloadVersion": "3", "messageId": "m1",
                                        "correlationToken": "c1"}, "payload": {}}
        if endpoint:
            d["endpoint"] = {"endpointId": endpoint, "scope": {"type": "BearerToken", "token": token}}
        else:
            d["payload"] = {"scope": {"type": "BearerToken", "token": token}}
        return {"directive": d}
    found = await hub.alexa(directive("Alexa.Discovery", "Discover", tokens["access_token"]))
    ids = [e["endpointId"] for e in found["event"]["payload"]["endpoints"]]
    assert {i.split("-")[0] for i in ids} == {"C0FFEEAA0001", "C0FFEE77AB12", "C0FFEE5A5A5A"} and len(ids) == 15  # theirs only
    bad = await hub.alexa(directive("Alexa.Discovery", "Discover", "nope"))
    assert bad["event"]["payload"]["type"] == "INVALID_AUTHORIZATION_CREDENTIAL"
    nosuch = await hub.alexa(directive("Alexa.PowerController", "TurnOn", tokens["access_token"], "A1B2C3D4E5F6-1"))
    assert nosuch["event"]["payload"]["type"] == "NO_SUCH_ENDPOINT"            # someone else's strip
    owner_tok = hub.store.oauth_tokens("alexa", "owner", time.time())["access_token"]
    relays[2] = False
    on = await hub.alexa(directive("Alexa.PowerController", "TurnOn", owner_tok, "A1B2C3D4E5F6-2"))
    assert on["event"]["header"]["name"] == "Response" and on["event"]["header"]["correlationToken"] == "c1"
    assert on["context"]["properties"][0]["value"] == "ON" and relays[2]
    state_report = await hub.alexa(directive("Alexa", "ReportState", owner_tok, "A1B2C3D4E5F6-2"))
    assert state_report["event"]["header"]["name"] == "StateReport"
    status, via_web = await loop.run_in_executor(None, post, "/api/alexa", directive("Alexa.Discovery", "Discover", owner_tok))
    assert status == 200 and via_web["event"]["header"]["name"] == "Discover.Response"

    assert (await loop.run_in_executor(None, post, "/api/logout", {}, ali))[0] == 200
    assert (await loop.run_in_executor(None, http, "/api/state", None, ali))[0] == 401
    assert (await loop.run_in_executor(None, post, "/api/account/delete", {}, res["token"]))[0] == 200
    assert "C0FFEEAA0001" not in hub.strips and not any(u.get("role") == CUSTOMER for u in hub.store.users)
    status, state = await loop.run_in_executor(None, http, "/api/state")
    assert status == 200 and state["strips"][0]["outlets"][0]["name"] == "Kettle"
    status, res = await loop.run_in_executor(None, http, "/api/switch", {"strip": "A1B2C3D4E5F6", "outlet": 4, "on": False})
    assert status == 200 and res["confirmed"] and not relays[4]
    assert (await loop.run_in_executor(None, http, "/api/switch", {"strip": "A1B2C3D4E5F6", "outlet": 9, "on": True}))[0] == 400
    status, report = await loop.run_in_executor(None, http, "/api/history?range=week")
    assert status == 200 and len(report["buckets"]) == 7
    status, res = await loop.run_in_executor(None, http, "/api/meta", {"strip": "A1B2C3D4E5F6", "outlet": 2, "icon": "router"})
    assert status == 200 and res["strip"]["outlets"][1]["icon"] == "router"
    status, res = await loop.run_in_executor(None, http, "/api/schedules/save",
                                             {"strip": "A1B2C3D4E5F6", "outlet": 0, "on": False, "time": "23:30", "days": [0, 1, 2, 3, 4]})
    assert status == 200 and len(res["schedules"]) == 1
    status, res = await loop.run_in_executor(None, http, "/api/settings", {"price_kwh": 1.75})
    assert status == 200 and res["settings"]["price_kwh"] == 1.75
    status, res = await loop.run_in_executor(None, http, "/api/events?after=0")
    assert status == 200 and res["last_id"] >= 1
    status, state = await loop.run_in_executor(None, http, "/api/state")
    assert state["schedules"][0]["time"] == "23:30" and state["settings"]["price_kwh"] == 1.75
    S = "A1B2C3D4E5F6"
    assert (await loop.run_in_executor(None, http, "/api/lock", {"strip": S, "pin": "1111"}))[0] == 200
    assert (await loop.run_in_executor(None, http, "/api/switch", {"strip": S, "outlets": [1, 2], "on": True}))[0] == 403
    status, res = await loop.run_in_executor(None, http, "/api/switch", {"strip": S, "outlets": [1, 2], "on": True, "pin": "1111"})
    assert status == 200 and relays[1] and relays[2]
    assert (await loop.run_in_executor(None, http, "/api/lock", {"strip": S, "pin": "", "old_pin": "1111"}))[0] == 200
    status, res = await loop.run_in_executor(None, http, "/api/scenes/save",
                                             {"name": "Off", "actions": [{"strip": S, "outlet": 0, "on": False}]})
    assert status == 200
    status, res = await loop.run_in_executor(None, http, "/api/scenes/run", {"id": res["scene"]["id"]})
    assert status == 200 and not any(relays[n] for n in OUTLETS)
    status, res = await loop.run_in_executor(None, http, "/api/timer", {"strip": S, "outlets": [1, 4], "minutes": 30, "on": True})
    assert status == 200 and res["strip"]["outlets"][0]["timer"] and res["strip"]["outlets"][3]["timer"]
    # family sharing: members get their own token, a role and optionally only some strips
    hub.strips["B2B2B2B2B2B2"] = Strip("B2B2B2B2B2B2")          # a second (offline) strip
    hub.store.approve("B2B2B2B2B2B2")
    status, res = await loop.run_in_executor(None, http, "/api/users/add", {"name": "Mona", "role": "view"})
    assert status == 200 and res["user"]["role"] == "view" and len(res["token"]) >= 16
    viewer = res["token"]
    status, state = await loop.run_in_executor(None, http, "/api/state", None, viewer)
    assert status == 200 and state["me"]["role"] == "view" and len(state["strips"]) == 2
    assert "ip" not in state["server"]                       # view-only members never add strips
    status, _ = await loop.run_in_executor(None, http, "/api/switch", {"strip": S, "outlet": 1, "on": True}, viewer)
    assert status == 403
    status, res = await loop.run_in_executor(None, http, "/api/users/add",
                                             {"name": "Omar", "role": "control", "strips": [S.lower()]})
    assert status == 200 and res["user"]["strips"] == [S]
    kid, kid_id = res["token"], res["user"]["id"]
    status, state = await loop.run_in_executor(None, http, "/api/state", None, kid)
    assert [x["id"] for x in state["strips"]] == [S] and state["me"]["name"] == "Omar"
    assert (await loop.run_in_executor(None, http, "/api/switch", {"strip": S, "outlets": [4], "on": True}, kid))[0] == 200
    assert relays[4]
    assert (await loop.run_in_executor(None, http, "/api/switch", {"strip": "B2B2B2B2B2B2", "outlet": 1, "on": True}, kid))[0] == 403
    assert (await loop.run_in_executor(None, http, "/api/settings", {"price_kwh": 9}, kid))[0] == 403
    assert (await loop.run_in_executor(None, http, "/api/lock", {"strip": S, "pin": "1234"}, kid))[0] == 403
    assert (await loop.run_in_executor(None, http, "/api/users", None, kid))[0] == 403
    status, res = await loop.run_in_executor(None, http, "/api/users")
    assert status == 200 and {u["name"] for u in res["users"]} == {"Mona", "Omar"}
    assert next(u for u in res["users"] if u["name"] == "Omar")["last_seen"] > 0
    assert (await loop.run_in_executor(None, http, "/api/users/add", {"name": "X", "role": "boss"}))[0] == 400
    status, res = await loop.run_in_executor(None, http, "/api/users/update", {"id": kid_id, "new_token": True})
    assert status == 200 and res["token"] != kid
    assert (await loop.run_in_executor(None, http, "/api/state", None, kid))[0] == 401   # the old token stops working
    assert (await loop.run_in_executor(None, http, "/api/state", None, res["token"]))[0] == 200
    status, res = await loop.run_in_executor(None, http, "/api/users/delete", {"id": kid_id})
    assert status == 200 and [u["name"] for u in res["users"]] == ["Mona"]
    del hub.strips["B2B2B2B2B2B2"]
    web.shutdown()

    # Alexa: an Echo discovers the outlets, reads their names and switches them
    def free_ports(count: int) -> int:
        """The first of [count] free ports in a row, below the range the OS hands out to outgoing connections."""
        for base in range(21000, 30000, 50):
            socks = []
            try:
                for port in range(base, base + count):
                    sock = socket.socket()
                    socks.append(sock)
                    sock.bind(("0.0.0.0", port))
                return base
            except OSError:
                continue
            finally:
                for sock in socks:
                    sock.close()
        raise RuntimeError("no free ports for the Alexa test")
    alexa = Alexa(hub, "127.0.0.1", ssdp_port=0, base_port=free_ports(20))
    await alexa.start_discovery()
    await alexa.reconcile()
    assert len(alexa.servers) == 5                  # the strip itself + 4 outlets
    echo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    echo.settimeout(2)
    echo.sendto(b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
                b'MX: 2\r\nST: urn:Belkin:device:**\r\n\r\n', ("127.0.0.1", alexa.transport.get_extra_info("sockname")[1]))

    def collect() -> List[str]:
        found = []
        try:
            while len(found) < 5:
                found.append(echo.recv(2048).decode())
        except socket.timeout:
            pass
        return found
    replies = await loop.run_in_executor(None, collect)
    echo.close()
    assert len(replies) == 5 and all("urn:Belkin:device:**" in r for r in replies)
    locations = [re.search(r"LOCATION: (\S+)", r).group(1) for r in replies]

    def fetch(url: str, body: Optional[str] = None, action: str = "") -> str:
        req = urllib.request.Request(url, data=body.encode() if body else None,
                                     headers={"SOAPACTION": '"urn:Belkin:service:basicevent:1#%s"' % action} if action else {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.read().decode()
    setups = {u: await loop.run_in_executor(None, fetch, u) for u in locations}
    kettle = next(u for u, x in setups.items() if "<friendlyName>Kettle</friendlyName>" in x)
    control = kettle.replace("/setup.xml", "/upnp/control/basicevent1")
    relays[1] = True
    body = ('<?xml version="1.0"?><s:Envelope><s:Body><u:SetBinaryState xmlns:u="urn:Belkin:service:basicevent:1">'
            '<BinaryState>0</BinaryState></u:SetBinaryState></s:Body></s:Envelope>')
    assert "<BinaryState>0</BinaryState>" in await loop.run_in_executor(None, fetch, control, body, "SetBinaryState")
    for _ in range(30):
        await asyncio.sleep(0.1)
        if not relays[1]:
            break
    assert not relays[1]
    got = await loop.run_in_executor(None, fetch, control, "<x/>", "GetBinaryState")
    assert "<BinaryState>0</BinaryState>" in got
    hub.store.set_pin("A1B2C3D4E5F6", "9999")       # a locked strip is hidden from Alexa
    await alexa.reconcile()
    assert not alexa.servers
    hub.store.set_pin("A1B2C3D4E5F6", "")
    alexa.transport.close()

    strip_task.cancel()
    writer.close()
    await asyncio.sleep(0.1)
    assert not strip.online
    hub.check_alerts(time.time() + OFFLINE_ALERT_AFTER + 1)
    assert (await hub.events(0))["events"][0]["kind"] == "offline"

    # a command for an offline strip waits and runs when it reconnects
    relays[2] = False
    code, body = await hub.switch("A1B2C3D4E5F6", 2, True)
    assert code == 202 and body["queued"] and body["strip"]["pending"] == {"2": True}
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    strip_task = asyncio.ensure_future(fake_strip(reader, writer))
    writer.write(b"up:bootinfo:LGU+-TAP-HW002;a1b2c3d4e5f6;a1b2c3d4e5f7;0.1.54-1.0.66;connect\r\n")
    await writer.drain()
    for _ in range(30):
        await asyncio.sleep(0.1)
        if relays[2]:
            break
    assert relays[2] and not hub.store.pending

    # a power cut: the strip comes back with every outlet off. The app hears the power is back, outlets set to
    # come back on do (now or after their wait), and "turn back on what was on" handles the rest
    assert (await hub.set_meta(S, 3, {"after_power": 0}))[0] == 200
    assert (await hub.set_meta(S, 1, {"after_power": 5}))[0] == 200
    assert (await hub.set_meta(S, 4, {"after_power": 999}))[0] == 400
    assert (await hub.set_meta(S, 0, {"after_power": 5}))[0] == 400
    assert (await hub.switch(S, 0, True, outlets=[1, 3, 4]))[0] == 200 and all(relays[n] for n in OUTLETS)

    async def reconnect(power_cut: bool) -> Any:
        strip_task.cancel()
        writer.close()
        for _ in range(30):
            await asyncio.sleep(0.1)
            if not strip.online:
                break
        if power_cut:
            for n in OUTLETS:
                relays[n] = False
        r, w = await asyncio.open_connection("127.0.0.1", port)
        task = asyncio.ensure_future(fake_strip(r, w))
        w.write(b"up:bootinfo:LGU+-TAP-HW002;a1b2c3d4e5f6;a1b2c3d4e5f7;0.1.54-1.0.66;connect\r\n")
        await w.drain()
        for _ in range(40):
            await asyncio.sleep(0.1)
            if hub.store.outages.get(S, {}).get("back"):
                break
        return task, w
    assert hub.store.outages.get(S, {}).get("back")                 # the earlier reconnect was a network dropout
    events_before = (await hub.events(0))["last_id"]
    strip_task, writer = await reconnect(power_cut=False)          # the network drops, the power stays on
    assert (await hub.events(events_before))["events"] == [] and not hub.store.restores
    strip_task, writer = await reconnect(power_cut=True)
    events = (await hub.events(events_before))["events"]
    assert [e["kind"] for e in events] == ["power_back"] and not any(relays[n] for n in OUTLETS)
    assert set(hub.store.restores) == {S + "/1", S + "/3"}           # 2 and 4 wait for the user's OK
    shown = next(x for x in (await hub.snapshot())["strips"] if x["id"] == S)
    assert shown["power_back"]["on"] == [1, 2, 3, 4] and shown["outlets"][0]["after_power"] == 5
    assert shown["outlets"][0]["restore_at"] and shown["outlets"][3]["after_power"] is None
    await hub.run_restores()
    assert relays[3] and not relays[1]
    await hub.run_restores(time.time() + 301)
    assert relays[1] and not relays[2] and not relays[4] and not hub.store.restores
    assert [e["kind"] for e in (await hub.events(events_before))["events"]][:2] == ["power_restore", "power_restore"]
    status, res = await hub.restore_after_power(S)
    assert status == 200 and relays[2] and relays[4] and res["strip"]["power_back"] is None
    assert (await hub.restore_after_power(S))[0] == 409
    strip_task.cancel()
    writer.close()
    srv.close()
    print("selftest OK: protocol, switching, button events, names, timers, energy history, rooms/icons, "
          "schedules, cycles, scenes, PIN locks, offline queue, power cuts, alerts, settings, Alexa, family sharing, protection, strip approval, customer accounts, admin, Alexa skill, web API + token")


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command")

    s = sub.add_parser("serve", help="run the strip server and web UI (default)")
    s.add_argument("--web-port", type=int, default=int(os.environ.get("SP_WEB_PORT", "8080")),
                   help="port for the web page and the app API (default 8080, env SP_WEB_PORT)")
    s.add_argument("--token", default=os.environ.get("SP_TOKEN", ""),
                   help="password for the web page and API (env SP_TOKEN). Always set one on a public server.")
    s.add_argument("--public-ip", default=os.environ.get("SP_PUBLIC_IP", ""),
                   help="address shown in hints, e.g. your VPS public IP (default: auto-detect)")
    s.add_argument("--bind", default="0.0.0.0", help="interface to listen on (default all)")
    s.add_argument("--no-alexa", action="store_true",
                   help="do not offer the outlets to Amazon Echo devices on the home network")
    s.add_argument("--no-app-download", action="store_true",
                   help="do not fetch the Android app from GitHub to serve it at /app.apk")
    s.add_argument("--data", default=os.environ.get("SP_DATA", str(HERE / "smartpower-data.json")),
                   help="file for outlet names and timers (env SP_DATA)")

    p = sub.add_parser("provision", help="give a strip in setup mode your Wi-Fi and this server's IP")
    p.add_argument("--server-ip", required=True, help="IPv4 of the machine running `serve` (LAN IP or VPS public IP)")
    p.add_argument("--ssid", required=True, help="your 2.4 GHz Wi-Fi name")
    p.add_argument("--wifi-password", required=True, help="your Wi-Fi password")
    p.add_argument("--setup-host", default=SETUP_ADDR[0], help=argparse.SUPPRESS)
    p.add_argument("--wait", type=float, default=20.0, help="seconds to wait for the strip (default 20)")

    sub.add_parser("selftest", help="check the server logic against a simulated strip")

    argv = sys.argv[1:] or ["serve"]
    args = ap.parse_args(argv)
    if args.command == "provision":
        return provision(args)
    if args.command == "selftest":
        asyncio.run(selftest())
        return 0
    try:
        return asyncio.run(serve(args))
    except KeyboardInterrupt:
        return 0
    except OSError as err:
        print("cannot start: %s (is another copy already running on these ports?)" % err)
        return 1


if __name__ == "__main__":
    sys.exit(main())
