package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.togetherWith
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.data.Prefs
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

fun daemonUrlsMatch(savedUrl: String, daemonBaseUrl: String): Boolean {
    val a = Prefs.normalizeBaseUrl(savedUrl)
    val b = Prefs.normalizeBaseUrl(daemonBaseUrl)
    return a.isNotBlank() && b.isNotBlank() && a.equals(b, ignoreCase = true)
}

/**
 * Connect / Connected (green) / Disconnect for a discovered link daemon row.
 */
@Composable
fun SirenaDaemonConnectButton(
    vm: CompanionViewModel,
    daemonBaseUrl: String,
    savedUrl: String,
    linkOnline: Boolean,
    modifier: Modifier = Modifier,
    onConnected: (() -> Unit)? = null,
    onDisconnected: (() -> Unit)? = null,
    onConnectError: ((String) -> Unit)? = null,
) {
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var showConnectedFlash by remember { mutableStateOf(false) }
    var confirmDisconnect by remember { mutableStateOf(false) }
    val isThisRobot = daemonUrlsMatch(savedUrl, daemonBaseUrl) && linkOnline

    LaunchedEffect(isThisRobot) {
        if (isThisRobot) {
            showConnectedFlash = true
            delay(2500)
            showConnectedFlash = false
        } else {
            showConnectedFlash = false
        }
    }

    val greenColors =
        ButtonDefaults.buttonColors(
            containerColor = SirenaColors.pillOkBg,
            contentColor = SirenaColors.pillOkFg,
            disabledContainerColor = SirenaColors.pillOkBg,
            disabledContentColor = SirenaColors.pillOkFg,
        )

    val connectPhase =
        when {
            isThisRobot && showConnectedFlash -> "flash"
            isThisRobot -> "disconnect"
            busy -> "busy"
            else -> "connect"
        }
    AnimatedContent(
        targetState = connectPhase,
        modifier = modifier,
        transitionSpec = {
            (fadeIn(tween(180)) + scaleIn(initialScale = 0.92f, animationSpec = tween(180))) togetherWith
                (fadeOut(tween(120)) + scaleOut(targetScale = 0.96f, animationSpec = tween(120)))
        },
        label = "connectBtn",
    ) { phase ->
        when (phase) {
            "flash" -> {
                Button(
                    onClick = {},
                    enabled = false,
                    modifier = Modifier.height(40.dp).widthIn(min = 108.dp),
                    shape = RoundedCornerShape(10.dp),
                    colors = greenColors,
                    border = BorderStroke(1.dp, SirenaColors.pillOkFg.copy(alpha = 0.35f)),
                ) {
                    Text("Connected", fontWeight = FontWeight.SemiBold)
                }
            }
            "disconnect" -> {
                Button(
                    onClick = {
                        if (busy) return@Button
                        confirmDisconnect = true
                    },
                    enabled = !busy,
                    modifier = Modifier.height(40.dp).widthIn(min = 108.dp),
                    shape = RoundedCornerShape(10.dp),
                    colors = greenColors,
                    border = BorderStroke(1.dp, SirenaColors.pillOkFg.copy(alpha = 0.4f)),
                ) {
                    Text(if (busy) "…" else "Disconnect", fontWeight = FontWeight.SemiBold)
                }
            }
            else -> {
                Button(
                    onClick = {
                        if (busy) return@Button
                        scope.launch {
                            busy = true
                            try {
                                val err = vm.connectDiscoveredAndRefresh(daemonBaseUrl)
                                if (err == null) {
                                    onConnected?.invoke()
                                } else {
                                    onConnectError?.invoke(err)
                                }
                            } finally {
                                busy = false
                            }
                        }
                    },
                    enabled = !busy,
                    modifier = Modifier.height(40.dp).widthIn(min = 108.dp),
                    shape = RoundedCornerShape(10.dp),
                    colors =
                        ButtonDefaults.buttonColors(
                            containerColor = SirenaColors.red,
                            contentColor = Color.White,
                        ),
                ) {
                    Text(if (busy) "…" else "Connect", fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }

    if (confirmDisconnect) {
        SirenaConfirmDialog(
            onDismiss = { confirmDisconnect = false },
            message = "Disconnect from this robot on the tablet?",
            confirmText = "Disconnect",
            dangerous = true,
            onConfirm = {
                confirmDisconnect = false
                scope.launch {
                    busy = true
                    try {
                        vm.disconnectRobot()
                        onDisconnected?.invoke()
                    } finally {
                        busy = false
                    }
                }
            },
        )
    }
}
