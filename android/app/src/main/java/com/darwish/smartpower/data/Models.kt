package com.darwish.smartpower.data

import org.json.JSONArray
import org.json.JSONObject

/** A pending automatic switch: [turnOn] at [atEpochSeconds]. */
data class StripTimer(val turnOn: Boolean, val atEpochSeconds: Long)

data class Outlet(
    val index: Int,
    val name: String?,
    val on: Boolean,
    val watts: Double,
    val kwh: Double,
    val tempC: Int?,
    val timer: StripTimer?,
    val icon: String = "plug",
    val favorite: Boolean = false,
    val todayKwh: Double = 0.0,
)

data class Strip(
    val id: String,
    val name: String?,
    val model: String,
    val firmware: String,
    val online: Boolean,
    val lastSeenEpochSeconds: Long,
    val watts: Double,
    val kwh: Double,
    val volts: Double?,
    val amps: Double?,
    val rssi: Int?,
    val timer: StripTimer?,
    val outlets: List<Outlet>,
    val room: String? = null,
    val todayKwh: Double = 0.0,
    /** A PIN is needed to switch it. */
    val locked: Boolean = false,
    /** Commands waiting until the strip is back online: outlet (0 = all) to on/off. */
    val pending: Map<Int, Boolean> = emptyMap(),
) {
    val anyOn: Boolean get() = outlets.any { it.on }
}

/**
 * [kind] "time": switch [outlets] to [turnOn] at [time] ("HH:MM") on [days] (0 = Monday … 6 = Sunday).
 * [kind] "cycle": on for [onMinutes], off for [offMinutes], repeating.
 * [outlets] contains 0 for all outlets.
 */
data class Schedule(
    val id: String,
    val stripId: String,
    val outlets: List<Int>,
    val turnOn: Boolean = false,
    val time: String = "00:00",
    val days: List<Int> = emptyList(),
    val enabled: Boolean = true,
    val kind: String = KIND_TIME,
    val onMinutes: Int = 0,
    val offMinutes: Int = 0,
    /** For a running cycle: whether it is in its "on" part now, and when that changes. */
    val phaseOn: Boolean? = null,
    val nextChangeEpochSeconds: Long = 0,
    /** Monitoring rule: while on, [metric] ("power" W / "temp" °C) above (or below) [value] for [seconds]
     *  makes the server switch the outlets off ([action] "off") or only report it ("alert"). */
    val metric: String = METRIC_POWER,
    val above: Boolean = true,
    val value: Double = 0.0,
    val seconds: Int = 0,
    val action: String = ACTION_OFF,
) {
    val isCycle: Boolean get() = kind == KIND_CYCLE
    val isWatch: Boolean get() = kind == KIND_WATCH

    companion object {
        const val KIND_TIME = "time"
        const val KIND_CYCLE = "cycle"
        const val KIND_WATCH = "watch"
        const val METRIC_POWER = "power"
        const val METRIC_TEMP = "temp"
        const val ACTION_OFF = "off"
        const val ACTION_ALERT = "alert"
    }
}

data class SceneAction(val stripId: String, val outlet: Int, val turnOn: Boolean)

/** A saved group of outlet states, run with one tap. [icon] is an emoji. */
data class Scene(val id: String, val name: String, val icon: String, val actions: List<SceneAction>)

data class ServerSettings(
    val pricePerKwh: Double = 1.5,
    val currency: String = "EGP",
    val maxTempC: Double = 60.0,
    val maxWatts: Double = 3000.0,
    /** Offer the outlets to Amazon Echo devices on the home network. */
    val alexa: Boolean = true,
)

data class Usage(val kwh: Double, val cost: Double)

/** Who this app is signed in as: "owner" (the server password) or a family member ("control" / "view"). */
data class Me(val role: String = ROLE_OWNER, val name: String? = null, val login: String? = null) {
    val isOwner: Boolean get() = role == ROLE_OWNER
    val isCustomer: Boolean get() = role == ROLE_CUSTOMER
    val canControl: Boolean get() = role != ROLE_VIEW

    companion object {
        const val ROLE_OWNER = "owner"
        const val ROLE_CONTROL = "control"
        const val ROLE_VIEW = "view"
        /** Signed up in the app; sees only the strips they added. */
        const val ROLE_CUSTOMER = "customer"
    }
}

/** A family member the owner shared access with. [strips] empty = all strips. */
data class Member(val id: String, val name: String, val role: String, val strips: List<String>, val lastSeenEpochSeconds: Long)

/** A strip that connected but is not approved yet (shown to the owner only). */
data class NewStrip(val id: String, val address: String, val online: Boolean)

