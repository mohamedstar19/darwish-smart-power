@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package com.darwish.smartpower.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.darwish.smartpower.R
import com.darwish.smartpower.data.NearbyWifi
import com.darwish.smartpower.data.Prefs
import com.darwish.smartpower.data.ScanOutcome
import com.darwish.smartpower.data.SetupProblem
import com.darwish.smartpower.data.SetupResult
import com.darwish.smartpower.data.SetupRules
import kotlinx.coroutines.launch

/** Automatic setup: get ready -> search -> pick the strip and home Wi-Fi -> connecting -> done. */
private enum class Phase { READY, SEARCHING, PICK, CONNECTING, DONE }

@Composable
fun SetupScreen(vm: AppViewModel, snackbar: SnackbarHostState, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val state by vm.state.collectAsStateWithLifecycle()
    // putting a strip the account already has on Wi-Fi again: its setup Wi-Fi ends with the end of its MAC
    val target by vm.reconnect.collectAsStateWithLifecycle()
    val targetStrip = state.strips.firstOrNull { it.id == target }
    val targetCode = target?.takeLast(7)
    var serverIp by rememberSaveable { mutableStateOf(vm.suggestedServerIp()) }
    var serverIpEdited by rememberSaveable { mutableStateOf(false) }
    // the server reports its address once connected; follow it until the user types their own
    LaunchedEffect(state.server?.serverIp) {
        if (!serverIpEdited) serverIp = vm.suggestedServerIp()
    }
    var ssid by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var phase by remember { mutableStateOf(Phase.READY) }
    var scan by remember { mutableStateOf<ScanOutcome?>(null) }
    var chosen by rememberSaveable { mutableStateOf<String?>(null) }
    var result by remember { mutableStateOf<SetupResult?>(null) }
    var manual by rememberSaveable { mutableStateOf(false) }
    var manualBusy by remember { mutableStateOf(false) }
    var manualResult by remember { mutableStateOf<SetupResult?>(null) }

    fun search() {
        phase = Phase.SEARCHING
        result = null
        scope.launch {
            val all = vm.scanForStrips()
            // reconnecting one strip: show just its Wi-Fi when we can tell which it is (its code may come from
            // its second MAC, so if none matches, show every strip in setup mode rather than none)
            val found = if (targetCode != null && all is ScanOutcome.Found) {
                val mine = all.strips.filter { targetCode.endsWith(SetupRules.apCode(it.ssid), ignoreCase = true) }
                if (mine.isNotEmpty()) all.copy(strips = mine) else all
            } else all
            scan = found
            if (found is ScanOutcome.Found && found.strips.none { it.ssid == chosen }) {
                chosen = found.strips.firstOrNull()?.ssid
            }
            phase = Phase.PICK
        }
    }

    val askPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        if (granted.isNotEmpty() && granted.values.all { it }) {
            search()
        } else {
            scan = ScanOutcome.NeedPermission
            phase = Phase.PICK
        }
    }

    fun ready() {
        if (vm.stripFinder.hasPermission()) search() else askPermission.launch(vm.stripFinder.permissions)
    }

    fun add(apSsid: String) {
        phase = Phase.CONNECTING
        result = null
        scope.launch {
            val outcome = vm.provisionFound(apSsid, serverIp, ssid, password)
            result = outcome
            phase = if (outcome is SetupResult.Done) Phase.DONE else Phase.PICK
        }
    }

    Scaffold(
        containerColor = Color.Transparent,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent),
                title = { Text(stringResource(if (target != null) R.string.reconnect_wifi else R.string.setup_title)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.back))
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (target != null && phase != Phase.DONE) {
                GlassCard(Modifier.fillMaxWidth()) {
                    Text("📶 " + (targetStrip?.let { stripName(it) } ?: target.orEmpty()), style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(6.dp))
                    Text(stringResource(R.string.reconnect_hint), color = Glass.TextSoft, style = MaterialTheme.typography.bodyMedium)
                }
            }
            when (phase) {
                Phase.READY -> ReadyCard(onReady = ::ready)
                Phase.SEARCHING -> BusyCard("📶", stringResource(R.string.setup_searching), emptyList())
                Phase.CONNECTING -> BusyCard(
                    "🔌", stringResource(R.string.setup_connecting),
                    listOf(R.string.setup_connecting_1, R.string.setup_connecting_2, R.string.setup_connecting_3),
                )
                Phase.DONE -> {
                    result?.let { SetupResultCard(it) }
                    Button(onClick = onBack, modifier = Modifier.fillMaxWidth()) { Text(stringResource(R.string.back)) }
                }
                Phase.PICK -> when (val found = scan) {
                    ScanOutcome.NeedPermission -> NoticeCard(stringResource(R.string.setup_need_permission)) {
                        Button(onClick = { askPermission.launch(vm.stripFinder.permissions) }) {
                            Text(stringResource(R.string.setup_allow))
                        }
                    }
                    ScanOutcome.WifiOff -> NoticeCard(stringResource(R.string.setup_wifi_off)) {
                        Button(onClick = ::search) { Text(stringResource(R.string.setup_search_again)) }
                    }
                    ScanOutcome.LocationOff -> NoticeCard(stringResource(R.string.setup_location_off)) {
                        Button(onClick = ::search) { Text(stringResource(R.string.setup_search_again)) }
                    }
                    is ScanOutcome.Found -> if (found.strips.isEmpty()) {
                        NoticeCard(stringResource(when {
                            found.seen == 0 -> R.string.setup_sees_nothing
                            target != null -> R.string.reconnect_not_found
                            else -> R.string.setup_found_none
                        })) {
                            Button(onClick = ::search) { Text(stringResource(R.string.setup_search_again)) }
                        }
                        // Android sometimes hides the strip's Wi-Fi from apps; the person can see it in Wi-Fi settings
                        TypedApCard { apSsid ->
                            scan = found.copy(strips = listOf(NearbyWifi(apSsid, 0, 2412)))
                            chosen = apSsid
                        }
                    } else {
                        StripsCard(found.strips, chosen, onChoose = { chosen = it; result = null }, onSearch = ::search)
                        HomeWifiCard(
                            homes = found.homes, ssid = ssid, password = password, serverIp = serverIp,
                            onSsid = { ssid = it; result = null },
                            onPassword = { password = it; result = null },
                            onServerIp = { serverIp = it.trim(); serverIpEdited = true; result = null },
                        )
                        result?.let { SetupResultCard(it) }
                        Button(
                            onClick = { chosen?.let(::add) },
                            enabled = chosen != null,
                            modifier = Modifier.fillMaxWidth().height(52.dp),
                        ) { Text(stringResource(R.string.setup_add), fontWeight = FontWeight.Bold) }
                    }
                    null -> ReadyCard(onReady = ::ready)
                }
            }

            // the old way still works: join TONLY_TAP_ by hand and send from here
            if (phase != Phase.CONNECTING && phase != Phase.DONE) {
                TextButton(onClick = { manual = !manual }) {
                    Text((if (manual) "▴ " else "▾ ") + stringResource(R.string.setup_manual))
                }
                if (manual) {
                    ManualSetup(
                        ssid = ssid, password = password, serverIp = serverIp, busy = manualBusy,
                        onSsid = { ssid = it; manualResult = null },
                        onPassword = { password = it; manualResult = null },
                        onServerIp = { serverIp = it.trim(); serverIpEdited = true; manualResult = null },
                        onSend = {
                            manualBusy = true
                            manualResult = null
                            scope.launch {
                                manualResult = vm.provision(serverIp, ssid, password)
                                manualBusy = false
                            }
                        },
                    )
                    manualResult?.let { SetupResultCard(it, auto = false) }
                }
            }
        }
    }
}

