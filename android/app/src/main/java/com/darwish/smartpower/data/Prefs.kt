package com.darwish.smartpower.data

import android.content.Context
import androidx.core.content.edit

/** Server address and token, stored on the phone. */
class Prefs(context: Context) {
    private val prefs = context.getSharedPreferences("darwish_smart_power", Context.MODE_PRIVATE)

    var addressText: String
        get() = prefs.getString(KEY_ADDRESS, DEFAULT_ADDRESS).orEmpty()
        set(value) = prefs.edit { putString(KEY_ADDRESS, value) }

    var token: String
        get() = prefs.getString(KEY_TOKEN, "").orEmpty()
        set(value) = prefs.edit { putString(KEY_TOKEN, value) }

    val address: ServerAddress? get() = ServerAddress.parse(addressText)

    companion object {
        /** The home server this app was made for; can be changed in Settings. */
        const val DEFAULT_SERVER_IP = "192.168.1.116"
        const val DEFAULT_ADDRESS = "192.168.1.116:8095"

        private const val KEY_ADDRESS = "address"
        private const val KEY_TOKEN = "token"
    }
}
