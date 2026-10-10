@file:OptIn(ExperimentalMaterial3Api::class)

package com.darwish.smartpower.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.darwish.smartpower.R
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Recent alerts from the server: strips going offline, hot outlets, high power. */
@Composable
fun AlertsScreen(vm: AppViewModel, onBack: () -> Unit) {
    val state by vm.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.loadAlerts() }
    val format = SimpleDateFormat("d/M HH:mm", Locale.US)
    Scaffold(
        containerColor = Color.Transparent,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent),
                title = { Text(stringResource(R.string.alerts)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.back))
                    }
                },
            )
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier.padding(padding),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (state.alerts.isEmpty()) {
                item { Text(stringResource(R.string.alerts_empty), color = Glass.TextSoft) }
            }
            items(state.alerts, key = { it.id }) { e ->
                val strip = state.strips.firstOrNull { it.id == e.stripId }
                val stripLabel = strip?.let { stripName(it) } ?: e.stripId.takeLast(6)
                val outletLabel = strip?.outlets?.firstOrNull { it.index == e.outlet }?.let { outletName(it) }
                    ?: stringResource(R.string.default_outlet_name, e.outlet)
                val value = String.format(Locale.US, "%.0f", e.value)
                val (emoji, text) = when (e.kind) {
                    "offline" -> "📴" to stringResource(R.string.notif_offline, stripLabel)
                    "online" -> "✅" to stringResource(R.string.notif_online, stripLabel)
                    "temp" -> "🌡️" to stringResource(R.string.notif_temp, outletLabel, stripLabel, value)
                    "power" -> "⚡" to stringResource(R.string.notif_power, stripLabel, value)
                    "trip" -> "🛑" to stringResource(
                        if (e.value.toInt() == 2) R.string.notif_trip_heat else R.string.notif_trip_load, outletLabel, stripLabel,
                    )
                    else -> "ℹ️" to e.kind
                }
                GlassCard(Modifier.fillMaxWidth(), padding = 14.dp) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(emoji, fontSize = 24.sp)
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(text, fontWeight = FontWeight.Medium)
                            Text(ltr(format.format(Date(e.epochSeconds * 1000))), color = Glass.TextFaint,
                                style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            }
        }
    }
}