data class ServerState(
    val strips: List<Strip>,
    val serverIp: String?,
    val version: String?,
    val schedules: List<Schedule> = emptyList(),
    val scenes: List<Scene> = emptyList(),
    val settings: ServerSettings = ServerSettings(),
    val today: Usage = Usage(0.0, 0.0),
    /** kWh used in each hour of today, 24 values. */
    val todayHours: List<Double> = emptyList(),
    val month: Usage = Usage(0.0, 0.0),
    val lastEventId: Long = 0,
    val me: Me = Me(),
    /** Owner only: strips waiting for approval, and strips the owner blocked. */
    val newStrips: List<NewStrip> = emptyList(),
    val blockedStrips: List<String> = emptyList(),
)

data class EnergyBucket(val startEpochSeconds: Long, val kwh: Double)
data class OutletUsage(val stripId: String, val outlet: Int, val kwh: Double, val cost: Double)

data class EnergyReport(
    val range: String,
    val buckets: List<EnergyBucket>,
    val totalKwh: Double,
    val cost: Double,
    val currency: String,
    val byOutlet: List<OutletUsage>,
)

/** kind: offline, online, temp, power. */
data class AlertEvent(
    val id: Long,
    val epochSeconds: Long,
    val kind: String,
    val stripId: String,
    val outlet: Int,
    val value: Double,
)

/** Reads the JSON the Darwish Smart Power server (smartpower.py) returns. */
object StateJson {

    fun parseState(text: String): ServerState {
        val root = JSONObject(text)
        val server = root.optJSONObject("server")
        val today = root.optJSONObject("today")
        return ServerState(
            strips = root.optJSONArray("strips").objects().map { parseStrip(it) },
            serverIp = server?.stringOrNull("ip"),
            version = server?.stringOrNull("version"),
            schedules = parseSchedules(root.optJSONArray("schedules")),
            scenes = parseScenes(root.optJSONArray("scenes")),
            settings = parseSettings(root.optJSONObject("settings")),
            today = parseUsage(today),
            todayHours = today?.optJSONArray("hours").doubles(),
            month = parseUsage(root.optJSONObject("month")),
            lastEventId = root.optLong("last_event", 0L),
            me = root.optJSONObject("me")?.let { Me(it.optString("role", Me.ROLE_OWNER), it.stringOrNull("name"), it.stringOrNull("login")) }
                ?: Me(),
            newStrips = root.optJSONArray("new_strips").objects().map {
                NewStrip(it.getString("id"), it.optString("address"), it.optBoolean("online"))
            },
            blockedStrips = root.optJSONArray("blocked_strips")?.let { a -> (0 until a.length()).map { a.getString(it) } }
                ?: emptyList(),
        )
    }

    fun parseMembers(a: JSONArray?): List<Member> = a.objects().map {
        Member(
            id = it.getString("id"),
            name = it.optString("name"),
            role = it.optString("role", Me.ROLE_CONTROL),
            strips = it.optJSONArray("strips")?.let { s -> (0 until s.length()).map { i -> s.getString(i) } } ?: emptyList(),
            lastSeenEpochSeconds = it.optLong("last_seen", 0L),
        )
    }

    fun parseStrip(o: JSONObject): Strip = Strip(
        id = o.getString("id"),
        name = o.stringOrNull("name"),
        model = o.stringOrNull("model").orEmpty(),
        firmware = o.stringOrNull("fw").orEmpty(),
        online = o.optBoolean("online", false),
        lastSeenEpochSeconds = o.optLong("last_seen", 0L),
        watts = o.optDouble("watts", 0.0),
        kwh = o.optDouble("kwh", 0.0),
        volts = o.doubleOrNull("volts"),
        amps = o.doubleOrNull("amps"),
        rssi = o.doubleOrNull("rssi")?.toInt(),
        timer = parseTimer(o.optJSONObject("timer")),
        outlets = o.optJSONArray("outlets").objects().map { parseOutlet(it) }.sortedBy { it.index },
        room = o.stringOrNull("room"),
        todayKwh = o.optDouble("today_kwh", 0.0),
        locked = o.optBoolean("locked", false),
        pending = o.optJSONObject("pending")?.let { p ->
            p.keys().asSequence().mapNotNull { k -> k.toIntOrNull()?.let { it to p.optBoolean(k) } }.toMap()
        } ?: emptyMap(),
    )

    private fun parseOutlet(o: JSONObject) = Outlet(
        index = o.getInt("index"),
        name = o.stringOrNull("name"),
        on = o.optBoolean("on", false),
        watts = o.optDouble("watts", 0.0),
        kwh = o.optDouble("kwh", 0.0),
        tempC = o.doubleOrNull("temp_c")?.toInt(),
        timer = parseTimer(o.optJSONObject("timer")),
        icon = o.stringOrNull("icon") ?: "plug",
        favorite = o.optBoolean("favorite", false),
        todayKwh = o.optDouble("today_kwh", 0.0),
    )

