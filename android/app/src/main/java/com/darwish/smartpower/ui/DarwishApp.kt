package com.darwish.smartpower.ui

import android.content.Context
import androidx.activity.compose.BackHandler
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.viewmodel.compose.viewModel

enum class Screen { HOME, SETTINGS, SETUP }

fun Note.format(context: Context): String =
    if (arg != null) context.getString(text, arg) else context.getString(text)

@Composable
fun DarwishApp(vm: AppViewModel = viewModel()) {
    var screen by rememberSaveable { mutableStateOf(Screen.HOME) }
    val snackbar = remember { SnackbarHostState() }
    val context = LocalContext.current

    LaunchedEffect(vm) {
        vm.messages.collect { snackbar.showSnackbar(it.format(context)) }
    }
    BackHandler(enabled = screen != Screen.HOME) {
        screen = if (screen == Screen.SETUP) Screen.SETTINGS else Screen.HOME
    }

    when (screen) {
        Screen.HOME -> HomeScreen(
            vm = vm,
            snackbar = snackbar,
            onSettings = { screen = Screen.SETTINGS },
            onSetup = { screen = Screen.SETUP },
        )
        Screen.SETTINGS -> SettingsScreen(
            vm = vm,
            snackbar = snackbar,
            onBack = { screen = Screen.HOME },
            onSetup = { screen = Screen.SETUP },
        )
        Screen.SETUP -> SetupScreen(
            vm = vm,
            snackbar = snackbar,
            onBack = { screen = Screen.SETTINGS },
        )
    }
}
