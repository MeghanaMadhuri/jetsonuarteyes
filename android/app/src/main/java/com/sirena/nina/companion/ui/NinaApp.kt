package com.sirena.nina.companion.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.ui.Alignment
import androidx.compose.material3.Button
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.net.Uri
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sirena.nina.companion.CompanionUiState
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.DiscoveryDiagnosticsUi
import com.sirena.nina.companion.util.NinaLog
import com.sirena.nina.companion.ui.sirena.SirenaBreadcrumbLine
import com.sirena.nina.companion.ui.sirena.SirenaCard
import com.sirena.nina.companion.ui.sirena.SirenaCardKind
import com.sirena.nina.companion.ui.sirena.SirenaColors
import com.sirena.nina.companion.ui.sirena.SirenaActionsScreen
import com.sirena.nina.companion.ui.sirena.SirenaBreakpointCompactHeight
import com.sirena.nina.companion.ui.sirena.SirenaBreakpointCompactSmallestWidthDp
import com.sirena.nina.companion.ui.sirena.SirenaBreakpointCompactWidth
import com.sirena.nina.companion.ui.sirena.SirenaDriveScreen
import com.sirena.nina.companion.ui.sirena.SirenaHealthScreen
import com.sirena.nina.companion.ui.sirena.NavEntry
import com.sirena.nina.companion.ui.sirena.SirenaHomeScreen
import com.sirena.nina.companion.ui.sirena.SirenaMapScreen
import com.sirena.nina.companion.ui.sirena.SirenaMutedText
import com.sirena.nina.companion.ui.sirena.SirenaNavCatalog
import com.sirena.nina.companion.ui.sirena.SirenaNetworkSettingsScrollContent
import com.sirena.nina.companion.ui.sirena.SirenaPrimaryButton
import com.sirena.nina.companion.ui.sirena.SirenaSecondaryButton
import com.sirena.nina.companion.ui.sirena.SirenaSectionLabel
import com.sirena.nina.companion.ui.sirena.SirenaType
import com.sirena.nina.companion.ui.sirena.SirenaPerceptionScreen
import com.sirena.nina.companion.ui.sirena.SirenaPlaceholderScreen
import com.sirena.nina.companion.ui.sirena.SirenaSettingsScreen
import com.sirena.nina.companion.ui.sirena.LocalSirenaShellCompact
import com.sirena.nina.companion.ui.sirena.SirenaNavDrawerSheetContent
import com.sirena.nina.companion.ui.sirena.SirenaShellFooter
import com.sirena.nina.companion.ui.sirena.SirenaShellHeader
import com.sirena.nina.companion.ui.sirena.SirenaSidebar
import com.sirena.nina.companion.ui.sirena.SirenaVisionScreen
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.isActive
import org.json.JSONObject
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/**
 * Layout aligned with [sirena_ui.main_window.MainWindow]: **full-width** red header, **sidebar** (tablet)
 * or **hamburger drawer** (compact phones), main **cloud** column, light **status strip** at bottom.
 */
