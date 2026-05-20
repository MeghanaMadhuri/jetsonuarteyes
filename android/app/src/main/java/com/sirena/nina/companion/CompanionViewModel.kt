package com.sirena.nina.companion

import android.app.Application
import android.content.Context
import android.net.Uri
import android.net.wifi.WifiManager
import androidx.lifecycle.AndroidViewModel
import kotlin.jvm.Volatile
import androidx.lifecycle.viewModelScope
import com.sirena.nina.companion.data.LinkApiException
import com.sirena.nina.companion.data.LinkClient
import com.sirena.nina.companion.data.SlamOccupancyGrid
import com.sirena.nina.companion.data.jsonCleanString
import com.sirena.nina.companion.data.Prefs
import com.sirena.nina.companion.network.BonjourJetsonFinder
import com.sirena.nina.companion.network.DaemonUrlResolver
import com.sirena.nina.companion.network.LanDaemonScanner
import com.sirena.nina.companion.util.NinaLog
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class StatusUi(
    val wifiRole: String,
    val ipv4: String?,
    val userMode: String,
    val bootWaitRemainingSec: Int,
    val clientSeen: Boolean,
    val lastError: String?,
    val savedNetworks: List<SavedNetUi>,
    val apSsid: String?,
    val activeStaSsid: String?,
    val activeStaProfile: String?,
    /** Jetson has issued a session token (fleet / pairing). */
    val paired: Boolean,
    val systemId: String?,
    val hostname: String?,
    /** Jetson-configured friendly label (`NINA_LINK_ROBOT_NAME` / ``display_name`` in JSON). */
    val displayName: String?,
)

data class SavedNetUi(
    val id: String,
    val uuid: String,
    val ssid: String,
    val nmAutoconnect: Boolean,
    /** ``ap`` for hotspot / Nina-AP style profiles, ``infrastructure`` for home STA. */
    val wifiMode: String = "infrastructure",
)

data class DiscoveredDaemonUi(
    val baseUrl: String,
    val systemId: String?,
    val hostname: String?,
    val displayName: String?,
)

data class DiscoveryDiagnosticsUi(
    val isScanning: Boolean = false,
    val deviceIpv4: String? = null,
    val subnetPrefix: String? = null,
    val hostCount: Int = 0,
    val probeAttempts: Int = 0,
    val successfulHosts: Int = 0,
    val failedProbes: Int = 0,
    val durationMs: Long? = null,
    val lastError: String? = null,
)

data class DriveCommandLogUi(
    val timestamp: String,
    val line: String,
)

/** Fast HTTP liveness to saved daemon URL (independent of full status refresh). */
data class JetsonLinkState(
    val isOnline: Boolean = false,
    val lastError: String? = null,
)

/** One row from Jetson `GET /v1/actions` (manifest). */
data class ActionRowUi(
    val name: String,
    val file: String?,
    val audio: String?,
    val audioOffsetSec: Double?,
    /** From motion JSON when ``duration_sec`` / ``frame_count`` are present on the link response. */
    val durationSec: Double?,
    val frameCount: Int?,
)

sealed interface CompanionUiState {
    data object Loading : CompanionUiState
    data class Ready(val url: String, val status: StatusUi?, val message: String?) : CompanionUiState
    data class Error(val text: String) : CompanionUiState
}

class CompanionViewModel(app: Application) : AndroidViewModel(app) {

    private fun vmD(msg: String) = NinaLog.debug("CompanionVM", msg)

    private val prefs = Prefs(app)
    private val client = LinkClient()

    private val appCtx get() = getApplication<Application>()

    /**
     * True after a successful session claim (kiosk stopped for nina-link).
     * Used so we release on leaving the Nina console or process death.
     */
    @Volatile
    private var robotConsoleSessionActive: Boolean = false

    /** Persisted daemon URL (normalized). */
    val savedDaemonUrl: Flow<String> = prefs.baseUrl

    /** Optional bearer for protected link routes (MJPEG streams may use this header when enabled). */
    val bearerToken: Flow<String?> = prefs.bearerToken

    private val _gatewayHint = MutableStateFlow<String?>(null)
    val gatewayHint: StateFlow<String?> = _gatewayHint.asStateFlow()

    private val _manifestActions = MutableStateFlow<List<ActionRowUi>>(emptyList())
    val manifestActions: StateFlow<List<ActionRowUi>> = _manifestActions.asStateFlow()

    private val _manifestActionsError = MutableStateFlow<String?>(null)
    val manifestActionsError: StateFlow<String?> = _manifestActionsError.asStateFlow()

    /** Short-lived hint after ``POST /v1/actions/play`` (HTTP has no completion callback). */
    private val _actionPlaybackStatus = MutableStateFlow<String?>(null)
    val actionPlaybackStatus: StateFlow<String?> = _actionPlaybackStatus.asStateFlow()

    @Volatile
    private var playbackHintJob: Job? = null

    private val _state = MutableStateFlow<CompanionUiState>(CompanionUiState.Loading)
    val state: StateFlow<CompanionUiState> = _state.asStateFlow()

    private val _jetsonLink = MutableStateFlow(JetsonLinkState())
    val jetsonLink: StateFlow<JetsonLinkState> = _jetsonLink.asStateFlow()

    /** Latest ``GET /v1/robot/capabilities`` (bridges, drive defaults). Cleared when status refresh fails. */
    private val _robotCapabilities = MutableStateFlow<JSONObject?>(null)
    val robotCapabilities: StateFlow<JSONObject?> = _robotCapabilities.asStateFlow()
    private val _discoveredDaemons = MutableStateFlow<List<DiscoveredDaemonUi>>(emptyList())
    val discoveredDaemons: StateFlow<List<DiscoveredDaemonUi>> = _discoveredDaemons.asStateFlow()
    private val _discoveryDiagnostics = MutableStateFlow(DiscoveryDiagnosticsUi())
    val discoveryDiagnostics: StateFlow<DiscoveryDiagnosticsUi> = _discoveryDiagnostics.asStateFlow()
    private val _driveCommandLog = MutableStateFlow<List<DriveCommandLogUi>>(emptyList())
    val driveCommandLog: StateFlow<List<DriveCommandLogUi>> = _driveCommandLog.asStateFlow()
    private val driveLogTimeFmt = SimpleDateFormat("HH:mm:ss", Locale.US)

