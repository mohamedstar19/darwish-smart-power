package com.darwish.smartpower.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket

/** Result of sending the setup lines to a strip in setup mode. */
sealed interface SetupResult {
    data object Done : SetupResult
    data class Invalid(val problem: SetupProblem) : SetupResult
    data object NoWifi : SetupResult
    data object NotReachable : SetupResult
    data class Refused(val answer: String) : SetupResult
}

/**
 * Sends the server IP and the home Wi-Fi to a strip whose setup network (TONLY_TAP_...)
 * this phone is joined to. That network has no internet, so Android may route normal
 * traffic over mobile data; the sockets are bound to the Wi-Fi network explicitly.
 */
class StripSetup(private val context: Context) {

    suspend fun provision(serverIp: String, ssid: String, password: String): SetupResult =
        withContext(Dispatchers.IO) {
            SetupRules.check(serverIp, ssid, password)?.let { return@withContext SetupResult.Invalid(it) }
            val wifi = wifiNetwork() ?: return@withContext SetupResult.NoWifi
            try {
                val first = exchange(wifi, SetupRules.serverCommand(serverIp))
                if (!first.contains(SetupRules.SERVER_OK)) return@withContext SetupResult.Refused(first)
                val second = exchange(wifi, SetupRules.wifiCommand(ssid, password))
                if (!second.contains(SetupRules.WIFI_OK)) return@withContext SetupResult.Refused(second)
                SetupResult.Done
            } catch (e: IOException) {
                SetupResult.NotReachable
            }
        }

    @Suppress("DEPRECATION") // allNetworks is the simplest way to find a Wi-Fi without internet
    private fun wifiNetwork(): Network? {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return null
        return cm.allNetworks.firstOrNull {
            cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        }
    }

    /** One line out, one line back, on a fresh connection (the strip expects that). */
    private fun exchange(network: Network, line: String): String {
        return network.socketFactory.createSocket().use { socket: Socket ->
            socket.connect(InetSocketAddress(SetupRules.SETUP_HOST, SetupRules.SETUP_PORT), 6_000)
            socket.soTimeout = 6_000
            socket.getOutputStream().apply { write("$line\r\n".toByteArray(Charsets.UTF_8)); flush() }
            val input = socket.getInputStream()
            val buffer = StringBuilder()
            try {
                while (true) {
                    val b = input.read()
                    if (b < 0 || b == '\n'.code) break
                    if (b != 0) buffer.append(b.toChar())
                }
            } catch (e: java.net.SocketTimeoutException) {
                if (buffer.isEmpty()) throw e
            }
            buffer.toString().trim()
        }
    }
}