@Composable
fun NinaApp(
    vm: CompanionViewModel,
    onBackToProductHub: (() -> Unit)? = null,
) {
    val state by vm.state.collectAsStateWithLifecycle()
    val discovered by vm.discoveredDaemons.collectAsStateWithLifecycle()
    val discoveryDiagnostics by vm.discoveryDiagnostics.collectAsStateWithLifecycle()
    val jetsonLink by vm.jetsonLink.collectAsStateWithLifecycle()
    val robotCaps by vm.robotCapabilities.collectAsStateWithLifecycle()

    var selectedNav by rememberSaveable { mutableStateOf("home") }
    /** Deep-link from Home quick tiles into Actions subtabs (`playback` / `record` / `audio`). */
    var actionsSubtab by rememberSaveable { mutableStateOf("playback") }
    /** One-shot prefill for the Audio tab (Playback **Audio**). Cleared after apply. */
    var actionsAudioPrefill by rememberSaveable { mutableStateOf<String?>(null) }
    var clockText by remember {
        mutableStateOf(LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm")))
    }
    var batteryRowOk by remember { mutableStateOf(false) }

    val ready = state as? CompanionUiState.Ready
    val hostLabel = resolveRobotDisplayName(ready, discovered)

    LaunchedEffect(Unit) {
        vm.refreshStatus()
    }

    LaunchedEffect(Unit) {
        snapshotFlow { selectedNav }
            .distinctUntilChanged()
            .filter { it == "find" }
            .collect {
                vm.scanForDaemons()
            }
    }

    LaunchedEffect(Unit) {
        while (isActive) {
            clockText = LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm"))
            delay(30_000L)
        }
    }

    LaunchedEffect(ready?.url, jetsonLink.isOnline) {
        while (isActive) {
            if (ready != null && jetsonLink.isOnline) {
                val h = vm.fetchDaemonHealth()
                batteryRowOk = healthBatteryOk(h)
            } else {
                batteryRowOk = false
            }
            delay(5000L)
        }
    }

    val companionNavEntries =
        remember(onBackToProductHub) {
            buildList {
                if (onBackToProductHub != null) {
                    add(NavEntry("products", "\u25A4", "Products"))
                }
                addAll(SirenaNavCatalog.companionNav)
            }
        }

    BoxWithConstraints(
        Modifier
            .fillMaxSize()
            .background(SirenaColors.cloud),
    ) {
        val configuration = LocalConfiguration.current
        /** Drawer + dense home: phones (`sw<600`) or physically small compose window (split-screen, etc.). */
        val shellCompact =
            configuration.smallestScreenWidthDp < SirenaBreakpointCompactSmallestWidthDp ||
                maxWidth < SirenaBreakpointCompactWidth ||
                maxHeight < SirenaBreakpointCompactHeight
        val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
        val scope = rememberCoroutineScope()

        fun applyNavSelection(key: String) {
            if (key == "products") {
                onBackToProductHub?.invoke()
            } else {
                selectedNav = key
            }
        }

        val shellBody: @Composable () -> Unit = {
            CompositionLocalProvider(LocalSirenaShellCompact provides shellCompact) {
                Column(Modifier.fillMaxSize()) {
                SirenaShellHeader(
                    modifier = Modifier.fillMaxWidth(),
                    title = SirenaNavCatalog.headerTitle(selectedNav),
                    clockText = clockText,
                    connectedLabel = hostLabel,
                    jetsonOnline = jetsonLink.isOnline,
                    showProductHubBack = onBackToProductHub != null,
                    onProductHubBack = onBackToProductHub,
                    showNavDrawerMenu = shellCompact,
                    onNavDrawerMenuClick =
                        if (shellCompact) {
                            { scope.launch { drawerState.open() } }
                        } else {
                            null
                        },
                    compact = shellCompact,
                )
                Row(
                    Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                ) {
                    if (!shellCompact) {
                        SirenaSidebar(
                            modifier = Modifier.fillMaxHeight(),
                            robotItems = SirenaNavCatalog.robotNav,
                            companionItems = companionNavEntries,
                            selectedKey = selectedNav,
                            compact = false,
                            onSelect = { applyNavSelection(it) },
                            versionLabel = "v1.0.0",
                            hostLabel = hostLabel,
                        )
                    }
                    Column(
                        Modifier
                            .weight(1f)
                            .fillMaxHeight(),
                    ) {
                        Column(
                            Modifier
                                .weight(1f)
                                .fillMaxWidth()
                                .background(SirenaColors.cloud),
                        ) {
                    when (val s = state) {
                        is CompanionUiState.Error -> {
                            SirenaCard(
                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
                                kind = SirenaCardKind.Error,
                            ) {
                                Text(
                                    s.text,
                                    color = SirenaColors.pillErrorFg,
                                    fontWeight = FontWeight.SemiBold,
                                )
                                SirenaMutedText(
                                    "You can retry the request or continue using Find robot, Network, and Settings.",
                                    maxLines = 3,
                                )
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Button(
                                        onClick = {
                                            NinaLog.debug("NinaApp", "errorBanner Retry")
                                            vm.refreshStatus()
                                        },
                                    ) { Text("Retry") }
                                    TextButton(
                                        onClick = {
                                            NinaLog.debug("NinaApp", "errorBanner Continue")
                                            vm.dismissErrorToDegradedReady()
                                        },
                                    ) {
                                        Text("Continue")
                                    }
                                }
                            }
                        }
                        is CompanionUiState.Loading -> {
                            SirenaCard(
                                modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
                                kind = SirenaCardKind.Subtle,
                            ) {
                                Text("Loading robot status…", color = SirenaColors.text)
                            }
                        }
                        else -> Unit
                    }
                    Box(Modifier.weight(1f).fillMaxWidth().fillMaxHeight()) {
                    val navigateQuick: (String) -> Unit = { key ->
                        NinaLog.debug("NinaApp", "navigateQuick key=$key")
                        when {
                            key.startsWith("actions:") -> {
                                actionsSubtab = key.removePrefix("actions:")
                                selectedNav = "actions"
                            }
                            else -> selectedNav = key
                        }
                    }
                    when (selectedNav) {
                        "find" ->
                            DiscoveryTab(
                                vm = vm,
                                state = state,
                                discovered = discovered,
                                diagnostics = discoveryDiagnostics,
                                shellCompact = shellCompact,
                            )
                        "network" ->
                            NetworkTab(
                                vm = vm,
                                state = state,
                                shellCompact = shellCompact,
                            )
                        "drive" ->
                            SirenaDriveScreen(
                                vm = vm,
                                caps = robotCaps,
                                daemonUrl = ready?.url,
                                shellCompact = shellCompact,
                            )
                        "vision" ->
                            SirenaVisionScreen(
                                vm = vm,
                                daemonUrl = ready?.url,
                                caps = robotCaps,
                                shellCompact = shellCompact,
                            )
                        "perception" ->
                            SirenaPerceptionScreen(
                                vm = vm,
                                daemonUrl = ready?.url,
                                shellCompact = shellCompact,
                            )
                        "map" ->
                            SirenaMapScreen(
                                vm = vm,
                                daemonUrl = ready?.url,
                                caps = robotCaps,
                                shellCompact = shellCompact,
                            )
                        "home" ->
                            SirenaHomeScreen(
                                state = state,
                                jetsonOnline = jetsonLink.isOnline,
                                robotDisplayName = resolveRobotDisplayName(ready, discovered),
                                systemId = robotHomeSystemId(ready),
                                ipv4 = robotHomePublicSubtitle(ready),
                                onNavigate = navigateQuick,
                                shellCompact = shellCompact,
                            )
                        "actions" ->
                            SirenaActionsScreen(
                                vm = vm,
                                initialSubtab = actionsSubtab,
                                caps = robotCaps,
                                shellCompact = shellCompact,
                                prefillAudioAction = actionsAudioPrefill,
                                onPrefillAudioConsumed = { actionsAudioPrefill = null },
                                onPlaybackOpenAudioEditor = { name ->
                                    actionsSubtab = "audio"
                                    actionsAudioPrefill = name
                                },
                            )
                        "settings" ->
                            SirenaSettingsScreen(
                                vm = vm,
                                state = state,
                                shellCompact = shellCompact,
                                onOpenNetworkTab = { selectedNav = "network" },
                                onNavigateToHealth = { selectedNav = "health" },
                                onBackToProductHub = onBackToProductHub,
                            )
                        "health" -> SirenaHealthScreen(vm = vm, shellCompact = shellCompact)
                        else -> SirenaPlaceholderScreen(selectedNav, selectedNav)
                    }
                    }
                }
                SirenaShellFooter(
                    modifier = Modifier.fillMaxWidth(),
                    busOk = jetsonLink.isOnline,
                    wifiOk = jetsonLink.isOnline,
                    batteryOk = batteryRowOk,
                    voiceOk = jetsonLink.isOnline,
                    rightCaption =
                        if (jetsonLink.isOnline) {
                            "link OK"
                        } else {
                            "offline"
                        },
                )
            }
        }
            }
            }
        }

        if (shellCompact) {
            ModalNavigationDrawer(
                drawerState = drawerState,
                drawerContent = {
                    ModalDrawerSheet(drawerContainerColor = Color.Transparent) {
                        SirenaNavDrawerSheetContent(
                            robotItems = SirenaNavCatalog.robotNav,
                            companionItems = companionNavEntries,
                            selectedKey = selectedNav,
                            onSelect = { key ->
                                scope.launch { drawerState.close() }
                                applyNavSelection(key)
                            },
                            versionLabel = "v1.0.0",
                            hostLabel = hostLabel,
                        )
                    }
                },
                content = shellBody,
            )
        } else {
            shellBody()
        }
    }
}

