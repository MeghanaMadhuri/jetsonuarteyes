package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sirena.nina.companion.ActionRowUi
import com.sirena.nina.companion.CompanionViewModel
import java.util.Locale
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

private enum class ActionsSubtab { Playback, Record, Audio }

private fun defaultSpeechForAction(name: String): String =
    name
        .split("_")
        .joinToString(" ") { part ->
            part.replaceFirstChar { c ->
                if (c.isLowerCase()) c.titlecase(Locale.getDefault()) else c.toString()
            }
        }

private fun parseDoubleOr(raw: String, default: Double): Double =
    raw.trim().toDoubleOrNull() ?: default

private fun formatMotionMeta(row: ActionRowUi): String {
    val d = row.durationSec
    val c = row.frameCount
    return when {
        d != null && c != null ->
            String.format(Locale.US, "%.1fs • %d frames", d, c)
        c != null -> "$c frames"
        d != null -> String.format(Locale.US, "%.1fs", d)
        else -> "—"
    }
}

private fun formatAudioMeta(row: ActionRowUi): String {
    val rel = row.audio?.trim()
    val off = row.audioOffsetSec
    if (rel.isNullOrEmpty()) return "Audio: none"
    val fileName = rel.substringAfterLast('/').ifBlank { rel }
    val suffix =
        if (off != null && off > 0.0) {
            String.format(Locale.US, " • +%.2fs", off)
        } else {
            ""
        }
    return "Audio: $fileName$suffix"
}

/** Status line for remote record polling (matches kiosk ``ActionsScreen``). */
private fun formatRemoteRecordStatus(st: JSONObject?): String {
    if (st == null) return "—"
    if (!st.optBoolean("running")) return "Idle"
    return when (st.optString("phase", "recording")) {
        "preparing" -> "Preparing to record…"
        "countdown" -> {
            val n = st.optInt("countdown_remaining", 0)
            if (n > 0) {
                "Torque released — starting in $n…"
            } else {
                "Torque released — starting…"
            }
        }
        "recording" -> {
            val cap = st.optInt("captured", 0)
            val tot = st.optInt("target", 0)
            val el = st.optDouble("elapsed_sec", 0.0)
            String.format(
                Locale.US,
                "● RECORDING | Torque released | Frames %d/%d • %.1fs",
                cap,
                tot,
                el,
            )
        }
        "saving" -> "Saving recording…"
        else -> "Recording in progress"
    }
}

/**
 * Actions: Playback / Record / Audio — same sub-tabs as [sirena_ui.screens.actions_screen.ActionsScreen],
 * backed by manifest + record HTTP on the link daemon. Content uses the full content area.
 */
