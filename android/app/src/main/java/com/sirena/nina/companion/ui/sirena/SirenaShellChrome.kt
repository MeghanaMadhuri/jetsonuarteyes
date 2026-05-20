package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.TextButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupProperties
import com.sirena.nina.companion.CompanionViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.widthIn
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sirena.nina.companion.R
import com.sirena.nina.companion.util.NinaLog

/** Qt-checked row surface on charcoal nav ([sirena_ui.styles] `BRAND_CHARCOAL_ACTIVE`). */
private val SidebarRowActive = SirenaColors.navRailRowActive

/** Solid dark sidebar — flat fill, single right edge (no gradient). */
private fun Modifier.sirenaNavRailSolid(): Modifier =
    this
        .background(SirenaColors.navRailSolid)
        .drawBehind {
            val w = size.width
            val h = size.height
            val stroke = 1.dp.toPx()
            drawLine(
                color = SirenaColors.navRailEdge,
                start = Offset(w - stroke, 0f),
                end = Offset(w - stroke, h),
                strokeWidth = stroke,
            )
        }

data class NavEntry(
    val key: String,
    val glyph: String,
    val label: String,
)

/**
 * Main nav rail — **expanded** (glyph + label) on tablets, **compact** (glyph-first) on phones / tight landscape.
 */
@Composable
fun SirenaSidebar(
    robotItems: List<NavEntry>,
    companionItems: List<NavEntry>,
    selectedKey: String,
    onSelect: (String) -> Unit,
    versionLabel: String,
    hostLabel: String,
    compact: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val railW = if (compact) SirenaDimens.sidebarWidthCompact else SirenaDimens.sidebarWidthExpanded
    Box(
        modifier
            .width(railW)
            .fillMaxHeight(),
    ) {
        Column(
            Modifier
                .fillMaxSize()
                .sirenaNavRailSolid(),
        ) {
            SirenaSidebarBrandRow(compact = compact)
            Spacer(Modifier.padding(vertical = if (compact) 2.dp else 4.dp))
            Column(
                Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState()),
            ) {
                robotItems.forEach { entry ->
                    SidebarNavRow(
                        entry = entry,
                        selected = selectedKey == entry.key,
                        compact = compact,
                        onClick = {
                            NinaLog.tap("Sidebar", "nav", entry.key)
                            onSelect(entry.key)
                        },
                    )
                }
                Spacer(Modifier.padding(vertical = if (compact) 4.dp else 6.dp))
                Box(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = if (compact) 6.dp else 12.dp, vertical = 4.dp)
                        .height(1.dp)
                        .background(SirenaColors.navRailEdgeSoft),
                )
                companionItems.forEach { entry ->
                    SidebarNavRow(
                        entry = entry,
                        selected = selectedKey == entry.key,
                        compact = compact,
                        companionSection = true,
                        onClick = {
                            NinaLog.tap("Sidebar", "nav", entry.key)
                            onSelect(entry.key)
                        },
                    )
                }
            }
            val footer =
                if (hostLabel.isNotBlank()) {
                    "$versionLabel · $hostLabel"
                } else {
                    versionLabel
                }
            Text(
                footer,
                modifier =
                    Modifier
                        .fillMaxWidth()
                        .padding(if (compact) 4.dp else 8.dp),
                fontSize = if (compact) 9.sp else SirenaType.quickBlurb,
                color = SirenaColors.navRailTextSecondary,
                textAlign = TextAlign.Center,
                maxLines = if (compact) 3 else 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
internal fun SirenaSidebarBrandRow(compact: Boolean) {
    if (compact) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 4.dp, vertical = 4.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Image(
                painter = painterResource(R.drawable.sirena_logo),
                contentDescription = "Sirena",
                modifier =
                    Modifier
                        .heightIn(max = 20.dp)
                        .widthIn(max = 48.dp),
                contentScale = ContentScale.Fit,
            )
        }
    } else {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 6.dp),
        ) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Image(
                    painter = painterResource(R.drawable.sirena_logo),
                    contentDescription = null,
                    modifier =
                        Modifier
                            .heightIn(max = 22.dp)
                            .widthIn(max = 72.dp),
                    contentScale = ContentScale.Fit,
                )
                Text(
                    "Sirena",
                    color = SirenaColors.navRailTextPrimary,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
            Spacer(Modifier.height(6.dp))
            Box(
                Modifier
                    .fillMaxWidth(0.55f)
                    .height(2.dp)
                    .clip(RoundedCornerShape(1.dp))
                    .background(
                        Brush.horizontalGradient(
                            colors =
                                listOf(
                                    SirenaColors.red.copy(alpha = 0.9f),
                                    SirenaColors.red.copy(alpha = 0.15f),
                                ),
                        ),
                    ),
            )
        }
    }
}

