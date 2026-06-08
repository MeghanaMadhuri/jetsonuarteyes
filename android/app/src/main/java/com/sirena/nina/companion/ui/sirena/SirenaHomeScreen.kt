package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.rememberCoroutineScope
import com.sirena.nina.companion.CompanionViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sirena.nina.companion.CompanionUiState
import com.sirena.nina.companion.R
import com.sirena.nina.companion.data.PowerStateUi

/**
 * Dashboard aligned with [sirena_ui.screens.home_screen.HomeScreen] quick tiles + status strip.
 * On phones ([shellCompact]) the shell uses a drawer and this screen **fills the viewport without scrolling**;
 * tablets keep a scrollable column for comfortable reading.
 */
data class SirenaQuickAction(
    val navKey: String,
    val label: String,
    val glyph: String,
    val blurb: String,
)

/** Same keys/order as ``QUICK_ACTIONS`` in ``home_screen.py`` (before ``MainWindow.navigate``). */
val SIRENA_QUICK_ACTIONS: List<SirenaQuickAction> =
    listOf(
        SirenaQuickAction("actions:playback", "Play action", "\u25B6", "Run a saved motion"),
        SirenaQuickAction("actions:record", "Record", "\u25CF", "Capture a new pose"),
        SirenaQuickAction("actions:audio", "Audio", "\u266B", "Voice clips"),
        SirenaQuickAction("drive", "Drive", "\u2B95", "Manual control"),
        SirenaQuickAction("movements", "Movements", "\u21BB", "Saved drive sequences"),
        SirenaQuickAction("vision", "Vision", "\u25CE", "Camera & faces"),
        SirenaQuickAction("map", "Map", "\u25A6", "SLAM & dock"),
        SirenaQuickAction("health", "Health", "\u2665", "System checks"),
        SirenaQuickAction("settings", "Settings", "\u2699", "Configure"),
    )

private fun homeQuickActionColumns(maxWidth: Dp, shellCompact: Boolean): Int =
    when {
        shellCompact -> 2
        maxWidth >= 720.dp -> 4
        maxWidth >= 520.dp -> 3
        else -> 2
    }

private data class HomePillUi(val text: String, val kind: SirenaPillKind)

private fun healthRowByKey(rows: JSONArray?, key: String): JSONObject? {
    if (rows == null) return null
    for (i in 0 until rows.length()) {
        val o = rows.optJSONObject(i) ?: continue
        if (o.optString("key") == key) return o
    }
    return null
}

private fun pillKindForHealthStatus(status: String): SirenaPillKind =
    when (status.trim().lowercase()) {
        "ok", "ready" -> SirenaPillKind.Ok
        "warn" -> SirenaPillKind.Warn
        "error" -> SirenaPillKind.Error
        else -> SirenaPillKind.Neutral
    }

private fun heroBusPill(rows: JSONArray?, jetsonOnline: Boolean): HomePillUi {
    if (!jetsonOnline) return HomePillUi("Bus —", SirenaPillKind.Neutral)
    val row = healthRowByKey(rows, "bus") ?: return HomePillUi("Bus —", SirenaPillKind.Neutral)
    val st = row.optString("status").trim().lowercase().ifBlank { "pending" }
    val kind = pillKindForHealthStatus(st)
    val detail = row.optString("detail").trim()
    val text =
        when {
            st in listOf("ok", "ready") -> "Bus ready"
            st == "pending" -> "Bus idle"
            st in listOf("warn", "error") -> detail.take(22).ifBlank { "Bus issue" }
            else -> detail.take(22).ifBlank { "Bus" }
        }
    return HomePillUi(text, kind)
}

private fun heroTorquePill(drive: JSONObject?, jetsonOnline: Boolean): HomePillUi {
    if (!jetsonOnline || drive == null) return HomePillUi("Torque …", SirenaPillKind.Neutral)
    val connected = drive.optBoolean("connected")
    val msg = drive.optString("message").trim()
    if (connected) return HomePillUi("Torque ON", SirenaPillKind.Ok)
    val low = msg.lowercase()
    if (
        msg.isBlank() ||
            listOf("initialis", "initializ", "waiting", "not yet", "queued").any { it in low }
    ) {
        return HomePillUi("Drive …", SirenaPillKind.Neutral)
    }
    if ("simulation" in low) return HomePillUi("Simulation", SirenaPillKind.Warn)
    return HomePillUi("Torque off", SirenaPillKind.Warn)
}

