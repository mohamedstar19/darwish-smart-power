package com.darwish.smartpower.ui

import android.app.Application
import androidx.annotation.StringRes
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.darwish.smartpower.R
import com.darwish.smartpower.data.AlertEvent
import com.darwish.smartpower.data.EnergyReport
import com.darwish.smartpower.data.Me
import com.darwish.smartpower.data.Member
import com.darwish.smartpower.data.Prefs
import com.darwish.smartpower.data.ScanOutcome
import com.darwish.smartpower.data.Scene
import com.darwish.smartpower.data.Schedule
import com.darwish.smartpower.data.ServerAddress
import com.darwish.smartpower.data.ServerClient
import com.darwish.smartpower.data.ServerException
import com.darwish.smartpower.data.ServerSettings
import com.darwish.smartpower.data.ServerState
import com.darwish.smartpower.data.SetupResult
import com.darwish.smartpower.data.SetupRules
import com.darwish.smartpower.data.Strip
import com.darwish.smartpower.data.StripFinder
import com.darwish.smartpower.data.StripSetup
import com.darwish.smartpower.data.SwitchOutcome
import com.darwish.smartpower.notify.AlertNotifier
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException

/** Why the strips cannot be shown right now. */
enum class Problem { NOT_CONFIGURED, UNREACHABLE, BAD_TOKEN, SERVER }

/** Why creating an account or signing in did not work. */
enum class AccountError { NAME, LOGIN, PASSWORD, EXISTS, WRONG, CLOSED, TOO_MANY, UNREACHABLE, OTHER }

data class UiState(
    val addressText: String = "",
    val token: String = "",
    val server: ServerState? = null,
    val loaded: Boolean = false,
    val problem: Problem? = null,
    /** "stripId/outlet" keys with a command still running. */
    val pending: Set<String> = emptySet(),
    val report: EnergyReport? = null,
    val reportRange: String = "day",
    val alerts: List<AlertEvent> = emptyList(),
    val members: List<Member> = emptyList(),
    /** A family member's token right after it was made, to share; shown once. */
    val invite: Invite? = null,
) {
    val me: Me get() = server?.me ?: Me()
    val strips: List<Strip> get() = server?.strips.orEmpty()
}

/** What to send a new family member: their name and token. */
data class Invite(val name: String, val token: String)

/** A one-off message for the snackbar. */
data class Note(@param:StringRes val text: Int, val arg: String? = null)

/** The server asked for a strip's PIN; [retry] repeats the action with it. */
class PinRequest(val stripId: String, val stripName: String, val wrong: Boolean, val retry: suspend (String) -> Unit)

class AppViewModel(app: Application) : AndroidViewModel(app) {
    val prefs = Prefs(app)
    private val setup = StripSetup(app)

    private val _state = MutableStateFlow(UiState(addressText = prefs.addressText, token = prefs.token))
    val state: StateFlow<UiState> = _state.asStateFlow()

    private val _pinRequest = MutableStateFlow<PinRequest?>(null)
    val pinRequest: StateFlow<PinRequest?> = _pinRequest.asStateFlow()

    private val notes = Channel<Note>(Channel.BUFFERED)
    val messages = notes.receiveAsFlow()

    /** PINs typed in this session, so a locked strip asks only once. */
    private val sessionPins = mutableMapOf<String, String>()

    private fun client(): ServerClient? = prefs.address?.let { ServerClient(it, prefs.token) }

    private fun pinFor(stripId: String): String? = sessionPins[stripId] ?: prefs.savedPin(stripId)

    // ---- polling

    /** Runs while the app is visible. */
    suspend fun pollForever() {
        while (true) {
            refresh()
            delay(2_000)
        }
    }

