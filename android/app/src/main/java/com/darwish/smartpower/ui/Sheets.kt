@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.darwish.smartpower.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.darwish.smartpower.R
import com.darwish.smartpower.data.Outlet
import com.darwish.smartpower.data.Strip

/** After a power cut: stays off, back on at once, or after a few minutes (a fridge's compressor wants a pause). */
private val AFTER_POWER_CHOICES = listOf(null, 0, 3, 5, 10, 30)

/** Long-press on an outlet: name, icon, favourite, timer, what to do after a power cut. */
@Composable
fun OutletSheet(vm: AppViewModel, strip: Strip, outlet: Outlet, onClose: () -> Unit) {
    var renaming by remember { mutableStateOf(false) }
    var timer by remember { mutableStateOf(false) }
    ModalBottomSheet(onDismissRequest = onClose, containerColor = Glass.Deep) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp).navigationBarsPadding().verticalScroll(rememberScrollState())) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(deviceEmoji(outlet.icon), fontSize = 34.sp)
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(outletName(outlet), style = MaterialTheme.typography.titleLarge)
                    Text(stripName(strip) + " · " + watts(outlet.watts), color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
                }
                Switch(checked = outlet.on, onCheckedChange = { vm.switch(strip.id, outlet.index, it) })
            }
            Spacer(Modifier.height(16.dp))
            SheetRow("✏️", stringResource(R.string.rename)) { renaming = true }
            SheetRow("⏱", stringResource(R.string.timer)) { timer = true }
            Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("⭐", fontSize = 20.sp)
                Spacer(Modifier.width(14.dp))
                Text(stringResource(R.string.favorite), Modifier.weight(1f))
                Switch(checked = outlet.favorite, onCheckedChange = { vm.setOutletLook(strip.id, outlet.index, favorite = it) })
            }
            Spacer(Modifier.height(12.dp))
            Text(stringResource(R.string.after_power_title), style = SectionTitleStyle)
            Text(stringResource(R.string.after_power_hint), color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(8.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                AFTER_POWER_CHOICES.forEach { minutes ->
                    FilterChip(
                        selected = outlet.afterPower == minutes,
                        onClick = { vm.setAfterPower(strip.id, outlet.index, minutes) },
                        label = {
                            Text(when (minutes) {
                                null -> stringResource(R.string.after_power_off)
                                0 -> stringResource(R.string.after_power_now)
                                else -> stringResource(R.string.after_power_minutes, minutes)
                            })
                        },
                    )
                }
            }
            if (outlet.icon == "fridge" || outlet.icon == "ac") {
                Text(stringResource(R.string.after_power_fridge), color = Glass.Amber, style = MaterialTheme.typography.labelSmall)
            }
            Spacer(Modifier.height(12.dp))
            Text(stringResource(R.string.icon), style = SectionTitleStyle)
            Spacer(Modifier.height(8.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                DeviceIcons.forEach { icon ->
                    val selected = icon.key == outlet.icon
                    Column(
                        Modifier
                            .width(68.dp)
                            .clip(RoundedCornerShape(16.dp))
                            .background(if (selected) Glass.Orange.copy(alpha = 0.22f) else Glass.Fill)
                            .border(1.dp, if (selected) Glass.Orange else Glass.Stroke, RoundedCornerShape(16.dp))
                            .clickable { vm.setOutletLook(strip.id, outlet.index, icon = icon.key) }
                            .padding(vertical = 8.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text(icon.emoji, fontSize = 22.sp)
                        Text(stringResource(icon.label), style = MaterialTheme.typography.labelSmall, maxLines = 1, color = Glass.TextSoft)
                    }
                }
            }
        }
    }
    if (renaming) {
        RenameDialog(outlet.name, stringResource(R.string.default_outlet_name, outlet.index), { renaming = false }) {
            vm.rename(strip.id, outlet.index, it)
            renaming = false
        }
    }
    if (timer) {
        TimerDialog(strip, setOf(outlet.index), { timer = false }) { outlets, minutes, on ->
            vm.setTimer(strip.id, outlets, minutes, on)
            timer = false
        }
    }
}

