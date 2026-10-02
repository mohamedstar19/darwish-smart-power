package com.darwish.smartpower.ui

import android.app.Application
import androidx.annotation.StringRes
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.darwish.smartpower.R
import com.darwish.smartpower.data.Prefs
import com.darwish.smartpower.data.ServerAddress
import com.darwish.smartpower.data.ServerClient
import com.darwish.smartpower.data.ServerException
import com.darwish.smartpower.data.SetupResult
import com.darwish.smartpower.data.Strip
import com.darwish.smartpower.data.StripSetup
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException

/** Why the strips cannot be shown right now. */
enum class Problem { NOT_CONFIGURED, UNREACHABLE, BAD_TOKEN, SERVER }

data class UiState(
    val addressText: String = "",
    val token: String = "",
    val strips: List<Strip> = emptyList(),
    val serverIp: String? = null,
    val loaded: Boolean = false,
    val problem: Problem? = null,
    /** "stripId/outlet" keys with a command still running. */
    val pending: Set<String> = emptySet(),
)

/** A one-off message for the snackbar. */
data class Note(@param:StringRes val text: Int, val arg: String? = null)

class AppViewModel(app: Application) : AndroidViewModel(app) {
    private val prefs = Prefs(app)
    private val setup = StripSetup(app)

    private val _state = MutableStateFlow(UiState(addressText = prefs.addressText, token = prefs.token))
    val state: StateFlow<UiState> = _state.asStateFlow()

    private val notes = Channel<Note>(Channel.BUFFERED)
    val messages = notes.receiveAsFlow()

    private fun client(): ServerClient? = prefs.address?.let { ServerClient(it, prefs.token) }

    /** Runs while the home screen is visible. */
    suspend fun pollForever() {
        while (true) {
            refresh()
            delay(2_000)
        }
    }

    suspend fun refresh() {
        val client = client()
        if (client == null) {
            _state.update { it.copy(problem = Problem.NOT_CONFIGURED, strips = emptyList(), loaded = true) }
            return
        }
        try {
            val server = client.state()
            _state.update { s ->
                s.copy(
                    strips = server.strips.map { keepPending(it, s) },
                    serverIp = server.serverIp,
                    loaded = true,
                    problem = null,
                )
            }
        } catch (e: Exception) {
            _state.update { it.copy(problem = problemOf(e), loaded = true) }
        }
    }

    /** While a switch is in flight, show the requested state instead of a stale poll. */
    private fun keepPending(fresh: Strip, s: UiState): Strip {
        val old = s.strips.firstOrNull { it.id == fresh.id } ?: return fresh
        if (s.pending.none { it.startsWith(fresh.id + "/") }) return fresh
        return fresh.copy(outlets = fresh.outlets.map { o ->
            val shown = old.outlets.firstOrNull { it.index == o.index }
            if (shown != null && (key(fresh.id, o.index) in s.pending || key(fresh.id, 0) in s.pending)) o.copy(on = shown.on) else o
        })
    }

    fun switch(stripId: String, outlet: Int, on: Boolean) {
        val k = key(stripId, outlet)
        if (k in _state.value.pending) return                    // one command per outlet at a time
        val client = client() ?: return
        _state.update { s ->
            s.copy(
                pending = s.pending + k,
                strips = s.strips.map { strip ->
                    if (strip.id != stripId) strip
                    else strip.copy(outlets = strip.outlets.map { if (outlet == 0 || it.index == outlet) it.copy(on = on) else it })
                },
            )
        }
        viewModelScope.launch {
            try {
                val (strip, confirmed) = client.switch(stripId, outlet, on)
                _state.update { s -> s.copy(strips = s.strips.map { if (it.id == strip.id) strip else it }) }
                if (!confirmed) notes.send(Note(R.string.note_not_confirmed))
            } catch (e: Exception) {
                notes.send(noteOf(e))
            } finally {
                _state.update { it.copy(pending = it.pending - k) }
                refresh()
            }
        }
    }

    fun rename(stripId: String, outlet: Int, name: String) = act { client ->
        replace(client.rename(stripId, outlet, name.trim()))
    }

    fun setTimer(stripId: String, outlet: Int, minutes: Int, turnOn: Boolean) = act { client ->
        replace(client.timer(stripId, outlet, minutes, turnOn))
        notes.send(Note(if (minutes == 0) R.string.note_timer_cancelled else R.string.note_timer_set))
    }

    private fun replace(strip: Strip) =
        _state.update { s -> s.copy(strips = s.strips.map { if (it.id == strip.id) strip else it }) }

    private fun act(block: suspend (ServerClient) -> Unit) {
        val client = client() ?: return
        viewModelScope.launch {
            try {
                block(client)
            } catch (e: Exception) {
                notes.send(noteOf(e))
            }
        }
    }

    // ---- settings

    /** Saves the address and token; returns false when the address cannot be used. */
    fun saveSettings(addressText: String, token: String): Boolean {
        val address = ServerAddress.parse(addressText) ?: return false
        prefs.addressText = address.display()
        prefs.token = token.trim()
        _state.update {
            it.copy(addressText = prefs.addressText, token = prefs.token, strips = emptyList(), loaded = false, problem = null)
        }
        viewModelScope.launch {
            notes.send(Note(R.string.settings_saved))
            refresh()
        }
        return true
    }

    /** Checks an address/token pair without saving it. */
    suspend fun test(addressText: String, token: String): Note {
        val address = ServerAddress.parse(addressText) ?: return Note(R.string.settings_bad_address)
        return try {
            val client = ServerClient(address, token.trim())
            val version = client.health()
            val strips = client.state().strips
            Note(R.string.test_ok, "$version · ${strips.count { it.online }}/${strips.size}")
        } catch (e: Exception) {
            noteOf(e)
        }
    }

    // ---- strip setup

    suspend fun provision(serverIp: String, ssid: String, password: String): SetupResult =
        setup.provision(serverIp, ssid, password)

    /** Best guess for the IP the strip should dial: what the server reports, else the saved host. */
    fun suggestedServerIp(): String =
        _state.value.serverIp ?: prefs.address?.host?.takeIf { it.all { c -> c.isDigit() || c == '.' } }.orEmpty()

    companion object {
        fun key(stripId: String, outlet: Int) = "$stripId/$outlet"

        fun problemOf(e: Exception): Problem = when {
            e is ServerException && e.code == 401 -> Problem.BAD_TOKEN
            e is ServerException -> Problem.SERVER
            e is IOException -> Problem.UNREACHABLE
            else -> Problem.SERVER
        }

        fun noteOf(e: Exception): Note = when {
            e is ServerException && e.code == 401 -> Note(R.string.problem_bad_token)
            e is ServerException && e.code == 503 -> Note(R.string.note_strip_offline)
            e is ServerException -> Note(R.string.note_server_error, e.message ?: "HTTP ${e.code}")
            e is IOException -> Note(R.string.problem_unreachable)
            else -> Note(R.string.note_server_error, e.message ?: e.javaClass.simpleName)
        }
    }
}
