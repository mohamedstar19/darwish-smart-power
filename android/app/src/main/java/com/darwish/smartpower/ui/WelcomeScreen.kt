package com.darwish.smartpower.ui

import android.net.Uri
import android.provider.Settings
import androidx.appcompat.app.AppCompatDelegate
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.os.LocaleListCompat
import com.darwish.smartpower.R
import kotlinx.coroutines.launch

/** Characters in a new account's password (the server asks the same). */
const val MIN_PASSWORD = 8

/** Support on WhatsApp (01200414873), for a customer who forgot the password. */
const val SUPPORT_WHATSAPP = "https://wa.me/201200414873"

/**
 * The first screen until this phone is signed in: sign in or create a customer account.
 * [expired]: the phone had a sign-in that the server no longer accepts.
 */
@Composable
fun WelcomeScreen(vm: AppViewModel, expired: Boolean, onServerPassword: () -> Unit) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    // "remove animations" in the phone's accessibility settings: keep everything still
    val still = remember {
        Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f
    }
    var newAccount by rememberSaveable { mutableStateOf(!expired) }
    var name by rememberSaveable { mutableStateOf("") }
    var login by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var confirm by rememberSaveable { mutableStateOf("") }
    var showPassword by rememberSaveable { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<AccountError?>(null) }
    var forgot by remember { mutableStateOf(false) }
    val shake = remember { Animatable(0f) }

    val mismatch = newAccount && confirm.isNotEmpty() && confirm != password
    val ready = login.isNotBlank() && password.isNotEmpty() &&
        (!newAccount || (name.isNotBlank() && password.length >= MIN_PASSWORD && confirm == password))

    fun submit() {
        if (busy || !ready) return
        busy = true
        error = null
        scope.launch {
            error = if (newAccount) vm.signUp(name, login, password) else vm.signIn(login, password)
            busy = false
            if (error != null && !still) {                  // a little shake says "no" without words
                for (x in listOf(14f, -12f, 9f, -6f, 3f, 0f)) shake.animateTo(x, tween(55))
            }
        }
    }

    Box(Modifier.fillMaxSize().background(Glass.Night)) {
        WelcomeHero(still)
        Column(
            Modifier
                .fillMaxSize()
                .imePadding()
                .verticalScroll(rememberScrollState())
                .statusBarsPadding()
                .navigationBarsPadding()
                .padding(horizontal = 18.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Row(Modifier.fillMaxWidth().padding(top = 10.dp), horizontalArrangement = Arrangement.End) { LanguagePill() }
            AppMark(still)
            Spacer(Modifier.height(10.dp))
            AnimatedContent(
                targetState = newAccount,
                transitionSpec = { fadeIn(tween(260)) togetherWith fadeOut(tween(180)) },
                label = "title",
            ) { creating ->
                Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        stringResource(if (creating) R.string.welcome_new_title else R.string.welcome_back_title),
                        style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.ExtraBold, textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        stringResource(
                            when {
                                creating -> R.string.welcome_new_sub
                                expired -> R.string.account_expired
                                else -> R.string.welcome_back_sub
                            }
                        ),
                        color = Color(0xFFE7CDBD), style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center,
                    )
                }
            }
            Spacer(Modifier.height(22.dp))

            Column(
                Modifier
                    .fillMaxWidth()
                    .graphicsLayer { translationX = shake.value.dp.toPx() }
                    .clip(Glass.Card)
                    .background(Brush.linearGradient(listOf(Color(0x24FFFFFF), Color(0x0DFFFFFF))))
                    .border(1.dp, Glass.Stroke, Glass.Card)
                    .padding(18.dp),
            ) {
                ModeSwitch(newAccount) { newAccount = it; error = null }
                Spacer(Modifier.height(16.dp))
                AnimatedVisibility(newAccount, enter = expandVertically() + fadeIn(), exit = shrinkVertically() + fadeOut()) {
                    Field(
                        value = name, onValue = { name = it.take(30); error = null }, label = R.string.account_name,
                        icon = Icons.Filled.Person, isError = error == AccountError.NAME,
                    )
                }
                Field(
                    value = login, onValue = { login = it; error = null }, label = R.string.account_login, icon = Icons.Filled.Phone,
                    isError = error == AccountError.LOGIN || error == AccountError.EXISTS || error == AccountError.WRONG,
                    keyboard = KeyboardType.Email,
                )
                Field(
                    value = password, onValue = { password = it; error = null }, label = R.string.account_password, icon = Icons.Filled.Lock,
                    isError = error == AccountError.PASSWORD || error == AccountError.WRONG,
                    keyboard = KeyboardType.Password, hidden = !showPassword,
                    last = !newAccount, onDone = ::submit,
                    trailing = {
                        TextButton(onClick = { showPassword = !showPassword }) {
                            Text(if (showPassword) "🙈" else "👁", fontSize = 18.sp)
                        }
                    },
                )
                AnimatedVisibility(newAccount, enter = expandVertically() + fadeIn(), exit = shrinkVertically() + fadeOut()) {
                    Column {
                        Field(
                            value = confirm, onValue = { confirm = it; error = null }, label = R.string.account_confirm,
                            icon = Icons.Filled.CheckCircle, isError = mismatch, keyboard = KeyboardType.Password,
                            hidden = !showPassword, last = true, onDone = ::submit,
                        )
                        if (mismatch) {
                            Text(stringResource(R.string.account_mismatch), color = Glass.Red, style = MaterialTheme.typography.bodySmall)
                        }
                        Strength(password)
                    }
                }
                error?.let {
                    Spacer(Modifier.height(6.dp))
                    Text(stringResource(accountErrorText(it)), color = Glass.Red, style = MaterialTheme.typography.bodySmall)
                }
                Spacer(Modifier.height(14.dp))
                GlowButton(
                    text = stringResource(if (newAccount) R.string.account_create else R.string.account_sign_in),
                    mark = if (newAccount) "＋" else "➜", busy = busy, enabled = ready, onClick = ::submit,
                )
                if (!newAccount) {
                    TextButton(onClick = { forgot = true }, modifier = Modifier.align(Alignment.CenterHorizontally)) {
                        Text(stringResource(R.string.account_forgot), color = Glass.Gold)
                    }
                }
                HorizontalDivider(Modifier.padding(vertical = 6.dp), color = Glass.Stroke)
                TextButton(onClick = onServerPassword, modifier = Modifier.align(Alignment.CenterHorizontally)) {
                    Text(stringResource(R.string.account_have_password), color = Glass.TextSoft)
                }
            }
            Spacer(Modifier.height(22.dp))
            Text(stringResource(R.string.welcome_tagline), color = Glass.TextFaint, style = MaterialTheme.typography.bodySmall,
                textAlign = TextAlign.Center)
            Spacer(Modifier.height(24.dp))
        }
    }

    if (forgot) {
        val uri = LocalUriHandler.current
        AlertDialog(
            onDismissRequest = { forgot = false },
            title = { Text(stringResource(R.string.account_forgot)) },
            text = { Text(stringResource(R.string.account_forgot_body)) },
            confirmButton = {
                val message = stringResource(R.string.account_forgot_message, login.trim())
                TextButton(onClick = { forgot = false; uri.openUri(SUPPORT_WHATSAPP + "?text=" + Uri.encode(message)) }) {
                    Text(stringResource(R.string.account_contact))
                }
            },
            dismissButton = { TextButton(onClick = { forgot = false }) { Text(stringResource(R.string.ok)) } },
        )
    }
}