    fun parseSchedules(a: JSONArray?): List<Schedule> = a.objects().map { s ->
        Schedule(
            id = s.getString("id"),
            stripId = s.getString("strip"),
            outlets = s.optJSONArray("outlets")?.doubles()?.map { it.toInt() } ?: listOf(s.optInt("outlet", 0)),
            turnOn = s.optBoolean("on", false),
            time = s.optString("time", "00:00"),
            days = s.optJSONArray("days").doubles().map { it.toInt() },
            enabled = s.optBoolean("enabled", true),
            kind = s.optString("kind", Schedule.KIND_TIME),
            onMinutes = s.optInt("on_minutes", 0),
            offMinutes = s.optInt("off_minutes", 0),
            phaseOn = if (s.has("phase_on")) s.optBoolean("phase_on") else null,
            nextChangeEpochSeconds = s.optLong("next_change", 0L),
            metric = s.optString("metric", Schedule.METRIC_POWER),
            above = s.optBoolean("above", true),
            value = s.optDouble("value", 0.0),
            seconds = s.optInt("seconds", 0),
            action = s.optString("action", Schedule.ACTION_OFF),
        )
    }

    fun parseScenes(a: JSONArray?): List<Scene> = a.objects().map { s ->
        Scene(
            id = s.getString("id"),
            name = s.optString("name"),
            icon = s.stringOrNull("icon") ?: "✨",
            actions = s.optJSONArray("actions").objects().map {
                SceneAction(it.getString("strip"), it.optInt("outlet", 0), it.optBoolean("on", false))
            },
        )
    }

    fun parseSettings(o: JSONObject?): ServerSettings {
        val d = ServerSettings()
        if (o == null) return d
        return ServerSettings(
            pricePerKwh = o.optDouble("price_kwh", d.pricePerKwh),
            currency = o.stringOrNull("currency") ?: d.currency,
            maxTempC = o.optDouble("max_temp_c", d.maxTempC),
            maxWatts = o.optDouble("max_watts", d.maxWatts),
            alexa = o.optBoolean("alexa", d.alexa),
        )
    }

    fun parseReport(text: String): EnergyReport {
        val o = JSONObject(text)
        return EnergyReport(
            range = o.optString("range", "day"),
            buckets = o.optJSONArray("buckets").objects().map { EnergyBucket(it.optLong("t"), it.optDouble("kwh", 0.0)) },
            totalKwh = o.optDouble("total_kwh", 0.0),
            cost = o.optDouble("cost", 0.0),
            currency = o.stringOrNull("currency") ?: "EGP",
            byOutlet = o.optJSONArray("by_outlet").objects().map {
                OutletUsage(it.getString("strip"), it.optInt("outlet"), it.optDouble("kwh", 0.0), it.optDouble("cost", 0.0))
            },
        )
    }

    fun parseEvents(text: String): Pair<List<AlertEvent>, Long> {
        val o = JSONObject(text)
        val events = o.optJSONArray("events").objects().map {
            AlertEvent(
                id = it.getLong("id"),
                epochSeconds = it.optLong("ts"),
                kind = it.optString("kind"),
                stripId = it.optString("strip"),
                outlet = it.optInt("outlet", 0),
                value = it.optDouble("value", 0.0),
            )
        }
        return events to o.optLong("last_id", 0L)
    }

    private fun parseUsage(o: JSONObject?) = Usage(o?.optDouble("kwh", 0.0) ?: 0.0, o?.optDouble("cost", 0.0) ?: 0.0)

    private fun parseTimer(o: JSONObject?): StripTimer? {
        if (o == null || !o.has("at")) return null
        return StripTimer(turnOn = o.optBoolean("on", false), atEpochSeconds = o.optLong("at"))
    }

    private fun JSONArray?.objects(): List<JSONObject> =
        if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

    private fun JSONArray?.doubles(): List<Double> =
        if (this == null) emptyList() else (0 until length()).map { optDouble(it, 0.0) }

    // org.json turns JSON null into the string "null" in optString, so check explicitly.
    private fun JSONObject.stringOrNull(key: String): String? =
        if (!has(key) || isNull(key)) null else getString(key).takeIf { it.isNotBlank() }

    private fun JSONObject.doubleOrNull(key: String): Double? =
        if (!has(key) || isNull(key)) null else optDouble(key).takeUnless { it.isNaN() }
}
