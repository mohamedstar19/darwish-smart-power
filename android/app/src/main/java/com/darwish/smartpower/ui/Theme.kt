package com.darwish.smartpower.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** The "dark glass" look: a deep warm gradient with translucent cards and orange and gold accents. */
@Immutable
object Glass {
    val Night = Color(0xFF0D0907)
    val Deep = Color(0xFF1E130D)
    val Ember = Color(0xFF3A1A0C)
    val Umber = Color(0xFF2A1F08)

    val Fill = Color(0x14FFFFFF)          // 8% white
    val FillStrong = Color(0x24FFFFFF)    // 14% white
    val Stroke = Color(0x26FFFFFF)        // 15% white

    val Text = Color(0xFFFFF4EC)
    val TextSoft = Color(0xFFC9B8AC)
    val TextFaint = Color(0xFF8A7A6F)

    val Orange = Color(0xFFFF7A3D)
    val Gold = Color(0xFFFFB347)
    val Green = Color(0xFF3EE6A0)
    val Amber = Color(0xFFFFC857)
    val Pink = Color(0xFFFF6B9A)
    val Red = Color(0xFFFF6B6B)

    val Accent = Brush.linearGradient(listOf(Orange, Gold))
    val Warm = Brush.linearGradient(listOf(Amber, Pink))

    val Card = RoundedCornerShape(24.dp)
    val Tile = RoundedCornerShape(20.dp)
    val Chip = RoundedCornerShape(50)
}

private val Scheme = darkColorScheme(
    primary = Glass.Orange,
    onPrimary = Color(0xFF2A0E00),
    primaryContainer = Color(0xFF4A2010),
    onPrimaryContainer = Glass.Text,
    secondary = Glass.Gold,
    onSecondary = Color(0xFF2A1A00),
    secondaryContainer = Color(0xFF3D2A0E),
    onSecondaryContainer = Glass.Text,
    tertiary = Glass.Green,
    background = Glass.Night,
    onBackground = Glass.Text,
    surface = Glass.Deep,
    onSurface = Glass.Text,
    surfaceVariant = Color(0xFF2E1F17),
    onSurfaceVariant = Glass.TextSoft,
    surfaceContainerHigh = Color(0xFF2A1B13),
    surfaceContainer = Color(0xFF231710),
    surfaceContainerLow = Color(0xFF1C120C),
    outline = Glass.Stroke,
    outlineVariant = Color(0x1AFFFFFF),
    error = Glass.Red,
    errorContainer = Color(0xFF4A1620),
    onErrorContainer = Color(0xFFFFDADA),
)

private val Type = Typography().let { t ->
    t.copy(
        displaySmall = t.displaySmall.copy(fontWeight = FontWeight.Bold, letterSpacing = (-1).sp),
        headlineMedium = t.headlineMedium.copy(fontWeight = FontWeight.Bold),
        titleLarge = t.titleLarge.copy(fontWeight = FontWeight.SemiBold),
        titleMedium = t.titleMedium.copy(fontWeight = FontWeight.SemiBold),
    )
}

@Composable
fun DarwishTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = Scheme, typography = Type, content = content)
}

/** Full-screen gradient with two soft colour blobs behind everything. */
@Composable
fun GlassBackground(modifier: Modifier = Modifier, content: @Composable BoxScope.() -> Unit) {
    Box(
        modifier
            .fillMaxSize()
            .background(Brush.verticalGradient(listOf(Glass.Deep, Glass.Night, Glass.Night)))
            .background(Brush.radialGradient(listOf(Glass.Ember.copy(alpha = 0.9f), Color.Transparent), radius = 900f))
            .background(
                Brush.radialGradient(
                    listOf(Glass.Umber.copy(alpha = 0.8f), Color.Transparent),
                    center = androidx.compose.ui.geometry.Offset(1400f, 2200f),
                    radius = 1100f,
                )
            ),
        content = content,
    )
}

/** A translucent card. [glow] draws a coloured border, e.g. for something that is on. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun GlassCard(
    modifier: Modifier = Modifier,
    shape: Shape = Glass.Card,
    fill: Brush? = null,
    glow: Brush? = null,
    padding: Dp = 16.dp,
    onClick: (() -> Unit)? = null,
    onLongClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    var m = modifier
        .clip(shape)
        .background(fill ?: Brush.linearGradient(listOf(Glass.FillStrong, Glass.Fill)))
        .border(BorderStroke(1.dp, glow ?: Brush.linearGradient(listOf(Glass.Stroke, Color(0x0DFFFFFF)))), shape)
    if (onClick != null || onLongClick != null) {
        m = m.combinedClickable(onClick = { onClick?.invoke() }, onLongClick = onLongClick)
    }
    Column(m.padding(padding), content = content)
}

/** Small rounded label. */
@Composable
fun GlassPill(text: String, modifier: Modifier = Modifier, color: Color = Glass.TextSoft, fill: Color = Glass.Fill) {
    Surface(modifier = modifier, color = fill, contentColor = color, shape = Glass.Chip) {
        Text(
            text,
            style = MaterialTheme.typography.labelMedium,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
        )
    }
}

val SectionTitleStyle: TextStyle
    @Composable get() = MaterialTheme.typography.titleMedium.copy(color = Glass.Text)