private fun heroVoicePill(rows: JSONArray?, jetsonOnline: Boolean): HomePillUi {
    if (!jetsonOnline) return HomePillUi("Voice —", SirenaPillKind.Neutral)
    val row = healthRowByKey(rows, "voice") ?: return HomePillUi("Voice —", SirenaPillKind.Neutral)
    val st = row.optString("status").trim().lowercase().ifBlank { "pending" }
    val kind = pillKindForHealthStatus(st)
    val detail = row.optString("detail").trim()
    val text =
        when {
            st in listOf("ok", "ready") -> "Voice ready"
            st == "pending" -> "Voice idle"
            else -> detail.take(22).ifBlank { "Voice" }
        }
    return HomePillUi(text, kind)
}

private fun overviewPill(rows: JSONArray?, key: String, title: String): Pair<String, SirenaPillKind> {
    val row = healthRowByKey(rows, key)
    if (row == null) return "—" to SirenaPillKind.Neutral
    val st = row.optString("status").trim().lowercase().ifBlank { "pending" }
    val kind = pillKindForHealthStatus(st)
    var detail = row.optString("detail").trim()
    if (key == "wifi" && detail.isNotBlank()) {
        val low = detail.lowercase()
        if ("offline" in low) return "Offline" to SirenaPillKind.Neutral
        if ("connect" in low) return "Online" to SirenaPillKind.Ok
    }
    val cap = if (detail.isNotBlank()) detail.take(22) else "—"
    return cap to kind
}

@Composable
private fun HomeHeroCard(
    jetsonOnline: Boolean,
    busPill: HomePillUi,
    torquePill: HomePillUi,
    voicePill: HomePillUi,
    onPlayActions: () -> Unit,
    onRecordNew: () -> Unit,
    compact: Boolean,
    phoneDense: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val imgW =
        when {
            phoneDense -> 40.dp
            compact -> 80.dp
            else -> 108.dp
        }
    val imgMaxH =
        when {
            phoneDense -> 48.dp
            compact -> 120.dp
            else -> 168.dp
        }
    val titleSp =
        when {
            phoneDense -> 14.sp
            compact -> 20.sp
            else -> 22.sp
        }
    val blurbLines = if (compact) 2 else 3

    val imgH =
        when {
            phoneDense -> 72.dp
            compact -> 110.dp
            else -> 110.dp
        }
    SirenaCard(
        kind = SirenaCardKind.Hero,
        modifier = modifier,
        contentPadding = PaddingValues(if (phoneDense) 8.dp else 12.dp),
        verticalArrangement = Arrangement.spacedBy(if (phoneDense) 4.dp else 8.dp),
    ) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Image(
                painter = painterResource(R.drawable.nina_robot),
                contentDescription = null,
                modifier =
                    Modifier
                        .width(imgW)
                        .heightIn(max = imgH),
                contentScale = ContentScale.Fit,
            )
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    "Hi, I'm Nina.",
                    fontWeight = FontWeight.Bold,
                    fontSize = if (phoneDense) 16.sp else 20.sp,
                    color = SirenaColors.text,
                )
                SirenaMutedText(
                    "Sirena Robotics · ready when you are.",
                    maxLines = 1,
                )
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    SirenaStatusPill(busPill.text, busPill.kind)
                    SirenaStatusPill(torquePill.text, torquePill.kind)
                    SirenaStatusPill(voicePill.text, voicePill.kind)
                }
            }
            if (!phoneDense) {
                Column(
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    SirenaPrimaryButton(
                        text = "Play actions",
                        onClick = onPlayActions,
                        modifier = Modifier.widthIn(min = 140.dp),
                    )
                    SirenaSecondaryButton(
                        text = "Record new",
                        onClick = onRecordNew,
                        modifier = Modifier.widthIn(min = 140.dp),
                    )
                }
            }
        }
        if (phoneDense) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SirenaPrimaryButton(
                    text = "Play",
                    onClick = onPlayActions,
                    modifier = Modifier.weight(1f),
                )
                SirenaSecondaryButton(
                    text = "Record",
                    onClick = onRecordNew,
                    modifier = Modifier.weight(1f),
                )
            }
        }
    }
}

