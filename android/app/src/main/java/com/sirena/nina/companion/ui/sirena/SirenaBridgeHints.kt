package com.sirena.nina.companion.ui.sirena

import android.content.res.Configuration
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** Landscape or wide content: keep link / error banners to one shallow row. */
@Composable
fun rememberSlimStatusBanner(): Boolean {
    val c = LocalConfiguration.current
    return c.orientation == Configuration.ORIENTATION_LANDSCAPE || c.screenWidthDp >= 520
}

/**
 * Compact status strip for global errors and callouts — tall copy in portrait,
 * single horizontal band in landscape / wide layout.
 */
@Composable
fun SirenaInlineStatusBanner(
    modifier: Modifier = Modifier,
    isError: Boolean,
    title: String,
    message: String,
    detailHint: String? = null,
    primaryLabel: String? = null,
    onPrimary: (() -> Unit)? = null,
    secondaryLabel: String? = null,
    onSecondary: (() -> Unit)? = null,
) {
    val slim = rememberSlimStatusBanner()
    val bg = if (isError) SirenaColors.pillErrorBg else SirenaColors.calloutBg
    val fg = if (isError) SirenaColors.pillErrorFg else SirenaColors.text
    val shape = RoundedCornerShape(SirenaDimens.cardRadiusSubtle)
    Surface(
        modifier =
            modifier
                .fillMaxWidth()
                .clip(shape)
                .border(BorderStroke(1.dp, SirenaColors.border), shape),
        shape = shape,
        color = bg,
        shadowElevation = 0.dp,
        tonalElevation = 0.dp,
    ) {
        if (slim) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = 40.dp, max = 54.dp)
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                val head =
                    if (title.isNotBlank()) {
                        "$title · $message"
                    } else {
                        message
                    }
                Text(
                    head,
                    modifier = Modifier.weight(1f),
                    color = fg,
                    fontWeight = if (title.isNotBlank()) FontWeight.SemiBold else FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    fontSize = SirenaType.muted,
                )
                if (primaryLabel != null && onPrimary != null) {
                    TextButton(onClick = onPrimary, modifier = Modifier.heightIn(max = 40.dp)) {
                        Text(primaryLabel, color = SirenaColors.red, fontWeight = FontWeight.SemiBold)
                    }
                }
                if (secondaryLabel != null && onSecondary != null) {
                    TextButton(onClick = onSecondary, modifier = Modifier.heightIn(max = 40.dp)) {
                        Text(secondaryLabel, color = fg, fontWeight = FontWeight.Medium)
                    }
                }
            }
        } else {
            Column(
                Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                if (title.isNotBlank()) {
                    Text(title, fontWeight = FontWeight.SemiBold, color = fg)
                }
                if (isError) {
                    Text(message, color = fg, fontSize = SirenaType.muted, maxLines = 8)
                } else {
                    SirenaMutedText(message, maxLines = 4)
                }
                if (!detailHint.isNullOrBlank()) {
                    SirenaMutedText(detailHint, maxLines = 3)
                }
                if ((primaryLabel != null && onPrimary != null) ||
                    (secondaryLabel != null && onSecondary != null)
                ) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (primaryLabel != null && onPrimary != null) {
                            TextButton(onClick = onPrimary) {
                                Text(primaryLabel, color = SirenaColors.red, fontWeight = FontWeight.SemiBold)
                            }
                        }
                        if (secondaryLabel != null && onSecondary != null) {
                            TextButton(onClick = onSecondary) {
                                Text(secondaryLabel, color = fg, fontWeight = FontWeight.Medium)
                            }
                        }
                    }
                }
            }
        }
    }
}

