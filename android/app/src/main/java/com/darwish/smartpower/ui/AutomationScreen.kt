@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.darwish.smartpower.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimeInput
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.darwish.smartpower.R
import com.darwish.smartpower.data.Scene
import com.darwish.smartpower.data.SceneAction
import com.darwish.smartpower.data.Schedule
import com.darwish.smartpower.data.Strip

/** Weekdays in the order an Egyptian week is read, Saturday first. Values: 0 = Monday … 6 = Sunday. */
private val WEEK = listOf(5 to R.string.day_sat, 6 to R.string.day_sun, 0 to R.string.day_mon, 1 to R.string.day_tue,
    2 to R.string.day_wed, 3 to R.string.day_thu, 4 to R.string.day_fri)

private enum class Editing { NONE, SCHEDULE, CYCLE, WATCH, SCENE }

@Composable
fun AutomationScreen(vm: AppViewModel) {
    val state by vm.state.collectAsStateWithLifecycle()
    val schedules = state.server?.schedules.orEmpty()
    val scenes = state.server?.scenes.orEmpty()
    val timers = state.strips.flatMap { s ->
        listOfNotNull(s.timer?.let { Triple(s, 0, it) }) + s.outlets.mapNotNull { o -> o.timer?.let { Triple(s, o.index, it) } }
    }
    var editing by remember { mutableStateOf(Editing.NONE) }
    var editSchedule by remember { mutableStateOf<Schedule?>(null) }
    var editScene by remember { mutableStateOf<Scene?>(null) }
    var addMenu by remember { mutableStateOf(false) }

    Box(Modifier.fillMaxSize()) {
        LazyColumn(
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 96.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Text(stringResource(R.string.tab_automation), style = MaterialTheme.typography.headlineMedium)
                Text(stringResource(R.string.automation_sub), color = Glass.TextSoft)
            }
            item {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Kpi(ltr(schedules.count { !it.isCycle }.toString()), "📅 " + stringResource(R.string.kind_schedule), Modifier.weight(1f))
                    Kpi(ltr(timers.size.toString()), "⏱ " + stringResource(R.string.kind_timer), Modifier.weight(1f))
                    Kpi(ltr(schedules.count { it.isCycle }.toString()), "🔁 " + stringResource(R.string.kind_cycle), Modifier.weight(1f))
                    Kpi(ltr(scenes.size.toString()), "✨ " + stringResource(R.string.scenes), Modifier.weight(1f))
                }
            }
            if (state.strips.isEmpty()) {
                item { Text(stringResource(R.string.automation_no_strips), color = Glass.TextSoft) }
            }
            if (schedules.isNotEmpty()) {
                item { SectionHeader(stringResource(R.string.schedules_and_cycles)) }
                items(schedules, key = { it.id }) { s ->
                    ScheduleCard(
                        s, state.strips,
                        onToggle = { vm.saveSchedule(s.copy(enabled = it)) },
                        onEdit = {
                            editSchedule = s
                            editing = if (s.isWatch) Editing.WATCH else if (s.isCycle) Editing.CYCLE else Editing.SCHEDULE
                        },
                        onDelete = { vm.deleteSchedule(s) },
                    )
                }
            }
            if (timers.isNotEmpty()) {
                item { SectionHeader(stringResource(R.string.active_timers)) }
                items(timers, key = { (s, o, _) -> "t-${s.id}-$o" }) { (strip, outlet, timer) ->
                    GlassCard(Modifier.fillMaxWidth(), padding = 14.dp) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("⏱", fontSize = 22.sp)
                            Spacer(Modifier.width(12.dp))
                            Column(Modifier.weight(1f)) {
                                Text(targetName(strip, listOf(outlet)), fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                TimerLine(timer)
                            }
                            IconButton(onClick = { vm.setTimer(strip.id, listOf(outlet), 0, false) }) {
                                Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.cancel_timer), tint = Glass.Red)
                            }
                        }
                    }
                }
            }
            item { SectionHeader(stringResource(R.string.scenes)) }
            if (scenes.isEmpty()) {
                item { Text(stringResource(R.string.scenes_empty), color = Glass.TextSoft) }
            }
            items(scenes, key = { "sc-" + it.id }) { scene ->
                GlassCard(Modifier.fillMaxWidth(), padding = 14.dp, onClick = { vm.runScene(scene) }) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(scene.icon, fontSize = 26.sp)
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(scene.name, fontWeight = FontWeight.SemiBold)
                            Text(stringResource(R.string.scene_count, scene.actions.size), color = Glass.TextSoft,
                                style = MaterialTheme.typography.labelSmall)
                        }
                        IconButton(onClick = { editScene = scene; editing = Editing.SCENE }) {
                            Icon(Icons.Filled.Edit, contentDescription = stringResource(R.string.edit), tint = Glass.TextSoft)
                        }
                        IconButton(onClick = { vm.deleteScene(scene) }) {
                            Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.delete), tint = Glass.Red)
                        }
                    }
                }
            }
        }
        if (state.strips.isNotEmpty() && state.me.canControl) {
            Box(Modifier.align(Alignment.BottomEnd).padding(16.dp)) {
                ExtendedFloatingActionButton(
                    onClick = { addMenu = true },
                    icon = { Icon(Icons.Filled.Add, contentDescription = null) },
                    text = { Text(stringResource(R.string.add)) },
                    containerColor = Glass.Orange,
                    contentColor = Glass.Night,
                )
                DropdownMenu(expanded = addMenu, onDismissRequest = { addMenu = false }) {
                    DropdownMenuItem(text = { Text("📅  " + stringResource(R.string.new_schedule)) },
                        onClick = { addMenu = false; editSchedule = null; editing = Editing.SCHEDULE })
                    DropdownMenuItem(text = { Text("🔁  " + stringResource(R.string.new_cycle)) },
                        onClick = { addMenu = false; editSchedule = null; editing = Editing.CYCLE })
                    DropdownMenuItem(text = { Text("🛡️  " + stringResource(R.string.new_watch)) },
                        onClick = { addMenu = false; editSchedule = null; editing = Editing.WATCH })
                    DropdownMenuItem(text = { Text("✨  " + stringResource(R.string.new_scene)) },
                        onClick = { addMenu = false; editScene = null; editing = Editing.SCENE })
                }
            }
        }
    }

    when (editing) {
        Editing.SCHEDULE, Editing.CYCLE -> ScheduleEditor(
            strips = state.strips,
            initial = editSchedule,
            cycle = editing == Editing.CYCLE,
            onDismiss = { editing = Editing.NONE },
            onSave = { vm.saveSchedule(it); editing = Editing.NONE },
        )
        Editing.WATCH -> WatchEditor(
            strips = state.strips,
            initial = editSchedule,
            onDismiss = { editing = Editing.NONE },
            onSave = { vm.saveSchedule(it); editing = Editing.NONE },
        )
        Editing.SCENE -> SceneEditor(
            strips = state.strips,
            initial = editScene,
            onDismiss = { editing = Editing.NONE },
            onSave = { vm.saveScene(it); editing = Editing.NONE },
        )
        Editing.NONE -> Unit
    }
}

