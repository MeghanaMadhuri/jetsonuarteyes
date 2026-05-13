package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * Responsive layout derived from the **content area** max width/height (after sidebar, etc.).
 * Wide windows keep Qt-style multi-column rows; narrow windows stack and scroll so UI stays usable.
 */
data class SirenaLayoutContext(
    val maxWidth: Dp,
    val maxHeight: Dp,
    /** LiDAR / RGB / Depth in one row (desktop Perception). */
    val perceptionTriple: Boolean,
    /** Map card + side rail side-by-side (~60/40). */
    val mapSplit: Boolean,
    /** Camera card + recognition rail side-by-side (~62/38). */
    val visionSplit: Boolean,
    /** Breadcrumb + pills on one row; if false, pills move to a second scrollable row. */
    val headerSingleRow: Boolean,
    /** Primary + muted footer on one row (Perception autonomy row). */
    val footerSingleRow: Boolean,
    val perceptionViewportMin: Dp,
    val visionCameraViewportMin: Dp,
    /** Minimum drawing height for the map bitmap area when stacked. */
    val mapStackedGridMinHeight: Dp,
) {
    companion object {
        fun from(maxWidth: Dp, maxHeight: Dp): SirenaLayoutContext {
            val squat = maxHeight < 520.dp
            val perceptionTriple = maxWidth >= 720.dp
            val mapSplit = maxWidth >= 600.dp
            val visionSplit = maxWidth >= 560.dp
            val headerSingleRow = maxWidth >= 480.dp
            val footerSingleRow = maxWidth >= 540.dp

            val pViewport =
                when {
                    squat -> 152.dp
                    maxWidth < 380.dp -> 160.dp
                    maxWidth < 520.dp -> 180.dp
                    maxWidth < 640.dp -> 200.dp
                    else -> 220.dp
                }
            val vCam =
                when {
                    squat -> 220.dp
                    maxWidth < 400.dp -> 260.dp
                    maxWidth < 560.dp -> 280.dp
                    else -> 280.dp
                }
            val mapGridMin =
                when {
                    squat -> 200.dp
                    maxWidth < 360.dp -> 200.dp
                    else -> 240.dp
                }

            return SirenaLayoutContext(
                maxWidth = maxWidth,
                maxHeight = maxHeight,
                perceptionTriple = perceptionTriple,
                mapSplit = mapSplit,
                visionSplit = visionSplit,
                headerSingleRow = headerSingleRow,
                footerSingleRow = footerSingleRow,
                perceptionViewportMin = pViewport,
                visionCameraViewportMin = vCam,
                mapStackedGridMinHeight = mapGridMin,
            )
        }
    }
}

/**
 * Measures the available size for [content] and provides [SirenaLayoutContext].
 */
@Composable
fun SirenaAdaptiveContainer(
    modifier: Modifier = Modifier,
    content: @Composable (SirenaLayoutContext) -> Unit,
) {
    BoxWithConstraints(modifier.fillMaxSize()) {
        val ctx = remember(maxWidth, maxHeight) { SirenaLayoutContext.from(maxWidth, maxHeight) }
        content(ctx)
    }
}

/** Breadcrumb on the left; status pills in a horizontally scrollable strip (no clipping on phones). */
@Composable
fun SirenaAdaptiveHeaderPills(
    ctx: SirenaLayoutContext,
    breadcrumb: @Composable () -> Unit,
    pills: @Composable () -> Unit,
) {
    if (ctx.headerSingleRow) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            breadcrumb()
            Spacer(Modifier.weight(1f))
            pills()
        }
    } else {
        Column(
            Modifier.fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                breadcrumb()
            }
            Row(
                Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                pills()
            }
        }
    }
}