@Composable
fun SirenaActionsScreen(
    vm: CompanionViewModel,
    initialSubtab: String,
    caps: JSONObject? = null,
    /** When true, tab bodies do not use vertical drag scroll (phone shell). */
    shellCompact: Boolean = false,
    /** When set (e.g. from Playback **Audio**), switches to the Audio tab and seeds the action picker. */
    prefillAudioAction: String? = null,
    onPrefillAudioConsumed: () -> Unit = {},
    /** Playback **Audio** button: parent may update nav + [prefillAudioAction]. */
    onPlaybackOpenAudioEditor: (String) -> Unit = {},
) {
    val scope = rememberCoroutineScope()
    val actions by vm.manifestActions.collectAsStateWithLifecycle()
    val manifestErr by vm.manifestActionsError.collectAsStateWithLifecycle()
    val link by vm.jetsonLink.collectAsStateWithLifecycle()
    val playbackHint by vm.actionPlaybackStatus.collectAsStateWithLifecycle()
    val protectedNeutral =
        caps?.optString("neutral_action_name")?.trim()?.takeIf { it.isNotEmpty() } ?: "neutral"

    var subtab by remember {
        mutableStateOf(
            when (initialSubtab.lowercase()) {
                "record" -> ActionsSubtab.Record
                "audio" -> ActionsSubtab.Audio
                else -> ActionsSubtab.Playback
            },
        )
    }

    var recordName by remember { mutableStateOf("motion") }
    var recordSeconds by remember { mutableStateOf("5.0") }
    var recordHz by remember { mutableStateOf("20.0") }
    var recordCountdown by remember { mutableStateOf("3.0") }
    var recordRegister by remember { mutableStateOf(true) }
    var recordHoldAfter by remember { mutableStateOf(false) }
    var recordErr by remember { mutableStateOf<String?>(null) }
    var recordLine by remember { mutableStateOf("—") }
    var recordingActive by remember { mutableStateOf(false) }
    var recordPhase by remember { mutableStateOf("") }
    var recordCountdownRemaining by remember { mutableIntStateOf(0) }
    var recordCaptured by remember { mutableIntStateOf(0) }
    var recordTarget by remember { mutableIntStateOf(0) }
    var recordElapsedSec by remember { mutableStateOf(0.0) }
    var wasRecording by remember { mutableStateOf(false) }

    var selectedActionName by remember { mutableStateOf("") }
    var audioSpeechText by remember { mutableStateOf("") }
    var voicePresetIndex by remember { mutableIntStateOf(0) }
    var audioOffsetStr by remember { mutableStateOf("0.0") }
    var audioActionMenuExpanded by remember { mutableStateOf(false) }
    var voiceMenuExpanded by remember { mutableStateOf(false) }
    var audioErr by remember { mutableStateOf<String?>(null) }
    var audioLast by remember { mutableStateOf("—") }
    var pendingDelete by remember { mutableStateOf<String?>(null) }
    var pendingRemoveAudio by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(initialSubtab, prefillAudioAction) {
        subtab =
            when (initialSubtab.lowercase()) {
                "record" -> ActionsSubtab.Record
                "audio" -> ActionsSubtab.Audio
                else -> ActionsSubtab.Playback
            }
        val p = prefillAudioAction?.trim()?.takeIf { it.isNotEmpty() }
        if (p != null) {
            subtab = ActionsSubtab.Audio
            selectedActionName = p
            audioSpeechText = defaultSpeechForAction(p)
            voicePresetIndex = 0
            audioOffsetStr = "0.0"
            onPrefillAudioConsumed()
        }
    }

    LaunchedEffect(Unit) {
        vm.refreshManifestActions()
    }

    LaunchedEffect(subtab, link.isOnline) {
        if (subtab != ActionsSubtab.Record || !link.isOnline) {
            recordingActive = false
            recordPhase = ""
            return@LaunchedEffect
        }
        while (isActive) {
            try {
                val st = vm.fetchRecordStatus()
                val running = st?.optBoolean("running") == true
                recordingActive = running
                if (running && st != null) {
                    recordPhase = st.optString("phase", "recording")
                    recordCountdownRemaining = st.optInt("countdown_remaining", 0)
                    recordCaptured = st.optInt("captured", 0)
                    recordTarget = st.optInt("target", 0)
                    recordElapsedSec = st.optDouble("elapsed_sec", 0.0)
                } else if (!running) {
                    recordPhase = ""
                    recordCountdownRemaining = 0
                    recordCaptured = 0
                    recordTarget = 0
                    recordElapsedSec = 0.0
                }
                recordLine = formatRemoteRecordStatus(st)
                if (wasRecording && !running) {
                    vm.refreshManifestActions()
                }
                wasRecording = running
            } catch (_: Exception) {
            }
            delay(if (recordingActive) 280L else 800L)
        }
    }

    val buttonTall = Modifier.defaultMinSize(minHeight = 48.dp)

    @Composable
    fun TabStrip() {
        SirenaSegmentedTabs(
            tabs = listOf("Playback", "Record", "Audio"),
            selectedIndex =
                when (subtab) {
                    ActionsSubtab.Playback -> 0
                    ActionsSubtab.Record -> 1
                    ActionsSubtab.Audio -> 2
                },
            onSelect = { i ->
                if (!recordingActive) {
                    subtab =
                        when (i) {
                            0 -> ActionsSubtab.Playback
                            1 -> ActionsSubtab.Record
                            else -> ActionsSubtab.Audio
                        }
                }
            },
            modifier = Modifier.fillMaxWidth(),
        )
    }

    @Composable
    fun SubtabBody() {
        when (subtab) {
            ActionsSubtab.Playback -> {
                BoxWithConstraints(
                    Modifier
                        .fillMaxSize()
                        .padding(vertical = 4.dp),
                ) {
                    val gridCols =
                        when {
                            maxWidth >= 600.dp -> 3
                            maxWidth >= 320.dp -> 2
                            else -> 1
                        }
                    val estCellWidth = (maxWidth.value / gridCols).dp
                    val stackButtons = estCellWidth < 168.dp

                    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        SirenaCardTitle("Playback actions")
                        LazyVerticalGrid(
                            columns = GridCells.Fixed(gridCols),
                            modifier = Modifier.weight(1f).fillMaxWidth(),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            items(actions, key = { it.name }) { row: ActionRowUi ->
                                val cardPad = PaddingValues(horizontal = 10.dp, vertical = 8.dp)
                                SirenaCard(contentPadding = cardPad) {
                                    Row(
                                        Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.SpaceBetween,
                                        verticalAlignment = Alignment.Top,
                                    ) {
                                        Column(Modifier.weight(1f)) {
                                            Text(
                                                row.name,
                                                fontWeight = FontWeight.SemiBold,
                                                color = SirenaColors.text,
                                            )
                                            row.file?.let { SirenaMutedText(it, maxLines = 1) }
                                            SirenaMutedText(formatMotionMeta(row), maxLines = 1)
                                            SirenaMutedText(formatAudioMeta(row), maxLines = 2)
                                        }
                                        IconButton(
                                            onClick = {
                                                if (row.name != protectedNeutral) {
                                                    pendingDelete = row.name
                                                }
                                            },
                                            enabled = row.name != protectedNeutral,
                                            modifier = Modifier.size(40.dp),
                                        ) {
                                            Icon(
                                                Icons.Outlined.Delete,
                                                contentDescription = "Delete action",
                                                tint =
                                                    if (row.name == protectedNeutral) {
                                                        SirenaColors.disabledText
                                                    } else {
                                                        SirenaColors.muted
                                                    },
                                            )
                                        }
                                    }
                                    Spacer(Modifier.height(8.dp))
                                    if (stackButtons) {
                                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                            SirenaPrimaryButton(
                                                text = "Play",
                                                onClick = { vm.playManifestAction(row.name) },
                                                modifier = buttonTall.fillMaxWidth(),
                                            )
                                            SirenaSecondaryButton(
                                                text = "Audio",
                                                onClick = { onPlaybackOpenAudioEditor(row.name) },
                                                modifier = buttonTall.fillMaxWidth(),
                                            )
                                        }
                                    } else {
                                        Row(
                                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                                            verticalAlignment = Alignment.CenterVertically,
                                        ) {
                                            SirenaPrimaryButton(
                                                text = "Play",
                                                onClick = { vm.playManifestAction(row.name) },
                                                modifier = buttonTall,
                                            )
                                            SirenaSecondaryButton(
                                                text = "Audio",
                                                onClick = { onPlaybackOpenAudioEditor(row.name) },
                                                modifier = buttonTall,
                                            )
                                        }
                                    }
                                }
                            }
                        }
                        TextButton(
                            onClick = { vm.refreshManifestActions() },
                            modifier = Modifier.align(Alignment.End),
                        ) {
                            Text("Refresh from manifest", color = SirenaColors.muted)
                        }
                    }
                }
            }

            ActionsSubtab.Record -> {
                Column(
                    Modifier
                        .fillMaxSize()
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    SirenaMutedText(
                        "Remote capture uses the same HTTP as the Jetson tablet.",
                        maxLines = 3,
                    )
                    OutlinedTextField(
                        value = recordName,
                        onValueChange = { recordName = it },
                        label = { Text("Motion name") },
                        singleLine = true,
                        enabled = !recordingActive,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = recordSeconds,
                        onValueChange = { recordSeconds = it },
                        label = { Text("Duration (s)") },
                        singleLine = true,
                        enabled = !recordingActive,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = recordHz,
                        onValueChange = { recordHz = it },
                        label = { Text("Sample rate (Hz)") },
                        singleLine = true,
                        enabled = !recordingActive,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = recordCountdown,
                        onValueChange = { recordCountdown = it },
                        label = { Text("Countdown (s)") },
                        singleLine = true,
                        enabled = !recordingActive,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Checkbox(
                            checked = recordRegister,
                            onCheckedChange = { recordRegister = it },
                            enabled = !recordingActive,
                        )
                        Text("Register in manifest", color = SirenaColors.text)
                    }
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Checkbox(
                            checked = recordHoldAfter,
                            onCheckedChange = { recordHoldAfter = it },
                            enabled = !recordingActive,
                        )
                        Text("Hold after capture", color = SirenaColors.text)
                    }
                    if (recordingActive) {
                        when (recordPhase) {
                            "countdown" ->
                                if (recordCountdownRemaining > 0) {
                                    Surface(
                                        modifier = Modifier.fillMaxWidth(),
                                        shape = RoundedCornerShape(12.dp),
                                        color = SirenaColors.calloutBg,
                                    ) {
                                        Column(
                                            Modifier
                                                .fillMaxWidth()
                                                .padding(vertical = 20.dp),
                                            horizontalAlignment = Alignment.CenterHorizontally,
                                        ) {
                                            Text(
                                                recordCountdownRemaining.toString(),
                                                fontSize = 56.sp,
                                                fontWeight = FontWeight.Bold,
                                                color = SirenaColors.text,
                                                textAlign = TextAlign.Center,
                                            )
                                            Text(
                                                "Torque released — pose the robot",
                                                fontSize = SirenaType.muted,
                                                color = SirenaColors.muted,
                                                textAlign = TextAlign.Center,
                                            )
                                        }
                                    }
                                } else {
                                    LinearProgressIndicator(Modifier.fillMaxWidth())
                                }
                            "recording" -> {
                                val frac =
                                    if (recordTarget > 0) {
                                        recordCaptured.toFloat() / recordTarget.toFloat()
                                    } else {
                                        0f
                                    }
                                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                    LinearProgressIndicator(
                                        progress = { frac.coerceIn(0f, 1f) },
                                        modifier =
                                            Modifier
                                                .fillMaxWidth()
                                                .height(8.dp),
                                    )
                                    Text(
                                        String.format(
                                            Locale.US,
                                            "Frames %d / %d • %.1f s elapsed",
                                            recordCaptured,
                                            recordTarget.coerceAtLeast(1),
                                            recordElapsedSec,
                                        ),
                                        fontSize = SirenaType.muted,
                                        color = SirenaColors.muted,
                                    )
                                }
                            }
                            else ->
                                LinearProgressIndicator(Modifier.fillMaxWidth())
                        }
                    }
                    recordErr?.let {
                        SirenaCard(kind = SirenaCardKind.Error) {
                            Text(it, color = SirenaColors.pillErrorFg, fontSize = SirenaType.muted)
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        SirenaPrimaryButton(
                            text = "Start recording",
                            enabled = !recordingActive,
                            onClick = {
                                scope.launch {
                                    recordErr = null
                                    val err =
                                        vm.startRemoteRecord(
                                            name = recordName.trim().ifBlank { "motion" },
                                            seconds = parseDoubleOr(recordSeconds, 5.0),
                                            hz = parseDoubleOr(recordHz, 20.0),
                                            countdown = parseDoubleOr(recordCountdown, 3.0),
                                            holdAfter = recordHoldAfter,
                                            register = recordRegister,
                                        )
                                    recordErr = err
                                    if (err == null) {
                                        recordingActive = true
                                        recordPhase = "preparing"
                                        recordLine = "Preparing to record…"
                                    }
                                }
                            },
                            modifier = buttonTall,
                        )
                        SirenaSecondaryButton(
                            text = "Stop recording",
                            enabled = recordingActive,
                            onClick = {
                                scope.launch {
                                    recordErr = vm.stopRemoteRecord()
                                    recordLine = "Stopping recording…"
                                }
                            },
                            modifier = buttonTall,
                        )
                    }
                    SirenaCardTitle("Record status")
                    Text(
                        recordLine,
                        fontSize = if (recordingActive) SirenaType.base else SirenaType.muted,
                        fontWeight = if (recordingActive) FontWeight.SemiBold else FontWeight.Normal,
                        color = if (recordingActive) SirenaColors.text else SirenaColors.muted,
                    )
                }
            }

            ActionsSubtab.Audio -> {
                Column(
                    Modifier
                        .fillMaxSize()
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    SirenaMutedText(
                        "Generate spoken clips with gTTS and attach them to manifest actions.",
                        maxLines = 3,
                    )
                    Box {
                        SirenaSecondaryButton(
                            text =
                                if (selectedActionName.isBlank()) {
                                    "Select action ▾"
                                } else {
                                    "$selectedActionName ▾"
                                },
                            onClick = { audioActionMenuExpanded = true },
                            modifier = Modifier.fillMaxWidth(),
                        )
                        DropdownMenu(
                            expanded = audioActionMenuExpanded,
                            onDismissRequest = { audioActionMenuExpanded = false },
                        ) {
                            actions.forEach { row ->
                                DropdownMenuItem(
                                    text = { Text(row.name) },
                                    onClick = {
                                        selectedActionName = row.name
                                        audioSpeechText = defaultSpeechForAction(row.name)
                                        audioActionMenuExpanded = false
                                    },
                                )
                            }
                        }
                    }
                    OutlinedTextField(
                        value = audioSpeechText,
                        onValueChange = { audioSpeechText = it },
                        label = { Text("Text to speak") },
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Box {
                        val preset = SirenaVoicePresets[voicePresetIndex.coerceIn(0, SirenaVoicePresets.lastIndex)]
                        SirenaSecondaryButton(
                            text = "${preset.label} ▾",
                            onClick = { voiceMenuExpanded = true },
                            modifier = Modifier.fillMaxWidth(),
                        )
                        DropdownMenu(
                            expanded = voiceMenuExpanded,
                            onDismissRequest = { voiceMenuExpanded = false },
                        ) {
                            SirenaVoicePresets.forEachIndexed { idx, p ->
                                DropdownMenuItem(
                                    text = { Text(p.label) },
                                    onClick = {
                                        voicePresetIndex = idx
                                        voiceMenuExpanded = false
                                    },
                                )
                            }
                        }
                    }
                    OutlinedTextField(
                        value = audioOffsetStr,
                        onValueChange = { audioOffsetStr = it },
                        label = { Text("Audio offset (s)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    SirenaMutedText(
                        "Preview plays the motion’s attached clip on the robot speakers (link must be online).",
                        maxLines = 3,
                    )
                    audioErr?.let {
                        SirenaCard(kind = SirenaCardKind.Error) {
                            Text(it, color = SirenaColors.pillErrorFg, fontSize = SirenaType.muted)
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        val selectedRow = actions.find { it.name == selectedActionName.trim() }
                        val hasAudio = selectedRow?.audio?.trim()?.isNotEmpty() == true
                        SirenaSecondaryButton(
                            text = "Preview",
                            onClick = {
                                scope.launch {
                                    audioErr = null
                                    val act = selectedActionName.trim()
                                    if (act.isEmpty()) {
                                        audioErr = "Select an action."
                                        return@launch
                                    }
                                    audioErr = vm.postActionAudioPreview(act)
                                    if (audioErr == null) {
                                        audioLast = "Playing preview on the robot…"
                                    }
                                }
                            },
                            enabled = selectedActionName.isNotBlank() && hasAudio && link.isOnline,
                            modifier = buttonTall,
                        )
                        SirenaPrimaryButton(
                            text = "Generate & save",
                            onClick = {
                                scope.launch {
                                    audioErr = null
                                    val act = selectedActionName.trim()
                                    if (act.isEmpty()) {
                                        audioErr = "Select an action."
                                        return@launch
                                    }
                                    val preset =
                                        SirenaVoicePresets[voicePresetIndex.coerceIn(0, SirenaVoicePresets.lastIndex)]
                                    val off = parseDoubleOr(audioOffsetStr, 0.0)
                                    audioErr =
                                        vm.postActionAudioGenerate(
                                            action = act,
                                            text = audioSpeechText,
                                            lang = preset.lang,
                                            tld = preset.tld,
                                            audioOffsetSec = off,
                                            slow = preset.slow,
                                        )
                                    if (audioErr == null) {
                                        audioLast = "Audio saved for this action."
                                        vm.refreshManifestActions()
                                    }
                                }
                            },
                            enabled = selectedActionName.isNotBlank(),
                            modifier = buttonTall,
                        )
                        SirenaSecondaryButton(
                            text = "Save offset",
                            onClick = {
                                scope.launch {
                                    audioErr = null
                                    val act = selectedActionName.trim()
                                    if (act.isEmpty()) {
                                        audioErr = "Select an action."
                                        return@launch
                                    }
                                    val off = parseDoubleOr(audioOffsetStr, 0.0)
                                    audioErr = vm.postActionAudioOffset(act, off)
                                    if (audioErr == null) {
                                        audioLast = "Offset updated."
                                        vm.refreshManifestActions()
                                    }
                                }
                            },
                            enabled = selectedActionName.isNotBlank(),
                            modifier = buttonTall,
                        )
                        SirenaSecondaryButton(
                            text = "Remove",
                            onClick = {
                                val act = selectedActionName.trim()
                                if (act.isEmpty()) {
                                    audioErr = "Select an action."
                                } else {
                                    pendingRemoveAudio = act
                                }
                            },
                            enabled = selectedActionName.isNotBlank(),
                            modifier = buttonTall,
                        )
                    }
                    SirenaCardTitle("Last result")
                    Text(audioLast, fontSize = SirenaType.muted, color = SirenaColors.text)
                }
            }
        }
    }

    Box(Modifier.fillMaxSize()) {
        pendingDelete?.let { name ->
            SirenaConfirmDialog(
                onDismiss = { pendingDelete = null },
                message =
                    "Delete \"$name\" from the manifest on the robot? " +
                        "Recording files are not deleted automatically.",
                confirmText = "Delete",
                dangerous = true,
                onConfirm = {
                    val action = name
                    pendingDelete = null
                    scope.launch {
                        if (action != protectedNeutral) {
                            vm.deleteManifestAction(
                                action,
                                deleteRecording = true,
                                deleteAudio = false,
                            )
                            vm.refreshManifestActions()
                        }
                    }
                },
            )
        }

        pendingRemoveAudio?.let { act ->
            SirenaConfirmDialog(
                onDismiss = { pendingRemoveAudio = null },
                message = "Remove audio from action \"$act\" on the robot?",
                confirmText = "Remove",
                dangerous = true,
                onConfirm = {
                    val action = act
                    pendingRemoveAudio = null
                    scope.launch {
                        audioErr = null
                        audioErr = vm.postActionAudioClear(action)
                        if (audioErr == null) {
                            audioLast = "Audio removed from this action."
                            vm.refreshManifestActions()
                        }
                    }
                },
            )
        }

        Column(
        Modifier
            .fillMaxSize()
            .padding(horizontal = 10.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SirenaBreadcrumbLine(listOf("Nina", "Actions"))
            Spacer(Modifier.weight(1f))
            SirenaStatusPill(
                text =
                    run {
                        val ph = playbackHint
                        when {
                            !link.isOnline -> "Offline"
                            !ph.isNullOrBlank() ->
                                ph.trim().take(28).ifBlank { "Playing…" }
                            manifestErr != null -> "Manifest error"
                            else -> "Bus: ready"
                        }
                    },
                kind =
                    run {
                        val ph = playbackHint
                        when {
                            !link.isOnline -> SirenaPillKind.Neutral
                            manifestErr != null -> SirenaPillKind.Error
                            !ph.isNullOrBlank() -> SirenaPillKind.Warn
                            else -> SirenaPillKind.Ok
                        }
                    },
            )
        }

        if (!link.isOnline) {
            SirenaCard(kind = SirenaCardKind.Callout) {
                Text("Link daemon unreachable", fontWeight = FontWeight.SemiBold, color = SirenaColors.text)
                SirenaMutedText(
                    link.lastError ?: "Check Wi‑Fi, open Find robot, or set the daemon URL in Settings.",
                    maxLines = 3,
                )
            }
        } else {
            SirenaActionsJetsonGateCallouts(linkOnline = true, caps = caps)
        }

        manifestErr?.let {
            SirenaCard(kind = SirenaCardKind.Error) {
                Text(
                    "Could not load actions from the robot.",
                    color = SirenaColors.pillErrorFg,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = SirenaType.base,
                )
                SirenaMutedText(
                    "Check the link, then use Refresh from manifest on the Playback tab.",
                    maxLines = 2,
                )
            }
        }

        Column(
            Modifier
                .weight(1f)
                .fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            TabStrip()
            Box(Modifier.weight(1f).fillMaxWidth()) {
                SubtabBody()
            }
        }
    }
    }
}
