package com.darwish.smartpower.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.Inet4Address
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
                val (host, first) = firstExchange(wifi, SetupRules.serverCommand(serverIp))
                if (!first.contains(SetupRules.SERVER_OK)) return@withContext SetupResult.Refused(first)
                val second = exchange(wifi, host, SetupRules.wifiCommand(ssid, password))
                if (!second.contains(SetupRules.WIFI_OK)) return@withContext SetupResult.Refused(second)
                // leave setup mode now, so the strip joins the home Wi-Fi instead of staying in setup
                runCatching { send(wifi, host, SetupRules.REBOOT) }
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

    /** The gateway the strip's Wi-Fi gave this phone: that is the strip itself. */
    private fun gateway(network: Network): String? {
        val cm = context.getSystemService(ConnectivityManager::class.java) ?: return null
        val routes = cm.getLinkProperties(network)?.routes ?: return null
        return routes.firstOrNull { it.isDefaultRoute && it.gateway is Inet4Address }?.gateway?.hostAddress
    }

    /** Right after joining, the strip's network can need a moment before it answers; other batches of
     *  strips listen on another address, so every known one is tried. Returns the address that answered. */
    private suspend fun firstExchange(network: Network, line: String): Pair<String, String> {
        val hosts = SetupRules.setupHosts(gateway(network))
        var last: IOException? = null
        repeat(4) {
            for (host in hosts) {
                try {
                    return host to exchange(network, host, line)
                } catch (e: IOException) {
                    last = e
                }
            }
            delay(1_500)
        }
        throw last ?: IOException("no answer")
    }

    /** One line out, nothing back (the strip restarts right away). */
    private fun send(network: Network, host: String, line: String) {
        network.socketFactory.createSocket().use { socket: Socket ->
            socket.connect(InetSocketAddress(host, SetupRules.SETUP_PORT), 6_000)
            socket.getOutputStream().apply { write("$line\r\n".toByteArray(Charsets.UTF_8)); flush() }
        }
    }

    /** One line out, one line back, on a fresh connection (the strip expects that). */
    private fun exchange(network: Network, host: String, line: String): String {
        return network.socketFactory.createSocket().use { socket: Socket ->
            socket.connect(InetSocketAddress(host, SetupRules.SETUP_PORT), 6_000)
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
