package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Surface
import androidx.compose.material.ripple.rememberRipple
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.os.Build
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive

/** Matches [sirena_ui.widgets.common.Card] variants (objectName card / cardSubtle / cardHero). */
enum class SirenaCardKind {
    Standard,
    Subtle,
    Hero,
    Callout,
    /** Soft error / warning panel (pill error background). */
    Error,
}

/**
 * White (or cloud) rounded card with 1px border — same role as Qt `Card` frame.
 * Inner spacing defaults to desktop `padding=12`, `spacing=8`.
 */
@Composable
fun SirenaCard(
    modifier: Modifier = Modifier,
    kind: SirenaCardKind = SirenaCardKind.Standard,
    contentPadding: PaddingValues = PaddingValues(SirenaDimens.cardInnerPad),
    verticalArrangement: Arrangement.Vertical = Arrangement.spacedBy(SirenaDimens.cardSpacing),
    @Suppress("UNUSED_PARAMETER") liquidGlass: Boolean = false,
    content: @Composable ColumnScope.() -> Unit,
) {
    val shell = LocalSirenaShellCompact.current
    val defaultPad = PaddingValues(SirenaDimens.cardInnerPad)
    val effectivePadding =
        if (shell && contentPadding == defaultPad) {
            PaddingValues(horizontal = 6.dp, vertical = 5.dp)
        } else {
            contentPadding
        }
    val vSpace =
        if (shell && verticalArrangement == Arrangement.spacedBy(SirenaDimens.cardSpacing)) {
            Arrangement.spacedBy(3.dp)
        } else {
            verticalArrangement
        }
    val radius =
        when (kind) {
            SirenaCardKind.Standard ->
                if (shell) {
                    9.dp
                } else {
                    SirenaDimens.cardRadius
                }
            SirenaCardKind.Subtle ->
                if (shell) {
                    8.dp
                } else {
                    SirenaDimens.cardRadiusSubtle
                }
            SirenaCardKind.Hero ->
                if (shell) {
                    11.dp
                } else {
                    SirenaDimens.cardRadiusHero
                }
            SirenaCardKind.Callout ->
                if (shell) {
                    8.dp
                } else {
                    SirenaDimens.cardRadiusSubtle
                }
            SirenaCardKind.Error ->
                if (shell) {
                    8.dp
                } else {
                    SirenaDimens.cardRadiusSubtle
                }
        }
    val shape = RoundedCornerShape(radius)
    val bg =
        when (kind) {
            SirenaCardKind.Standard, SirenaCardKind.Hero -> SirenaColors.panel
            SirenaCardKind.Subtle -> SirenaColors.cloud
            SirenaCardKind.Callout -> SirenaColors.calloutBg
            SirenaCardKind.Error -> SirenaColors.pillErrorBg
        }
    Surface(
        modifier = modifier.clip(shape),
        shape = shape,
        color = bg,
        border = BorderStroke(1.dp, SirenaColors.border),
        shadowElevation = 0.dp,
        tonalElevation = 0.dp,
    ) {
        Column(
            Modifier.padding(effectivePadding),
            verticalArrangement = vSpace,
            content = content,
        )
    }
}

/** Same chrome as [SirenaCard] but arbitrary content (maps, canvases). */
@Composable
fun SirenaCardSurface(
    modifier: Modifier = Modifier,
    kind: SirenaCardKind = SirenaCardKind.Standard,
    contentPadding: PaddingValues = PaddingValues(SirenaDimens.cardInnerPad),
    content: @Composable BoxScope.() -> Unit,
) {
    val shell = LocalSirenaShellCompact.current
    val defaultPad = PaddingValues(SirenaDimens.cardInnerPad)
    val effectivePadding =
        if (shell && contentPadding == defaultPad) {
            PaddingValues(horizontal = 6.dp, vertical = 5.dp)
        } else {
            contentPadding
        }
    val radius =
        when (kind) {
            SirenaCardKind.Standard ->
                if (shell) {
                    9.dp
                } else {
                    SirenaDimens.cardRadius
                }
            SirenaCardKind.Subtle ->
                if (shell) {
                    8.dp
                } else {
                    SirenaDimens.cardRadiusSubtle
                }
            SirenaCardKind.Hero ->
                if (shell) {
                    11.dp
                } else {
                    SirenaDimens.cardRadiusHero
                }
            SirenaCardKind.Callout ->
                if (shell) {
                    8.dp
                } else {
                    SirenaDimens.cardRadiusSubtle
                }
            SirenaCardKind.Error ->
                if (shell) {
                    8.dp
                } else {
                    SirenaDimens.cardRadiusSubtle
                }
        }
    val bg =
        when (kind) {
            SirenaCardKind.Standard, SirenaCardKind.Hero -> SirenaColors.panel
            SirenaCardKind.Subtle -> SirenaColors.cloud
            SirenaCardKind.Callout -> SirenaColors.calloutBg
            SirenaCardKind.Error -> SirenaColors.pillErrorBg
        }
    val shape = RoundedCornerShape(radius)
    Surface(
        modifier =
            modifier
                .clip(shape)
                .border(BorderStroke(1.dp, SirenaColors.border), shape),
        shape = shape,
        color = bg,
        shadowElevation = 0.dp,
        tonalElevation = 0.dp,
    ) {
        Box(Modifier.padding(effectivePadding), content = content)
    }
}