    suspend fun refresh() {
        val client = client()
        if (client == null) {
            _state.update { it.copy(problem = Problem.NOT_CONFIGURED, server = null, loaded = true) }
            return
        }
        try {
            val server = client.state()
            _state.update { s ->
                s.copy(server = server.copy(strips = server.strips.map { keepPending(it, s) }), loaded = true, problem = null)
            }
            if (prefs.notifications && server.lastEventId > prefs.lastNotifiedEvent) {
                viewModelScope.launch { runCatching { AlertNotifier.check(getApplication<Application>()) } }
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

    private fun replaceStrip(strip: Strip) = _state.update { s ->
        s.copy(server = s.server?.copy(strips = s.strips.map { if (it.id == strip.id) strip else it }))
    }

    private fun stripName(id: String): String =
        _state.value.strips.firstOrNull { it.id == id }?.name ?: id.takeLast(6)

    // ---- actions that may need a strip's PIN

    /**
     * Runs [block] with the PIN we know for [stripId]. When the server says the strip is locked,
     * asks for the PIN (fingerprint first if one is saved) and runs it again.
     */
    private fun guarded(stripId: String, block: suspend (ServerClient, String?) -> Unit) {
        val client = client() ?: return
        viewModelScope.launch {
            try {
                block(client, pinFor(stripId))
            } catch (e: ServerException) {
                if (e.pinNeeded && e.code == 403) askPin(stripId, wrong = pinFor(stripId) != null) { pin -> block(client, pin) }
                else notes.send(noteOf(e))
            } catch (e: Exception) {
                notes.send(noteOf(e))
            }
        }
    }

    private fun askPin(stripId: String, wrong: Boolean, action: suspend (String) -> Unit) {
        sessionPins.remove(stripId)
        _pinRequest.value = PinRequest(stripId, stripName(stripId), wrong) { pin ->
            try {
                action(pin)
                sessionPins[stripId] = pin
                _pinRequest.value = null
            } catch (e: ServerException) {
                if (e.pinNeeded && e.code == 403) {
                    _pinRequest.value = PinRequest(stripId, stripName(stripId), true, _pinRequest.value!!.retry)
                } else {
                    _pinRequest.value = null
                    notes.send(noteOf(e))
                }
            }
        }
    }

    fun dismissPin() { _pinRequest.value = null }

    /** View-only members can look but not switch; say so instead of trying. */
    private fun viewOnly(): Boolean {
        if (_state.value.me.canControl) return false
        viewModelScope.launch { notes.send(Note(R.string.view_only_note)) }
        return true
    }

    fun switch(stripId: String, outlet: Int, on: Boolean) {
        if (viewOnly()) return
        val k = key(stripId, outlet)
        if (k in _state.value.pending) return                    // one command per outlet at a time
        _state.update { s ->
            s.copy(
                pending = s.pending + k,
                server = s.server?.copy(strips = s.strips.map { strip ->
                    if (strip.id != stripId) strip
                    else strip.copy(outlets = strip.outlets.map { if (outlet == 0 || it.index == outlet) it.copy(on = on) else it })
                }),
            )
        }
        guarded(stripId) { client, pin ->
            try {
                val (strip, outcome) = client.switch(stripId, listOf(outlet), on, pin)
                replaceStrip(strip)
                when (outcome) {
                    SwitchOutcome.QUEUED -> notes.send(Note(R.string.note_queued))
                    SwitchOutcome.UNCONFIRMED -> notes.send(Note(R.string.note_not_confirmed))
                    SwitchOutcome.CONFIRMED -> Unit
                }
            } finally {
                _state.update { it.copy(pending = it.pending - k) }
                refresh()
            }
        }
    }

    fun rename(stripId: String, outlet: Int, name: String) = guarded(stripId) { client, _ ->
        replaceStrip(client.rename(stripId, outlet, name.trim()))
    }

    fun setTimer(stripId: String, outlets: List<Int>, minutes: Int, turnOn: Boolean) = guarded(stripId) { client, pin ->
        replaceStrip(client.timer(stripId, outlets, minutes, turnOn, pin))
        notes.send(Note(if (minutes == 0) R.string.note_timer_cancelled else R.string.note_timer_set))
    }

    fun setRoom(stripId: String, room: String) = guarded(stripId) { client, _ ->
        replaceStrip(client.setMeta(stripId, 0, room = room.trim()))
    }

    fun setOutletLook(stripId: String, outlet: Int, icon: String? = null, favorite: Boolean? = null) = guarded(stripId) { client, _ ->
        replaceStrip(client.setMeta(stripId, outlet, icon = icon, favorite = favorite))
    }

    /** Sets or changes the PIN ([oldPin] when it is already locked); an empty [pin] removes it. */
    fun setLock(stripId: String, pin: String, oldPin: String?, useFingerprint: Boolean) {
        val client = client() ?: return
        viewModelScope.launch {
            try {
                replaceStrip(client.setLock(stripId, pin, oldPin))
                sessionPins.remove(stripId)
                prefs.savePin(stripId, if (pin.isNotEmpty() && useFingerprint) pin else null)
                if (pin.isNotEmpty()) sessionPins[stripId] = pin
                notes.send(Note(if (pin.isEmpty()) R.string.note_unlocked else R.string.note_locked))
            } catch (e: Exception) {
                notes.send(if (e is ServerException && e.code == 403) Note(R.string.pin_wrong) else noteOf(e))
            }
        }
    }

    fun rememberPinForFingerprint(stripId: String, pin: String) = prefs.savePin(stripId, pin)

    // ---- schedules, cycles, scenes

    fun saveSchedule(schedule: Schedule) = guarded(schedule.stripId) { client, pin ->
        val list = client.saveSchedule(schedule, pin)
        _state.update { s -> s.copy(server = s.server?.copy(schedules = list)) }
        notes.send(Note(R.string.note_saved))
    }

    fun deleteSchedule(schedule: Schedule) = guarded(schedule.stripId) { client, pin ->
        val list = client.deleteSchedule(schedule.id, pin)
        _state.update { s -> s.copy(server = s.server?.copy(schedules = list)) }
    }

    fun saveScene(scene: Scene) = act { client ->
        val list = client.saveScene(scene)
        _state.update { s -> s.copy(server = s.server?.copy(scenes = list)) }
        notes.send(Note(R.string.note_saved))
    }

    fun deleteScene(scene: Scene) = act { client ->
        val list = client.deleteScene(scene.id)
        _state.update { s -> s.copy(server = s.server?.copy(scenes = list)) }
    }

    fun runScene(scene: Scene) {
        val firstLocked = scene.actions.map { it.stripId }.firstOrNull { id -> _state.value.strips.any { it.id == id && it.locked } }
        guarded(firstLocked ?: scene.actions.firstOrNull()?.stripId ?: return) { client, pin ->
            client.runScene(scene.id, pin)
            notes.send(Note(R.string.note_scene_ran, scene.name))
            refresh()
        }
    }

    // ---- family sharing (owner only)

    fun loadMembers() = act { client ->
        val list = client.members()
        _state.update { it.copy(members = list) }
    }

    fun addMember(name: String, role: String, strips: List<String>) = act { client ->
        val (list, token) = client.addMember(name.trim(), role, strips)
        _state.update { it.copy(members = list, invite = Invite(name.trim(), token)) }
    }

    fun changeMemberRole(member: Member, role: String) = act { client ->
        val (list, _) = client.updateMember(member.id, role = role)
        _state.update { it.copy(members = list) }
    }

    fun renewMemberToken(member: Member) = act { client ->
        val (list, token) = client.updateMember(member.id, newToken = true)
        _state.update { it.copy(members = list, invite = token?.let { t -> Invite(member.name, t) }) }
    }

    fun removeMember(member: Member) = act { client ->
        val list = client.deleteMember(member.id)
        _state.update { it.copy(members = list) }
        notes.send(Note(R.string.member_removed, member.name))
    }

    fun dismissInvite() = _state.update { it.copy(invite = null) }

    // ---- customer accounts

    suspend fun signUp(name: String, login: String, password: String): AccountError? =
        accountCall { it.signUp(name.trim(), login.trim(), password) }

    suspend fun signIn(login: String, password: String): AccountError? =
        accountCall { it.signIn(login.trim(), password) }

    /** Runs a sign-up or sign-in; on success the returned token becomes this phone's sign-in. */
    private suspend fun accountCall(block: suspend (ServerClient) -> String): AccountError? {
        val address = prefs.address ?: return AccountError.UNREACHABLE
        return try {
            useToken(block(ServerClient(address, "")))
            null
        } catch (e: ServerException) {
            when {
                e.code == 409 -> AccountError.EXISTS
                e.code == 401 -> AccountError.WRONG
                e.code == 403 -> AccountError.CLOSED
                e.code == 429 -> AccountError.TOO_MANY
                e.field == "name" -> AccountError.NAME
                e.field == "login" -> AccountError.LOGIN
                e.field == "password" -> AccountError.PASSWORD
                else -> AccountError.OTHER
            }
        } catch (e: IOException) {
            AccountError.UNREACHABLE
        }
    }

    private suspend fun useToken(token: String) {
        prefs.token = token
        _state.update { it.copy(token = token, server = null, loaded = false, problem = null) }
        refresh()
    }

    fun signOut() {
        val client = client()
        viewModelScope.launch {
            client?.let { runCatching { it.signOut() } }
            useToken("")
        }
    }

    fun deleteAccount() = act { client ->
        client.deleteAccount()
        useToken("")
        notes.send(Note(R.string.account_deleted))
    }

    // ---- approving and removing strips (owner)

    fun approveStrip(id: String) = act { client ->
        client.approveStrip(id)
        refresh()
        notes.send(Note(R.string.strip_approved, id.takeLast(6)))
    }

    fun removeStrip(id: String, block: Boolean) = act { client ->
        client.removeStrip(id, block)
        refresh()
        notes.send(Note(if (block) R.string.strip_blocked else R.string.strip_removed, id.takeLast(6)))
    }

    // ---- energy, alerts, server settings

    fun loadReport(range: String) {
        _state.update { it.copy(reportRange = range) }
        act { client ->
            val report = client.history(range)
            _state.update { if (it.reportRange == range) it.copy(report = report) else it }
        }
    }

    /** Saves the energy log of [range] as an Excel file where the person chose to keep it ([uri]). */
    fun exportHistory(range: String, uri: android.net.Uri) = viewModelScope.launch {
        val client = client() ?: return@launch
        val app = getApplication<Application>()
        val lang = if (app.resources.configuration.locales[0].language == "ar") "ar" else "en"
        try {
            val data = client.historyXlsx(range, lang)
            withContext(Dispatchers.IO) { app.contentResolver.openOutputStream(uri)?.use { it.write(data) } }
            notes.send(Note(R.string.export_done))
        } catch (e: Exception) {
            notes.send(noteOf(e))
        }
    }

    fun loadAlerts() = act { client ->
        val (events, _) = client.events(0)
        _state.update { it.copy(alerts = events) }
    }

    fun saveServerSettings(settings: ServerSettings) = act { client ->
        val saved = client.updateSettings(settings)
        _state.update { s -> s.copy(server = s.server?.copy(settings = saved)) }
        notes.send(Note(R.string.note_saved))
    }

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

    // ---- connection settings

    /** Saves the address and token; returns false when the address cannot be used. */
    fun saveSettings(addressText: String, token: String): Boolean {
        val address = ServerAddress.parse(addressText) ?: return false
        prefs.addressText = address.display()
        prefs.token = token.trim()
        _state.update {
            it.copy(addressText = prefs.addressText, token = prefs.token, server = null, loaded = false, problem = null)
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

    fun setNotifications(on: Boolean) {
        prefs.notifications = on
        AlertNotifier.schedule(getApplication<Application>(), on)
    }

    // ---- strip setup

    suspend fun provision(serverIp: String, ssid: String, password: String): SetupResult =
        setup.provision(serverIp, ssid, password)

    val stripFinder = StripFinder(app)

    /** The strip being put on Wi-Fi again (its id); null when a new strip is being set up. */
    private val _reconnect = MutableStateFlow<String?>(null)
    val reconnect: StateFlow<String?> = _reconnect.asStateFlow()

    fun startReconnect(stripId: String) { _reconnect.value = stripId }

    fun endReconnect() { _reconnect.value = null }

    suspend fun scanForStrips(): ScanOutcome = stripFinder.scan()

    /** Joins the strip's own Wi-Fi by itself, sends the setup, then gives the phone back its Wi-Fi. */
    suspend fun provisionFound(apSsid: String, serverIp: String, ssid: String, password: String): SetupResult {
        SetupRules.check(serverIp, ssid, password)?.let { return SetupResult.Invalid(it) }
        // tell the server this strip is ours, so it is approved as soon as it connects
        client()?.let { runCatching { it.expectStrip(SetupRules.apCode(apSsid)) } }
        // if the person missed Android's "connect?" prompt, ask once more by itself
        val joined = stripFinder.join(apSsid) ?: stripFinder.join(apSsid) ?: return SetupResult.JoinFailed
        val result = try {
            setup.provision(serverIp, ssid, password, joined.network)
        } finally {
            joined.release()
        }
        if (result is SetupResult.Done) announceAgain(SetupRules.apCode(apSsid))
        return result
    }

    /**
     * Tells the server about the strip once more when the phone is back on its own network: the first
     * notice may not have got through (no internet just then), and from the strip's home network the
     * server can hand over a strip that already connected.
     */
    private fun announceAgain(code: String) = viewModelScope.launch {
        repeat(8) {
            delay(5_000)
            val sent = client()?.let { c -> runCatching { c.expectStrip(code) }.isSuccess } ?: return@launch
            if (sent) return@launch
        }
    }

    /** Best guess for the IP the strip should dial: what the server reports, else the saved host. */
    fun suggestedServerIp(): String =
        _state.value.server?.serverIp ?: prefs.address?.host?.takeIf { it.all { c -> c.isDigit() || c == '.' } }
        ?: ""

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
            e is ServerException && e.code == 429 -> Note(R.string.pin_too_many)
            e is ServerException && e.code == 403 && e.message == "view only" -> Note(R.string.view_only_note)
            e is ServerException && e.code == 403 -> Note(R.string.not_allowed_note)
            e is ServerException -> Note(R.string.note_server_error, e.message ?: "HTTP ${e.code}")
            e is IOException -> Note(R.string.problem_unreachable)
            else -> Note(R.string.note_server_error, e.message ?: e.javaClass.simpleName)
        }
    }
}
