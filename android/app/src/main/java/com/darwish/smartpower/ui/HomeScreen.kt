@file:OptIn(ExperimentalLayoutApi::class, ExperimentalTextApi::class)

package com.darwish.smartpower.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.darwish.smartpower.R
import com.darwish.smartpower.data.NewStrip
import com.darwish.smartpower.data.Outlet
import com.darwish.smartpower.data.Scene
import com.darwish.smartpower.data.Strip
import kotlinx.coroutines.launch
import java.util.Calendar

@Composable
fun HomeScreen(vm: AppViewModel, onSettings: () -> Unit, onSetup: () -> Unit, onAlerts: (() -> Unit)?) {
    val state by vm.state.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    var outletSheet by remember { mutableStateOf<Pair<String, Int>?>(null) }
    var stripSheet by remember { mutableStateOf<String?>(null) }
    val server = state.server
    val problem = state.problem

    LazyColumn(
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        item { Header(onAlerts) }
        val newStrips = server?.newStrips.orEmpty()
        if (state.me.isOwner && newStrips.isNotEmpty()) item {
            NewStripsCard(newStrips, onApprove = vm::approveStrip, onBlock = { vm.removeStrip(it, block = true) })
        }
        when {
            !state.loaded -> item {
                Box(Modifier.fillMaxWidth().padding(48.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
            }
            problem == Problem.NOT_CONFIGURED -> item {
                MessageCard(stringResource(R.string.welcome_title), stringResource(R.string.welcome_body),
                    stringResource(R.string.open_settings), onSettings)
            }
            // not signed in (or the sign-in ended): customers sign in or create an account right here
            problem == Problem.BAD_TOKEN && state.strips.isEmpty() -> item {
                AccountCard(vm, expired = state.token.isNotEmpty(), onServerPassword = onSettings)
            }
            state.strips.isEmpty() && problem != null -> item {
                MessageCard(stringResource(R.string.problem_title), stringResource(problemText(problem)),
                    stringResource(R.string.retry), { scope.launch { vm.refresh() } },
                    stringResource(R.string.open_settings), onSettings)
            }
            state.strips.isEmpty() -> item {
                MessageCard(stringResource(R.string.no_strips_title), stringResource(R.string.no_strips_body),
                    stringResource(R.string.setup_strip), onSetup)
            }
            else -> {
                if (problem != null) item {
                    GlassPill(stringResource(R.string.stale_banner) + " " + stringResource(problemText(problem)),
                        color = Glass.Amber, fill = Glass.Amber.copy(alpha = 0.12f))
                }
                if (!state.me.canControl) item {
                    GlassPill("👁 " + stringResource(R.string.role_view_long), color = Glass.Orange, fill = Glass.Orange.copy(alpha = 0.12f))
                }
                item { Hero(state) }
                val scenes = server?.scenes.orEmpty()
                if (scenes.isNotEmpty()) {
                    item { SectionHeader(stringResource(R.string.scenes)) }
                    item { ScenesRow(scenes, vm::runScene) }
                }
                val favorites = state.strips.flatMap { s -> s.outlets.filter { it.favorite }.map { s to it } }
                if (favorites.isNotEmpty()) {
                    item { SectionHeader(stringResource(R.string.favorites)) }
                    item {
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            items(favorites, key = { (s, o) -> s.id + o.index }) { (s, o) ->
                                FavoriteChip(s, o, busy = AppViewModel.key(s.id, o.index) in state.pending) {
                                    vm.switch(s.id, o.index, !o.on)
                                }
                            }
                        }
                    }
                }
                val rooms = state.strips.groupBy { it.room.orEmpty() }
                rooms.forEach { (room, strips) ->
                    if (rooms.size > 1 || room.isNotEmpty()) item(key = "room-$room") {
                        SectionHeader(room.ifEmpty { stringResource(R.string.no_room) })
                    }
                    items(strips, key = { it.id }) { strip ->
                        StripCard(
                            strip = strip,
                            pending = state.pending,
                            onSwitch = vm::switch,
                            onOutletMenu = { outletSheet = strip.id to it },
                            onStripMenu = { stripSheet = strip.id },
                        )
                    }
                }
            }
        }
    }

    outletSheet?.let { (stripId, index) ->
        val strip = state.strips.firstOrNull { it.id == stripId }
        val outlet = strip?.outlets?.firstOrNull { it.index == index }
        if (strip != null && outlet != null) OutletSheet(vm, strip, outlet) { outletSheet = null }
        else outletSheet = null
    }
    stripSheet?.let { id ->
        val strip = state.strips.firstOrNull { it.id == id }
        if (strip != null) StripSheet(vm, strip, state.strips.mapNotNull { it.room }.distinct()) { stripSheet = null }
        else stripSheet = null
    }
}

/** Strips that dialled in without being added from the app; the owner decides about each one. */
@Composable
private fun NewStripsCard(strips: List<NewStrip>, onApprove: (String) -> Unit, onBlock: (String) -> Unit) {
    GlassCard(Modifier.fillMaxWidth(), glow = Brush.linearGradient(listOf(Glass.Amber, Glass.Orange))) {
        Text("🆕 " + stringResource(R.string.new_strips_title), style = MaterialTheme.typography.titleMedium)
        Text(stringResource(R.string.new_strips_body), color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
        strips.forEach { strip ->
            Spacer(Modifier.height(10.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(stringResource(R.string.new_strip_line, ltr(strip.id.takeLast(6))), fontWeight = FontWeight.SemiBold)
                    if (strip.address.isNotEmpty()) {
                        Text(stringResource(R.string.new_strip_from, ltr(strip.address)), color = Glass.TextSoft,
                            style = MaterialTheme.typography.labelSmall)
                    }
                }
                TextButton(onClick = { onBlock(strip.id) }) { Text(stringResource(R.string.block), color = Glass.Red) }
                Button(onClick = { onApprove(strip.id) }) { Text(stringResource(R.string.approve)) }
            }
        }
    }
}

@Composable
private fun Header(onAlerts: (() -> Unit)?) {
    val hour = remember { Calendar.getInstance().get(Calendar.HOUR_OF_DAY) }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier.size(44.dp).clip(RoundedCornerShape(14.dp)).background(Glass.Accent),
            contentAlignment = Alignment.Center,
        ) { Text("⚡", fontSize = 22.sp) }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(stringResource(if (hour < 12) R.string.hello_morning else R.string.hello_evening),
                color = Glass.TextSoft, style = MaterialTheme.typography.bodyMedium)
            Text(stringResource(R.string.app_name), style = MaterialTheme.typography.titleLarge, maxLines = 1)
        }
        if (onAlerts != null) IconButton(onClick = onAlerts) {   // none before signing in
            Icon(Icons.Filled.Notifications, contentDescription = stringResource(R.string.alerts), tint = Glass.TextSoft)
        }
    }
}