/** Match saved daemon URL to scan results so hostname / system_id show before the next full status refresh. */
private fun normalizeDaemonBaseUrl(url: String): String = url.trim().trimEnd('/').lowercase()

private fun resolveRobotDisplayName(
    ready: CompanionUiState.Ready?,
    discovered: List<com.sirena.nina.companion.DiscoveredDaemonUi>,
): String {
    val st = ready?.status
    val fromStatus =
        st?.displayName?.trim()?.takeIf { it.isNotEmpty() }
            ?: st?.hostname?.trim()?.takeIf { it.isNotEmpty() }
    if (!fromStatus.isNullOrBlank()) return fromStatus
    val base = ready?.url?.trim()?.takeIf { it.isNotEmpty() } ?: return "Nina"
    val key = normalizeDaemonBaseUrl(base)
    val row = discovered.firstOrNull { normalizeDaemonBaseUrl(it.baseUrl) == key }
    val fromScan =
        row?.displayName?.trim()?.takeIf { it.isNotEmpty() }
            ?: row?.hostname?.trim()?.takeIf { it.isNotEmpty() }
            ?: row?.systemId?.trim()?.takeIf { it.isNotEmpty() }
    if (!fromScan.isNullOrBlank()) return fromScan
    return Uri.parse(base).host?.takeIf { it.isNotEmpty() } ?: "Nina"
}

