# Darwish Smart Power ⚡

تحكم كامل في مشترك الكهرباء الذكي **MTTL-W01** (LG U+ / Jinheung، 4 مخارج) من الموبايل أو المتصفح،
عن طريق سيرفر بتاعك إنت. من غير حساب سحابي، ومن غير ما تغيّر حاجة في الراوتر.

[English below ↓](#english)

| الجزء | الملف |
|---|---|
| السيرفر | `smartpower.py`: ملف واحد، Python 3.8+ من غير أي مكتبات خارجية |
| صفحة الويب | جوه السيرفر نفسه: عربي/إنجليزي، فاتح/غامق |
| تطبيق أندرويد | `android/`: Kotlin + Jetpack Compose (Material 3) |
| تشغيله كخدمة | `smartpower.service`: ملف systemd للـ VPS |

## سيرفرك: 192.168.1.116 (بورت 8095) و power.darwish-tech.com

بورت 8080 مستخدم لداشبورد نظام التسويق، و8090 مستخدم لبرنامج تاني على السيرفر، علشان كده Darwish Smart Power شغال على **8095**. ولو 8095 اتحجز في يوم، `install.sh` بيختار بورت فاضي لوحده، وشغّل بعده `tunnel.sh` علشان الدومين يمشي وراه.

**التثبيت بخطوة واحدة** (على السيرفر نفسه):

```bash
cd ~ && { [ -d darwish-smart-power ] || git clone https://github.com/mohamedstar19/darwish-smart-power.git; } && cd darwish-smart-power && git pull -q && sudo bash install.sh
```

السكربت `install.sh` بيعمل باسورد عشوائي، ويثبّت السيرفر كخدمة بتشتغل لوحدها بعد الريستارت، ويفتح البورتات في الـ firewall،
وفي الآخر بيطبع الباسورد والروابط. لو شغّلته تاني بيحدّث التثبيت ويطبع نفس الباسورد.

| إيه | من البيت | من أي مكان (Cloudflare Tunnel) |
|---|---|---|
| لوحة التحكم | `http://192.168.1.116:8095/` | `https://power.darwish-tech.com/` |
| الصفحة التعريفية | `http://192.168.1.116:8095/welcome` | `https://power.darwish-tech.com/welcome` |
| تحميل التطبيق | `http://192.168.1.116:8095/app.apk` | `https://power.darwish-tech.com/app.apk` |

- التطبيق متظبط إنه يتصل بـ `https://power.darwish-tech.com` لوحده، وده شغال من البيت ومن برّه.
  كل اللي عليك تكتب الرمز (token) في الإعدادات.
- علشان رابط `app.apk` يشتغل: نزّل الـ APK من صفحة الـ Releases في GitHub وحطه جنب `smartpower.py` باسم `darwish-smart-power.apk`.
- **ربط الدومين بخطوة واحدة** (على السيرفر):
  `cd ~/darwish-smart-power && git pull -q && sudo bash tunnel.sh power.darwish-tech.com`
  السكربت بيثبّت `cloudflared`، ويطبع رابط واحد تفتحه وتختار الدومين وتدوس Authorize، وبعدها بيعمل الـ tunnel
  والـ subdomain وخدمة بتشتغل لوحدها بعد الريستارت. مش بيلمس أي tunnel تاني على السيرفر (زي بتاع n8n).
- الدومين شغال عن طريق **Cloudflare Tunnel** (Zero Trust ← Networks ← Tunnels)، والـ Public hostname بتاعه
  `power.darwish-tech.com` ← `HTTP` ← `localhost:8095`. مش محتاج Port Forwarding ولا IP عام.
- طول ما الدومين شغال **لازم** السيرفر يكون شغال بـ `--token`.

## المميزات

- تشغيل/إطفاء كل مخرج لوحده، أو الكل مرة واحدة
- استهلاك لحظي بالوات، والطاقة بالـ kWh، والجهد والتيار، والحرارة، وقوة إشارة الواي فاي
- **أسماء للمخارج** (مثلاً "الغلاية"، "التكييف")، محفوظة على السيرفر
- **مؤقت**: "اطفي بعد ساعة" أو "شغّل بعد 30 دقيقة"، ويشتغل على السيرفر حتى لو الموبايل مقفول
- **إعداد المشترك من التطبيق نفسه**: من غير كمبيوتر ومن غير Termux
- لو حد داس على زرار المشترك بإيده، الحالة بتتحدث على طول
- حماية برمز (token) لو السيرفر مفتوح على الإنترنت
- التطبيق عربي وإنجليزي (بيتبع لغة الموبايل، أو تختار من الإعدادات)

## الفكرة ببساطة

المشترك نفسه هو اللي **بيتصل** بالسيرفر على بورت **10086**. خلال الإعداد بنسجّل فيه عنوان السيرفر بتاعك،
وبعدها السيرفر بيستقبل الاتصال ويعرض صفحة ويب وواجهة JSON للتطبيق.

```
الموبايل / المتصفح ──► http://SERVER:8080 ──► smartpower.py ◄── TCP 10086 ── المشترك
```

علشان المشترك هو اللي بيتصل لبرّه، مش محتاج تفتح أي بورت في راوتر البيت.

## 1) شغّل السيرفر

على أي جهاز بيفضل شغال (كمبيوتر، Raspberry Pi، أو VPS):

```bash
python3 smartpower.py
```

هيطبعلك العنوان، مثلاً `http://192.168.1.20:8080/`. افتحه من أي متصفح على نفس الشبكة.

> اعمل **IP ثابت** للجهاز ده (DHCP reservation من الراوتر)، لأن المشترك بيحفظ عنوان واحد بس.

لو السيرفر على الإنترنت (VPS) **لازم** تحط رمز:

```bash
python3 smartpower.py serve --token "رمز-طويل-عشوائي" --public-ip 203.0.113.5
```

وافتح البورتين في الـ firewall: **10086/tcp** (للمشترك) و **8080/tcp** (ليك إنت). وعلشان يشتغل لوحده بعد الريستارت،
استخدم `smartpower.service` (التعليمات جوه الملف).

## 2) اعمل إعداد للمشترك (مرة واحدة)

**من التطبيق (أسهل طريقة):** الإعدادات ← إعداد مشترك، واتبع الخطوات:

1. اضغط زرار المشترك الرئيسي ~10 ثواني لحد ما اللمبة ترمش بسرعة.
2. من واي فاي الموبايل ادخل على شبكة `TONLY_TAP_XXXXXXX` (الباسورد `LGU_` + نفس الـ 7 حروف).
   لو أندرويد قال "مفيش إنترنت" اختار تفضل متصل.
3. اكتب IP السيرفر واسم وباسورد واي فاي البيت (2.4 جيجا بس) واضغط **إرسال للمشترك**.
4. رجّع الموبايل على واي فاي البيت، والمشترك هيظهر خلال دقيقة.

**أو من الكمبيوتر:** بعد ما تدخل على شبكة `TONLY_TAP_...`:

```bash
python3 smartpower.py provision --server-ip 192.168.1.20 --ssid "اسم-الواي-فاي" --wifi-password "الباسورد"
```

> اسم الواي فاي والباسورد ما ينفعش يكون فيهم علامة `:` (بروتوكول المشترك ما بيقبلهاش).

## 3) تطبيق أندرويد

- **تحميل جاهز:** افتح الرابط ده من الموبايل:
  **https://github.com/mohamedstar19/darwish-smart-power/releases/latest/download/darwish-smart-power.apk**
  وبعد ما يتحمّل افتحه واسمح بـ "تثبيت التطبيقات من مصادر غير معروفة".
  كل push على GitHub بيبني نسخة جديدة ويحدّث نفس الرابط.
  لو حطيت الملف جنب `smartpower.py` باسم `darwish-smart-power.apk`، تقدر تنزّله من الموبايل على
  `http://SERVER:8080/app.apk`.
- **أو ابنيه بنفسك:** افتح فولدر `android/` في Android Studio، أو `./gradlew assembleDebug`.

في التطبيق: الإعدادات ← عنوان السيرفر (مثلاً `192.168.1.20` أو `203.0.113.5:8080`) + الرمز ← **اختبار** ← **حفظ**.

## فحص سريع من غير مشترك

```bash
python3 smartpower.py selftest
```

بيشغّل مشترك وهمي ويختبر البروتوكول، والتشغيل/الإطفاء، والأسماء، والمؤقتات، وواجهة الويب بالرمز.

---

<a id="english"></a>
## English

Self-hosted controller for the **MTTL-W01** 4-outlet Wi-Fi power strip (LG U+ / Jinheung):
a one-file Python server with a built-in web page, plus a native Android app.

### Features

- Per-outlet and all-outlet on/off, with live watts, kWh, volts, amps, temperature and Wi-Fi signal
- **Outlet names**, stored on the server
- **Timers** ("turn off in 1 h") that run on the server, so the phone can be off
- **Strip setup from the app**: the phone sends the Wi-Fi and server IP to the strip itself
- Physical button presses on the strip show up immediately
- Optional token for servers reachable from the internet
- Arabic and English in both the app and the web page

**Android app download:** https://github.com/mohamedstar19/darwish-smart-power/releases/latest/download/darwish-smart-power.apk
(rebuilt by CI on every push; or build it yourself with `cd android && ./gradlew assembleDebug`).

### Run it

```bash
python3 smartpower.py                                   # LAN, no token
python3 smartpower.py serve --token SECRET --public-ip 203.0.113.5 --web-port 8080   # VPS
python3 smartpower.py provision --server-ip 192.168.1.20 --ssid HomeWiFi --wifi-password PASS
python3 smartpower.py selftest
```

Options can also come from the environment: `SP_TOKEN`, `SP_PUBLIC_IP`, `SP_WEB_PORT`, `SP_DATA`.
See `smartpower.service` for a systemd setup. The strip port is fixed at **10086** by its firmware.

### HTTP API

Authentication (when `--token` is set): `X-Token: TOKEN`, `Authorization: Bearer TOKEN`,
HTTP Basic with the token as password (what browsers use), or `?token=TOKEN`.
POST bodies must be JSON (`Content-Type: application/json`).

| Method | Path | Body / notes |
|---|---|---|
| GET | `/api/state` | all strips, outlets, readings, names and timers |
| GET | `/api/health` | `{"ok": true, "version": "…"}`, used by the app's Test button |
| POST | `/api/switch` | `{"strip": "<id>", "outlet": 0-4, "on": true}`, where outlet `0` = all; waits for the strip to confirm |
| POST | `/api/rename` | `{"strip": "<id>", "outlet": 0-4, "name": "Kettle"}`, where outlet `0` = the strip; empty name = default |
| POST | `/api/timer` | `{"strip": "<id>", "outlet": 0-4, "minutes": 30, "on": false}`; `minutes: 0` cancels |
| GET | `/smartpower.py`, `/app.apk` | public downloads (no token), for setting up a new phone or laptop |
| GET | `/welcome` | public introduction page (from `website/index.html`); its download button points to `/app.apk` when the APK is next to the server |

### Strip protocol (TCP 10086, CRLF-terminated text lines)

| Direction | Line |
|---|---|
| strip → server | `up:bootinfo:<model>;<mac>;<mac>;<fw>;connect` |
| strip → server | `up:getinfo:1:<12 fields>:2:<…>:3:<…>:4:<…>:5:<…>`, with fields separated by `;`: runtime, relay `on/off`, state, overload, overheat, power mW, energy hex Wh, previous energy, config, status, event, temperature °C |
| strip → server | `up:power_report:<ch>:<value>` (≥ 50000 → millivolts) · `up:query:<rssi>` · `up:event:onoff:<0-4>:on/off` |
| server → strip | `up:getinfo:all` · `up:onoff:<1-4>:on/off` · `up:power_report:1:vol` · `up:query:wifirssi` |
| setup AP `192.168.1.1:30300` | `up:ip:<ipv4>` → `ip_ok` · `up:connect:<ssid>:<password>` → `connect_ok` |

### Credits

The strip's protocol was documented by [ahmedtohamy1/powerk](https://github.com/ahmedtohamy1/powerk).
Darwish Smart Power is a separate implementation written from scratch, with no code taken from that project.
