package com.darwish.smartpower.data

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
) {
    val anyOn: Boolean get() = outlets.any { it.on }
}

data class ServerState(val strips: List<Strip>, val serverIp: String?, val version: String?)

/** Reads the JSON the Darwish Smart Power server (smartpower.py) returns. */
object StateJson {

    fun parseState(text: String): ServerState {
        val root = JSONObject(text)
        val list = root.optJSONArray("strips")
        val strips = (0 until (list?.length() ?: 0)).map { parseStrip(list!!.getJSONObject(it)) }
        val server = root.optJSONObject("server")
        return ServerState(strips, server?.stringOrNull("ip"), server?.stringOrNull("version"))
    }

    fun parseStrip(o: JSONObject): Strip {
        val outlets = o.optJSONArray("outlets")
        return Strip(
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
            outlets = (0 until (outlets?.length() ?: 0)).map { parseOutlet(outlets!!.getJSONObject(it)) }
                .sortedBy { it.index },
        )
    }

    private fun parseOutlet(o: JSONObject) = Outlet(
        index = o.getInt("index"),
        name = o.stringOrNull("name"),
        on = o.optBoolean("on", false),
        watts = o.optDouble("watts", 0.0),
        kwh = o.optDouble("kwh", 0.0),
        tempC = o.doubleOrNull("temp_c")?.toInt(),
        timer = parseTimer(o.optJSONObject("timer")),
    )

    private fun parseTimer(o: JSONObject?): StripTimer? {
        if (o == null || !o.has("at")) return null
        return StripTimer(turnOn = o.optBoolean("on", false), atEpochSeconds = o.optLong("at"))
    }

    // org.json turns JSON null into the string "null" in optString, so check explicitly.
    private fun JSONObject.stringOrNull(key: String): String? =
        if (!has(key) || isNull(key)) null else getString(key).takeIf { it.isNotBlank() }

    private fun JSONObject.doubleOrNull(key: String): Double? =
        if (!has(key) || isNull(key)) null else optDouble(key).takeUnless { it.isNaN() }
}
