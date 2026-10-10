package com.darwish.smartpower.data

/** Why the values for strip setup cannot be sent. */
enum class SetupProblem { BAD_SERVER_IP, EMPTY_SSID, BAD_SSID, EMPTY_PASSWORD, BAD_PASSWORD }

/** Rules for the two setup lines the strip accepts on its own Wi-Fi (192.168.1.1:30300). */
object SetupRules {
    const val SETUP_HOST = "192.168.1.1"
    const val SETUP_PORT = 30300

    /** The strip only dials an IPv4 address it stores; host names are not supported. */
    fun isIpv4(text: String): Boolean {
        val parts = text.split('.')
        return parts.size == 4 && parts.all { p ->
            p.length in 1..3 && p.all { it in '0'..'9' } && p.toInt() <= 255
        }
    }

    /** The setup protocol separates fields with ':' and ends commands with a line break. */
    private fun fitsProtocol(value: String) = value.none { it == ':' || it == '\r' || it == '\n' }

    fun check(serverIp: String, ssid: String, password: String): SetupProblem? = when {
        !isIpv4(serverIp.trim()) -> SetupProblem.BAD_SERVER_IP
        ssid.isEmpty() -> SetupProblem.EMPTY_SSID
        !fitsProtocol(ssid) -> SetupProblem.BAD_SSID
        password.isEmpty() -> SetupProblem.EMPTY_PASSWORD
        !fitsProtocol(password) -> SetupProblem.BAD_PASSWORD
        else -> null
    }

    /** A strip in setup mode opens a Wi-Fi called TONLY_TAP_<code> (some batches ONLY_TAP_<code>);
     *  its password is LGU_<code>. */
    val AP_PREFIXES = listOf("TONLY_TAP_", "ONLY_TAP_")
    private const val AP_PASSWORD_PREFIX = "LGU_"

    fun isStripAp(ssid: String): Boolean = AP_PREFIXES.any { ssid.startsWith(it) && ssid.length > it.length }

    fun apPassword(ssid: String): String? = if (isStripAp(ssid)) AP_PASSWORD_PREFIX + apCode(ssid) else null

    /** The code after the last "_" of the setup Wi-Fi name (the end of the strip's MAC), shown to tell strips apart. */
    fun apCode(ssid: String): String = ssid.substringAfterLast('_')

    /** Where the strip listens while in setup mode: its own address on its Wi-Fi, 192.168.1.1 on the strips
     *  we know, 192.168.4.1 on some other batches. [gateway] is what the phone was given on that Wi-Fi. */
    fun setupHosts(gateway: String?): List<String> =
        listOfNotNull(gateway?.takeIf { isIpv4(it) && it != "0.0.0.0" }, SETUP_HOST, "192.168.4.1").distinct()

    /** The strip only works on 2.4 GHz Wi-Fi. */
    fun is24GHz(frequencyMhz: Int): Boolean = frequencyMhz in 2400..2500

    fun serverCommand(serverIp: String) = "up:ip:${serverIp.trim()}"
    /** Leaves setup mode: the strip restarts and joins the home Wi-Fi (no answer is expected). */
    const val REBOOT = "up:reboot:0"
    fun wifiCommand(ssid: String, password: String) = "up:connect:$ssid:$password"
    const val SERVER_OK = "ip_ok"
    const val WIFI_OK = "connect_ok"
}