@Composable
private fun HomeSystemOverviewCard(
    rows: JSONArray?,
    healthLoaded: Boolean,
    onOpenHealth: () -> Unit,
    phoneDense: Boolean,
    jetsonOnline: Boolean,
    modifier: Modifier = Modifier,
) {
    val keys =
        listOf(
            "bus" to "Bus",
            "camera" to "Camera",
            "lidar" to "Lidar",
            "battery" to "Battery",
            "wifi" to "Wi-Fi",
        )
    SirenaCard(
        modifier = modifier.clickable { onOpenHealth() },
        kind = SirenaCardKind.Standard,
        contentPadding = PaddingValues(10.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "System overview",
                fontWeight = FontWeight.Bold,
                fontSize = SirenaType.cardTitle,
                color = SirenaColors.text,
            )
            SirenaMutedText("Tap Health for details", maxLines = 1)
        }
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(if (phoneDense) 4.dp else 8.dp),
        ) {
            keys.forEachIndexed { index, (key, title) ->
                val (cap, kind) = overviewPill(rows, key, title)
                SirenaAnimatedEnter(
                    visible = healthLoaded || !jetsonOnline,
                    modifier = Modifier.weight(1f),
                    delayIndex = index,
                ) {
                    SirenaCard(
                        modifier = Modifier.fillMaxWidth(),
                        kind = SirenaCardKind.Subtle,
                        contentPadding = PaddingValues(8.dp, 6.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        Text(
                            title,
                            fontSize = 10.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = SirenaColors.muted,
                            maxLines = 1,
                        )
                        SirenaStatusPill(cap, kind)
                    }
                }
            }
        }
    }
}

