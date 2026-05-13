package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * Desktop-only sections on the Jetson Qt app; companion shows the same chrome
 * with guidance to use the robot UI for full control.
 */
@Composable
fun SirenaPlaceholderScreen(
    navKey: String,
    breadcrumbSecond: String,
) {
    Column(
        Modifier
            .fillMaxSize()
            .padding(10.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        SirenaBreadcrumbLine(listOf("Nina", breadcrumbSecond))
        SirenaCard {
            SirenaMutedText(
                "This section matches the Sirena Control Center on the Jetson (${navKey}). " +
                    "Use the touchscreen or VNC on the robot for full ${breadcrumbSecond} tools " +
                    "while the companion handles Drive, Vision, Perception, Map, and Wi‑Fi over HTTP.",
                maxLines = 8,
            )
        }
    }
}
