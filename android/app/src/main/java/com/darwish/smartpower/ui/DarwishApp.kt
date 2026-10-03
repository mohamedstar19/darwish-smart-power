package com.darwish.smartpower.ui

import android.content.Context
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.darwish.smartpower.R
import com.darwish.smartpower.security.Biometric

enum class Tab(val icon: ImageVector, val label: Int) {
    HOME(Icons.Filled.Home, R.string.tab_home),
    ENERGY(BoltIcon, R.string.tab_energy),
    AUTOMATION(Icons.Filled.DateRange, R.string.tab_automation),
    SETTINGS(Icons.Filled.Settings, R.string.tab_settings),
}

/** Screens shown on top of the tabs. */
enum class Overlay { NONE, SETUP, ALERTS }

fun Note.format(context: Context): String =
    if (arg != null) context.getString(text, arg) else context.getString(text)

@Composable
fun DarwishApp(locked: Boolean, onUnlocked: () -> Unit, vm: AppViewModel = viewModel()) {
    if (locked) {
        LockScreen(onUnlocked)
        return
    }
    var tab by rememberSaveable { mutableStateOf(Tab.HOME) }
    var overlay by rememberSaveable { mutableStateOf(Overlay.NONE) }
    val snackbar = remember { SnackbarHostState() }
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val pin by vm.pinRequest.collectAsStateWithLifecycle()

    LaunchedEffect(vm) { vm.messages.collect { snackbar.showSnackbar(it.format(context)) } }
    LaunchedEffect(lifecycleOwner) { lifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) { vm.pollForever() } }

    BackHandler(enabled = overlay != Overlay.NONE || tab != Tab.HOME) {
        if (overlay != Overlay.NONE) overlay = Overlay.NONE else tab = Tab.HOME
    }

    GlassBackground {
        when (overlay) {
            Overlay.SETUP -> SetupScreen(vm, snackbar, onBack = { overlay = Overlay.NONE })
            Overlay.ALERTS -> AlertsScreen(vm, onBack = { overlay = Overlay.NONE })
            Overlay.NONE -> Scaffold(
                containerColor = Color.Transparent,
                snackbarHost = { SnackbarHost(snackbar) },
                bottomBar = {
                    NavigationBar(containerColor = Glass.Night.copy(alpha = 0.92f), tonalElevation = 0.dp) {
                        Tab.entries.forEach { t ->
                            NavigationBarItem(
                                selected = tab == t,
                                onClick = { tab = t },
                                icon = { Icon(t.icon, contentDescription = null) },
                                label = { Text(stringResource(t.label)) },
                                colors = NavigationBarItemDefaults.colors(
                                    selectedIconColor = Glass.Night,
                                    indicatorColor = Glass.Orange,
                                    selectedTextColor = Glass.Orange,
                                    unselectedIconColor = Glass.TextSoft,
                                    unselectedTextColor = Glass.TextSoft,
                                ),
                            )
                        }
                    }
                },
            ) { padding ->
                Box(Modifier.padding(padding)) {
                    when (tab) {
                        Tab.HOME -> HomeScreen(
                            vm,
                            onSettings = { tab = Tab.SETTINGS },
                            onSetup = { overlay = Overlay.SETUP },
                            onAlerts = { overlay = Overlay.ALERTS },
                        )
                        Tab.ENERGY -> EnergyScreen(vm)
                        Tab.AUTOMATION -> AutomationScreen(vm)
                        Tab.SETTINGS -> SettingsScreen(vm, onSetup = { overlay = Overlay.SETUP })
                    }
                }
            }
        }
    }
    pin?.let { PinDialog(it, vm) }
}

/** Shown when "lock the app" is on, until the fingerprint (or the phone's PIN) is given. */
@Composable
fun LockScreen(onUnlocked: () -> Unit) {
    val context = LocalContext.current
    val title = stringResource(R.string.lock_title)
    val ask = { Biometric.authenticate(context, title, null) { ok -> if (ok) onUnlocked() } }
    LaunchedEffect(Unit) { ask() }
    GlassBackground {
        Column(
            Modifier.fillMaxSize().padding(32.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("🔒", fontSize = 64.sp)
            Spacer(Modifier.height(16.dp))
            Text(title, style = MaterialTheme.typography.headlineMedium, textAlign = TextAlign.Center)
            Spacer(Modifier.height(8.dp))
            Text(stringResource(R.string.lock_body), color = Glass.TextSoft, textAlign = TextAlign.Center)
            Spacer(Modifier.height(28.dp))
            Button(onClick = ask, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                Text(stringResource(R.string.lock_unlock))
            }
            Spacer(Modifier.size(1.dp))
        }
    }
}
