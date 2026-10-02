@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.darwish.smartpower.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import com.darwish.smartpower.R
import com.darwish.smartpower.data.Outlet
import com.darwish.smartpower.data.Strip
import com.darwish.smartpower.data.StripTimer
import kotlinx.coroutines.launch

/** What a rename or timer dialog is about. [outlet] 0 = the whole strip. */
data class Target(
    val stripId: String,
    val outlet: Int,
    val name: String?,
    val defaultName: String,
    val isOn: Boolean,
    val timer: StripTimer?,
)

@Composable
fun HomeScreen(vm: AppViewModel, snackbar: SnackbarHostState, onSettings: () -> Unit, onSetup: () -> Unit) {
    val state by vm.state.collectAsStateWithLifecycle()
    val lifecycleOwner = LocalLifecycleOwner.current
    val scope = rememberCoroutineScope()
    var renaming by remember { mutableStateOf<Target?>(null) }
    var timing by remember { mutableStateOf<Target?>(null) }

    LaunchedEffect(lifecycleOwner) {
        lifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) { vm.pollForever() }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.app_name)) },
                actions = {
                    IconButton(onClick = onSettings) {
                        Icon(Icons.Filled.Settings, contentDescription = stringResource(R.string.settings))
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        LazyColumn(
            modifier = Modifier.padding(padding),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            val problem = state.problem
            when {
                !state.loaded -> item {
                    Box(Modifier.fillMaxWidth().padding(48.dp), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator()
                    }
                }
                problem == Problem.NOT_CONFIGURED -> item {
                    MessageCard(
                        title = stringResource(R.string.welcome_title),
                        body = stringResource(R.string.welcome_body),
                        action = stringResource(R.string.open_settings),
                        onAction = onSettings,
                    )
                }
                state.strips.isEmpty() && problem != null -> item {
                    MessageCard(
                        title = stringResource(R.string.problem_title),
                        body = stringResource(problemText(problem)),
                        action = stringResource(R.string.retry),
                        onAction = { scope.launch { vm.refresh() } },
                        secondary = stringResource(R.string.open_settings),
                        onSecondary = onSettings,
                    )
                }
                state.strips.isEmpty() -> item {
                    MessageCard(
                        title = stringResource(R.string.no_strips_title),
                        body = stringResource(R.string.no_strips_body),
                        action = stringResource(R.string.setup_strip),
                        onAction = onSetup,
                    )
                }
                else -> {
                    if (problem != null) item {
                        Surface(color = MaterialTheme.colorScheme.errorContainer, shape = RoundedCornerShape(12.dp)) {
                            Text(
                                stringResource(R.string.stale_banner) + " " + stringResource(problemText(problem)),
                                modifier = Modifier.fillMaxWidth().padding(12.dp),
                                color = MaterialTheme.colorScheme.onErrorContainer,
                                style = MaterialTheme.typography.bodyMedium,
                            )
                        }
                    }
                    items(state.strips, key = { it.id }) { strip ->
                        StripCard(
                            strip = strip,
                            pending = state.pending,
                            onSwitch = vm::switch,
                            onRename = { renaming = it },
                            onTimer = { timing = it },
                        )
                    }
                }
            }
        }
    }

    renaming?.let { t ->
        RenameDialog(t, onDismiss = { renaming = null }, onSave = { name ->
            vm.rename(t.stripId, t.outlet, name)
            renaming = null
        })
    }
    timing?.let { t ->
        TimerDialog(
            target = t,
            onDismiss = { timing = null },
            onStart = { minutes, turnOn ->
                vm.setTimer(t.stripId, t.outlet, minutes, turnOn)
                timing = null
            },
            onCancelTimer = {
                vm.setTimer(t.stripId, t.outlet, 0, false)
                timing = null
            },
        )
    }
}

fun problemText(problem: Problem): Int = when (problem) {
    Problem.NOT_CONFIGURED -> R.string.welcome_body
    Problem.UNREACHABLE -> R.string.problem_unreachable
    Problem.BAD_TOKEN -> R.string.problem_bad_token
    Problem.SERVER -> R.string.problem_server
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
    ElevatedCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(title, style = MaterialTheme.typography.titleLarge)
            Text(body, style = MaterialTheme.typography.bodyMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onAction) { Text(action) }
                if (secondary != null) OutlinedButton(onClick = onSecondary) { Text(secondary) }
            }
        }
    }
}

