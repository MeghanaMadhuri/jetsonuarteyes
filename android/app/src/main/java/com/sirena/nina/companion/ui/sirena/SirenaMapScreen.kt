package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.data.SlamOccupancyGrid
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.Locale
import org.json.JSONArray
import org.json.JSONObject

private fun letterboxedRect(boxW: Float, boxH: Float, imgW: Int, imgH: Int): Rect {
    val iw = imgW.toFloat()
    val ih = imgH.toFloat()
    val sar = iw / ih
    val bar = boxW / boxH
    return if (bar > sar) {
        val h = boxH
        val w = h * sar
        val ox = (boxW - w) / 2f
        Rect(ox, 0f, ox + w, h)
    } else {
        val w = boxW
        val h = w / sar
        val oy = (boxH - h) / 2f
        Rect(0f, oy, w, oy + h)
    }
}

private fun tapToWorldMm(
    tapX: Float,
    tapY: Float,
    boxW: Float,
    boxH: Float,
    imgW: Int,
    imgH: Int,
    scaleMmPerPx: Double,
): Pair<Double, Double>? {
    val r = letterboxedRect(boxW, boxH, imgW, imgH)
    if (tapX < r.left || tapX >= r.right || tapY < r.top || tapY >= r.bottom) return null
    val sx = r.width / imgW.toFloat()
    val sy = r.height / imgH.toFloat()
    val gpx = (tapX - r.left) / sx
    val gpy = (tapY - r.top) / sy
    val cx = imgW / 2.0
    val cy = imgH / 2.0
    val xMm = (gpx - cx) * scaleMmPerPx
    val yMm = (cy - gpy) * scaleMmPerPx
    return xMm to yMm
}

private fun healthPill(
    label: String,
    o: JSONObject?,
): Pair<String, SirenaPillKind> {
    if (o == null) return "$label -" to SirenaPillKind.Neutral
    val ok = o.optBoolean("connected", false)
    return (
        if (ok) "$label ok" else "$label —"
        ) to if (ok) SirenaPillKind.Ok else SirenaPillKind.Neutral
}

private fun ultraSummary(arr: JSONArray?): String {
    if (arr == null || arr.length() == 0) return "Ultra -"
    var any = false
    for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        if (o.optBoolean("connected", false)) any = true
    }
    return if (any) "Ultra ok" else "Ultra —"
}

/**
 * Layout aligned with desktop [sirena_ui.screens.map_screen.MapScreen]:
 * breadcrumb + pills, **~60/40** map vs side rail, legend under grid, side sections.
 */
