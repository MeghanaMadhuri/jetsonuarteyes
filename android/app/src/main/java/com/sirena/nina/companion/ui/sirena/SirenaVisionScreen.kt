package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.clickable
import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsDraggedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import com.sirena.nina.companion.ui.theme.SirenaSwitch
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.util.NinaLog
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/**
 * Layout aligned with desktop [sirena_ui.screens.vision_screen.VisionScreen]:
 * breadcrumb + camera pill, **~62/38** camera vs recognition rail.
 */
@Composable
fun SirenaVisionScreen(
    vm: CompanionViewModel,
    daemonUrl: String?,
    caps: JSONObject? = null,
    shellCompact: Boolean = false,
) {
    val scope = rememberCoroutineScope()
    val jetsonLink by vm.jetsonLink.collectAsStateWithLifecycle()
    val bearer by vm.bearerToken.collectAsStateWithLifecycle(initialValue = null)
    val root = daemonUrl?.trimEnd('/') ?: ""
    val visionOn = root.isNotBlank() && jetsonLink.isOnline

    var faceOn by remember { mutableStateOf(true) }
    var objectsOn by remember { mutableStateOf(true) }
    var enrollName by remember { mutableStateOf("") }
    /** Follow target sent to ``visionFollowStart`` (empty = largest face). */
    var followPickValue by remember { mutableStateOf("") }
    var followPickLabel by remember { mutableStateOf("Largest face (any)") }
    var followMenuOpen by remember { mutableStateOf(false) }
    var followFaceNames by remember { mutableStateOf<List<String>>(emptyList()) }
    var statusJson by remember { mutableStateOf<JSONObject?>(null) }
    var detectLine by remember { mutableStateOf("—") }
    var followLine by remember { mutableStateOf("—") }
    var err by remember { mutableStateOf<String?>(null) }
    var objectConfPct by remember { mutableStateOf(80) }
    val objectConfDrag = remember { MutableInteractionSource() }
    val objectConfDragging by objectConfDrag.collectIsDraggedAsState()
    var arucoMarkerId by remember { mutableStateOf("0") }
    var arucoLine by remember { mutableStateOf("ArUco: off") }
    val resOptions = listOf("1280x720", "640x480", "320x240")
    var resIndex by remember { mutableStateOf(1) }
    var streamGeneration by remember { mutableStateOf(0) }
    var resApplying by remember { mutableStateOf(false) }

    fun resolutionIndexFor(width: Int, height: Int): Int {
        val label = "${width}x$height"
        val exact = resOptions.indexOf(label)
        if (exact >= 0) return exact
        val area = width * height
        return resOptions.indices.minByOrNull { i ->
            val parts = resOptions[i].split("x")
            val w = parts.getOrNull(0)?.toIntOrNull() ?: 0
            val h = parts.getOrNull(1)?.toIntOrNull() ?: 0
            kotlin.math.abs(w * h - area)
        } ?: 1
    }

    fun applyResolutionChoice(index: Int) {
        if (!visionOn || index !in resOptions.indices) return
        scope.launch {
            resApplying = true
            err = null
            try {
                val label = resOptions[index]
                val r =
                    vm.postVisionOptionsSync(
                        face = null,
                        objects = null,
                        objectConfidence = null,
                        resolution = label,
                    )
                if (r != null && !r.optBoolean("ok", true)) {
                    err = r.optString("error").ifBlank { "Resolution not applied" }
                    return@launch
                }
                resIndex = index
                r?.optString("resolution")?.trim()?.let { applied ->
                    val idx = resOptions.indexOf(applied)
                    if (idx >= 0) resIndex = idx
                }
                vm.visionOpen()
                streamGeneration++
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                err = e.message ?: "Resolution failed"
            } finally {
                resApplying = false
            }
        }
    }

    LaunchedEffect(visionOn, caps?.optBoolean("vision_bridge_enabled")) {
        if (!visionOn) return@LaunchedEffect
        if (caps != null && !caps.optBoolean("vision_bridge_enabled")) return@LaunchedEffect
        delay(300)
        try {
            vm.visionOpen()
        } catch (_: Exception) {
        }
    }

    LaunchedEffect(visionOn, caps?.optBoolean("vision_bridge_enabled")) {
        if (!visionOn) return@LaunchedEffect
        while (isActive) {
            try {
                if (caps != null && !caps.optBoolean("vision_bridge_enabled")) {
                    delay(5000L)
                    continue
                }
                coroutineScope {
                    val stDef = async { vm.fetchVisionStatus() }
                    val detDef = async { vm.fetchVisionDetections() }
                    val fcDef = async { vm.fetchVisionFaces() }
                    val flDef = async { vm.fetchVisionFollowStatus() }
                    val st = stDef.await()
                    statusJson = st
                    if (!objectConfDragging && !resApplying) {
                        st?.optDouble("object_confidence")?.let { c ->
                            objectConfPct = (c * 100.0).toInt().coerceIn(50, 99)
                        }
                        st?.let { s ->
                            val w = s.optInt("capture_width", 0)
                            val h = s.optInt("capture_height", 0)
                            if (w > 0 && h > 0) {
                                resIndex = resolutionIndexFor(w, h)
                            } else {
                                s.optString("resolution").trim().takeIf { it.isNotEmpty() }?.let { label ->
                                    val idx = resOptions.indexOf(label)
                                    if (idx >= 0) resIndex = idx
                                }
                            }
                        }
                    }
                    val fc = fcDef.await()
                    detectLine = summarizeDetections(detDef.await())
                    followFaceNames = enrolledFaceNames(fc)
                    followLine = formatFollowStatus(flDef.await())
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                NinaLog.warn("SirenaVisionScreen", e.message ?: "poll")
            }
            // Tighter poll for autonomy-relevant vision JSON (MJPEG is continuous via Compose decoder).
            delay(320)
        }
    }

    LaunchedEffect(visionOn) {
        if (!visionOn) return@LaunchedEffect
        while (isActive) {
            try {
                val st = vm.fetchVisionArucoStatus()
                arucoLine = st?.optString("message")?.take(48) ?: "ArUco: off"
            } catch (_: Exception) {
            }
            delay(1000L)
        }
    }

    LaunchedEffect(followMenuOpen, visionOn, caps?.optBoolean("vision_bridge_enabled")) {
        if (!followMenuOpen || !visionOn) return@LaunchedEffect
        if (caps != null && !caps.optBoolean("vision_bridge_enabled")) return@LaunchedEffect
        try {
            followFaceNames = enrolledFaceNames(vm.fetchVisionFaces())
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            NinaLog.warn("SirenaVisionScreen", e.message ?: "faces_menu")
        }
    }

    val camOpen = statusJson?.optBoolean("camera_open") == true
    val camPillText =
        when {
            !visionOn -> "USB camera not connected"
            camOpen -> "USB camera connected"
            else -> statusJson?.optString("message")?.trim()?.take(40)?.ifBlank { "USB camera idle" }
                ?: "USB camera idle"
        }
    val camPillKind =
        when {
            !visionOn -> SirenaPillKind.Neutral
            camOpen -> SirenaPillKind.Ok
            else -> SirenaPillKind.Neutral
        }
    val fpsPillText =
        when {
            !visionOn -> "\u2014"
            statusJson?.has("fps") == true ->
                String.format(
                    java.util.Locale.US,
                    "%.1f fps",
                    statusJson!!.optDouble("fps"),
                )
            camOpen -> "Live"
            else -> "\u2014"
        }

    @Composable
    fun VisionCameraCard(
        cardModifier: Modifier,
        expandViewport: Boolean,
        viewportMin: Dp,
        fpsPillText: String,
    ) {
        SirenaCard(
            modifier = cardModifier,
            contentPadding = androidx.compose.foundation.layout.PaddingValues(10.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                SirenaCardTitle("Camera")
                Spacer(Modifier.weight(1f))
                SirenaStatusPill(fpsPillText, SirenaPillKind.Neutral)
            }
            val base =
                Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(SirenaDimens.cardRadiusSubtle))
                    .background(SirenaColors.cloud)
            Box(
                modifier =
                    base.then(
                        if (expandViewport) {
                            Modifier
                                .weight(1f)
                                .defaultMinSize(minHeight = viewportMin)
                        } else {
                            Modifier.heightIn(min = viewportMin, max = 400.dp)
                        },
                    ),
            ) {
                if (visionOn && camOpen) {
                    SirenaMjpegImage(
                        streamUrl = "$root/v1/vision/stream",
                        bearer = bearer,
                        modifier = Modifier.fillMaxSize(),
                        maxLongEdge = SirenaMjpegPreviewMaxLongEdge,
                        streamEnabled = true,
                        targetFps = SirenaMjpegDefaultTargetFps,
                        streamGeneration = streamGeneration,
                        idleMessage = "USB camera not streaming",
                    )
                } else if (visionOn) {
                    Text(
                        camPillText,
                        modifier = Modifier.align(Alignment.Center).padding(16.dp),
                        color = SirenaColors.muted,
                        fontSize = SirenaType.muted,
                    )
                } else {
                    Text(
                        "Connect to a robot to view the live feed.",
                        modifier = Modifier.align(Alignment.Center).padding(16.dp),
                        color = SirenaColors.muted,
                        fontSize = SirenaType.muted,
                    )
                }
            }
        }
    }

    @Composable
    fun VisionRecognitionRail(
        modifier: Modifier,
        compactActions: Boolean,
        splitRailLayout: Boolean,
    ) {
        val railMidScroll = rememberScrollState()
        val stackedScroll = rememberScrollState()

        fun pushVisionOptions() {
            scope.launch {
                err = null
                try {
                    val r =
                        vm.postVisionOptionsSync(
                            faceOn,
                            objectsOn,
                            objectConfPct / 100.0,
                        )
                    if (r != null && !r.optBoolean("ok", true)) {
                        err = r.optString("error").ifBlank { r.toString().take(120) }
                    }
                } catch (e: Exception) {
                    err = e.message
                }
            }
        }

        @Composable
        fun VisionCameraPlaceholders() {
            SirenaSectionLabel("Camera")
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SirenaMutedText("Resolution", maxLines = 1)
                Text(
                    resOptions[resIndex],
                    color = SirenaColors.text,
                    fontWeight = FontWeight.Medium,
                    modifier = Modifier.weight(1f),
                )
                SirenaSecondaryButton(
                    text = if (resApplying) "Applying…" else "Apply",
                    onClick = { applyResolutionChoice(resIndex) },
                    enabled = visionOn && !resApplying,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                resOptions.forEachIndexed { i, label ->
                    SirenaTogglePill(
                        text = label,
                        checked = resIndex == i,
                        onClick = { applyResolutionChoice(i) },
                        enabled = visionOn && !resApplying,
                    )
                }
            }
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                SirenaMutedText("Brightness", maxLines = 1)
                Slider(
                    value = 55f,
                    onValueChange = {},
                    enabled = false,
                    modifier = Modifier.weight(1f),
                    valueRange = 0f..100f,
                    colors =
                        SliderDefaults.colors(
                            thumbColor = SirenaColors.border,
                            activeTrackColor = SirenaColors.border,
                            inactiveTrackColor = SirenaColors.border,
                        ),
                )
                SirenaStatusPill("55%", SirenaPillKind.Neutral)
            }
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SirenaMutedText("Exposure", maxLines = 1)
                Text("Auto", color = SirenaColors.text, fontWeight = FontWeight.Medium, fontSize = SirenaType.muted)
            }
        }

        @Composable
        fun CoreSections() {
            SirenaSectionLabel("Recognition")
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    "Face recognition",
                    modifier = Modifier.weight(1f),
                    fontWeight = FontWeight.Medium,
                    color = SirenaColors.text,
                    fontSize = SirenaType.base,
                )
                SirenaSwitch(
                    checked = faceOn,
                    onCheckedChange = { v ->
                        faceOn = v
                        pushVisionOptions()
                    },
                    enabled = visionOn,
                )
            }
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    "Object detection",
                    modifier = Modifier.weight(1f),
                    fontWeight = FontWeight.Medium,
                    color = SirenaColors.text,
                    fontSize = SirenaType.base,
                )
                SirenaSwitch(
                    checked = objectsOn,
                    onCheckedChange = { v ->
                        objectsOn = v
                        pushVisionOptions()
                    },
                    enabled = visionOn,
                )
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = { scope.launch { err = vm.visionOpen() } },
                    enabled = visionOn,
                ) { Text("Open camera") }
                Button(
                    onClick = { scope.launch { err = vm.visionStop() } },
                    enabled = visionOn,
                ) { Text("Stop") }
            }

            SirenaSectionLabel("Person follow")
            SirenaMutedText(
                "Drives toward a standoff face size, holds when centred, and reverses " +
                    "if you move closer\u2014using the same closeness limit for hold and reverse.",
                maxLines = 6,
            )
            Box(Modifier.fillMaxWidth()) {
                OutlinedTextField(
                    value = followPickLabel,
                    onValueChange = {},
                    readOnly = true,
                    singleLine = true,
                    label = { Text("Follow target") },
                    trailingIcon = {
                        IconButton(
                            onClick = { followMenuOpen = !followMenuOpen },
                            enabled = visionOn,
                        ) {
                            Icon(
                                Icons.Filled.ArrowDropDown,
                                contentDescription = "Open follow target menu",
                            )
                        }
                    },
                    modifier =
                        Modifier
                            .fillMaxWidth()
                            .clickable(enabled = visionOn) { followMenuOpen = !followMenuOpen },
                    enabled = visionOn,
                )
                DropdownMenu(
                    expanded = followMenuOpen,
                    onDismissRequest = { followMenuOpen = false },
                ) {
                    DropdownMenuItem(
                        text = { Text("Largest face (any)") },
                        onClick = {
                            followPickLabel = "Largest face (any)"
                            followPickValue = ""
                            followMenuOpen = false
                        },
                    )
                    followFaceNames.forEach { n ->
                        DropdownMenuItem(
                            text = { Text(n) },
                            onClick = {
                                followPickLabel = n
                                followPickValue = n
                                followMenuOpen = false
                            },
                        )
                    }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SirenaPrimaryButton(
                    text = "Start follow",
                    onClick = {
                        scope.launch {
                            try {
                                vm.visionFollowStart(followPickValue.trim())
                            } catch (e: Exception) {
                                err = e.message
                            }
                        }
                    },
                    enabled = visionOn,
                    modifier = Modifier.height(34.dp).weight(1f),
                )
                SirenaSecondaryButton(
                    text = "Stop follow",
                    onClick = {
                        scope.launch {
                            try {
                                vm.visionFollowStop()
                            } catch (e: Exception) {
                                err = e.message
                            }
                        }
                    },
                    enabled = visionOn,
                    modifier = Modifier.height(34.dp).weight(1f),
                )
            }
            SirenaStatusPill(
                text = followLine.take(48).ifBlank { "Follow: off" },
                SirenaPillKind.Neutral,
            )

            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                SirenaMutedText("Object confidence", maxLines = 1)
                Slider(
                    value = objectConfPct.toFloat(),
                    onValueChange = { v -> objectConfPct = v.toInt().coerceIn(50, 99) },
                    onValueChangeFinished = { pushVisionOptions() },
                    valueRange = 50f..99f,
                    steps = 47,
                    enabled = visionOn,
                    modifier = Modifier.weight(1f),
                    interactionSource = objectConfDrag,
                    colors =
                        SliderDefaults.colors(
                            thumbColor = SirenaColors.red,
                            activeTrackColor = SirenaColors.red,
                        ),
                )
                SirenaStatusPill("$objectConfPct%", SirenaPillKind.Neutral)
            }

            SirenaSectionLabel("ArUco marker approach")
            SirenaMutedText(
                "Uses the live camera to approach the chosen marker ID at straight-bench speed.",
                maxLines = 4,
            )
            OutlinedTextField(
                value = arucoMarkerId,
                onValueChange = { arucoMarkerId = it.filter { c -> c.isDigit() }.take(3) },
                label = { Text("Marker ID") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                enabled = visionOn,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SirenaPrimaryButton(
                    text = "Start approach",
                    onClick = {
                        scope.launch {
                            val id = arucoMarkerId.toIntOrNull() ?: 0
                            val r = vm.postVisionArucoStart(id)
                            if (r?.optBoolean("ok") != true) {
                                err = r?.optString("error") ?: "ArUco start failed"
                            }
                        }
                    },
                    enabled = visionOn,
                    modifier = Modifier.weight(1f),
                )
                SirenaSecondaryButton(
                    text = "Stop approach",
                    onClick = {
                        scope.launch {
                            vm.postVisionArucoStop()
                        }
                    },
                    enabled = visionOn,
                    modifier = Modifier.weight(1f),
                )
            }
            SirenaStatusPill(arucoLine, SirenaPillKind.Neutral)

            OutlinedTextField(
                value = enrollName,
                onValueChange = { enrollName = it },
                label = { Text("Name") },
                supportingText = {
                    Text(
                        "Used by Train face",
                        fontSize = SirenaType.quickBlurb,
                        color = SirenaColors.muted,
                    )
                },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            SirenaSectionLabel("Detected")
            SirenaCard(
                kind = SirenaCardKind.Subtle,
                contentPadding = androidx.compose.foundation.layout.PaddingValues(8.dp),
            ) {
                Text(detectLine, fontSize = SirenaType.muted, color = SirenaColors.text)
            }

            VisionCameraPlaceholders()
        }

        @Composable
        fun ActionButtonRow(outer: Modifier) {
            if (compactActions) {
                Column(outer, verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    SirenaPrimaryButton(
                        text = "Train face",
                        onClick = {
                            scope.launch {
                                err = null
                                if (enrollName.isBlank()) {
                                    err = "Enter a name for Train face / enroll."
                                    return@launch
                                }
                                val (j, netErr) = vm.visionEnroll(enrollName.trim(), 8)
                                if (netErr != null) err = netErr
                                else if (j != null && !j.optBoolean("ok", true)) {
                                    err = j.optString("message").ifBlank { j.toString().take(120) }
                                } else if (j != null && j.optBoolean("ok", true)) {
                                    followFaceNames = enrolledFaceNames(vm.fetchVisionFaces())
                                }
                            }
                        },
                        enabled = visionOn,
                        modifier = Modifier.fillMaxWidth().height(34.dp),
                    )
                    SirenaSecondaryButton(
                        text = "Snapshot",
                        onClick = {
                            scope.launch {
                                err = null
                                val jpeg = vm.fetchVisionSnapshotJpeg()
                                if (jpeg == null || jpeg.isEmpty()) {
                                    err = "Snapshot failed"
                                }
                            }
                        },
                        enabled = visionOn,
                        modifier = Modifier.fillMaxWidth().height(34.dp),
                    )
                    SirenaPrimaryButton(
                        text = "Speak",
                        onClick = {
                            scope.launch {
                                try {
                                    vm.visionAnnounceObjects()
                                } catch (e: Exception) {
                                    err = e.message
                                }
                            }
                        },
                        enabled = visionOn && objectsOn,
                        modifier = Modifier.fillMaxWidth().height(34.dp),
                    )
                }
            } else {
                Row(outer, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    SirenaPrimaryButton(
                        text = "Train face",
                        onClick = {
                            scope.launch {
                                err = null
                                if (enrollName.isBlank()) {
                                    err = "Enter a name for Train face / enroll."
                                    return@launch
                                }
                                val (j, netErr) = vm.visionEnroll(enrollName.trim(), 8)
                                if (netErr != null) err = netErr
                                else if (j != null && !j.optBoolean("ok", true)) {
                                    err = j.optString("message").ifBlank { j.toString().take(120) }
                                } else if (j != null && j.optBoolean("ok", true)) {
                                    followFaceNames = enrolledFaceNames(vm.fetchVisionFaces())
                                }
                            }
                        },
                        enabled = visionOn,
                        modifier = Modifier.height(34.dp).weight(1f),
                    )
                    SirenaSecondaryButton(
                        text = "Snapshot",
                        onClick = {
                            scope.launch {
                                err = null
                                val jpeg = vm.fetchVisionSnapshotJpeg()
                                if (jpeg == null || jpeg.isEmpty()) {
                                    err = "Snapshot failed"
                                }
                            }
                        },
                        enabled = visionOn,
                        modifier = Modifier.height(34.dp).weight(1f),
                    )
                    SirenaPrimaryButton(
                        text = "Speak",
                        onClick = {
                            scope.launch {
                                try {
                                    vm.visionAnnounceObjects()
                                } catch (e: Exception) {
                                    err = e.message
                                }
                            }
                        },
                        enabled = visionOn && objectsOn,
                        modifier = Modifier.height(34.dp).weight(1f),
                    )
                }
            }
        }

        if (splitRailLayout) {
            Column(
                modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Column(
                    Modifier
                        .weight(1f)
                        .fillMaxWidth()
                        .verticalScroll(railMidScroll),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    CoreSections()
                }
                ActionButtonRow(Modifier.fillMaxWidth())
            }
        } else {
            Column(
                modifier
                    .fillMaxWidth()
                    .verticalScroll(stackedScroll),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                CoreSections()
                ActionButtonRow(Modifier.fillMaxWidth())
            }
        }
    }
    SirenaAdaptiveContainer(Modifier.padding(10.dp)) { ctx ->
        Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            SirenaAdaptiveHeaderPills(
                ctx = ctx,
                breadcrumb = { SirenaBreadcrumbLine(listOf("Nina", "Vision")) },
                pills = { SirenaStatusPill(camPillText, camPillKind) },
            )
            SirenaJetsonFeatureGateCallout(
                linkOnline = jetsonLink.isOnline,
                caps = caps,
                flagKey = "vision_bridge_enabled",
                title = "Vision HTTP bridge is off",
                envLine = "On the Jetson: export NINA_LINK_ENABLE_VISION_BRIDGE=1 (and install vision deps), then restart nina-link.",
            )
            err?.let { msg ->
                SirenaCard(
                    modifier = Modifier.fillMaxWidth(),
                    kind = SirenaCardKind.Error,
                ) {
                    Text(msg, color = SirenaColors.pillErrorFg, fontSize = SirenaType.muted)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        SirenaSecondaryButton(text = "Retry", onClick = { err = null })
                    }
                }
            }
            val vMin = ctx.visionCameraViewportMin
            val compactButtons = ctx.maxWidth < 400.dp
            if (ctx.visionSplit) {
                Row(
                    Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    VisionCameraCard(
                        cardModifier = Modifier.weight(0.62f).fillMaxHeight(),
                        expandViewport = true,
                        viewportMin = vMin,
                        fpsPillText = fpsPillText,
                    )
                    VisionRecognitionRail(
                        modifier =
                            Modifier
                                .weight(0.38f)
                                .fillMaxHeight(),
                        compactActions = compactButtons,
                        splitRailLayout = true,
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
                    VisionCameraCard(
                        cardModifier = Modifier.fillMaxWidth(),
                        expandViewport = false,
                        viewportMin = vMin,
                        fpsPillText = fpsPillText,
                    )
                    VisionRecognitionRail(
                        modifier = Modifier.fillMaxWidth(),
                        compactActions = compactButtons,
                        splitRailLayout = false,
                    )
                }
            }
        }
    }
}

