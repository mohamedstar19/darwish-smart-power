package com.darwish.smartpower.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatDelegate
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Switch
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
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.os.LocaleListCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.darwish.smartpower.BuildConfig
import com.darwish.smartpower.R
import com.darwish.smartpower.security.Biometric
import kotlinx.coroutines.launch

@Composable
fun SettingsScreen(vm: AppViewModel, onSetup: () -> Unit) {
    val state by vm.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val uri = LocalUriHandler.current
    var address by rememberSaveable { mutableStateOf(state.addressText) }
    var token by rememberSaveable { mutableStateOf(state.token) }
    var showToken by rememberSaveable { mutableStateOf(false) }
    var addressError by remember { mutableStateOf(false) }
    var testing by remember { mutableStateOf(false) }
    var testResult by remember { mutableStateOf<String?>(null) }
    var notify by remember { mutableStateOf(vm.prefs.notifications) }
    var appLock by remember { mutableStateOf(vm.prefs.appLock) }
    val askNotify = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        notify = granted
        vm.setNotifications(granted)
    }

    Column(
        Modifier.imePadding().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text(stringResource(R.string.tab_settings), style = MaterialTheme.typography.headlineMedium)

        // ---- connection
        GlassCard(Modifier.fillMaxWidth()) {
            CardTitle("🌐", stringResource(R.string.section_server))
            OutlinedTextField(
                value = address,
                onValueChange = { address = it; addressError = false; testResult = null },
                label = { Text(stringResource(R.string.server_address)) },
                placeholder = { Text(com.darwish.smartpower.data.Prefs.DEFAULT_ADDRESS) },
                supportingText = { Text(stringResource(if (addressError) R.string.settings_bad_address else R.string.server_address_hint)) },
                isError = addressError,
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = token,
                onValueChange = { token = it; testResult = null },
                label = { Text(stringResource(R.string.token)) },
                supportingText = { Text(stringResource(R.string.token_hint)) },
                singleLine = true,
                visualTransformation = if (showToken) VisualTransformation.None else PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                trailingIcon = {
                    TextButton(onClick = { showToken = !showToken }) { Text(stringResource(if (showToken) R.string.hide else R.string.show)) }
                },
                modifier = Modifier.fillMaxWidth(),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Button(onClick = { if (!vm.saveSettings(address, token)) addressError = true }) { Text(stringResource(R.string.save)) }
                OutlinedButton(enabled = !testing, onClick = {
                    testing = true
                    testResult = null
                    scope.launch {
                        testResult = vm.test(address, token).format(context)
                        testing = false
                    }
                }) { Text(stringResource(R.string.test), color = Glass.Text) }
                if (testing) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
            }
            testResult?.let { Spacer(Modifier.height(8.dp)); Text(it, color = Glass.TextSoft) }
        }

        // ---- family: the owner manages it, members see who they are
        if (state.server != null) {
            if (state.me.isOwner) FamilyCard(vm, state) else SignedInAsCard(state.me)
        }

        // ---- strips the owner blocked; one tap lets a strip in again
        val blocked = state.server?.blockedStrips.orEmpty()
        if (state.me.isOwner && blocked.isNotEmpty()) {
            GlassCard(Modifier.fillMaxWidth()) {
                CardTitle("⛔", stringResource(R.string.blocked_strips_title))
                blocked.forEach { id ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(stringResource(R.string.new_strip_line, ltr(id.takeLast(6))), modifier = Modifier.weight(1f))
                        TextButton(onClick = { vm.approveStrip(id) }) { Text(stringResource(R.string.unblock)) }
                    }
                }
            }
        }

        // ---- bill and alert limits (kept on the server, owner only)
        state.server?.settings?.takeIf { state.me.isOwner }?.let { current ->
            var price by remember(current) { mutableStateOf(number(current.pricePerKwh, 2)) }
            var maxTemp by remember(current) { mutableStateOf(current.maxTempC.toInt().toString()) }
            var maxWatts by remember(current) { mutableStateOf(current.maxWatts.toInt().toString()) }
            GlassCard(Modifier.fillMaxWidth()) {
                CardTitle("💰", stringResource(R.string.section_bill))
                NumberField(price, stringResource(R.string.price_per_kwh, currencyLabel(current.currency)), decimal = true) { price = it }
                Spacer(Modifier.height(4.dp))
                Text(stringResource(R.string.price_hint), color = Glass.TextFaint, style = MaterialTheme.typography.labelSmall)
                Spacer(Modifier.height(10.dp))
                CardTitle("🚨", stringResource(R.string.section_alert_limits))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    NumberField(maxTemp, stringResource(R.string.max_temp), Modifier.weight(1f)) { maxTemp = it }
                    NumberField(maxWatts, stringResource(R.string.max_watts), Modifier.weight(1f)) { maxWatts = it }
                }
                Spacer(Modifier.height(8.dp))
                Button(onClick = {
                    vm.saveServerSettings(current.copy(
                        pricePerKwh = price.toDoubleOrNull() ?: current.pricePerKwh,
                        maxTempC = maxTemp.toDoubleOrNull() ?: current.maxTempC,
                        maxWatts = maxWatts.toDoubleOrNull() ?: current.maxWatts,
                    ))
                }) { Text(stringResource(R.string.save)) }
            }
        }

        // ---- Alexa (on the server, inside the home network)
        state.server?.settings?.takeIf { state.me.isOwner }?.let { current ->
            GlassCard(Modifier.fillMaxWidth()) {
                CardTitle("🗣️", stringResource(R.string.section_alexa))
                SwitchRow(stringResource(R.string.alexa_enable), stringResource(R.string.alexa_hint), current.alexa) { on ->
                    vm.saveServerSettings(current.copy(alexa = on))
                }
                Text(stringResource(R.string.alexa_steps), color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
            }
        }

        // ---- this phone
        GlassCard(Modifier.fillMaxWidth()) {
            CardTitle("📱", stringResource(R.string.section_phone))
            SwitchRow(stringResource(R.string.notifications), stringResource(R.string.notifications_hint), notify) { on ->
                if (on && Build.VERSION.SDK_INT >= 33) {
                    askNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
                } else {
                    notify = on
                    vm.setNotifications(on)
                }
            }
            if (Biometric.available(context)) {
                val title = stringResource(R.string.lock_title)
                SwitchRow(stringResource(R.string.app_lock), stringResource(R.string.app_lock_hint), appLock) { on ->
                    // confirm with the fingerprint before turning the lock on or off
                    Biometric.authenticate(context, title, null) { ok ->
                        if (ok) {
                            appLock = on
                            vm.prefs.appLock = on
                        }
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            Text(stringResource(R.string.section_language), fontWeight = FontWeight.SemiBold)
            LanguagePicker()
        }

        // ---- new strip
        GlassCard(Modifier.fillMaxWidth()) {
            CardTitle("➕", stringResource(R.string.section_setup))
            Text(stringResource(R.string.setup_intro), color = Glass.TextSoft)
            Spacer(Modifier.height(10.dp))
            Button(onClick = onSetup) { Text(stringResource(R.string.setup_strip)) }
        }

        // ---- about
        Column(Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 16.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(stringResource(R.string.app_name) + " " + ltr(BuildConfig.VERSION_NAME), color = Glass.TextSoft,
                style = MaterialTheme.typography.labelMedium)
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.developed_by) + " ", color = Glass.TextFaint, style = MaterialTheme.typography.labelMedium)
                TextButton(onClick = { uri.openUri(OFFICIAL_SITE) }) {
                    Text("Darwish Tech · darwish-tech.com", color = Glass.Orange, fontWeight = FontWeight.Bold,
                        style = MaterialTheme.typography.labelMedium, textAlign = TextAlign.Center)
                }
            }
            TextButton(onClick = { uri.openUri(PRIVACY_POLICY) }) {
                Text(stringResource(R.string.privacy_policy), color = Glass.TextSoft, style = MaterialTheme.typography.labelMedium)
            }
        }
    }
    LaunchedEffect(state.addressText, state.token) {
        address = state.addressText
        token = state.token
    }
}