/** [sirena_ui.widgets.common.CardTitle] */
@Composable
fun SirenaCardTitle(text: String, modifier: Modifier = Modifier) {
    val shell = LocalSirenaShellCompact.current
    Text(
        text,
        modifier = modifier,
        color = SirenaColors.text,
        fontSize = if (shell) 12.sp else SirenaType.cardTitle,
        fontWeight = FontWeight.SemiBold,
    )
}

/** [sirena_ui.widgets.common.SectionLabel] — uppercase, letter-spaced. */
@Composable
fun SirenaSectionLabel(text: String, modifier: Modifier = Modifier) {
    val shell = LocalSirenaShellCompact.current
    Text(
        text.uppercase(),
        modifier = modifier,
        color = SirenaColors.grey,
        fontSize = if (shell) 8.sp else SirenaType.sectionLabel,
        fontWeight = FontWeight.Bold,
        letterSpacing = if (shell) 1.0.sp else SirenaType.sectionLabelLetterSpacing,
    )
}

/** [sirena_ui.widgets.common.MutedLabel] */
@Composable
fun SirenaMutedText(
    text: String,
    modifier: Modifier = Modifier,
    maxLines: Int = Int.MAX_VALUE,
) {
    val shell = LocalSirenaShellCompact.current
    Text(
        text,
        modifier = modifier,
        color = SirenaColors.muted,
        fontSize = if (shell) 9.sp else SirenaType.muted,
        maxLines = maxLines,
        overflow = TextOverflow.Ellipsis,
    )
}

/** [sirena_ui.widgets.common.Breadcrumb] */
@Composable
fun SirenaBreadcrumbLine(parts: List<String>, modifier: Modifier = Modifier) {
    val shell = LocalSirenaShellCompact.current
    Text(
        parts.joinToString(" / "),
        modifier = modifier,
        color = SirenaColors.breadcrumb,
        fontSize = if (shell) 10.sp else SirenaType.breadcrumb,
    )
}

/** [sirena_ui.widgets.common.HRule] */
@Composable
fun SirenaHRule(modifier: Modifier = Modifier) {
    Box(
        modifier
            .fillMaxWidth()
            .height(1.dp)
            .background(SirenaColors.rule),
    )
}

enum class SirenaPillKind {
    Ok,
    Warn,
    Error,
    Neutral,
}

