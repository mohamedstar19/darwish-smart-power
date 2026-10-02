package com.darwish.smartpower.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val Light = lightColorScheme(
    primary = Color(0xFFB83F0C),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFFFDBCC),
    onPrimaryContainer = Color(0xFF3A0B00),
    secondary = Color(0xFF2B7A3B),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFC7EFCB),
    onSecondaryContainer = Color(0xFF00210A),
    background = Color(0xFFFBF8F4),
    surface = Color(0xFFFBF8F4),
    surfaceVariant = Color(0xFFF0E9E2),
)

private val Dark = darkColorScheme(
    primary = Color(0xFFFFB597),
    onPrimary = Color(0xFF5E1800),
    primaryContainer = Color(0xFF842B00),
    onPrimaryContainer = Color(0xFFFFDBCC),
    secondary = Color(0xFF8FD99A),
    onSecondary = Color(0xFF003915),
    secondaryContainer = Color(0xFF115225),
    onSecondaryContainer = Color(0xFFC7EFCB),
    background = Color(0xFF15120F),
    surface = Color(0xFF15120F),
    surfaceVariant = Color(0xFF2A2420),
)

/** Material You colours on Android 12+, the brand orange everywhere else. */
@Composable
fun DarwishTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val scheme: ColorScheme = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> Dark
        else -> Light
    }
    MaterialTheme(colorScheme = scheme, content = content)
}
