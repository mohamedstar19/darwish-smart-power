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
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

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
ALERT_REPEAT = {"temp": 3600, "power": 1800}
MAX_EVENTS = 500
QUEUE_TTL = 24 * 3600                    # commands for an offline strip are kept this long
PIN_MAX_TRIES = 5                        # wrong PINs before a strip refuses PINs for a minute
# Protection for the strip port, which is open to the internet: anything can connect and claim to
# be a strip. Limits are per internet address and generous, because many homes share one address
# (carrier NAT). Home-network and loopback addresses are never limited or blocked.
HELLO_TIMEOUT = 15                       # seconds a new connection gets to introduce itself as a strip
MAX_LINE = 4096                          # longest line accepted from a strip
MAX_CONNECTIONS_PER_IP = 16              # open strip connections at the same time
MAX_CONNECTS_PER_MINUTE = 30             # new strip connections per minute
MAX_NEW_STRIPS_PER_DAY = 6               # strips never seen before, per address per day
MAX_UNKNOWN_STRIPS = 64                  # never-named strips kept in memory before new ones are refused
MAX_STRIKES = 3                          # misbehaving connections (10 minutes) before a block
BLOCK_SECONDS = 3600
LOGIN_FAILS = 10                         # different wrong passwords from one address (5 minutes) before a block
LOGIN_BLOCK_SECONDS = 900
ICONS = ("plug", "kettle", "router", "tv", "ac", "lamp", "heater", "fan", "fridge",
         "washer", "charger", "computer", "speaker", "camera", "microwave", "iron")
DEFAULT_SETTINGS = {"price_kwh": 1.5, "currency": "EGP", "max_temp_c": 60, "max_watts": 3000, "alexa": True}
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
    "/privacy": ("privacy.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
}
ICON_TYPES = {".png": "image/png", ".svg": "image/svg+xml"}


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


def parse_bootinfo(line: str) -> Optional[Dict[str, str]]:
    """up:bootinfo:<model>;<mac>;<mac>;<firmware>;connect"""
    prefix = "up:bootinfo:"
    if not line.startswith(prefix):
        return None
    parts = line[len(prefix):].split(";")
    if len(parts) != 5 or parts[4] != "connect" or not is_mac(parts[1]):
        return None
    return {"model": parts[0], "mac": parts[1].upper(), "fw": parts[3]}


