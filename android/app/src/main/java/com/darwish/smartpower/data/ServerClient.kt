package com.darwish.smartpower.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** An answer from the server that was not a success. [code] is the HTTP status. */
class ServerException(val code: Int, message: String, val pinNeeded: Boolean = false) : Exception(message)

enum class SwitchOutcome { CONFIRMED, UNCONFIRMED, QUEUED }

/** Talks to smartpower.py over its JSON API. All calls run on the IO dispatcher. */
class ServerClient(private val address: ServerAddress, private val token: String) {

    suspend fun state(): ServerState = StateJson.parseState(call("GET", "api/state"))

    /** Returns the server version when the address and token are right. */
    suspend fun health(): String = JSONObject(call("GET", "api/health")).optString("version", "?")

    /**
     * Switches [outlets] (0 = all). [pin] is needed for a locked strip.
     * Returns the updated strip and the outcome; an offline strip runs it when it reconnects.
     */
    suspend fun switch(stripId: String, outlets: List<Int>, on: Boolean, pin: String? = null): Pair<Strip, SwitchOutcome> {
        val body = JSONObject().put("strip", stripId).put("outlets", JSONArray(outlets)).put("on", on)
        pin?.let { body.put("pin", it) }
        val reply = JSONObject(call("POST", "api/switch", body))
        val outcome = when {
            reply.optBoolean("queued") -> SwitchOutcome.QUEUED
            reply.optBoolean("confirmed") -> SwitchOutcome.CONFIRMED
            else -> SwitchOutcome.UNCONFIRMED
        }
        return StateJson.parseStrip(reply.getJSONObject("strip")) to outcome
    }

    /** Sets, changes ([oldPin] needed) or removes (empty [pin]) the strip's PIN. */
    suspend fun setLock(stripId: String, pin: String, oldPin: String? = null): Strip {
        val body = JSONObject().put("strip", stripId).put("pin", pin)
        oldPin?.let { body.put("old_pin", it) }
        return StateJson.parseStrip(JSONObject(call("POST", "api/lock", body)).getJSONObject("strip"))
    }

    suspend fun saveScene(scene: Scene): List<Scene> {
        val actions = JSONArray()
        scene.actions.forEach { actions.put(JSONObject().put("strip", it.stripId).put("outlet", it.outlet).put("on", it.turnOn)) }
        val body = JSONObject().put("name", scene.name).put("icon", scene.icon).put("actions", actions)
        if (scene.id.isNotEmpty()) body.put("id", scene.id)
        return StateJson.parseScenes(JSONObject(call("POST", "api/scenes/save", body)).optJSONArray("scenes"))
    }

    suspend fun deleteScene(id: String): List<Scene> =
        StateJson.parseScenes(JSONObject(call("POST", "api/scenes/delete", JSONObject().put("id", id))).optJSONArray("scenes"))

    suspend fun runScene(id: String, pin: String? = null) {
        val body = JSONObject().put("id", id)
        pin?.let { body.put("pin", it) }
        call("POST", "api/scenes/run", body)
    }

    /** [outlet] 0 renames the strip itself. An empty [name] restores the default. */
    suspend fun rename(stripId: String, outlet: Int, name: String): Strip {
        val body = JSONObject().put("strip", stripId).put("outlet", outlet).put("name", name)
        return StateJson.parseStrip(JSONObject(call("POST", "api/rename", body)).getJSONObject("strip"))
    }

    /** Switch [outlets] (0 = all) to [turnOn] after [minutes]; 0 minutes cancels their timers. */
    suspend fun timer(stripId: String, outlets: List<Int>, minutes: Int, turnOn: Boolean, pin: String? = null): Strip {
        val body = JSONObject().put("strip", stripId).put("outlets", JSONArray(outlets))
            .put("minutes", minutes).put("on", turnOn)
        pin?.let { body.put("pin", it) }
        return StateJson.parseStrip(JSONObject(call("POST", "api/timer", body)).getJSONObject("strip"))
    }

    /** [range]: day, week or month. [stripId] null = all strips. */
    suspend fun history(range: String, stripId: String? = null): EnergyReport {
        val query = "api/history?range=$range" + (stripId?.let { "&strip=$it" } ?: "")
        return StateJson.parseReport(call("GET", query))
    }

    /** Alerts newer than [afterId], newest first, plus the newest id on the server. */
    suspend fun events(afterId: Long): Pair<List<AlertEvent>, Long> =
        StateJson.parseEvents(call("GET", "api/events?after=$afterId"))

    /** Room for the strip ([outlet] 0), or icon / favourite for an outlet. Null values are left as they are. */
    suspend fun setMeta(stripId: String, outlet: Int, room: String? = null, icon: String? = null, favorite: Boolean? = null): Strip {
        val body = JSONObject().put("strip", stripId).put("outlet", outlet)
        room?.let { body.put("room", it) }
        icon?.let { body.put("icon", it) }
        favorite?.let { body.put("favorite", it) }
        return StateJson.parseStrip(JSONObject(call("POST", "api/meta", body)).getJSONObject("strip"))
    }

