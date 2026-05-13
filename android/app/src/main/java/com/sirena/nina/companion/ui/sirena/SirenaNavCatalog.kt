package com.sirena.nina.companion.ui.sirena

/**
 * Mirrors [sirena_ui.widgets.sidebar.NAV_ITEMS] + companion-only entries.
 * Order and glyphs match the Jetson Sirena Control Center sidebar.
 */
object SirenaNavCatalog {

    /** Same keys/order/glyphs as Qt ``NAV_ITEMS`` (tablet shows full nav; some routes are placeholders). */
    val robotNav: List<NavEntry> =
        listOf(
            NavEntry("home", "\u2302", "Home"),
            NavEntry("drive", "\u2B95", "Drive"),
            NavEntry("vision", "\u25CE", "Vision"),
            NavEntry("perception", "\u2299", "Perception"),
            NavEntry("map", "\u25A6", "Map"),
            NavEntry("actions", "\u2630", "Actions"),
            NavEntry("settings", "\u2699", "Settings"),
            NavEntry("health", "\u2665", "Health"),
        )

    /** Below the divider — tablet pairing / Wi‑Fi (not in Qt sidebar; companion-specific). */
    val companionNav: List<NavEntry> =
        listOf(
            NavEntry("find", "\u2315", "Find robot"),
            NavEntry("network", "\u2706", "Network"),
        )

    /** Matches [sirena_ui.main_window.MainWindow._titles] + companion titles. */
    fun headerTitle(navKey: String): String =
        when (navKey) {
            "home" -> "Nina · Home"
            "drive" -> "Nina · Drive"
            "vision" -> "Nina · Vision"
            "perception" -> "Nina · Perception"
            "map" -> "Nina · Map (SLAM)"
            "actions" -> "Nina · Actions"
            "settings" -> "Nina · Settings"
            "health" -> "Nina · Health Check"
            "find" -> "Nina · Find robot"
            "network" -> "Nina · Network"
            "products" -> "Products"
            else -> "Sirena"
        }
}
