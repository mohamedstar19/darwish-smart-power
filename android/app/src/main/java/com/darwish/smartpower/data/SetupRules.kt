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

    fun serverCommand(serverIp: String) = "up:ip:${serverIp.trim()}"
    fun wifiCommand(ssid: String, password: String) = "up:connect:$ssid:$password"
    const val SERVER_OK = "ip_ok"
    const val WIFI_OK = "connect_ok"
}
