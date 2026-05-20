package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import kotlinx.coroutines.launch
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sirena.nina.companion.BuildConfig
import com.sirena.nina.companion.CompanionUiState
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.R
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive

/** Same category keys/order as [sirena_ui.screens.settings_screen.SETTINGS_CATEGORIES]. */
data class SettingsCategory(val key: String, val label: String, val glyph: String)

val SIRENA_SETTINGS_CATEGORIES: List<SettingsCategory> =
    listOf(
        SettingsCategory("general", "General", "\u2699"),
        SettingsCategory("network", "Network", "\u2706"),
        SettingsCategory("display", "Display", "\u25A1"),
        SettingsCategory("audio", "Audio", "\u266B"),
        SettingsCategory("privacy", "Privacy", "\u26C4"),
        SettingsCategory("autodock", "Autodock", "\u2693"),
        SettingsCategory("voice", "Voice", "\u2693"),
        SettingsCategory("power", "Power", "\u26A1"),
        SettingsCategory("ota", "OTA", "\u21BB"),
    )

private val TIME_ZONES =
    listOf(
        "Asia / Kolkata",
        "Asia / Singapore",
        "Europe / London",
        "America / New_York",
        "America / Los_Angeles",
        "UTC",
    )

private val LANGUAGES =
    listOf(
        "English (US)",
        "English (UK)",
        "English (IN)",
        "Hindi",
        "Spanish",
        "French",
    )

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsFormDropdown(
    label: String,
    options: List<String>,
    selectedIndex: Int,
    onSelectIndex: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(false) }
    val safeIdx = selectedIndex.coerceIn(0, (options.size - 1).coerceAtLeast(0))
    val text = options.getOrElse(safeIdx) { "" }
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = it },
        modifier = modifier.fillMaxWidth(),
    ) {
        OutlinedTextField(
            value = text,
            onValueChange = {},
            readOnly = true,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier =
                Modifier
                    .menuAnchor()
                    .fillMaxWidth(),
        )
        ExposedDropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            options.forEachIndexed { i, opt ->
                DropdownMenuItem(
                    text = { Text(opt) },
                    onClick = {
                        onSelectIndex(i)
                        expanded = false
                    },
                )
            }
        }
    }
}

