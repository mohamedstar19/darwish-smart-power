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
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.darwish.smartpower.R

private const val MAX_NAME = 40
private const val MAX_MINUTES = 7 * 24 * 60
private val PRESETS = listOf(15, 30, 60, 120, 240, 480)

@Composable
fun RenameDialog(target: Target, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var text by rememberSaveable { mutableStateOf(target.name.orEmpty()) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.rename_title)) },
        text = {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it.take(MAX_NAME) },
                label = { Text(stringResource(R.string.name)) },
                placeholder = { Text(target.defaultName) },
                supportingText = { Text(stringResource(R.string.rename_hint)) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = { TextButton(onClick = { onSave(text) }) { Text(stringResource(R.string.save)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel)) } },
    )
}

@Composable
fun TimerDialog(
    target: Target,
    onDismiss: () -> Unit,
    onStart: (minutes: Int, turnOn: Boolean) -> Unit,
    onCancelTimer: () -> Unit,
) {
    var turnOn by rememberSaveable { mutableStateOf(!target.isOn) }     // usually: switch to the other state
    var minutesText by rememberSaveable { mutableStateOf("30") }
    val minutes = minutesText.toIntOrNull()
    val valid = minutes != null && minutes in 1..MAX_MINUTES

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.timer_title, target.name ?: target.defaultName)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                target.timer?.let { TimerLine(it) }
                Text(stringResource(R.string.timer_action), style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = !turnOn, onClick = { turnOn = false }, label = { Text(stringResource(R.string.turn_off)) })
                    FilterChip(selected = turnOn, onClick = { turnOn = true }, label = { Text(stringResource(R.string.turn_on)) })
                }
                Text(stringResource(R.string.timer_after), style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    PRESETS.forEach { m ->
                        FilterChip(
                            selected = minutes == m,
                            onClick = { minutesText = m.toString() },
                            label = { Text(durationLabel(m)) },
                        )
                    }
                }
                OutlinedTextField(
                    value = minutesText,
                    onValueChange = { minutesText = it.filter(Char::isDigit).take(5) },
                    label = { Text(stringResource(R.string.minutes)) },
                    isError = !valid,
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onStart(minutes ?: 0, turnOn) }, enabled = valid) {
                Text(stringResource(R.string.start))
            }
        },
        dismissButton = {
            Row {
                if (target.timer != null) {
                    TextButton(onClick = onCancelTimer) { Text(stringResource(R.string.cancel_timer)) }
                }
                TextButton(onClick = onDismiss) { Text(stringResource(R.string.close)) }
            }
        },
    )
}

@Composable
private fun durationLabel(minutes: Int): String =
    if (minutes % 60 == 0) stringResource(R.string.hours_short, minutes / 60)
    else stringResource(R.string.minutes_short, minutes)
