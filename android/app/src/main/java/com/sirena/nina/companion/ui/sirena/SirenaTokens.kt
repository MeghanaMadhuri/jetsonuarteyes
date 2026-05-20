package com.sirena.nina.companion.ui.sirena

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Mirrors [sirena_ui.styles] — single source of truth for Compose to match Qt pixel-for-pixel intent.
 */
object SirenaColors {
    val red = Color(0xFFC8102E)
    val redDark = Color(0xFF9B0C23)
    val redHover = Color(0xFFDC2741)
    val redTint = Color(0xFFFBE7EB)
    val white = Color(0xFFFFFFFF)
    val cloud = Color(0xFFF5F5F7)
    /**
     * Qt `QFrame#sidebar` / [sirena_ui.styles] `BRAND_CHARCOAL` — nav rail fallback (opaque).
     * Prefer [navRailSolid] for the live shell sidebar.
     */
    val navRail = Color(0xFF2C2C2E)
    /** Solid dark rail (no gradient / translucency). */
    val navRailSolid = Color(0xFF1A1A1C)
    /** @deprecated Use [navRailSolid] — kept for callers not yet migrated. */
    val navRailGlassBase = navRailSolid
    /** Qt `BRAND_CHARCOAL_ACTIVE` / checked nav row. */
    val navRailRowActive = Color(0xFF3A3A3C)
    /** Primary labels on dark nav (Qt sidebar text). */
    val navRailTextPrimary = Color(0xFFF2F2F7)
    /** Qt `QLabel#sidebarFooter` #9a9a9f */
    val navRailTextSecondary = Color(0xFF9A9A9F)
    /** Hairlines + row separators on dark glass. */
    val navRailEdge = Color.White.copy(alpha = 0.14f)
    val navRailEdgeSoft = Color.White.copy(alpha = 0.08f)
    val panel = Color(0xFFFFFFFF)
    val border = Color(0xFFE3E3E6)
    val text = Color(0xFF1C1C1E)
    val muted = Color(0xFF6E6E73)
    val grey = Color(0xFF8E8E93)
    val charcoal = Color(0xFF2C2C2E)
    val success = Color(0xFF2ECC71)
    val warning = Color(0xFFF5A623)
    val danger = Color(0xFFE74C3C)
    val pillOkBg = Color(0xFFE7F7EE)
    val pillOkFg = Color(0xFF1F8A4C)
    val pillWarnBg = Color(0xFFFFF5E0)
    val pillWarnFg = Color(0xFFA86A00)
    val pillErrorBg = Color(0xFFFDE7E9)
    val pillErrorFg = Color(0xFFC8102E)
    val pillNeutralBg = Color(0xFFECECEF)
    val pillNeutralFg = Color(0xFF6E6E73)
    val toggleOffBg = Color(0xFFECECEF)
    /** HRule, subtle dividers */
    val rule = Color(0xFFE3E3E6)
    /** Desktop breadcrumb / section secondary */
    val breadcrumb = Color(0xFF8E8E93)
    /** Muted callout panels (capabilities hints) */
    val calloutBg = Color(0xFFE8E8ED)
    val disabledFill = Color(0xFFD8D8DB)
    val disabledText = Color(0xFF9A9A9F)

    /**
     * “Liquid glass” — higher translucency + Sirena rose/red body tint so panels read over [cloud].
     * Tune toward reference: specular (bright rim), body (soft milky + red wash), depth (charcoal vignette).
     */
    val glassPanel = Color(0xA8FFFFFF)
    val glassPanelSubtle = Color(0x92F5F5F7)
    val glassBodyTint = Color(0x55FBE7EB)
    val glassBorder = Color(0x7AFFFFFF)
    val glassBorderTint = Color(0x55C8102E)
    val glassSpecular = Color(0xE6FFFFFF)
    val glassSpecularSoft = Color(0x66FFFFFF)
    val glassInnerShadow = Color(0x381C1C1E)
    val glassShadow = Color(0x331C1C1E)
    val glassShadowTint = Color(0x289B0C23)
}

object SirenaDimens {
    val cardRadius = 14.dp
    val cardRadiusSubtle = 12.dp
    val cardRadiusHero = 18.dp
    val cardInnerPad = 12.dp
    val cardSpacing = 8.dp
    val quickTileMinHeight = 80.dp
    val quickTileHPad = 12.dp
    val quickTileVPad = 8.dp
    val headerBarHeight = 50.dp
    val dpadMin = 60.dp
    val primaryButtonRadius = 14.dp
    val secondaryButtonRadius = 12.dp
    /** Sidebar full width (tablets / wide landscape). */
    val sidebarWidthExpanded = 160.dp
    /** Icon-first rail for phones and tight landscape. */
    val sidebarWidthCompact = 76.dp
}

/** Shell layout: below this width use compact sidebar + tighter home grid. */
val SirenaBreakpointCompactWidth = 720.dp

/**
 * Short window edge (dp). Catches landscape phones and narrow split windows when combined with width.
 */
val SirenaBreakpointCompactHeight = 500.dp

/**
 * Same idea as `-sw600dp` / Material **compact** window: smallest screen dimension in **dp**,
 * stable across orientation. Use for phone vs tablet so **sensorLandscape** phones are not misclassified
 * as tablets (wide × ~430dp window).
 */
const val SirenaBreakpointCompactSmallestWidthDp = 600

object SirenaType {
    val base = 14.sp
    val cardTitle = 15.sp
    val sectionLabel = 11.sp
    val sectionLabelLetterSpacing = 1.5.sp
    val muted = 12.sp
    val breadcrumb = 13.sp
    val quickGlyph = 18.sp
    val quickTitle = 14.sp
    val quickBlurb = 11.sp
    val headerTitle = 20.sp
    val headerTray = 16.sp
    val headerClock = 14.sp
    val pill = 12.sp
}