/** Shown under robot name (non-sensitive), e.g. IPv4. */
private fun robotHomePublicSubtitle(ready: CompanionUiState.Ready?): String? =
    ready?.status?.ipv4?.trim()?.takeIf { it.isNotBlank() }

private fun robotHomeSystemId(ready: CompanionUiState.Ready?): String? =
    ready?.status?.systemId?.trim()?.takeIf { it.isNotBlank() }

private fun discoveredRobotTitle(d: com.sirena.nina.companion.DiscoveredDaemonUi): String {
    val friendly = d.displayName?.trim()?.takeIf { it.isNotEmpty() }
    if (!friendly.isNullOrBlank()) return friendly
    val h = d.hostname?.trim()?.takeIf { it.isNotEmpty() }
    if (!h.isNullOrBlank()) return h
    val sid = d.systemId?.trim()?.takeIf { it.isNotEmpty() }
    if (!sid.isNullOrBlank()) return sid
    return Uri.parse(d.baseUrl).host?.takeIf { it.isNotEmpty() } ?: "Robot"
}

private fun healthBatteryOk(h: JSONObject?): Boolean {
    val rows = h?.optJSONArray("rows") ?: return false
    for (i in 0 until rows.length()) {
        val o = rows.optJSONObject(i) ?: continue
        if (o.optString("key") == "battery") {
            val st = o.optString("state").lowercase()
            return st == "ok" || st == "ready"
        }
    }
    return false
}

