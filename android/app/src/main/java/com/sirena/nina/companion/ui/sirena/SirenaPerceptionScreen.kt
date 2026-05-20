package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.data.SlamOccupancyGrid
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Layout aligned with desktop [sirena_ui.screens.perception_screen.PerceptionScreen]:
 * breadcrumb + pills row, three equal sensor columns (min viewport 220dp), autonomy footer.
 */
@Composable
fun SirenaPerceptionScreen(
    vm: CompanionViewModel,
    daemonUrl: String?,
    caps: JSONObject? = null,
    shellCompact: Boolean = false,
) {
    val scope = rememberCoroutineScope()
    val jetsonLink by vm.jetsonLink.collectAsStateWithLifecycle()
    val bearer by vm.bearerToken.collectAsStateWithLifecycle(initialValue = null)
    val root = daemonUrl?.trimEnd('/') ?: ""
    val online = root.isNotBlank() && jetsonLink.isOnline
    val visionBridge = caps?.optBoolean("vision_bridge_enabled") != false
    val slamBridge = caps?.optBoolean("slam_bridge_enabled") != false
    val depthBridge = caps?.optBoolean("depth_bridge_enabled") != false

    var grid by remember { mutableStateOf<SlamOccupancyGrid?>(null) }
    var slamJson by remember { mutableStateOf<JSONObject?>(null) }
    var visionJson by remember { mutableStateOf<JSONObject?>(null) }
    var depthJson by remember { mutableStateOf<JSONObject?>(null) }
    var autonomyJson by remember { mutableStateOf<JSONObject?>(null) }
    var autonomyOn by remember { mutableStateOf(false) }
    val lidarDepthComingSoon = true

    LaunchedEffect(online, visionBridge) {
        if (!online || !visionBridge) return@LaunchedEffect
        delay(350)
        try {
            if (visionJson?.optBoolean("camera_open") != true) {
                vm.visionOpen()
            }
        } catch (_: Exception) {
        }
    }

    LaunchedEffect(online, slamBridge, depthBridge, visionBridge) {
        if (!online) {
            grid = null
            slamJson = null
            visionJson = null
            depthJson = null
            autonomyJson = null
            autonomyOn = false
            return@LaunchedEffect
        }
        while (isActive) {
            try {
                slamJson = if (slamBridge) vm.fetchSlamStatus() else null
                visionJson = if (visionBridge) vm.fetchVisionStatus() else null
                depthJson =
                    if (depthBridge && !lidarDepthComingSoon) {
                        vm.fetchDepthStatus()
                    } else {
                        null
                    }
                autonomyJson = vm.fetchAutonomyStatus()
                autonomyOn = autonomyJson?.optBoolean("enabled") == true
                val lidarLive = slamJson?.optBoolean("lidar_connected") == true
                grid =
                    if (!lidarDepthComingSoon && slamBridge && lidarLive) {
                        vm.fetchSlamOccupancyGrid()
                    } else {
                        null
                    }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
            }
            val lidarLive = slamJson?.optBoolean("lidar_connected") == true
            val depthLive = depthHardwareLive(depthJson)
            delay(
                when {
                    lidarLive -> 900L
                    depthLive -> 1800L
                    else -> 4000L
                },
            )
        }
    }

    val lidarPill =
        remember(slamJson, online, lidarDepthComingSoon) {
            if (lidarDepthComingSoon) {
                PillTriple("Lidar soon", SirenaPillKind.Neutral, "coming soon")
            } else {
                lidarPillFromSlam(slamJson, online)
            }
        }
    val camPill = remember(visionJson, online) { camPillFromVision(visionJson, online) }
    val depthPill =
        remember(depthJson, online, lidarDepthComingSoon) {
            if (lidarDepthComingSoon) {
                PillTriple("Depth soon", SirenaPillKind.Neutral, "coming soon")
            } else {
                depthPillFromDepth(depthJson, online)
            }
        }
    val autoKind = if (autonomyOn) SirenaPillKind.Warn else SirenaPillKind.Neutral
    val rgbStreamOn =
        online && visionBridge && visionJson?.optBoolean("camera_open") == true
    val depthStreamOn = online && depthBridge && depthHardwareLive(depthJson)

    SirenaAdaptiveContainer(Modifier.padding(10.dp)) { ctx ->
        Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            SirenaAdaptiveHeaderPills(
                ctx = ctx,
                breadcrumb = { SirenaBreadcrumbLine(listOf("Nina", "Perception")) },
                pills = {
                    SirenaStatusPill(
                        text = if (autonomyOn) "Autonomous: ON" else "Autonomous: OFF",
                        kind = autoKind,
                    )
                    SirenaStatusPill(text = lidarPill.chip, kind = lidarPill.kind)
                    SirenaStatusPill(text = camPill.chip, kind = camPill.kind)
                    SirenaStatusPill(text = depthPill.chip, kind = depthPill.kind)
                },
            )

            val expand = ctx.perceptionTriple
            val vMin = ctx.perceptionViewportMin
            if (ctx.perceptionTriple) {
                Row(
                    Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    PerceptionSensorColumn(
                        title = "LiDAR",
                        pillText = lidarPill.card,
                        pillKind = lidarPill.kind,
                        expandViewport = expand,
                        viewportMin = vMin,
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    ) {
                        if (lidarDepthComingSoon) {
                            PerceptionComingSoonLabel()
                        } else {
                            val g = grid
                            if (g != null && g.width > 0 && g.height > 0) {
                                val bmp = remember(g) { g.toBitmap().asImageBitmap() }
                                Image(
                                    bitmap = bmp,
                                    contentDescription = "Occupancy grid",
                                    modifier = Modifier.fillMaxSize(),
                                    contentScale = ContentScale.Fit,
                                )
                            } else {
                                Text(
                                    "Lidar not connected",
                                    modifier = Modifier.align(Alignment.Center),
                                    color = SirenaColors.muted,
                                    fontSize = SirenaType.muted,
                                )
                            }
                        }
                    }
                    PerceptionSensorColumn(
                        title = "RGB camera",
                        pillText = camPill.card,
                        pillKind = camPill.kind,
                        expandViewport = expand,
                        viewportMin = vMin,
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    ) {
                        PerceptionRgbViewport(
                            online = online,
                            visionBridge = visionBridge,
                            rgbStreamOn = rgbStreamOn,
                            visionJson = visionJson,
                            streamUrl = "$root/v1/vision/stream",
                            bearer = bearer,
                        )
                    }
                    PerceptionSensorColumn(
                        title = "Depth camera",
                        pillText = depthPill.card,
                        pillKind = depthPill.kind,
                        expandViewport = expand,
                        viewportMin = vMin,
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    ) {
                        if (lidarDepthComingSoon) {
                            PerceptionComingSoonLabel()
                        } else {
                            PerceptionDepthViewport(
                                online = online,
                                depthBridge = depthBridge,
                                depthStreamOn = depthStreamOn,
                                depthJson = depthJson,
                                streamUrl = "$root/v1/depth/stream",
                                bearer = bearer,
                            )
                        }
                    }
                }
            } else {
                Column(
                    Modifier
                        .weight(1f)
                        .fillMaxWidth()
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    PerceptionSensorColumn(
                        title = "LiDAR",
                        pillText = lidarPill.card,
                        pillKind = lidarPill.kind,
                        expandViewport = false,
                        viewportMin = vMin,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        if (lidarDepthComingSoon) {
                            PerceptionComingSoonLabel()
                        } else {
                            val g = grid
                            if (g != null && g.width > 0 && g.height > 0) {
                                val bmp = remember(g) { g.toBitmap().asImageBitmap() }
                                Image(
                                    bitmap = bmp,
                                    contentDescription = "Occupancy grid",
                                    modifier = Modifier.fillMaxSize(),
                                    contentScale = ContentScale.Fit,
                                )
                            } else {
                                Text(
                                    "Lidar not connected",
                                    modifier = Modifier.align(Alignment.Center),
                                    color = SirenaColors.muted,
                                    fontSize = SirenaType.muted,
                                )
                            }
                        }
                    }
                    PerceptionSensorColumn(
                        title = "RGB camera",
                        pillText = camPill.card,
                        pillKind = camPill.kind,
                        expandViewport = false,
                        viewportMin = vMin,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        PerceptionRgbViewport(
                            online = online,
                            visionBridge = visionBridge,
                            rgbStreamOn = rgbStreamOn,
                            visionJson = visionJson,
                            streamUrl = "$root/v1/vision/stream",
                            bearer = bearer,
                        )
                    }
                    PerceptionSensorColumn(
                        title = "Depth camera",
                        pillText = depthPill.card,
                        pillKind = depthPill.kind,
                        expandViewport = false,
                        viewportMin = vMin,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        if (lidarDepthComingSoon) {
                            PerceptionComingSoonLabel()
                        } else {
                            PerceptionDepthViewport(
                                online = online,
                                depthBridge = depthBridge,
                                depthStreamOn = depthStreamOn,
                                depthJson = depthJson,
                                streamUrl = "$root/v1/depth/stream",
                                bearer = bearer,
                            )
                        }
                    }
                }
            }

            val footerMuted =
                if (autonomyOn) {
                    "Autonomy is on. Nina can use depth and planning when armed from Map/Drive."
                } else {
                    "Autonomy is off. The depth panel above is open for visualization, but Nina " +
                        "won't drive herself until the toggle is ON."
                }
            if (ctx.footerSingleRow) {
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    SirenaPrimaryButton(
                        text = if (autonomyOn) "Autonomous mode: ON" else "Autonomous mode: OFF",
                        onClick = {
                            scope.launch {
                                val want = !autonomyOn
                                val r = vm.postAutonomyEnabled(want)
                                if (r != null && r.optBoolean("ok", true)) {
                                    autonomyOn = r.optBoolean("enabled", want)
                                }
                            }
                        },
                        enabled = online && autonomyJson?.optBoolean("bridge_enabled", true) != false,
                        modifier = Modifier.height(34.dp),
                    )
                    SirenaMutedText(
                        text = footerMuted,
                        modifier = Modifier.weight(1f),
                        maxLines = 4,
                    )
                }
            } else {
                Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    SirenaPrimaryButton(
                        text = if (autonomyOn) "Autonomous mode: ON" else "Autonomous mode: OFF",
                        onClick = {
                            scope.launch {
                                val want = !autonomyOn
                                val r = vm.postAutonomyEnabled(want)
                                if (r != null && r.optBoolean("ok", true)) {
                                    autonomyOn = r.optBoolean("enabled", want)
                                }
                            }
                        },
                        enabled = online && autonomyJson?.optBoolean("bridge_enabled", true) != false,
                        modifier =
                            Modifier
                                .fillMaxWidth()
                                .height(34.dp),
                    )
                    SirenaMutedText(text = footerMuted, maxLines = 6)
                }
            }
        }
    }
}

