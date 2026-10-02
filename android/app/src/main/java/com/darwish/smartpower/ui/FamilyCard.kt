@file:OptIn(ExperimentalLayoutApi::class)

package com.darwish.smartpower.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.darwish.smartpower.R
import com.darwish.smartpower.data.Me
import com.darwish.smartpower.data.Member
import com.darwish.smartpower.data.Strip

/** Owner only: who else can use the strips, with what access, and invites to send them. */
@Composable
fun FamilyCard(vm: AppViewModel, state: UiState) {
    var adding by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { vm.loadMembers() }
    GlassCard(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("👨‍👩‍👧")
            Spacer(Modifier.width(8.dp))
            Text(stringResource(R.string.family_title), style = SectionTitleStyle, modifier = Modifier.weight(1f))
        }
        Spacer(Modifier.height(4.dp))
        Text(stringResource(R.string.family_hint), color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(10.dp))
        if (state.members.isEmpty()) {
            Text(stringResource(R.string.family_empty), color = Glass.TextFaint, style = MaterialTheme.typography.bodySmall)
        }
        state.members.forEach { m -> MemberRow(vm, m, state.strips) }
        Spacer(Modifier.height(10.dp))
        Button(onClick = { adding = true }, enabled = state.strips.isNotEmpty()) { Text("＋ " + stringResource(R.string.family_add)) }
    }
    if (adding) {
        AddMemberDialog(state.strips, onDismiss = { adding = false }) { name, role, strips ->
            vm.addMember(name, role, strips)
            adding = false
        }
    }
    state.invite?.let { InviteDialog(it, state.addressText, onClose = vm::dismissInvite) }
}

@Composable
private fun MemberRow(vm: AppViewModel, m: Member, strips: List<Strip>) {
    var menu by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(if (m.role == Me.ROLE_VIEW) "👁" else "🎛", fontSize = 20.sp)
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(m.name, fontWeight = FontWeight.SemiBold)
            Text(
                stringResource(if (m.role == Me.ROLE_VIEW) R.string.role_view else R.string.role_control) + " · " +
                    (if (m.strips.isEmpty()) stringResource(R.string.family_all_strips)
                    else strips.filter { it.id in m.strips }.map { stripName(it) }.joinToString("، ")),
                color = Glass.TextSoft, style = MaterialTheme.typography.labelSmall, maxLines = 2, overflow = TextOverflow.Ellipsis,
            )
        }
        IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, contentDescription = null, tint = Glass.TextSoft) }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            val other = if (m.role == Me.ROLE_VIEW) Me.ROLE_CONTROL else Me.ROLE_VIEW
            DropdownMenuItem(
                text = { Text(stringResource(if (other == Me.ROLE_VIEW) R.string.family_make_view else R.string.family_make_control)) },
                onClick = { menu = false; vm.changeMemberRole(m, other) },
            )
            DropdownMenuItem(text = { Text(stringResource(R.string.family_new_code)) }, onClick = { menu = false; vm.renewMemberToken(m) })
            DropdownMenuItem(text = { Text(stringResource(R.string.family_remove), color = Glass.Red) }, onClick = { menu = false; vm.removeMember(m) })
        }
    }
}

@Composable
private fun AddMemberDialog(strips: List<Strip>, onDismiss: () -> Unit, onAdd: (String, String, List<String>) -> Unit) {
    var name by remember { mutableStateOf("") }
    var role by remember { mutableStateOf(Me.ROLE_CONTROL) }
    var chosen by remember { mutableStateOf(emptySet<String>()) }        // empty = all strips
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.family_add)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value = name, onValueChange = { name = it.take(30) }, singleLine = true,
                    label = { Text(stringResource(R.string.name)) }, placeholder = { Text(stringResource(R.string.family_name_hint)) },
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(stringResource(R.string.family_access), style = MaterialTheme.typography.labelLarge)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = role == Me.ROLE_CONTROL, onClick = { role = Me.ROLE_CONTROL },
                        label = { Text("🎛 " + stringResource(R.string.role_control)) })
                    FilterChip(selected = role == Me.ROLE_VIEW, onClick = { role = Me.ROLE_VIEW },
                        label = { Text("👁 " + stringResource(R.string.role_view)) })
                }
                Text(stringResource(R.string.family_which_strips), style = MaterialTheme.typography.labelLarge)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = chosen.isEmpty(), onClick = { chosen = emptySet() },
                        label = { Text(stringResource(R.string.family_all_strips)) })
                    strips.forEach { s ->
                        FilterChip(selected = s.id in chosen, onClick = { chosen = if (s.id in chosen) chosen - s.id else chosen + s.id },
                            label = { Text(stripName(s)) })
                    }
                }
            }
        },
        confirmButton = {
            TextButton(enabled = name.isNotBlank(), onClick = { onAdd(name, role, chosen.toList()) }) { Text(stringResource(R.string.family_create)) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel)) } },
    )
}

/** Shows the new member's code once, with a ready-made message to send. */
@Composable
private fun InviteDialog(invite: Invite, address: String, onClose: () -> Unit) {
    val context = LocalContext.current
    val server = com.darwish.smartpower.data.ServerAddress.parse(address)
    val message = stringResource(
        R.string.family_invite_message, invite.name, server?.url("app.apk").orEmpty(), server?.display() ?: address,
        invite.token, panelLink(address, invite.token),
    )
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text(stringResource(R.string.family_invite_title, invite.name)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.family_invite_once), color = Glass.Amber, style = MaterialTheme.typography.bodySmall)
                Text(ltr(invite.token), fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                Text(stringResource(R.string.family_invite_explain), color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = {
            TextButton(onClick = { share(context, message) }) { Text(stringResource(R.string.share)) }
        },
        dismissButton = {
            Row {
                TextButton(onClick = { copy(context, invite.token) }) { Text(stringResource(R.string.copy)) }
                TextButton(onClick = onClose) { Text(stringResource(R.string.close)) }
            }
        },
    )
}

private fun panelLink(address: String, token: String): String {
    val base = com.darwish.smartpower.data.ServerAddress.parse(address)?.url("panel") ?: return ""
    return "$base?token=$token"
}

private fun share(context: Context, text: String) {
    val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
    context.startActivity(Intent.createChooser(send, null))
}

private fun copy(context: Context, text: String) {
    context.getSystemService(ClipboardManager::class.java)?.setPrimaryClip(ClipData.newPlainText("code", text))
}

/** For family members: who they are signed in as. */
@Composable
fun SignedInAsCard(me: Me) {
    GlassCard(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(if (me.role == Me.ROLE_VIEW) "👁" else "🎛", fontSize = 22.sp)
            Spacer(Modifier.width(10.dp))
            Column {
                Text(stringResource(R.string.signed_in_as, me.name ?: ""), fontWeight = FontWeight.SemiBold)
                Text(stringResource(if (me.role == Me.ROLE_VIEW) R.string.role_view_long else R.string.role_control_long),
                    color = Glass.TextSoft, style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}
