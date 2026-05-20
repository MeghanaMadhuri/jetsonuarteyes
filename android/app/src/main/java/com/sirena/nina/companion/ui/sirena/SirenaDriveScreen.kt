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
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.data.LinkApiException
import com.sirena.nina.companion.util.NinaLog
import java.io.IOException
import java.net.SocketTimeoutException
import kotlin.math.sqrt
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.supervisorScope
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

/** Matches kiosk ``STRAIGHT_READY_POLL_MS``; shorter cap than kiosk (HTTP already waited on prime). */
private const val STRAIGHT_READY_POLL_MS = 50L
private const val STRAIGHT_READY_MAX_POLLS = 30
/** Pulse bench can run up to ~120s; never leave the UI locked longer. */
private const val STRAIGHT_BENCH_MAX_MS = 130_000L
/** Idle poll — slow enough to stay behind drive POSTs on the Jetson command plane. */
private const val DRIVE_STATUS_POLL_MS = 2000L
/** While the operator is holding the D-pad, pause polls so stop/hold are not queued behind GETs. */
private const val DRIVE_STATUS_POLL_WHILE_DRIVE_MS = 4000L
private const val DRIVE_STATUS_FAIL_DISCONNECT = 3

private fun driveHttpError(e: Exception): String =
    when (e) {
        is SocketTimeoutException ->
            "Drive timeout — robot still starting motors. Wait for green status, then retry."
        is LinkApiException ->
            when (e.code) {
                500 -> "Drive server error (HTTP 500) — tap E‑STOP, release brake, retry"
                503 -> "Drive bridge busy or off (HTTP 503)"
                else -> e.message?.trim().orEmpty().ifBlank { "HTTP ${e.code}" }
            }
        is IOException ->
            e.message?.trim().orEmpty().ifBlank { "Network error — check Wi‑Fi to the Jetson" }
        else -> e.message?.trim().orEmpty().ifBlank { "Drive request failed" }
    }

/** Stop motion and engage brake on the robot (parallel, non-blocking UI). */
private fun launchDriveHalt(
    scope: CoroutineScope,
    vm: CompanionViewModel,
    emergency: Boolean,
    onUiStopped: () -> Unit,
    onError: (String?) -> Unit,
) {
    onUiStopped()
    scope.launch {
        var err: String? = null
        supervisorScope {
            if (emergency) {
                launch {
                    runCatching { vm.robotEmergencyStop() }
                        .onFailure { t ->
                            if (t is Exception) err = driveHttpError(t)
                        }
                }
            }
            launch {
                runCatching { vm.robotDriveHoldStop() }
                    .onFailure { t ->
                        if (err == null && t is Exception) err = driveHttpError(t)
                    }
            }
            launch {
                runCatching { vm.robotDriveStraightStop() }
            }
            launch {
                runCatching { vm.robotSetBrake(true) }
                    .onFailure { t ->
                        if (err == null && t is Exception) err = driveHttpError(t)
                    }
            }
        }
        onError(err)
    }
}

private fun formatImuHud(drift: Double, side: String, straightActive: Boolean): String {
    if (!straightActive) return "—"
    if (side == "off") return "IMU off"
    if (!drift.isFinite() || side == "n/a") return "—"
    val sym =
        when (side) {
            "left" -> "\u2190"
            "right" -> "\u2192"
            else -> "\u00b7"
        }
    return String.format("%+.1f\u00b0 %s", drift, sym)
}

private suspend fun CompanionViewModel.awaitDriveHardwareReady(): JSONObject? {
    repeat(STRAIGHT_READY_MAX_POLLS) {
        val j = fetchRobotDriveStatus()
        if (j?.optBoolean("connected") == true) return j
        delay(STRAIGHT_READY_POLL_MS)
    }
    return fetchRobotDriveStatus()
}

private suspend fun CompanionViewModel.pollUntilStraightBenchDone() {
    val deadline = System.currentTimeMillis() + STRAIGHT_BENCH_MAX_MS
    var sawPulseActive = false
    while (System.currentTimeMillis() < deadline) {
        delay(STRAIGHT_READY_POLL_MS)
        val j = fetchRobotDriveStatus() ?: continue
        val active = j.optBoolean("straight_pulse_active", false)
        if (active) {
            sawPulseActive = true
            continue
        }
        // Do not treat idle-before-start as "done" — only end after pulse was seen.
        if (sawPulseActive) return
    }
}

