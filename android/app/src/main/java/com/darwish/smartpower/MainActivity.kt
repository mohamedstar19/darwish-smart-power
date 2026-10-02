package com.darwish.smartpower

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import com.darwish.smartpower.ui.DarwishApp
import com.darwish.smartpower.ui.DarwishTheme

// AppCompatActivity (not ComponentActivity) so the in-app language switch works on Android 8-12 too.
class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent {
            DarwishTheme { DarwishApp() }
        }
    }
}