/** The ⋮ menu of a strip: name, room, timer, PIN lock. */
@Composable
fun StripSheet(vm: AppViewModel, strip: Strip, rooms: List<String>, onClose: () -> Unit) {
    var renaming by remember { mutableStateOf(false) }
    var timer by remember { mutableStateOf(false) }
    var lock by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf(false) }
    val me = vm.state.value.me
    val isOwner = me.isOwner
    var room by rememberSaveable { mutableStateOf(strip.room.orEmpty()) }
    ModalBottomSheet(onDismissRequest = onClose, containerColor = Glass.Deep) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp).navigationBarsPadding().verticalScroll(rememberScrollState())) {
            Text(stripName(strip), style = MaterialTheme.typography.titleLarge)
            Text(listOf(strip.model, strip.firmware, strip.id).filter { it.isNotEmpty() }.joinToString(" · "),
                color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(14.dp))
            SheetRow("✏️", stringResource(R.string.rename_strip)) { renaming = true }
            SheetRow("⏱", stringResource(R.string.timer)) { timer = true }
            SheetRow(if (strip.locked) "🔓" else "🔒", stringResource(if (strip.locked) R.string.lock_change else R.string.lock_set)) { lock = true }
            Spacer(Modifier.height(14.dp))
            Text(stringResource(R.string.room), style = SectionTitleStyle)
            Spacer(Modifier.height(8.dp))
            val suggestions = (rooms + listOf(
                stringResource(R.string.room_living), stringResource(R.string.room_kitchen),
                stringResource(R.string.room_bedroom), stringResource(R.string.room_office),
            )).distinct()
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                suggestions.forEach { r ->
                    FilterChip(selected = room == r, onClick = { room = r; vm.setRoom(strip.id, r) }, label = { Text(r) })
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = room,
                    onValueChange = { room = it.take(30) },
                    label = { Text(stringResource(R.string.room_custom)) },
                    singleLine = true,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { vm.setRoom(strip.id, room) }) { Text(stringResource(R.string.save)) }
            }
            Spacer(Modifier.height(10.dp))
            if (me.canControl) {
                SheetRow("📶", stringResource(R.string.reconnect_wifi)) { onClose(); vm.startReconnect(strip.id) }
            }
            if (isOwner || me.isCustomer) {
                SheetRow("🗑", stringResource(R.string.remove_strip)) { removing = true }
            }
        }
    }
    if (removing) {
        AlertDialog(
            onDismissRequest = { removing = false },
            title = { Text(stringResource(R.string.remove_strip)) },
            text = {
                Text(stringResource(if (isOwner) R.string.remove_strip_body else R.string.remove_strip_body_customer, stripName(strip)))
            },
            confirmButton = {
                TextButton(onClick = { removing = false; onClose(); vm.removeStrip(strip.id, block = false) }) {
                    Text(stringResource(R.string.remove))
                }
            },
            dismissButton = {
                Row {
                    if (isOwner) TextButton(onClick = { removing = false; onClose(); vm.removeStrip(strip.id, block = true) }) {
                        Text(stringResource(R.string.remove_and_block), color = Glass.Red)
                    }
                    TextButton(onClick = { removing = false }) { Text(stringResource(R.string.cancel)) }
                }
            },
        )
    }
    if (renaming) {
        RenameDialog(strip.name, stringResource(R.string.default_strip_name, strip.id.takeLast(6)), { renaming = false }) {
            vm.rename(strip.id, 0, it)
            renaming = false
        }
    }
    if (timer) {
        TimerDialog(strip, setOf(0), { timer = false }) { outlets, minutes, on ->
            vm.setTimer(strip.id, outlets, minutes, on)
            timer = false
        }
    }
    if (lock) {
        LockDialog(strip, { lock = false }) { pin, old, fingerprint ->
            vm.setLock(strip.id, pin, old, fingerprint)
            lock = false
        }
    }
}

@Composable
fun SheetRow(emoji: String, label: String, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).clickable(onClick = onClick).padding(vertical = 12.dp, horizontal = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(28.dp), contentAlignment = Alignment.Center) { Text(emoji, fontSize = 20.sp) }
        Spacer(Modifier.width(12.dp))
        Text(label, fontWeight = FontWeight.Medium)
    }
}