private fun enrolledFaceNames(j: JSONObject?): List<String> {
    if (j == null) return emptyList()
    if (j.has("ok") && !j.optBoolean("ok", true)) return emptyList()
    val arr = j.optJSONArray("faces") ?: j.optJSONArray("names") ?: return emptyList()
    val out = mutableListOf<String>()
    for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i)
        if (o != null) {
            o.optString("name").trim().takeIf { it.isNotEmpty() }?.let { out.add(it) }
        } else {
            val s = arr.optString(i).trim()
            if (s.isNotEmpty()) out.add(s)
        }
    }
    return out.distinct().sorted()
}

private fun formatFollowStatus(j: JSONObject?): String {
    if (j == null) return "Follow: unavailable"
    if (j.has("ok") && !j.optBoolean("ok", true)) {
        val err = j.optString("error").trim().takeIf { it.isNotEmpty() }
        return err ?: "Follow: error"
    }
    val active = j.optBoolean("active", false)
    val msg = j.optString("message").trim()
    return when {
        active && msg.isNotEmpty() -> msg.take(96)
        active -> "Following (active)"
        msg.isNotEmpty() -> msg.take(96)
        else -> "Follow idle"
    }
}

private fun summarizeDetections(j: JSONObject?): String {
    if (j == null) return "—"
    val emptyCopy = "No objects detected."

    fun fromLabelStrings(arr: JSONArray?): String {
        if (arr == null || arr.length() == 0) return emptyCopy
        val parts = mutableListOf<String>()
        for (i in 0 until minOf(arr.length(), 24)) {
            val s = arr.optString(i).trim()
            if (s.isNotEmpty()) parts.add(s)
        }
        return parts.distinct().joinToString(", ").ifBlank { emptyCopy }
    }

    val labels = j.optJSONArray("labels")
    if (labels != null) {
        return fromLabelStrings(labels)
    }
    val dets = j.optJSONArray("detections")
    if (dets != null) {
        if (dets.length() == 0) return emptyCopy
        val counts = linkedMapOf<String, Int>()
        for (i in 0 until minOf(dets.length(), 24)) {
            val o = dets.optJSONObject(i) ?: continue
            val label =
                o.optString("label").trim().ifBlank {
                    o.optString("class_name").trim().ifBlank {
                        o.optString("identity").trim().ifBlank { o.optString("kind").trim() }
                    }
                }
            if (label.isNotEmpty()) {
                counts[label] = (counts[label] ?: 0) + 1
            }
        }
        if (counts.isEmpty()) return emptyCopy
        return counts.entries.joinToString(", ") { (name, n) ->
            if (n > 1) "$name ($n)" else name
        }
    }
    return emptyCopy
}