@Composable
private fun StripCard(
    strip: Strip,
    pending: Set<String>,
    onSwitch: (String, Int, Boolean) -> Unit,
    onRename: (Target) -> Unit,
    onTimer: (Target) -> Unit,
) {
    val defaultName = stringResource(R.string.default_strip_name, strip.id.takeLast(6))
    val stripTarget = Target(strip.id, 0, strip.name, defaultName, strip.anyOn, strip.timer)
    val allBusy = AppViewModel.key(strip.id, 0) in pending

    ElevatedCard(Modifier.fillMaxWidth().alpha(if (strip.online) 1f else 0.65f)) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Row(
                        Modifier.clickable { onRename(stripTarget) },
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            strip.name ?: defaultName,
                            style = MaterialTheme.typography.titleLarge,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f, fill = false),
                        )
                        Icon(
                            Icons.Filled.Edit,
                            contentDescription = stringResource(R.string.rename),
                            modifier = Modifier.padding(start = 6.dp).size(16.dp),
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Spacer(Modifier.height(4.dp))
                    StatusPill(strip.online)
                }
                val shown by animateFloatAsState(strip.watts.toFloat(), label = "watts")
                Text(
                    watts(shown.toDouble()),
                    style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.Bold,
                )
            }

            Spacer(Modifier.height(12.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Pill(kwh(strip.kwh))
                strip.volts?.let { Pill(volts(it)) }
                strip.amps?.let { Pill(amps(it)) }
                strip.rssi?.let { Pill(rssi(it)) }
                if (!strip.online) Pill(stringResource(R.string.last_seen, clock(strip.lastSeenEpochSeconds)))
            }
            strip.timer?.let {
                Spacer(Modifier.height(8.dp))
                TimerLine(it)
            }

            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                FilledTonalButton(
                    onClick = { onSwitch(strip.id, 0, true) },
                    enabled = strip.online && !allBusy,
                    modifier = Modifier.weight(1f),
                ) { Text(stringResource(R.string.all_on), maxLines = 1) }
                OutlinedButton(
                    onClick = { onSwitch(strip.id, 0, false) },
                    enabled = strip.online && !allBusy,
                    modifier = Modifier.weight(1f),
                ) { Text(stringResource(R.string.all_off), maxLines = 1) }
                TextButton(onClick = { onTimer(stripTarget) }) { Text(stringResource(R.string.timer)) }
            }

            Spacer(Modifier.height(12.dp))
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                strip.outlets.chunked(2).forEach { pair ->
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        pair.forEach { outlet ->
                            val outletDefault = stringResource(R.string.default_outlet_name, outlet.index)
                            val target = Target(strip.id, outlet.index, outlet.name, outletDefault, outlet.on, outlet.timer)
                            OutletTile(
                                outlet = outlet,
                                name = outlet.name ?: outletDefault,
                                enabled = strip.online,
                                busy = allBusy || AppViewModel.key(strip.id, outlet.index) in pending,
                                onSwitch = { on -> onSwitch(strip.id, outlet.index, on) },
                                onRename = { onRename(target) },
                                onTimer = { onTimer(target) },
                                modifier = Modifier.weight(1f),
                            )
                        }
                        if (pair.size == 1) Spacer(Modifier.weight(1f))
                    }
                }
            }
        }
    }
}

@Composable
private fun OutletTile(
    outlet: Outlet,
    name: String,
    enabled: Boolean,
    busy: Boolean,
    onSwitch: (Boolean) -> Unit,
    onRename: () -> Unit,
    onTimer: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = MaterialTheme.colorScheme
    val container by animateColorAsState(if (outlet.on) colors.secondaryContainer else colors.surfaceVariant, label = "tile")
    val content by animateColorAsState(if (outlet.on) colors.onSecondaryContainer else colors.onSurfaceVariant, label = "tileText")
    var menu by remember { mutableStateOf(false) }

    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = container, contentColor = content),
        shape = RoundedCornerShape(18.dp),
    ) {
        Column(Modifier.padding(start = 12.dp, end = 4.dp, top = 4.dp, bottom = 12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    name,
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f).padding(top = 8.dp),
                )
                Box {
                    IconButton(onClick = { menu = true }) {
                        Icon(Icons.Filled.MoreVert, contentDescription = stringResource(R.string.more))
                    }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        DropdownMenuItem(text = { Text(stringResource(R.string.rename)) }, onClick = { menu = false; onRename() })
                        DropdownMenuItem(text = { Text(stringResource(R.string.timer)) }, onClick = { menu = false; onTimer() })
                    }
                }
            }
            val details = buildList {
                add(watts(outlet.watts))
                outlet.tempC?.let { add(celsius(it)) }
            }.joinToString(" · ")
            Text(details, style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(end = 8.dp)) {
                Text(
                    stringResource(if (outlet.on) R.string.state_on else R.string.state_off),
                    style = MaterialTheme.typography.labelLarge,
                    modifier = Modifier.weight(1f),
                )
                if (busy) {
                    CircularProgressIndicator(Modifier.padding(end = 8.dp).size(18.dp), strokeWidth = 2.dp)
                }
                Switch(checked = outlet.on, onCheckedChange = onSwitch, enabled = enabled && !busy)
            }
            outlet.timer?.let {
                Spacer(Modifier.height(4.dp))
                TimerLine(it)
            }
        }
    }
}

@Composable
private fun StatusPill(online: Boolean) {
    val colors = MaterialTheme.colorScheme
    Surface(
        color = if (online) colors.secondaryContainer else colors.surfaceVariant,
        contentColor = if (online) colors.onSecondaryContainer else colors.onSurfaceVariant,
        shape = RoundedCornerShape(50),
    ) {
        Text(
            stringResource(if (online) R.string.online else R.string.offline),
            style = MaterialTheme.typography.labelMedium,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 2.dp),
        )
    }
}

@Composable
private fun Pill(text: String) {
    Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(8.dp)) {
        Text(
            text,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
        )
    }
}

@Composable
fun TimerLine(timer: StripTimer) {
    Text(
        stringResource(if (timer.turnOn) R.string.timer_turns_on else R.string.timer_turns_off, clock(timer.atEpochSeconds)),
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.primary,
    )
}
