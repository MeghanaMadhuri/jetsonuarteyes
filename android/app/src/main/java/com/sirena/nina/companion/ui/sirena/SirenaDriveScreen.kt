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

/** Matches kiosk ``STRAIGHT_READY_POLL_MS`` / ``STRAIGHT_READY_MAX_POLLS``. */
private const val STRAIGHT_READY_POLL_MS = 50L
private const val STRAIGHT_READY_MAX_POLLS = 100

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
    while (true) {
        delay(STRAIGHT_READY_POLL_MS)
        val j = fetchRobotDriveStatus() ?: break
        if (!j.optBoolean("straight_pulse_active", false)) break
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
    var motionBusy by remember { mutableStateOf(false) }
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
    val jetsonOnline = jetsonLink.isOnline
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
        delay(400)
        focusRequester.requestFocus()
        while (isActive) {
            var pollMs = if (straightRunning) 50L else 2500L
            try {
                val j = vm.fetchRobotDriveStatus()
                if (j != null) {
                    val initializing = j.optBoolean("hardware_initializing", false)
                    bldcInitializing = initializing
                    if (initializing) {
                        bldcConnected = null
                        pollMs = 500L
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
                    if (j.has("brake")) brakeOn = j.optBoolean("brake", true)
                    if (j.has("reverse")) reverseOn = j.optBoolean("reverse", false)
                    if (j.has("speed_pct")) hudSpeed = "${j.optInt("speed_pct")}%"
                    val drift = j.optDouble("imu_drift_deg")
                    val side = j.optString("imu_drift_side", "n/a")
                    val straightActive = j.optBoolean("straight_pulse_active", false)
                    hudImu = formatImuHud(drift, side, straightActive || straightRunning)
                } else {
                    bldcInitializing = false
                    bldcConnected = false
                    if (bldcDetail.isNullOrBlank()) {
                        bldcDetail = "drive status unreachable"
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                bldcInitializing = false
                bldcConnected = false
                if (bldcDetail.isNullOrBlank()) {
                    bldcDetail = "drive status unreachable"
                }
            }
            delay(pollMs)
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
                if (!bridgeOn || !jetsonOnline || motionBusy) return@onPreviewKeyEvent false
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
                                brakeOn = true
                                scope.launch {
                                    try {
                                        vm.robotDriveHoldStop()
                                        val j = vm.robotEmergencyStop()
                                        actionErr = j.driveCommandErrorOrNull()
                                    } catch (e: Exception) {
                                        actionErr = e.message
                                    }
                                }
                                return@onPreviewKeyEvent true
                            }
                            KeyEvent.KEYCODE_SPACE -> {
                                scope.launch {
                                    try {
                                        keyboardDriveDir = null
                                        vm.robotDriveHoldStop()
                                    } catch (e: Exception) {
                                        actionErr = e.message
                                    }
                                }
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
                                actionErr = e.message
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
                        motionBusy = motionBusy,
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
                            if (motionBusy || straightRunning) {
                                JSONObject().put("ok", false).put("error", "Wait for timed move to finish.")
                            } else if (brakeOn) {
                                JSONObject().put("ok", false).put("error", "Release brake to drive.")
                            } else {
                                vm.robotDriveHold(dir)
                            }
                        },
                        onDriveHoldStop = { vm.robotDriveHoldStop() },
                        onDriveResult = { j -> actionErr = j.driveCommandErrorOrNull() },
                        onStraightFront = {
                            scope.launch {
                                if (!bridgeOn || brakeOn || motionBusy) return@launch
                                motionBusy = true
                                straightRunning = true
                                try {
                                    val ready =
                                        if (driveConnected) {
                                            vm.fetchRobotDriveStatus()
                                        } else {
                                            vm.awaitDriveHardwareReady()
                                        }
                                    if (ready?.optBoolean("connected") != true) {
                                        actionErr =
                                            ready?.optString("message").orEmpty().ifBlank {
                                                "Drive hardware not ready — wait for green status."
                                            }
                                        return@launch
                                    }
                                    val j = vm.robotDriveStraight(backward = false)
                                    val err = j.driveCommandErrorOrNull()
                                    if (err != null) {
                                        actionErr = err
                                        return@launch
                                    }
                                    actionErr = null
                                    vm.pollUntilStraightBenchDone()
                                } catch (e: CancellationException) {
                                    throw e
                                } catch (e: Exception) {
                                    actionErr = e.message
                                } finally {
                                    try {
                                        vm.robotDriveStraightStop()
                                    } catch (_: Exception) {
                                    }
                                    straightRunning = false
                                    motionBusy = false
                                    hudImu = "—"
                                }
                            }
                        },
                        onStraightBack = {
                            scope.launch {
                                if (!bridgeOn || brakeOn || motionBusy) return@launch
                                motionBusy = true
                                straightRunning = true
                                try {
                                    val ready =
                                        if (driveConnected) {
                                            vm.fetchRobotDriveStatus()
                                        } else {
                                            vm.awaitDriveHardwareReady()
                                        }
                                    if (ready?.optBoolean("connected") != true) {
                                        actionErr =
                                            ready?.optString("message").orEmpty().ifBlank {
                                                "Drive hardware not ready — wait for green status."
                                            }
                                        return@launch
                                    }
                                    val j = vm.robotDriveStraight(backward = true)
                                    val err = j.driveCommandErrorOrNull()
                                    if (err != null) {
                                        actionErr = err
                                        return@launch
                                    }
                                    actionErr = null
                                    vm.pollUntilStraightBenchDone()
                                } catch (e: CancellationException) {
                                    throw e
                                } catch (e: Exception) {
                                    actionErr = e.message
                                } finally {
                                    try {
                                        vm.robotDriveStraightStop()
                                    } catch (_: Exception) {
                                    }
                                    straightRunning = false
                                    motionBusy = false
                                    hudImu = "—"
                                }
                            }
                        },
                        onTurnLeft = {
                            scope.launch {
                                if (!bridgeOn || brakeOn || motionBusy) return@launch
                                motionBusy = true
                                try {
                                    val j = vm.robotDriveTurn("left")
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
                        onTurnRight = {
                            scope.launch {
                                if (!bridgeOn || brakeOn || motionBusy) return@launch
                                motionBusy = true
                                try {
                                    val j = vm.robotDriveTurn("right")
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
                        onEstop = {
                            brakeOn = true
                            scope.launch {
                                try {
                                    vm.robotDriveHoldStop()
                                    if (straightRunning) vm.robotDriveStraightStop()
                                    val j = vm.robotEmergencyStop()
                                    actionErr = j.driveCommandErrorOrNull()
                                } catch (e: Exception) {
                                    actionErr = e.message
                                } finally {
                                    straightRunning = false
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
    motionBusy: Boolean,
    brakeOn: Boolean,
    reverseOn: Boolean,
    onBrakeChange: (Boolean) -> Unit,
    onReverseChange: (Boolean) -> Unit,
    onDriveHoldStart: suspend (String) -> JSONObject,
    onDriveHoldStop: suspend () -> JSONObject,
    onDriveResult: (JSONObject) -> Unit,
    onStraightFront: () -> Unit,
    onStraightBack: () -> Unit,
    onTurnLeft: () -> Unit,
    onTurnRight: () -> Unit,
    onEstop: () -> Unit,
    onOpenMotionCalibration: (() -> Unit)?,
) {
    var autoComingSoonOpen by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val padMovesEnabled = bridgeOn && !brakeOn && !motionBusy
    val timedMovesEnabled = bridgeOn && !brakeOn && !motionBusy
    val releaseStop: suspend () -> Unit = {
        if (bridgeOn) {
            try {
                val j = onDriveHoldStop()
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
                onPress = {
                    val j = onDriveHoldStart("forward")
                    onDriveResult(j)
                },
                onRelease = releaseStop,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                SirenaDpadHoldButton(
                    "\u2190",
                    padMovesEnabled,
                    onPress = {
                        val j = onDriveHoldStart("left")
                        onDriveResult(j)
                    },
                    onRelease = releaseStop,
                )
                SirenaDpadStop("STOP", {
                    scope.launch {
                        try {
                            val j = onDriveHoldStop()
                            onDriveResult(j)
                        } catch (_: Exception) {
                        }
                    }
                }, enabled = bridgeOn)
                SirenaDpadHoldButton(
                    "\u2192",
                    padMovesEnabled,
                    onPress = {
                        val j = onDriveHoldStart("right")
                        onDriveResult(j)
                    },
                    onRelease = releaseStop,
                )
            }
            SirenaDpadHoldButton(
                "\u2193",
                padMovesEnabled,
                onPress = {
                    val j = onDriveHoldStart("back")
                    onDriveResult(j)
                },
                onRelease = releaseStop,
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
