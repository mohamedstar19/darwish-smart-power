package com.darwish.smartpower.ui

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Keeps "61.8 W" in that order inside Arabic text (a left-to-right isolate);
 * without it the bidi algorithm shows "W 61.8".
 */
fun ltr(text: String) = "⁦$text⁩"

fun number(value: Double, decimals: Int): String = String.format(Locale.US, "%.${decimals}f", value)

fun watts(value: Double) = ltr(number(value, 1) + " W")
fun kwh(value: Double) = ltr(number(value, 2) + " kWh")
fun volts(value: Double) = ltr(number(value, 1) + " V")
fun amps(value: Double) = ltr(number(value, 2) + " A")
fun celsius(value: Int) = ltr("$value°C")
fun rssi(value: Int) = ltr("Wi-Fi $value dBm")

fun clock(epochSeconds: Long): String =
    if (epochSeconds <= 0) "—" else ltr(SimpleDateFormat("HH:mm", Locale.US).format(Date(epochSeconds * 1000)))

fun money(value: Double, currency: String) = ltr(number(value, 2)) + " " + currency

/** "06:30" -> "6:30" style for lists; kept left-to-right inside Arabic text. */
fun hhmm(text: String) = ltr(text)

fun minutesLabel(minutes: Int): String = ltr(if (minutes % 60 == 0) "${minutes / 60}h" else "${minutes}m")
