package com.darwish.smartpower.data

import android.Manifest
import android.annotation.SuppressLint
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.location.LocationManager
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.ScanResult
import android.net.wifi.WifiConfiguration
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build
import androidx.core.content.ContextCompat
import androidx.core.location.LocationManagerCompat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/** A Wi-Fi network seen in a scan. */
data class NearbyWifi(val ssid: String, val level: Int, val frequency: Int)

sealed interface ScanOutcome {
    data object NeedPermission : ScanOutcome
    data object WifiOff : ScanOutcome
    data object LocationOff : ScanOutcome
    /** [strips]: strips in setup mode; [homes]: other 2.4 GHz networks, strongest first. */
    data class Found(val strips: List<NearbyWifi>, val homes: List<NearbyWifi>) : ScanOutcome
}

/** The strip's Wi-Fi while the app is joined to it; [release] hands the phone back to its usual Wi-Fi. */
class JoinedStrip(val network: Network, val release: () -> Unit)

/**
 * Finds strips in setup mode (Wi-Fi TONLY_TAP_<code>) and joins one with its known password
 * (LGU_<code>), so nobody has to open the Wi-Fi settings or type that password.
 */
class StripFinder(context: Context) {
    private val app = context.applicationContext
    private val wifi = app.getSystemService(WifiManager::class.java)
    private val connectivity = app.getSystemService(ConnectivityManager::class.java)

    /** Android 13+ has a Wi-Fi-only permission; older versions need location to list networks. */
    val permissions: Array<String> =
        if (Build.VERSION.SDK_INT >= 33) arrayOf(Manifest.permission.NEARBY_WIFI_DEVICES)
        else arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)

    fun hasPermission(): Boolean = permissions.all {
        ContextCompat.checkSelfPermission(app, it) == PackageManager.PERMISSION_GRANTED
    }

    @SuppressLint("MissingPermission") // checked by hasPermission() first
    suspend fun scan(): ScanOutcome {
        if (!hasPermission()) return ScanOutcome.NeedPermission
        if (wifi == null || !wifi.isWifiEnabled) return ScanOutcome.WifiOff
        if (Build.VERSION.SDK_INT < 33) {
            val location = app.getSystemService(LocationManager::class.java)
            if (location == null || !LocationManagerCompat.isLocationEnabled(location)) return ScanOutcome.LocationOff
        }
        val fresh = CompletableDeferred<Unit>()
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                fresh.complete(Unit)
            }
        }
        ContextCompat.registerReceiver(
            app, receiver, IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION), ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        try {
            @Suppress("DEPRECATION")            // still the only way to ask for a scan; Android may throttle it
            val started = wifi.startScan()
            if (started) withTimeoutOrNull(10_000) { fresh.await() }
        } finally {
            runCatching { app.unregisterReceiver(receiver) }
        }
        val seen = wifi.scanResults.mapNotNull { it.toNearby() }
            .groupBy { it.ssid }.map { (_, same) -> same.maxBy { it.level } }
            .sortedByDescending { it.level }
        return ScanOutcome.Found(
            strips = seen.filter { SetupRules.isStripAp(it.ssid) },
            homes = seen.filter { !SetupRules.isStripAp(it.ssid) && SetupRules.is24GHz(it.frequency) },
        )
    }

    /** Joins the strip's Wi-Fi; Android 10+ shows its own "connect to this device?" prompt first. */
    suspend fun join(apSsid: String): JoinedStrip? {
        val password = SetupRules.apPassword(apSsid) ?: return null
        return if (Build.VERSION.SDK_INT >= 29) joinWithSpecifier(apSsid, password) else joinLegacy(apSsid, password)
    }

    private suspend fun joinWithSpecifier(ssid: String, password: String): JoinedStrip? {
        val cm = connectivity ?: return null
        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .setNetworkSpecifier(WifiNetworkSpecifier.Builder().setSsid(ssid).setWpa2Passphrase(password).build())
            .build()
        return suspendCancellableCoroutine { cont ->
            val callback = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    if (cont.isActive) cont.resume(JoinedStrip(network) { runCatching { cm.unregisterNetworkCallback(this) } })
                }

                override fun onUnavailable() {
                    if (cont.isActive) cont.resume(null)
                }
            }
            cm.requestNetwork(request, callback, 60_000)
            cont.invokeOnCancellation { runCatching { cm.unregisterNetworkCallback(callback) } }
        }
    }

    /** Android 8 and 9: add the network, switch to it, and remove it again afterwards. */
    @Suppress("DEPRECATION")
    @SuppressLint("MissingPermission")
    private suspend fun joinLegacy(ssid: String, password: String): JoinedStrip? = withContext(Dispatchers.IO) {
        val wm = wifi ?: return@withContext null
        val cm = connectivity ?: return@withContext null
        val config = WifiConfiguration().apply {
            SSID = "\"$ssid\""
            preSharedKey = "\"$password\""
        }
        val id = wm.addNetwork(config)
        if (id == -1) return@withContext null
        val release = {
            wm.removeNetwork(id)
            wm.reconnect()
            Unit
        }
        wm.disconnect()
        wm.enableNetwork(id, true)
        wm.reconnect()
        repeat(40) {
            delay(500)
            val joined = wm.connectionInfo?.ssid == "\"$ssid\""
            val network = cm.allNetworks.firstOrNull {
                cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
            }
            if (joined && network != null) return@withContext JoinedStrip(network, release)
        }
        release()
        null
    }

    @Suppress("DEPRECATION")                    // SSID is replaced by wifiSsid only on Android 13+
    private fun ScanResult.toNearby(): NearbyWifi? {
        val name = SSID?.trim('"').orEmpty()
        return if (name.isEmpty()) null else NearbyWifi(name, level, frequency)
    }
}