private data class PillTriple(
    val chip: String,
    val kind: SirenaPillKind,
    val card: String,
)

private fun lidarPillFromSlam(s: JSONObject?, online: Boolean): PillTriple {
    if (!online || s == null) return PillTriple("Lidar -", SirenaPillKind.Neutral, "offline")
    val ok = s.optBoolean("lidar_connected", false)
    val kind = if (ok) SirenaPillKind.Ok else SirenaPillKind.Neutral
    return PillTriple(
        "Lidar " + if (ok) "ok" else "—",
        kind,
        if (ok) "live" else "not connected",
    )
}

private fun camPillFromVision(v: JSONObject?, online: Boolean): PillTriple {
    if (!online || v == null) {
        return PillTriple("Cam -", SirenaPillKind.Neutral, "waiting")
    }
    val open = v.optBoolean("camera_open", false)
    val kind =
        when {
            open && (v.optBoolean("face_ready") || v.optBoolean("object_ready")) -> SirenaPillKind.Ok
            open -> SirenaPillKind.Neutral
            else -> SirenaPillKind.Neutral
        }
    val chip = if (open) "Cam ok" else "Cam —"
    val card = if (open) "streaming" else "waiting"
    return PillTriple(chip, kind, card)
}

private fun fmtDepthMm(v: Int?): String {
    if (v == null) return "\u2014"
    return if (v >= 1000) String.format("%.2f m", v / 1000.0) else "$v mm"
}

