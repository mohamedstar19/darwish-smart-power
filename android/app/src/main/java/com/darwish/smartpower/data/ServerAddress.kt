package com.darwish.smartpower.data

/** Where the Darwish Smart Power server lives, e.g. 192.168.1.20:8080 or https://power.example.com */
data class ServerAddress(val https: Boolean, val host: String, val port: Int) {

    fun url(path: String): String =
        (if (https) "https://" else "http://") + host + ":" + port + "/" + path.trimStart('/')

    /** Short form for display and for saving. */
    fun display(): String {
        val defaultPort = if (https) 443 else DEFAULT_PORT
        val base = if (https) "https://$host" else host
        return if (port == defaultPort) base else "$base:$port"
    }

    companion object {
        const val DEFAULT_PORT = 8080

        /**
         * Accepts what people actually type: "192.168.1.20", "192.168.1.20:7896",
         * "http://1.2.3.4:8080/", "https://power.example.com", "[fd00::5]:8080".
         * Returns null when it cannot be an address.
         */
        fun parse(input: String): ServerAddress? {
            var s = input.trim()
            if (s.isEmpty()) return null
            var https = false
            when {
                s.startsWith("https://", ignoreCase = true) -> { https = true; s = s.substring(8) }
                s.startsWith("http://", ignoreCase = true) -> s = s.substring(7)
            }
            s = s.substringBefore('/').substringBefore('?').substringBefore('#')
            if (s.isEmpty()) return null

            val host: String
            val portText: String?
            if (s.startsWith("[")) {                      // IPv6 literal
                val end = s.indexOf(']')
                if (end < 0) return null
                host = s.substring(0, end + 1)
                val rest = s.substring(end + 1)
                portText = when {
                    rest.isEmpty() -> null
                    rest.startsWith(":") -> rest.substring(1)
                    else -> return null
                }
            } else {
                val colons = s.count { it == ':' }
                if (colons > 1) return null              // bare IPv6 needs [brackets]
                host = s.substringBefore(':')
                portText = if (colons == 1) s.substringAfter(':') else null
            }
            if (host.isEmpty() || host.any { it.isWhitespace() || it == '@' }) return null
            val port = if (portText == null) {
                if (https) 443 else DEFAULT_PORT
            } else {
                portText.toIntOrNull()?.takeIf { it in 1..65535 } ?: return null
            }
            return ServerAddress(https, host, port)
        }
    }
}