@Composable
private fun ScheduleCard(s: Schedule, strips: List<Strip>, onToggle: (Boolean) -> Unit, onEdit: () -> Unit, onDelete: () -> Unit) {
    val strip = strips.firstOrNull { it.id == s.stripId }
    GlassCard(Modifier.fillMaxWidth(), padding = 14.dp) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(if (s.isWatch) "🛡️" else if (s.isCycle) "🔁" else if (s.turnOn) "🟢" else "⭕", fontSize = 22.sp)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                if (s.isWatch) {
                    Text(watchLine(s), fontWeight = FontWeight.SemiBold)
                } else if (s.isCycle) {
                    Text(stringResource(R.string.cycle_line, durationLabel(s.onMinutes), durationLabel(s.offMinutes)),
                        fontWeight = FontWeight.SemiBold)
                } else {
                    Text(hhmm(s.time) + " · " + stringResource(if (s.turnOn) R.string.turn_on else R.string.turn_off),
                        fontWeight = FontWeight.SemiBold)
                }
                Text(strip?.let { targetName(it, s.outlets) } ?: s.stripId.takeLast(6), color = Glass.TextSoft,
                    style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (s.isWatch) {
                    Text(stringResource(if (s.action == Schedule.ACTION_OFF) R.string.watch_off else R.string.watch_alert),
                        color = Glass.Amber, style = MaterialTheme.typography.labelSmall)
                } else if (s.isCycle) {
                    if (s.enabled && s.phaseOn != null) {
                        Text(
                            stringResource(if (s.phaseOn) R.string.cycle_now_on else R.string.cycle_now_off, clock(s.nextChangeEpochSeconds)),
                            color = Glass.Amber, style = MaterialTheme.typography.labelSmall,
                        )
                    }
                } else {
                    Text(daysLabel(s.days), color = Glass.TextFaint, style = MaterialTheme.typography.labelSmall)
                }
            }
            Switch(checked = s.enabled, onCheckedChange = onToggle)
        }
        Row(Modifier.align(Alignment.End)) {
            IconButton(onClick = onEdit) { Icon(Icons.Filled.Edit, contentDescription = stringResource(R.string.edit), tint = Glass.TextSoft) }
            IconButton(onClick = onDelete) { Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.delete), tint = Glass.Red) }
        }
    }
}

