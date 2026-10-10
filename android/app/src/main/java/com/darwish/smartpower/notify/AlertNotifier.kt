package com.darwish.smartpower.notify

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.darwish.smartpower.MainActivity
import com.darwish.smartpower.R
import com.darwish.smartpower.data.AlertEvent
import com.darwish.smartpower.data.Prefs
import com.darwish.smartpower.data.ServerClient
import com.darwish.smartpower.data.Strip
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Turns server alerts (strip offline, too hot, too much power) into phone notifications.
 * Runs when the app polls, and every 15 minutes in the background (the shortest Android allows).
 */
object AlertNotifier {
    private const val CHANNEL = "alerts"
    private const val WORK = "alert-check"
    private val lock = Mutex()

    fun schedule(context: Context, enabled: Boolean) {
        val wm = WorkManager.getInstance(context)
        if (!enabled) {
            wm.cancelUniqueWork(WORK)
            return
        }
        val request = PeriodicWorkRequestBuilder<AlertWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        wm.enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP, request)
    }

    suspend fun check(context: Context) = lock.withLock {
        val prefs = Prefs(context)
        if (!prefs.notifications) return@withLock
        val address = prefs.address ?: return@withLock
        val client = ServerClient(address, prefs.token)
        val last = prefs.lastNotifiedEvent
        val (events, newest) = client.events(if (last < 0) Long.MAX_VALUE else last)
        if (last < 0) {                                   // first run: start from now, don't replay history
            prefs.lastNotifiedEvent = newest
            return@withLock
        }
        if (events.isEmpty()) return@withLock
        val strips = runCatching { client.state().strips }.getOrDefault(emptyList())
        createChannel(context)
        events.sortedBy { it.id }.forEach { post(context, it, strips) }
        prefs.lastNotifiedEvent = maxOf(newest, events.maxOf { it.id })
    }

    private fun createChannel(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, context.getString(R.string.notif_channel), NotificationManager.IMPORTANCE_HIGH)
        )
    }

    private fun post(context: Context, e: AlertEvent, strips: List<Strip>) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val strip = strips.firstOrNull { it.id == e.stripId }
        val stripName = strip?.name ?: context.getString(R.string.default_strip_name, e.stripId.takeLast(6))
        val outletName = strip?.outlets?.firstOrNull { it.index == e.outlet }?.name
            ?: context.getString(R.string.default_outlet_name, e.outlet)
        val value = String.format(Locale.US, "%.0f", e.value)
        val (title, text) = when (e.kind) {
            "offline" -> context.getString(R.string.notif_offline_title) to context.getString(R.string.notif_offline, stripName)
            "online" -> context.getString(R.string.notif_online_title) to context.getString(R.string.notif_online, stripName)
            "power_back" -> context.getString(R.string.notif_power_back_title) to context.getString(
                R.string.notif_power_back, stripName,
                if (e.value < 1) context.getString(R.string.under_a_minute)
                else context.getString(R.string.minutes_value, e.value.toInt()),
            )
            "power_restore" -> context.getString(R.string.notif_power_restore_title) to
                context.getString(R.string.notif_power_restore, outletName, stripName)
            "temp" -> context.getString(R.string.notif_temp_title) to context.getString(R.string.notif_temp, outletName, stripName, value)
            "power" -> context.getString(R.string.notif_power_title) to context.getString(R.string.notif_power, stripName, value)
            "rule_power_off", "rule_temp_off", "rule_power", "rule_temp" -> context.getString(R.string.notif_rule_title) to
                context.getString(
                    if (e.kind.endsWith("_off")) R.string.notif_rule_off else R.string.notif_rule, outletName, stripName,
                    if (e.kind.startsWith("rule_temp")) "$value°C" else context.getString(R.string.watts_value, value),
                )
            "trip" -> context.getString(R.string.notif_trip_title) to context.getString(
                if (e.value.toInt() == 2) R.string.notif_trip_heat else R.string.notif_trip_load, outletName, stripName,
            )
            else -> return
        }
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setWhen(e.epochSeconds * 1000)
            .build()
        try {
            NotificationManagerCompat.from(context).notify(e.id.toInt(), n)
        } catch (e: SecurityException) {
            // notifications were turned off for the app in the meantime
        }
    }
}

class AlertWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = try {
        AlertNotifier.check(applicationContext)
        Result.success()
    } catch (e: Exception) {
        Result.retry()
    }
}