    /** Creates a schedule or cycle, or replaces the one with the same id. Returns all schedules. */
    suspend fun saveSchedule(s: Schedule, pin: String? = null): List<Schedule> {
        val body = JSONObject().put("strip", s.stripId).put("outlets", JSONArray(s.outlets))
            .put("kind", s.kind).put("enabled", s.enabled)
        if (s.isCycle) {
            body.put("on_minutes", s.onMinutes).put("off_minutes", s.offMinutes)
        } else {
            body.put("on", s.turnOn).put("time", s.time).put("days", JSONArray(s.days))
        }
        if (s.id.isNotEmpty()) body.put("id", s.id)
        pin?.let { body.put("pin", it) }
        return StateJson.parseSchedules(JSONObject(call("POST", "api/schedules/save", body)).optJSONArray("schedules"))
    }

    suspend fun deleteSchedule(id: String, pin: String? = null): List<Schedule> {
        val body = JSONObject().put("id", id)
        pin?.let { body.put("pin", it) }
        return StateJson.parseSchedules(JSONObject(call("POST", "api/schedules/delete", body)).optJSONArray("schedules"))
    }

    suspend fun updateSettings(s: ServerSettings): ServerSettings {
        val body = JSONObject().put("price_kwh", s.pricePerKwh).put("currency", s.currency)
            .put("max_temp_c", s.maxTempC).put("max_watts", s.maxWatts).put("alexa", s.alexa)
        return StateJson.parseSettings(JSONObject(call("POST", "api/settings", body)).optJSONObject("settings"))
    }

    suspend fun members(): List<Member> = StateJson.parseMembers(JSONObject(call("GET", "api/users")).optJSONArray("users"))

    /** Adds a family member; returns everyone and the new member's token (shown only this once). */
    suspend fun addMember(name: String, role: String, strips: List<String>): Pair<List<Member>, String> {
        val body = JSONObject().put("name", name).put("role", role).put("strips", JSONArray(strips))
        val reply = JSONObject(call("POST", "api/users/add", body))
        return StateJson.parseMembers(reply.optJSONArray("users")) to reply.getString("token")
    }

    /** Changes a member; with [newToken] the old token stops working and the new one is returned. */
    suspend fun updateMember(id: String, role: String? = null, strips: List<String>? = null, newToken: Boolean = false): Pair<List<Member>, String?> {
        val body = JSONObject().put("id", id)
        role?.let { body.put("role", it) }
        strips?.let { body.put("strips", JSONArray(it)) }
        if (newToken) body.put("new_token", true)
        val reply = JSONObject(call("POST", "api/users/update", body))
        return StateJson.parseMembers(reply.optJSONArray("users")) to reply.optString("token").takeIf { it.isNotEmpty() }
    }

    suspend fun deleteMember(id: String): List<Member> =
        StateJson.parseMembers(JSONObject(call("POST", "api/users/delete", JSONObject().put("id", id))).optJSONArray("users"))

    /** Tells the server a strip with this setup code is being added from the app, so it is approved by itself. */
    suspend fun expectStrip(code: String) {
        call("POST", "api/strips/expect", JSONObject().put("code", code))
    }

    suspend fun approveStrip(id: String) {
        call("POST", "api/strips/approve", JSONObject().put("strip", id))
    }

    /** Forgets a strip; with [block] the server refuses it from now on. */
    suspend fun removeStrip(id: String, block: Boolean) {
        call("POST", "api/strips/remove", JSONObject().put("strip", id).put("block", block))
    }

    private suspend fun call(method: String, path: String, body: JSONObject? = null): String =
        withContext(Dispatchers.IO) {
            val conn = URL(address.url(path)).openConnection() as HttpURLConnection
            try {
                conn.requestMethod = method
                conn.connectTimeout = 5_000
                conn.readTimeout = 20_000                 // switching waits for the strip to confirm
                conn.setRequestProperty("Accept", "application/json")
                if (token.isNotEmpty()) conn.setRequestProperty("X-Token", token)
                if (body != null) {
                    conn.doOutput = true
                    conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                    conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
                }
                val code = conn.responseCode
                val stream = if (code in 200..299) conn.inputStream else conn.errorStream
                val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
                if (code !in 200..299) {
                    val json = runCatching { JSONObject(text) }.getOrNull()
                    val error = json?.optString("error")
                    throw ServerException(code, error?.takeIf { it.isNotBlank() } ?: "HTTP $code",
                        pinNeeded = json?.optBoolean("locked", false) == true)
                }
                text
            } finally {
                conn.disconnect()
            }
        }
}