    init {
        vmD("init CompanionViewModel")
        refreshStatus()
        viewModelScope.launch {
            var failStreak = 0
            var wasOnline = false
            while (isActive) {
                val url =
                    try {
                        prefs.baseUrl.first()
                    } catch (_: Exception) {
                        ""
                    }
                if (url.isBlank()) {
                    if (wasOnline) vmD("jetsonLinkPoll urlBlank -> offline")
                    wasOnline = false
                    _jetsonLink.value = JetsonLinkState(false, null)
                    failStreak = 0
                    delay(3000)
                    continue
                }
                try {
                    client.health(url)
                    if (!wasOnline) {
                        vmD("jetsonLinkPoll online host=${Uri.parse(url).host ?: url}")
                    }
                    wasOnline = true
                    _jetsonLink.value = JetsonLinkState(true, null)
                    failStreak = 0
                    delay(2500)
                } catch (e: Exception) {
                    failStreak++
                    val msg = e.message?.trim()?.take(120)
                    if (wasOnline || failStreak == 1 || failStreak % 5 == 0) {
                        vmD("jetsonLinkPoll fail streak=$failStreak err=${msg ?: e.javaClass.simpleName}")
                    }
                    wasOnline = false
                    _jetsonLink.value = JetsonLinkState(false, msg)
                    val backoffMs = (2000L * failStreak).coerceAtMost(25_000L)
                    delay(backoffMs)
                }
            }
        }
    }

    /**
     * Leave full-screen [CompanionUiState.Error] while keeping navigation usable.
     * Restores [CompanionUiState.Ready] with no status snapshot until the next successful refresh.
     */
    fun dismissErrorToDegradedReady() {
        viewModelScope.launch {
            vmD("dismissErrorToDegradedReady state=${_state.value::class.simpleName}")
            if (_state.value !is CompanionUiState.Error) return@launch
            val url = Prefs.normalizeBaseUrl(prefs.baseUrl.first())
            vmD("dismissErrorToDegradedReady -> Ready urlHost=${Uri.parse(url).host}")
            _state.value =
                CompanionUiState.Ready(
                    url = url,
                    status = null,
                    message =
                        "Last status refresh failed. Use Find robot or Network, then tap Refresh status.",
                )
        }
    }

    fun refreshStatus() {
        viewModelScope.launch {
            vmD("refreshStatus start")
            try {
                val savedNorm = Prefs.normalizeBaseUrl(prefs.baseUrl.first())
                if (savedNorm.isBlank()) {
                    val gw = DaemonUrlResolver.gatewayIpv4(appCtx)
                    val myIp = DaemonUrlResolver.deviceIpv4(appCtx)
                    _gatewayHint.value = buildDiscoveryHint(myIp, gw)
                    _state.value = CompanionUiState.Ready(url = "", status = null, message = null)
                    _robotCapabilities.value = null
                    _manifestActions.value = emptyList()
                    vmD("refreshStatus no saved URL — idle Ready")
                    return@launch
                }
                val (url, statusUi) = fetchStatusForSavedUrl(savedNorm)
                val gw = DaemonUrlResolver.gatewayIpv4(appCtx)
                val myIp = DaemonUrlResolver.deviceIpv4(appCtx)
                _gatewayHint.value = buildDiscoveryHint(myIp, gw)
                _state.value = CompanionUiState.Ready(url, statusUi, null)
                vmD(
                    "refreshStatus Ready host=${statusUi.hostname} role=${statusUi.wifiRole} ipv4=${statusUi.ipv4}",
                )
                try {
                    _robotCapabilities.value = client.capabilities(url)
                    vmD(
                        "refreshStatus capabilities keyCount=${_robotCapabilities.value?.length() ?: 0}",
                    )
                } catch (_: Exception) {
                    _robotCapabilities.value = null
                    vmD("refreshStatus capabilities fetch failed (ignored)")
                }
                refreshManifestActions()
            } catch (e: LinkApiException) {
                NinaLog.warn("refreshStatus", friendlyHttp(e))
                vmD("refreshStatus LinkApiException code=${e.code}")
                _robotCapabilities.value = null
                _state.update {
                    CompanionUiState.Error(friendlyHttp(e))
                }
            } catch (e: Exception) {
                NinaLog.warn("refreshStatus", e.message ?: "unknown")
                vmD("refreshStatus Exception ${e.javaClass.simpleName}")
                _robotCapabilities.value = null
                _state.value = CompanionUiState.Error(
                    e.message ?: "Could not reach Nina Link daemon. Check Wi‑Fi.",
                )
            }
        }
    }

    /** Health + status for the persisted daemon URL only (never writes prefs). */
    private suspend fun fetchStatusForSavedUrl(savedNorm: String): Pair<String, StatusUi> {
        val bearer = prefs.bearerToken.first()
        vmD("fetchStatusForSavedUrl host=${Uri.parse(savedNorm).host}")
        assertUrlNotTabletOwnIp(savedNorm)
        client.health(savedNorm)
        val st = client.status(savedNorm, bearer)
        return savedNorm to parseStatus(st)
    }

    private fun buildDiscoveryHint(myIp: String?, gw: String?): String {
        return when {
            DaemonUrlResolver.isTypicalHomeLanClient(myIp) ->
                "Home Wi‑Fi: use Find robot or the radar on the product screen to scan, then tap Connect on a system."
            gw != null ->
                "Jetson AP gateway (if any): http://$gw:8787 — home routers are not the daemon host."
            else ->
                "Set the Jetson link-daemon URL after discovery, or enter it in Network / Settings."
        }
    }

    private fun assertUrlNotTabletOwnIp(url: String) {
        val host = Uri.parse(url).host ?: return
        val my = DaemonUrlResolver.deviceIpv4(appCtx) ?: return
        if (host.equals(my, ignoreCase = true)) {
            throw IllegalArgumentException(
                "That address ($host) is this tablet, not the Jetson. " +
                    "Enter the robot's LAN IP from your router's client list, or connect via Nina AP " +
                    "(e.g. http://10.42.0.1:8787).",
            )
        }
    }

