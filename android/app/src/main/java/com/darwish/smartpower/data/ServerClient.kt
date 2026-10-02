package com.darwish.smartpower.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** An answer from the server that was not a success. [code] is the HTTP status. */
class ServerException(val code: Int, message: String) : Exception(message)

/** Talks to smartpower.py over its JSON API. All calls run on the IO dispatcher. */
class ServerClient(private val address: ServerAddress, private val token: String) {

    suspend fun state(): ServerState = StateJson.parseState(call("GET", "api/state"))

    /** Returns the server version when the address and token are right. */
    suspend fun health(): String = JSONObject(call("GET", "api/health")).optString("version", "?")

    /** [outlet] 0 means all outlets. Returns the updated strip and whether the strip confirmed it. */
    suspend fun switch(stripId: String, outlet: Int, on: Boolean): Pair<Strip, Boolean> {
        val body = JSONObject().put("strip", stripId).put("outlet", outlet).put("on", on)
        val reply = JSONObject(call("POST", "api/switch", body))
        return StateJson.parseStrip(reply.getJSONObject("strip")) to reply.optBoolean("confirmed", false)
    }

    /** [outlet] 0 renames the strip itself. An empty [name] restores the default. */
    suspend fun rename(stripId: String, outlet: Int, name: String): Strip {
        val body = JSONObject().put("strip", stripId).put("outlet", outlet).put("name", name)
        return StateJson.parseStrip(JSONObject(call("POST", "api/rename", body)).getJSONObject("strip"))
    }

    /** Switch [outlet] (0 = all) to [turnOn] after [minutes]; 0 minutes cancels the timer. */
    suspend fun timer(stripId: String, outlet: Int, minutes: Int, turnOn: Boolean): Strip {
        val body = JSONObject().put("strip", stripId).put("outlet", outlet)
            .put("minutes", minutes).put("on", turnOn)
        return StateJson.parseStrip(JSONObject(call("POST", "api/timer", body)).getJSONObject("strip"))
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
                    val error = runCatching { JSONObject(text).optString("error") }.getOrNull()
                    throw ServerException(code, error?.takeIf { it.isNotBlank() } ?: "HTTP $code")
                }
                text
            } finally {
                conn.disconnect()
            }
        }
}
