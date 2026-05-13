package com.sirena.nina.companion.ui

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Radar
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.ui.sirena.SirenaColors
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * FAB-style control with a continuous radar sweep, soft ring pulse, and centered icon.
 * Fits the product hub “discover” affordance without extra dependencies.
 */
@Composable
fun AnimatedRadarFab(
    onClick: () -> Unit,
    contentDescription: String,
    modifier: Modifier = Modifier,
    /** When true (e.g. sheet open + scan running), sweep runs a bit faster for extra energy. */
    energetic: Boolean = false,
) {
    val transition = rememberInfiniteTransition(label = "radar_fab")
    val sweepDegrees by transition.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec =
            infiniteRepeatable(
                animation =
                    tween(
                        durationMillis = if (energetic) 1_400 else 2_600,
                        easing = LinearEasing,
                    ),
                repeatMode = RepeatMode.Restart,
            ),
        label = "sweep",
    )
    val ringPulse by transition.animateFloat(
        initialValue = 0.35f,
        targetValue = 0.85f,
        animationSpec =
            infiniteRepeatable(
                animation = tween(1_800, easing = FastOutSlowInEasing),
                repeatMode = RepeatMode.Reverse,
            ),
        label = "ring_pulse",
    )

    val red = SirenaColors.red
    val charcoal = SirenaColors.charcoal
    val ringAlpha = 0.08f + 0.14f * ringPulse

    Box(
        modifier
            .size(52.dp)
            .shadow(
                elevation = 6.dp,
                shape = CircleShape,
                ambientColor = red.copy(alpha = 0.12f),
                spotColor = red.copy(alpha = 0.22f),
            )
            .clip(CircleShape)
            .background(
                Brush.radialGradient(
                    colors =
                        listOf(
                            SirenaColors.white,
                            SirenaColors.cloud,
                        ),
                ),
            )
            .semantics {
                this.contentDescription = contentDescription
                this.role = Role.Button
            }
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.fillMaxSize()) {
            val c = Offset(size.width / 2f, size.height / 2f)
            val maxR = size.minDimension / 2f

            for (i in 1..3) {
                val r = maxR * (0.28f + i * 0.18f)
                drawCircle(
                    color = charcoal.copy(alpha = 0.06f + i * 0.02f),
                    radius = r,
                    center = c,
                    style = Stroke(width = 1.1.dp.toPx()),
                )
            }

            for (deg in 0 until 360 step 45) {
                rotate(deg.toFloat(), pivot = c) {
                    drawLine(
                        color = charcoal.copy(alpha = 0.08f),
                        start = Offset(c.x, c.y - maxR * 0.92f),
                        end = Offset(c.x, c.y - maxR * 0.72f),
                        strokeWidth = 1.dp.toPx(),
                    )
                }
            }

            rotate(sweepDegrees, pivot = c) {
                drawArc(
                    brush =
                        Brush.sweepGradient(
                            0f to Color.Transparent,
                            0.12f to red.copy(alpha = 0.08f),
                            0.35f to red.copy(alpha = 0.2f),
                            0.55f to red.copy(alpha = 0.07f),
                            1f to Color.Transparent,
                            center = c,
                        ),
                    startAngle = -12f,
                    sweepAngle = 110f,
                    useCenter = true,
                    topLeft = Offset.Zero,
                    size = this.size,
                )
            }

            rotate(sweepDegrees, pivot = c) {
                val beamRad = 38.0 * PI / 180.0
                drawArc(
                    brush =
                        Brush.linearGradient(
                            colors =
                                listOf(
                                    red.copy(alpha = 0.02f),
                                    red.copy(alpha = 0.58f),
                                    red.copy(alpha = 0.1f),
                                ),
                            start = c,
                            end =
                                Offset(
                                    c.x + cos(beamRad).toFloat() * maxR,
                                    c.y + sin(beamRad).toFloat() * maxR,
                                ),
                        ),
                    startAngle = -8f,
                    sweepAngle = 52f,
                    useCenter = true,
                    topLeft = Offset.Zero,
                    size = this.size,
                )
            }

            drawCircle(
                color = red.copy(alpha = ringAlpha * 0.45f),
                radius = maxR - 1.5.dp.toPx(),
                center = c,
                style = Stroke(width = 1.4.dp.toPx()),
            )

            drawCircle(
                color = SirenaColors.white,
                radius = maxR * 0.22f,
                center = c,
            )
            drawCircle(
                color = red.copy(alpha = 0.12f),
                radius = maxR * 0.22f,
                center = c,
                style = Stroke(width = 1.dp.toPx()),
            )
        }

        Icon(
            imageVector = Icons.Filled.Radar,
            contentDescription = null,
            modifier = Modifier.size(22.dp),
            tint = red,
        )
    }
}
