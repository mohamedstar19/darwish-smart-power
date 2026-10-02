@file:OptIn(ExperimentalMaterial3Api::class)

package com.darwish.smartpower.ui

import androidx.appcompat.app.AppCompatDelegate
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.os.LocaleListCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.darwish.smartpower.R
import com.darwish.smartpower.data.Prefs
import kotlinx.coroutines.launch

@Composable
fun SettingsScreen(vm: AppViewModel, snackbar: SnackbarHostState, onBack: () -> Unit, onSetup: () -> Unit) {
    val state by vm.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var address by rememberSaveable { mutableStateOf(state.addressText) }
    var token by rememberSaveable { mutableStateOf(state.token) }
    var showToken by rememberSaveable { mutableStateOf(false) }
    var addressError by remember { mutableStateOf(false) }
    var testing by remember { mutableStateOf(false) }
    var testResult by remember { mutableStateOf<String?>(null) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.settings)) },
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
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            SectionTitle(stringResource(R.string.section_server))
            OutlinedTextField(
                value = address,
                onValueChange = { address = it; addressError = false; testResult = null },
                label = { Text(stringResource(R.string.server_address)) },
                placeholder = { Text(Prefs.DEFAULT_ADDRESS) },
                supportingText = {
                    Text(stringResource(if (addressError) R.string.settings_bad_address else R.string.server_address_hint))
                },
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
                    TextButton(onClick = { showToken = !showToken }) {
                        Text(stringResource(if (showToken) R.string.hide else R.string.show))
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Button(onClick = {
                    if (vm.saveSettings(address, token)) onBack() else addressError = true
                }) { Text(stringResource(R.string.save)) }
                OutlinedButton(
                    enabled = !testing,
                    onClick = {
                        testing = true
                        testResult = null
                        scope.launch {
                            testResult = vm.test(address, token).format(context)
                            testing = false
                        }
                    },
                ) { Text(stringResource(R.string.test)) }
                if (testing) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
            }
            testResult?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }

            HorizontalDivider()
            SectionTitle(stringResource(R.string.section_language))
            LanguagePicker()

            HorizontalDivider()
            SectionTitle(stringResource(R.string.section_setup))
            Text(stringResource(R.string.setup_intro), style = MaterialTheme.typography.bodyMedium)
            OutlinedButton(onClick = onSetup) { Text(stringResource(R.string.setup_strip)) }

            HorizontalDivider()
            Text(stringResource(R.string.about), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary)
}

/** Phone language, Arabic or English. AppCompat stores the choice and recreates the screen. */
@Composable
private fun LanguagePicker() {
    val current = AppCompatDelegate.getApplicationLocales().toLanguageTags()
    val options = listOf(
        "" to R.string.lang_system,
        "ar" to R.string.lang_ar,
        "en" to R.string.lang_en,
    )
    Column(Modifier.selectableGroup()) {
        options.forEach { (tag, label) ->
            val selected = if (tag.isEmpty()) current.isEmpty() else current.startsWith(tag)
            Row(
                Modifier
                    .fillMaxWidth()
                    .selectable(selected = selected, role = Role.RadioButton, onClick = {
                        AppCompatDelegate.setApplicationLocales(
                            if (tag.isEmpty()) LocaleListCompat.getEmptyLocaleList() else LocaleListCompat.forLanguageTags(tag)
                        )
                    })
                    .padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = selected, onClick = null)
                Text(stringResource(label), modifier = Modifier.padding(start = 12.dp))
            }
        }
    }
}