@Composable
private fun Hero(state: UiState) {
    val server = state.server ?: return
    val total = state.strips.sumOf { it.watts }
    val shown by animateFloatAsState(total.toFloat(), label = "total")
    val currency = currencyLabel(server.settings.currency)
    GlassCard(Modifier.fillMaxWidth(), padding = 20.dp) {
        Text(stringResource(R.string.using_now), color = Glass.TextSoft, style = MaterialTheme.typography.bodyMedium)
        Text(
            watts(shown.toDouble()),
            style = MaterialTheme.typography.displaySmall.copy(brush = Brush.linearGradient(listOf(Color.White, Glass.Orange))),
        )
        Spacer(Modifier.height(8.dp))
        Sparkline(server.todayHours, Modifier.fillMaxWidth().height(56.dp))
        Spacer(Modifier.height(14.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Kpi(kwh(server.today.kwh), stringResource(R.string.today), Modifier.weight(1f))
            Kpi(money(server.today.cost, currency), stringResource(R.string.today_cost), Modifier.weight(1f))
            Kpi(money(server.month.cost, currency), stringResource(R.string.month_cost), Modifier.weight(1f))
        }
        Spacer(Modifier.height(10.dp))
        val online = state.strips.count { it.online }
        Text(
            stringResource(R.string.strips_online, online, state.strips.size),
            color = if (online == state.strips.size) Glass.Green else Glass.Amber,
            style = MaterialTheme.typography.labelMedium,
        )
    }
}

@Composable
fun Kpi(value: String, label: String, modifier: Modifier = Modifier) {
    Column(modifier.clip(RoundedCornerShape(16.dp)).background(Glass.Fill).padding(horizontal = 10.dp, vertical = 10.dp)) {
        Text(value, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(label, color = Glass.TextSoft, style = MaterialTheme.typography.labelSmall, maxLines = 1)
    }
}

/** Today's usage per hour as a soft glowing line. */
@Composable
fun Sparkline(values: List<Double>, modifier: Modifier = Modifier) {
    val data = if (values.size >= 2) values else List(24) { 0.0 }
    Canvas(modifier) {
        val max = (data.maxOrNull() ?: 0.0).coerceAtLeast(0.001)
        val step = size.width / (data.size - 1)
        fun y(v: Double) = (size.height - 4f) - (v / max).toFloat() * (size.height - 10f)
        val line = Path().apply {
            data.forEachIndexed { i, v -> if (i == 0) moveTo(0f, y(v)) else lineTo(i * step, y(v)) }
        }
        val area = Path().apply {
            addPath(line)
            lineTo(size.width, size.height)
            lineTo(0f, size.height)
            close()
        }
        drawPath(area, Brush.verticalGradient(listOf(Glass.Orange.copy(alpha = 0.35f), Color.Transparent)))
        drawPath(line, Glass.Orange, style = Stroke(width = 3.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
        val last = data.indexOfLast { it > 0 }
        if (last >= 0) drawCircle(Glass.Orange, 4.dp.toPx(), Offset(last * step, y(data[last])))
    }
}

@Composable
fun SectionHeader(text: String) {
    Text(text, style = SectionTitleStyle, modifier = Modifier.padding(start = 4.dp, top = 6.dp))
}

@Composable
private fun ScenesRow(scenes: List<Scene>, onRun: (Scene) -> Unit) {
    LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        items(scenes, key = { it.id }) { scene ->
            GlassCard(
                fill = Brush.linearGradient(listOf(Glass.Orange.copy(alpha = 0.18f), Glass.Gold.copy(alpha = 0.14f))),
                shape = RoundedCornerShape(18.dp),
                padding = 14.dp,
                onClick = { onRun(scene) },
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(scene.icon, fontSize = 20.sp)
                    Spacer(Modifier.width(8.dp))
                    Text(scene.name, fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }
}

@Composable
private fun FavoriteChip(strip: Strip, outlet: Outlet, busy: Boolean, onToggle: () -> Unit) {
    val haptic = LocalHapticFeedback.current
    GlassCard(
        modifier = Modifier.width(132.dp).alpha(if (strip.online) 1f else 0.6f),
        shape = Glass.Tile,
        padding = 12.dp,
        fill = if (outlet.on) Brush.linearGradient(listOf(Glass.Orange.copy(alpha = 0.38f), Glass.Gold.copy(alpha = 0.22f))) else null,
        glow = if (outlet.on) Brush.linearGradient(listOf(Glass.Gold, Glass.Orange)) else null,
        onClick = { haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove); onToggle() },
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(deviceEmoji(outlet.icon), fontSize = 22.sp)
            Spacer(Modifier.weight(1f))
            if (busy) CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp) else PowerDot(outlet.on)
        }
        Spacer(Modifier.height(8.dp))
        Text(outletName(outlet), fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(watts(outlet.watts), color = Glass.TextSoft, style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun PowerDot(on: Boolean) {
    Box(Modifier.size(10.dp).clip(CircleShape).background(if (on) Glass.Green else Glass.TextFaint))
}

@Composable
private fun StripCard(
    strip: Strip,
    pending: Set<String>,
    onSwitch: (String, Int, Boolean) -> Unit,
    onOutletMenu: (Int) -> Unit,
    onStripMenu: () -> Unit,
) {
    val allBusy = AppViewModel.key(strip.id, 0) in pending
    GlassCard(Modifier.fillMaxWidth().alpha(if (strip.online) 1f else 0.7f)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(stripName(strip), style = MaterialTheme.typography.titleLarge, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                    if (strip.locked) Text(" 🔒", fontSize = 14.sp)
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    PowerDot(strip.online)
                    Spacer(Modifier.width(6.dp))
                    Text(
                        if (strip.online) stringResource(R.string.online)
                        else stringResource(R.string.last_seen, clock(strip.lastSeenEpochSeconds)),
                        color = Glass.TextSoft, style = MaterialTheme.typography.labelMedium,
                    )
                }
            }
            Text(watts(strip.watts), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            IconButton(onClick = onStripMenu) {
                Icon(Icons.Filled.MoreVert, contentDescription = stringResource(R.string.more), tint = Glass.TextSoft)
            }
        }
        Spacer(Modifier.height(8.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            GlassPill(stringResource(R.string.today_short, kwh(strip.todayKwh)))
            strip.volts?.let { GlassPill(volts(it)) }
            strip.amps?.let { GlassPill(amps(it)) }
            strip.rssi?.let { GlassPill(rssi(it)) }
            if (strip.pending.isNotEmpty()) GlassPill("⏳ " + stringResource(R.string.waiting_for_strip), color = Glass.Amber)
        }
        strip.timer?.let { Spacer(Modifier.height(8.dp)); TimerLine(it) }
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { onSwitch(strip.id, 0, true) }, enabled = !allBusy, modifier = Modifier.weight(1f)) {
                Text(stringResource(R.string.all_on), maxLines = 1)
            }
            OutlinedButton(onClick = { onSwitch(strip.id, 0, false) }, enabled = !allBusy, modifier = Modifier.weight(1f)) {
                Text(stringResource(R.string.all_off), maxLines = 1, color = Glass.Text)
            }
        }
        Spacer(Modifier.height(12.dp))
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            strip.outlets.chunked(2).forEach { pair ->
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    pair.forEach { outlet ->
                        OutletTile(
                            outlet = outlet,
                            busy = allBusy || AppViewModel.key(strip.id, outlet.index) in pending,
                            queued = strip.pending.containsKey(outlet.index) || strip.pending.containsKey(0),
                            onToggle = { onSwitch(strip.id, outlet.index, !outlet.on) },
                            onMenu = { onOutletMenu(outlet.index) },
                            modifier = Modifier.weight(1f),
                        )
                    }
                    if (pair.size == 1) Spacer(Modifier.weight(1f))
                }
            }
        }
    }
}

@Composable
private fun OutletTile(
    outlet: Outlet,
    busy: Boolean,
    queued: Boolean,
    onToggle: () -> Unit,
    onMenu: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val haptic = LocalHapticFeedback.current
    val knob by animateFloatAsState(if (outlet.on) 1f else 0f, label = "knob")
    val track by animateColorAsState(if (outlet.on) Glass.Orange else Color(0x33FFFFFF), label = "track")
    GlassCard(
        modifier = modifier,
        shape = Glass.Tile,
        padding = 12.dp,
        fill = if (outlet.on) Brush.linearGradient(listOf(Glass.Orange.copy(alpha = 0.38f), Glass.Gold.copy(alpha = 0.22f))) else null,
        glow = if (outlet.on) Brush.linearGradient(listOf(Glass.Gold.copy(alpha = 0.8f), Glass.Orange.copy(alpha = 0.9f))) else null,
        onClick = { haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove); onToggle() },
        onLongClick = { haptic.performHapticFeedback(HapticFeedbackType.LongPress); onMenu() },
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(deviceEmoji(outlet.icon), fontSize = 26.sp)
            Spacer(Modifier.weight(1f))
            if (busy) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
            } else {
                // a small switch drawn by hand so the whole tile stays one tap target
                Box(Modifier.width(38.dp).height(22.dp).clip(RoundedCornerShape(50)).background(track).padding(3.dp)) {
                    Box(
                        Modifier.padding(start = (16 * knob).dp).size(16.dp).clip(CircleShape).background(Color.White)
                    )
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        Text(outletName(outlet), fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(
            listOfNotNull(watts(outlet.watts), outlet.tempC?.let { celsius(it) }).joinToString(" · "),
            color = Glass.TextSoft, style = MaterialTheme.typography.labelSmall,
        )
        when {
            queued -> Text("⏳ " + stringResource(R.string.waiting_for_strip), color = Glass.Amber, style = MaterialTheme.typography.labelSmall)
            outlet.timer != null -> TimerLine(outlet.timer)
        }
        Text(
            "⋯",
            color = Glass.TextSoft,
            modifier = Modifier.align(Alignment.End).clip(CircleShape).clickable(onClick = onMenu).padding(horizontal = 8.dp),
        )
    }
}

@Composable
private fun MessageCard(
    title: String,
    body: String,
    action: String,
    onAction: () -> Unit,
    secondary: String? = null,
    onSecondary: () -> Unit = {},
) {
    GlassCard(Modifier.fillMaxWidth(), padding = 20.dp) {
        Text(title, style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp))
        Text(body, color = Glass.TextSoft)
        Spacer(Modifier.height(16.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = onAction) { Text(action) }
            if (secondary != null) OutlinedButton(onClick = onSecondary) { Text(secondary, color = Glass.Text) }
        }
    }
}

fun problemText(problem: Problem): Int = when (problem) {
    Problem.NOT_CONFIGURED -> R.string.welcome_body
    Problem.UNREACHABLE -> R.string.problem_unreachable
    Problem.BAD_TOKEN -> R.string.problem_bad_token
    Problem.SERVER -> R.string.problem_server
}

@Composable
fun TimerLine(timer: com.darwish.smartpower.data.StripTimer) {
    Text(
        stringResource(if (timer.turnOn) R.string.timer_turns_on else R.string.timer_turns_off, clock(timer.atEpochSeconds)),
        style = MaterialTheme.typography.labelSmall,
        color = Glass.Amber,
    )
}

@Composable
fun stripName(strip: Strip): String = strip.name ?: stringResource(R.string.default_strip_name, strip.id.takeLast(6))

@Composable
fun outletName(outlet: Outlet): String = outlet.name ?: stringResource(R.string.default_outlet_name, outlet.index)

@Composable
fun currencyLabel(code: String): String = if (code == "EGP") stringResource(R.string.currency_egp) else code
