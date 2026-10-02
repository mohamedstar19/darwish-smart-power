package com.darwish.smartpower.data

import android.content.Context
import androidx.core.content.edit

/** Server address, token and app options, stored on the phone. */
class Prefs(context: Context) {
    private val prefs = context.getSharedPreferences("darwish_smart_power", Context.MODE_PRIVATE)

    var addressText: String
        get() = prefs.getString(KEY_ADDRESS, DEFAULT_ADDRESS).orEmpty()
        set(value) = prefs.edit { putString(KEY_ADDRESS, value) }

    var token: String
        get() = prefs.getString(KEY_TOKEN, "").orEmpty()
        set(value) = prefs.edit { putString(KEY_TOKEN, value) }

    val address: ServerAddress? get() = ServerAddress.parse(addressText)

    /** Ask for the fingerprint (or screen lock) every time the app opens. */
    var appLock: Boolean
        get() = prefs.getBoolean(KEY_APP_LOCK, false)
        set(value) = prefs.edit { putBoolean(KEY_APP_LOCK, value) }

    var notifications: Boolean
        get() = prefs.getBoolean(KEY_NOTIFY, true)
        set(value) = prefs.edit { putBoolean(KEY_NOTIFY, value) }

    /** Newest alert already shown; -1 = never checked (then old alerts are skipped). */
    var lastNotifiedEvent: Long
        get() = prefs.getLong(KEY_LAST_EVENT, -1L)
        set(value) = prefs.edit { putLong(KEY_LAST_EVENT, value) }

    /** A strip PIN the user chose to unlock with the fingerprint instead of typing it. */
    fun savedPin(stripId: String): String? = prefs.getString(KEY_PIN + stripId, null)

    fun savePin(stripId: String, pin: String?) = prefs.edit {
        if (pin == null) remove(KEY_PIN + stripId) else putString(KEY_PIN + stripId, pin)
    }

    companion object {
        /** The home server this app was made for; can be changed in Settings. */
        const val DEFAULT_SERVER_IP = "41.38.141.215"   // the server's fixed internet IP: strips anywhere reach it

        /** Works at home and outside (Cloudflare Tunnel), whatever port the server uses. */
        const val DEFAULT_ADDRESS = "https://power.darwish-tech.com"

        private const val KEY_ADDRESS = "address"
        private const val KEY_TOKEN = "token"
        private const val KEY_APP_LOCK = "app_lock"
        private const val KEY_NOTIFY = "notifications"
        private const val KEY_LAST_EVENT = "last_event"
        private const val KEY_PIN = "pin_"
    }
}