/** The warm header behind the logo, with slowly drifting soft circles. */
@Composable
private fun WelcomeHero(still: Boolean) {
    val drift = rememberInfiniteTransition(label = "hero")
    val moving by drift.animateFloat(
        0f, 1f, infiniteRepeatable(tween(9000, easing = LinearEasing), RepeatMode.Reverse), label = "drift",
    )
    val p = if (still) 0.5f else moving
    Box(
        Modifier
            .fillMaxWidth()
            .height(340.dp)
            .clip(RoundedCornerShape(bottomStart = 56.dp, bottomEnd = 56.dp))
            .background(Brush.linearGradient(listOf(Color(0xFF5A2410), Color(0xFF3A170B), Glass.Deep))),
    ) {
        Bubble(230.dp, Modifier.align(Alignment.TopStart).offset((-80 + 24 * p).dp, (-70 + 18 * p).dp))
        Bubble(90.dp, Modifier.align(Alignment.TopEnd).offset((-30 - 16 * p).dp, (130 + 10 * p).dp), 0.6f)
        Bubble(270.dp, Modifier.align(Alignment.BottomEnd).offset((120 - 20 * p).dp, (110 - 16 * p).dp), 0.5f)
    }
}

@Composable
private fun Bubble(size: Dp, modifier: Modifier, alpha: Float = 1f) {
    Box(
        modifier
            .size(size)
            .graphicsLayer { this.alpha = alpha }
            .clip(CircleShape)
            .background(Brush.radialGradient(listOf(Color(0x8CFF8A4C), Color(0x14FF6A2B)))),
    )
}

