package com.sirena.nina.companion.ui.sirena

import android.view.KeyEvent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.TwoWheeler
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import com.sirena.nina.companion.ui.theme.SirenaSwitch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.Alignment
import androidx.compose.foundation.focusable
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.util.NinaLog
import kotlin.math.sqrt
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Non-HTTP failures use [LinkApiException]; HTTP 200 with `ok: false` surfaces here. */
private fun JSONObject.driveCommandErrorOrNull(): String? {
    if (length() == 0) return null
    if (optBoolean("ok", true)) return null
    val e = optString("error").trim()
    if (e.isNotEmpty() && !e.equals("null", ignoreCase = true)) return e
    val m = optString("message").trim()
    if (m.isNotEmpty() && !m.equals("null", ignoreCase = true)) return m
    return "Drive request failed"
}

private fun batteryLabelFromHealth(h: JSONObject?): String {
    val rows = h?.optJSONArray("rows") ?: return "n/a"
    for (i in 0 until rows.length()) {
        val o = rows.optJSONObject(i) ?: continue
        if (o.optString("key") == "battery") {
            return o.optString("detail").trim().ifBlank { "—" }
        }
    }
    return "n/a"
}

/** Android gateway clamps each momentary drive call to 5000 ms (see `drive_http.py`). */
private const val STRAIGHT_SEGMENT_MS = 5000

/** Extra ms between D-pad HTTP pulses so each momentary can finish before the next (avoids overlapping forwards). */
private const val DRIVE_REPEAT_GAP_MS = 140L

/** Brief gap between chained straight pulses so two 5 s requests approximate desktop “10 s”. */
private const val STRAIGHT_PULSE_GAP_MS = 80L

private const val TURN_DURATION_MS = 2300
private const val TURN_SPEED_PERCENT = 20

private fun effectiveHorizonDriveDir(requested: String, reverseOn: Boolean): String =
    when (requested) {
        "forward" -> if (reverseOn) "back" else "forward"
        "back" -> if (reverseOn) "forward" else "back"
        else -> requested
    }

/**
 * Mirrors [sirena_ui.screens.drive_screen.DriveScreen] —
 * camera preview (MJPEG when vision bridge on), manual BLDC pulses, autonomy UI toggle.
 */