def parse_getinfo(payload: str) -> Dict[int, Dict[str, Any]]:
    """'1:<fields>:2:<fields>:...' -> {outlet: reading}.

    Each <fields> block is 12 values separated by ';':
    runtime, relay on/off, state, overload, overheat, power (mW), energy (hex Wh),
    previous energy, config, status, event, temperature (C).
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
        #                  "token_hash", "created"}; the server token itself is the owner
        self.users: List[Dict[str, Any]] = []
        self.settings: Dict[str, Any] = dict(DEFAULT_SETTINGS)
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
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as err:
            log("[store] could not read %s (%s), starting empty" % (path, err))

    def save(self) -> None:
        data = json.dumps({"names": self.names, "timers": self.timers, "meta": self.meta,
                           "schedules": self.schedules, "scenes": self.scenes, "pending": self.pending,
                           "alexa_ports": self.alexa_ports, "users": self.users,
                           "settings": self.settings},
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

    def knows(self, mac: str) -> bool:
        """True once the owner has named the strip or one of its outlets, or set its room/icons."""
        return bool(self.names.get(mac)) or bool(self.meta.get(mac))

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

    def set_meta(self, mac: str, outlet: int, room: Optional[str] = None,
                 icon: Optional[str] = None, favorite: Optional[bool] = None) -> None:
        entry = self.meta.setdefault(mac, {})
        if room is not None:
            if room:
                entry["room"] = room
            else:
                entry.pop("room", None)
        if outlet and (icon is not None or favorite is not None):
            o = entry.setdefault("outlets", {}).setdefault(str(outlet), {})
            if icon is not None:
                o["icon"] = icon
            if favorite is not None:
                o["fav"] = favorite
        self.save()

    # schedules, kind "time": {"id", "strip", "outlets", "on", "time": "HH:MM", "days": [0..6 = Mon..Sun], "enabled"}
    #            kind "cycle": {"id", "strip", "outlets", "on_minutes", "off_minutes", "started_at", "enabled"}
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
        return None

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


OWNER = {"role": "owner", "name": None, "id": None, "strips": None}
ROLES = ("control", "view")
OWNER_ONLY = ("/api/users", "/api/settings", "/api/lock")


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


class Strip:
    def __init__(self, mac: str):
        self.mac = mac
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
            if self.before_hello > 5:
                self.hub.guard.strike(self.address, "talks but never says it is a strip")
                self.close()
            return
        strip.last_seen = time.time()
        kind, _, rest = line.partition(":")[2].partition(":")
        if kind == "getinfo":
            readings = parse_getinfo(rest)
            if len(readings) == len(OUTLETS):
                for n, r in readings.items():
                    o = strip.outlets[n]
                    o.on, o.watts, o.kwh, o.temp_c = r["on"], r["watts"], r["kwh"], r["temp_c"]
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

    async def run(self) -> None:
        try:
            while not self.closed:
                if self.strip is None:              # a real strip says hello right away
                    raw = await asyncio.wait_for(self.reader.readline(), HELLO_TIMEOUT)
                else:
                    raw = await self.reader.readline()
                if not raw:
                    break
                line = clean_line(raw)
                if line:
                    self.handle(line)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except asyncio.TimeoutError:
            self.hub.guard.strike(self.address, "connected but never said it is a strip")
        except (ValueError, asyncio.LimitOverrunError) as err:      # absurdly long line
            log("[strip] dropping %s: %s" % (self.address, err))
            self.hub.guard.strike(self.address, "line too long")
        finally:
            self.closed = True
            self.hub.detach(self)
            self.writer.close()


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
        self.new_strips: Dict[str, List[float]] = {}              # ip -> times a new strip was added
        self.login_fails: Dict[str, Dict[str, float]] = {}        # ip -> {wrong password hash: when}
        self.refused = 0
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
        """Can [ip] bring in a strip this server has never seen?"""
        if is_local_address(ip):
            return True
        now = time.time() if now is None else now
        if unknown_now >= MAX_UNKNOWN_STRIPS:
            self.strike(ip, "new strip refused: %d unnamed strips already" % unknown_now, now)
            return False
        added = self._recent(self.new_strips.setdefault(ip, []), 24 * 3600, now)
        if len(added) >= MAX_NEW_STRIPS_PER_DAY:
            self.block(ip, "more than %d new strips in a day" % MAX_NEW_STRIPS_PER_DAY, now=now)
            return False
        added.append(now)
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
        self.pin_failures: Dict[str, Tuple[int, float]] = {}
        self.user_seen: Dict[str, float] = {}
        self.guard = Guard()

    @staticmethod
    def label(strip: Strip) -> str:
        return "strip %s" % strip.mac[-6:]

    def attach(self, link: StripLink, boot: Dict[str, str]) -> Optional[Strip]:
        """The strip behind [link], or None when the connection is refused."""
        strip = self.strips.get(boot["mac"])
        if strip is not None and strip.online and strip.link is not link and strip.link.address != link.address:
            # an online strip does not move to another address; someone is impersonating it
            self.guard.strike(link.address, "claimed to be %s, which is online from another address" % self.label(strip))
            return None
        if strip is None:
            unknown = sum(1 for s in self.strips.values() if not self.store.knows(s.mac))
            if not self.guard.may_add_strip(link.address, unknown):
                return None
            strip = self.strips[boot["mac"]] = Strip(boot["mac"])
        if strip.link is not None and strip.link is not link:
            strip.link.close()                      # the strip re-dialled; the old socket is dead
        strip.model, strip.fw, strip.address = boot["model"], boot["fw"], link.address
        strip.link, strip.last_seen = link, time.time()
        log("[strip] %s online (model %s, firmware %s, from %s)" % (self.label(strip), strip.model, strip.fw, link.address))
        return strip

    def detach(self, link: StripLink) -> None:
        strip = link.strip
        if strip is not None and strip.link is link:
            strip.link = None
            log("[strip] %s offline" % self.label(strip))

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
        await link.read_state()
        strip = link.strip
        if strip is None:
            return
        queued = self.store.take_pending(strip.mac, time.time())
        for on in (True, False):
            outlets = [n for n, want in queued.items() if want == on]
            if outlets and not link.closed:
                targets = list(OUTLETS) if 0 in outlets else outlets
                ok = await link.switch(targets, on)
                log("[queue] %s outlets %s -> %s (%s)" % (self.label(strip), targets, "on" if on else "off",
                                                          "done" if ok else "not confirmed"))

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

    def cost(self, kwh: float) -> float:
        return round(kwh * float(self.store.settings.get("price_kwh", 0)), 2)

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
        strips = sorted(self.strips.values(), key=lambda s: ((self.store.room(s.mac) or "~").lower(), s.mac))
        return {
            "strips": [s.to_json(self.store, today) for s in strips],
            "schedules": self.schedules_json(now),
            "scenes": self.store.scenes,
            "settings": self.store.settings,
            "today": {"kwh": round(today_kwh, 3), "cost": self.cost(today_kwh),
                      "hours": [round(h, 3) for h in hours]},
            "month": {"kwh": round(month_kwh, 3), "cost": self.cost(month_kwh)},
            "last_event": self.history.last_event_id(),
        }

    async def history_report(self, rng: str, strip_id: Optional[str] = None, now: Optional[float] = None,
                             allowed: Optional[set] = None) -> Tuple[int, Dict[str, Any]]:
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
        return 200, {
            "range": rng,
            "buckets": [{"t": t, "kwh": round(v, 3)} for t, v in zip(labels, buckets)],
            "total_kwh": round(total, 3),
            "cost": self.cost(total),
            "currency": self.store.settings.get("currency", "EGP"),
            "price_kwh": self.store.settings.get("price_kwh", 0),
            "by_outlet": [{"strip": m, "outlet": o, "kwh": round(v, 3), "cost": self.cost(v)}
                          for (m, o), v in sorted(by_outlet.items(), key=lambda kv: -kv[1])],
        }

    async def events(self, after: int) -> Dict[str, Any]:
        return {"events": self.history.events_after(after), "last_id": self.history.last_event_id()}

    # ---- family members

    def user_json(self, user: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": user["id"], "name": user["name"], "role": user["role"], "strips": user.get("strips", []),
                "created": user.get("created", 0), "last_seen": int(self.user_seen.get(user["id"], 0))}

    async def users(self) -> Dict[str, Any]:
        return {"users": [self.user_json(u) for u in self.store.users]}

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
            unknown = [x for x in strips if x not in self.strips]
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

    def filter_for(self, who: Dict[str, Any], snap: Dict[str, Any]) -> Dict[str, Any]:
        """What a member limited to some strips may see of /api/state."""
        snap["me"] = {"role": who["role"], "name": who["name"], "strips": sorted(who["strips"]) if who["strips"] else []}
        if who["strips"] is None:
            return snap
        snap["strips"] = [x for x in snap["strips"] if may_see(who, x["id"])]
        snap["schedules"] = [x for x in snap["schedules"] if may_see(who, x["strip"])]
        snap["scenes"] = [x for x in snap["scenes"] if all(may_see(who, a["strip"]) for a in x["actions"])]
        # today's totals for just their strips
        kwh = sum(x["today_kwh"] for x in snap["strips"])
        snap["today"] = {"kwh": round(kwh, 3), "cost": self.cost(kwh), "hours": snap["today"]["hours"]}
        snap["month"] = {"kwh": 0.0, "cost": 0.0}
        return snap

    def scene_strips(self, scene_id: str) -> List[str]:
        scene = next((x for x in self.store.scenes if x.get("id") == scene_id), None)
        return [a["strip"] for a in scene["actions"]] if scene else []

    def schedule_strip(self, sched_id: str) -> Optional[str]:
        sched = next((x for x in self.store.schedules if x.get("id") == sched_id), None)
        return sched["strip"] if sched else None

    def find(self, strip_id: Any) -> Optional[Strip]:
        return self.strips.get(str(strip_id).upper())

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
        self.store.set_meta(strip.mac, outlet, room=room, icon=icon,
                            favorite=None if favorite is None else bool(favorite))
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
        else:
            return 400, {"error": "kind must be time or cycle"}
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
        limits = {"price_kwh": (0, 1000), "max_temp_c": (20, 150), "max_watts": (50, 100000)}
        for key, (low, high) in limits.items():
            if key in req:
                value = float(req[key])
                if not low <= value <= high:
                    return 400, {"error": "%s must be between %s and %s" % (key, low, high)}
                new[key] = value
        if "currency" in req:
            new["currency"] = str(req["currency"]).strip()[:8] or "EGP"
        if "alexa" in req:
            new["alexa"] = bool(req["alexa"])
        self.store.settings = new
        self.store.save()
        return 200, {"ok": True, "settings": new}

    # ---- background work

    def check_alerts(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        settings = self.store.settings
        for strip in self.strips.values():
            mac = strip.mac
            if not strip.online:
                if strip.last_seen and now - strip.last_seen >= OFFLINE_ALERT_AFTER \
                        and not self.alerted_offline.get(mac):
                    self.alerted_offline[mac] = True
                    self.history.add_event("offline", mac, now=now)
                continue
            if self.alerted_offline.pop(mac, False):
                self.history.add_event("online", mac, now=now)
            for o in strip.outlets.values():
                if o.temp_c is not None and o.temp_c >= float(settings.get("max_temp_c", 60)):
                    self.alert_once("temp", mac, o.index, o.temp_c, now)
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
            except sqlite3.Error as err:
                log("[alerts] %s" % err)
            await asyncio.sleep(POLL_EVERY)

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

    def run_on_loop(self, coro, timeout: float = 20.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def handle_error(self, request, client_address) -> None:
        # a phone or the tunnel hanging up in the middle of a reply (e.g. a cancelled app download)
        # is normal; only real errors deserve a traceback in the log
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class WebHandler(BaseHTTPRequestHandler):
    server: WebServer
    server_version = "DarwishSmartPower/" + VERSION

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
            if user:
                self.server.hub.user_seen[user["id"]] = time.time()
                return {"role": user["role"], "name": user["name"], "id": user["id"],
                        "strips": set(user.get("strips") or []) or None}
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
        extra = dict(extra or {})
        self.send_header("Cache-Control", extra.pop("Cache-Control", "no-store"))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra.items():
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
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
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
        self.reply(200, target.read_bytes(), ctype, extra)
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
            if apk:
                self.send_file(apk, "application/vnd.android.package-archive", "darwish-smart-power.apk")
            else:                                   # not downloaded yet: send the phone to the GitHub copy
                self.reply(302, b"", "text/plain", {"Location": APK_RELEASE_URL})
            return
        if self.send_static(path):                  # public: website, control panel (asks for the password), PWA files
            return
        who = self.signed_in()
        if who is None:
            return
        hub = self.server.hub
        if path == "/api/state":
            snap = hub.filter_for(who, self.server.run_on_loop(hub.snapshot()))
            snap["server"] = {"strip_port": STRIP_PORT, "version": VERSION}
            if who["role"] != "view":               # the address new strips dial; only people who add strips need it
                snap["server"]["ip"] = self.server.public_ip
            self.reply_json(200, snap)
        elif path == "/api/me":
            self.reply_json(200, {"role": who["role"], "name": who["name"]})
        elif path == "/api/users":
            if who["role"] != "owner":
                self.reply_json(403, {"error": "only the owner manages family members"})
            else:
                self.reply_json(200, self.server.run_on_loop(hub.users()))
        elif path == "/api/health":
            self.reply_json(200, {"ok": True, "app": "darwish-smart-power", "version": VERSION})
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
                query.get("range", ["day"])[0], strip, allowed=who["strips"]))
            self.reply_json(code, body)
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

    def do_POST(self):
        path = urlsplit(self.path).path
        who = self.signed_in()
        if who is None:
            return
        if who["role"] == "view":
            self.reply_json(403, {"error": "view only", "view_only": True})
            return
        if who["role"] != "owner" and path.startswith(OWNER_ONLY):
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
            if path == "/api/users/add":
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
        strips = [x for x in self.hub.strips.values() if not store.locked(x.mac)]
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
    print("  strip port       TCP %d" % STRIP_PORT)
    print("  security         %s" % ("token required" if args.token else "NO TOKEN - only use on a home network you trust"))
    print("  saved settings   %s" % store.path)
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

    # alerts: hot outlet (thresholds come from the settings)
    assert (await hub.update_settings({"max_temp_c": 5}))[0] == 400
    await hub.update_settings({"max_temp_c": 34, "price_kwh": 2})
    hub.check_alerts(now)
    hub.check_alerts(now + 60)                      # repeated alerts are rate-limited
    events = (await hub.events(0))["events"]
    assert [(e["kind"], e["outlet"]) for e in events] == [("temp", 4)]
    assert hub.cost(1.5) == 3.0

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
    assert all(g.may_add_strip("196.135.103.190", 0, t0) for _ in range(MAX_NEW_STRIPS_PER_DAY))
    assert not g.may_add_strip("196.135.103.190", 0, t0) and g.is_blocked("196.135.103.190", t0)
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
    assert await asyncio.wait_for(silent_r.read(), 5) == b""                  # dropped for saying nothing
    silent_w.close()
    HELLO_TIMEOUT = saved_timeout

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
    alexa = Alexa(hub, "127.0.0.1", ssdp_port=0, base_port=53100)
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
    strip_task.cancel()
    writer.close()
    srv.close()
    print("selftest OK: protocol, switching, button events, names, timers, energy history, rooms/icons, "
          "schedules, cycles, scenes, PIN locks, offline queue, alerts, settings, Alexa, family sharing, protection, web API + token")


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
