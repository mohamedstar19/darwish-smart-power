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

/** The "dark glass" look: a deep gradient with translucent cards and glowing accents. */
@Immutable
object Glass {
    val Night = Color(0xFF070B18)
    val Deep = Color(0xFF111833)
    val Violet = Color(0xFF231446)
    val Teal = Color(0xFF0B3340)

    val Fill = Color(0x14FFFFFF)          // 8% white
    val FillStrong = Color(0x24FFFFFF)    // 14% white
    val Stroke = Color(0x26FFFFFF)        // 15% white

    val Text = Color(0xFFF3F6FF)
    val TextSoft = Color(0xFFA9B2CF)
    val TextFaint = Color(0xFF6E7899)

    val Cyan = Color(0xFF4FD1FF)
    val Green = Color(0xFF3EE6A0)
    val Amber = Color(0xFFFFB547)
    val Pink = Color(0xFFFF6B9A)
    val Red = Color(0xFFFF6B6B)
    val Purple = Color(0xFFA78BFA)

    val OnGlow = Brush.linearGradient(listOf(Color(0xFF1ED8A0), Color(0xFF14A9E0)))
    val Accent = Brush.linearGradient(listOf(Cyan, Purple))
    val Warm = Brush.linearGradient(listOf(Amber, Pink))

    val Card = RoundedCornerShape(24.dp)
    val Tile = RoundedCornerShape(20.dp)
    val Chip = RoundedCornerShape(50)
}

private val Scheme = darkColorScheme(
    primary = Glass.Cyan,
    onPrimary = Color(0xFF00222E),
    primaryContainer = Color(0xFF103A4D),
    onPrimaryContainer = Glass.Text,
    secondary = Glass.Green,
    onSecondary = Color(0xFF00281A),
    secondaryContainer = Color(0xFF0E3B2E),
    onSecondaryContainer = Glass.Text,
    tertiary = Glass.Amber,
    background = Glass.Night,
    onBackground = Glass.Text,
    surface = Glass.Deep,
    onSurface = Glass.Text,
    surfaceVariant = Color(0xFF1C2445),
    onSurfaceVariant = Glass.TextSoft,
    surfaceContainerHigh = Color(0xFF1A2142),
    surfaceContainer = Color(0xFF151C3A),
    surfaceContainerLow = Color(0xFF121834),
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
            .background(Brush.radialGradient(listOf(Glass.Violet.copy(alpha = 0.85f), Color.Transparent), radius = 900f))
            .background(
                Brush.radialGradient(
                    listOf(Glass.Teal.copy(alpha = 0.7f), Color.Transparent),
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
