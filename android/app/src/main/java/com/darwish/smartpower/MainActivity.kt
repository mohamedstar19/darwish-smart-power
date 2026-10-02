package com.darwish.smartpower

import android.os.Bundle
import android.os.SystemClock
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.darwish.smartpower.data.Prefs
import com.darwish.smartpower.notify.AlertNotifier
import com.darwish.smartpower.security.Biometric
import com.darwish.smartpower.ui.DarwishApp
import com.darwish.smartpower.ui.DarwishTheme

// AppCompatActivity (a FragmentActivity) so the in-app language switch works on Android 8-12 and
// the fingerprint prompt can be shown.
class MainActivity : AppCompatActivity() {
    private lateinit var prefs: Prefs
    private var locked by mutableStateOf(false)
    private var leftAt = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        locked = savedInstanceState?.getBoolean(KEY_LOCKED) ?: lockWanted()
        if (prefs.notifications) AlertNotifier.schedule(this, true)
        setContent {
            DarwishTheme { DarwishApp(locked = locked, onUnlocked = { locked = false }) }
        }
    }

    override fun onStart() {
        super.onStart()
        // lock again when the app comes back after more than a minute in the background
        if (leftAt > 0 && SystemClock.elapsedRealtime() - leftAt > RELOCK_AFTER_MS && lockWanted()) locked = true
    }

    override fun onStop() {
        super.onStop()
        if (!isChangingConfigurations) leftAt = SystemClock.elapsedRealtime()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(KEY_LOCKED, locked)
    }

    private fun lockWanted() = prefs.appLock && Biometric.available(this)

    private companion object {
        const val KEY_LOCKED = "locked"
        const val RELOCK_AFTER_MS = 60_000L
    }
}
