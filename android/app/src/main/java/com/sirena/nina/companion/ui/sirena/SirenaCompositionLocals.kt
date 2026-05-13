package com.sirena.nina.companion.ui.sirena

import androidx.compose.runtime.compositionLocalOf

/**
 * True when the companion shell is in **phone / compact** mode (drawer nav, dense layout).
 * Set from [com.sirena.nina.companion.ui.NinaApp]; defaults to false outside that tree.
 */
val LocalSirenaShellCompact = compositionLocalOf { false }