private fun depthOverlayText(d: JSONObject?): String {
    if (d == null) return "F: \u2014   L: \u2014   R: \u2014"
    val f = if (d.has("forward_min_mm") && !d.isNull("forward_min_mm")) d.optInt("forward_min_mm") else null
    val l = if (d.has("left_min_mm") && !d.isNull("left_min_mm")) d.optInt("left_min_mm") else null
    val r = if (d.has("right_min_mm") && !d.isNull("right_min_mm")) d.optInt("right_min_mm") else null
    return "F: ${fmtDepthMm(f)}   L: ${fmtDepthMm(l)}   R: ${fmtDepthMm(r)}"
}

private fun depthHardwareLive(d: JSONObject?): Boolean {
    if (d == null) return false
    if (!d.optBoolean("bridge_enabled", true)) return false
    val msg = d.optString("message", "").trim()
    return d.optBoolean("ok", true) && !msg.contains("unavailable", ignoreCase = true)
}

@Composable
private fun BoxScope.PerceptionRgbViewport(
    online: Boolean,
    visionBridge: Boolean,
    rgbStreamOn: Boolean,
    visionJson: JSONObject?,
    streamUrl: String,
    bearer: String?,
) {
    when {
        !online -> {
            Text(
                "Connect to a robot to view cameras",
                modifier = Modifier.align(Alignment.Center),
                color = SirenaColors.muted,
                fontSize = SirenaType.muted,
            )
        }
        !visionBridge -> {
            Text(
                "Vision bridge off on Jetson",
                modifier = Modifier.align(Alignment.Center),
                color = SirenaColors.muted,
                fontSize = SirenaType.muted,
            )
        }
        rgbStreamOn -> {
            SirenaMjpegImage(
                streamUrl = streamUrl,
                bearer = bearer,
                modifier = Modifier.fillMaxSize(),
                maxLongEdge = SirenaMjpegPreviewMaxLongEdge,
                streamEnabled = true,
                targetFps = SirenaMjpegDefaultTargetFps,
                idleMessage = "USB camera not streaming",
            )
        }
        else -> {
            val hint =
                visionJson?.optString("message")?.trim()?.take(80)?.ifBlank {
                    "Opening USB camera…"
                } ?: "Opening USB camera…"
            Text(
                hint,
                modifier = Modifier.align(Alignment.Center).padding(12.dp),
                color = SirenaColors.muted,
                fontSize = SirenaType.muted,
            )
        }
    }
}