/** [sirena_ui.widgets.common.Pill] + stylesheet #pillOk / #pillWarn / … */
@Composable
fun SirenaStatusPill(
    text: String,
    kind: SirenaPillKind,
    modifier: Modifier = Modifier,
    maxLines: Int = 2,
) {
    val shell = LocalSirenaShellCompact.current
    val (bg, fg) =
        when (kind) {
            SirenaPillKind.Ok -> SirenaColors.pillOkBg to SirenaColors.pillOkFg
            SirenaPillKind.Warn -> SirenaColors.pillWarnBg to SirenaColors.pillWarnFg
            SirenaPillKind.Error -> SirenaColors.pillErrorBg to SirenaColors.pillErrorFg
            SirenaPillKind.Neutral -> SirenaColors.pillNeutralBg to SirenaColors.pillNeutralFg
        }
    val animatedBg by androidx.compose.animation.animateColorAsState(
        targetValue = bg,
        animationSpec = spring(stiffness = Spring.StiffnessMediumLow),
        label = "pillBg",
    )
    Surface(
        modifier = modifier.sirenaPulseOnChange("$text|$kind"),
        shape = RoundedCornerShape(if (shell) 8.dp else 10.dp),
        color = animatedBg,
    ) {
        Text(
            text,
            Modifier.padding(
                horizontal = if (shell) 8.dp else 10.dp,
                vertical = if (shell) 1.dp else 2.dp,
            ),
            color = fg,
            fontSize = if (shell) 10.sp else SirenaType.pill,
            fontWeight = FontWeight.SemiBold,
            maxLines = maxLines,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/** `#togglePill` — brake / reverse style toggles on Drive. */
@Composable
fun SirenaTogglePill(
    text: String,
    checked: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val shell = LocalSirenaShellCompact.current
    val shape = RoundedCornerShape(if (shell) 10.dp else 12.dp)
    Surface(
        modifier =
            modifier
                .clip(shape)
                .clickable(enabled = enabled, onClick = onClick),
        shape = shape,
        color = if (checked) SirenaColors.red else SirenaColors.toggleOffBg,
    ) {
        Text(
            text,
            Modifier.padding(
                horizontal = if (shell) 12.dp else 14.dp,
                vertical = if (shell) 4.dp else 5.dp,
            ),
            color = if (checked) SirenaColors.white else SirenaColors.text,
            fontSize = if (shell) 13.sp else SirenaType.base,
            fontWeight = FontWeight.SemiBold,
        )
    }
}

/** Compact filled danger control (E‑stop row). */
@Composable
fun SirenaEstopButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    var shakeTick by remember { mutableStateOf(0) }
    Button(
        onClick = {
            shakeTick++
            onClick()
        },
        modifier = modifier.height(40.dp).sirenaShake(shakeTick),
        enabled = enabled,
        shape = RoundedCornerShape(SirenaDimens.primaryButtonRadius),
        colors =
            ButtonDefaults.buttonColors(
                containerColor = SirenaColors.danger,
                contentColor = SirenaColors.white,
                disabledContainerColor = SirenaColors.disabledFill,
                disabledContentColor = SirenaColors.disabledText,
            ),
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 6.dp),
    ) {
        Text(text, fontWeight = FontWeight.SemiBold, fontSize = SirenaType.base)
    }
}

/** Desktop `QPushButton#primary` — solid brand red with press scale. */
@Composable
fun SirenaPrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val shell = LocalSirenaShellCompact.current
    val shape = RoundedCornerShape(SirenaDimens.primaryButtonRadius)
    val interactionSource = remember { MutableInteractionSource() }
    val pressed by interactionSource.collectIsPressedAsState()
    val scale by animateFloatAsState(
        targetValue = if (pressed && enabled) 0.96f else 1f,
        animationSpec = spring(),
        label = "primaryBtnScale",
    )
    val bg = if (enabled) SirenaColors.red else SirenaColors.disabledFill
    Box(
        modifier =
            modifier
                .scale(scale)
                .defaultMinSize(minHeight = if (shell) 42.dp else 48.dp)
                .clip(shape)
                .background(bg)
                .border(1.dp, if (enabled) SirenaColors.redDark else SirenaColors.border, shape)
                .clickable(
                    interactionSource = interactionSource,
                    indication =
                        rememberRipple(
                            bounded = true,
                            color = Color.White.copy(alpha = 0.22f),
                        ),
                    enabled = enabled,
                    role = Role.Button,
                    onClick = onClick,
                ),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = text,
            color = if (enabled) SirenaColors.white else SirenaColors.disabledText,
            fontWeight = FontWeight.SemiBold,
            fontSize = if (shell) 13.sp else SirenaType.base,
            modifier =
                Modifier.padding(
                    horizontal = if (shell) 12.dp else 14.dp,
                    vertical = if (shell) 5.dp else 6.dp,
                ),
        )
    }
}