@Composable
fun SirenaMapScreen(
    vm: CompanionViewModel,
    daemonUrl: String?,
    caps: JSONObject? = null,
    shellCompact: Boolean = false,
) {
    val scope = rememberCoroutineScope()
    val jetsonLink by vm.jetsonLink.collectAsStateWithLifecycle()
    val online = !daemonUrl.isNullOrBlank() && jetsonLink.isOnline

    var grid by remember { mutableStateOf<SlamOccupancyGrid?>(null) }
    var scaleMm by remember { mutableStateOf(1.0) }
    var slamRunning by remember { mutableStateOf(false) }
    var slamJson by remember { mutableStateOf<JSONObject?>(null) }
    var autonomyJson by remember { mutableStateOf<JSONObject?>(null) }
    var autonomyOn by remember { mutableStateOf(false) }
    var saveName by remember { mutableStateOf("nina_map.pgm") }
    var lastGoal by remember { mutableStateOf("—") }
    var err by remember { mutableStateOf<String?>(null) }
    var tapGotoOn by remember { mutableStateOf(false) }
    var lastGridWallMs by remember { mutableStateOf(0L) }
    var gridAgeTick by remember { mutableIntStateOf(0) }

    LaunchedEffect(grid?.width, grid?.height, online) {
        val g = grid
        if (g != null && g.width > 0 && g.height > 0 && online) {
            lastGridWallMs = System.currentTimeMillis()
        }
    }

    LaunchedEffect(online, lastGridWallMs) {
        if (!online || lastGridWallMs <= 0L) return@LaunchedEffect
        while (isActive) {
            delay(500)
            gridAgeTick++
        }
    }

    LaunchedEffect(online, caps?.optBoolean("slam_bridge_enabled")) {
        if (!online) return@LaunchedEffect
        while (isActive) {
            try {
                if (caps != null && !caps.optBoolean("slam_bridge_enabled")) {
                    delay(10_000L)
                    continue
                }
                val st = vm.fetchSlamStatus()
                slamJson = st
                val snap = st?.optJSONObject("snapshot")
                if (snap != null) {
                    scaleMm = snap.optDouble("scale_mm_per_px", 1.0).takeIf { it > 0 } ?: 1.0
                }
                slamRunning = st?.optBoolean("running", false) == true
                grid = vm.fetchSlamOccupancyGrid()
                val au = vm.fetchAutonomyStatus()
                autonomyJson = au
                autonomyOn = au?.optBoolean("enabled") == true
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
            }
            delay(1800)
        }
    }

    val sim = !autonomyJson?.optString("sim").isNullOrBlank()
    val sensorTop =
        remember(autonomyJson, slamJson, sim, autonomyOn, online) {
            when {
                !online -> "Sensors idle"
                autonomyOn -> "Autonomy engaged"
                sim -> "Simulation"
                slamJson?.optBoolean("lidar_connected") == true -> "Sensors live"
                else -> "Sensors idle"
            }
        }
    val sensorTopKind =
        remember(autonomyOn, sim, slamJson, online) {
            when {
                !online -> SirenaPillKind.Neutral
                autonomyOn -> SirenaPillKind.Ok
                sim -> SirenaPillKind.Warn
                slamJson?.optBoolean("lidar_connected") == true -> SirenaPillKind.Ok
                else -> SirenaPillKind.Neutral
            }
        }
    val slamTop =
        remember(slamJson) {
            val j = slamJson ?: return@remember "SLAM idle"
            val running = j.optBoolean("running", false)
            when {
                !running -> "SLAM idle"
                j.optBoolean("slam_fallback") -> {
                    val msg = j.optString("slam_message").trim().ifBlank { "no BreezySLAM" }
                    "SLAM fallback — ${msg.take(40)}"
                }
                else -> {
                    val n = j.optInt("scans_processed", 0)
                    "SLAM live — $n scans"
                }
            }
        }
    val slamTopKind =
        remember(slamJson) {
            val j = slamJson
            if (j == null) return@remember SirenaPillKind.Neutral
            val running = j.optBoolean("running", false)
            when {
                !running -> SirenaPillKind.Neutral
                j.optBoolean("slam_fallback") -> SirenaPillKind.Warn
                else -> SirenaPillKind.Ok
            }
        }
    val mapPill =
        remember(grid, slamJson) {
            when {
                grid == null -> "waiting for first scan"
                slamJson?.optBoolean("lidar_connected") == false -> "simulation / no lidar"
                else -> "live grid"
            }
        }
    val mapPillKind =
        if (grid != null && slamJson?.optBoolean("lidar_connected") != false) {
            SirenaPillKind.Ok
        } else {
            SirenaPillKind.Neutral
        }

    val gridAgeSec =
        run {
            val g = grid
            if (lastGridWallMs > 0L && g != null && g.width > 0 && g.height > 0) {
                (System.currentTimeMillis() - lastGridWallMs) / 1000.0
            } else {
                0.0
            }
        }
    val mapHeaderPillText =
        remember(grid, slamJson, gridAgeTick, mapPill) {
            val g = grid
            when {
                g != null && g.width > 0 && g.height > 0 ->
                    "updated " + String.format(Locale.US, "%.1f", gridAgeSec) + "s ago"
                slamJson?.optBoolean("running") == true &&
                    slamJson?.optBoolean("lidar_connected") == false -> {
                    val sj = slamJson!!
                    val hint = sj.optString("lidar_message").trim()
                    if (hint.isNotEmpty()) "Lidar sim — ${hint.take(36)}" else "Lidar simulation"
                }
                else -> mapPill
            }
        }
    val mapHeaderPillKind =
        remember(grid, mapPillKind) {
            val g = grid
            if (g != null && g.width > 0 && g.height > 0) SirenaPillKind.Ok else mapPillKind
        }

    val health = autonomyJson?.optJSONObject("health")
    val lidarH = healthPill("Lidar", health?.optJSONObject("lidar"))
    val depthH = healthPill("Depth", health?.optJSONObject("depth"))
    val irH = healthPill("IR", health?.optJSONObject("ir"))
    val ultraTxt = ultraSummary(health?.optJSONArray("ultrasonic"))
    val ultraKind =
        if (ultraTxt.contains("ok")) SirenaPillKind.Ok else SirenaPillKind.Neutral

    val pose = slamJson?.optJSONObject("snapshot")?.optJSONObject("pose")
    val poseText =
        if (pose != null) {
            val x = pose.optDouble("x_mm", Double.NaN)
            val y = pose.optDouble("y_mm", Double.NaN)
            val th = pose.optDouble("theta_deg", Double.NaN)
            "x: ${fmtMm(x)}\ny: ${fmtMm(y)}\nθ: ${fmtDeg(th)}"
        } else {
            "x: —\ny: —\nθ: —"
        }

    val pilot = autonomyJson?.optJSONObject("pilot")
    val pilotText =
        pilot?.optString("last_action")?.trim()?.takeIf { it.isNotEmpty() }
            ?: pilot?.optString("last_reason")?.trim()?.takeIf { it.isNotEmpty() }
            ?: "idle"

    val goto = autonomyJson?.optJSONObject("goto")
    val gotoState = goto?.optString("state")?.trim().orEmpty().ifBlank { "idle" }
    val gotoPillText =
        if (gotoState == "idle") {
            "Goto idle"
        } else {
            "Goto: $gotoState"
        }
    val gotoActive =
        gotoState in setOf("planning", "driving", "turning", "replanning", "avoiding")

    @Composable
    fun OccupancyMapColumn(mapCardModifier: Modifier, gridBoxModifier: Modifier) {
        SirenaCard(
            modifier = mapCardModifier,
            contentPadding = androidx.compose.foundation.layout.PaddingValues(10.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                SirenaCardTitle("Occupancy map")
                Spacer(Modifier.weight(1f))
                SirenaStatusPill(mapHeaderPillText, mapHeaderPillKind)
            }

            val g = grid
            Box(
                modifier = gridBoxModifier,
            ) {
                if (g != null && g.width > 0 && g.height > 0) {
                    val bmp = remember(g) { g.toBitmap().asImageBitmap() }
                    Image(
                        bitmap = bmp,
                        contentDescription = "SLAM map — tap to set goto goal when enabled",
                        contentScale = ContentScale.Fit,
                        modifier =
                            Modifier
                                .fillMaxSize()
                                .then(
                                    if (tapGotoOn) {
                                        Modifier.pointerInput(scaleMm, g.width, g.height, tapGotoOn) {
                                            detectTapGestures { pos ->
                                                val bw = size.width.toFloat()
                                                val bh = size.height.toFloat()
                                                val mm =
                                                    tapToWorldMm(
                                                        pos.x,
                                                        pos.y,
                                                        bw,
                                                        bh,
                                                        g.width,
                                                        g.height,
                                                        scaleMm,
                                                    )
                                                if (mm != null) {
                                                    scope.launch {
                                                        err = null
                                                        val r = vm.postAutonomyGoal(mm.first, mm.second)
                                                        if (r != null && r.optBoolean("ok", true)) {
                                                            lastGoal =
                                                                "${mm.first.toInt()} mm, ${mm.second.toInt()} mm"
                                                        } else {
                                                            val msg =
                                                                r?.optString("detail").orEmpty().ifBlank {
                                                                    r?.optString("message").orEmpty().ifBlank {
                                                                        r?.toString()?.take(120)
                                                                    }
                                                                }
                                                            lastGoal = msg ?: "goal failed"
                                                            err = msg
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    } else {
                                        Modifier
                                    },
                                ),
                    )
                } else {
                    val simMsg =
                        if (slamJson?.optBoolean("running") == true &&
                            slamJson?.optBoolean("lidar_connected") == false
                        ) {
                            slamJson!!.optString("lidar_message").trim().ifBlank {
                                "Lidar simulation — connect hardware to build a live map."
                            }
                        } else {
                            null
                        }
                    Text(
                        simMsg ?: "Waiting for SLAM occupancy grid…",
                        modifier = Modifier.align(Alignment.Center).padding(12.dp),
                        color = SirenaColors.muted,
                        fontSize = SirenaType.muted,
                    )
                }
            }

            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                LegendDot(Color(0xFFC8102E), "Nina")
                LegendDot(Color(0xFF1C1C1E), "Wall")
                LegendDot(Color(0xFFD1D1D6), "Free space")
                LegendDot(Color(0xFF8E8E93), "Unknown")
                Spacer(Modifier.weight(1f))
            }
        }
    }

    @Composable
    fun MapSideRailCard(
        sideModifier: Modifier,
        scrollRail: Boolean,
        caps: JSONObject?,
    ) {
        val railScroll = rememberScrollState()
        SirenaCard(
            modifier =
                if (scrollRail) {
                    sideModifier.verticalScroll(railScroll)
                } else {
                    sideModifier
                },
            contentPadding = androidx.compose.foundation.layout.PaddingValues(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
                err?.let { msg ->
                    SirenaInlineStatusBanner(
                        isError = true,
                        title = "Map",
                        message = msg,
                    )
                }

                SirenaSectionLabel("Autonomous nav")
                SirenaPrimaryButton(
                    text = if (autonomyOn) "Autonomous mode: ON" else "Autonomous mode: OFF",
                    onClick = {
                        scope.launch {
                            err = null
                            val want = !autonomyOn
                            val r = vm.postAutonomyEnabled(want)
                            if (r != null && r.optBoolean("ok", true)) {
                                autonomyOn = r.optBoolean("enabled", want)
                            } else {
                                err = r?.optString("error").orEmpty().ifBlank { "autonomy failed" }
                            }
                        }
                    },
                    enabled = online && autonomyJson?.optBoolean("bridge_enabled", true) != false,
                    modifier = Modifier.height(34.dp),
                )
                SirenaMutedText(
                    "When ON: lidar + SLAM + obstacle avoidance start, and Nina drives herself " +
                        "while reactively avoiding obstacles.",
                    maxLines = 5,
                )

                SirenaSectionLabel("Go to point")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SirenaSecondaryButton(
                        text = if (tapGotoOn) "Tap on map: ON" else "Tap on map: OFF",
                        onClick = { tapGotoOn = !tapGotoOn },
                        enabled = online,
                    )
                    SirenaSecondaryButton(
                        text = "Cancel",
                        onClick = {
                            scope.launch {
                                err = null
                                try {
                                    vm.deleteAutonomyGoal()
                                    lastGoal = "cancelled"
                                } catch (e: Exception) {
                                    err = e.message
                                }
                            }
                        },
                        enabled = online && gotoActive,
                    )
                }
                SirenaStatusPill(gotoPillText, SirenaPillKind.Neutral)
                SirenaMutedText(
                    "Tap on the map to send Nina to a point. She'll plan a path on the SLAM grid, " +
                        "drive there with reactive obstacle avoidance, and stop on arrival.",
                    maxLines = 5,
                )
                SirenaMutedText("Last tap goal: $lastGoal", maxLines = 2)

                SirenaSectionLabel("Mapping")
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    SirenaSecondaryButton(
                        text = if (slamRunning) "Stop mapping" else "Start mapping",
                        onClick = {
                            scope.launch {
                                err = null
                                val wantRun = !slamRunning
                                val r = vm.postSlamRunning(wantRun)
                                if (r != null && r.optBoolean("ok", true)) {
                                    slamRunning = r.optBoolean("running", wantRun)
                                } else {
                                    err =
                                        r?.optString("detail") ?: r?.toString()?.take(120)
                                            ?: if (wantRun) "start failed" else "stop failed"
                                }
                            }
                        },
                        enabled = online,
                    )
                    SirenaSecondaryButton(
                        text = "Save map",
                        onClick = {
                            scope.launch {
                                err = null
                                val r = vm.saveSlamMapPgm(saveName.trim().ifBlank { "nina_map.pgm" })
                                if (r == null || !r.optBoolean("ok", false)) {
                                    err = r?.optString("detail") ?: r?.toString() ?: "save failed"
                                }
                            }
                        },
                        enabled = online,
                    )
                    SirenaSecondaryButton(
                        text = "Clear",
                        onClick = {
                            scope.launch {
                                err = null
                                val r = vm.postSlamClear()
                                if (r == null || !r.optBoolean("ok", false)) {
                                    err = r?.optString("detail") ?: r?.toString()?.take(120) ?: "clear failed"
                                }
                            }
                        },
                        enabled = online,
                    )
                }
                OutlinedTextField(
                    value = saveName,
                    onValueChange = { saveName = it },
                    label = { Text("PGM filename") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                SirenaMutedText(
                    "Clears the SLAM map on the robot (same as Qt: autonomy turns off if it was on, then SLAM restarts).",
                    maxLines = 3,
                )

                SirenaSectionLabel("Sensor health")
                Row(
                    Modifier.horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    SirenaStatusPill(lidarH.first, lidarH.second)
                    SirenaStatusPill(depthH.first, depthH.second)
                    SirenaStatusPill(irH.first, irH.second)
                    SirenaStatusPill(ultraTxt, ultraKind)
                }

                SirenaSectionLabel("Pose")
                Text(
                    poseText,
                    modifier =
                        Modifier
                            .fillMaxWidth()
                            .background(Color(0xFFF5F5F7), shape = RoundedCornerShape(8.dp))
                            .padding(10.dp),
                    fontFamily = FontFamily.Monospace,
                    fontSize = 13.sp,
                    color = Color(0xFF1C1C1E),
                )

                SirenaSectionLabel("Pilot")
                Text(
                    pilotText,
                    modifier =
                        Modifier
                            .fillMaxWidth()
                            .background(Color(0xFFF5F5F7), shape = RoundedCornerShape(8.dp))
                            .padding(10.dp),
                    fontFamily = FontFamily.Monospace,
                    fontSize = 13.sp,
                    color = Color(0xFF1C1C1E),
                )

                if (online) {
                    SirenaJetsonFeatureGateCallout(
                        linkOnline = true,
                        caps = caps,
                        flagKey = "slam_bridge_enabled",
                        title = "SLAM / map HTTP bridge is off",
                        envLine =
                            "On the Jetson: export NINA_LINK_ENABLE_SLAM_BRIDGE=1, then restart nina-link. " +
                                "Autonomy goto also needs NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1.",
                    )
                }
        }
    }

    SirenaAdaptiveContainer(Modifier.padding(10.dp)) { ctx ->
        Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            SirenaAdaptiveHeaderPills(
                ctx = ctx,
                breadcrumb = { SirenaBreadcrumbLine(listOf("Nina", "Map")) },
                pills = {
                    SirenaStatusPill(sensorTop, sensorTopKind)
                    SirenaStatusPill(slamTop, slamTopKind)
                },
            )
            if (ctx.mapSplit) {
                Row(
                    Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    OccupancyMapColumn(
                        mapCardModifier = Modifier.weight(0.6f).fillMaxHeight(),
                        gridBoxModifier = Modifier.fillMaxWidth().weight(1f),
                    )
                    MapSideRailCard(
                        sideModifier = Modifier.weight(0.4f).fillMaxHeight(),
                        scrollRail = true,
                        caps = caps,
                    )
                }
            } else {
                Column(
                    Modifier
                        .weight(1f)
                        .fillMaxWidth()
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OccupancyMapColumn(
                        mapCardModifier = Modifier.fillMaxWidth(),
                        gridBoxModifier =
                            Modifier
                                .fillMaxWidth()
                                .heightIn(min = ctx.mapStackedGridMinHeight, max = 520.dp),
                    )
                    MapSideRailCard(
                        sideModifier = Modifier.fillMaxWidth(),
                        scrollRail = false,
                        caps = caps,
                    )
                }
            }
        }
    }
}

@Composable
private fun LegendDot(color: Color, label: String) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(
            Modifier
                .size(10.dp)
                .background(color, CircleShape),
        )
        Text(
            label,
            fontSize = 12.sp,
            color = SirenaColors.muted,
            fontWeight = FontWeight.Normal,
        )
    }
}

private fun fmtMm(v: Double): String =
    if (v.isFinite()) String.format("%.0f mm", v) else "—"

private fun fmtDeg(v: Double): String =
    if (v.isFinite()) String.format("%.1f°", v) else "—"