@Composable
private fun daysLabel(days: List<Int>): String = when {
    days.size == 7 -> stringResource(R.string.every_day)
    else -> WEEK.filter { it.first in days }.map { stringResource(it.second) }.joinToString("، ")
}

@Composable
fun targetName(strip: Strip, outlets: List<Int>): String =
    if (outlets.isEmpty() || 0 in outlets) stripName(strip) + " · " + stringResource(R.string.all_outlets)
    else strip.outlets.filter { it.index in outlets }.map { outletName(it) }.joinToString("، ") + " · " + stripName(strip)

/** New or edited schedule (a time on chosen days) or cycle (on/off minutes, repeating). */
@Composable
private fun ScheduleEditor(strips: List<Strip>, initial: Schedule?, cycle: Boolean, onDismiss: () -> Unit, onSave: (Schedule) -> Unit) {
    var stripId by remember { mutableStateOf(initial?.stripId ?: strips.first().id) }
    val strip = strips.firstOrNull { it.id == stripId } ?: strips.first()
    var outlets by remember { mutableStateOf(initial?.outlets?.toSet() ?: setOf(0)) }
    var turnOn by remember { mutableStateOf(initial?.turnOn ?: true) }
    var days by remember { mutableStateOf(initial?.days?.toSet() ?: (0..6).toSet()) }
    var onText by remember { mutableStateOf((initial?.onMinutes ?: 10).toString()) }
    var offText by remember { mutableStateOf((initial?.offMinutes ?: 50).toString()) }
    val (h0, m0) = (initial?.time ?: "07:00").split(":").map { it.toIntOrNull() ?: 0 }.let { it[0] to it.getOrElse(1) { 0 } }
    val time = rememberTimePickerState(initialHour = h0, initialMinute = m0, is24Hour = true)
    val onMin = onText.toIntOrNull()
    val offMin = offText.toIntOrNull()
    val valid = outlets.isNotEmpty() && if (cycle) onMin in 1..1440 && offMin in 1..1440 else days.isNotEmpty()

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(if (cycle) R.string.new_cycle else R.string.new_schedule)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (strips.size > 1) {
                    Text(stringResource(R.string.strip), style = MaterialTheme.typography.labelLarge)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        strips.forEach { s ->
                            FilterChip(selected = s.id == stripId, onClick = { stripId = s.id; outlets = setOf(0) }, label = { Text(stripName(s)) })
                        }
                    }
                }
                Text(stringResource(R.string.outlets), style = MaterialTheme.typography.labelLarge)
                OutletPicker(strip, outlets) { outlets = it }
                if (cycle) {
                    Text(stringResource(R.string.cycle_explain), color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        MinutesField(onText, stringResource(R.string.cycle_on_minutes), Modifier.weight(1f)) { onText = it }
                        MinutesField(offText, stringResource(R.string.cycle_off_minutes), Modifier.weight(1f)) { offText = it }
                    }
                } else {
                    Text(stringResource(R.string.timer_action), style = MaterialTheme.typography.labelLarge)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(selected = turnOn, onClick = { turnOn = true }, label = { Text(stringResource(R.string.turn_on)) })
                        FilterChip(selected = !turnOn, onClick = { turnOn = false }, label = { Text(stringResource(R.string.turn_off)) })
                    }
                    Text(stringResource(R.string.at_time), style = MaterialTheme.typography.labelLarge)
                    TimeInput(state = time)
                    Text(stringResource(R.string.on_days), style = MaterialTheme.typography.labelLarge)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        WEEK.forEach { (d, label) ->
                            FilterChip(selected = d in days, onClick = { days = if (d in days) days - d else days + d },
                                label = { Text(stringResource(label)) })
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(enabled = valid, onClick = {
                onSave(
                    Schedule(
                        id = initial?.id.orEmpty(),
                        stripId = strip.id,
                        outlets = outlets.sorted(),
                        turnOn = turnOn,
                        time = String.format(java.util.Locale.US, "%02d:%02d", time.hour, time.minute),
                        days = days.sorted(),
                        enabled = initial?.enabled ?: true,
                        kind = if (cycle) Schedule.KIND_CYCLE else Schedule.KIND_TIME,
                        onMinutes = onMin ?: 0,
                        offMinutes = offMin ?: 0,
                    )
                )
            }) { Text(stringResource(R.string.save)) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel)) } },
    )
}

