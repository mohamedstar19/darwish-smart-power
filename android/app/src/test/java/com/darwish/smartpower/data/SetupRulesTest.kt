package com.darwish.smartpower.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SetupRulesTest {

    @Test
    fun ipv4Check() {
        assertTrue(SetupRules.isIpv4("192.168.1.20"))
        assertTrue(SetupRules.isIpv4("0.0.0.0"))
        listOf("192.168.1", "192.168.1.256", "a.b.c.d", "1.2.3.4.5", "1..2.3", "-1.2.3.4", "1.2.3.0004", "power.example.com")
            .forEach { assertFalse("should reject '$it'", SetupRules.isIpv4(it)) }
    }

    @Test
    fun validValuesPass() {
        assertNull(SetupRules.check("192.168.1.20", "Home WiFi", "p@ss w0rd!"))
        assertNull(SetupRules.check(" 192.168.1.20 ", "بيتي", "كلمة السر"))
    }

    @Test
    fun problemsAreReported() {
        assertEquals(SetupProblem.BAD_SERVER_IP, SetupRules.check("my-pc", "wifi", "pw"))
        assertEquals(SetupProblem.EMPTY_SSID, SetupRules.check("10.0.0.2", "", "pw"))
        assertEquals(SetupProblem.BAD_SSID, SetupRules.check("10.0.0.2", "a:b", "pw"))
        assertEquals(SetupProblem.EMPTY_PASSWORD, SetupRules.check("10.0.0.2", "wifi", ""))
        assertEquals(SetupProblem.BAD_PASSWORD, SetupRules.check("10.0.0.2", "wifi", "line\nbreak"))
    }

    @Test
    fun commandsMatchTheStripProtocol() {
        assertEquals("up:ip:10.0.0.2", SetupRules.serverCommand(" 10.0.0.2 "))
        assertEquals("up:connect:HomeWiFi:secret", SetupRules.wifiCommand("HomeWiFi", "secret"))
    }
}