@Composable
internal fun SidebarNavRow(
    entry: NavEntry,
    selected: Boolean,
    compact: Boolean = false,
    companionSection: Boolean = false,
    onClick: () -> Unit,
) {
    val pillShape = RoundedCornerShape(10.dp)
    val labelColor =
        if (selected) {
            SirenaColors.red
        } else {
            SirenaColors.navRailTextPrimary.copy(alpha = 0.92f)
        }
    val letterSpacing = if (companionSection) 0.3.sp else 0.sp
    val labelText =
        if (compact) {
            entry.glyph.trim().ifEmpty { entry.label.take(1) }
        } else {
            "${entry.glyph}  ${entry.label}"
        }
    val rowBg by animateColorAsState(
        targetValue = if (selected) SidebarRowActive else Color.Transparent,
        animationSpec = spring(stiffness = Spring.StiffnessMediumLow),
        label = "navRowBg",
    )
    val rowScale by animateFloatAsState(
        targetValue = if (selected) 1.02f else 1f,
        animationSpec = spring(stiffness = Spring.StiffnessMediumLow),
        label = "navRowScale",
    )
    Row(
        modifier =
            Modifier
                .fillMaxWidth()
                .scale(rowScale)
                .padding(horizontal = if (compact) 2.dp else 6.dp, vertical = if (compact) 2.dp else 3.dp)
                .heightIn(min = if (compact) 40.dp else 42.dp)
                .clip(pillShape)
                .background(rowBg)
                .clickable(onClick = onClick),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = if (compact) Arrangement.Center else Arrangement.Start,
    ) {
        if (!compact) {
            Box(
                Modifier
                    .width(3.dp)
                    .fillMaxHeight()
                    .background(if (selected) SirenaColors.red else Color.Transparent),
            )
        }
        Text(
            text = labelText,
            modifier =
                Modifier
                    .then(if (compact) Modifier else Modifier.weight(1f))
                    .padding(vertical = if (compact) 6.dp else 9.dp, horizontal = if (compact) 0.dp else 2.dp),
            color = labelColor,
            fontSize = if (compact) 17.sp else 13.5.sp,
            letterSpacing = letterSpacing,
            fontWeight =
                if (selected) {
                    FontWeight.SemiBold
                } else {
                    FontWeight.Medium
                },
            maxLines = 1,
            overflow = TextOverflow.Clip,
            textAlign = if (compact) TextAlign.Center else TextAlign.Start,
        )
    }
}

/**
 * Full-width labels for [ModalDrawerSheet] — same destinations as [SirenaSidebar], tap-only navigation.
 */