    fun ping(urlOverride: String? = null) {
        viewModelScope.launch {
            vmD("ping override=${urlOverride != null}")
            try {
                val raw = urlOverride?.trim() ?: prefs.baseUrl.first()
                val url = Prefs.normalizeBaseUrl(raw)
                assertUrlNotTabletOwnIp(url)
                client.health(url)
                prefs.setBaseUrl(url)
                vmD("ping ok host=${Uri.parse(url).host}")
                refreshStatus()
            } catch (e: Exception) {
                vmD("ping fail ${e.message}")
                _state.value = CompanionUiState.Error(e.message ?: "Ping failed")
            }
        }
    }

    fun saveBaseUrl(url: String) {
        viewModelScope.launch {
            vmD("saveBaseUrl host=${Uri.parse(Prefs.normalizeBaseUrl(url)).host}")
            try {
                assertUrlNotTabletOwnIp(Prefs.normalizeBaseUrl(url))
                prefs.setBaseUrl(url)
                refreshStatus()
            } catch (e: IllegalArgumentException) {
                vmD("saveBaseUrl invalid ${e.message}")
                _state.value = CompanionUiState.Error(e.message ?: "Invalid URL")
            } catch (e: Exception) {
                vmD("saveBaseUrl fail ${e.message}")
                _state.value = CompanionUiState.Error(e.message ?: "Save failed")
            }
        }
    }

    /**
     * Persists [url] and loads status without switching to full-screen [CompanionUiState.Error]
     * (for discovery sheet / product hub). Returns null on success, or a short error message.
     */
    /** Clear saved robot URL so another daemon can be selected from Find robot / product hub. */
    suspend fun disconnectRobot() {
        vmD("disconnectRobot")
        try {
            visionStop()
        } catch (_: Exception) {
        }
        prefs.clearBaseUrl()
        _jetsonLink.value = JetsonLinkState(false, null)
        _robotCapabilities.value = null
        _manifestActions.value = emptyList()
        _state.value = CompanionUiState.Ready(url = "", status = null, message = null)
    }

    fun isConnectedToDaemon(baseUrl: String): Boolean {
        val saved =
            when (val s = _state.value) {
                is CompanionUiState.Ready -> s.url.trim()
                else -> ""
            }
        if (saved.isBlank()) return false
        return Prefs.normalizeBaseUrl(saved).equals(
            Prefs.normalizeBaseUrl(baseUrl),
            ignoreCase = true,
        )
    }

    suspend fun connectDiscoveredAndRefresh(url: String): String? {
        return try {
            val norm = Prefs.normalizeBaseUrl(url)
            assertUrlNotTabletOwnIp(norm)
            prefs.setBaseUrl(norm)
            val (finalUrl, statusUi) = fetchStatusForSavedUrl(norm)
            val gw = DaemonUrlResolver.gatewayIpv4(appCtx)
            val myIp = DaemonUrlResolver.deviceIpv4(appCtx)
            _gatewayHint.value = buildDiscoveryHint(myIp, gw)
            _state.value = CompanionUiState.Ready(finalUrl, statusUi, null)
            _jetsonLink.value = JetsonLinkState(true, null)
            try {
                _robotCapabilities.value = client.capabilities(finalUrl)
            } catch (_: Exception) {
                _robotCapabilities.value = null
            }
            refreshManifestActions()
            null
        } catch (e: IllegalArgumentException) {
            e.message ?: "Invalid address"
        } catch (e: Exception) {
            e.message ?: "Could not connect"
        }
    }

    fun saveBearer(token: String?) {
        viewModelScope.launch {
            vmD("saveBearer hasToken=${!token.isNullOrBlank()}")
            prefs.setBearerToken(token)
            refreshStatus()
        }
    }