/** Desktop `QPushButton#secondary` — solid panel + brand border. */
@Composable
fun SirenaSecondaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val shell = LocalSirenaShellCompact.current
    val shape = RoundedCornerShape(SirenaDimens.secondaryButtonRadius)
    val interactionSource = remember { MutableInteractionSource() }
    val pressed by interactionSource.collectIsPressedAsState()
    val scale by animateFloatAsState(
        targetValue = if (pressed && enabled) 0.97f else 1f,
        animationSpec = spring(),
        label = "secondaryBtnScale",
    )
    Box(
        modifier =
            modifier
                .scale(scale)
                .defaultMinSize(minHeight = if (shell) 42.dp else 48.dp)
                .clip(shape)
                .background(if (enabled) SirenaColors.panel else SirenaColors.disabledFill)
                .border(1.dp, if (enabled) SirenaColors.red else SirenaColors.border, shape)
                .clickable(
                    interactionSource = interactionSource,
                    indication =
                        rememberRipple(
                            bounded = true,
                            color = SirenaColors.red.copy(alpha = if (enabled) 0.16f else 0.08f),
                        ),
                    enabled = enabled,
                    role = Role.Button,
                    onClick = onClick,
                ),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = text,
            color = if (enabled) SirenaColors.red else SirenaColors.disabledText,
            fontWeight = FontWeight.SemiBold,
            fontSize = if (shell) 13.sp else SirenaType.base,
            modifier =
                Modifier.padding(
                    horizontal = if (shell) 10.dp else 12.dp,
                    vertical = if (shell) 5.dp else 6.dp,
                ),
        )
    }
}

/** Desktop Actions screen sub-tab strip (`QPushButton#subTabButton` style). */
@Composable
fun SirenaSegmentedTabs(
    tabs: List<String>,
    selectedIndex: Int,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val shellShape = RoundedCornerShape(12.dp)
    val pillShape = RoundedCornerShape(10.dp)
    Box(
        modifier =
            modifier
                .clip(shellShape)
                .background(SirenaColors.cloud)
                .border(1.dp, SirenaColors.border, shellShape),
    ) {
        Row(
            Modifier.padding(4.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            tabs.forEachIndexed { i, label ->
                val sel = i == selectedIndex
                val interactionSource = remember(i) { MutableInteractionSource() }
                val pressed by interactionSource.collectIsPressedAsState()
                val scale by animateFloatAsState(
                    targetValue = if (pressed) 0.95f else 1f,
                    animationSpec = spring(),
                    label = "tabScale$i",
                )
                Box(
                    modifier =
                        Modifier
                            .scale(scale)
                            .clip(pillShape)
                            .then(
                                if (sel) {
                                    Modifier.background(SirenaColors.red)
                                } else {
                                    Modifier
                                },
                            )
                            .clickable(
                                interactionSource = interactionSource,
                                indication =
                                    rememberRipple(
                                        bounded = true,
                                        color =
                                            if (sel) {
                                                Color.White.copy(alpha = 0.14f)
                                            } else {
                                                SirenaColors.red.copy(alpha = 0.12f)
                                            },
                                    ),
                                onClick = { onSelect(i) },
                            ),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        label,
                        Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
                        color = if (sel) SirenaColors.white else SirenaColors.text,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = SirenaType.base,
                    )
                }
            }
        }
    }
}

/** Desktop `QPushButton#dangerButton` */
@Composable
fun SirenaDangerButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    OutlinedButton(
        onClick = onClick,
        modifier = modifier,
        enabled = enabled,
        shape = RoundedCornerShape(SirenaDimens.secondaryButtonRadius),
        border = BorderStroke(1.dp, if (enabled) SirenaColors.danger else SirenaColors.border),
        colors =
            ButtonDefaults.outlinedButtonColors(
                contentColor = SirenaColors.danger,
                disabledContentColor = SirenaColors.disabledText,
            ),
        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 5.dp),
    ) {
        Text(text, fontWeight = FontWeight.SemiBold, fontSize = SirenaType.base)
    }
}

/** Slider groove/handle uses brand red ([sirena_ui.styles] horizontal slider). */
@Composable
fun SirenaSlider(
    value: Float,
    onValueChange: (Float) -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    valueRange: ClosedFloatingPointRange<Float> = 0f..1f,
    steps: Int = 0,
) {
    Slider(
        modifier = modifier,
        value = value,
        onValueChange = onValueChange,
        enabled = enabled,
        valueRange = valueRange,
        steps = steps,
        colors =
            SliderDefaults.colors(
                thumbColor = SirenaColors.red,
                activeTrackColor = SirenaColors.red,
                inactiveTrackColor = SirenaColors.border,
                disabledThumbColor = SirenaColors.disabledFill,
                disabledActiveTrackColor = SirenaColors.border,
                disabledInactiveTrackColor = SirenaColors.border.copy(alpha = 0.4f),
            ),
    )
}

