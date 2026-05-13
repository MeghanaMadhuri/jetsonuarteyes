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
    shellCompact: Boolean = false,
) {
    val scope = rememberCoroutineScope()
    val jetsonLink by vm.jetsonLink.collectAsStateWithLifecycle()
    val bearer by vm.bearerToken.collectAsStateWithLifecycle(initialValue = null)
    val root = daemonUrl?.trimEnd('/') ?: ""
    val online = root.isNotBlank() && jetsonLink.isOnline

    var grid by remember { mutableStateOf<SlamOccupancyGrid?>(null) }
    var slamJson by remember { mutableStateOf<JSONObject?>(null) }
    var visionJson by remember { mutableStateOf<JSONObject?>(null) }
    var depthJson by remember { mutableStateOf<JSONObject?>(null) }
    var autonomyJson by remember { mutableStateOf<JSONObject?>(null) }
    var autonomyOn by remember { mutableStateOf(false) }

    LaunchedEffect(online) {
        if (!online) return@LaunchedEffect
        while (isActive) {
            try {
                grid = vm.fetchSlamOccupancyGrid()
                slamJson = vm.fetchSlamStatus()
                visionJson = vm.fetchVisionStatus()
                depthJson = vm.fetchDepthStatus()
                autonomyJson = vm.fetchAutonomyStatus()
                autonomyOn = autonomyJson?.optBoolean("enabled") == true
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
            }
            delay(650)
        }
    }

    val lidarPill = remember(slamJson, online) { lidarPillFromSlam(slamJson, online) }
    val camPill = remember(visionJson, online) { camPillFromVision(visionJson, online) }
    val depthPill = remember(depthJson, online) { depthPillFromDepth(depthJson, online) }
    val autoKind = if (autonomyOn) SirenaPillKind.Warn else SirenaPillKind.Neutral

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
                    PerceptionSensorColumn(
                        title = "RGB camera",
                        pillText = camPill.card,
                        pillKind = camPill.kind,
                        expandViewport = expand,
                        viewportMin = vMin,
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    ) {
                        if (online) {
                            SirenaMjpegImage(
                                streamUrl = "$root/v1/vision/stream",
                                bearer = bearer,
                                modifier = Modifier.fillMaxSize(),
                                maxLongEdge = SirenaMjpegPreviewMaxLongEdge,
                            )
                        } else {
                            Text(
                                "USB camera not connected",
                                modifier = Modifier.align(Alignment.Center),
                                color = SirenaColors.muted,
                                fontSize = SirenaType.muted,
                            )
                        }
                    }
                    PerceptionSensorColumn(
                        title = "Depth camera",
                        pillText = depthPill.card,
                        pillKind = depthPill.kind,
                        expandViewport = expand,
                        viewportMin = vMin,
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    ) {
                        if (online && depthJson?.optBoolean("bridge_enabled", true) != false) {
                            SirenaMjpegImage(
                                streamUrl = "$root/v1/depth/stream",
                                bearer = bearer,
                                modifier = Modifier.fillMaxSize(),
                                maxLongEdge = SirenaMjpegPreviewMaxLongEdge,
                            )
                        } else {
                            Text(
                                "Depth camera not connected",
                                modifier = Modifier.align(Alignment.Center),
                                color = SirenaColors.muted,
                                fontSize = SirenaType.muted,
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
                    PerceptionSensorColumn(
                        title = "RGB camera",
                        pillText = camPill.card,
                        pillKind = camPill.kind,
                        expandViewport = false,
                        viewportMin = vMin,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        if (online) {
                            SirenaMjpegImage(
                                streamUrl = "$root/v1/vision/stream",
                                bearer = bearer,
                                modifier = Modifier.fillMaxSize(),
                                maxLongEdge = SirenaMjpegPreviewMaxLongEdge,
                            )
                        } else {
                            Text(
                                "USB camera not connected",
                                modifier = Modifier.align(Alignment.Center),
                                color = SirenaColors.muted,
                                fontSize = SirenaType.muted,
                            )
                        }
                    }
                    PerceptionSensorColumn(
                        title = "Depth camera",
                        pillText = depthPill.card,
                        pillKind = depthPill.kind,
                        expandViewport = false,
                        viewportMin = vMin,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        if (online && depthJson?.optBoolean("bridge_enabled", true) != false) {
                            SirenaMjpegImage(
                                streamUrl = "$root/v1/depth/stream",
                                bearer = bearer,
                                modifier = Modifier.fillMaxSize(),
                                maxLongEdge = SirenaMjpegPreviewMaxLongEdge,
                            )
                        } else {
                            Text(
                                "Depth camera not connected",
                                modifier = Modifier.align(Alignment.Center),
                                color = SirenaColors.muted,
                                fontSize = SirenaType.muted,
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
    if (!online || s == null) return PillTriple("Lidar -", SirenaPillKind.Neutral, "waiting")
    val ok = s.optBoolean("lidar_connected", false)
    val kind = if (ok) SirenaPillKind.Ok else SirenaPillKind.Neutral
    return PillTriple(
        "Lidar " + if (ok) "ok" else "—",
        kind,
        if (ok) "live" else "waiting",
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

private fun depthPillFromDepth(d: JSONObject?, online: Boolean): PillTriple {
    if (!online || d == null || d.optBoolean("bridge_enabled", true) == false) {
        return PillTriple("Depth -", SirenaPillKind.Neutral, "waiting")
    }
    val ok = d.optBoolean("ok", true) && d.optString("message", "").contains("unavailable").not()
    val msg = d.optString("message").trim()
    val connected = ok && !msg.contains("unavailable", ignoreCase = true)
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
