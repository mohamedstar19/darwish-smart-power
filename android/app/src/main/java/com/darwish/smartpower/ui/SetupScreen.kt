@file:OptIn(ExperimentalMaterial3Api::class)

package com.darwish.smartpower.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.darwish.smartpower.R
import com.darwish.smartpower.data.Prefs
import com.darwish.smartpower.data.SetupProblem
import com.darwish.smartpower.data.SetupResult
import kotlinx.coroutines.launch

@Composable
fun SetupScreen(vm: AppViewModel, snackbar: SnackbarHostState, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    var serverIp by rememberSaveable { mutableStateOf(vm.suggestedServerIp()) }
    var ssid by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var showPassword by rememberSaveable { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<SetupResult?>(null) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.setup_title)) },
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
            listOf(R.string.setup_step1, R.string.setup_step2, R.string.setup_step3, R.string.setup_step4)
                .forEachIndexed { i, step ->
                    Row {
                        Text("${i + 1}.", style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.width(24.dp))
                        Text(stringResource(step), style = MaterialTheme.typography.bodyMedium)
                    }
                }

            OutlinedTextField(
                value = serverIp,
                onValueChange = { serverIp = it.trim(); result = null },
                label = { Text(stringResource(R.string.setup_server_ip)) },
                placeholder = { Text(Prefs.DEFAULT_SERVER_IP) },
                supportingText = { Text(stringResource(R.string.setup_server_ip_hint)) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = ssid,
                onValueChange = { ssid = it; result = null },
                label = { Text(stringResource(R.string.setup_wifi_name)) },
                supportingText = { Text(stringResource(R.string.setup_wifi_hint)) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = password,
                onValueChange = { password = it; result = null },
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
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(
                    enabled = !busy,
                    onClick = {
                        busy = true
                        result = null
                        scope.launch {
                            result = vm.provision(serverIp, ssid, password)
                            busy = false
                        }
                    },
                ) { Text(stringResource(R.string.setup_send)) }
                if (busy) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
            }
            result?.let { SetupResultCard(it, serverIp) }
        }
    }
}

@Composable
private fun SetupResultCard(result: SetupResult, serverIp: String) {
    val ok = result is SetupResult.Done
    val text = when (result) {
        SetupResult.Done -> stringResource(R.string.setup_done, serverIp)
        SetupResult.NoWifi -> stringResource(R.string.setup_no_wifi)
        SetupResult.NotReachable -> stringResource(R.string.setup_not_reachable)
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