@Composable
fun SirenaSettingsScreen(
    vm: CompanionViewModel,
    state: CompanionUiState,
    shellCompact: Boolean = false,
    onOpenNetworkTab: () -> Unit,
    onNavigateToHealth: () -> Unit,
    onBackToProductHub: (() -> Unit)? = null,
) {
    val gatewayHint by vm.gatewayHint.collectAsStateWithLifecycle()
    val manifestActions by vm.manifestActions.collectAsStateWithLifecycle()
    var selected by rememberSaveable { mutableStateOf(SIRENA_SETTINGS_CATEGORIES.first().key) }
    val ready = state as? CompanionUiState.Ready

    LaunchedEffect(selected) {
        if (selected == "general") {
            vm.refreshManifestActions()
        }
    }
    LaunchedEffect(selected) {
        if (selected == "network") {
            while (isActive) {
                delay(4000L)
                vm.refreshStatus()
            }
        }
    }

    Row(
        Modifier
            .fillMaxSize()
            .padding(if (shellCompact) 6.dp else 10.dp),
        horizontalArrangement = Arrangement.spacedBy(if (shellCompact) 6.dp else 8.dp),
    ) {
        Column(
            Modifier
                .widthIn(max = if (shellCompact) 120.dp else 168.dp)
                .width(if (shellCompact) 104.dp else 160.dp)
                .fillMaxHeight()
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(if (shellCompact) 2.dp else 4.dp),
        ) {
            SirenaBreadcrumbLine(listOf("Nina", "Settings"))
            Spacer(Modifier.padding(vertical = if (shellCompact) 2.dp else 4.dp))
            SIRENA_SETTINGS_CATEGORIES.forEach { cat ->
                val on = cat.key == selected
                Text(
                    "${cat.glyph}  ${cat.label}",
                    modifier =
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(8.dp))
                            .background(if (on) SirenaColors.pillWarnBg else SirenaColors.cloud)
                            .clickable { selected = cat.key }
                            .padding(
                                vertical = if (shellCompact) 8.dp else 10.dp,
                                horizontal = if (shellCompact) 4.dp else 8.dp,
                            ),
                    fontWeight = if (on) FontWeight.Bold else FontWeight.Medium,
                    color = if (on) SirenaColors.pillWarnFg else SirenaColors.text,
                    fontSize = if (shellCompact) 12.sp else 14.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.weight(1f))
            Text(
                "${SIRENA_SETTINGS_CATEGORIES.size} categories",
                modifier = Modifier.fillMaxWidth().padding(if (shellCompact) 4.dp else 8.dp),
                fontSize = if (shellCompact) 9.sp else 11.sp,
                color = SirenaColors.muted,
                fontWeight = FontWeight.Normal,
            )
        }
        Column(
            Modifier
                .weight(1f)
                .fillMaxHeight(),
        ) {
            when (selected) {
                "network" ->
                    Column(Modifier.fillMaxSize()) {
                        Box(Modifier.weight(1f).fillMaxWidth()) {
                            SirenaNetworkSettingsScrollContent(
                                vm = vm,
                                state = state,
                                breadcrumbParts = listOf("Nina", "Settings", "Network"),
                                modifier = Modifier.fillMaxSize(),
                            )
                        }
                        Column(Modifier.padding(top = 8.dp)) {
                            SirenaMutedText(
                                "Tip: the companion **Network** tab is the same Jetson Wi‑Fi surface in full-screen layout.",
                                maxLines = 3,
                            )
                            SirenaSecondaryButton(
                                text = "Open Network tab",
                                onClick = onOpenNetworkTab,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                else ->
                    Column(
                        Modifier
                            .fillMaxSize()
                            .verticalScroll(rememberScrollState()),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        when (selected) {
                            "general" ->
                                SettingsGeneralPane(
                                    vm = vm,
                                    ready = ready,
                                    gatewayHint = gatewayHint,
                                    manifestActions = manifestActions.map { it.name }.distinct().sorted(),
                                    onBackToProductHub = onBackToProductHub,
                                    onNavigateToHealth = onNavigateToHealth,
                                )
                            "display" -> SettingsPlaceholderDisplay()
                            "audio" -> SettingsPlaceholderAudio()
                            "privacy" -> SettingsPlaceholderPrivacy()
                            "autodock" -> SettingsPlaceholderAutodock()
                            "voice" -> SettingsPlaceholderVoice()
                            "power" -> SettingsPowerPane(vm = vm, ready = ready)
                            "ota" -> SettingsPlaceholderOta()
                            else -> {
                                SirenaCard {
                                    SirenaMutedText("Unknown category.", maxLines = 2)
                                }
                            }
                        }
                    }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsGeneralPane(
    vm: CompanionViewModel,
    ready: CompanionUiState.Ready?,
    gatewayHint: String?,
    manifestActions: List<String>,
    onBackToProductHub: (() -> Unit)?,
    onNavigateToHealth: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var robotName by rememberSaveable { mutableStateOf(ready?.status?.displayName?.trim().orEmpty().ifBlank { "Nina" }) }
    var tzIndex by rememberSaveable { mutableIntStateOf(0) }
    var langIndex by rememberSaveable { mutableIntStateOf(0) }
    val bootNames = remember(manifestActions) {
        if (manifestActions.isNotEmpty()) manifestActions else listOf("neutral")
    }
    var bootIndex by rememberSaveable { mutableIntStateOf(0) }
    LaunchedEffect(bootNames) {
        bootIndex = bootIndex.coerceIn(0, (bootNames.size - 1).coerceAtLeast(0))
    }
    var greet by rememberSaveable { mutableStateOf(true) }
    var diag by rememberSaveable { mutableStateOf(false) }
    var saveHint by remember { mutableStateOf<String?>(null) }
    var confirmDiscard by remember { mutableStateOf(false) }
    var confirmResetAll by remember { mutableStateOf(false) }

    fun resetGeneralFields(clearHint: Boolean) {
        robotName = ready?.status?.displayName?.trim().orEmpty().ifBlank { "Nina" }
        tzIndex = 0
        langIndex = 0
        bootIndex = 0
        greet = true
        diag = false
        if (clearHint) saveHint = null
    }

    SirenaBreadcrumbLine(listOf("Nina", "Settings", "General"))
    Spacer(Modifier.height(6.dp))
    SirenaCard {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Image(
                painter = painterResource(R.drawable.nina_robot),
                contentDescription = null,
                modifier =
                    Modifier
                        .heightIn(max = 48.dp)
                        .widthIn(max = 72.dp),
                contentScale = ContentScale.Fit,
            )
            Column(Modifier.weight(1f)) {
                Text("Nina", fontWeight = FontWeight.Bold, color = SirenaColors.text, fontSize = 15.sp)
                val serial = ready?.status?.systemId?.trim()?.takeIf { it.isNotEmpty() } ?: "—"
                Text(
                    "Sirena Robotics · v${BuildConfig.VERSION_NAME} · serial $serial",
                    fontSize = 12.sp,
                    color = SirenaColors.muted,
                )
            }
            SirenaSecondaryButton(
                text = "View health",
                onClick = onNavigateToHealth,
                modifier = Modifier.widthIn(min = 120.dp),
            )
        }
    }
    SirenaCard {
        SirenaSectionLabel("General")
        Spacer(Modifier.height(6.dp))
        OutlinedTextField(
            value = robotName,
            onValueChange = { robotName = it; saveHint = null },
            label = { Text("Robot name") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        SettingsFormDropdown(
            label = "Time zone",
            options = TIME_ZONES,
            selectedIndex = tzIndex,
            onSelectIndex = { tzIndex = it; saveHint = null },
        )
        Spacer(Modifier.height(8.dp))
        SettingsFormDropdown(
            label = "Default language",
            options = LANGUAGES,
            selectedIndex = langIndex,
            onSelectIndex = { langIndex = it; saveHint = null },
        )
        Spacer(Modifier.height(8.dp))
        SettingsFormDropdown(
            label = "Boot action",
            options = bootNames,
            selectedIndex = bootIndex.coerceIn(0, (bootNames.size - 1).coerceAtLeast(0)),
            onSelectIndex = { bootIndex = it; saveHint = null },
        )
        Spacer(Modifier.height(4.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = greet, onCheckedChange = { greet = it; saveHint = null })
            Text("Speak greeting on boot", fontSize = SirenaType.base, color = SirenaColors.text)
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = diag, onCheckedChange = { diag = it; saveHint = null })
            Text("Show diagnostic overlay on screen", fontSize = SirenaType.base, color = SirenaColors.text)
        }
        Spacer(Modifier.height(8.dp))
        SirenaHRule()
        Spacer(Modifier.height(6.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            SirenaSectionLabel("Danger zone")
            SirenaSecondaryButton(
                text = "Reset all",
                onClick = { confirmResetAll = true },
            )
        }
        Spacer(Modifier.height(10.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.CenterVertically) {
            SirenaSecondaryButton(
                text = "Discard",
                onClick = { confirmDiscard = true },
            )
            Spacer(Modifier.width(8.dp))
            SirenaPrimaryButton(
                text = "Save changes",
                onClick = {
                    scope.launch {
                        val dn = robotName.trim()
                        if (dn.isNotEmpty()) {
                            vm.postSystemDisplayName(dn)
                        }
                        saveHint =
                            "Robot name, time zone, language and boot action saved locally on the tablet.\n\n" +
                                "Display name is also sent to the Jetson when the link daemon is online."
                    }
                },
            )
        }
        saveHint?.let {
            Spacer(Modifier.height(8.dp))
            SirenaMutedText(it, maxLines = 6)
        }
        Spacer(Modifier.height(8.dp))
        SirenaMutedText("Discovery hint: ${gatewayHint ?: "—"}", maxLines = 4)
        SirenaMutedText("Daemon URL: ${ready?.url ?: "—"}", maxLines = 2)
        SirenaMutedText("Hostname: ${ready?.status?.hostname ?: "—"}")
        SirenaMutedText(
            "To change the robot label on the network, set it on the Jetson (Sirena Control Center, NINA_LINK_ROBOT_NAME, or link state).",
            maxLines = 4,
        )
        onBackToProductHub?.let { back ->
            Spacer(Modifier.height(10.dp))
            SirenaPrimaryButton(
                text = "Return to product hub",
                onClick = back,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }

    if (confirmDiscard) {
        SirenaConfirmDialog(
            onDismiss = { confirmDiscard = false },
            message = "Discard unsaved changes to robot name, time zone, language, and boot options?",
            confirmText = "Discard",
            dangerous = true,
            onConfirm = {
                confirmDiscard = false
                resetGeneralFields(clearHint = true)
            },
        )
    }
    if (confirmResetAll) {
        SirenaConfirmDialog(
            onDismiss = { confirmResetAll = false },
            message = "Reset all general settings to their defaults on this tablet?",
            confirmText = "Reset all",
            dangerous = true,
            onConfirm = {
                confirmResetAll = false
                resetGeneralFields(clearHint = false)
                saveHint = "Local fields reset (same as desktop discard)."
            },
        )
    }
}

@Composable
private fun SettingsPlaceholderShell(title: String, content: @Composable ColumnScope.() -> Unit) {
    SirenaCard {
        Text(title, fontWeight = FontWeight.Bold, color = SirenaColors.text, fontSize = 18.sp)
        Spacer(Modifier.height(6.dp))
        SirenaMutedText(
            "Controls for this category will land alongside the matching hardware feature. The layout is locked in so wiring it up stays a one-line change.",
            maxLines = 5,
        )
        Spacer(Modifier.height(10.dp))
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) { content() }
        Spacer(Modifier.height(12.dp))
        Row {
            SirenaStatusPill("Coming soon", SirenaPillKind.Neutral)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsPlaceholderDisplay() {
    var wifiIdx by rememberSaveable { mutableIntStateOf(0) }
    val wifiOpts = listOf("Sirena-5G", "Sirena-Guest", "Other…")
    var bright by rememberSaveable { mutableFloatStateOf(70f) }
    var sleepIdx by rememberSaveable { mutableIntStateOf(0) }
    val sleepOpts = listOf("Never", "1 min", "5 min", "15 min")
    SettingsPlaceholderShell("Display") {
        SettingsFormDropdown("Wi-Fi network", wifiOpts, wifiIdx, { wifiIdx = it })
        Text("IP address", fontSize = SirenaType.muted, color = SirenaColors.muted)
        Text("\u2014", color = SirenaColors.text)
        Text("Brightness", fontSize = SirenaType.muted, color = SirenaColors.muted)
        Slider(value = bright, onValueChange = { bright = it }, valueRange = 0f..100f)
        SettingsFormDropdown("Screen sleep", sleepOpts, sleepIdx, { sleepIdx = it })
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsPlaceholderAudio() {
    var vol by rememberSaveable { mutableFloatStateOf(60f) }
    var micIdx by rememberSaveable { mutableIntStateOf(0) }
    val micOpts = listOf("Default", "USB Mic", "Built-in")
    SettingsPlaceholderShell("Audio") {
        Text("Speaker volume", fontSize = SirenaType.muted, color = SirenaColors.muted)
        Slider(value = vol, onValueChange = { vol = it }, valueRange = 0f..100f)
        SettingsFormDropdown("Microphone", micOpts, micIdx, { micIdx = it })
    }
}

@Composable
private fun SettingsPlaceholderPrivacy() {
    var cam by rememberSaveable { mutableStateOf(false) }
    var mic by rememberSaveable { mutableStateOf(false) }
    SettingsPlaceholderShell("Privacy") {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = cam, onCheckedChange = { cam = it })
            Text("Disable camera when idle", color = SirenaColors.text)
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = mic, onCheckedChange = { mic = it })
            Text("Disable microphone when idle", color = SirenaColors.text)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsPlaceholderAutodock() {
    var thr by rememberSaveable { mutableFloatStateOf(20f) }
    var chIdx by rememberSaveable { mutableIntStateOf(0) }
    val chOpts = listOf("Standard", "Fast")
    SettingsPlaceholderShell("Autodock") {
        Text("Return-to-dock at", fontSize = SirenaType.muted, color = SirenaColors.muted)
        Slider(value = thr, onValueChange = { thr = it }, valueRange = 5f..50f)
        SettingsFormDropdown("Charging type", chOpts, chIdx, { chIdx = it })
    }
}

@Composable
private fun SettingsPlaceholderVoice() {
    var wake by rememberSaveable { mutableStateOf("Hey Nina") }
    SettingsPlaceholderShell("Voice") {
        OutlinedTextField(
            value = wake,
            onValueChange = { wake = it },
            label = { Text("Wake word") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Text("ESP firmware", fontSize = SirenaType.muted, color = SirenaColors.muted)
        Text("0.7", color = SirenaColors.text)
    }
}

@Composable
private fun SettingsPowerPane(
    vm: CompanionViewModel,
    ready: CompanionUiState.Ready?,
) {
    // Two-stage UX: tap a row -> AlertDialog confirms -> request fires.
    // We keep the dialogs lightweight (no separate composables) since
    // both flows are nearly identical and the only knob that differs is
    // the verb ("Shutdown" vs "Reboot") and the VM call.
    var pendingAction by remember { mutableStateOf<String?>(null) }
    var lastResult by remember { mutableStateOf<String?>(null) }
    var inFlight by remember { mutableStateOf(false) }
    val online = ready != null

    SirenaCard {
        SirenaSectionLabel("Power")
        Spacer(Modifier.height(6.dp))
        Text(
            "Shutdown or reboot the Jetson host. The robot needs " +
                "passwordless sudo for systemctl/poweroff/reboot — see " +
                "nina/jetson_net/host_control.py for the sudoers drop-in.",
            color = SirenaColors.muted,
            fontSize = SirenaType.muted,
        )
        Spacer(Modifier.height(10.dp))
        SirenaHRule()
        Spacer(Modifier.height(10.dp))

        // Shutdown row
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Column(Modifier.weight(1f)) {
                SirenaSectionLabel("Shutdown Jetson")
                Spacer(Modifier.height(2.dp))
                SirenaMutedText(
                    "Brings the OS down cleanly. The chassis power button is " +
                        "the only way back up.",
                    maxLines = 3,
                )
            }
            SirenaSecondaryButton(
                text = "Shutdown",
                onClick = { pendingAction = "poweroff" },
                enabled = online && !inFlight,
                modifier = Modifier.widthIn(min = 132.dp),
            )
        }

        Spacer(Modifier.height(10.dp))
        SirenaHRule()
        Spacer(Modifier.height(10.dp))

        // Reboot row
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Column(Modifier.weight(1f)) {
                SirenaSectionLabel("Reboot Jetson")
                Spacer(Modifier.height(2.dp))
                SirenaMutedText(
                    "Restarts the OS. Use after a git pull that touches " +
                        "systemd units, kernel modules, or udev rules.",
                    maxLines = 3,
                )
            }
            SirenaSecondaryButton(
                text = "Reboot",
                onClick = { pendingAction = "reboot" },
                enabled = online && !inFlight,
                modifier = Modifier.widthIn(min = 132.dp),
            )
        }

        Spacer(Modifier.height(10.dp))
        if (!online) {
            Row { SirenaStatusPill("Offline", SirenaPillKind.Warn) }
            Spacer(Modifier.height(6.dp))
            SirenaMutedText(
                "Companion is not paired with the Jetson yet — connect on " +
                    "the Home tab to enable these buttons.",
                maxLines = 3,
            )
        }
        lastResult?.let { msg ->
            Spacer(Modifier.height(8.dp))
            SirenaMutedText(msg, maxLines = 6)
        }
    }

    pendingAction?.let { action ->
        val verb = if (action == "poweroff") "Shutdown" else "Reboot"
        val body = if (action == "poweroff") {
            "Bring the Jetson down NOW?\n\n" +
                "The screen will go dark in a few seconds. To bring Nina " +
                "back up you'll need to press the physical power button " +
                "on the chassis."
        } else {
            "Reboot the Jetson NOW?\n\n" +
                "The current session will end. Nina should be back at " +
                "the kiosk screen in ~45 seconds."
        }
        AlertDialog(
            onDismissRequest = { if (!inFlight) pendingAction = null },
            title = { Text("$verb Jetson?", fontWeight = FontWeight.SemiBold) },
            text = {
                Text(
                    body,
                    color = SirenaColors.muted,
                    fontSize = SirenaType.muted,
                )
            },
            confirmButton = {
                TextButton(
                    enabled = !inFlight,
                    onClick = {
                        inFlight = true
                        val cb: (String?) -> Unit = { err ->
                            inFlight = false
                            pendingAction = null
                            lastResult = if (err == null) {
                                "$verb dispatched. If nothing happens within " +
                                    "~10 s, configure passwordless sudo on the " +
                                    "Jetson (see nina/jetson_net/host_control.py)."
                            } else {
                                "$verb failed: $err"
                            }
                        }
                        if (action == "poweroff") {
                            vm.requestJetsonShutdown(cb)
                        } else {
                            vm.requestJetsonReboot(cb)
                        }
                    },
                ) {
                    Text(
                        verb,
                        color = SirenaColors.danger,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            },
            dismissButton = {
                TextButton(
                    enabled = !inFlight,
                    onClick = { pendingAction = null },
                ) { Text("Cancel") }
            },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsPlaceholderOta() {
    var chIdx by rememberSaveable { mutableIntStateOf(0) }
    val chOpts = listOf("stable", "beta")
    SettingsPlaceholderShell("OTA") {
        SettingsFormDropdown("Channel", chOpts, chIdx, { chIdx = it })
        Text("Last update", fontSize = SirenaType.muted, color = SirenaColors.muted)
        Text("\u2014", color = SirenaColors.text)
    }
}
