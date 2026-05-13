package com.sirena.nina.companion

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModelProvider
import com.sirena.nina.companion.ui.NinaApp
import com.sirena.nina.companion.ui.ProductHubScreen
import com.sirena.nina.companion.ui.SplashVideo
import com.sirena.nina.companion.ui.theme.SirenaTheme
import com.sirena.nina.companion.util.NinaFileLogger

class MainActivity : ComponentActivity() {

    private lateinit var vm: CompanionViewModel
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        NinaFileLogger.install(applicationContext)
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        WindowCompat.getInsetsController(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.statusBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
        vm = ViewModelProvider(
            this,
            ViewModelProvider.AndroidViewModelFactory.getInstance(application),
        )[CompanionViewModel::class.java]

        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        networkCallback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                vm.refreshStatus()
            }
        }
        cm.registerDefaultNetworkCallback(networkCallback!!)

        setContent {
            /** `splash` → product hub → Nina console; survives rotation. */
            var mainPhase by rememberSaveable { mutableStateOf("splash") }
            SirenaTheme(forceLight = true) {
                when (mainPhase) {
                    "splash" -> SplashVideo(onFinished = { mainPhase = "hub" })
                    "hub" -> ProductHubScreen(vm = vm, onOpenNina = { mainPhase = "nina" })
                    else -> NinaApp(vm = vm, onBackToProductHub = { mainPhase = "hub" })
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        if (::vm.isInitialized) vm.refreshStatus()
    }

    override fun onDestroy() {
        networkCallback?.let { cb ->
            try {
                (getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager)
                    .unregisterNetworkCallback(cb)
            } catch (_: Exception) {
                // ignore
            }
        }
        networkCallback = null
        super.onDestroy()
    }
}
