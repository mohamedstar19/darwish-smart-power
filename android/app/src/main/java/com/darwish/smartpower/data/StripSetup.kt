package com.darwish.smartpower.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
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
    /** Could not join the strip's own Wi-Fi (automatic setup). */
    data object JoinFailed : SetupResult
}

/**
 * Sends the server IP and the home Wi-Fi to a strip whose setup network (TONLY_TAP_...)
 * this phone is joined to. That network has no internet, so Android may route normal
 * traffic over mobile data; the sockets are bound to the Wi-Fi network explicitly.
 */
class StripSetup(private val context: Context) {

    /** [network]: the strip's Wi-Fi when the app joined it itself, else the Wi-Fi the phone is on. */
    suspend fun provision(serverIp: String, ssid: String, password: String, network: Network? = null): SetupResult =
        withContext(Dispatchers.IO) {
            SetupRules.check(serverIp, ssid, password)?.let { return@withContext SetupResult.Invalid(it) }
            val wifi = network ?: wifiNetwork() ?: return@withContext SetupResult.NoWifi
            try {
                val first = exchangeWithRetry(wifi, SetupRules.serverCommand(serverIp))
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

    /** Right after joining, the strip's network can need a moment before it answers. */
    private suspend fun exchangeWithRetry(network: Network, line: String): String {
        repeat(3) {
            try {
                return exchange(network, line)
            } catch (e: IOException) {
                delay(1_500)
            }
        }
        return exchange(network, line)
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
