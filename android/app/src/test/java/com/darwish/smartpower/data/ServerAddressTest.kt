package com.darwish.smartpower.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerAddressTest {

    @Test
    fun plainIpUsesDefaultPort() {
        val a = ServerAddress.parse(" 192.168.1.20 ")!!
        assertEquals(ServerAddress(false, "192.168.1.20", 8080), a)
        assertEquals("http://192.168.1.20:8080/api/state", a.url("api/state"))
        assertEquals("192.168.1.20", a.display())
    }

    @Test
    fun portSchemeAndPathAreUnderstood() {
        assertEquals(ServerAddress(false, "203.0.113.5", 7896), ServerAddress.parse("203.0.113.5:7896"))
        assertEquals(ServerAddress(false, "10.0.0.2", 8080), ServerAddress.parse("http://10.0.0.2:8080/?token=x"))
        val https = ServerAddress.parse("HTTPS://power.example.com/")!!
        assertEquals(ServerAddress(true, "power.example.com", 443), https)
        assertEquals("https://power.example.com", https.display())
        assertEquals("https://power.example.com:443/api/health", https.url("/api/health"))
        assertEquals("203.0.113.5:7896", ServerAddress.parse("203.0.113.5:7896")!!.display())
    }

    @Test
    fun ipv6NeedsBrackets() {
        assertEquals(ServerAddress(false, "[fd00::5]", 9000), ServerAddress.parse("[fd00::5]:9000"))
        assertEquals(ServerAddress(false, "[fd00::5]", 8080), ServerAddress.parse("[fd00::5]"))
        assertNull(ServerAddress.parse("fd00::5"))
    }

    @Test
    fun rejectsNonsense() {
        listOf("", "   ", "http://", "1.2.3.4:", "1.2.3.4:0", "1.2.3.4:70000", "1.2.3.4:abc",
            "my server", "user@host", "[fd00::5]x").forEach {
            assertNull("should reject '$it'", ServerAddress.parse(it))
        }
    }
}
