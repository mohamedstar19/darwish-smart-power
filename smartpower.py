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
import hmac
import ipaddress
import json
import os
import socket
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

VERSION = "1.0.0"
STRIP_PORT = 10086                       # fixed in the strip firmware
SETUP_ADDR = ("192.168.1.1", 30300)      # the strip's own access point while in setup mode
OUTLETS = (1, 2, 3, 4)
POLL_EVERY = 5.0                         # seconds between state reads
DIAG_EVERY = 30.0                        # seconds between voltage / Wi-Fi signal reads
MAX_MISSED_POLLS = 3                     # unanswered reads before the connection is dropped
TIMER_GRACE = 600                        # drop a timer that could not run this long after it was due
MAX_BODY = 16 * 1024
HERE = Path(__file__).resolve().parent
# the Android app: a downloaded copy next to this file, or a local Gradle build
APK_CANDIDATES = (HERE / "darwish-smart-power.apk", HERE / "android/app/build/outputs/apk/debug/app-debug.apk")

APK_RELEASE_URL = "https://github.com/mohamedstar19/darwish-smart-power/releases/latest/download/darwish-smart-power.apk"
WEBSITE_PATH = HERE / "website" / "index.html"


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
    """Outlet names and timers, kept in a small JSON file next to the script."""

    def __init__(self, path: Path):
        self.path = path
        self.names: Dict[str, Dict[str, str]] = {}
        self.timers: Dict[str, Dict[str, Any]] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.names = dict(data.get("names", {}))
            self.timers = dict(data.get("timers", {}))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as err:
            log("[store] could not read %s (%s), starting empty" % (path, err))

    def save(self) -> None:
        data = json.dumps({"names": self.names, "timers": self.timers}, ensure_ascii=False, indent=1)
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

    def to_json(self, store: Store) -> Dict[str, Any]:
        watts = round(sum(o.watts for o in self.outlets.values()), 2)
        all_timer = store.timer(self.mac, 0)
        return {
            "id": self.mac,
            "name": store.name(self.mac, 0),
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
            self.strip = self.hub.attach(self, boot)
            asyncio.ensure_future(self.read_state())
            return
        strip = self.strip
        if strip is None:
            return
        strip.last_seen = time.time()
        kind, _, rest = line.partition(":")[2].partition(":")
        if kind == "getinfo":
            readings = parse_getinfo(rest)
            if len(readings) == len(OUTLETS):
                for n, r in readings.items():
                    o = strip.outlets[n]
                    o.on, o.watts, o.kwh, o.temp_c = r["on"], r["watts"], r["kwh"], r["temp_c"]
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
        log("[strip] connection from %s" % self.address)
        try:
            while True:
                raw = await self.reader.readline()
                if not raw:
                    break
                line = clean_line(raw)
                if line:
                    self.handle(line)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except (ValueError, asyncio.LimitOverrunError) as err:      # absurdly long line
            log("[strip] dropping %s: %s" % (self.address, err))
        finally:
            self.closed = True
            self.hub.detach(self)
            self.writer.close()


class Hub:
    def __init__(self, store: Store):
        self.store = store
        self.strips: Dict[str, Strip] = {}

    @staticmethod
    def label(strip: Strip) -> str:
        return "strip %s" % strip.mac[-6:]

    def attach(self, link: StripLink, boot: Dict[str, str]) -> Strip:
        strip = self.strips.get(boot["mac"])
        if strip is None:
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
        await StripLink(self, reader, writer).run()

    # ---- used by the web side (always on the event loop)

    async def snapshot(self) -> Dict[str, Any]:
        return {"strips": [s.to_json(self.store) for s in self.strips.values()]}

    def find(self, strip_id: Any) -> Optional[Strip]:
        return self.strips.get(str(strip_id).upper())

    async def switch(self, strip_id: Any, outlet: int, on: bool) -> Tuple[int, Dict[str, Any]]:
        strip = self.find(strip_id)
        if strip is None:
            return 404, {"error": "unknown strip"}
        if not strip.online or strip.link is None:
            return 503, {"error": "strip offline"}
        targets = list(OUTLETS) if outlet == 0 else [outlet]
        confirmed = await strip.link.switch(targets, on)
        return 200, {"ok": True, "confirmed": confirmed, "strip": strip.to_json(self.store)}

    async def rename(self, strip_id: Any, outlet: int, name: str) -> Tuple[int, Dict[str, Any]]:
        strip = self.find(strip_id)
        if strip is None:
            return 404, {"error": "unknown strip"}
        self.store.rename(strip.mac, outlet, name)
        return 200, {"ok": True, "strip": strip.to_json(self.store)}

    async def set_timer(self, strip_id: Any, outlet: int, minutes: float, on: bool) -> Tuple[int, Dict[str, Any]]:
        strip = self.find(strip_id)
        if strip is None:
            return 404, {"error": "unknown strip"}
        if minutes <= 0:
            self.store.clear_timer(strip.mac, outlet)
        else:
            self.store.set_timer(strip.mac, outlet, on, int(time.time() + minutes * 60))
        return 200, {"ok": True, "strip": strip.to_json(self.store)}

    # ---- background work

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
            code, body = await self.switch(mac, int(outlet), bool(timer.get("on")))
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

PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Darwish Smart Power</title>
<style>
:root{--bg:#f4f1ec;--panel:#fff;--ink:#1d1b19;--soft:#6f6a63;--line:#e4ded5;--accent:#d9480f;--on:#2b8a3e;--onbg:#e6f4ea;--warn:#c92a2a}
@media (prefers-color-scheme:dark){:root{--bg:#141210;--panel:#1f1c19;--ink:#f1ece6;--soft:#a39b91;--line:#332e29;--accent:#ff8c42;--on:#69db7c;--onbg:#1d3324;--warn:#ff8787}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif}
header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px;max-width:760px;margin:auto}
header h1{margin:0;font-size:19px}header h1 span{color:var(--accent)}
.lang{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;padding:6px 14px;font:inherit;cursor:pointer}
main{max-width:760px;margin:auto;padding:0 16px 32px}
.strip{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:16px;margin-bottom:16px}
.strip.offline{opacity:.6}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.title{font-weight:650;font-size:17px;cursor:pointer}
.badge{display:inline-block;font-size:12px;border-radius:999px;padding:2px 10px;margin-inline-start:6px;background:var(--line);color:var(--soft)}
.badge.on{background:var(--onbg);color:var(--on)}
.big{font-size:34px;font-weight:700;letter-spacing:-.5px}.big small{font-size:15px;color:var(--soft);font-weight:500}
.stats{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 14px}
.stats span{background:var(--bg);border-radius:8px;padding:3px 9px;font-size:13px;color:var(--soft)}
.row{display:flex;gap:8px;flex-wrap:wrap}
.btn{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:10px;padding:8px 14px;font:inherit;cursor:pointer}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:14px}
.outlet{border:1px solid var(--line);border-radius:14px;padding:12px;background:var(--bg)}
.outlet.on{background:var(--onbg);border-color:var(--on)}
.oname{font-weight:600;cursor:pointer;overflow-wrap:anywhere}
.ometa{font-size:12px;color:var(--soft);margin:4px 0 10px}
.toggle{width:100%;border:0;border-radius:10px;padding:10px;font:inherit;font-weight:600;cursor:pointer;background:var(--line);color:var(--ink)}
.outlet.on .toggle{background:var(--on);color:#fff}
.timer{font-size:12px;color:var(--accent);margin-top:6px;min-height:1em}
.link{background:none;border:0;color:var(--soft);font:inherit;font-size:12px;cursor:pointer;padding:6px 0 0;text-decoration:underline}
.empty{background:var(--panel);border:1px dashed var(--line);border-radius:18px;padding:20px;color:var(--soft)}
.empty code{background:var(--bg);padding:2px 6px;border-radius:6px;color:var(--ink);overflow-wrap:anywhere}
.err{color:var(--warn);min-height:1.2em;margin:0 0 10px}
</style></head>
<body>
<header><h1><span>&#9889;</span> Darwish Smart Power</h1><button class="lang" id="lang"></button></header>
<main><p class="err" id="err"></p><div id="app"></div></main>
<script>
const L={
 en:{lang:"العربية",strip:"Power strip",outlet:"Outlet",on:"ON",off:"OFF",online:"online",offline:"offline",
  allOn:"All on",allOff:"All off",turnOn:"Turn on",turnOff:"Turn off",timer:"Timer",rename:"Rename",
  renameAsk:"New name (empty = default):",timerAsk:"Minutes until it switches (0 = cancel):",
  willOff:"Turns off at",willOn:"Turns on at",lastSeen:"last seen",
  none:"No strip connected yet. Put the strip in setup mode and run:",fail:"Command failed: "},
 ar:{lang:"English",strip:"مشترك الكهرباء",outlet:"مخرج",on:"شغال",off:"مطفي",online:"متصل",offline:"غير متصل",
  allOn:"تشغيل الكل",allOff:"إطفاء الكل",turnOn:"تشغيل",turnOff:"إطفاء",timer:"مؤقت",rename:"تغيير الاسم",
  renameAsk:"الاسم الجديد (فارغ = الافتراضي):",timerAsk:"بعد كم دقيقة يتغير؟ (0 = إلغاء):",
  willOff:"يطفي الساعة",willOn:"يشتغل الساعة",lastSeen:"آخر ظهور",
  none:"لا يوجد مشترك متصل بعد. حط المشترك في وضع الإعداد وشغّل:",fail:"فشل الأمر: "}};
let lang=localStorage.getItem("sp-lang")||((navigator.language||"").startsWith("ar")?"ar":"en");
const token=new URLSearchParams(location.search).get("token")||"";
let state={strips:[]},busy=false;
const t=k=>L[lang][k];
const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const clock=ts=>new Date(ts*1000).toLocaleTimeString(lang==="ar"?"ar-EG":"en-GB",{hour:"2-digit",minute:"2-digit"});
async function call(path,body){
  const h={};if(token)h["X-Token"]=token;
  const opt=body?{method:"POST",headers:{...h,"Content-Type":"application/json"},body:JSON.stringify(body)}:{headers:h};
  const r=await fetch(path,opt);const j=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(j.error||r.status);return j;
}
function timerText(tm){return tm?(tm.on?t("willOn"):t("willOff"))+" "+clock(tm.at):""}
function render(){
  document.documentElement.lang=lang;document.documentElement.dir=lang==="ar"?"rtl":"ltr";
  document.getElementById("lang").textContent=t("lang");
  const app=document.getElementById("app");
  if(!state.strips.length){
    const ip=state.server&&state.server.ip||"SERVER-IP";
    app.innerHTML=`<div class="empty">${t("none")}<br><br><code>python3 smartpower.py provision --server-ip ${esc(ip)} --ssid WIFI --wifi-password PASSWORD</code></div>`;return;
  }
  app.innerHTML=state.strips.map(s=>{
    const anyOn=s.outlets.some(o=>o.on);
    const stats=[`${s.kwh} kWh`,s.volts?`${s.volts} V`:"",s.amps!=null?`${s.amps} A`:"",s.rssi!=null?`Wi-Fi ${s.rssi} dBm`:"",
      s.online?"":`${t("lastSeen")} ${s.last_seen?clock(s.last_seen):"-"}`].filter(Boolean);
    return `<section class="strip${s.online?"":" offline"}">
     <div class="top"><div>
       <div class="title" data-act="rename" data-id="${s.id}" data-n="0">${esc(s.name||t("strip")+" "+s.id.slice(-6))}
         <span class="badge${s.online?" on":""}">${s.online?t("online"):t("offline")}</span></div>
       <div class="big"><bdi dir="ltr">${s.watts} <small>W</small></bdi></div></div>
       <div class="row"><button class="btn primary" data-act="sw" data-id="${s.id}" data-n="0" data-on="1">${t("allOn")}</button>
       <button class="btn" data-act="sw" data-id="${s.id}" data-n="0" data-on="0">${t("allOff")}</button>
       <button class="btn" data-act="timer" data-id="${s.id}" data-n="0" data-on="${anyOn?0:1}">${t("timer")}</button></div></div>
     <div class="stats">${stats.map(x=>`<span><bdi>${esc(x)}</bdi></span>`).join("")}</div>
     <div class="timer">${timerText(s.timer)}</div>
     <div class="grid">${s.outlets.map(o=>`<div class="outlet${o.on?" on":""}">
       <div class="oname" data-act="rename" data-id="${s.id}" data-n="${o.index}">${esc(o.name||t("outlet")+" "+o.index)}</div>
       <div class="ometa">${o.on?t("on"):t("off")} · <bdi>${o.watts} W</bdi>${o.temp_c!=null?` · <bdi>${o.temp_c}°C</bdi>`:""}</div>
       <button class="toggle" data-act="sw" data-id="${s.id}" data-n="${o.index}" data-on="${o.on?0:1}">${o.on?t("turnOff"):t("turnOn")}</button>
       <div class="timer">${timerText(o.timer)}</div>
       <button class="link" data-act="timer" data-id="${s.id}" data-n="${o.index}" data-on="${o.on?0:1}">${t("timer")}</button>
     </div>`).join("")}</div></section>`;}).join("");
}
async function refresh(){if(busy)return;try{state=await call("api/state");document.getElementById("err").textContent="";render()}catch(e){document.getElementById("err").textContent=t("fail")+e.message}}
document.getElementById("app").addEventListener("click",async ev=>{
  const b=ev.target.closest("[data-act]");if(!b)return;
  const id=b.dataset.id,outlet=+b.dataset.n,on=b.dataset.on==="1";
  try{
    if(b.dataset.act==="sw"){busy=true;b.disabled=true;await call("api/switch",{strip:id,outlet,on})}
    else if(b.dataset.act==="rename"){const n=prompt(t("renameAsk"));if(n===null)return;await call("api/rename",{strip:id,outlet,name:n})}
    else if(b.dataset.act==="timer"){const m=prompt(t("timerAsk"),"30");if(m===null)return;await call("api/timer",{strip:id,outlet,minutes:+m||0,on})}
  }catch(e){document.getElementById("err").textContent=t("fail")+e.message}
  finally{busy=false;refresh()}
});
document.getElementById("lang").onclick=()=>{lang=lang==="ar"?"en":"ar";localStorage.setItem("sp-lang",lang);render()};
refresh();setInterval(refresh,2000);
</script>
</body></html>
"""


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, hub: Hub, loop: asyncio.AbstractEventLoop, token: str, public_ip: str):
        super().__init__(addr, WebHandler)
        self.hub, self.loop, self.token, self.public_ip = hub, loop, token, public_ip

    def run_on_loop(self, coro, timeout: float = 20.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)


class WebHandler(BaseHTTPRequestHandler):
    server: WebServer
    server_version = "DarwishSmartPower/" + VERSION
    PUBLIC = ("/smartpower.py", "/app.apk", "/welcome")

    def log_message(self, fmt, *args):
        pass

    # ---- helpers

    def token_ok(self) -> bool:
        expected = self.server.token
        if not expected:
            return True
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
        return any(o and hmac.compare_digest(o.encode(), expected.encode()) for o in offered)

    def reply(self, code: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def reply_json(self, code: int, obj: Dict[str, Any]) -> None:
        self.reply(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def deny(self) -> None:
        self.reply(401, b"token required\n", "text/plain; charset=utf-8",
                   {"WWW-Authenticate": 'Basic realm="Darwish Smart Power"'})

    def send_file(self, path: Path, ctype: str, filename: str) -> None:
        if not path.is_file():
            self.reply_json(404, {"error": "not built yet - see README (Android app)"})
            return
        self.reply(200, path.read_bytes(), ctype, {"Content-Disposition": 'attachment; filename="%s"' % filename})

    def send_welcome(self) -> None:
        page = WEBSITE_PATH.read_text(encoding="utf-8") if WEBSITE_PATH.is_file() else None
        if page is None:
            self.reply_json(404, {"error": "website/index.html is missing"})
            return
        if find_apk():                              # download the app from this server instead of GitHub
            page = page.replace(APK_RELEASE_URL, "/app.apk")
        self.reply(200, page.encode("utf-8"), "text/html; charset=utf-8")

    # ---- routes

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/smartpower.py":                 # public: lets a phone or laptop fetch the provisioner
            self.send_file(Path(__file__).resolve(), "text/x-python", "smartpower.py")
            return
        if path in ("/welcome", "/welcome/"):        # public: the introduction page with an app download button
            self.send_welcome()
            return
        if path == "/app.apk":                      # public: install the Android app from the phone browser
            self.send_file(find_apk() or APK_CANDIDATES[0], "application/vnd.android.package-archive", "darwish-smart-power.apk")
            return
        if not self.token_ok():
            self.deny()
            return
        if path in ("/", "/index.html"):
            self.reply(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            snap = self.server.run_on_loop(self.server.hub.snapshot())
            snap["server"] = {"ip": self.server.public_ip, "strip_port": STRIP_PORT, "version": VERSION}
            self.reply_json(200, snap)
        elif path == "/api/health":
            self.reply_json(200, {"ok": True, "app": "darwish-smart-power", "version": VERSION})
        else:
            self.reply_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.token_ok():
            self.deny()
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
            strip_id = str(req["strip"])
            outlet = int(req.get("outlet", 0))
            if outlet != 0 and outlet not in OUTLETS:
                raise ValueError("outlet must be 0 (all) or 1-4")
        except (KeyError, TypeError, ValueError) as err:
            self.reply_json(400, {"error": "bad request: %s" % err})
            return

        hub = self.server.hub
        try:
            if path == "/api/switch":
                code, body = self.server.run_on_loop(hub.switch(strip_id, outlet, bool(req.get("on"))))
            elif path == "/api/rename":
                name = str(req.get("name") or "").strip()[:40]
                code, body = self.server.run_on_loop(hub.rename(strip_id, outlet, name))
            elif path == "/api/timer":
                minutes = float(req.get("minutes", 0))
                if not 0 <= minutes <= 7 * 24 * 60:
                    raise ValueError("minutes must be between 0 and 10080")
                code, body = self.server.run_on_loop(hub.set_timer(strip_id, outlet, minutes, bool(req.get("on"))))
            else:
                code, body = 404, {"error": "not found"}
        except (TypeError, ValueError) as err:
            code, body = 400, {"error": "bad request: %s" % err}
        except Exception as err:
            code, body = 502, {"error": "command failed: %r" % (err,)}
        self.reply_json(code, body)


# --------------------------------------------------------------------------- commands

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
    hub = Hub(store)
    strip_server = await asyncio.start_server(hub.accept, args.bind, STRIP_PORT)
    ip = args.public_ip or guess_lan_ip()
    web = WebServer((args.bind, args.web_port), hub, asyncio.get_running_loop(), args.token, ip)
    threading.Thread(target=web.serve_forever, daemon=True).start()

    url = "http://%s:%d/" % (ip, args.web_port)
    print("Darwish Smart Power %s" % VERSION)
    print("  web page / app   %s%s" % (url, "?token=<TOKEN>" if args.token else ""))
    print("  strip port       TCP %d" % STRIP_PORT)
    print("  security         %s" % ("token required" if args.token else "NO TOKEN - only use on a home network you trust"))
    print("  saved settings   %s" % store.path)
    if find_apk():
        print("  Android app      %sapp.apk" % url)
    print("  intro page       %swelcome" % url)
    print("  provision with   python3 smartpower.py provision --server-ip %s --ssid WIFI --wifi-password PASS" % ip)
    print("  Ctrl+C to stop", flush=True)
    async with strip_server:
        await asyncio.gather(hub.poll_forever(), hub.timers_forever())
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
    hub = Hub(Store(Path(tmpdir) / "data.json"))
    srv = await asyncio.start_server(hub.accept, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    relays = {1: False, 2: True, 3: False, 4: False}
    sent: List[str] = []

    def getinfo() -> str:
        blocks = []
        for n in (1, 2, 3, 4, 5):
            on = relays.get(n, any(relays.values()))
            mw = 60000 if (n == 2 and on) else 0
            blocks.append("%d:%d;%s;%d;0;0;%d;%08X;00000000;00000000;0;00;%d" % (n, n * 10, "on" if on else "off", on, mw, 1500 * n, 30 + n))
        return "up:getinfo:" + ":".join(blocks)

    async def fake_strip() -> None:
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

    strip_task = asyncio.ensure_future(fake_strip())
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
    status, state = await loop.run_in_executor(None, http, "/api/state")
    assert status == 200 and state["strips"][0]["outlets"][0]["name"] == "Kettle"
    status, res = await loop.run_in_executor(None, http, "/api/switch", {"strip": "A1B2C3D4E5F6", "outlet": 4, "on": False})
    assert status == 200 and res["confirmed"] and not relays[4]
    assert (await loop.run_in_executor(None, http, "/api/switch", {"strip": "A1B2C3D4E5F6", "outlet": 9, "on": True}))[0] == 400
    web.shutdown()

    strip_task.cancel()
    writer.close()
    await asyncio.sleep(0.1)
    assert not strip.online
    srv.close()
    print("selftest OK: protocol parsing, switching, button events, names, timers, web API + token")


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
