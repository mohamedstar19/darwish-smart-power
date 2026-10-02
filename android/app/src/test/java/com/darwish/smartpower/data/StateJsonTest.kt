package com.darwish.smartpower.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class StateJsonTest {

    // Shaped exactly like smartpower.py's /api/state reply.
    private val sample = """
        {"strips": [{"id": "A1B2C3D4E5F6", "name": null, "model": "LGU+-TAP-HW002", "fw": "0.1.54-1.0.66",
          "address": "10.0.0.7", "online": true, "last_seen": 1790000000, "watts": 61.8, "kwh": 23.0,
          "volts": 229.8, "amps": 0.27, "rssi": -58, "timer": null,
          "outlets": [
            {"index": 2, "name": "Kettle", "on": false, "watts": 0.0, "kwh": 4.6, "temp_c": 33,
             "timer": {"on": true, "at": 1790003600}},
            {"index": 1, "name": null, "on": true, "watts": 45.2, "kwh": 2.3, "temp_c": 32, "timer": null},
            {"index": 3, "name": null, "on": true, "watts": 16.6, "kwh": 6.9, "temp_c": null, "timer": null},
            {"index": 4, "name": "", "on": false, "watts": 0, "kwh": 9.2, "temp_c": 35, "timer": null}
          ]}],
         "server": {"ip": "192.168.1.20", "strip_port": 10086, "version": "1.0.0"}}
    """.trimIndent()

    @Test
    fun parsesStateFromServer() {
        val state = StateJson.parseState(sample)
        assertEquals("192.168.1.20", state.serverIp)
        assertEquals("1.0.0", state.version)
        val strip = state.strips.single()
        assertEquals("A1B2C3D4E5F6", strip.id)
        assertNull("JSON null must not become the text \"null\"", strip.name)
        assertTrue(strip.online)
        assertEquals(61.8, strip.watts, 1e-9)
        assertEquals(229.8, strip.volts!!, 1e-9)
        assertEquals(-58, strip.rssi)
        assertNull(strip.timer)
        assertTrue(strip.anyOn)
    }

    @Test
    fun outletsAreSortedAndOptionalFieldsHandled() {
        val outlets = StateJson.parseState(sample).strips.single().outlets
        assertEquals(listOf(1, 2, 3, 4), outlets.map { it.index })
        assertEquals("Kettle", outlets[1].name)
        assertEquals(StripTimer(turnOn = true, atEpochSeconds = 1790003600), outlets[1].timer)
        assertNull(outlets[2].tempC)
        assertNull("blank names fall back to the default", outlets[3].name)
        assertFalse(outlets[3].on)
    }

    @Test
    fun offlineStripWithoutReadings() {
        val state = StateJson.parseState(
            """{"strips":[{"id":"X","online":false,"watts":0,"kwh":0,"volts":null,"amps":null,"rssi":null,"outlets":[]}]}"""
        )
        val strip = state.strips.single()
        assertFalse(strip.online)
        assertNull(strip.volts)
        assertNull(strip.amps)
        assertNull(strip.rssi)
        assertNull(state.serverIp)
    }

    @Test
    fun emptyServer() {
        assertTrue(StateJson.parseState("""{"strips": []}""").strips.isEmpty())
    }
}