@Composable
private fun DiscoveryTab(
    vm: CompanionViewModel,
    state: CompanionUiState,
    discovered: List<com.sirena.nina.companion.DiscoveredDaemonUi>,
    diagnostics: DiscoveryDiagnosticsUi,
    shellCompact: Boolean,
) {
    val scope = rememberCoroutineScope()
    var findConnectError by remember { mutableStateOf<String?>(null) }
    Column(
        Modifier
            .fillMaxSize()
            .padding(
                horizontal = if (shellCompact) 4.dp else 12.dp,
                vertical = if (shellCompact) 3.dp else 10.dp,
            ),
        verticalArrangement = Arrangement.spacedBy(if (shellCompact) 3.dp else 10.dp),
    ) {
        SirenaBreadcrumbLine(listOf("Nina", "Find robot"))

        SirenaCard(kind = SirenaCardKind.Callout) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(if (shellCompact) 6.dp else 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(
                    Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(if (shellCompact) 3.dp else 4.dp),
                ) {
                    Text(
                        "Discover Nina on your network",
                        fontWeight = FontWeight.Bold,
                        color = SirenaColors.text,
                        fontSize = if (shellCompact) 13.sp else SirenaType.cardTitle,
                    )
                    SirenaMutedText(
                        if (shellCompact) {
                            "mDNS + LAN scan for link daemon (8787)."
                        } else {
                            "Scans mDNS and your LAN for the link daemon (port 8787). Use Connect to save a daemon — the app does not auto-pick one."
                        },
                        maxLines = if (shellCompact) 1 else 4,
                    )
                }
                Column(
                    horizontalAlignment = Alignment.End,
                    verticalArrangement = Arrangement.spacedBy(if (shellCompact) 4.dp else 6.dp),
                ) {
                    SirenaPrimaryButton(
                        text = if (diagnostics.isScanning) "Scanning…" else "Scan LAN",
                        onClick = { vm.scanForDaemons() },
                        enabled = !diagnostics.isScanning,
                        modifier = Modifier.widthIn(min = 120.dp),
                    )
                    SirenaSecondaryButton(
                        text = "Refresh status",
                        onClick = { vm.refreshStatus() },
                    )
                }
            }
        }

        val tabReady = state as? CompanionUiState.Ready
        val active = tabReady?.status
        val connectedTitle = resolveRobotDisplayName(tabReady, discovered)

        @Composable
        fun DiagnosticsCard(modifier: Modifier = Modifier) {
            SirenaCard(modifier = modifier, kind = SirenaCardKind.Subtle) {
                Text("Discovery diagnostics", fontWeight = FontWeight.SemiBold, color = SirenaColors.text)
                Spacer(Modifier.height(if (shellCompact) 4.dp else 6.dp))
                SirenaMutedText("This tablet: ${diagnostics.deviceIpv4 ?: "—"}")
                SirenaMutedText("Subnet scanned: ${(diagnostics.subnetPrefix?.plus(".x")) ?: "—"} · hosts ${diagnostics.hostCount}")
                SirenaMutedText("Probes: ${diagnostics.probeAttempts} · responsive ${diagnostics.successfulHosts} · failed ${diagnostics.failedProbes}")
                SirenaMutedText(
                    "Last scan: ${diagnostics.durationMs?.let { "${it} ms" } ?: "—"}",
                )
                diagnostics.lastError?.let {
                    SirenaMutedText(
                        "Last scan did not finish cleanly — try Scan LAN again or use the product hub radar.",
                        maxLines = 3,
                    )
                }
            }
        }

        @Composable
        fun ConnectedCard(modifier: Modifier = Modifier) {
            SirenaCard(modifier = modifier) {
                Text("Currently connected", fontWeight = FontWeight.SemiBold, color = SirenaColors.text)
                Spacer(Modifier.height(if (shellCompact) 4.dp else 6.dp))
                Text(
                    connectedTitle,
                    fontWeight = FontWeight.Bold,
                    color = SirenaColors.text,
                )
                SirenaMutedText("Configured name: ${active?.displayName ?: "—"}")
                SirenaMutedText("Reported host: ${active?.hostname ?: "—"}")
                SirenaMutedText("System ID: ${active?.systemId ?: "—"} · Role: ${active?.wifiRole ?: "—"}")
            }
        }

        BoxWithConstraints(Modifier.fillMaxWidth()) {
            val split = maxWidth >= 520.dp
            if (split) {
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.Top,
                ) {
                    DiagnosticsCard(Modifier.weight(1f))
                    ConnectedCard(Modifier.weight(1f))
                }
            } else {
                Column(
                    Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(if (shellCompact) 6.dp else 10.dp),
                ) {
                    DiagnosticsCard(Modifier.fillMaxWidth())
                    ConnectedCard(Modifier.fillMaxWidth())
                }
            }
        }

        SirenaSectionLabel("Nearby systems")
        findConnectError?.let { err ->
            SirenaCard(kind = SirenaCardKind.Error) {
                Text(
                    err,
                    color = SirenaColors.pillErrorFg,
                    fontSize = if (shellCompact) 9.sp else SirenaType.muted,
                    maxLines = 3,
                )
            }
        }
        if (discovered.isEmpty()) {
            SirenaCard(kind = SirenaCardKind.Subtle) {
                SirenaMutedText(
                    if (shellCompact) {
                        "No systems — Scan LAN or use product hub."
                    } else {
                        "No systems found yet. Open the product hub radar or tap Scan LAN. Ensure nina-link is running."
                    },
                    maxLines = if (shellCompact) 2 else 4,
                )
            }
        } else {
            val shown = if (shellCompact) discovered.take(6) else discovered
            BoxWithConstraints(Modifier.weight(1f).fillMaxWidth()) {
                val cols =
                    when {
                        shellCompact && maxWidth >= 720.dp -> 3
                        shellCompact -> 2
                        maxWidth >= 520.dp -> 2
                        else -> 1
                    }
                val gap = if (shellCompact) 5.dp else 10.dp
                Column(
                    Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(gap),
                ) {
                    shown.chunked(cols).forEach { row ->
                        Row(
                            Modifier
                                .then(
                                    if (shellCompact) {
                                        Modifier
                                            .weight(1f)
                                            .fillMaxWidth()
                                    } else {
                                        Modifier.fillMaxWidth()
                                    },
                                ),
                            horizontalArrangement = Arrangement.spacedBy(gap),
                        ) {
                            row.forEach { d ->
                                val title = discoveredRobotTitle(d)
                                SirenaCard(
                                    modifier = Modifier.weight(1f),
                                ) {
                                    Row(
                                        Modifier.fillMaxWidth(),
                                        horizontalArrangement =
                                            Arrangement.spacedBy(if (shellCompact) 6.dp else 10.dp),
                                        verticalAlignment = Alignment.CenterVertically,
                                    ) {
                                        Column(
                                            Modifier.weight(1f),
                                            verticalArrangement =
                                                Arrangement.spacedBy(if (shellCompact) 3.dp else 4.dp),
                                        ) {
                                            Text(
                                                title,
                                                fontWeight = FontWeight.Bold,
                                                color = SirenaColors.text,
                                                fontSize = if (shellCompact) 13.sp else SirenaType.cardTitle,
                                            )
                                            SirenaMutedText(d.baseUrl, maxLines = 2)
                                            d.systemId?.let { sid ->
                                                if (sid.isNotBlank()) {
                                                    SirenaMutedText("ID · $sid", maxLines = 1)
                                                }
                                            }
                                        }
                                        SirenaPrimaryButton(
                                            text = "Connect",
                                            onClick = {
                                                scope.launch {
                                                    findConnectError =
                                                        vm.connectDiscoveredAndRefresh(d.baseUrl)
                                                }
                                            },
                                            modifier = Modifier.height(40.dp),
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun NetworkTab(
    vm: CompanionViewModel,
    state: CompanionUiState,
    shellCompact: Boolean,
) {
    SirenaNetworkSettingsScrollContent(
        vm = vm,
        state = state,
        breadcrumbParts = listOf("Nina", "Network"),
        modifier =
            Modifier
                .fillMaxSize()
                .padding(if (shellCompact) 4.dp else 10.dp),
    )
}