    fun setMode(mode: String) {
        viewModelScope.launch {
            vmD("setMode mode=$mode")
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.setMode(url, bearer, mode)
                refreshStatus()
            } catch (e: Exception) {
                vmD("setMode fail ${e.message}")
                _state.value = CompanionUiState.Error(e.message ?: "Mode failed")
            }
        }
    }

    fun saveHomeAndOptionallyConnect(ssid: String, password: String, connect: Boolean) {
        viewModelScope.launch {
            vmD("saveHomeWifi ssid=$ssid connect=$connect pwdLen=${password.length}")
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.saveHomeWifi(url, bearer, ssid, password)
                if (connect) {
                    client.connectHome(url, bearer, null)
                }
                refreshStatus()
            } catch (e: Exception) {
                vmD("saveHomeWifi fail ${e.message}")
                _state.value = CompanionUiState.Error(e.message ?: "Save Wi‑Fi failed")
            }
        }
    }

    fun connectJetsonHome(ssid: String?) {
        viewModelScope.launch {
            vmD("connectJetsonHome ssid=$ssid")
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.connectHome(url, bearer, ssid)
                refreshStatus()
            } catch (e: Exception) {
                vmD("connectJetsonHome fail ${e.message}")
                _state.value = CompanionUiState.Error(e.message ?: "Connect failed — check password on Jetson.")
            }
        }
    }

    fun startApOnJetson() {
        viewModelScope.launch {
            vmD("startApOnJetson")
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.startAp(url, bearer)
                refreshStatus()
            } catch (e: Exception) {
                vmD("startApOnJetson fail ${e.message}")
                _state.value = CompanionUiState.Error(e.message ?: "Could not start AP on Jetson")
            }
        }
    }

    fun deleteProfile(profileId: String) {
        viewModelScope.launch {
            vmD("deleteProfile id=$profileId")
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.deleteSaved(url, bearer, profileId)
                refreshStatus()
            } catch (e: Exception) {
                vmD("deleteProfile fail ${e.message}")
                _state.value = CompanionUiState.Error(e.message ?: "Delete failed")
            }
        }
    }

    fun pair(
        pin: String,
        onToken: (String) -> Unit,
        onNoToken: () -> Unit = {},
    ) {
        viewModelScope.launch {
            vmD("pair start pinLen=${pin.length} (pin not logged)")
            try {
                val url = prefs.baseUrl.first()
                val body = client.pair(url, pin)
                val token = body.optString("token", "")
                if (token.isNotBlank()) {
                    vmD("pair tokenReceived len=${token.length}")
                    prefs.setBearerToken(token)
                    onToken(token)
                } else {
                    vmD("pair noTokenInResponse")
                    onNoToken()
                }
                refreshStatus()
            } catch (e: Exception) {
                vmD("pair exception ${e.message}")
                _state.value = CompanionUiState.Error(e.message ?: "Pairing failed")
            }
        }
    }

    suspend fun loadRobotCapabilities(): JSONObject {
        vmD("loadRobotCapabilities")
        val url = prefs.baseUrl.first()
        return client.capabilities(url)
    }

    fun refreshManifestActions() {
        viewModelScope.launch {
            vmD("refreshManifestActions")
            NinaLog.api("GET", "/v1/actions")
            try {
                val url = prefs.baseUrl.first()
                val j = client.listActions(url)
                val arr = j.optJSONArray("actions") ?: JSONArray()
                val list = mutableListOf<ActionRowUi>()
                for (i in 0 until arr.length()) {
                    val o = arr.getJSONObject(i)
                    val off =
                        when {
                            !o.has("audio_offset") -> null
                            o.isNull("audio_offset") -> null
                            else -> o.optDouble("audio_offset").takeUnless { it.isNaN() }
                        }
                    val dur =
                        when {
                            !o.has("duration_sec") -> null
                            o.isNull("duration_sec") -> null
                            else -> o.optDouble("duration_sec").takeUnless { it.isNaN() }
                        }
                    val fc =
                        when {
                            !o.has("frame_count") -> null
                            o.isNull("frame_count") -> null
                            else -> o.optInt("frame_count").takeIf { it >= 0 }
                        }
                    list.add(
                        ActionRowUi(
                            name = o.optString("name"),
                            file = o.optString("file").takeIf { it.isNotBlank() },
                            audio = o.optString("audio").takeIf { it.isNotBlank() },
                            audioOffsetSec = off,
                            durationSec = dur,
                            frameCount = fc,
                        ),
                    )
                }
                _manifestActions.value = list.sortedBy { it.name.lowercase() }
                _manifestActionsError.value = null
                NinaLog.api("manifest_actions", "ok count=${list.size}")
            } catch (e: Exception) {
                NinaLog.warn("manifest_actions", e.message ?: "failed")
                _manifestActionsError.value = e.message
                _manifestActions.value = emptyList()
            }
        }
    }

    fun playManifestAction(name: String) {
        NinaLog.tap("Actions", "play_motion", name)
        playbackHintJob?.cancel()
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                NinaLog.api("POST", "/v1/actions/play action=$name")
                client.playAction(url, bearer, name)
                _manifestActionsError.value = null
                _actionPlaybackStatus.value = "Playing '$name'…"
                playbackHintJob =
                    launch {
                        delay(55_000)
                        _actionPlaybackStatus.value = null
                    }
            } catch (e: Exception) {
                NinaLog.warn("play_action", e.message ?: "failed")
                _actionPlaybackStatus.value = null
                _manifestActionsError.value = e.message ?: "Play failed"
            }
        }
    }

    /** Momentary drive pulse — requires Jetson `NINA_LINK_ENABLE_ROBOT_BRIDGE=1`. */
    suspend fun robotDriveMomentary(
        direction: String,
        durationMs: Int = 280,
        speedPercent: Int? = null,
    ): JSONObject {
        NinaLog.tap("Drive", "momentary", "$direction ${durationMs}ms speed=$speedPercent")
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        addDriveLog("send momentary dir=$direction ms=$durationMs speed=${speedPercent ?: "-"}")
        return try {
            val result = client.robotDriveMomentary(url, bearer, direction, durationMs, speedPercent)
            addDriveLog("recv momentary ok=${result.optBoolean("ok", true)} err=${result.optString("error").ifBlank { "-" }}")
            result
        } catch (e: Exception) {
            addDriveLog("recv momentary failed=${e.message ?: e.javaClass.simpleName}")
            throw e
        }
    }

    suspend fun robotSetBrake(on: Boolean): JSONObject {
        NinaLog.tap("Drive", "brake", if (on) "on" else "off")
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        return client.robotDriveBrake(url, bearer, on)
    }

    suspend fun robotDriveHold(direction: String): JSONObject {
        NinaLog.tap("Drive", "hold", direction)
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        return client.robotDriveHold(url, bearer, direction)
    }

    suspend fun robotDriveHoldStop(): JSONObject {
        NinaLog.tap("Drive", "hold", "stop")
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        return client.robotDriveHoldStop(url, bearer)
    }

    suspend fun robotDriveTurn(which: String): JSONObject {
        NinaLog.tap("Drive", "turn", which)
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        return client.robotDriveTurn(url, bearer, which)
    }

    suspend fun robotDriveReverse(on: Boolean): JSONObject {
        NinaLog.tap("Drive", "reverse", if (on) "on" else "off")
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        return client.robotDriveReverse(url, bearer, on)
    }

    suspend fun fetchRobotDriveStatus(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.robotDriveStatus(url, bearer)
        } catch (_: Exception) {
            null
        }

    suspend fun postRobotDriveInvert(left: Boolean?, right: Boolean?): JSONObject? {
        if (left == null && right == null) return null
        return try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            NinaLog.tap("Drive", "invert", "L=$left R=$right")
            addDriveLog("send invert left=${left ?: "-"} right=${right ?: "-"}")
            val result = client.robotDriveInvert(url, bearer, left, right)
            addDriveLog("recv invert ok=${result.optBoolean("ok", true)} err=${result.optString("error").ifBlank { "-" }}")
            result
        } catch (_: Exception) {
            addDriveLog("recv invert failed")
            null
        }
    }

    suspend fun robotDriveStraight(backward: Boolean): JSONObject {
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        NinaLog.tap("Drive", "straight", if (backward) "back" else "front")
        return client.robotDriveStraight(url, bearer, backward)
    }

    suspend fun robotDriveStraightStop(): JSONObject {
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        return client.robotDriveStraightStop(url, bearer)
    }

    suspend fun fetchDriveCalibration(): JSONObject? =
        try {
            client.robotDriveCalibrationGet(prefs.baseUrl.first())
        } catch (_: Exception) {
            null
        }

    suspend fun previewDriveCalibration(left: Int, right: Int): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.robotDriveCalibrationPreview(url, bearer, left, right)
        } catch (_: Exception) {
            null
        }

    suspend fun neutralDriveCalibration(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.robotDriveCalibrationNeutral(url, bearer)
        } catch (_: Exception) {
            null
        }

    suspend fun saveDriveCalibration(body: JSONObject): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.robotDriveCalibrationSave(url, bearer, body)
        } catch (_: Exception) {
            null
        }

    suspend fun postSystemDisplayName(name: String): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.systemDisplayName(url, bearer, name)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchVisionArucoStatus(): JSONObject? =
        try {
            client.visionArucoStatus(prefs.baseUrl.first())
        } catch (_: Exception) {
            null
        }

    suspend fun postVisionArucoStart(markerId: Int): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionArucoStart(url, bearer, markerId)
        } catch (_: Exception) {
            null
        }

    suspend fun postVisionArucoStop(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionArucoStop(url, bearer)
        } catch (_: Exception) {
            null
        }

    suspend fun robotEmergencyStop(): JSONObject {
        NinaLog.tap("Drive", "emergency_stop", "")
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        addDriveLog("send emergency-stop")
        return try {
            val result = client.robotEmergencyStop(url, bearer)
            addDriveLog("recv emergency-stop ok=${result.optBoolean("ok", true)} err=${result.optString("error").ifBlank { "-" }}")
            result
        } catch (e: Exception) {
            addDriveLog("recv emergency-stop failed=${e.message ?: e.javaClass.simpleName}")
            throw e
        }
    }

    fun requestJetsonShutdown(onResult: (String?) -> Unit) {
        NinaLog.tap("System", "jetson_poweroff", "")
        vmD("requestJetsonShutdown")
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.systemPoweroff(url, bearer)
                onResult(null)
            } catch (e: Exception) {
                onResult(e.message ?: "Poweroff request failed")
            }
        }
    }

    fun requestJetsonReboot(onResult: (String?) -> Unit) {
        NinaLog.tap("System", "jetson_reboot", "")
        vmD("requestJetsonReboot")
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.systemReboot(url, bearer)
                onResult(null)
            } catch (e: Exception) {
                onResult(e.message ?: "Reboot request failed")
            }
        }
    }

    private fun parseStatus(j: JSONObject): StatusUi {
        val saved = mutableListOf<SavedNetUi>()
        val arr = j.optJSONArray("saved_networks")
        if (arr != null) {
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                saved.add(
                    SavedNetUi(
                        o.optString("id"),
                        o.optString("uuid"),
                        o.optCleanString("ssid") ?: "",
                        nmAutoconnect = o.optBoolean("autoconnect", false),
                        wifiMode =
                            o.optString("wifi_mode").trim().lowercase().takeIf { it.isNotEmpty() }
                                ?: "infrastructure",
                    ),
                )
            }
        }
        return StatusUi(
            wifiRole = j.optCleanString("wifi_role") ?: "—",
            ipv4 = j.optCleanString("ipv4"),
            userMode = j.optCleanString("user_mode") ?: "—",
            bootWaitRemainingSec = j.optInt("boot_wait_remaining_sec"),
            clientSeen = j.optBoolean("client_seen"),
            lastError = j.optCleanString("last_error"),
            savedNetworks = saved,
            apSsid = j.optCleanString("ap_ssid"),
            activeStaSsid = j.optCleanString("active_sta_ssid"),
            activeStaProfile = j.optCleanString("active_sta_profile"),
            paired = j.optBoolean("paired"),
            systemId = j.optCleanString("system_id"),
            hostname = j.optCleanString("hostname"),
            displayName = j.optCleanString("display_name"),
        )
    }

    fun scanForDaemons() {
        viewModelScope.launch {
            vmD("scanForDaemons start")
            val myIp = DaemonUrlResolver.deviceIpv4(appCtx)
            _discoveryDiagnostics.value = _discoveryDiagnostics.value.copy(isScanning = true, lastError = null)
            @Suppress("DEPRECATION")
            val mlock =
                try {
                    (appCtx.getSystemService(Context.WIFI_SERVICE) as? WifiManager)
                        ?.createMulticastLock("nina-companion-mdns")
                        ?.apply {
                            setReferenceCounted(false)
                            acquire()
                        }
                } catch (_: Exception) {
                    null
                }
            val mdnsUrls =
                try {
                    BonjourJetsonFinder.discoverBaseUrls(appCtx, 3600L)
                } catch (e: Exception) {
                    NinaLog.warn("scanForDaemons", "mDNS: ${e.message}")
                    emptyList()
                } finally {
                    try {
                        if (mlock?.isHeld == true) mlock.release()
                    } catch (_: Exception) {
                    }
                }
            val fromMdns =
                withContext(Dispatchers.IO) {
                    val out = mutableListOf<LanDaemonScanner.DiscoveredDaemon>()
                    for (raw in mdnsUrls) {
                        val base = raw.trimEnd('/')
                        try {
                            val j = client.health(base)
                            out.add(
                                LanDaemonScanner.DiscoveredDaemon(
                                    baseUrl = base,
                                    systemId = j.optString("system_id").trim().takeIf { it.isNotEmpty() },
                                    hostname = j.optString("hostname").trim().takeIf { it.isNotEmpty() },
                                    displayName = j.optString("display_name").trim().takeIf { it.isNotEmpty() },
                                ),
                            )
                        } catch (_: Exception) {
                        }
                    }
                    out
                }
            val report = LanDaemonScanner.scanIpv4SubnetWithReport(myIp)
            val merged =
                (fromMdns + report.discovered)
                    .groupBy { it.baseUrl.lowercase() }
                    .map { (_, list) ->
                        val first = list.first()
                        LanDaemonScanner.DiscoveredDaemon(
                            baseUrl = first.baseUrl,
                            displayName = list.mapNotNull { it.displayName }.firstOrNull(),
                            systemId = list.mapNotNull { it.systemId }.firstOrNull(),
                            hostname = list.mapNotNull { it.hostname }.firstOrNull(),
                        )
                    }
                    .sortedBy { it.baseUrl.lowercase() }
            val rows =
                merged.map {
                    DiscoveredDaemonUi(
                        baseUrl = it.baseUrl,
                        systemId = it.systemId,
                        hostname = it.hostname,
                        displayName = it.displayName,
                    )
                }
            _discoveredDaemons.value = rows
            _discoveryDiagnostics.value =
                DiscoveryDiagnosticsUi(
                    isScanning = false,
                    deviceIpv4 = report.deviceIpv4,
                    subnetPrefix = report.subnetPrefix,
                    hostCount = report.hostCount,
                    probeAttempts = report.probeAttempts + mdnsUrls.size,
                    successfulHosts = merged.size,
                    failedProbes = report.failedProbes,
                    durationMs = report.durationMs,
                    lastError = report.error,
                )
            vmD("scanForDaemons done count=${rows.size} mdns=${mdnsUrls.size}")
        }
    }

    suspend fun fetchRecordStatus(): JSONObject? =
        try {
            vmD("fetchRecordStatus")
            val url = prefs.baseUrl.first()
            client.recordStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun startRemoteRecord(
        name: String,
        seconds: Double = 5.0,
        hz: Double = 20.0,
        countdown: Double = 3.0,
        holdAfter: Boolean = false,
        register: Boolean = true,
    ): String? {
        vmD("startRemoteRecord name=$name sec=$seconds hz=$hz")
        return try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            val resp =
                client.recordStart(url, bearer, name.trim(), seconds, hz, countdown, holdAfter, register)
            if (!resp.optBoolean("ok", true)) {
                resp.jsonCleanString("error")
                    ?: "Recording did not start (server rejected request)."
            } else {
                null
            }
        } catch (e: LinkApiException) {
            vmD("startRemoteRecord LinkApi ${e.code}")
            normalizeRemoteError(e)
        } catch (e: Exception) {
            vmD("startRemoteRecord fail ${e.message}")
            normalizeRemoteError(e)
        }
    }

    suspend fun stopRemoteRecord(): String? {
        vmD("stopRemoteRecord")
        return try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            val resp = client.recordStop(url, bearer)
            if (!resp.optBoolean("ok", true)) {
                resp.jsonCleanString("error") ?: "Could not stop recording."
            } else {
                null
            }
        } catch (e: LinkApiException) {
            normalizeRemoteError(e)
        } catch (e: Exception) {
            normalizeRemoteError(e)
        }
    }

    suspend fun deleteManifestAction(
        actionName: String,
        deleteRecording: Boolean = true,
        deleteAudio: Boolean = false,
    ): String? =
        try {
            vmD("deleteManifestAction name=$actionName rec=$deleteRecording audio=$deleteAudio")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.deleteManifestAction(url, bearer, actionName, deleteRecording, deleteAudio)
            null
        } catch (e: LinkApiException) {
            vmD("deleteManifestAction LinkApi ${e.code}")
            normalizeRemoteError(e)
        } catch (e: Exception) {
            vmD("deleteManifestAction ${e.message}")
            normalizeRemoteError(e)
        }

    private fun normalizeRemoteError(e: Exception): String =
        when (e) {
            is LinkApiException -> friendlyHttp(e)
            else -> {
                val m = e.message?.trim()
                if (m.isNullOrBlank() || m.equals("null", ignoreCase = true)) {
                    "Request failed (${e.javaClass.simpleName})"
                } else {
                    m
                }
            }
        }

    suspend fun fetchDaemonHealth(): JSONObject? =
        try {
            vmD("fetchDaemonHealth")
            val url = prefs.baseUrl.first()
            client.health(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchActionAudioInfo(action: String): JSONObject? =
        try {
            vmD("fetchActionAudioInfo action=$action")
            val url = prefs.baseUrl.first()
            client.actionAudioInfo(url, action)
        } catch (_: Exception) {
            null
        }

    suspend fun postActionAudioOffset(action: String, audioOffsetSec: Double): String? =
        try {
            vmD("postActionAudioOffset action=$action off=$audioOffsetSec")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.actionAudioOffset(url, bearer, action, audioOffsetSec)
            null
        } catch (e: Exception) {
            e.message
        }

    suspend fun postActionAudioClear(action: String): String? =
        try {
            vmD("postActionAudioClear action=$action")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.actionAudioClear(url, bearer, action)
            null
        } catch (e: Exception) {
            e.message
        }

    suspend fun postActionAudioPreview(action: String): String? =
        try {
            vmD("postActionAudioPreview action=$action")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.actionAudioPreview(url, bearer, action)
            null
        } catch (e: Exception) {
            e.message
        }

    suspend fun postActionAudioGenerate(
        action: String,
        text: String,
        lang: String,
        tld: String,
        audioOffsetSec: Double,
        slow: Boolean = false,
    ): String? =
        try {
            vmD("postActionAudioGenerate action=$action lang=$lang textLen=${text.length}")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.actionAudioGenerate(url, bearer, action, text, lang, tld, audioOffsetSec, slow)
            null
        } catch (e: Exception) {
            e.message
        }

    suspend fun fetchVisionStatus(): JSONObject? =
        try {
            vmD("fetchVisionStatus")
            val url = prefs.baseUrl.first()
            client.visionStatus(url)
        } catch (_: Exception) {
            null
        }

    /** Apply vision toggles and return the daemon JSON (includes toggle_*_error on failure). */
    suspend fun postVisionOptionsSync(
        face: Boolean?,
        objects: Boolean?,
        objectConfidence: Double? = null,
        resolution: String? = null,
    ): JSONObject? =
        try {
            vmD("postVisionOptionsSync face=$face objects=$objects conf=$objectConfidence res=$resolution")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionOptions(url, bearer, face, objects, objectConfidence, resolution)
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            NinaLog.warn("vision_options", e.message ?: "failed")
            null
        }

    suspend fun visionOpen(): String? =
        try {
            vmD("visionOpen")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionOpen(url, bearer)
            null
        } catch (e: Exception) {
            e.message
        }

    suspend fun visionStop(): String? =
        try {
            vmD("visionStop")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionStop(url, bearer)
            null
        } catch (e: Exception) {
            e.message
        }

    /** Start face enrollment; second value is a human-readable network/auth error when present. */
    suspend fun visionEnroll(name: String, targetSamples: Int = 8): Pair<JSONObject?, String?> =
        try {
            vmD("visionEnroll name=$name samples=$targetSamples")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            Pair(client.visionEnroll(url, bearer, name, targetSamples), null)
        } catch (e: LinkApiException) {
            Pair(null, friendlyHttp(e))
        } catch (e: Exception) {
            Pair(null, normalizeRemoteError(e))
        }

    suspend fun fetchVisionEnrollStatus(): JSONObject? =
        try {
            vmD("fetchVisionEnrollStatus")
            val url = prefs.baseUrl.first()
            client.visionEnrollStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun visionAnnounceObjects(): JSONObject? =
        try {
            vmD("visionAnnounceObjects")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionAnnounce(url, bearer)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchVisionAnnounceStatus(): JSONObject? =
        try {
            vmD("fetchVisionAnnounceStatus")
            val url = prefs.baseUrl.first()
            client.visionAnnounceStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchVisionDetections(): JSONObject? =
        try {
            vmD("fetchVisionDetections")
            val url = prefs.baseUrl.first()
            client.visionDetections(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchVisionFaces(): JSONObject? =
        try {
            vmD("fetchVisionFaces")
            val url = prefs.baseUrl.first()
            client.visionFaces(url)
        } catch (_: Exception) {
            null
        }

    suspend fun visionFollowStart(target: String): JSONObject? =
        try {
            vmD("visionFollowStart target=$target")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionFollowStart(url, bearer, target)
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            NinaLog.warn("vision_follow_start", e.message ?: "failed")
            null
        }

    suspend fun visionFollowStop(): JSONObject? =
        try {
            vmD("visionFollowStop")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionFollowStop(url, bearer)
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            NinaLog.warn("vision_follow_stop", e.message ?: "failed")
            null
        }

    suspend fun fetchVisionFollowStatus(): JSONObject? =
        try {
            vmD("fetchVisionFollowStatus")
            val url = prefs.baseUrl.first()
            client.visionFollowStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchVisionSnapshotJpeg(): ByteArray? =
        try {
            vmD("fetchVisionSnapshotJpeg")
            val url = prefs.baseUrl.first()
            client.visionSnapshotJpeg(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchSlamStatus(): JSONObject? =
        try {
            vmD("fetchSlamStatus")
            val url = prefs.baseUrl.first()
            client.slamStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchSlamSnapshot(): JSONObject? =
        try {
            vmD("fetchSlamSnapshot")
            val url = prefs.baseUrl.first()
            client.slamSnapshot(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchSlamOccupancyGrid(): SlamOccupancyGrid? =
        try {
            vmD("fetchSlamOccupancyGrid")
            val url = prefs.baseUrl.first()
            client.slamOccupancyGrid(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchRobotHealth(): JSONObject? =
        try {
            vmD("fetchRobotHealth")
            val url = prefs.baseUrl.first()
            client.robotHealth(url)
        } catch (_: Exception) {
            null
        }

    /** Jetson ALSA/Pulse output level (`GET /v1/system/volume`). */
    suspend fun fetchSystemVolumePct(): Int? =
        try {
            val url = prefs.baseUrl.first()
            if (url.isBlank()) {
                null
            } else {
                val j = client.systemVolumeGet(url)
                if (!j.optBoolean("ok", false) || !j.optBoolean("available", false)) {
                    null
                } else {
                    j.optInt("volume_pct").takeIf { !j.isNull("volume_pct") }
                }
            }
        } catch (_: Exception) {
            null
        }

    /**
     * Set Jetson speaker volume (`POST /v1/system/volume`, requires pair token).
     * Returns null on success, or a short error for the UI.
     */
    suspend fun setSystemVolumePct(pct: Int): String? =
        try {
            val url = prefs.baseUrl.first()
            if (url.isBlank()) return "No robot URL"
            val bearer = prefs.bearerToken.first()
            if (bearer.isNullOrBlank()) {
                return "Pair with the robot first to change volume"
            }
            val j = client.systemVolumeSet(url, bearer, pct)
            when {
                j.optBoolean("ok", false) && j.optBoolean("available", true) -> null
                !j.optBoolean("available", true) ->
                    "Volume control unavailable on robot (install alsa-utils or check audio sink)"
                else ->
                    j.optString("detail").trim().ifBlank {
                        j.optString("error").trim().ifBlank { "Volume change failed" }
                    }
            }
        } catch (e: Exception) {
            e.message?.trim().orEmpty().ifBlank { "Volume change failed" }
        }

    /** Warm Dynamixel + hoverboard stack while the Drive screen is open (reduces first D-pad delay). */
    suspend fun prefetchRobotDriveStatus() {
        if (prefs.baseUrl.first().isBlank()) return
        repeat(4) {
            fetchRobotDriveStatus()
            delay(180L)
        }
    }

    suspend fun saveSlamMapPgm(filename: String): JSONObject? =
        try {
            vmD("saveSlamMapPgm file=$filename")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.slamSave(url, bearer, filename)
        } catch (e: LinkApiException) {
            JSONObject().put("ok", false).put("detail", e.message ?: "HTTP ${e.code}")
        } catch (_: Exception) {
            null
        }

    suspend fun postSlamRunning(running: Boolean): JSONObject? =
        try {
            vmD("postSlamRunning running=$running")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.slamSetRunning(url, bearer, running)
        } catch (e: LinkApiException) {
            JSONObject().put("ok", false).put("detail", e.message ?: "HTTP ${e.code}")
        } catch (_: Exception) {
            null
        }

    /** POST /v1/slam/clear — same reset as Qt Map screen Clear (SLAM stop/start; autonomy off if was on). */
    suspend fun postSlamClear(): JSONObject? =
        try {
            vmD("postSlamClear")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.slamClear(url, bearer)
        } catch (e: LinkApiException) {
            JSONObject().put("ok", false).put("detail", e.message ?: "HTTP ${e.code}")
        } catch (_: Exception) {
            null
        }

    suspend fun fetchDepthStatus(): JSONObject? =
        try {
            vmD("fetchDepthStatus")
            val url = prefs.baseUrl.first()
            client.depthStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchAutonomyStatus(): JSONObject? =
        try {
            vmD("fetchAutonomyStatus")
            val url = prefs.baseUrl.first()
            client.autonomyStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun postAutonomyEnabled(enabled: Boolean): JSONObject? =
        try {
            vmD("postAutonomyEnabled enabled=$enabled")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.setAutonomyEnabled(url, bearer, enabled)
        } catch (_: Exception) {
            null
        }

    /** POST /v1/autonomy/goal — arm goto with the given world-mm coordinates. */
    suspend fun postAutonomyGoal(xMm: Double, yMm: Double): JSONObject? =
        try {
            vmD("postAutonomyGoal x=$xMm y=$yMm")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.setAutonomyGoal(url, bearer, xMm, yMm)
        } catch (_: Exception) {
            null
        }

    /** DELETE /v1/autonomy/goal — cancel an in-flight goto. */
    suspend fun deleteAutonomyGoal(): JSONObject? =
        try {
            vmD("deleteAutonomyGoal")
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.clearAutonomyGoal(url, bearer)
        } catch (_: Exception) {
            null
        }

    fun sessionClaim(onResult: (String?) -> Unit) {
        vmD("sessionClaim")
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.sessionClaim(url, bearer)
                robotConsoleSessionActive = true
                vmD("sessionClaim ok")
                onResult(null)
            } catch (e: LinkApiException) {
                vmD("sessionClaim LinkApi code=${e.code}")
                if (e.code == 503) {
                    onResult(null)
                } else {
                    onResult(e.message)
                }
            } catch (e: Exception) {
                vmD("sessionClaim fail ${e.message}")
                onResult(e.message)
            }
        }
    }

    fun sessionRelease(onResult: (String?) -> Unit) {
        vmD("sessionRelease")
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.sessionRelease(url, bearer)
                vmD("sessionRelease ok")
                onResult(null)
            } catch (e: Exception) {
                vmD("sessionRelease fail ${e.message}")
                onResult(e.message)
            } finally {
                robotConsoleSessionActive = false
            }
        }
    }

    /**
     * Opening the full Nina console should pause the on-robot kiosk so `nina-link` can open USB/GPIO
     * (see `NINA_LINK_SESSION_SCRIPT` on the Jetson). Closing the console releases.
     */
    fun notifyRobotConsoleVisibility(visible: Boolean) {
        vmD("notifyRobotConsoleVisibility visible=$visible active=$robotConsoleSessionActive")
        viewModelScope.launch {
            if (visible) {
                if (robotConsoleSessionActive) return@launch
                try {
                    val url = prefs.baseUrl.first()
                    val bearer = prefs.bearerToken.first()
                    client.sessionClaim(url, bearer)
                    robotConsoleSessionActive = true
                    NinaLog.api("session_claim", "robot console opened")
                } catch (e: LinkApiException) {
                    if (e.code != 503) {
                        NinaLog.warn("Session", e.message ?: "claim")
                    }
                } catch (e: Exception) {
                    NinaLog.warn("Session", e.message ?: "claim")
                }
            } else {
                if (!robotConsoleSessionActive) return@launch
                try {
                    val url = prefs.baseUrl.first()
                    val bearer = prefs.bearerToken.first()
                    client.sessionRelease(url, bearer)
                    NinaLog.api("session_release", "robot console closed")
                } catch (e: Exception) {
                    NinaLog.warn("Session", e.message ?: "release")
                } finally {
                    robotConsoleSessionActive = false
                }
            }
        }
    }

    override fun onCleared() {
        vmD("onCleared sessionActive=$robotConsoleSessionActive")
        if (robotConsoleSessionActive) {
            runBlocking {
                try {
                    val url = prefs.baseUrl.first()
                    val bearer = prefs.bearerToken.first()
                    client.sessionRelease(url, bearer)
                } catch (_: Exception) {
                    // best-effort — process is dying
                } finally {
                    robotConsoleSessionActive = false
                }
            }
        }
        super.onCleared()
    }

    suspend fun mediaFileUrl(relativePath: String): String {
        vmD("mediaFileUrl relLen=${relativePath.length}")
        val base = prefs.baseUrl.first().trimEnd('/')
        val enc = java.net.URLEncoder.encode(relativePath, Charsets.UTF_8.toString())
        return "$base/v1/media/file?relative=$enc"
    }

    private fun friendlyHttp(e: LinkApiException): String {
        val raw = e.message?.trim()
        val cleaned =
            if (raw.isNullOrBlank() || raw.equals("null", ignoreCase = true)) {
                null
            } else {
                raw
            }
        if (e.code == 401) {
            return "Unauthorized — set a fleet token or pair with PIN (Setup tab)."
        }
        return cleaned ?: "HTTP ${e.code}"
    }

    private fun addDriveLog(message: String) {
        val row = DriveCommandLogUi(timestamp = driveLogTimeFmt.format(Date()), line = message)
        _driveCommandLog.update { (it + row).takeLast(80) }
    }
}

/** JSON string fields: treat blank and literal `"null"` as absent (some intermediaries stringify null). */
private fun JSONObject.optCleanString(key: String): String? {
    if (!has(key) || isNull(key)) return null
    val s = optString(key).trim()
    if (s.isEmpty() || s.equals("null", ignoreCase = true)) return null
    return s
}
