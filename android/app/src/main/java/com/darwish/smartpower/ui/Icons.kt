package com.darwish.smartpower.ui

import androidx.annotation.StringRes
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.unit.dp
import com.darwish.smartpower.R

/** Device icons are emoji: colourful, crisp at any size and no icon library needed. */
data class DeviceIcon(val key: String, val emoji: String, @param:StringRes val label: Int)

val DeviceIcons = listOf(
    DeviceIcon("plug", "🔌", R.string.icon_plug),
    DeviceIcon("kettle", "☕", R.string.icon_kettle),
    DeviceIcon("router", "📶", R.string.icon_router),
    DeviceIcon("tv", "📺", R.string.icon_tv),
    DeviceIcon("ac", "❄️", R.string.icon_ac),
    DeviceIcon("lamp", "💡", R.string.icon_lamp),
    DeviceIcon("heater", "🔥", R.string.icon_heater),
    DeviceIcon("fan", "🌀", R.string.icon_fan),
    DeviceIcon("fridge", "🧊", R.string.icon_fridge),
    DeviceIcon("washer", "🧺", R.string.icon_washer),
    DeviceIcon("charger", "🔋", R.string.icon_charger),
    DeviceIcon("computer", "💻", R.string.icon_computer),
    DeviceIcon("speaker", "🔊", R.string.icon_speaker),
    DeviceIcon("camera", "📷", R.string.icon_camera),
    DeviceIcon("microwave", "🍲", R.string.icon_microwave),
    DeviceIcon("iron", "👔", R.string.icon_iron),
)

fun deviceEmoji(key: String): String = DeviceIcons.firstOrNull { it.key == key }?.emoji ?: "🔌"

val SceneEmojis = listOf("✨", "🎬", "🌙", "☀️", "🍽️", "🛋️", "🛏️", "🚪", "🎮", "📖", "🧹", "🔕")

/** A lightning bolt for the Energy tab (not in the core icon set). */
val BoltIcon: androidx.compose.ui.graphics.vector.ImageVector by lazy {
    androidx.compose.ui.graphics.vector.ImageVector.Builder(
        name = "Bolt", defaultWidth = 24.dp, defaultHeight = 24.dp, viewportWidth = 24f, viewportHeight = 24f,
    ).apply {
        path(fill = androidx.compose.ui.graphics.SolidColor(androidx.compose.ui.graphics.Color.Black)) {
            moveTo(13f, 2f)
            lineTo(4f, 14f)
            lineTo(11f, 14f)
            lineTo(10f, 22f)
            lineTo(20f, 9f)
            lineTo(13f, 9f)
            close()
        }
    }.build()
}