@Composable
fun SirenaNavDrawerSheetContent(
    robotItems: List<NavEntry>,
    companionItems: List<NavEntry>,
    selectedKey: String,
    onSelect: (String) -> Unit,
    versionLabel: String,
    hostLabel: String,
) {
    Column(
        Modifier
            .fillMaxSize()
            .sirenaNavRailSolid(),
    ) {
        SirenaSidebarBrandRow(compact = false)
        Spacer(Modifier.height(4.dp))
        Column(
            Modifier
                .weight(1f)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState()),
        ) {
            robotItems.forEach { entry ->
                SidebarNavRow(
                    entry = entry,
                    selected = selectedKey == entry.key,
                    compact = false,
                    onClick = {
                        NinaLog.tap("NavDrawer", "nav", entry.key)
                        onSelect(entry.key)
                    },
                )
            }
            Spacer(Modifier.padding(vertical = 6.dp))
            Box(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 4.dp)
                    .height(1.dp)
                    .background(
                        Brush.horizontalGradient(
                            colors =
                                listOf(
                                    Color.Transparent,
                                    SirenaColors.navRailEdgeSoft,
                                    Color.Transparent,
                                ),
                        ),
                    ),
            )
            companionItems.forEach { entry ->
                SidebarNavRow(
                    entry = entry,
                    selected = selectedKey == entry.key,
                    compact = false,
                    companionSection = true,
                    onClick = {
                        NinaLog.tap("NavDrawer", "nav", entry.key)
                        onSelect(entry.key)
                    },
                )
            }
        }
        val footer =
            if (hostLabel.isNotBlank()) {
                "$versionLabel · $hostLabel"
            } else {
                versionLabel
            }
        Text(
            footer,
            modifier =
                Modifier
                    .fillMaxWidth()
                    .padding(12.dp),
            fontSize = SirenaType.quickBlurb,
            color = SirenaColors.navRailTextSecondary,
            textAlign = TextAlign.Center,
            maxLines = 3,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/**
 * Red header — title, link status, Jetson volume, overflow menu (health export, Wi‑Fi).
 */
@Composable
fun SirenaShellHeader(
    title: String,
    clockText: String,
    vm: CompanionViewModel,
    connectedLabel: String? = null,
    jetsonOnline: Boolean = false,
    showProductHubBack: Boolean = false,
    onProductHubBack: (() -> Unit)? = null,
    showNavDrawerMenu: Boolean = false,
    onNavDrawerMenuClick: (() -> Unit)? = null,
    onExportHealthShare: () -> Unit,
    onExportHealthDownload: () -> Unit,
    onOpenWifiSettings: () -> Unit,
    compact: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val scope = rememberCoroutineScope()
    var menuOpen by remember { mutableStateOf(false) }
    var exportDialogOpen by remember { mutableStateOf(false) }
    var volumeOpen by remember { mutableStateOf(false) }
    var jetsonVolume by remember { mutableFloatStateOf(70f) }
    var volumeAvailable by remember { mutableStateOf(false) }
    var volumeBusy by remember { mutableStateOf(false) }

    LaunchedEffect(volumeOpen, jetsonOnline) {
        if (!volumeOpen || !jetsonOnline) return@LaunchedEffect
        while (true) {
            val pct = vm.fetchSystemVolumePct()
            if (pct != null) {
                volumeAvailable = true
                if (!volumeBusy) {
                    jetsonVolume = pct.toFloat()
                }
            } else {
                volumeAvailable = false
            }
            delay(900L)
        }
    }

    LaunchedEffect(jetsonVolume, volumeOpen, volumeAvailable) {
        if (!volumeOpen || !volumeAvailable || !jetsonOnline) return@LaunchedEffect
        delay(220L)
        volumeBusy = true
        vm.setSystemVolumePct(jetsonVolume.toInt())
        volumeBusy = false
    }

    val trimmed = connectedLabel?.trim()?.takeIf { it.isNotEmpty() }
    val robotLine =
        if (jetsonOnline) {
            "${trimmed ?: "Connected"} · Live"
        } else {
            "No connection"
        }
    val trailingReserve = if (compact) 156.dp else 248.dp

    Box(
        modifier
            .fillMaxWidth()
            .height(SirenaDimens.headerBarHeight)
            .background(
                Brush.verticalGradient(
                    colors =
                        listOf(
                            SirenaColors.red.copy(alpha = 0.98f),
                            SirenaColors.red,
                            SirenaColors.redDark,
                        ),
                ),
            )
            .drawBehind {
                val stroke = 1.dp.toPx()
                drawLine(
                    color = Color.White.copy(alpha = 0.18f),
                    start = Offset(0f, 1f),
                    end = Offset(size.width, 1f),
                    strokeWidth = stroke,
                )
                drawLine(
                    color = Color.Black.copy(alpha = 0.12f),
                    start = Offset(0f, size.height - stroke),
                    end = Offset(size.width, size.height - stroke),
                    strokeWidth = stroke,
                )
            }
            .padding(horizontal = 6.dp, vertical = 4.dp),
    ) {
        val leadingActionsDp =
            (if (showProductHubBack && onProductHubBack != null) 40 else 0) +
                (if (showNavDrawerMenu && onNavDrawerMenuClick != null) 40 else 0)
        Row(
            Modifier.align(Alignment.CenterStart),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (showProductHubBack && onProductHubBack != null) {
                IconButton(onClick = onProductHubBack, modifier = Modifier.size(38.dp)) {
                    Icon(
                        Icons.AutoMirrored.Filled.ArrowBack,
                        contentDescription = "Back to products",
                        tint = SirenaColors.white,
                    )
                }
            }
            if (showNavDrawerMenu && onNavDrawerMenuClick != null) {
                IconButton(onClick = onNavDrawerMenuClick, modifier = Modifier.size(38.dp)) {
                    Icon(Icons.Filled.Menu, contentDescription = "Open menu", tint = SirenaColors.white)
                }
            }
        }
        Text(
            title,
            modifier =
                Modifier
                    .align(Alignment.Center)
                    .fillMaxWidth()
                    .padding(
                        start = if (leadingActionsDp == 0) 10.dp else leadingActionsDp.dp,
                        end = trailingReserve,
                    ),
            fontSize = if (compact) 15.sp else SirenaType.headerTitle,
            fontWeight = FontWeight.Bold,
            color = SirenaColors.white,
            textAlign = TextAlign.Center,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Row(
            Modifier.align(Alignment.CenterEnd),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            if (!compact) {
                Box(
                    Modifier
                        .size(8.dp)
                        .clip(CircleShape)
                        .background(if (jetsonOnline) SirenaColors.success else SirenaColors.danger),
                )
                Text(
                    robotLine,
                    color = SirenaColors.white.copy(alpha = 0.95f),
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.widthIn(max = 100.dp),
                )
            }
            Text(
                clockText,
                color = SirenaColors.white.copy(alpha = 0.92f),
                fontSize = SirenaType.headerClock,
                fontWeight = FontWeight.Medium,
                modifier = Modifier.padding(horizontal = 4.dp),
            )
            Box {
                IconButton(
                    onClick = {
                        volumeOpen = !volumeOpen
                        menuOpen = false
                        NinaLog.tap("Header", "volume", if (volumeOpen) "open" else "close")
                    },
                    enabled = jetsonOnline,
                    modifier = Modifier.size(38.dp),
                ) {
                    Icon(
                        Icons.Filled.VolumeUp,
                        contentDescription = "Jetson volume",
                        tint = if (jetsonOnline) SirenaColors.white else SirenaColors.white.copy(alpha = 0.45f),
                    )
                }
                if (volumeOpen) {
                    Popup(
                        alignment = Alignment.TopEnd,
                        onDismissRequest = { volumeOpen = false },
                        properties = PopupProperties(focusable = true),
                    ) {
                        SirenaCard(
                            modifier = Modifier.widthIn(min = 240.dp, max = 300.dp).padding(top = 4.dp, end = 4.dp),
                            kind = SirenaCardKind.Standard,
                            contentPadding = PaddingValues(14.dp),
                        ) {
                            Text("Jetson speaker", fontWeight = FontWeight.Bold, color = SirenaColors.text)
                            SirenaMutedText(
                                if (volumeAvailable) "System output volume" else "Volume control unavailable on robot",
                                maxLines = 2,
                            )
                            Slider(
                                value = jetsonVolume,
                                onValueChange = { jetsonVolume = it },
                                enabled = jetsonOnline && volumeAvailable && !volumeBusy,
                                valueRange = 0f..100f,
                                colors =
                                    SliderDefaults.colors(
                                        thumbColor = SirenaColors.red,
                                        activeTrackColor = SirenaColors.red,
                                        inactiveTrackColor = SirenaColors.border,
                                    ),
                            )
                            Text(
                                "${jetsonVolume.toInt()}%",
                                color = SirenaColors.muted,
                                fontSize = SirenaType.muted,
                            )
                        }
                    }
                }
            }
            Box {
                IconButton(
                    onClick = {
                        menuOpen = true
                        volumeOpen = false
                        NinaLog.tap("Header", "menu", "open")
                    },
                    modifier = Modifier.size(38.dp),
                ) {
                    Icon(Icons.Filled.MoreVert, contentDescription = "More options", tint = SirenaColors.white)
                }
                DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                    DropdownMenuItem(
                        text = { Text("Export health report") },
                        onClick = {
                            menuOpen = false
                            exportDialogOpen = true
                            NinaLog.tap("Header", "export_health", "open")
                        },
                    )
                    HorizontalDivider()
                    DropdownMenuItem(
                        text = { Text("Change Wi‑Fi") },
                        leadingIcon = { Icon(Icons.Filled.Wifi, contentDescription = null) },
                        onClick = {
                            menuOpen = false
                            onOpenWifiSettings()
                        },
                    )
                }
            }
        }
    }

    if (exportDialogOpen) {
        AlertDialog(
            onDismissRequest = { exportDialogOpen = false },
            title = { Text("Export health report", fontWeight = FontWeight.Bold) },
            text = {
                Text(
                    "Save a JSON snapshot of subsystem health to this device, or share it via WhatsApp, email, or any app.",
                    color = SirenaColors.text,
                    fontSize = SirenaType.muted,
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        exportDialogOpen = false
                        onExportHealthDownload()
                    },
                ) {
                    Text("Download", color = SirenaColors.red, fontWeight = FontWeight.SemiBold)
                }
            },
            dismissButton = {
                TextButton(
                    onClick = {
                        exportDialogOpen = false
                        onExportHealthShare()
                    },
                ) {
                    Text("Share", color = SirenaColors.red)
                }
            },
        )
    }
}