@Composable
private fun BoxScope.PerceptionDepthViewport(
    online: Boolean,
    depthBridge: Boolean,
    depthStreamOn: Boolean,
    depthJson: JSONObject?,
    streamUrl: String,
    bearer: String?,
) {
    when {
        !online -> {
            Text(
                "Connect to a robot to view depth",
                modifier = Modifier.align(Alignment.Center),
                color = SirenaColors.muted,
                fontSize = SirenaType.muted,
            )
        }
        !depthBridge -> {
            Text(
                "Depth bridge off on Jetson",
                modifier = Modifier.align(Alignment.Center),
                color = SirenaColors.muted,
                fontSize = SirenaType.muted,
            )
        }
        depthStreamOn -> {
            Box(Modifier.fillMaxSize()) {
                SirenaMjpegImage(
                    streamUrl = streamUrl,
                    bearer = bearer,
                    modifier = Modifier.fillMaxSize(),
                    maxLongEdge = SirenaMjpegPreviewMaxLongEdge,
                    streamEnabled = true,
                    idleMessage = "Depth stream unavailable",
                )
                Text(
                    depthOverlayText(depthJson),
                    modifier =
                        Modifier
                            .align(Alignment.BottomCenter)
                            .padding(6.dp),
                    color = SirenaColors.text,
                    fontSize = SirenaType.muted,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }
        else -> {
            Text(
                "Depth camera not connected",
                modifier = Modifier.align(Alignment.Center),
                color = SirenaColors.muted,
                fontSize = SirenaType.muted,
            )
        }
    }
}

private fun depthPillFromDepth(d: JSONObject?, online: Boolean): PillTriple {
    if (!online || d == null || d.optBoolean("bridge_enabled", true) == false) {
        return PillTriple("Depth -", SirenaPillKind.Neutral, "not installed")
    }
    val connected = depthHardwareLive(d)
    return PillTriple(
        if (connected) "Depth ok" else "Depth —",
        if (connected) SirenaPillKind.Ok else SirenaPillKind.Neutral,
        if (connected) "live" else "waiting",
    )
}

@Composable
private fun PerceptionSensorColumn(
    title: String,
    pillText: String,
    pillKind: SirenaPillKind,
    expandViewport: Boolean,
    viewportMin: Dp,
    modifier: Modifier = Modifier,
    viewport: @Composable BoxScope.() -> Unit,
) {
    SirenaCard(
        modifier = modifier,
        contentPadding = androidx.compose.foundation.layout.PaddingValues(8.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            SirenaCardTitle(title)
            Spacer(Modifier.weight(1f))
            SirenaStatusPill(pillText, pillKind)
        }
        val base =
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(SirenaDimens.cardRadiusSubtle))
                .background(SirenaColors.cloud)
        // Stacked: fixed 4:3 frame so LiDAR / RGB / depth tiles stay consistent; wide: grow with row.
        Box(
            modifier =
                base.then(
                    if (expandViewport) {
                        Modifier
                            .weight(1f)
                            .defaultMinSize(minHeight = viewportMin)
                    } else {
                        Modifier.fillMaxWidth().aspectRatio(4f / 3f)
                    },
                ),
            content = viewport,
        )
    }
}
