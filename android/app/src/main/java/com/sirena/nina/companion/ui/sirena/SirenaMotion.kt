package com.sirena.nina.companion.ui.sirena

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.slideInVertically
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.graphicsLayer

/** Fade + slight upward slide when content appears. */
@Composable
fun SirenaAnimatedEnter(
    visible: Boolean,
    modifier: Modifier = Modifier,
    delayIndex: Int = 0,
    content: @Composable () -> Unit,
) {
    val delayMs = (delayIndex.coerceIn(0, 12) * 45)
    AnimatedVisibility(
        visible = visible,
        modifier = modifier,
        enter =
            fadeIn(animationSpec = tween(280, delayMillis = delayMs, easing = FastOutSlowInEasing)) +
                slideInVertically(
                    animationSpec = tween(320, delayMillis = delayMs, easing = FastOutSlowInEasing),
                    initialOffsetY = { it / 6 },
                ),
        exit = fadeOut(animationSpec = tween(160)) + scaleOut(targetScale = 0.98f),
    ) {
        content()
    }
}

/** Brief scale pulse when [pulseKey] changes (status pill updates). */
fun Modifier.sirenaPulseOnChange(pulseKey: Any?): Modifier =
    composed {
        val anim = remember { Animatable(1f) }
        LaunchedEffect(pulseKey) {
            anim.snapTo(1f)
            anim.animateTo(1.07f, tween(90, easing = FastOutSlowInEasing))
            anim.animateTo(1f, spring(stiffness = Spring.StiffnessMedium))
        }
        scale(anim.value)
    }

/** Horizontal shake for E-STOP feedback. */
fun Modifier.sirenaShake(trigger: Int): Modifier =
    composed {
        val offset = remember { Animatable(0f) }
        LaunchedEffect(trigger) {
            if (trigger <= 0) return@LaunchedEffect
            repeat(3) {
                offset.animateTo(6f, tween(40))
                offset.animateTo(-6f, tween(40))
            }
            offset.animateTo(0f, tween(60))
        }
        graphicsLayer { translationX = offset.value }
    }

/** Scale-in for coming-soon / dialog cards. */
@Composable
fun SirenaScaleIn(
    visible: Boolean,
    content: @Composable () -> Unit,
) {
    AnimatedVisibility(
        visible = visible,
        enter = scaleIn(initialScale = 0.92f, animationSpec = spring(stiffness = Spring.StiffnessMediumLow)) +
            fadeIn(tween(220)),
        exit = scaleOut(targetScale = 0.96f) + fadeOut(tween(120)),
    ) {
        content()
    }
}