/** Jetson link daemon offline — one-line strip in landscape with optional Network shortcut. */
@Composable
fun SirenaJetsonOfflineStrip(
    lastError: String?,
    modifier: Modifier = Modifier,
    onOpenNetwork: (() -> Unit)? = null,
) {
    val slim = rememberSlimStatusBanner()
    val msg =
        lastError?.trim()?.takeIf { it.isNotEmpty() }
            ?: "Check Wi‑Fi, Find robot, or set the daemon URL in Settings."
    val shape = RoundedCornerShape(SirenaDimens.cardRadiusSubtle)
    Surface(
        modifier =
            modifier
                .fillMaxWidth()
                .clip(shape)
                .border(BorderStroke(1.dp, SirenaColors.border), shape),
        shape = shape,
        color = SirenaColors.calloutBg,
        shadowElevation = 0.dp,
        tonalElevation = 0.dp,
    ) {
        if (slim) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = 40.dp, max = 54.dp)
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    "Offline",
                    fontWeight = FontWeight.SemiBold,
                    color = SirenaColors.text,
                    maxLines = 1,
                )
                Text(
                    msg,
                    modifier = Modifier.weight(1f),
                    color = SirenaColors.muted,
                    fontSize = SirenaType.muted,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (onOpenNetwork != null) {
                    TextButton(onClick = onOpenNetwork, modifier = Modifier.heightIn(max = 40.dp)) {
                        Text("Network", color = SirenaColors.red, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        } else {
            Column(Modifier.padding(horizontal = 12.dp, vertical = 10.dp)) {
                Text("Link daemon unreachable", fontWeight = FontWeight.SemiBold, color = SirenaColors.text)
                SirenaMutedText(msg, maxLines = 3)
                if (onOpenNetwork != null) {
                    TextButton(onClick = onOpenNetwork) {
                        Text("Open Network", color = SirenaColors.red, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }
    }
}

/**
 * Surfaces Jetson `nina-link` feature flags from [GET /v1/robot/capabilities] so the tablet
 * does not look “broken” when HTTP returns 503 — the bridge env vars are simply off.
 */
@Composable
fun SirenaJetsonFeatureGateCallout(
    linkOnline: Boolean,
    caps: JSONObject?,
    flagKey: String,
    title: String,
    envLine: String,
) {
    if (!linkOnline) return
    val slim = rememberSlimStatusBanner()
    if (caps == null) {
        val capsMsg =
            "Capabilities not loaded — open Network and tap Refresh status."
        if (slim) {
            SirenaInlineStatusBanner(
                isError = false,
                title = title,
                message = capsMsg,
            )
        } else {
            SirenaCard(kind = SirenaCardKind.Callout) {
                Text(title, fontWeight = FontWeight.SemiBold, color = SirenaColors.text)
                SirenaMutedText(
                    "Robot capabilities are not loaded yet. Open Network (or wait) and tap Refresh status, then return here.",
                    maxLines = 4,
                )
            }
        }
        return
    }
    if (caps.optBoolean(flagKey)) return
    if (slim) {
        SirenaInlineStatusBanner(
            isError = false,
            title = title,
            message = envLine,
        )
    } else {
        SirenaCard(kind = SirenaCardKind.Callout) {
            Text(title, fontWeight = FontWeight.SemiBold, color = SirenaColors.text)
            SirenaMutedText(envLine, maxLines = 4)
            Spacer(Modifier.height(4.dp))
            SirenaMutedText(
                "Full matrix: docs/COMPANION_CONTROLS.md in the repo. Restart nina-link after changing env.",
                maxLines = 3,
            )
        }
    }
}

/** Record + static audio gates used by the Actions screen. */
@Composable
fun SirenaActionsJetsonGateCallouts(
    linkOnline: Boolean,
    caps: JSONObject?,
) {
    if (!linkOnline) return
    val slim = rememberSlimStatusBanner()
    if (caps == null) {
        if (slim) {
            SirenaInlineStatusBanner(
                isError = false,
                title = "Actions",
                message = "Capabilities not loaded — open Network, tap Refresh status.",
            )
        } else {
            SirenaCard(kind = SirenaCardKind.Callout) {
                Text("Actions — capabilities not loaded", fontWeight = FontWeight.SemiBold, color = SirenaColors.text)
                SirenaMutedText(
                    "Open Network and tap Refresh status so the tablet can read Jetson feature flags.",
                    maxLines = 3,
                )
            }
        }
        return
    }
    val recordOk = caps.optBoolean("record_bridge_enabled")
    val staticOk = caps.optBoolean("actions_static_enabled")
    if (recordOk && staticOk) return
    if (slim) {
        val parts = mutableListOf<String>()
        if (!recordOk) parts.add("record bridge off")
        if (!staticOk) parts.add("static actions off")
        SirenaInlineStatusBanner(
            isError = false,
            title = "Jetson",
            message = parts.joinToString(" · ") + " — see COMPANION_CONTROLS.md",
        )
    } else {
        SirenaCard(kind = SirenaCardKind.Callout) {
            Text("Some Actions features are off on the Jetson", fontWeight = FontWeight.SemiBold, color = SirenaColors.text)
            Column {
                if (!recordOk) {
                    SirenaMutedText(
                        "• Remote record start/stop: set NINA_LINK_ENABLE_RECORD_BRIDGE=1 and restart nina-link.",
                        maxLines = 3,
                    )
                }
                if (!staticOk) {
                    Spacer(Modifier.height(4.dp))
                    SirenaMutedText(
                        "• Audio file / offset / generate APIs: set NINA_LINK_ENABLE_ACTIONS_STATIC=1 and restart nina-link.",
                        maxLines = 3,
                    )
                }
            }
            Spacer(Modifier.height(4.dp))
            SirenaMutedText("See docs/COMPANION_CONTROLS.md.", maxLines = 2)
        }
    }
}