@Composable
private fun HomeQuickTile(
    tile: SirenaQuickAction,
    onNavigate: (String) -> Unit,
    compact: Boolean,
    phoneDense: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val vPad =
        when {
            phoneDense -> 3.dp
            compact -> 6.dp
            else -> 8.dp
        }
    val hPad =
        when {
            phoneDense -> 5.dp
            compact -> 8.dp
            else -> 12.dp
        }
    val glyphSp = if (phoneDense) 22.sp else SirenaType.quickGlyph
    val titleSp = if (phoneDense) 11.sp else SirenaType.quickTitle
    val tileShape = RoundedCornerShape(if (phoneDense) 10.dp else 12.dp)
    SirenaCard(
        modifier =
            modifier
                .fillMaxWidth()
                .then(
                    if (phoneDense) {
                        Modifier.border(1.dp, SirenaColors.red.copy(alpha = 0.35f), tileShape)
                    } else {
                        Modifier
                    },
                )
                .clickable { onNavigate(tile.navKey) },
        contentPadding = PaddingValues(hPad, vPad),
    ) {
        if (phoneDense) {
            Column(
                Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Text(
                    tile.glyph,
                    fontSize = glyphSp,
                    color = SirenaColors.red,
                    fontWeight = FontWeight.Bold,
                    lineHeight = 24.sp,
                )
                Text(
                    tile.label,
                    fontWeight = FontWeight.Bold,
                    fontSize = titleSp,
                    color = SirenaColors.text,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    textAlign = TextAlign.Center,
                )
            }
        } else {
            Text(
                tile.glyph,
                fontSize = glyphSp,
                color = SirenaColors.red,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                tile.label,
                fontWeight = FontWeight.Bold,
                fontSize = titleSp,
                color = SirenaColors.text,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            SirenaMutedText(tile.blurb, maxLines = 2)
        }
    }
}

@Composable
fun SirenaHomeScreen(
    vm: CompanionViewModel,
    state: CompanionUiState,
    jetsonOnline: Boolean,
    onNavigate: (quickActionKey: String) -> Unit,
    shellCompact: Boolean = false,
) {
    val scope = rememberCoroutineScope()
    var healthRows by remember { mutableStateOf<JSONArray?>(null) }
    var driveStatus by remember { mutableStateOf<JSONObject?>(null) }
    var powerState by remember { mutableStateOf<PowerStateUi?>(null) }
    var wakeInFlight by remember { mutableStateOf(false) }
    var wakeMessage by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(jetsonOnline) {
        while (isActive) {
            if (jetsonOnline) {
                try {
                    coroutineScope {
                        launch {
                            try {
                                healthRows = vm.fetchRobotHealth()?.optJSONArray("rows")
                            } catch (e: CancellationException) {
                                throw e
                            } catch (_: Exception) {
                            }
                        }
                        launch {
                            try {
                                driveStatus = vm.fetchRobotDriveStatus()
                            } catch (e: CancellationException) {
                                throw e
                            } catch (_: Exception) {
                            }
                        }
                        launch {
                            try {
                                powerState = vm.fetchPowerState()
                            } catch (e: CancellationException) {
                                throw e
                            } catch (_: Exception) {
                            }
                        }
                    }
                } catch (e: CancellationException) {
                    throw e
                }
            } else {
                healthRows = null
                driveStatus = null
                powerState = null
            }
            delay(5000L)
        }
    }

    val onWakeRobot = {
        wakeInFlight = true
        wakeMessage = null
        vm.requestJetsonWake { err ->
            wakeInFlight = false
            wakeMessage = err ?: "Wake sent."
            scope.launch {
                powerState = vm.fetchPowerState()
            }
        }
    }

    val busPill = remember(healthRows, jetsonOnline) { heroBusPill(healthRows, jetsonOnline) }
    val torquePill = remember(driveStatus, jetsonOnline) { heroTorquePill(driveStatus, jetsonOnline) }
    val voicePill = remember(healthRows, jetsonOnline) { heroVoicePill(healthRows, jetsonOnline) }
    val onPlay = { onNavigate("actions:playback") }
    val onRecord = { onNavigate("actions:record") }
    val onHealth = { onNavigate("health") }

    if (shellCompact) {
        BoxWithConstraints(
            Modifier
                .fillMaxSize()
                .padding(horizontal = 8.dp, vertical = 4.dp),
        ) {
            val gridCols = if (maxWidth >= 600.dp) 4 else 2
            Column(
                Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    "NINA · HOME",
                    color = SirenaColors.grey,
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.1.sp,
                    modifier = Modifier.fillMaxWidth(),
                )
                HomeHeroCard(
                    jetsonOnline = jetsonOnline,
                    busPill = busPill,
                    torquePill = torquePill,
                    voicePill = voicePill,
                    onPlayActions = onPlay,
                    onRecordNew = onRecord,
                    compact = true,
                    phoneDense = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                if (jetsonOnline && powerState?.needsWake == true) {
                    SirenaPowerSaveSection(
                        power = powerState,
                        jetsonOnline = true,
                        wakeInFlight = wakeInFlight,
                        wakeMessage = wakeMessage,
                        onWake = onWakeRobot,
                        compact = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                Text(
                    "QUICK ACTIONS",
                    color = SirenaColors.grey,
                    fontSize = 9.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.1.sp,
                    modifier = Modifier.fillMaxWidth(),
                )
                Column(
                    Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    val rows = SIRENA_QUICK_ACTIONS.chunked(gridCols)
                    rows.forEach { rowTiles ->
                        Row(
                            Modifier
                                .weight(1f)
                                .fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            rowTiles.forEach { tile ->
                                HomeQuickTile(
                                    tile = tile,
                                    onNavigate = onNavigate,
                                    compact = true,
                                    phoneDense = true,
                                    modifier = Modifier.weight(1f).fillMaxHeight(),
                                )
                            }
                            repeat(gridCols - rowTiles.size) {
                                Spacer(Modifier.weight(1f).fillMaxHeight())
                            }
                        }
                    }
                }
            }
        }
    } else {
        BoxWithConstraints(
            Modifier
                .fillMaxSize()
                .padding(horizontal = 12.dp, vertical = 10.dp),
        ) {
            val boxMaxWidth = maxWidth
            val columns = homeQuickActionColumns(boxMaxWidth, shellCompact = false)
            val scroll = rememberScrollState()
            val rowMinH = 84.dp

            Column(
                Modifier
                    .fillMaxSize()
                    .verticalScroll(scroll),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                SirenaBreadcrumbLine(listOf("Nina", "Home"))

                HomeHeroCard(
                    jetsonOnline = jetsonOnline,
                    busPill = busPill,
                    torquePill = torquePill,
                    voicePill = voicePill,
                    onPlayActions = onPlay,
                    onRecordNew = onRecord,
                    compact = boxMaxWidth < 400.dp,
                    phoneDense = false,
                    modifier = Modifier.fillMaxWidth(),
                )

                if (jetsonOnline && powerState?.needsWake == true) {
                    SirenaPowerSaveSection(
                        power = powerState,
                        jetsonOnline = true,
                        wakeInFlight = wakeInFlight,
                        wakeMessage = wakeMessage,
                        onWake = onWakeRobot,
                        compact = false,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }

                SirenaSectionLabel("Quick actions")

                val rows = SIRENA_QUICK_ACTIONS.chunked(columns)
                rows.forEach { rowTiles ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .heightIn(min = rowMinH),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.Top,
                    ) {
                        rowTiles.forEach { tile ->
                            HomeQuickTile(
                                tile = tile,
                                onNavigate = onNavigate,
                                compact = false,
                                phoneDense = false,
                                modifier = Modifier.weight(1f),
                            )
                        }
                        repeat(columns - rowTiles.size) {
                            Spacer(Modifier.weight(1f))
                        }
                    }
                }

                HomeSystemOverviewCard(
                    rows = healthRows,
                    healthLoaded = healthRows != null,
                    onOpenHealth = onHealth,
                    phoneDense = false,
                    jetsonOnline = jetsonOnline,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
            }
        }
    }
}
