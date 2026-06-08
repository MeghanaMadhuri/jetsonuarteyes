package com.sirena.nina.companion.data

import org.json.JSONObject

/**
 * [GET /v1/system/power-state] — mirrors Jetson PowerManager.status_dict().
 */
data class PowerStateUi(
    val state: String,
    val enabled: Boolean,
    val idleSecRemaining: Int,
    val sleepSecRemaining: Int,
    val hint: String?,
    val wakeTabletEnabled: Boolean,
) {
    val isAsleep: Boolean get() = state == "sleep"
    val isIdle: Boolean get() = state == "idle"
    val needsWake: Boolean get() = isAsleep || isIdle

    val statusLabel: String
        get() =
            when (state) {
                "sleep" -> "Asleep"
                "idle" -> "Idle (power save)"
                else -> "Active"
            }
}

fun JSONObject.toPowerStateUi(): PowerStateUi {
    val wake = optJSONObject("wake_sources_enabled")
    return PowerStateUi(
        state = optString("state", "active").trim().lowercase().ifBlank { "active" },
        enabled = optBoolean("enabled", false),
        idleSecRemaining = optInt("idle_sec_remaining"),
        sleepSecRemaining = optInt("sleep_sec_remaining"),
        hint = optString("hint").trim().takeIf { it.isNotEmpty() },
        wakeTabletEnabled = wake?.optBoolean("tablet", true) ?: true,
    )
}