/** Outlined field matching Qt `QLineEdit` focus border (brand red). */
@Composable
fun SirenaOutlinedTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    singleLine: Boolean = true,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
    visualTransformation: VisualTransformation = VisualTransformation.None,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = modifier,
        singleLine = singleLine,
        label = label,
        keyboardOptions = keyboardOptions,
        visualTransformation = visualTransformation,
        colors =
            OutlinedTextFieldDefaults.colors(
                focusedBorderColor = SirenaColors.red,
                unfocusedBorderColor = SirenaColors.border,
                cursorColor = SirenaColors.red,
                focusedLabelColor = SirenaColors.red,
                unfocusedLabelColor = SirenaColors.muted,
            ),
    )
}

/** Standard destructive / discard confirmation. */
@Composable
fun SirenaConfirmDialog(
    onDismiss: () -> Unit,
    message: String,
    onConfirm: () -> Unit,
    title: String = "Are you sure?",
    confirmText: String = "Yes, continue",
    dismissText: String = "Cancel",
    dangerous: Boolean = false,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title, fontWeight = FontWeight.Bold, color = SirenaColors.text) },
        text = {
            Text(message, color = SirenaColors.text, fontSize = SirenaType.muted)
        },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text(
                    confirmText,
                    color = if (dangerous) SirenaColors.danger else SirenaColors.red,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text(dismissText, color = SirenaColors.muted)
            }
        },
    )
}

/** Transparent dialog actions — Qt headerTray-style minimal. */
@Composable
fun SirenaTextButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    TextButton(onClick = onClick, modifier = modifier) {
        Text(text, color = SirenaColors.red, fontWeight = FontWeight.SemiBold)
    }
}

/**
 * [sirena_ui.screens.home_screen._QuickTile] — white card button, red glyph, hover = tint (pressed here).
 */
@Composable
fun SirenaQuickTile(
    glyph: String,
    label: String,
    blurb: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier =
            modifier
                .heightIn(min = SirenaDimens.quickTileMinHeight)
                .fillMaxWidth()
                .clip(RoundedCornerShape(SirenaDimens.cardRadiusSubtle))
                .clickable(onClick = onClick),
        shape = RoundedCornerShape(SirenaDimens.cardRadiusSubtle),
        color = SirenaColors.panel,
        border = BorderStroke(1.dp, SirenaColors.border),
        shadowElevation = 0.dp,
    ) {
        Column(
            Modifier.padding(
                horizontal = SirenaDimens.quickTileHPad,
                vertical = SirenaDimens.quickTileVPad,
            ),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(
                glyph,
                color = SirenaColors.red,
                fontSize = SirenaType.quickGlyph,
            )
            Text(
                label,
                color = SirenaColors.text,
                fontSize = SirenaType.quickTitle,
                fontWeight = FontWeight.Bold,
            )
            Text(
                blurb,
                color = SirenaColors.muted,
                fontSize = SirenaType.quickBlurb,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

/** Desktop `#tabButton` / `#subTabButton` row. */
@Composable
fun SirenaSubTabRow(
    labels: List<String>,
    selectedIndex: Int,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Start) {
        labels.forEachIndexed { index, label ->
            val selected = index == selectedIndex
            Column(
                modifier =
                    Modifier
                        .clickable { onSelect(index) }
                        .padding(horizontal = 18.dp, vertical = 10.dp),
            ) {
                Text(
                    label,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = SirenaType.base,
                    color =
                        if (selected) {
                            SirenaColors.red
                        } else {
                            SirenaColors.muted
                        },
                )
                Box(
                    Modifier
                        .padding(top = 2.dp)
                        .fillMaxWidth()
                        .height(3.dp)
                        .background(if (selected) SirenaColors.red else Color.Transparent),
                )
            }
        }
    }
}

/** Hold-to-drive D-pad — kiosk parity: one [onPress] on finger down, [onRelease] on finger up. */
@Composable
fun SirenaDpadHoldButton(
    label: String,
    enabled: Boolean,
    onPress: suspend () -> Unit,
    onRelease: suspend () -> Unit,
    modifier: Modifier = Modifier,
) {
    val interactionSource = remember { MutableInteractionSource() }
    val pressed by interactionSource.collectIsPressedAsState()
    val latestPress by rememberUpdatedState(onPress)
    val latestRelease by rememberUpdatedState(onRelease)
    var prevPressed by remember { mutableStateOf(false) }
    LaunchedEffect(pressed, enabled) {
        val down = pressed && enabled
        if (down && !prevPressed) {
            try {
                latestPress()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
            }
        }
        if (prevPressed && !down) {
            try {
                latestRelease()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
            }
        }
        prevPressed = down
    }
    val shape = RoundedCornerShape(12.dp)
    val scale by animateFloatAsState(
        targetValue = if (pressed && enabled) 0.94f else 1f,
        animationSpec = spring(stiffness = Spring.StiffnessMedium),
        label = "dpadScale",
    )
    val borderColor =
        when {
            pressed && enabled -> SirenaColors.red
            enabled -> SirenaColors.border
            else -> SirenaColors.border.copy(alpha = 0.5f)
        }
    Surface(
        modifier =
            modifier
                .scale(scale)
                .size(SirenaDimens.dpadMin)
                .clip(shape)
                .clickable(
                    interactionSource = interactionSource,
                    indication = null,
                    enabled = enabled,
                    onClick = {},
                ),
        shape = shape,
        color = if (enabled) SirenaColors.panel else Color(0xFFF0F0F3),
        border = BorderStroke(if (pressed && enabled) 2.dp else 1.dp, borderColor),
    ) {
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Text(
                label,
                color = if (enabled) SirenaColors.text else SirenaColors.disabledText,
                fontSize = SirenaType.base,
                fontWeight = FontWeight.SemiBold,
                textAlign = TextAlign.Center,
            )
        }
    }
}

/** `#dpadButton` — square control pad keys. */
@Composable
fun SirenaDpadButton(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val shape = RoundedCornerShape(12.dp)
    Surface(
        modifier =
            modifier
                .size(SirenaDimens.dpadMin)
                .clip(shape)
                .clickable(enabled = enabled, onClick = onClick),
        shape = shape,
        color = if (enabled) SirenaColors.panel else Color(0xFFF0F0F3),
        border = BorderStroke(1.dp, if (enabled) SirenaColors.border else SirenaColors.border.copy(alpha = 0.5f)),
    ) {
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Text(
                label,
                color = if (enabled) SirenaColors.text else SirenaColors.disabledText,
                fontSize = SirenaType.base,
                fontWeight = FontWeight.SemiBold,
                textAlign = TextAlign.Center,
            )
        }
    }
}

