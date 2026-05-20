package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.CompanionViewModel
import org.json.JSONObject

/**
 * Map / SLAM navigation — companion preview is not ready yet; show coming-soon gate.
 */
@Composable
fun SirenaMapScreen(
    vm: CompanionViewModel,
    daemonUrl: String?,
    caps: JSONObject? = null,
    shellCompact: Boolean = false,
) {
    @Suppress("UNUSED_PARAMETER")
    val _unused = listOf(vm, daemonUrl, caps, shellCompact)

    SirenaComingSoonGate(
        title = "Map & navigation",
        message = "Occupancy grid, tap-to-goal, and autonomous routing are coming soon on the companion app.",
        modifier = Modifier.fillMaxSize(),
    ) {
        Column(
            Modifier
                .fillMaxSize()
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            SirenaBreadcrumbLine(listOf("Nina", "Map"))
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                SirenaStatusPill("SLAM", SirenaPillKind.Neutral)
                SirenaStatusPill("Goals", SirenaPillKind.Neutral)
                SirenaStatusPill("Auto", SirenaPillKind.Neutral)
            }
            Row(
                Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Box(
                    Modifier
                        .weight(0.62f)
                        .fillMaxSize()
                        .clip(RoundedCornerShape(10.dp))
                        .background(SirenaColors.panel),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        "Occupancy grid",
                        color = SirenaColors.muted,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                Column(
                    Modifier
                        .weight(0.38f)
                        .fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    repeat(3) {
                        Box(
                            Modifier
                                .fillMaxWidth()
                                .height(72.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(SirenaColors.panel),
                        )
                    }
                }
            }
        }
    }
}