/** "Power under 3 W for 10 min" and the like. */
@Composable
private fun watchLine(s: Schedule): String {
    val number = if (s.value % 1.0 == 0.0) s.value.toInt().toString() else s.value.toString()
    val amount = if (s.metric == Schedule.METRIC_TEMP) "$number°C" else stringResource(R.string.watts_value, number)
    return stringResource(
        R.string.watch_line,
        stringResource(if (s.metric == Schedule.METRIC_TEMP) R.string.watch_temp else R.string.watch_power),
        stringResource(if (s.above) R.string.watch_above else R.string.watch_below),
        ltr(amount),
        secondsLabel(s.seconds),
    )
}

@Composable
private fun secondsLabel(seconds: Int): String =
    if (seconds % 60 == 0) durationLabel(seconds / 60) else stringResource(R.string.seconds_value, seconds)

/** A monitoring rule: what to watch, the limit, for how long, and whether to switch off or only report. */
@Composable
private fun WatchEditor(strips: List<Strip>, initial: Schedule?, onDismiss: () -> Unit, onSave: (Schedule) -> Unit) {
    var stripId by remember { mutableStateOf(initial?.stripId ?: strips.first().id) }
    val strip = strips.firstOrNull { it.id == stripId } ?: strips.first()
    var outlets by remember { mutableStateOf(initial?.outlets?.toSet() ?: setOf(1)) }
    var metric by remember { mutableStateOf(initial?.metric ?: Schedule.METRIC_POWER) }
    var above by remember { mutableStateOf(initial?.above ?: false) }
    var valueText by remember {
        mutableStateOf(initial?.value?.let { if (it % 1.0 == 0.0) it.toInt().toString() else it.toString() } ?: "3")
    }
    var inMinutes by remember { mutableStateOf(initial?.let { it.seconds % 60 == 0 } ?: true) }
    var timeText by remember {
        mutableStateOf(initial?.seconds?.let { (if (it % 60 == 0) it / 60 else it).toString() } ?: "10")
    }
    var action by remember { mutableStateOf(initial?.action ?: Schedule.ACTION_OFF) }
    fun preset(m: String, up: Boolean, v: String, minutes: Boolean, t: String) {
        metric = m; above = up; valueText = v; inMinutes = minutes; timeText = t; action = Schedule.ACTION_OFF
    }
    val value = valueText.toDoubleOrNull()
    val seconds = timeText.toIntOrNull()?.let { if (inMinutes) it * 60 else it }
    val range = if (metric == Schedule.METRIC_TEMP) 20.0..100.0 else 0.0..4000.0
    val valid = outlets.isNotEmpty() && value != null && value in range && (above || value > 0) &&
        seconds != null && seconds in 10..86400

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.new_watch)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(stringResource(R.string.watch_explain), color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
                Text(stringResource(R.string.watch_presets), style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = false, onClick = { preset(Schedule.METRIC_POWER, false, "3", true, "10") },
                        label = { Text(stringResource(R.string.watch_preset_charger)) })
                    FilterChip(selected = false, onClick = { preset(Schedule.METRIC_POWER, true, "2000", false, "30") },
                        label = { Text(stringResource(R.string.watch_preset_overload)) })
                    FilterChip(selected = false, onClick = { preset(Schedule.METRIC_TEMP, true, "60", false, "60") },
                        label = { Text(stringResource(R.string.watch_preset_hot)) })
                }
                if (strips.size > 1) {
                    Text(stringResource(R.string.strip), style = MaterialTheme.typography.labelLarge)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        strips.forEach { s ->
                            FilterChip(selected = s.id == stripId, onClick = { stripId = s.id; outlets = setOf(1) }, label = { Text(stripName(s)) })
                        }
                    }
                }
                Text(stringResource(R.string.outlets), style = MaterialTheme.typography.labelLarge)
                OutletPicker(strip, outlets) { outlets = it }
                Text(stringResource(R.string.watch_metric), style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = metric == Schedule.METRIC_POWER, onClick = { metric = Schedule.METRIC_POWER },
                        label = { Text(stringResource(R.string.watch_power)) })
                    FilterChip(selected = metric == Schedule.METRIC_TEMP, onClick = { metric = Schedule.METRIC_TEMP; above = true },
                        label = { Text(stringResource(R.string.watch_temp)) })
                }
                if (metric == Schedule.METRIC_POWER) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(selected = !above, onClick = { above = false }, label = { Text(stringResource(R.string.watch_below)) })
                        FilterChip(selected = above, onClick = { above = true }, label = { Text(stringResource(R.string.watch_above)) })
                    }
                }
                OutlinedTextField(
                    value = valueText,
                    onValueChange = { valueText = it.filter { c -> c.isDigit() || c == '.' }.take(6) },
                    label = { Text(stringResource(if (metric == Schedule.METRIC_TEMP) R.string.watch_value_temp else R.string.watch_value_power)) },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(stringResource(R.string.watch_for), style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    MinutesField(timeText, "", Modifier.weight(1f)) { timeText = it }
                    FilterChip(selected = !inMinutes, onClick = { inMinutes = false }, label = { Text(stringResource(R.string.unit_seconds)) })
                    FilterChip(selected = inMinutes, onClick = { inMinutes = true }, label = { Text(stringResource(R.string.unit_minutes)) })
                }
                Text(stringResource(R.string.watch_action), style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = action == Schedule.ACTION_OFF, onClick = { action = Schedule.ACTION_OFF },
                        label = { Text(stringResource(R.string.watch_off)) })
                    FilterChip(selected = action == Schedule.ACTION_ALERT, onClick = { action = Schedule.ACTION_ALERT },
                        label = { Text(stringResource(R.string.watch_alert)) })
                }
            }
        },
        confirmButton = {
            TextButton(enabled = valid, onClick = {
                onSave(
                    Schedule(
                        id = initial?.id.orEmpty(),
                        stripId = strip.id,
                        outlets = outlets.sorted(),
                        enabled = initial?.enabled ?: true,
                        kind = Schedule.KIND_WATCH,
                        metric = metric,
                        above = metric == Schedule.METRIC_TEMP || above,
                        value = value ?: 0.0,
                        seconds = seconds ?: 0,
                        action = action,
                    )
                )
            }) { Text(stringResource(R.string.save)) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel)) } },
    )
}

