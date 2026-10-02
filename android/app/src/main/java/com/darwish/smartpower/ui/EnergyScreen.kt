package com.darwish.smartpower.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.darwish.smartpower.R
import com.darwish.smartpower.data.EnergyReport
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private val RANGES = listOf("day" to R.string.range_day, "week" to R.string.range_week, "month" to R.string.range_month)

@Composable
fun EnergyScreen(vm: AppViewModel) {
    val state by vm.state.collectAsStateWithLifecycle()
    val range = state.reportRange
    LaunchedEffect(range) { vm.loadReport(range) }
    val report = state.report?.takeIf { it.range == range }
    val currency = currencyLabel(state.server?.settings?.currency ?: "EGP")

    LazyColumn(
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item {
            Text(stringResource(R.string.tab_energy), style = MaterialTheme.typography.headlineMedium)
            Text(stringResource(R.string.energy_sub), color = Glass.TextSoft)
        }
        item {
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(50)).background(Glass.Fill).padding(4.dp),
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                RANGES.forEach { (key, label) ->
                    val selected = key == range
                    Box(
                        Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(50))
                            .background(if (selected) Glass.Accent else Brush.linearGradient(listOf(Glass.Fill.copy(alpha = 0f), Glass.Fill.copy(alpha = 0f))))
                            .clickable { vm.loadReport(key) }
                            .padding(vertical = 10.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(stringResource(label), fontWeight = FontWeight.SemiBold, color = if (selected) Glass.Night else Glass.TextSoft)
                    }
                }
            }
        }
        if (report == null) {
            item { Box(Modifier.fillMaxWidth().padding(40.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
        } else {
            item {
                GlassCard(Modifier.fillMaxWidth(), padding = 20.dp) {
                    Row {
                        Column(Modifier.weight(1f)) {
                            Text(stringResource(R.string.energy_used), color = Glass.TextSoft, style = MaterialTheme.typography.labelLarge)
                            Text(kwh(report.totalKwh), style = MaterialTheme.typography.headlineMedium)
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Text(stringResource(R.string.energy_cost), color = Glass.TextSoft, style = MaterialTheme.typography.labelLarge)
                            Text(money(report.cost, currency), style = MaterialTheme.typography.headlineMedium, color = Glass.Amber)
                        }
                    }
                    Spacer(Modifier.height(18.dp))
                    BarChart(report, Modifier.fillMaxWidth().height(170.dp))
                    Spacer(Modifier.height(6.dp))
                    BarLabels(report)
                    state.server?.settings?.let {
                        Spacer(Modifier.height(10.dp))
                        Text(stringResource(R.string.price_note, money(it.pricePerKwh, currency)),
                            color = Glass.TextFaint, style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
            if (report.byOutlet.isNotEmpty()) {
                item { SectionHeader(stringResource(R.string.energy_by_device)) }
                val top = report.byOutlet.maxOf { it.kwh }.coerceAtLeast(0.001)
                items(report.byOutlet, key = { it.stripId + it.outlet }) { usage ->
                    val strip = state.strips.firstOrNull { it.id == usage.stripId }
                    val outlet = strip?.outlets?.firstOrNull { it.index == usage.outlet }
                    GlassCard(Modifier.fillMaxWidth(), padding = 14.dp) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(deviceEmoji(outlet?.icon ?: "plug"), fontSize = 24.sp)
                            Spacer(Modifier.width(12.dp))
                            Column(Modifier.weight(1f)) {
                                Text(
                                    outlet?.let { outletName(it) } ?: stringResource(R.string.default_outlet_name, usage.outlet),
                                    fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                )
                                Text(strip?.let { stripName(it) } ?: usage.stripId.takeLast(6), color = Glass.TextSoft,
                                    style = MaterialTheme.typography.labelSmall)
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(kwh(usage.kwh), fontWeight = FontWeight.SemiBold)
                                Text(money(usage.cost, currency), color = Glass.Amber, style = MaterialTheme.typography.labelSmall)
                            }
                        }
                        Spacer(Modifier.height(10.dp))
                        Box(Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(50)).background(Glass.Fill)) {
                            Box(
                                Modifier.fillMaxWidth((usage.kwh / top).toFloat().coerceIn(0.02f, 1f)).fillMaxHeight()
                                    .clip(RoundedCornerShape(50)).background(Glass.Accent)
                            )
                        }
                    }
                }
            } else {
                item {
                    Text(stringResource(R.string.energy_empty), color = Glass.TextSoft, modifier = Modifier.padding(8.dp))
                }
            }
        }
    }
}

@Composable
private fun BarChart(report: EnergyReport, modifier: Modifier) {
    val values = report.buckets.map { it.kwh }
    Canvas(modifier) {
        if (values.isEmpty()) return@Canvas
        val max = values.maxOrNull()?.coerceAtLeast(0.001) ?: 0.001
        val slot = size.width / values.size
        val barWidth = slot * 0.62f
        // faint guide lines
        for (i in 1..3) {
            val y = size.height * i / 4f
            drawLine(Glass.Stroke, Offset(0f, y), Offset(size.width, y), strokeWidth = 1f)
        }
        values.forEachIndexed { i, v ->
            val h = ((v / max).toFloat() * (size.height - 6f)).coerceAtLeast(if (v > 0) 4f else 2f)
            val x = i * slot + (slot - barWidth) / 2
            drawRoundRect(
                brush = if (v > 0) Brush.verticalGradient(listOf(Glass.Cyan, Glass.Purple)) else Brush.linearGradient(listOf(Glass.Stroke, Glass.Stroke)),
                topLeft = Offset(x, size.height - h),
                size = Size(barWidth, h),
                cornerRadius = CornerRadius(barWidth / 2, barWidth / 2),
            )
        }
    }
}

/** A few labels under the chart: hours for a day, dates for a week or month. */
@Composable
private fun BarLabels(report: EnergyReport) {
    val buckets = report.buckets
    if (buckets.isEmpty()) return
    val format = SimpleDateFormat(if (report.range == "day") "HH" else "d/M", Locale.US)
    val picks = when (report.range) {
        "day" -> listOf(0, 6, 12, 18, 23)
        "week" -> buckets.indices.toList()
        else -> listOf(0, 7, 14, 21, buckets.lastIndex)
    }.filter { it in buckets.indices }
    // charts read left to right in both languages, like the bars above
    CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr) {
    Row(Modifier.fillMaxWidth()) {
        picks.forEachIndexed { n, i ->
            Text(
                ltr(format.format(Date(buckets[i].startEpochSeconds * 1000))),
                color = Glass.TextFaint,
                style = MaterialTheme.typography.labelSmall,
                modifier = Modifier.weight(1f),
                textAlign = when (n) {
                    0 -> androidx.compose.ui.text.style.TextAlign.Start
                    picks.lastIndex -> androidx.compose.ui.text.style.TextAlign.End
                    else -> androidx.compose.ui.text.style.TextAlign.Center
                },
            )
        }
    }
    }
}
