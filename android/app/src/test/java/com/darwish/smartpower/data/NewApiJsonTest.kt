package com.darwish.smartpower.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NewApiJsonTest {

    // Shaped like smartpower.py 2.0's /api/state.
    private val state = """
        {"strips": [{"id": "A1", "name": "Kitchen strip", "room": "المطبخ", "online": true, "watts": 60, "kwh": 15,
                     "today_kwh": 0.085, "outlets": [
                       {"index": 1, "name": "الغلاية", "on": true, "watts": 60, "kwh": 1.5, "temp_c": 31,
                        "icon": "kettle", "favorite": true, "today_kwh": 0.06, "timer": null}]}],
         "schedules": [{"id": "ab12", "strip": "A1", "outlet": 1, "on": true, "time": "06:30",
                        "days": [0, 1, 2, 3, 4], "enabled": true}],
         "settings": {"price_kwh": 1.75, "currency": "EGP", "max_temp_c": 60, "max_watts": 3000},
         "today": {"kwh": 0.085, "cost": 0.15, "hours": [0, 0.01, 0.075]},
         "month": {"kwh": 12.5, "cost": 21.88},
         "last_event": 7,
         "server": {"ip": "192.168.1.116", "strip_port": 10086, "version": "2.0.0"}}
    """.trimIndent()

    @Test
    fun stateCarriesRoomsIconsAndUsage() {
        val s = StateJson.parseState(state)
        val strip = s.strips.single()
        assertEquals("المطبخ", strip.room)
        assertEquals(0.085, strip.todayKwh, 1e-9)
        val outlet = strip.outlets.single()
        assertEquals("kettle", outlet.icon)
        assertTrue(outlet.favorite)
        assertEquals(0.06, outlet.todayKwh, 1e-9)
        assertEquals(Usage(0.085, 0.15), s.today)
        assertEquals(listOf(0.0, 0.01, 0.075), s.todayHours)
        assertEquals(Usage(12.5, 21.88), s.month)
        assertEquals(7L, s.lastEventId)
        assertEquals(ServerSettings(1.75, "EGP", 60.0, 3000.0), s.settings)
        assertEquals(Schedule("ab12", "A1", listOf(1), true, "06:30", listOf(0, 1, 2, 3, 4), true), s.schedules.single())
    }

    @Test
    fun cyclesScenesLocksAndQueue() {
        val s = StateJson.parseState(
            """{"strips": [{"id": "A1", "locked": true, "pending": {"2": true, "0": false}, "outlets": []}],
                "schedules": [{"id": "c1", "strip": "A1", "outlets": [1, 3], "kind": "cycle", "on_minutes": 10,
                               "off_minutes": 50, "started_at": 100, "enabled": true, "phase_on": false, "next_change": 900}],
                "scenes": [{"id": "s1", "name": "السهرة", "icon": "🎬",
                            "actions": [{"strip": "A1", "outlet": 1, "on": true}, {"strip": "A1", "outlet": 2, "on": false}]}]}"""
        )
        val strip = s.strips.single()
        assertTrue(strip.locked)
        assertEquals(mapOf(2 to true, 0 to false), strip.pending)
        val cycle = s.schedules.single()
        assertTrue(cycle.isCycle)
        assertEquals(listOf(1, 3), cycle.outlets)
        assertEquals(10, cycle.onMinutes)
        assertEquals(50, cycle.offMinutes)
        assertEquals(false, cycle.phaseOn)
        assertEquals(900L, cycle.nextChangeEpochSeconds)
        val scene = s.scenes.single()
        assertEquals("السهرة", scene.name)
        assertEquals("🎬", scene.icon)
        assertEquals(listOf(SceneAction("A1", 1, true), SceneAction("A1", 2, false)), scene.actions)
    }

    @Test
    fun olderServerStillParses() {
        val s = StateJson.parseState("""{"strips": [{"id": "X", "outlets": [{"index": 1}]}]}""")
        assertEquals("plug", s.strips.single().outlets.single().icon)
        assertFalse(s.strips.single().outlets.single().favorite)
        assertTrue(s.schedules.isEmpty())
        assertEquals(ServerSettings(), s.settings)
    }

    @Test
    fun energyReport() {
        val r = StateJson.parseReport(
            """{"range": "week", "buckets": [{"t": 100, "kwh": 1.5}, {"t": 200, "kwh": 0}],
                "total_kwh": 1.5, "cost": 2.25, "currency": "EGP", "price_kwh": 1.5,
                "by_outlet": [{"strip": "A1", "outlet": 2, "kwh": 1.5, "cost": 2.25}]}"""
        )
        assertEquals("week", r.range)
        assertEquals(listOf(EnergyBucket(100, 1.5), EnergyBucket(200, 0.0)), r.buckets)
        assertEquals(2.25, r.cost, 1e-9)
        assertEquals(OutletUsage("A1", 2, 1.5, 2.25), r.byOutlet.single())
    }

    @Test
    fun events() {
        val (events, last) = StateJson.parseEvents(
            """{"events": [{"id": 9, "ts": 1790000000, "kind": "temp", "strip": "A1", "outlet": 4, "value": 61.5}],
                "last_id": 9}"""
        )
        assertEquals(9L, last)
        assertEquals(AlertEvent(9, 1790000000, "temp", "A1", 4, 61.5), events.single())
    }
}
