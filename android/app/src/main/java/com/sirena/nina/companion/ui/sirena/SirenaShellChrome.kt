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
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
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

/**
 * Charcoal glass rail — frosted stack (translucent base + vertical frost + hairlines),
 * aligned with desktop Sirena sidebar intent.
 */
private fun Modifier.sirenaNavRailGlassMorph(): Modifier =
    this
        .background(SirenaColors.navRailGlassBase)
        .drawBehind {
            val w = size.width
            val h = size.height
            drawRect(
                brush =
                    Brush.verticalGradient(
                        colors =
                            listOf(
                                Color.White.copy(alpha = 0.16f),
                                Color.White.copy(alpha = 0.04f),
                                Color.Transparent,
                                Color.Black.copy(alpha = 0.34f),
                            ),
                        startY = 0f,
                        endY = h,
                    ),
                size = size,
            )
            val stroke = 1.dp.toPx()
            drawLine(
                color = SirenaColors.navRailEdge,
                start = Offset(0f, stroke * 0.5f),
                end = Offset(w, stroke * 0.5f),
                strokeWidth = stroke,
            )
            drawLine(
                color = SirenaColors.navRailEdgeSoft,
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
                .sirenaNavRailGlassMorph(),
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
    Row(
        modifier =
            Modifier
                .fillMaxWidth()
                .padding(horizontal = if (compact) 2.dp else 6.dp, vertical = if (compact) 2.dp else 3.dp)
                .heightIn(min = if (compact) 40.dp else 42.dp)
                .clip(pillShape)
                .background(if (selected) SidebarRowActive else Color.Transparent)
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
            .sirenaNavRailGlassMorph(),
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
 * Red header with optional back to product hub, centered title, robot name on one line, clock.
 * Title and trailing cluster use horizontal insets so long labels ellipsize instead of overlapping.
 */
@Composable
fun SirenaShellHeader(
    title: String,
    clockText: String,
    connectedLabel: String? = null,
    jetsonOnline: Boolean = false,
    showProductHubBack: Boolean = false,
    onProductHubBack: (() -> Unit)? = null,
    /** Hamburger opens [ModalNavigationDrawer] when the persistent sidebar is hidden (phones). */
    showNavDrawerMenu: Boolean = false,
    onNavDrawerMenuClick: (() -> Unit)? = null,
    compact: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val trimmed = connectedLabel?.trim()?.takeIf { it.isNotEmpty() }
    val robotLine =
        if (jetsonOnline) {
            "${trimmed ?: "Connected"} · Live"
        } else {
            "No connection"
        }
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
                    color = Color.Black.copy(alpha = 0.1f),
                    start = Offset(0f, size.height - stroke),
                    end = Offset(size.width, size.height - stroke),
                    strokeWidth = stroke,
                )
            }
            .padding(start = 4.dp, top = 4.dp, end = 8.dp, bottom = 4.dp),
    ) {
        val leadingActionsDp =
            (if (showProductHubBack && onProductHubBack != null) 44 else 0) +
                (if (showNavDrawerMenu && onNavDrawerMenuClick != null) 44 else 0)
        val titleStartPadding = if (leadingActionsDp == 0) 8.dp else leadingActionsDp.dp
        Row(
            Modifier.align(Alignment.CenterStart),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (showProductHubBack && onProductHubBack != null) {
                IconButton(
                    onClick = onProductHubBack,
                    modifier = Modifier.size(40.dp),
                ) {
                    Icon(
                        imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                        contentDescription = "Back to products",
                        tint = SirenaColors.white,
                    )
                }
            }
            if (showNavDrawerMenu && onNavDrawerMenuClick != null) {
                IconButton(
                    onClick = onNavDrawerMenuClick,
                    modifier = Modifier.size(40.dp),
                ) {
                    Icon(
                        imageVector = Icons.Filled.Menu,
                        contentDescription = "Open navigation menu",
                        tint = SirenaColors.white,
                    )
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
                        start = titleStartPadding,
                        end = if (compact) 108.dp else 212.dp,
                    ),
            fontSize = if (compact) 15.sp else SirenaType.headerTitle,
            fontWeight = FontWeight.SemiBold,
            color = SirenaColors.white,
            textAlign = TextAlign.Center,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Row(
            Modifier
                .align(Alignment.CenterEnd)
                .widthIn(max = if (compact) 132.dp else 204.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                Modifier
                    .size(8.dp)
                    .clip(CircleShape)
                    .background(if (jetsonOnline) SirenaColors.success else SirenaColors.danger),
            )
            Text(
                robotLine,
                color = SirenaColors.white,
                fontSize = if (compact) 10.sp else 12.sp,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = if (compact) 72.dp else 132.dp),
            )
            Text(
                clockText,
                color = SirenaColors.white,
                fontSize = SirenaType.headerClock,
                modifier = Modifier.widthIn(min = 44.dp),
            )
            Text(
                "\u22EE",
                color = SirenaColors.white,
                fontSize = SirenaType.headerTray,
                modifier =
                    Modifier
                        .clickable { NinaLog.tap("Header", "menu", "kebab") }
                        .padding(start = 2.dp),
            )
        }
    }
}
