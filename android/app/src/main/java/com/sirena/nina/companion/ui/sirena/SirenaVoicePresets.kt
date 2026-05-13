package com.sirena.nina.companion.ui.sirena

/**
 * gTTS voice presets aligned with Qt [sirena_ui.widgets.audio_editor_dialog.VOICE_PRESETS]
 * (`label`, `lang`, `tld`, `slow`).
 */
data class SirenaVoicePreset(
    val label: String,
    val lang: String,
    val tld: String,
    val slow: Boolean,
)

val SirenaVoicePresets: List<SirenaVoicePreset> =
    listOf(
        SirenaVoicePreset("US English (default)", "en", "us", false),
        SirenaVoicePreset("US English · robotic slow (US, female-leaning)", "en", "us", true),
        SirenaVoicePreset("UK English", "en", "co.uk", false),
        SirenaVoicePreset("Australian English", "en", "com.au", false),
        SirenaVoicePreset("Indian English", "en", "co.in", false),
        SirenaVoicePreset("Hindi", "hi", "co.in", false),
        SirenaVoicePreset("Spanish (Spain)", "es", "es", false),
        SirenaVoicePreset("French (France)", "fr", "fr", false),
    )