const val OFFICIAL_SITE = "https://darwish-tech.com"
const val PRIVACY_POLICY = "https://power.darwish-tech.com/privacy"

@Composable
private fun CardTitle(emoji: String, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(bottom = 8.dp)) {
        Text(emoji)
        Spacer(Modifier.width(8.dp))
        Text(text, style = SectionTitleStyle)
    }
}

@Composable
private fun NumberField(value: String, label: String, modifier: Modifier = Modifier.fillMaxWidth(), decimal: Boolean = false,
                        onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = { v -> onChange(v.filter { it.isDigit() || (decimal && it == '.') }.take(8)) },
        label = { Text(label) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = if (decimal) KeyboardType.Decimal else KeyboardType.Number),
        modifier = modifier,
    )
}

@Composable
private fun SwitchRow(title: String, hint: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Text(hint, color = Glass.TextSoft, style = MaterialTheme.typography.labelSmall)
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

/** Phone language, Arabic or English. AppCompat stores the choice and recreates the screen. */
@Composable
private fun LanguagePicker() {
    val current = AppCompatDelegate.getApplicationLocales().toLanguageTags()
    val options = listOf("" to R.string.lang_system, "ar" to R.string.lang_ar, "en" to R.string.lang_en)
    Column(Modifier.selectableGroup()) {
        options.forEach { (tag, label) ->
            val selected = if (tag.isEmpty()) current.isEmpty() else current.startsWith(tag)
            Row(
                Modifier.fillMaxWidth()
                    .selectable(selected = selected, role = Role.RadioButton, onClick = {
                        AppCompatDelegate.setApplicationLocales(
                            if (tag.isEmpty()) LocaleListCompat.getEmptyLocaleList() else LocaleListCompat.forLanguageTags(tag)
                        )
                    })
                    .padding(vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = selected, onClick = null)
                Text(stringResource(label), modifier = Modifier.padding(start = 12.dp))
            }
        }
    }
}