@Composable
fun SirenaDriveScreen(
    vm: CompanionViewModel,
    caps: JSONObject?,
    daemonUrl: String?,
    shellCompact: Boolean = false,
) {
    val scope = rememberCoroutineScope()
    var actionErr by remember { mutableStateOf<String?>(null) }
    var motionBusy by remember { mutableStateOf(false) }
    // When capabilities are missing, assume drive is off until a successful status refresh loads them.
    val bridgeOn = caps?.optBoolean("robot_bridge_enabled") ?: false
    val visionOn = caps?.optBoolean("vision_bridge_enabled") ?: false
    val autonomyApi = caps?.optBoolean("autonomy_bridge_enabled") ?: false
    val defaultMs = caps?.optInt("default_duration_ms")?.takeIf { it > 0 } ?: 280
    val speedMin = caps?.optInt("drive_speed_min_percent")?.takeIf { it in 1..99 } ?: 15
    val speedMax = caps?.optInt("drive_speed_max_percent")?.takeIf { it > speedMin } ?: 25
    var speedPct by remember(speedMin, speedMax) {
        mutableFloatStateOf(speedMin.toFloat())
    }
    var autonomyOn by remember { mutableStateOf(false) }
    var cameraPreviewOn by remember { mutableStateOf(false) }
    var bldcConnected by remember { mutableStateOf<Boolean?>(null) }
    var bldcDetail by remember { mutableStateOf<String?>(null) }
    var brakeOn by remember { mutableStateOf(true) }
    var reverseOn by remember { mutableStateOf(false) }
    var invertLeft by remember { mutableStateOf(false) }
    var invertRight by remember { mutableStateOf(false) }
    var hudHeading by remember { mutableStateOf("—") }
    var hudDistance by remember { mutableStateOf("—") }
    var batteryLabel by remember { mutableStateOf("n/a") }
    val slamOn = caps?.optBoolean("slam_bridge_enabled") == true

    val jetsonLink by vm.jetsonLink.collectAsStateWithLifecycle()
    val bearer by vm.bearerToken.collectAsStateWithLifecycle(initialValue = null)
    val jetsonOnline = jetsonLink.isOnline
    val slimBanner = rememberSlimStatusBanner()
    val slimCopy = slimBanner || shellCompact
    val focusRequester = remember { FocusRequester() }

    val streamRoot = daemonUrl?.trimEnd('/') ?: ""

    LaunchedEffect(cameraPreviewOn, visionOn, streamRoot) {
        if (!cameraPreviewOn || !visionOn || streamRoot.isBlank()) {
            return@LaunchedEffect
        }
        val err = vm.visionOpen()
        if (err != null) actionErr = err
    }

    LaunchedEffect(cameraPreviewOn, visionOn) {
        if (cameraPreviewOn || !visionOn) return@LaunchedEffect
        vm.visionStop()
    }

    LaunchedEffect(bridgeOn, jetsonOnline) {
        if (!bridgeOn || !jetsonOnline) {
            bldcConnected = null
            bldcDetail = null
            return@LaunchedEffect
        }
        delay(400)
        focusRequester.requestFocus()
        while (isActive) {
            try {
                val j = vm.fetchRobotDriveStatus()
                if (j != null) {
                    bldcConnected = j.optBoolean("connected")
                    val msg = j.optString("message").trim()
                    val lde = j.optString("last_drive_error").trim()
                    bldcDetail =
                        when {
                            msg.isNotEmpty() && lde.isNotEmpty() -> "$msg · $lde"
                            msg.isNotEmpty() -> msg
                            lde.isNotEmpty() -> lde
                            else -> null
                        }
                    if (j.has("invert_left")) invertLeft = j.optBoolean("invert_left")
                    if (j.has("invert_right")) invertRight = j.optBoolean("invert_right")
                }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
            }
            delay(2500)
        }
    }

    LaunchedEffect(jetsonOnline) {
        if (!jetsonOnline) {
            batteryLabel = "n/a"
            return@LaunchedEffect
        }
        while (isActive) {
            try {
                batteryLabel = batteryLabelFromHealth(vm.fetchRobotHealth())
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
            }
            delay(3000)
        }
    }

    LaunchedEffect(slamOn, jetsonOnline) {
        if (!slamOn || !jetsonOnline) {
            hudHeading = "—"
            hudDistance = "—"
            return@LaunchedEffect
        }
        while (isActive) {
            val st = try {
                vm.fetchSlamStatus()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                null
            }
            val snap = st?.optJSONObject("snapshot")
            val pose = snap?.optJSONObject("pose")
            if (pose != null) {
                val th = pose.optDouble("theta_deg", Double.NaN)
                hudHeading = if (th.isFinite()) String.format("%.0f°", th) else "—"
                val x = pose.optDouble("x_mm", 0.0)
                val y = pose.optDouble("y_mm", 0.0)
                val m = sqrt(x * x + y * y) / 1000.0
                hudDistance = String.format("%.1f m", m)
            } else {
                hudHeading = "—"
                hudDistance = "—"
            }
            delay(2000)
        }
    }

    LaunchedEffect(autonomyApi, jetsonOnline) {
        if (!autonomyApi || !jetsonOnline) return@LaunchedEffect
        while (isActive) {
            try {
                val st = vm.fetchAutonomyStatus()
                if (st?.optBoolean("bridge_enabled") == true) {
                    autonomyOn = st.optBoolean("enabled")
                }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
            }
            delay(2000)
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .focusRequester(focusRequester)
            .focusable()
            .onPreviewKeyEvent { ev ->
                if (ev.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                if (!bridgeOn || !jetsonOnline || motionBusy) return@onPreviewKeyEvent false
                val code = ev.nativeKeyEvent.keyCode
                when (code) {
                    KeyEvent.KEYCODE_ESCAPE -> {
                        brakeOn = true
                        scope.launch {
                            try {
                                val j = vm.robotEmergencyStop()
                                actionErr = j.driveCommandErrorOrNull()
                            } catch (e: Exception) {
                                actionErr = e.message
                            }
                        }
                        true
                    }
                    KeyEvent.KEYCODE_SPACE -> {
                        scope.launch {
                            try {
                                val j = vm.robotDriveMomentary("stop", defaultMs, speedPct.toInt())
                                actionErr = j.driveCommandErrorOrNull()
                            } catch (e: Exception) {
                                actionErr = e.message
                            }
                        }
                        true
                    }
                    KeyEvent.KEYCODE_W,
                    KeyEvent.KEYCODE_S,
                    KeyEvent.KEYCODE_A,
                    KeyEvent.KEYCODE_D,
                    KeyEvent.KEYCODE_DPAD_UP,
                    KeyEvent.KEYCODE_DPAD_DOWN,
                    KeyEvent.KEYCODE_DPAD_LEFT,
                    KeyEvent.KEYCODE_DPAD_RIGHT -> {
                        if (autonomyOn) return@onPreviewKeyEvent false
                        val raw =
                            when (code) {
                                KeyEvent.KEYCODE_W, KeyEvent.KEYCODE_DPAD_UP -> "forward"
                                KeyEvent.KEYCODE_S, KeyEvent.KEYCODE_DPAD_DOWN -> "back"
                                KeyEvent.KEYCODE_A, KeyEvent.KEYCODE_DPAD_LEFT -> "left"
                                KeyEvent.KEYCODE_D, KeyEvent.KEYCODE_DPAD_RIGHT -> "right"
                                else -> return@onPreviewKeyEvent false
                            }
                        if (brakeOn && raw in setOf("forward", "back", "left", "right")) {
                            return@onPreviewKeyEvent false
                        }
                        val eff = effectiveHorizonDriveDir(raw, reverseOn)
                        scope.launch {
                            try {
                                val j = vm.robotDriveMomentary(eff, defaultMs, speedPct.toInt())
                                actionErr = j.driveCommandErrorOrNull()
                            } catch (e: Exception) {
                                actionErr = e.message
                            }
                        }
                        true
                    }
                    else -> false
                }
            }
            .padding(
                horizontal = if (shellCompact) 6.dp else 10.dp,
                vertical = if (shellCompact) 6.dp else 10.dp,
            ),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SirenaBreadcrumbLine(listOf("Nina", "Drive"), modifier = Modifier.weight(1f))
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SirenaStatusPill(
                    "Autonomous: " + if (autonomyOn) "ON" else "OFF",
                    if (autonomyOn) SirenaPillKind.Warn else SirenaPillKind.Neutral,
                )
                val bldcKind =
                    when {
                        !bridgeOn || !jetsonOnline -> SirenaPillKind.Neutral
                        bldcConnected == true -> SirenaPillKind.Ok
                        bldcConnected == false -> SirenaPillKind.Error
                        else -> SirenaPillKind.Neutral
                    }
                SirenaStatusPill(
                    when {
                        !bridgeOn -> "BLDC · bridge off"
                        !jetsonOnline -> "BLDC · offline"
                        bldcConnected == true -> "BLDC · connected"
                        bldcConnected == false -> {
                            val d = bldcDetail
                            if (!d.isNullOrBlank()) "BLDC · ${d.take(48)}" else "BLDC · not connected"
                        }
                        else -> "BLDC · checking…"
                    },
                    bldcKind,
                    modifier = Modifier.widthIn(max = 280.dp),
                )
            }
        }

        Row(
            Modifier
                .weight(1f)
                .fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            val previewStreamOn = cameraPreviewOn && visionOn && streamRoot.isNotBlank()
            CameraCard(
                modifier =
                    Modifier
                        .weight(0.55f)
                        .fillMaxHeight(),
                cameraPreviewOn = previewStreamOn,
                streamRoot = streamRoot,
                visionOn = visionOn,
                cameraPreviewEnabled = cameraPreviewOn,
                onCameraPreviewChange = { cameraPreviewOn = it },
                speedPct = speedPct,
                hudHeading = hudHeading,
                hudDistance = hudDistance,
                batteryLabel = batteryLabel,
                bearer = bearer,
            )
            Column(
                modifier =
                    Modifier
                        .weight(0.45f)
                        .fillMaxHeight(),
            ) {
                val rightScroll = rememberScrollState()
                Column(
                    Modifier
                        .fillMaxSize()
                        .verticalScroll(rightScroll),
                    verticalArrangement = Arrangement.spacedBy(if (shellCompact) 4.dp else 8.dp),
                ) {
                    if (caps == null && jetsonOnline) {
                        SirenaInlineStatusBanner(
                            isError = false,
                            title = "Drive",
                            message =
                                if (slimCopy) {
                                    "Capabilities not loaded — open Network, tap Refresh status."
                                } else {
                                    "Robot capabilities not loaded yet. Open Network and tap Refresh status so drive / vision flags sync from the Jetson."
                                },
                        )
                    }
                    if (!bridgeOn) {
                        SirenaInlineStatusBanner(
                            isError = true,
                            title = "Drive bridge off",
                            message =
                                if (slimCopy) {
                                    "Set NINA_LINK_ENABLE_ROBOT_BRIDGE=1, restart nina-link. Close desktop Drive if open."
                                } else {
                                    "Drive bridge is off on the Jetson. Set NINA_LINK_ENABLE_ROBOT_BRIDGE=1 for the link daemon, restart the service, and avoid running the desktop Drive screen at the same time."
                                },
                        )
                    }
                    if (bridgeOn && jetsonOnline && bldcConnected == false) {
                        SirenaInlineStatusBanner(
                            isError = false,
                            title = "BLDC",
                            message =
                                if (slimCopy) {
                                    "Not reachable — close desktop Drive or fix Pi UART / NINA_NAV_REMOTE_PORT."
                                } else {
                                    "BLDC not reachable from nina-link (Pi UART bridge). Only one process may use that serial port — close the desktop Sirena Drive screen if it is open. Stock robots use remote mode on /dev/ttyTHS1 (defaults are in nina-link.service); for another device, set NINA_NAV_REMOTE_PORT in /etc/nina-link/navigation.env and restart nina-link."
                                },
                        )
                    }

                    actionErr?.let { err ->
                        SirenaInlineStatusBanner(
                            isError = true,
                            title = "Drive",
                            message = err,
                        )
                    }

                    ControlCard(
                        modifier = Modifier.fillMaxWidth(),
                        bridgeOn = bridgeOn,
                        motionBusy = motionBusy,
                        autonomyOn = autonomyOn,
                        autonomyApi = autonomyApi,
                        onAutonomyChange = { want ->
                            scope.launch {
                                val r = vm.postAutonomyEnabled(want)
                                if (r?.optBoolean("ok") == true) {
                                    autonomyOn = r.optBoolean("enabled", want)
                                    actionErr = null
                                } else {
                                    actionErr =
                                        r?.optString("error").orEmpty().ifBlank {
                                            r?.optString("message").orEmpty().ifBlank { "autonomy request failed" }
                                        }
                                }
                            }
                        },
                        invertLeft = invertLeft,
                        invertRight = invertRight,
                        onInvertLeft = { on ->
                            scope.launch {
                                val r = vm.postRobotDriveInvert(on, null)
                                if (r?.optBoolean("ok") == true) {
                                    invertLeft = r.optBoolean("invert_left", on)
                                    actionErr = null
                                } else {
                                    actionErr = r?.optString("error") ?: "invert failed"
                                }
                            }
                        },
                        onInvertRight = { on ->
                            scope.launch {
                                val r = vm.postRobotDriveInvert(null, on)
                                if (r?.optBoolean("ok") == true) {
                                    invertRight = r.optBoolean("invert_right", on)
                                    actionErr = null
                                } else {
                                    actionErr = r?.optString("error") ?: "invert failed"
                                }
                            }
                        },
                        brakeOn = brakeOn,
                        reverseOn = reverseOn,
                        onBrakeChange = { brakeOn = it },
                        onReverseChange = { reverseOn = it },
                        speedMin = speedMin,
                        speedMax = speedMax,
                        speedPct = speedPct,
                        onSpeedChange = { speedPct = it },
                        pulseIntervalMs = (defaultMs.toLong() + DRIVE_REPEAT_GAP_MS).coerceAtLeast(120L),
                        onDrivePulse = { dir ->
                            if (motionBusy) {
                                JSONObject().put("ok", false).put("error", "Wait for timed move to finish.")
                            } else if (brakeOn && dir in setOf("forward", "back", "left", "right")) {
                                JSONObject().put("ok", false).put("error", "Release brake to drive.")
                            } else {
                                val effective =
                                    when (dir) {
                                        "forward" -> if (reverseOn) "back" else "forward"
                                        "back" -> if (reverseOn) "forward" else "back"
                                        else -> dir
                                    }
                                vm.robotDriveMomentary(effective, defaultMs, speedPct.toInt())
                            }
                        },
                        onDriveResult = { j -> actionErr = j.driveCommandErrorOrNull() },
                        onStraightTest = {
                            scope.launch {
                                if (!bridgeOn || brakeOn || autonomyOn || motionBusy) return@launch
                                motionBusy = true
                                try {
                                    val eff = if (reverseOn) "back" else "forward"
                                    val sp = speedPct.toInt()
                                    var j = vm.robotDriveMomentary(eff, STRAIGHT_SEGMENT_MS, sp)
                                    actionErr = j.driveCommandErrorOrNull()
                                    if (actionErr != null) return@launch
                                    delay(STRAIGHT_PULSE_GAP_MS)
                                    j = vm.robotDriveMomentary(eff, STRAIGHT_SEGMENT_MS, sp)
                                    actionErr = j.driveCommandErrorOrNull()
                                } catch (e: CancellationException) {
                                    throw e
                                } catch (e: Exception) {
                                    actionErr = e.message
                                } finally {
                                    motionBusy = false
                                }
                            }
                        },
                        onTurnLeft = {
                            scope.launch {
                                if (!bridgeOn || brakeOn || autonomyOn || motionBusy) return@launch
                                motionBusy = true
                                try {
                                    val j =
                                        vm.robotDriveMomentary(
                                            "left",
                                            TURN_DURATION_MS,
                                            TURN_SPEED_PERCENT,
                                        )
                                    actionErr = j.driveCommandErrorOrNull()
                                } finally {
                                    motionBusy = false
                                }
                            }
                        },
                        onTurnRight = {
                            scope.launch {
                                if (!bridgeOn || brakeOn || autonomyOn || motionBusy) return@launch
                                motionBusy = true
                                try {
                                    val j =
                                        vm.robotDriveMomentary(
                                            "right",
                                            TURN_DURATION_MS,
                                            TURN_SPEED_PERCENT,
                                        )
                                    actionErr = j.driveCommandErrorOrNull()
                                } finally {
                                    motionBusy = false
                                }
                            }
                        },
                        onEstop = {
                            brakeOn = true
                            scope.launch {
                                try {
                                    val j = vm.robotEmergencyStop()
                                    actionErr = j.driveCommandErrorOrNull()
                                } catch (e: Exception) {
                                    actionErr = e.message
                                }
                            }
                        },
                    )

                    Surface(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(8.dp),
                        color = SirenaColors.calloutBg,
                    ) {
                        Text(
                            if (shellCompact) {
                                "D‑pad ≈${defaultMs} ms pulses · 10 s = two ${STRAIGHT_SEGMENT_MS / 1000}s · Keys: WASD / arrows, Space stop, Esc E‑STOP."
                            } else {
                                "D‑pad (hold) sends ≈${defaultMs} ms HTTP pulses. Straight 10s chains two ${STRAIGHT_SEGMENT_MS / 1000}s pulses (link cap). " +
                                    "With a hardware keyboard: W A S D or arrow keys pulse, Space stops, Esc E‑STOP (this screen keeps focus while Drive is visible)."
                            },
                            Modifier.padding(if (shellCompact) 6.dp else 10.dp),
                            fontSize = if (shellCompact) 9.sp else SirenaType.quickBlurb,
                            color = SirenaColors.text,
                            maxLines = if (shellCompact) 2 else 6,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun DriveTelemetryPill(
    title: String,
    value: String,
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier.padding(horizontal = 4.dp),
    ) {
        Text(
            title,
            fontSize = SirenaType.quickBlurb,
            color = SirenaColors.muted,
            fontWeight = FontWeight.Medium,
        )
        Text(
            value,
            fontSize = SirenaType.cardTitle,
            fontWeight = FontWeight.Bold,
            color = SirenaColors.text,
        )
    }
}

@Composable
private fun CameraCard(
    modifier: Modifier = Modifier,
    cameraPreviewOn: Boolean,
    streamRoot: String,
    visionOn: Boolean,
    cameraPreviewEnabled: Boolean,
    onCameraPreviewChange: (Boolean) -> Unit,
    speedPct: Float,
    hudHeading: String,
    hudDistance: String,
    batteryLabel: String,
    bearer: String?,
) {
    SirenaCard(modifier = modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Outlined.TwoWheeler, contentDescription = null, tint = SirenaColors.red)
                Text("Front camera", fontWeight = FontWeight.Bold, color = SirenaColors.text)
            }
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SirenaStatusPill(
                    if (cameraPreviewOn) "Live" else "Preview off",
                    if (cameraPreviewOn) SirenaPillKind.Ok else SirenaPillKind.Neutral,
                )
                Text("MJPEG", fontSize = SirenaType.muted, color = SirenaColors.muted)
                SirenaSwitch(
                    checked = cameraPreviewEnabled,
                    onCheckedChange = onCameraPreviewChange,
                    enabled = visionOn && streamRoot.isNotBlank(),
                )
            }
        }
        if (!visionOn || streamRoot.isBlank()) {
            Text(
                "Enable vision bridge on the Jetson and set a daemon URL for MJPEG preview.",
                fontSize = SirenaType.muted,
                color = SirenaColors.muted,
            )
        }
        Box(
            Modifier
                .weight(1f)
                .fillMaxWidth(),
            contentAlignment = Alignment.Center,
        ) {
            if (cameraPreviewOn && streamRoot.isNotBlank()) {
                val streamUrl = "$streamRoot/v1/vision/stream"
                SirenaMjpegImage(
                    streamUrl = streamUrl,
                    bearer = bearer,
                    modifier = Modifier.fillMaxSize(),
                    maxLongEdge = SirenaMjpegPreviewMaxLongEdge,
                )
            } else {
                Surface(
                    Modifier.fillMaxSize(),
                    shape = RoundedCornerShape(8.dp),
                    color = SirenaColors.cloud,
                ) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            "USB camera not connected",
                            color = SirenaColors.muted,
                            fontSize = SirenaType.muted,
                        )
                    }
                }
            }
        }
        HorizontalDivider(color = SirenaColors.border, thickness = 1.dp)
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceEvenly,
        ) {
            DriveTelemetryPill("Speed", "${speedPct.toInt()}%")
            DriveTelemetryPill("Heading", hudHeading)
            DriveTelemetryPill("Distance", hudDistance)
            DriveTelemetryPill("Battery", batteryLabel)
        }
    }
}