/**
 * Mirrors [sirena_ui.screens.drive_screen.DriveScreen] —
 * camera preview (MJPEG when vision bridge on), manual BLDC pulses; Auto is local UI only (coming soon).
 */
@Composable
fun SirenaDriveScreen(
    vm: CompanionViewModel,
    caps: JSONObject?,
    daemonUrl: String?,
    shellCompact: Boolean = false,
    onOpenMotionCalibration: (() -> Unit)? = null,
) {
    val scope = rememberCoroutineScope()
    var actionErr by remember { mutableStateOf<String?>(null) }
    // When capabilities are missing, assume drive is off until a successful status refresh loads them.
    val bridgeOn = caps?.optBoolean("robot_bridge_enabled") ?: false
    val visionOn = caps?.optBoolean("vision_bridge_enabled") ?: false
    var straightRunning by remember { mutableStateOf(false) }
    var keyboardDriveDir by remember { mutableStateOf<String?>(null) }
    var cameraPreviewOn by remember { mutableStateOf(false) }
    var bldcConnected by remember { mutableStateOf<Boolean?>(null) }
    var bldcInitializing by remember { mutableStateOf(false) }
    var bldcDetail by remember { mutableStateOf<String?>(null) }
    var brakeOn by remember { mutableStateOf(true) }
    var reverseOn by remember { mutableStateOf(false) }
    var hudSpeed by remember { mutableStateOf("0%") }
    var hudHeading by remember { mutableStateOf("—") }
    var hudDistance by remember { mutableStateOf("—") }
    var hudImu by remember { mutableStateOf("—") }
    var batteryLabel by remember { mutableStateOf("n/a") }
    val slamOn = caps?.optBoolean("slam_bridge_enabled") == true

    val jetsonLink by vm.jetsonLink.collectAsStateWithLifecycle()
    val bearer by vm.bearerToken.collectAsStateWithLifecycle(initialValue = null)
    val driveLogs by vm.driveCommandLog.collectAsStateWithLifecycle()
    val jetsonOnline = jetsonLink.isOnline
    var driveHoldJob by remember { mutableStateOf<Job?>(null) }
    val slimBanner = rememberSlimStatusBanner()
    val slimCopy = slimBanner || shellCompact
    val focusRequester = remember { FocusRequester() }

    val streamRoot = daemonUrl?.trimEnd('/') ?: ""

    DisposableEffect(bridgeOn, jetsonOnline) {
        onDispose {
            if (!bridgeOn || !jetsonOnline) return@onDispose
            scope.launch {
                try {
                    vm.robotDriveHoldStop()
                } catch (_: Exception) {
                }
                try {
                    vm.robotDriveStraightStop()
                } catch (_: Exception) {
                }
            }
        }
    }

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

    LaunchedEffect(bridgeOn, jetsonOnline, straightRunning) {
        if (!bridgeOn || !jetsonOnline) {
            bldcConnected = null
            bldcInitializing = false
            bldcDetail = null
            return@LaunchedEffect
        }
        delay(120)
        focusRequester.requestFocus()
        launch { vm.prefetchRobotDriveStatus() }
        var statusFailStreak = 0
        while (isActive) {
            val operatorDriving =
                driveHoldJob?.isActive == true || keyboardDriveDir != null
            val pollMs =
                when {
                    straightRunning -> 50L
                    operatorDriving -> DRIVE_STATUS_POLL_WHILE_DRIVE_MS
                    else -> DRIVE_STATUS_POLL_MS
                }
            try {
                val j = vm.fetchRobotDriveStatus()
                if (j != null) {
                    statusFailStreak = 0
                    val initializing = j.optBoolean("hardware_initializing", false)
                    bldcInitializing = initializing
                    if (initializing) {
                        bldcConnected = null
                    } else {
                        bldcConnected = j.optBoolean("connected")
                    }
                    val msg = j.optString("message").trim()
                    val lde = j.optString("last_drive_error").trim()
                    bldcDetail =
                        when {
                            msg.isNotEmpty() && lde.isNotEmpty() -> "$msg · $lde"
                            msg.isNotEmpty() -> msg
                            lde.isNotEmpty() -> lde
                            else -> null
                        }
                    if (j.has("brake") && !straightRunning) {
                        brakeOn = j.optBoolean("brake", true)
                    }
                    if (j.has("reverse")) reverseOn = j.optBoolean("reverse", false)
                    if (j.has("speed_pct")) hudSpeed = "${j.optInt("speed_pct")}%"
                    val drift = j.optDouble("imu_drift_deg")
                    val side = j.optString("imu_drift_side", "n/a")
                    val straightActive = j.optBoolean("straight_pulse_active", false)
                    hudImu = formatImuHud(drift, side, straightActive || straightRunning)
                } else {
                    statusFailStreak += 1
                    bldcInitializing = false
                    if (statusFailStreak >= DRIVE_STATUS_FAIL_DISCONNECT) {
                        bldcConnected = false
                    }
                    if (bldcDetail.isNullOrBlank()) {
                        bldcDetail = "drive status unreachable"
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                statusFailStreak += 1
                bldcInitializing = false
                if (statusFailStreak >= DRIVE_STATUS_FAIL_DISCONNECT) {
                    bldcConnected = false
                }
                bldcDetail = driveHttpError(e)
            }
            delay(pollMs)
        }
    }

    LaunchedEffect(straightRunning) {
        if (!straightRunning) return@LaunchedEffect
        delay(STRAIGHT_BENCH_MAX_MS)
        if (straightRunning) {
            straightRunning = false
            hudImu = "—"
            scope.launch {
                runCatching { vm.robotDriveStraightStop() }
                runCatching { vm.robotDriveHoldStop() }
            }
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

    Column(
        Modifier
            .fillMaxSize()
            .focusRequester(focusRequester)
            .focusable()
            .onPreviewKeyEvent { ev ->
                if (!bridgeOn || !jetsonOnline || straightRunning) return@onPreviewKeyEvent false
                val code = ev.nativeKeyEvent.keyCode
                val dir =
                    when (code) {
                        KeyEvent.KEYCODE_W, KeyEvent.KEYCODE_DPAD_UP -> "forward"
                        KeyEvent.KEYCODE_S, KeyEvent.KEYCODE_DPAD_DOWN -> "back"
                        KeyEvent.KEYCODE_A, KeyEvent.KEYCODE_DPAD_LEFT -> "left"
                        KeyEvent.KEYCODE_D, KeyEvent.KEYCODE_DPAD_RIGHT -> "right"
                        else -> null
                    }
                when (ev.type) {
                    KeyEventType.KeyDown -> {
                        when (code) {
                            KeyEvent.KEYCODE_ESCAPE -> {
                                keyboardDriveDir = null
                                launchDriveHalt(
                                    scope,
                                    vm,
                                    emergency = true,
                                    onUiStopped = {
                                        brakeOn = true
                                        straightRunning = false
                                    },
                                    onError = { actionErr = it },
                                )
                                return@onPreviewKeyEvent true
                            }
                            KeyEvent.KEYCODE_SPACE -> {
                                keyboardDriveDir = null
                                launchDriveHalt(
                                    scope,
                                    vm,
                                    emergency = false,
                                    onUiStopped = {
                                        brakeOn = true
                                        straightRunning = false
                                    },
                                    onError = { actionErr = it },
                                )
                                return@onPreviewKeyEvent true
                            }
                        }
                        if (dir == null || brakeOn) return@onPreviewKeyEvent false
                        if (keyboardDriveDir == dir) return@onPreviewKeyEvent true
                        keyboardDriveDir = dir
                        scope.launch {
                            try {
                                val j = vm.robotDriveHold(dir)
                                actionErr = j.driveCommandErrorOrNull()
                            } catch (e: Exception) {
                                actionErr = driveHttpError(e)
                            }
                        }
                        true
                    }
                    KeyEventType.KeyUp -> {
                        if (dir == null || keyboardDriveDir != dir) return@onPreviewKeyEvent false
                        keyboardDriveDir = null
                        scope.launch {
                            try {
                                vm.robotDriveHoldStop()
                            } catch (_: Exception) {
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
            val bldcKind =
                when {
                    !bridgeOn || !jetsonOnline -> SirenaPillKind.Neutral
                    bldcInitializing -> SirenaPillKind.Warn
                    bldcConnected == true -> SirenaPillKind.Ok
                    bldcConnected == false -> SirenaPillKind.Error
                    else -> SirenaPillKind.Neutral
                }
            SirenaStatusPill(
                when {
                    !bridgeOn -> "BLDC · bridge off"
                    !jetsonOnline -> "BLDC · offline"
                    bldcInitializing -> {
                        val d = bldcDetail
                        if (!d.isNullOrBlank()) "BLDC · ${d.take(48)}" else "BLDC · initializing…"
                    }
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
                hudSpeed = hudSpeed,
                hudHeading = hudHeading,
                hudDistance = hudDistance,
                hudImu = hudImu,
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
                        straightRunning = straightRunning,
                        brakeOn = brakeOn,
                        reverseOn = reverseOn,
                        onBrakeChange = { wantOn ->
                            if (!bridgeOn || !jetsonOnline) {
                                brakeOn = wantOn
                            } else {
                                scope.launch {
                                    val prev = brakeOn
                                    brakeOn = wantOn
                                    try {
                                        if (wantOn) {
                                            vm.robotDriveHoldStop()
                                            if (straightRunning) vm.robotDriveStraightStop()
                                        }
                                        val j = vm.robotSetBrake(wantOn)
                                        val err = j.driveCommandErrorOrNull()
                                        if (err != null) {
                                            brakeOn = prev
                                            actionErr = err
                                            return@launch
                                        }
                                        brakeOn = j.optBoolean("brake", wantOn)
                                        if (wantOn) straightRunning = false
                                        actionErr = null
                                    } catch (e: CancellationException) {
                                        throw e
                                    } catch (e: Exception) {
                                        brakeOn = prev
                                        actionErr = e.message ?: "Brake request failed"
                                    }
                                }
                            }
                        },
                        onReverseChange = { want ->
                            if (!bridgeOn || !jetsonOnline) {
                                reverseOn = want
                            } else {
                                scope.launch {
                                    val prev = reverseOn
                                    reverseOn = want
                                    try {
                                        val j = vm.robotDriveReverse(want)
                                        val err = j.driveCommandErrorOrNull()
                                        if (err != null) {
                                            reverseOn = prev
                                            actionErr = err
                                        } else {
                                            reverseOn = j.optBoolean("reverse", want)
                                            actionErr = null
                                        }
                                    } catch (e: CancellationException) {
                                        throw e
                                    } catch (e: Exception) {
                                        reverseOn = prev
                                        actionErr = e.message ?: "Reverse request failed"
                                    }
                                }
                            }
                        },
                        onOpenMotionCalibration = onOpenMotionCalibration,
                        onDriveHoldStart = { dir ->
                            when {
                                straightRunning ->
                                    actionErr = "Wait for straight test to finish."
                                brakeOn ->
                                    actionErr = "Release brake to drive."
                                else -> {
                                    actionErr = null
                                    driveHoldJob?.cancel()
                                    driveHoldJob =
                                        scope.launch {
                                            try {
                                                val j = vm.robotDriveHold(dir)
                                                val err = j.driveCommandErrorOrNull()
                                                if (err != null) actionErr = err
                                            } catch (e: CancellationException) {
                                                throw e
                                            } catch (e: Exception) {
                                                actionErr = driveHttpError(e)
                                            }
                                        }
                                }
                            }
                        },
                        onDriveHoldStop = {
                            driveHoldJob?.cancel()
                            driveHoldJob = null
                            scope.launch {
                                try {
                                    val j = vm.robotDriveHoldStop()
                                    val err = j.driveCommandErrorOrNull()
                                    if (err != null) actionErr = err
                                } catch (e: Exception) {
                                    actionErr = driveHttpError(e)
                                }
                            }
                        },
                        onDriveStopWithBrake = {
                            launchDriveHalt(
                                scope,
                                vm,
                                emergency = false,
                                onUiStopped = {
                                    brakeOn = true
                                    straightRunning = false
                                },
                                onError = { actionErr = it },
                            )
                        },
                        onStraightFront = {
                            scope.launch {
                                when {
                                    !bridgeOn -> actionErr = "Drive bridge off."
                                    brakeOn -> actionErr = "Release brake (Brake: OFF) first."
                                    straightRunning -> actionErr = "Straight test already running."
                                    else -> {
                                        straightRunning = true
                                        actionErr = null
                                        try {
                                            if (bldcConnected != true) {
                                                val ready = vm.awaitDriveHardwareReady()
                                                if (ready?.optBoolean("connected") != true) {
                                                    actionErr =
                                                        ready?.optString("message").orEmpty().ifBlank {
                                                            "Drive hardware not ready — wait for green status."
                                                        }
                                                    return@launch
                                                }
                                            }
                                            val j = vm.robotDriveStraight(backward = false)
                                            val err = j.driveCommandErrorOrNull()
                                            if (err != null) {
                                                actionErr = err
                                                return@launch
                                            }
                                            vm.pollUntilStraightBenchDone()
                                        } catch (e: CancellationException) {
                                            throw e
                                        } catch (e: Exception) {
                                            actionErr = driveHttpError(e)
                                        } finally {
                                            try {
                                                vm.robotDriveStraightStop()
                                            } catch (_: Exception) {
                                            }
                                            straightRunning = false
                                            hudImu = "—"
                                        }
                                    }
                                }
                            }
                        },
                        onStraightBack = {
                            scope.launch {
                                when {
                                    !bridgeOn -> actionErr = "Drive bridge off."
                                    brakeOn -> actionErr = "Release brake (Brake: OFF) first."
                                    straightRunning -> actionErr = "Straight test already running."
                                    else -> {
                                        straightRunning = true
                                        actionErr = null
                                        try {
                                            if (bldcConnected != true) {
                                                val ready = vm.awaitDriveHardwareReady()
                                                if (ready?.optBoolean("connected") != true) {
                                                    actionErr =
                                                        ready?.optString("message").orEmpty().ifBlank {
                                                            "Drive hardware not ready — wait for green status."
                                                        }
                                                    return@launch
                                                }
                                            }
                                            val j = vm.robotDriveStraight(backward = true)
                                            val err = j.driveCommandErrorOrNull()
                                            if (err != null) {
                                                actionErr = err
                                                return@launch
                                            }
                                            vm.pollUntilStraightBenchDone()
                                        } catch (e: CancellationException) {
                                            throw e
                                        } catch (e: Exception) {
                                            actionErr = driveHttpError(e)
                                        } finally {
                                            try {
                                                vm.robotDriveStraightStop()
                                            } catch (_: Exception) {
                                            }
                                            straightRunning = false
                                            hudImu = "—"
                                        }
                                    }
                                }
                            }
                        },
                        onTurnLeft = {
                            scope.launch {
                                when {
                                    !bridgeOn -> actionErr = "Drive bridge off."
                                    brakeOn -> actionErr = "Release brake (Brake: OFF) first."
                                    straightRunning -> actionErr = "Wait for straight test to finish."
                                    else -> {
                                        actionErr = null
                                        try {
                                            val j = vm.robotDriveTurn("left")
                                            actionErr = j.driveCommandErrorOrNull()
                                        } catch (e: CancellationException) {
                                            throw e
                                        } catch (e: Exception) {
                                            actionErr = driveHttpError(e)
                                        }
                                    }
                                }
                            }
                        },
                        onTurnRight = {
                            scope.launch {
                                when {
                                    !bridgeOn -> actionErr = "Drive bridge off."
                                    brakeOn -> actionErr = "Release brake (Brake: OFF) first."
                                    straightRunning -> actionErr = "Wait for straight test to finish."
                                    else -> {
                                        actionErr = null
                                        try {
                                            val j = vm.robotDriveTurn("right")
                                            actionErr = j.driveCommandErrorOrNull()
                                        } catch (e: CancellationException) {
                                            throw e
                                        } catch (e: Exception) {
                                            actionErr = driveHttpError(e)
                                        }
                                    }
                                }
                            }
                        },
                        onEstop = {
                            launchDriveHalt(
                                scope,
                                vm,
                                emergency = true,
                                onUiStopped = {
                                    brakeOn = true
                                    straightRunning = false
                                },
                                onError = { actionErr = it },
                            )
                        },
                    )

                    Surface(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(8.dp),
                        color = SirenaColors.calloutBg,
                    ) {
                        Text(
                            if (shellCompact) {
                                "D‑pad hold-to-drive (same as kiosk) · Straight front/back with IMU correction · WASD / Space / Esc."
                            } else {
                                "D‑pad hold matches the kiosk Drive screen (lean while pressed, stop on release). " +
                                    "Straight front / Straight back run the same hoverboard pulse bench with IMU self-correction on the Jetson. " +
                                    "Keyboard: W A S D or arrows to drive, Space to stop, Esc for E‑STOP."
                            },
                            Modifier.padding(if (shellCompact) 6.dp else 10.dp),
                            fontSize = if (shellCompact) 9.sp else SirenaType.quickBlurb,
                            color = SirenaColors.text,
                            maxLines = if (shellCompact) 2 else 6,
                        )
                    }
                    SirenaCard(kind = SirenaCardKind.Subtle) {
                        Text(
                            "Drive command log",
                            fontSize = SirenaType.base,
                            fontWeight = FontWeight.SemiBold,
                            color = SirenaColors.text,
                        )
                        if (driveLogs.isEmpty()) {
                            SirenaMutedText("No drive commands sent yet.")
                        } else {
                            driveLogs.takeLast(8).forEach { row ->
                                Text(
                                    "${row.timestamp}  ${row.line}",
                                    fontSize = SirenaType.quickBlurb,
                                    color = SirenaColors.text,
                                )
                            }
                            SirenaMutedText("Full log file: files/logs/nina_companion.log")
                        }
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
    hudSpeed: String,
    hudHeading: String,
    hudDistance: String,
    hudImu: String,
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
                    targetFps = SirenaMjpegDefaultTargetFps,
                )
            } else {
                Surface(
                    Modifier.fillMaxSize(),
                    shape = RoundedCornerShape(8.dp),
                    color = SirenaColors.cloud,
                ) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(
                            when {
                                !cameraPreviewEnabled -> "Turn on preview"
                                !visionOn || streamRoot.isBlank() ->
                                    "Enable vision bridge and connect to the robot"
                                else -> "Opening camera…"
                            },
                            color = SirenaColors.muted,
                            fontSize = SirenaType.muted,
                            textAlign = TextAlign.Center,
                            modifier = Modifier.padding(12.dp),
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
            DriveTelemetryPill("Speed", hudSpeed)
            DriveTelemetryPill("Heading", hudHeading)
            DriveTelemetryPill("Distance", hudDistance)
            DriveTelemetryPill("Battery", batteryLabel)
            DriveTelemetryPill("IMU drift", hudImu)
        }
    }
}

@Composable
private fun ControlCard(
    modifier: Modifier = Modifier,
    bridgeOn: Boolean,
    straightRunning: Boolean,
    brakeOn: Boolean,
    reverseOn: Boolean,
    onBrakeChange: (Boolean) -> Unit,
    onReverseChange: (Boolean) -> Unit,
    onDriveHoldStart: (String) -> Unit,
    onDriveHoldStop: () -> Unit,
    onDriveStopWithBrake: () -> Unit,
    onStraightFront: () -> Unit,
    onStraightBack: () -> Unit,
    onTurnLeft: () -> Unit,
    onTurnRight: () -> Unit,
    onEstop: () -> Unit,
    onOpenMotionCalibration: (() -> Unit)?,
) {
    var autoComingSoonOpen by remember { mutableStateOf(false) }
    val padMovesEnabled = bridgeOn && !brakeOn && !straightRunning
    val timedMovesEnabled = bridgeOn && !brakeOn && !straightRunning
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
            // Local-only: no ViewModel / Jetson calls — coming-soon dialog only.
            SirenaPrimaryButton(
                text = "Auto",
                onClick = { autoComingSoonOpen = true },
            )
        }

        if (autoComingSoonOpen) {
            SirenaConfirmDialog(
                onDismiss = { autoComingSoonOpen = false },
                title = "Autonomous mode",
                message = "Autonomous mode is coming soon. Use manual drive and the D-pad for now.",
                confirmText = "OK",
                onConfirm = { autoComingSoonOpen = false },
            )
        }

        onOpenMotionCalibration?.let { openCal ->
            SirenaSecondaryButton(
                text = "Motion calibration",
                onClick = openCal,
                modifier = Modifier.fillMaxWidth(),
                enabled = bridgeOn,
            )
        }

        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
            SirenaDpadHoldButton(
                "\u2191",
                padMovesEnabled,
                onPress = { onDriveHoldStart("forward") },
                onRelease = onDriveHoldStop,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                SirenaDpadHoldButton(
                    "\u2190",
                    padMovesEnabled,
                    onPress = { onDriveHoldStart("left") },
                    onRelease = onDriveHoldStop,
                )
                SirenaDpadStop("STOP", onDriveStopWithBrake, enabled = bridgeOn)
                SirenaDpadHoldButton(
                    "\u2192",
                    padMovesEnabled,
                    onPress = { onDriveHoldStart("right") },
                    onRelease = onDriveHoldStop,
                )
            }
            SirenaDpadHoldButton(
                "\u2193",
                padMovesEnabled,
                onPress = { onDriveHoldStart("back") },
                onRelease = onDriveHoldStop,
            )
        }

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SirenaSecondaryButton(
                text = "Straight front",
                onClick = onStraightFront,
                modifier = Modifier.weight(1f),
                enabled = timedMovesEnabled,
            )
            SirenaSecondaryButton(
                text = "Straight back",
                onClick = onStraightBack,
                modifier = Modifier.weight(1f),
                enabled = timedMovesEnabled,
            )
        }

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

        SirenaMutedText(
            "WASD drive · Space stop · Esc E‑STOP",
            maxLines = 1,
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