/** The app icon with a glow that breathes slowly. */
@Composable
private fun AppMark(still: Boolean) {
    val breathe = rememberInfiniteTransition(label = "mark")
    val glow by breathe.animateFloat(
        0.35f, 0.8f, infiniteRepeatable(tween(2200, easing = FastOutSlowInEasing), RepeatMode.Reverse), label = "glow",
    )
    val grow by breathe.animateFloat(
        1f, 1.04f, infiniteRepeatable(tween(2200, easing = FastOutSlowInEasing), RepeatMode.Reverse), label = "grow",
    )
    Box(Modifier.size(170.dp), contentAlignment = Alignment.Center) {
        Box(
            Modifier
                .size(170.dp)
                .graphicsLayer { alpha = if (still) 0.55f else glow }
                .background(Brush.radialGradient(listOf(Color(0xCCFF7A3D), Color.Transparent)), CircleShape),
        )
        Box(
            Modifier
                .size(108.dp)
                .graphicsLayer { val s = if (still) 1f else grow; scaleX = s; scaleY = s }
                .clip(RoundedCornerShape(30.dp)),
        ) {
            Image(painterResource(R.drawable.ic_launcher_background), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
            Image(painterResource(R.drawable.ic_launcher_foreground), null, Modifier.fillMaxSize().scale(1.35f))
        }
    }
}

/** "Sign in | New account", with the orange pill sliding to the chosen side. */
@Composable
private fun ModeSwitch(newAccount: Boolean, onChange: (Boolean) -> Unit) {
    BoxWithConstraints(
        Modifier
            .fillMaxWidth()
            .height(50.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(Color(0x59000000))
            .padding(4.dp),
    ) {
        val half = maxWidth / 2
        val slide by animateDpAsState(if (newAccount) half else 0.dp, tween(300, easing = FastOutSlowInEasing), label = "pill")
        Box(
            Modifier
                .offset(x = slide)              // follows the layout direction, so it slides the right way in Arabic too
                .width(half)
                .fillMaxSize()
                .clip(RoundedCornerShape(12.dp))
                .background(Glass.Accent),
        )
        Row(Modifier.fillMaxSize()) {
            listOf(false to R.string.account_sign_in, true to R.string.account_create).forEach { (creating, label) ->
                val on = creating == newAccount
                Box(
                    Modifier.weight(1f).fillMaxSize().clip(RoundedCornerShape(12.dp)).clickable { onChange(creating) },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(stringResource(label), fontWeight = FontWeight.Bold, color = if (on) Color(0xFF2A0E00) else Glass.TextSoft)
                }
            }
        }
    }
}

@Composable
private fun Field(
    value: String,
    onValue: (String) -> Unit,
    label: Int,
    icon: ImageVector,
    isError: Boolean = false,
    keyboard: KeyboardType = KeyboardType.Text,
    hidden: Boolean = false,
    last: Boolean = false,
    onDone: () -> Unit = {},
    trailing: (@Composable () -> Unit)? = null,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValue,
        label = { Text(stringResource(label)) },
        leadingIcon = { Icon(icon, contentDescription = null, tint = Glass.Orange) },
        trailingIcon = trailing,
        isError = isError,
        singleLine = true,
        shape = RoundedCornerShape(16.dp),
        visualTransformation = if (hidden) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = KeyboardOptions(keyboardType = keyboard, imeAction = if (last) ImeAction.Done else ImeAction.Next),
        keyboardActions = KeyboardActions(onDone = { onDone() }),
        colors = OutlinedTextFieldDefaults.colors(
            focusedContainerColor = Color(0x47000000), unfocusedContainerColor = Color(0x47000000),
            unfocusedBorderColor = Glass.Stroke, focusedBorderColor = Glass.Orange,
        ),
        modifier = Modifier.fillMaxWidth().padding(bottom = 10.dp),
    )
}

/** How strong a new password looks: a hint, never a rule beyond the minimum length. */
@Composable
private fun Strength(password: String) {
    val score = when {
        password.length < MIN_PASSWORD -> 0
        else -> 1 + listOf(
            password.length >= 12,
            password.any { it.isDigit() } && password.any { it.isLetter() },
            password.any { !it.isLetterOrDigit() } || (password.any { it.isUpperCase() } && password.any { it.isLowerCase() }),
        ).count { it }
    }
    val (text, color) = when (score) {
        0 -> R.string.password_short to Glass.TextFaint
        1 -> R.string.password_weak to Glass.Red
        2 -> R.string.password_ok to Glass.Amber
        else -> R.string.password_strong to Glass.Green
    }
    val shown by animateFloatAsState(score / 4f, tween(300), label = "strength")
    Spacer(Modifier.height(2.dp))
    LinearProgressIndicator(
        progress = { if (password.isEmpty()) 0f else maxOf(shown, 0.08f) },
        modifier = Modifier.fillMaxWidth().height(5.dp).clip(RoundedCornerShape(3.dp)),
        color = color, trackColor = Glass.Fill,
    )
    Text(
        if (password.isEmpty()) stringResource(R.string.account_password_hint) else stringResource(text),
        color = if (password.isEmpty()) Glass.TextFaint else color, style = MaterialTheme.typography.labelSmall,
        modifier = Modifier.padding(top = 4.dp),
    )
}

/** The big orange button: shrinks a little under the finger, spins while waiting. */
@Composable
private fun GlowButton(text: String, mark: String, busy: Boolean, enabled: Boolean, onClick: () -> Unit) {
    val press = remember { MutableInteractionSource() }
    val pressed by press.collectIsPressedAsState()
    val scale by animateFloatAsState(if (pressed) 0.97f else 1f, tween(120), label = "press")
    Box(
        Modifier
            .fillMaxWidth()
            .height(56.dp)
            .scale(scale)
            .clip(RoundedCornerShape(18.dp))
            .background(if (enabled || busy) Glass.Accent else Brush.linearGradient(listOf(Glass.FillStrong, Glass.Fill)))
            .clickable(interactionSource = press, indication = null, enabled = enabled && !busy, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        if (busy) {
            CircularProgressIndicator(Modifier.size(24.dp), color = Color(0xFF2A0E00), strokeWidth = 2.5.dp)
        } else {
            Text(
                "$text  $mark", fontWeight = FontWeight.ExtraBold, fontSize = 17.sp,
                color = if (enabled) Color(0xFF2A0E00) else Glass.TextFaint,
            )
        }
    }
}

/** Arabic / English, from the top corner. */
@Composable
private fun LanguagePill() {
    val arabic = AppCompatDelegate.getApplicationLocales().toLanguageTags().let {
        if (it.isEmpty()) java.util.Locale.getDefault().language == "ar" else it.startsWith("ar")
    }
    Box(
        Modifier
            .clip(RoundedCornerShape(50))
            .background(Color(0x14FFFFFF))
            .border(1.dp, Color(0x40FFFFFF), RoundedCornerShape(50))
            .clickable { AppCompatDelegate.setApplicationLocales(LocaleListCompat.forLanguageTags(if (arabic) "en" else "ar")) }
            .padding(horizontal = 14.dp, vertical = 7.dp),
    ) {
        Text("🌐  " + if (arabic) "English" else "العربية", style = MaterialTheme.typography.labelLarge)
    }
}

fun accountErrorText(error: AccountError): Int = when (error) {
    AccountError.NAME -> R.string.account_err_name
    AccountError.LOGIN -> R.string.account_err_login
    AccountError.PASSWORD -> R.string.account_err_password
    AccountError.EXISTS -> R.string.account_err_exists
    AccountError.WRONG -> R.string.account_err_wrong
    AccountError.CLOSED -> R.string.account_err_closed
    AccountError.TOO_MANY -> R.string.account_err_too_many
    AccountError.UNREACHABLE -> R.string.problem_unreachable
    AccountError.OTHER -> R.string.account_err_other
}