@Composable
private fun ControlCard(
    modifier: Modifier = Modifier,
    bridgeOn: Boolean,
    motionBusy: Boolean,
    autonomyOn: Boolean,
    autonomyApi: Boolean,
    onAutonomyChange: (Boolean) -> Unit,
    invertLeft: Boolean,
    invertRight: Boolean,
    onInvertLeft: (Boolean) -> Unit,
    onInvertRight: (Boolean) -> Unit,
    brakeOn: Boolean,
    reverseOn: Boolean,
    onBrakeChange: (Boolean) -> Unit,
    onReverseChange: (Boolean) -> Unit,
    speedMin: Int,
    speedMax: Int,
    speedPct: Float,
    onSpeedChange: (Float) -> Unit,
    pulseIntervalMs: Long,
    onDrivePulse: suspend (String) -> JSONObject,
    onDriveResult: (JSONObject) -> Unit,
    onStraightTest: () -> Unit,
    onTurnLeft: () -> Unit,
    onTurnRight: () -> Unit,
    onEstop: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val smin = speedMin.toFloat()
    val smax = speedMax.toFloat()
    val steps = (speedMax - speedMin).coerceAtLeast(0)
    val padMovesEnabled = bridgeOn && !brakeOn && !motionBusy
    val timedMovesEnabled = bridgeOn && !brakeOn && !autonomyOn && !motionBusy
    val releaseStop: () -> Unit = {
        scope.launch {
            if (!bridgeOn) return@launch
            try {
                val j = onDrivePulse("stop")
                onDriveResult(j)
            } catch (_: Exception) {
            }
        }
    }
    SirenaCard(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "Manual",
                fontSize = SirenaType.cardTitle,
                fontWeight = FontWeight.SemiBold,
                color = SirenaColors.red,
            )
            if (autonomyApi) {
                SirenaPrimaryButton(
                    text = if (autonomyOn) "Auto: ON" else "Auto: OFF",
                    onClick = { onAutonomyChange(!autonomyOn) },
                    enabled = bridgeOn,
                )
            } else {
                Text(
                    "Auto: N/A",
                    fontSize = SirenaType.muted,
                    color = SirenaColors.muted,
                )
            }
        }

        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
            SirenaDpadHoldButton(
                "\u2191",
                padMovesEnabled,
                pulseIntervalMs,
                {
                    val j = onDrivePulse("forward")
                    onDriveResult(j)
                },
                onRelease = releaseStop,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                SirenaDpadHoldButton(
                    "\u2190",
                    padMovesEnabled,
                    pulseIntervalMs,
                    {
                        val j = onDrivePulse("left")
                        onDriveResult(j)
                    },
                    onRelease = releaseStop,
                )
                SirenaDpadStop("STOP", {
                    scope.launch {
                        val j = onDrivePulse("stop")
                        onDriveResult(j)
                    }
                }, enabled = bridgeOn)
                SirenaDpadHoldButton(
                    "\u2192",
                    padMovesEnabled,
                    pulseIntervalMs,
                    {
                        val j = onDrivePulse("right")
                        onDriveResult(j)
                    },
                    onRelease = releaseStop,
                )
            }
            SirenaDpadHoldButton(
                "\u2193",
                padMovesEnabled,
                pulseIntervalMs,
                {
                    val j = onDrivePulse("back")
                    onDriveResult(j)
                },
                onRelease = releaseStop,
            )
        }

        SirenaSecondaryButton(
            text = "Straight 10s",
            onClick = onStraightTest,
            modifier = Modifier.fillMaxWidth(),
            enabled = timedMovesEnabled,
        )

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SirenaSecondaryButton(
                text = "Turn left",
                onClick = onTurnLeft,
                modifier = Modifier.weight(1f),
                enabled = timedMovesEnabled,
            )
            SirenaSecondaryButton(
                text = "Turn right",
                onClick = onTurnRight,
                modifier = Modifier.weight(1f),
                enabled = timedMovesEnabled,
            )
        }

        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text("Speed", fontSize = SirenaType.quickBlurb, color = SirenaColors.muted)
            SirenaSlider(
                modifier = Modifier.weight(1f),
                value = speedPct.coerceIn(smin, smax),
                onValueChange = { onSpeedChange(it.coerceIn(smin, smax)) },
                valueRange = smin..smax,
                steps = steps.coerceAtLeast(0),
                enabled = bridgeOn,
            )
            Surface(shape = RoundedCornerShape(999.dp), color = SirenaColors.pillErrorBg) {
                Text(
                    "${speedPct.toInt()}%",
                    Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                    fontSize = SirenaType.pill,
                    color = SirenaColors.pillErrorFg,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Wheels",
                fontSize = SirenaType.quickBlurb,
                color = SirenaColors.muted,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Flip L", fontSize = SirenaType.quickBlurb, color = SirenaColors.text)
                SirenaSwitch(
                    checked = invertLeft,
                    onCheckedChange = onInvertLeft,
                    enabled = bridgeOn,
                )
                Text("Flip R", fontSize = SirenaType.quickBlurb, color = SirenaColors.text)
                SirenaSwitch(
                    checked = invertRight,
                    onCheckedChange = onInvertRight,
                    enabled = bridgeOn,
                )
            }
        }

        SirenaMutedText(
            "If both wheels spin opposite directions on Forward, toggle Flip L or Flip R until they match.",
            maxLines = 2,
        )

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SirenaTogglePill(
                text = if (brakeOn) "Brake: ON" else "Brake: OFF",
                checked = brakeOn,
                onClick = { onBrakeChange(!brakeOn) },
                enabled = bridgeOn,
            )
            SirenaTogglePill(
                text = if (reverseOn) "Reverse: ON" else "Reverse: OFF",
                checked = reverseOn,
                onClick = { onReverseChange(!reverseOn) },
                enabled = bridgeOn,
            )
            Spacer(Modifier.weight(1f))
            SirenaEstopButton(
                text = "\u26A0 E‑STOP",
                onClick = onEstop,
                enabled = bridgeOn,
            )
        }

    }
}
