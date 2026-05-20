package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Charcoal footer strip — mirrors Qt [sirena_ui.widgets.status_bar.StatusBar] (`footerBar`).
 * ``Bus / Wi‑Fi / Battery / Voice`` + right caption and optional **Retry / Continue**.
 */
@Composable
fun SirenaShellFooter(
    busOk: Boolean,
    wifiOk: Boolean,
    batteryOk: Boolean,
    batteryWarn: Boolean = false,
    voiceOk: Boolean,
    /** When true and [voiceOk] is false, dot uses warn (amber) like Qt ESP placeholder. */
    voiceWarn: Boolean = false,
    rightCaption: String,
    /** When true, [rightCaption] uses error tint (e.g. connection refresh failed). */
    connectionCaptionIsError: Boolean = false,
    /** Shown only with [onConnectionContinue]; compact actions for connection errors. */
    onConnectionRetry: (() -> Unit)? = null,
    onConnectionContinue: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier
            .fillMaxWidth()
            .heightIn(min = 26.dp, max = 44.dp)
            .background(SirenaColors.charcoal)
            .padding(horizontal = 12.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        FooterDotLabel("Bus", busOk)
        FooterDotLabel("Wi-Fi", wifiOk)
        FooterDotLabel("Battery", ok = batteryOk, warn = batteryWarn)
        FooterDotLabel("Voice", ok = voiceOk, warn = voiceWarn)
        Text(
            text = rightCaption,
            modifier =
                Modifier
                    .weight(1f)
                    .padding(start = 8.dp, end = 4.dp),
            color =
                if (connectionCaptionIsError) {
                    SirenaColors.pillErrorFg
                } else {
                    SirenaColors.navRailTextSecondary
                },
            fontSize = 12.sp,
            fontWeight = FontWeight.Normal,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.End,
        )
        onConnectionRetry?.let { retry ->
            onConnectionContinue?.let { cont ->
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(0.dp),
                ) {
                    TextButton(
                        onClick = retry,
                        contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp),
                    ) {
                        Text(
                            "Retry",
                            color = SirenaColors.red,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                    TextButton(
                        onClick = cont,
                        contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp),
                    ) {
                        Text(
                            "Continue",
                            color = SirenaColors.muted,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Normal,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun FooterDotLabel(label: String, ok: Boolean, warn: Boolean = false) {
    val dotColor =
        when {
            ok -> SirenaColors.success
            warn -> SirenaColors.warning
            else -> SirenaColors.danger
        }
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text("\u25CF", color = dotColor, fontSize = 12.sp)
        Text(
            label,
            color = SirenaColors.navRailTextPrimary,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
        )
    }
}