@Composable
private fun MinutesField(value: String, label: String, modifier: Modifier, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = { onChange(it.filter(Char::isDigit).take(4)) },
        label = if (label.isEmpty()) null else ({ Text(label) }),
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = modifier,
    )
}

/** A scene: for each outlet choose "leave", "on" or "off". */
@Composable
private fun SceneEditor(strips: List<Strip>, initial: Scene?, onDismiss: () -> Unit, onSave: (Scene) -> Unit) {
    var name by remember { mutableStateOf(initial?.name.orEmpty()) }
    var icon by remember { mutableStateOf(initial?.icon ?: SceneEmojis.first()) }
    // key "stripId/outlet" -> true (on) / false (off); missing = leave as it is
    var actions by remember {
        mutableStateOf<Map<String, Boolean>>(initial?.actions?.associate { "${it.stripId}/${it.outlet}" to it.turnOn } ?: emptyMap())
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(if (initial == null) R.string.new_scene else R.string.edit_scene)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it.take(30) },
                    label = { Text(stringResource(R.string.scene_name)) },
                    placeholder = { Text(stringResource(R.string.scene_name_hint)) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    SceneEmojis.forEach { e ->
                        FilterChip(selected = e == icon, onClick = { icon = e }, label = { Text(e, fontSize = 18.sp) })
                    }
                }
                Text(stringResource(R.string.scene_explain), color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
                strips.forEach { strip ->
                    Text(stripName(strip), style = MaterialTheme.typography.labelLarge)
                    strip.outlets.forEach { o ->
                        val key = "${strip.id}/${o.index}"
                        val current = actions[key]
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(deviceEmoji(o.icon) + " " + outletName(o), Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                            FilterChip(selected = current == true, onClick = { actions = if (current == true) actions - key else actions + (key to true) },
                                label = { Text(stringResource(R.string.state_on)) })
                            Spacer(Modifier.width(6.dp))
                            FilterChip(selected = current == false, onClick = { actions = if (current == false) actions - key else actions + (key to false) },
                                label = { Text(stringResource(R.string.state_off)) })
                        }
                    }
                    Spacer(Modifier.height(4.dp))
                }
            }
        },
        confirmButton = {
            TextButton(enabled = name.isNotBlank() && actions.isNotEmpty(), onClick = {
                onSave(Scene(initial?.id.orEmpty(), name.trim(), icon, actions.map { (k, on) ->
                    SceneAction(k.substringBefore('/'), k.substringAfter('/').toInt(), on)
                }))
            }) { Text(stringResource(R.string.save)) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel)) } },
    )
}
