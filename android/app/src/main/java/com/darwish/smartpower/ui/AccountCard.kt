package com.darwish.smartpower.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.darwish.smartpower.R
import kotlinx.coroutines.launch

/**
 * Sign in or create a customer account, shown on Home while the app is not signed in.
 * [expired]: the phone had a sign-in that the server no longer accepts.
 */
@Composable
fun AccountCard(vm: AppViewModel, expired: Boolean, onServerPassword: () -> Unit) {
    val scope = rememberCoroutineScope()
    var newAccount by rememberSaveable { mutableStateOf(!expired) }
    var name by rememberSaveable { mutableStateOf("") }
    var login by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var showPassword by rememberSaveable { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<AccountError?>(null) }

    fun submit() {
        busy = true
        error = null
        scope.launch {
            error = if (newAccount) vm.signUp(name, login, password) else vm.signIn(login, password)
            busy = false
        }
    }

    GlassCard(Modifier.fillMaxWidth(), padding = 20.dp) {
        Text("⚡ " + stringResource(R.string.account_welcome), style = MaterialTheme.typography.titleLarge)
        Text(
            stringResource(if (expired) R.string.account_expired else R.string.account_intro),
            color = Glass.TextSoft, style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(selected = newAccount, onClick = { newAccount = true; error = null },
                label = { Text(stringResource(R.string.account_create)) })
            FilterChip(selected = !newAccount, onClick = { newAccount = false; error = null },
                label = { Text(stringResource(R.string.account_sign_in)) })
        }
        Spacer(Modifier.height(8.dp))
        if (newAccount) {
            OutlinedTextField(
                value = name,
                onValueChange = { name = it.take(30); error = null },
                label = { Text(stringResource(R.string.account_name)) },
                isError = error == AccountError.NAME,
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        OutlinedTextField(
            value = login,
            onValueChange = { login = it; error = null },
            label = { Text(stringResource(R.string.account_login)) },
            placeholder = { Text("01xxxxxxxxx") },
            isError = error == AccountError.LOGIN || error == AccountError.EXISTS,
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Next),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = password,
            onValueChange = { password = it; error = null },
            label = { Text(stringResource(R.string.account_password)) },
            supportingText = { if (newAccount) Text(stringResource(R.string.account_password_hint)) },
            isError = error == AccountError.PASSWORD || error == AccountError.WRONG,
            singleLine = true,
            visualTransformation = if (showPassword) VisualTransformation.None else PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
            trailingIcon = {
                TextButton(onClick = { showPassword = !showPassword }) {
                    Text(stringResource(if (showPassword) R.string.hide else R.string.show))
                }
            },
            modifier = Modifier.fillMaxWidth(),
        )
        error?.let {
            Spacer(Modifier.height(6.dp))
            Text(stringResource(accountErrorText(it)), color = Glass.Red, style = MaterialTheme.typography.bodySmall)
        }
        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(onClick = ::submit, enabled = !busy && login.isNotBlank() && password.isNotEmpty(),
                modifier = Modifier.weight(1f).height(50.dp)) {
                Text(stringResource(if (newAccount) R.string.account_create else R.string.account_sign_in), fontWeight = FontWeight.Bold)
            }
            if (busy) CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
        }
        TextButton(onClick = onServerPassword) {
            Text(stringResource(R.string.account_have_password), color = Glass.TextSoft)
        }
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