@Composable
private fun ReadyCard(onReady: () -> Unit) {
    GlassCard(Modifier.fillMaxWidth(), padding = 20.dp) {
        Box(
            Modifier.align(Alignment.CenterHorizontally).size(96.dp).clip(CircleShape)
                .background(Glass.Orange.copy(alpha = 0.16f)),
            contentAlignment = Alignment.Center,
        ) { Text("📶", fontSize = 40.sp) }
        Spacer(Modifier.height(14.dp))
        Text(
            stringResource(R.string.setup_ready_title), style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.align(Alignment.CenterHorizontally),
        )
        Spacer(Modifier.height(14.dp))
        listOf(R.string.setup_ready_1, R.string.setup_ready_2, R.string.setup_ready_3).forEachIndexed { i, step ->
            NumberedLine(i + 1, stringResource(step))
        }
        Spacer(Modifier.height(16.dp))
        Button(onClick = onReady, modifier = Modifier.fillMaxWidth().height(52.dp)) {
            Text("🔍  " + stringResource(R.string.setup_ready), fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun BusyCard(emoji: String, title: String, tips: List<Int>) {
    GlassCard(Modifier.fillMaxWidth(), padding = 24.dp) {
        Box(Modifier.align(Alignment.CenterHorizontally).size(96.dp), contentAlignment = Alignment.Center) {
            CircularProgressIndicator(Modifier.size(96.dp), strokeWidth = 3.dp)
            Text(emoji, fontSize = 34.sp)
        }
        Spacer(Modifier.height(16.dp))
        Text(
            title, style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center,
            modifier = Modifier.align(Alignment.CenterHorizontally),
        )
        if (tips.isNotEmpty()) Spacer(Modifier.height(12.dp))
        tips.forEachIndexed { i, tip -> NumberedLine(i + 1, stringResource(tip)) }
    }
}

/** When the scan shows no strip: type the name seen in the phone's Wi-Fi settings (or just its code). */
@Composable
private fun TypedApCard(onUse: (String) -> Unit) {
    var typed by rememberSaveable { mutableStateOf("") }
    val apSsid = SetupRules.apFromTyped(typed)
    GlassCard(Modifier.fillMaxWidth()) {
        Text(stringResource(R.string.setup_typed_title), style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(4.dp))
        Text(stringResource(R.string.setup_typed_hint), color = Glass.TextSoft, style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            value = typed, onValueChange = { typed = it.take(32) }, singleLine = true,
            label = { Text(stringResource(R.string.setup_typed_label)) },
            placeholder = { Text("TONLY_TAP_96BB292") },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        Button(onClick = { apSsid?.let(onUse) }, enabled = apSsid != null) { Text(stringResource(R.string.setup_typed_use)) }
    }
}

@Composable
private fun NoticeCard(text: String, action: @Composable () -> Unit) {
    GlassCard(Modifier.fillMaxWidth()) {
        Text(text, style = MaterialTheme.typography.bodyMedium)
        Spacer(Modifier.height(12.dp))
        action()
    }
}

@Composable
private fun StripsCard(strips: List<NearbyWifi>, chosen: String?, onChoose: (String) -> Unit, onSearch: () -> Unit) {
    GlassCard(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                stringResource(R.string.setup_choose_strip), style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onSearch) { Text(stringResource(R.string.setup_search_again)) }
        }
        strips.forEach { strip ->
            Row(
                Modifier.fillMaxWidth().clip(Glass.Tile).clickable { onChoose(strip.ssid) }.padding(vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = strip.ssid == chosen, onClick = { onChoose(strip.ssid) })
                Column(Modifier.weight(1f)) {
                    Text(
                        stringResource(R.string.setup_found_strip, ltr(SetupRules.apCode(strip.ssid))),
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(signalText(strip.level), color = Glass.TextSoft, style = MaterialTheme.typography.labelMedium)
                }
                Text("🔌", fontSize = 22.sp)
            }
        }
    }
}

@Composable
private fun signalText(level: Int): String = stringResource(
    when {
        level >= -60 -> R.string.setup_signal_strong
        level >= -75 -> R.string.setup_signal_ok
        else -> R.string.setup_signal_weak
    }
)

@Composable
private fun HomeWifiCard(
    homes: List<NearbyWifi>,
    ssid: String,
    password: String,
    serverIp: String,
    onSsid: (String) -> Unit,
    onPassword: (String) -> Unit,
    onServerIp: (String) -> Unit,
) {
    GlassCard(Modifier.fillMaxWidth()) {
        Text(stringResource(R.string.setup_home_wifi), style = MaterialTheme.typography.titleMedium)
        if (homes.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text(stringResource(R.string.setup_nearby_wifi), color = Glass.TextSoft, style = MaterialTheme.typography.labelMedium)
            Spacer(Modifier.height(6.dp))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                homes.take(6).forEach { home ->
                    FilterChip(selected = home.ssid == ssid, onClick = { onSsid(home.ssid) }, label = { Text(home.ssid) })
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        WifiFields(ssid, password, serverIp, onSsid, onPassword, onServerIp)
    }
}

@Composable
private fun ManualSetup(
    ssid: String,
    password: String,
    serverIp: String,
    busy: Boolean,
    onSsid: (String) -> Unit,
    onPassword: (String) -> Unit,
    onServerIp: (String) -> Unit,
    onSend: () -> Unit,
) {
    GlassCard(Modifier.fillMaxWidth()) {
        listOf(R.string.setup_step1, R.string.setup_step2, R.string.setup_step3, R.string.setup_step4)
            .forEachIndexed { i, step -> NumberedLine(i + 1, stringResource(step)) }
        Spacer(Modifier.height(8.dp))
        WifiFields(ssid, password, serverIp, onSsid, onPassword, onServerIp)
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(enabled = !busy, onClick = onSend) { Text(stringResource(R.string.setup_send)) }
            if (busy) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
        }
    }
}

@Composable
private fun WifiFields(
    ssid: String,
    password: String,
    serverIp: String,
    onSsid: (String) -> Unit,
    onPassword: (String) -> Unit,
    onServerIp: (String) -> Unit,
) {
    var showPassword by rememberSaveable { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(
            value = ssid,
            onValueChange = onSsid,
            label = { Text(stringResource(R.string.setup_wifi_name)) },
            supportingText = { Text(stringResource(R.string.setup_wifi_hint)) },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = password,
            onValueChange = onPassword,
            label = { Text(stringResource(R.string.setup_wifi_password)) },
            singleLine = true,
            visualTransformation = if (showPassword) VisualTransformation.None else PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            trailingIcon = {
                TextButton(onClick = { showPassword = !showPassword }) {
                    Text(stringResource(if (showPassword) R.string.hide else R.string.show))
                }
            },
            modifier = Modifier.fillMaxWidth(),
        )
        // the server fills this in; it stays out of sight unless it is missing or someone wants to change it
        var advanced by rememberSaveable { mutableStateOf(false) }
        if (serverIp.isNotBlank()) {
            TextButton(onClick = { advanced = !advanced }) {
                Text((if (advanced) "▴ " else "▾ ") + stringResource(R.string.setup_advanced))
            }
        }
        if (advanced || serverIp.isBlank()) {
            OutlinedTextField(
                value = serverIp,
                onValueChange = onServerIp,
                label = { Text(stringResource(R.string.setup_server_ip)) },
                placeholder = { Text(Prefs.EXAMPLE_SERVER_IP) },
                supportingText = { Text(stringResource(R.string.setup_server_ip_hint)) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun NumberedLine(number: Int, text: String) {
    Row(Modifier.padding(vertical = 4.dp)) {
        Box(
            Modifier.size(24.dp).clip(CircleShape).background(Glass.Orange.copy(alpha = 0.18f)),
            contentAlignment = Alignment.Center,
        ) {
            Text("$number", color = Glass.Orange, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.width(10.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun SetupResultCard(result: SetupResult, auto: Boolean = true) {
    val ok = result is SetupResult.Done
    val text = when (result) {
        SetupResult.Done -> stringResource(if (auto) R.string.setup_done_auto else R.string.setup_done)
        SetupResult.NoWifi -> stringResource(R.string.setup_no_wifi)
        SetupResult.NotReachable -> stringResource(R.string.setup_not_reachable)
        SetupResult.JoinFailed -> stringResource(R.string.setup_join_failed)
        is SetupResult.Refused -> stringResource(R.string.setup_refused, result.answer.ifEmpty { "—" })
        is SetupResult.Invalid -> stringResource(
            when (result.problem) {
                SetupProblem.BAD_SERVER_IP -> R.string.setup_bad_ip
                SetupProblem.EMPTY_SSID -> R.string.setup_empty_ssid
                SetupProblem.BAD_SSID -> R.string.setup_bad_ssid
                SetupProblem.EMPTY_PASSWORD -> R.string.setup_empty_password
                SetupProblem.BAD_PASSWORD -> R.string.setup_bad_password
            }
        )
    }
    val colors = MaterialTheme.colorScheme
    ElevatedCard(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.elevatedCardColors(
            containerColor = if (ok) colors.secondaryContainer else colors.errorContainer,
            contentColor = if (ok) colors.onSecondaryContainer else colors.onErrorContainer,
        ),
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(text, style = MaterialTheme.typography.bodyMedium)
        }
    }
}
