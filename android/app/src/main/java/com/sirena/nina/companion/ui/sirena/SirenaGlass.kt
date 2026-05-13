package com.sirena.nina.companion.ui.sirena

import android.graphics.RenderEffect
import android.graphics.Shader
import android.os.Build
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asComposeRenderEffect
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.graphics.Shape
import kotlin.math.max

/** Soft neutral + red-tinted drop shadow (floats above cloud like liquid glass). */
fun Modifier.sirenaGlassDropShadow(shape: Shape, elevationDp: Dp = 10.dp): Modifier =
    this.shadow(
        elevation = elevationDp,
        shape = shape,
        clip = false,
        ambientColor = SirenaColors.glassShadow,
        spotColor = SirenaColors.glassShadowTint,
    )

/**
 * Soft blur for a **solid / gradient background layer only** (no child composables).
 * API 31+; no-op below.
 */
fun Modifier.sirenaOptionalPanelBlur(radiusDp: Dp = 18.dp): Modifier =
    if (Build.VERSION.SDK_INT >= 31) {
        val r = radiusDp.value
        this.graphicsLayer {
            renderEffect =
                RenderEffect
                    .createBlurEffect(r, r, Shader.TileMode.DECAL)
                    .asComposeRenderEffect()
        }
    } else {
        this
    }

/** Outer rim: bright top / warm red undertone on sides. */
fun Modifier.sirenaGlassCardBorder(shape: RoundedCornerShape): Modifier =
    this.border(
        width = 1.2.dp,
        brush =
            Brush.verticalGradient(
                colors =
                    listOf(
                        SirenaColors.glassBorder,
                        SirenaColors.glassBorderTint.copy(alpha = 0.45f),
                        SirenaColors.glassBorder.copy(alpha = 0.55f),
                    ),
            ),
        shape = shape,
    )

fun Modifier.sirenaGlassCardBackground(
    shape: RoundedCornerShape,
    kind: SirenaCardKind,
): Modifier {
    val brush =
        when (kind) {
            SirenaCardKind.Standard, SirenaCardKind.Hero ->
                Brush.verticalGradient(
                    colors =
                        listOf(
                            SirenaColors.glassPanel,
                            SirenaColors.glassBodyTint,
                            SirenaColors.glassPanelSubtle,
                        ),
                )
            SirenaCardKind.Subtle ->
                Brush.verticalGradient(
                    colors =
                        listOf(
                            SirenaColors.glassPanelSubtle,
                            SirenaColors.glassBodyTint.copy(alpha = 0.28f),
                            SirenaColors.cloud.copy(alpha = 0.62f),
                        ),
                )
            SirenaCardKind.Callout ->
                Brush.verticalGradient(
                    colors =
                        listOf(
                            SirenaColors.calloutBg.copy(alpha = 0.78f),
                            SirenaColors.calloutBg.copy(alpha = 0.88f),
                        ),
                )
            SirenaCardKind.Error ->
                Brush.verticalGradient(
                    colors =
                        listOf(
                            SirenaColors.pillErrorBg.copy(alpha = 0.82f),
                            SirenaColors.pillErrorBg.copy(alpha = 0.94f),
                        ),
                )
        }
    return this.background(brush = brush, shape = shape)
}

/** Darken edges like refractive “liquid” depth. */
fun Modifier.sirenaGlassInnerVignette(cornerDp: Dp = 14.dp): Modifier =
    this.drawBehind {
        val rad = cornerDp.toPx()
        val c = Offset(size.width / 2f, size.height / 2f)
        val r = max(size.width, size.height) * 0.92f
        drawRoundRect(
            brush =
                Brush.radialGradient(
                    colorStops =
                        arrayOf(
                            0f to Color.Transparent,
                            0.55f to Color.Transparent,
                            1f to SirenaColors.glassInnerShadow,
                        ),
                    center = c,
                    radius = r,
                ),
            size = size,
            cornerRadius = CornerRadius(rad, rad),
        )
    }

/** Strong specular + softer second highlight (liquid gloss). [intensity] scales highlight strength (e.g. smaller controls). */
fun Modifier.sirenaGlassSpecularLayers(cornerDp: Dp = 14.dp, intensity: Float = 1f): Modifier {
    val a1 = (0.72f * intensity).coerceIn(0.12f, 0.85f)
    val a2 = (0.5f * intensity).coerceIn(0.08f, 0.6f)
    return this.drawBehind {
        val rad = cornerDp.toPx()
        drawRoundRect(
            brush =
                Brush.linearGradient(
                    colorStops =
                        arrayOf(
                            0f to SirenaColors.glassSpecular,
                            0.18f to SirenaColors.glassSpecularSoft,
                            0.45f to Color.Transparent,
                            1f to Color.Transparent,
                        ),
                    start = Offset(0f, 0f),
                    end = Offset(size.width * 0.65f, size.height * 0.5f),
                ),
            size = Size(size.width, size.height),
            cornerRadius = CornerRadius(rad, rad),
            alpha = a1,
        )
        drawRoundRect(
            brush =
                Brush.linearGradient(
                    colors =
                        listOf(
                            Color.Transparent,
                            SirenaColors.white.copy(alpha = 0.22f),
                            Color.Transparent,
                        ),
                    start = Offset(size.width * 0.55f, size.height * 0.35f),
                    end = Offset(size.width, size.height),
                ),
            size = Size(size.width, size.height),
            cornerRadius = CornerRadius(rad, rad),
            alpha = a2,
        )
    }
}

