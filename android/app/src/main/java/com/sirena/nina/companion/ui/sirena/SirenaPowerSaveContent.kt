package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PowerSettingsNew
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.data.PowerStateUi

fun powerStatePillKind(state: String): SirenaPillKind =
    when (state.trim().lowercase()) {
        "sleep" -> SirenaPillKind.Warn
        "idle" -> SirenaPillKind.Neutral
        else -> SirenaPillKind.Ok
    }

private fun formatCountdown(power: PowerStateUi): String? =
    when {
        !power.enabled -> "Power saving disabled on robot"
        power.isAsleep -> "Screen is black — tap robot display or wake here"
        power.isIdle ->
            power.hint
                ?: "Camera off — asleep in ${power.sleepSecRemaining}s if untouched"
        power.state == "active" && power.idleSecRemaining > 0 ->
            "Idle in ${power.idleSecRemaining}s · sleep in ${power.sleepSecRemaining}s"
        else -> null
    }

/** Compact circular wake control (Home / Settings) — does not stretch full width. */
@Composable
fun SirenaWakeCircleButton(
    inFlight: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val label = if (inFlight) "Waking robot" else "Wake robot"
    val bg = if (enabled) SirenaColors.red else SirenaColors.disabledFill
    Box(
        modifier =
            modifier
                .size(48.dp)
                .semantics {
                    role = Role.Button
                    contentDescription = label
                }
                .clip(CircleShape)
                .background(bg)
                .clickable(
                    enabled = enabled && !inFlight,
                    onClick = onClick,
                ),
        contentAlignment = Alignment.Center,
    ) {
        if (inFlight) {
            CircularProgressIndicator(
                modifier = Modifier.size(22.dp),
                color = SirenaColors.white,
                strokeWidth = 2.dp,
            )
        } else {
            Icon(
                Icons.Filled.PowerSettingsNew,
                contentDescription = null,
                tint = SirenaColors.white,
                modifier = Modifier.size(26.dp),
            )
        }
    }
}

/**
 * Shared power-save status + wake control (Home banner and Settings → Display).
 */
@Composable
fun SirenaPowerSaveSection(
    power: PowerStateUi?,
    jetsonOnline: Boolean,
    wakeInFlight: Boolean,
    wakeMessage: String?,
    onWake: () -> Unit,
    modifier: Modifier = Modifier,
    compact: Boolean = false,
) {
    SirenaCard(modifier = modifier) {
        SirenaSectionLabel("Power save")
        Spacer(Modifier.height(6.dp))
        if (!jetsonOnline) {
            SirenaMutedText(
                "Connect to the Jetson on Home to see sleep state and wake the kiosk.",
                maxLines = 4,
            )
            return@SirenaCard
        }
        if (power == null) {
            SirenaMutedText("Loading power state…", maxLines = 2)
            return@SirenaCard
        }
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                Modifier.weight(1f),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                SirenaStatusPill(
                    text = power.statusLabel,
                    kind = powerStatePillKind(power.state),
                )
                if (!power.enabled) {
                    SirenaStatusPill("Off", SirenaPillKind.Neutral)
                }
            }
            if (power.needsWake && power.wakeTabletEnabled) {
                SirenaWakeCircleButton(
                    inFlight = wakeInFlight,
                    enabled = true,
                    onClick = onWake,
                )
            }
        }
        formatCountdown(power)?.let { line ->
            Spacer(Modifier.height(8.dp))
            Text(
                line,
                color = SirenaColors.muted,
                fontSize = SirenaType.muted,
            )
        }
        if (power.isAsleep && !power.wakeTabletEnabled) {
            Spacer(Modifier.height(8.dp))
            SirenaMutedText(
                "Tablet wake is disabled on the robot (NINA_POWER_WAKE_TABLET=0). " +
                    "Tap the robot screen instead.",
                maxLines = 4,
            )
        }
        if (!compact) {
            Spacer(Modifier.height(8.dp))
            SirenaMutedText(
                "Idle/sleep timers are set on the Jetson (Sirena UI Settings → Display " +
                    "or /etc/nina-link/navigation.env).",
                maxLines = 5,
            )
        }
        wakeMessage?.let { msg ->
            Spacer(Modifier.height(8.dp))
            Text(
                msg,
                color = if (msg.contains("failed", ignoreCase = true)) {
                    SirenaColors.danger
                } else {
                    SirenaColors.muted
                },
                fontSize = SirenaType.muted,
            )
        }
    }
}