/** `#dpadStop` — circular red stop. */
@Composable
fun SirenaDpadStop(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val shape = RoundedCornerShape(SirenaDimens.dpadMin / 2)
    Surface(
        modifier =
            modifier
                .size(SirenaDimens.dpadMin)
                .clip(shape)
                .clickable(enabled = enabled, onClick = onClick),
        shape = shape,
        color = if (enabled) SirenaColors.red else SirenaColors.disabledFill,
    ) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text(
                label,
                color = SirenaColors.white,
                fontSize = 12.sp,
                fontWeight = FontWeight.ExtraBold,
            )
        }
    }
}

/** Blurred backdrop + centered card for features not yet on the companion app. */
@Composable
fun SirenaComingSoonGate(
    title: String,
    message: String,
    modifier: Modifier = Modifier,
    background: @Composable BoxScope.() -> Unit,
) {
    Box(
        modifier
            .fillMaxSize()
            .background(SirenaColors.cloud),
    ) {
        Box(
            Modifier
                .fillMaxSize()
                .then(
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                        Modifier.blur(18.dp)
                    } else {
                        Modifier
                    },
                ),
        ) {
            background()
        }
        Box(
            Modifier
                .fillMaxSize()
                .background(Color.Black.copy(alpha = 0.42f)),
        )
        SirenaScaleIn(visible = true) {
            SirenaCard(
                modifier =
                    Modifier
                        .align(Alignment.Center)
                        .padding(24.dp)
                        .fillMaxWidth(0.88f),
                kind = SirenaCardKind.Hero,
                contentPadding = PaddingValues(20.dp),
            ) {
                Text(
                    title,
                    fontWeight = FontWeight.Bold,
                    fontSize = 22.sp,
                    color = SirenaColors.text,
                )
                Text(
                    message,
                    color = SirenaColors.muted,
                    fontSize = SirenaType.muted,
                )
                SirenaStatusPill(text = "Coming soon", kind = SirenaPillKind.Warn)
            }
        }
    }
}

@Composable
fun BoxScope.PerceptionComingSoonLabel() {
    Column(
        Modifier
            .align(Alignment.Center)
            .padding(12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        SirenaStatusPill(text = "Coming soon", kind = SirenaPillKind.Warn)
        Text(
            "Companion preview",
            color = SirenaColors.muted,
            fontSize = SirenaType.muted,
            textAlign = TextAlign.Center,
        )
    }
}