/** Glossy primary (brand red) — optional shadow, gradient fill, top specular, light rim. */
fun Modifier.sirenaGlassPrimaryButtonChrome(
    shape: RoundedCornerShape,
    enabled: Boolean,
    withDropShadow: Boolean = true,
): Modifier =
    if (!enabled) {
        this.clip(shape).background(SirenaColors.disabledFill, shape)
    } else {
        val radius = SirenaDimens.primaryButtonRadius
        val base =
            if (withDropShadow) {
                this.sirenaGlassDropShadow(shape, elevationDp = 5.dp)
            } else {
                this
            }
        base
            .clip(shape)
            .background(
                brush =
                    Brush.verticalGradient(
                        colors =
                            listOf(
                                SirenaColors.redDark,
                                SirenaColors.red,
                                SirenaColors.redHover.copy(alpha = 0.92f),
                            ),
                    ),
                shape = shape,
            )
            .drawBehind {
                val rad = radius.toPx()
                drawRoundRect(
                    brush =
                        Brush.linearGradient(
                            colorStops =
                                arrayOf(
                                    0f to Color.White.copy(alpha = 0.38f),
                                    0.22f to Color.White.copy(alpha = 0.08f),
                                    0.55f to Color.Transparent,
                                    1f to Color.Transparent,
                                ),
                            start = Offset(0f, 0f),
                            end = Offset(size.width * 0.55f, size.height * 0.45f),
                        ),
                    size = Size(size.width, size.height),
                    cornerRadius = CornerRadius(rad, rad),
                    alpha = 0.55f,
                )
            }
            .border(
                width = 1.dp,
                brush =
                    Brush.verticalGradient(
                        colors =
                            listOf(
                                Color.White.copy(alpha = 0.45f),
                                Color.White.copy(alpha = 0.12f),
                                SirenaColors.redDark.copy(alpha = 0.35f),
                            ),
                    ),
                shape = shape,
            )
    }

/** Translucent secondary — same vocabulary as liquid-glass cards, tuned for controls. */
fun Modifier.sirenaGlassSecondaryButtonChrome(
    shape: RoundedCornerShape,
    enabled: Boolean,
): Modifier =
    if (!enabled) {
        this
            .clip(shape)
            .background(SirenaColors.cloud.copy(alpha = 0.9f), shape)
            .border(1.dp, SirenaColors.border, shape)
    } else {
        val r = SirenaDimens.secondaryButtonRadius
        this
            .sirenaGlassDropShadow(shape, elevationDp = 4.dp)
            .clip(shape)
            .background(
                brush =
                    Brush.verticalGradient(
                        colors =
                            listOf(
                                SirenaColors.glassPanel,
                                SirenaColors.glassBodyTint.copy(alpha = 0.42f),
                                SirenaColors.glassPanelSubtle,
                            ),
                    ),
                shape = shape,
            )
            .sirenaGlassInnerVignette(r)
            .sirenaGlassSpecularLayers(r, intensity = 0.55f)
            .sirenaGlassCardBorder(shape)
    }

/** Outer shell for segmented tab strips (Playback / Record / …). */
fun Modifier.sirenaGlassSegmentedTabsShell(cornerDp: Dp = 12.dp): Modifier {
    val shape = RoundedCornerShape(cornerDp)
    return this
        .sirenaGlassDropShadow(shape, elevationDp = 5.dp)
        .clip(shape)
        .background(
            brush =
                Brush.verticalGradient(
                    colors =
                        listOf(
                            SirenaColors.glassPanel.copy(alpha = 0.96f),
                            SirenaColors.glassBodyTint.copy(alpha = 0.38f),
                            SirenaColors.glassPanelSubtle,
                        ),
                ),
            shape = shape,
        )
        .sirenaGlassInnerVignette(cornerDp)
        .sirenaGlassSpecularLayers(cornerDp, intensity = 0.38f)
        .sirenaGlassCardBorder(shape)
}

/** Rose-tinted wash so translucent cards read clearly over flat cloud. */
fun Modifier.sirenaGlassAmbientCanvas(): Modifier =
    this.drawBehind {
        drawRect(
            brush =
                Brush.linearGradient(
                    colors =
                        listOf(
                            SirenaColors.cloud,
                            SirenaColors.redTint.copy(alpha = 0.38f),
                            SirenaColors.cloud,
                        ),
                    start = Offset.Zero,
                    end = Offset(size.width, size.height),
                ),
        )
    }
