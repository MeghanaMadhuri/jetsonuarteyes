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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material.icons.outlined.VisibilityOff
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
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

private fun maskedPlaceholder(len: Int): String {
    val n = len.coerceIn(8, 16)
    return "\u2022".repeat(n)
}

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

@Composable
private fun HomeHeroCard(
    robotDisplayName: String,
    systemId: String?,
    ipv4: String?,
    jetsonOnline: Boolean,
    wifiRole: String?,
    paired: Boolean?,
    compact: Boolean,
    /** Single-screen phone home: minimal vertical chrome. */
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

    if (phoneDense) {
        val pairedLabel =
            when (paired) {
                true -> "yes"
                false -> "no"
                else -> "—"
            }
        val statusInline = "Role ${wifiRole ?: "—"} · Paired $pairedLabel"
        SirenaCard(
            kind = SirenaCardKind.Hero,
            modifier = modifier,
            liquidGlass = true,
            contentPadding = PaddingValues(6.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.Top,
            ) {
                Column(
                    Modifier
                        .weight(1f)
                        .padding(end = 6.dp),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    Text(
                        robotDisplayName,
                        fontWeight = FontWeight.Bold,
                        fontSize = titleSp,
                        color = SirenaColors.text,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (!systemId.isNullOrBlank() || !ipv4.isNullOrBlank()) {
                        var identityRevealed by remember(systemId, ipv4) { mutableStateOf(false) }
                        Row(
                            Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            Column(
                                Modifier
                                    .weight(1f)
                                    .padding(end = 2.dp),
                                verticalArrangement = Arrangement.spacedBy(0.dp),
                            ) {
                                if (!systemId.isNullOrBlank()) {
                                    Text(
                                        text =
                                            if (identityRevealed) {
                                                "ID · $systemId"
                                            } else {
                                                "ID · ${maskedPlaceholder(systemId.length)}"
                                            },
                                        fontWeight = FontWeight.Medium,
                                        fontSize = 10.sp,
                                        color = SirenaColors.muted,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                                if (!ipv4.isNullOrBlank()) {
                                    Text(
                                        text =
                                            if (identityRevealed) {
                                                "IP · $ipv4"
                                            } else {
                                                "IP · ${maskedPlaceholder(ipv4.length)}"
                                            },
                                        fontWeight = FontWeight.Medium,
                                        fontSize = 10.sp,
                                        color = SirenaColors.muted,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                            }
                            IconButton(
                                onClick = { identityRevealed = !identityRevealed },
                                modifier = Modifier.size(32.dp),
                            ) {
                                Icon(
                                    imageVector =
                                        if (identityRevealed) {
                                            Icons.Outlined.VisibilityOff
                                        } else {
                                            Icons.Outlined.Visibility
                                        },
                                    contentDescription =
                                        if (identityRevealed) {
                                            "Hide robot identity"
                                        } else {
                                            "Show robot identity"
                                        },
                                    tint = SirenaColors.muted,
                                )
                            }
                        }
                    }
                    Text(
                        text =
                            if (jetsonOnline) {
                                "Daemon live · controls ready."
                            } else {
                                "Offline · use Find robot or Network."
                            },
                        fontSize = 11.sp,
                        color = SirenaColors.muted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        text = statusInline,
                        fontSize = 11.sp,
                        color = SirenaColors.muted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Column(
                    horizontalAlignment = Alignment.End,
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    SirenaStatusPill(
                        text = if (jetsonOnline) "Link OK" else "Offline",
                        kind = if (jetsonOnline) SirenaPillKind.Ok else SirenaPillKind.Neutral,
                    )
                    Image(
                        painter = painterResource(R.drawable.nina_robot),
                        contentDescription = null,
                        modifier =
                            Modifier
                                .width(imgW)
                                .heightIn(max = imgMaxH),
                        contentScale = ContentScale.Fit,
                    )
                }
            }
        }
    } else {
        SirenaCard(kind = SirenaCardKind.Hero, modifier = modifier, liquidGlass = true) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.Top,
        ) {
            Column(
                Modifier
                    .weight(1f)
                    .padding(end = 8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    robotDisplayName,
                    fontWeight = FontWeight.Bold,
                    fontSize = titleSp,
                    color = SirenaColors.text,
                    maxLines = 1,
                )
                if (!systemId.isNullOrBlank() || !ipv4.isNullOrBlank()) {
                    var identityRevealed by remember(systemId, ipv4) { mutableStateOf(false) }
                    Row(
                        Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Column(
                            Modifier
                                .weight(1f)
                                .padding(end = 4.dp),
                            verticalArrangement = Arrangement.spacedBy(2.dp),
                        ) {
                            if (!systemId.isNullOrBlank()) {
                                Text(
                                    text =
                                        if (identityRevealed) {
                                            "System ID · $systemId"
                                        } else {
                                            "System ID · ${maskedPlaceholder(systemId.length)}"
                                        },
                                    fontWeight = FontWeight.Medium,
                                    fontSize = SirenaType.muted,
                                    color = SirenaColors.muted,
                                    maxLines = if (compact) 1 else 2,
                                )
                            }
                            if (!ipv4.isNullOrBlank()) {
                                Text(
                                    text =
                                        if (identityRevealed) {
                                            "IPv4 · $ipv4"
                                        } else {
                                            "IPv4 · ${maskedPlaceholder(ipv4.length)}"
                                        },
                                    fontWeight = FontWeight.Medium,
                                    fontSize = SirenaType.muted,
                                    color = SirenaColors.muted,
                                    maxLines = 1,
                                )
                            }
                        }
                        IconButton(
                            onClick = { identityRevealed = !identityRevealed },
                            modifier = Modifier.size(40.dp),
                        ) {
                            Icon(
                                imageVector =
                                    if (identityRevealed) {
                                        Icons.Outlined.VisibilityOff
                                    } else {
                                        Icons.Outlined.Visibility
                                    },
                                contentDescription =
                                    if (identityRevealed) {
                                        "Hide robot identity"
                                    } else {
                                        "Show robot identity"
                                    },
                                tint = SirenaColors.muted,
                            )
                        }
                    }
                }
                SirenaMutedText(
                    if (jetsonOnline) {
                        "Link daemon reachable — companion controls are live."
                    } else {
                        "Not connected — open Find robot, the product hub radar, or check Network."
                    },
                    maxLines = blurbLines,
                )
                Spacer(Modifier.height(8.dp))
                HorizontalDivider(color = SirenaColors.rule.copy(alpha = 0.55f))
                Spacer(Modifier.height(8.dp))
                Text(
                    "Live status",
                    fontWeight = FontWeight.SemiBold,
                    color = SirenaColors.text,
                    fontSize = SirenaType.base,
                )
                Spacer(Modifier.height(4.dp))
                SirenaMutedText("Role: ${wifiRole ?: "—"}")
                SirenaMutedText("Paired: ${if (paired == true) "yes" else if (paired == false) "no" else "—"}")
            }
            Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(6.dp)) {
                SirenaStatusPill(
                    text = if (jetsonOnline) "Link OK" else "Offline",
                    kind = if (jetsonOnline) SirenaPillKind.Ok else SirenaPillKind.Neutral,
                )
                Image(
                    painter = painterResource(R.drawable.nina_robot),
                    contentDescription = null,
                    modifier =
                        Modifier
                            .width(imgW)
                            .heightIn(max = imgMaxH),
                    contentScale = ContentScale.Fit,
                )
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
        liquidGlass = true,
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
    state: CompanionUiState,
    jetsonOnline: Boolean,
    robotDisplayName: String,
    systemId: String?,
    ipv4: String?,
    onNavigate: (quickActionKey: String) -> Unit,
    /** When true, shell uses drawer + full-width body — home fits the viewport without scrolling. */
    shellCompact: Boolean = false,
) {
    val ready = state as? CompanionUiState.Ready
    val st = ready?.status

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
                    robotDisplayName = robotDisplayName,
                    systemId = systemId,
                    ipv4 = ipv4,
                    jetsonOnline = jetsonOnline,
                    wifiRole = st?.wifiRole,
                    paired = st?.paired,
                    compact = true,
                    phoneDense = true,
                    modifier = Modifier.fillMaxWidth(),
                )
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
                    robotDisplayName = robotDisplayName,
                    systemId = systemId,
                    ipv4 = ipv4,
                    jetsonOnline = jetsonOnline,
                    wifiRole = st?.wifiRole,
                    paired = st?.paired,
                    compact = boxMaxWidth < 400.dp,
                    phoneDense = false,
                    modifier = Modifier.fillMaxWidth(),
                )

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
                Spacer(Modifier.height(8.dp))
            }
        }
    }
}
