@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.darwish.smartpower.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.darwish.smartpower.R
import com.darwish.smartpower.data.Strip
import com.darwish.smartpower.security.Biometric
import kotlinx.coroutines.launch

private const val MAX_MINUTES = 7 * 24 * 60
private val PRESETS = listOf(15, 30, 60, 120, 240, 480)

/** Asks for a locked strip's PIN; uses the fingerprint first when the PIN was saved for it. */
@Composable
fun PinDialog(request: PinRequest, vm: AppViewModel) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var pin by rememberSaveable(request) { mutableStateOf("") }
    var keepForFingerprint by rememberSaveable(request) { mutableStateOf(Biometric.available(context)) }
    var busy by remember(request) { mutableStateOf(false) }
    val saved = vm.prefs.savedPin(request.stripId)
    val title = stringResource(R.string.pin_title, request.stripName)

    LaunchedEffect(request) {
        if (saved != null && !request.wrong) {
            Biometric.authenticate(context, title, null) { ok ->
                if (ok) scope.launch { busy = true; request.retry(saved); busy = false }
            }
        }
    }
    AlertDialog(
        onDismissRequest = vm::dismissPin,
        title = { Text(title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (request.wrong) Text(stringResource(R.string.pin_wrong), color = Glass.Red)
                OutlinedTextField(
                    value = pin,
                    onValueChange = { pin = it.filter(Char::isDigit).take(8) },
                    label = { Text(stringResource(R.string.pin)) },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                    modifier = Modifier.fillMaxWidth(),
                )
                if (Biometric.available(context)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = keepForFingerprint, onCheckedChange = { keepForFingerprint = it })
                        Text(stringResource(R.string.pin_use_fingerprint), style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        },
        confirmButton = {
            TextButton(enabled = pin.length >= 4 && !busy, onClick = {
                scope.launch {
                    busy = true
                    if (keepForFingerprint) vm.rememberPinForFingerprint(request.stripId, pin)
                    request.retry(pin)
                    busy = false
                }
            }) { Text(stringResource(R.string.unlock)) }
        },
        dismissButton = { TextButton(onClick = vm::dismissPin) { Text(stringResource(R.string.cancel)) } },
    )
}

@Composable
fun RenameDialog(current: String?, placeholder: String, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var text by rememberSaveable { mutableStateOf(current.orEmpty()) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.rename_title)) },
        text = {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it.take(40) },
                label = { Text(stringResource(R.string.name)) },
                placeholder = { Text(placeholder) },
                supportingText = { Text(stringResource(R.string.rename_hint)) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = { TextButton(onClick = { onSave(text) }) { Text(stringResource(R.string.save)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel)) } },
    )
}

/** One-off timer for one or more outlets of a strip. */
@Composable
fun TimerDialog(strip: Strip, preselected: Set<Int>, onDismiss: () -> Unit, onStart: (List<Int>, Int, Boolean) -> Unit) {
    var chosen by remember { mutableStateOf(preselected) }
    var turnOn by rememberSaveable { mutableStateOf(!strip.outlets.filter { it.index in preselected || 0 in preselected }.any { it.on }) }
    var minutesText by rememberSaveable { mutableStateOf("30") }
    val minutes = minutesText.toIntOrNull()
    val valid = minutes != null && minutes in 1..MAX_MINUTES && chosen.isNotEmpty()
    val hasTimers = strip.timer != null || strip.outlets.any { it.timer != null }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.timer_title_simple)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(stringResource(R.string.outlets), style = MaterialTheme.typography.labelLarge)
                OutletPicker(strip, chosen) { chosen = it }
                Text(stringResource(R.string.timer_action), style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = !turnOn, onClick = { turnOn = false }, label = { Text(stringResource(R.string.turn_off)) })
                    FilterChip(selected = turnOn, onClick = { turnOn = true }, label = { Text(stringResource(R.string.turn_on)) })
                }
                Text(stringResource(R.string.timer_after), style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    PRESETS.forEach { m ->
                        FilterChip(selected = minutes == m, onClick = { minutesText = m.toString() }, label = { Text(durationLabel(m)) })
                    }
                }
                OutlinedTextField(
                    value = minutesText,
                    onValueChange = { minutesText = it.filter(Char::isDigit).take(5) },
                    label = { Text(stringResource(R.string.minutes)) },
                    isError = minutes == null || minutes !in 1..MAX_MINUTES,
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(enabled = valid, onClick = { onStart(chosen.sorted(), minutes ?: 0, turnOn) }) {
                Text(stringResource(R.string.start))
            }
        },
        dismissButton = {
            Row {
                if (hasTimers) {
                    TextButton(onClick = { onStart(listOf(0), 0, false) }) { Text(stringResource(R.string.cancel_timers)) }
                }
                TextButton(onClick = onDismiss) { Text(stringResource(R.string.close)) }
            }
        },
    )
}

/** "All" plus each outlet; picking "All" clears the others and the other way round. */
@Composable
fun OutletPicker(strip: Strip, chosen: Set<Int>, onChange: (Set<Int>) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilterChip(selected = 0 in chosen, onClick = { onChange(setOf(0)) }, label = { Text(stringResource(R.string.all_outlets)) })
        strip.outlets.forEach { o ->
            FilterChip(
                selected = o.index in chosen,
                onClick = {
                    val next = (chosen - 0).let { if (o.index in it) it - o.index else it + o.index }
                    onChange(next)
                },
                label = { Text(deviceEmoji(o.icon) + " " + outletName(o)) },
            )
        }
    }
}

/** Set, change or remove a strip's PIN. */
@Composable
fun LockDialog(strip: Strip, onDismiss: () -> Unit, onSave: (pin: String, oldPin: String?, fingerprint: Boolean) -> Unit) {
    val context = LocalContext.current
    var old by rememberSaveable { mutableStateOf("") }
    var pin by rememberSaveable { mutableStateOf("") }
    var fingerprint by rememberSaveable { mutableStateOf(Biometric.available(context)) }
    val pinOk = pin.isEmpty() || pin.length in 4..8
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(if (strip.locked) R.string.lock_change else R.string.lock_set)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.lock_explain), color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
                if (strip.locked) PinField(old, stringResource(R.string.pin_old)) { old = it }
                PinField(pin, stringResource(if (strip.locked) R.string.pin_new_or_empty else R.string.pin_new)) { pin = it }
                if (Biometric.available(context)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = fingerprint, onCheckedChange = { fingerprint = it })
                        Text(stringResource(R.string.pin_use_fingerprint), style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = pinOk && (!strip.locked || old.length >= 4) && (strip.locked || pin.length >= 4),
                onClick = { onSave(pin, old.ifEmpty { null }, fingerprint) },
            ) { Text(stringResource(if (strip.locked && pin.isEmpty()) R.string.lock_remove else R.string.save)) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel)) } },
    )
}

@Composable
private fun PinField(value: String, label: String, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = { onChange(it.filter(Char::isDigit).take(8)) },
        label = { Text(label) },
        singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
fun durationLabel(minutes: Int): String =
    if (minutes % 60 == 0) stringResource(R.string.hours_short, minutes / 60)
    else stringResource(R.string.minutes_short, minutes)
