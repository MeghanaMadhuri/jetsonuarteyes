package com.sirena.nina.companion.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import com.sirena.nina.companion.network.IdempotentRetryInterceptor
import com.sirena.nina.companion.util.NinaLog
import okhttp3.ConnectionPool
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** HTTP client for the Jetson tablet gateway (same routes as `sirena_ui/android_gateway/fastapi_app.py`). */
class LinkClient {

    /** Tuned for Wi‑Fi: longer reads for MJPEG/JSON, HTTP/1.1 only, idempotent GET retries. */
    private val client =
        OkHttpClient.Builder()
            .connectionPool(ConnectionPool(8, 5, TimeUnit.MINUTES))
            .protocols(listOf(Protocol.HTTP_1_1))
            .connectTimeout(25, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .callTimeout(0, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .addInterceptor(IdempotentRetryInterceptor(maxRetries = 2, backoffStartMs = 140L))
            .build()

    /** Stop / status / E-stop — Jetson urgent queue, no long prime. */
    private val driveFastClient =
        OkHttpClient.Builder()
            .connectionPool(ConnectionPool(6, 2, TimeUnit.MINUTES))
            .protocols(listOf(Protocol.HTTP_1_1))
            .connectTimeout(4, TimeUnit.SECONDS)
            .readTimeout(8, TimeUnit.SECONDS)
            .writeTimeout(6, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()

    /**
     * Hold start, brake release, straight, turn — short prime when hardware is warm
     * (Drive screen prefetch + gateway bootstrap).
     */
    private val driveCommandClient =
        OkHttpClient.Builder()
            .connectionPool(ConnectionPool(4, 2, TimeUnit.MINUTES))
            .protocols(listOf(Protocol.HTTP_1_1))
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(12, TimeUnit.SECONDS)
            .writeTimeout(8, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()

    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    suspend fun health(baseUrl: String): JSONObject = withContext(Dispatchers.IO) {
        get("$baseUrl/health")
    }

    suspend fun status(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/status", bearer)
        }

    suspend fun setMode(baseUrl: String, bearer: String?, mode: String): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/mode",
                bearer,
                JSONObject().put("mode", mode).toString(),
            )
        }

    suspend fun saveHomeWifi(
        baseUrl: String,
        bearer: String?,
        ssid: String,
        password: String,
    ): JSONObject = withContext(Dispatchers.IO) {
        post(
            "$baseUrl/v1/wifi/home-credentials",
            bearer,
            JSONObject()
                .put("ssid", ssid)
                .put("password", password)
                .toString(),
        )
    }

    suspend fun connectHome(baseUrl: String, bearer: String?, ssid: String?): JSONObject =
        withContext(Dispatchers.IO) {
            val url = buildString {
                append("$baseUrl/v1/wifi/connect-home")
                if (!ssid.isNullOrBlank()) append("?ssid=${java.net.URLEncoder.encode(ssid, Charsets.UTF_8.name())}")
            }
            post(url, bearer, "{}")
        }

    suspend fun startAp(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/wifi/start-ap", bearer, "{}")
        }

    suspend fun deleteSaved(baseUrl: String, bearer: String?, profileId: String): JSONObject =
        withContext(Dispatchers.IO) {
            delete("$baseUrl/v1/wifi/saved/${java.net.URLEncoder.encode(profileId, Charsets.UTF_8.name())}", bearer)
        }

    suspend fun pair(baseUrl: String, pin: String): JSONObject = withContext(Dispatchers.IO) {
        post(
            "$baseUrl/v1/pair",
            null,
            JSONObject().put("pin", pin).toString(),
        )
    }

    suspend fun capabilities(baseUrl: String): JSONObject = withContext(Dispatchers.IO) {
        get("$baseUrl/v1/robot/capabilities")
    }

    /** Aggregated subsystem health (lidar, BLDC, vision, depth, …) for the Health screen. */
    suspend fun robotHealth(baseUrl: String): JSONObject = withContext(Dispatchers.IO) {
        get("$baseUrl/v1/robot/health")
    }

    suspend fun systemVolumeGet(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/system/volume")
        }

    suspend fun systemVolumeSet(
        baseUrl: String,
        bearer: String?,
        volumePct: Int,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/system/volume",
                bearer,
                JSONObject().put("volume_pct", volumePct.coerceIn(0, 100)).toString(),
            )
        }

    /** Save current SLAM grid as PGM under ``nina/data/maps/`` on the Jetson. */
    suspend fun slamSave(
        baseUrl: String,
        bearer: String?,
        filename: String,
    ): JSONObject = withContext(Dispatchers.IO) {
        post(
            "$baseUrl/v1/slam/save",
            bearer,
            JSONObject().put("filename", filename).toString(),
        )
    }

    /** Start or stop SLAM mapping (``POST /v1/slam/running`` — same as Qt Map screen). */
    suspend fun slamSetRunning(
        baseUrl: String,
        bearer: String?,
        running: Boolean,
    ): JSONObject = withContext(Dispatchers.IO) {
        post(
            "$baseUrl/v1/slam/running",
            bearer,
            JSONObject().put("running", running).toString(),
        )
    }

    /** Reset SLAM map like Qt Map **Clear** (``POST /v1/slam/clear``). */
    suspend fun slamClear(
        baseUrl: String,
        bearer: String?,
    ): JSONObject = withContext(Dispatchers.IO) {
        post("$baseUrl/v1/slam/clear", bearer, "{}")
    }

    suspend fun robotDriveMomentary(
        baseUrl: String,
        bearer: String?,
        direction: String,
        durationMs: Int,
        speedPercent: Int? = null,
    ): JSONObject = withContext(Dispatchers.IO) {
        val json = JSONObject().put("direction", direction).put("duration_ms", durationMs)
        if (speedPercent != null) json.put("speed_percent", speedPercent)
        postDriveCommand("$baseUrl/v1/robot/drive", bearer, json.toString())
    }

    /** Kiosk D-pad press: ``DriveController.drive`` until [robotDriveHoldStop]. */
    suspend fun robotDriveHold(
        baseUrl: String,
        bearer: String?,
        direction: String,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            postDriveCommand(
                "$baseUrl/v1/robot/drive/hold",
                bearer,
                JSONObject().put("direction", direction).toString(),
            )
        }

    suspend fun robotDriveHoldStop(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            postDrive("$baseUrl/v1/robot/drive/hold/stop", bearer, "{}")
        }

    /** Kiosk Turn left/right (``DriveController.turn_90``). */
    suspend fun robotDriveTurn(
        baseUrl: String,
        bearer: String?,
        which: String,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            postDriveCommand(
                "$baseUrl/v1/robot/drive/turn",
                bearer,
                JSONObject().put("which", which).toString(),
            )
        }

    suspend fun robotDriveReverse(
        baseUrl: String,
        bearer: String?,
        on: Boolean,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/robot/drive/reverse",
                bearer,
                JSONObject().put("on", on).toString(),
            )
        }

    /** Same stack as kiosk ``DriveController.set_brake`` (servo brake pose on lean axes). */
    suspend fun robotDriveBrake(baseUrl: String, bearer: String?, on: Boolean): JSONObject =
        withContext(Dispatchers.IO) {
            val body = JSONObject().put("on", on).toString()
            val url = "$baseUrl/v1/robot/drive/brake"
            if (on) {
                postDrive(url, bearer, body)
            } else {
                postDriveCommand(url, bearer, body)
            }
        }

    suspend fun robotEmergencyStop(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            postDrive("$baseUrl/v1/robot/emergency-stop", bearer, "{}")
        }

    /** Ask the Jetson host to power off (requires passwordless sudo on the robot — see nina-link docs). */
    suspend fun systemPoweroff(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/system/poweroff", bearer, "{}")
        }

    /** Ask the Jetson host to reboot (requires passwordless sudo on the robot — see nina-link docs). */
    suspend fun systemReboot(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/system/reboot", bearer, "{}")
        }

    /** BLDC hardware readiness (lazy NavigationManager probe; matches desktop Drive pill). */
    suspend fun robotDriveStatus(baseUrl: String, bearer: String? = null): JSONObject =
        withContext(Dispatchers.IO) {
            getDrive("$baseUrl/v1/robot/drive/status", bearer)
        }

    /** Per-wheel polarity flip (matches Qt Drive Flip L/R). */
    suspend fun robotDriveInvert(
        baseUrl: String,
        bearer: String?,
        left: Boolean?,
        right: Boolean?,
    ): JSONObject = withContext(Dispatchers.IO) {
        val json = JSONObject()
        if (left != null) json.put("left", left)
        if (right != null) json.put("right", right)
        post("$baseUrl/v1/robot/drive/invert", bearer, json.toString())
    }

    suspend fun robotDriveStraight(
        baseUrl: String,
        bearer: String?,
        backward: Boolean,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            postDriveCommand(
                "$baseUrl/v1/robot/drive/straight",
                bearer,
                JSONObject().put("backward", backward).toString(),
            )
        }

    suspend fun robotDriveStraightStop(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            postDrive("$baseUrl/v1/robot/drive/straight/stop", bearer, "{}")
        }

    suspend fun robotDriveCalibrationGet(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/robot/drive/calibration")
        }

    suspend fun robotDriveCalibrationPreview(
        baseUrl: String,
        bearer: String?,
        left: Int,
        right: Int,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/robot/drive/calibration/preview",
                bearer,
                JSONObject().put("left", left).put("right", right).toString(),
            )
        }

    suspend fun robotDriveCalibrationNeutral(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/robot/drive/calibration/neutral", bearer, "{}")
        }

    suspend fun robotDriveCalibrationSave(
        baseUrl: String,
        bearer: String?,
        body: JSONObject,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/robot/drive/calibration/save", bearer, body.toString())
        }

    suspend fun systemDisplayName(
        baseUrl: String,
        bearer: String?,
        displayName: String,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/system/display-name",
                bearer,
                JSONObject().put("display_name", displayName).toString(),
            )
        }

    /** Manifest-backed action list from the Jetson (`nina/actions/manifest.json`). */
    suspend fun listActions(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/actions")
        }

    /** Runs a named action on the Jetson when `NINA_LINK_ENABLE_ACTION_BRIDGE=1`. */
    suspend fun playAction(baseUrl: String, bearer: String?, actionName: String): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/actions/play",
                bearer,
                JSONObject().put("action", actionName).toString(),
            )
        }

    suspend fun listRecordings(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) { get("$baseUrl/v1/actions/recordings") }

    suspend fun recordStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) { get("$baseUrl/v1/actions/record/status") }

    suspend fun recordStart(
        baseUrl: String,
        bearer: String?,
        name: String,
        seconds: Double,
        hz: Double,
        countdown: Double,
        holdAfter: Boolean,
        register: Boolean,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val body =
                JSONObject()
                    .put("name", name)
                    .put("seconds", seconds)
                    .put("hz", hz)
                    .put("countdown", countdown)
                    .put("hold_after", holdAfter)
                    .put("register", register)
            post("$baseUrl/v1/actions/record/start", bearer, body.toString())
        }

    suspend fun recordStop(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/actions/record/stop", bearer, "{}")
        }

    /** Jetson manifest audio editor (`GET /v1/actions/audio/info`). */
    suspend fun actionAudioInfo(baseUrl: String, action: String): JSONObject =
        withContext(Dispatchers.IO) {
            val enc = java.net.URLEncoder.encode(action, Charsets.UTF_8.name())
            get("$baseUrl/v1/actions/audio/info?action=$enc")
        }

    suspend fun actionAudioOffset(
        baseUrl: String,
        bearer: String?,
        action: String,
        audioOffsetSec: Double,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val body =
                JSONObject()
                    .put("action", action)
                    .put("audio_offset", audioOffsetSec)
            post("$baseUrl/v1/actions/audio/offset", bearer, body.toString())
        }

    suspend fun actionAudioClear(baseUrl: String, bearer: String?, action: String): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/actions/audio/clear",
                bearer,
                JSONObject().put("action", action).toString(),
            )
        }

    /** Play manifest audio clip on the Jetson speakers (`POST /v1/actions/audio/preview`). */
    suspend fun actionAudioPreview(baseUrl: String, bearer: String?, action: String): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/actions/audio/preview",
                bearer,
                JSONObject().put("action", action).toString(),
            )
        }

    suspend fun actionAudioGenerate(
        baseUrl: String,
        bearer: String?,
        action: String,
        text: String,
        lang: String,
        tld: String,
        audioOffsetSec: Double,
        slow: Boolean = false,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val body =
                JSONObject()
                    .put("action", action)
                    .put("text", text)
                    .put("lang", lang)
                    .put("tld", tld)
                    .put("audio_offset", audioOffsetSec)
                    .put("slow", slow)
            post("$baseUrl/v1/actions/audio/generate", bearer, body.toString())
        }

    /** Remove manifest entry and optionally delete files (`POST /v1/actions/delete`). */
    suspend fun deleteManifestAction(
        baseUrl: String,
        bearer: String?,
        action: String,
        deleteRecording: Boolean = true,
        deleteAudio: Boolean = false,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val body =
                JSONObject()
                    .put("action", action)
                    .put("delete_recording", deleteRecording)
                    .put("delete_audio", deleteAudio)
            post("$baseUrl/v1/actions/delete", bearer, body.toString())
        }

    suspend fun visionStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) { get("$baseUrl/v1/vision/status") }

    suspend fun visionOptions(
        baseUrl: String,
        bearer: String?,
        face: Boolean?,
        objects: Boolean?,
        objectConfidence: Double?,
        resolution: String? = null,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val o = JSONObject()
            if (face != null) o.put("face", face)
            if (objects != null) o.put("objects", objects)
            if (objectConfidence != null) o.put("object_confidence", objectConfidence)
            if (resolution != null) o.put("resolution", resolution)
            post("$baseUrl/v1/vision/options", bearer, o.toString())
        }

    suspend fun visionArucoStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/vision/aruco/status")
        }

    suspend fun visionArucoStart(
        baseUrl: String,
        bearer: String?,
        markerId: Int,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/vision/aruco/start",
                bearer,
                JSONObject().put("marker_id", markerId).toString(),
            )
        }

    suspend fun visionArucoStop(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/vision/aruco/stop", bearer, "{}")
        }

    suspend fun visionOpen(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/vision/open", bearer, "{}")
        }

    suspend fun visionStop(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/vision/stop", bearer, "{}")
        }

    /** Queue face enrollment on the Jetson (same 8-sample flow as Sirena UI). */
    suspend fun visionEnroll(
        baseUrl: String,
        bearer: String?,
        name: String,
        targetSamples: Int = 8,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/vision/enroll",
                bearer,
                JSONObject()
                    .put("name", name)
                    .put("target_samples", targetSamples)
                    .toString(),
            )
        }

    suspend fun visionEnrollStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/vision/enroll/status")
        }

    /** gTTS + play on robot for current object labels (matches desktop “Play objects”). */
    suspend fun visionAnnounce(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/vision/announce", bearer, "{}")
        }

    suspend fun visionAnnounceStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/vision/announce/status")
        }

    suspend fun visionDetections(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/vision/detections")
        }

    /** Enrolled face names for person-follow target selection (matches Qt combo). */
    suspend fun visionFaces(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/vision/faces")
        }

    /**
     * Start vision-guided person follow on the Jetson (``FaceFollowController``).
     * [target] empty = largest face; else an enrolled identity name.
     */
    suspend fun visionFollowStart(
        baseUrl: String,
        bearer: String?,
        target: String,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/vision/follow/start",
                bearer,
                JSONObject().put("target", target).toString(),
            )
        }

    suspend fun visionFollowStop(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/vision/follow/stop", bearer, "{}")
        }

    suspend fun visionFollowStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/vision/follow/status")
        }

    /**
     * Single JPEG frame from the live vision pipeline (``GET /v1/vision/snapshot``).
     * Returns null on HTTP error or empty body.
     */
    suspend fun visionSnapshotJpeg(baseUrl: String): ByteArray? =
        withContext(Dispatchers.IO) {
            val url = "$baseUrl/v1/vision/snapshot"
            val req = Request.Builder()
                .url(url)
                .header("Accept", "image/jpeg")
                .get()
                .build()
            val snapLabel = safeRequestLabel(req)
            NinaLog.debug("LinkClient", ">> $snapLabel (jpeg)")
            try {
                client.newCall(req).execute().use { resp ->
                    val bytes = resp.body?.bytes()
                    NinaLog.debug(
                        "LinkClient",
                        "<< $snapLabel http=${resp.code} jpegBytes=${bytes?.size ?: 0}",
                    )
                    if (!resp.isSuccessful) {
                        if (resp.code != 404) {
                            NinaLog.warn(
                                "LinkClient",
                                "GET $url -> ${resp.code} ${resp.message}",
                            )
                        }
                        return@withContext null
                    }
                    bytes
                }
            } catch (e: IOException) {
                NinaLog.warn("LinkClient", "<< $snapLabel IOException: ${e.message}")
                null
            }
        }

    suspend fun sessionClaim(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/session/claim", bearer, "{}")
        }

    suspend fun sessionRelease(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/session/release", bearer, "{}")
        }

    suspend fun slamStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/slam/status")
        }

    suspend fun slamSnapshot(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/slam/snapshot")
        }

    /**
     * Raw occupancy grid (`application/octet-stream`) plus dimensions from response headers.
     */
    suspend fun slamOccupancyGrid(baseUrl: String): SlamOccupancyGrid? =
        withContext(Dispatchers.IO) {
            val url = "$baseUrl/v1/slam/occupancy".toHttpUrlOrNull() ?: return@withContext null
            val req = Request.Builder().url(url).get().build()
            val occLabel = safeRequestLabel(req)
            NinaLog.debug("LinkClient", ">> $occLabel (octet-stream)")
            client.newCall(req).execute().use { resp ->
                NinaLog.debug("LinkClient", "<< $occLabel http=${resp.code}")
                if (!resp.isSuccessful) return@withContext null
                val w = resp.header("X-Slam-Width")?.toIntOrNull() ?: return@withContext null
                val h = resp.header("X-Slam-Height")?.toIntOrNull() ?: return@withContext null
                val bytes = resp.body?.bytes() ?: return@withContext null
                NinaLog.debug("LinkClient", "slam occupancy grid w=$w h=${h} rawBytes=${bytes.size}")
                if (bytes.size < w * h) return@withContext null
                SlamOccupancyGrid(bytes, w, h)
            }
        }

    suspend fun depthStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/depth/status")
        }

    suspend fun autonomyStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/autonomy/status")
        }

    suspend fun setAutonomyEnabled(
        baseUrl: String,
        bearer: String?,
        enabled: Boolean,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/autonomy/enabled",
                bearer,
                JSONObject().put("enabled", enabled).toString(),
            )
        }

    /**
     * POST /v1/autonomy/goal — arm the goto pilot to drive to (x, y) mm.
     * World frame = SLAM map frame: origin at map centre, +x right, +y forward.
     * Caller converts a tap on the occupancy bitmap to mm using the snapshot's
     * scale + width/height before invoking this.
     */
    suspend fun setAutonomyGoal(
        baseUrl: String,
        bearer: String?,
        xMm: Double,
        yMm: Double,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/autonomy/goal",
                bearer,
                JSONObject().put("x_mm", xMm).put("y_mm", yMm).toString(),
            )
        }

    /** DELETE /v1/autonomy/goal — cancel an in-flight goto. */
    suspend fun clearAutonomyGoal(
        baseUrl: String,
        bearer: String?,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            delete("$baseUrl/v1/autonomy/goal", bearer)
        }

    private fun get(url: String, bearer: String? = null): JSONObject = get(url, bearer, client)

    private fun get(url: String, bearer: String?, http: OkHttpClient): JSONObject {
        val req = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .apply { if (!bearer.isNullOrBlank()) header("Authorization", "Bearer $bearer") }
            .get()
            .build()
        return execute(req, http)
    }

    private fun getDrive(url: String, bearer: String? = null): JSONObject = get(url, bearer, driveFastClient)

    private fun post(url: String, bearer: String?, jsonBody: String): JSONObject =
        post(url, bearer, jsonBody, client)

    private fun post(url: String, bearer: String?, jsonBody: String, http: OkHttpClient): JSONObject {
        val body = jsonBody.toRequestBody(jsonMedia)
        val req = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .apply { if (!bearer.isNullOrBlank()) header("Authorization", "Bearer $bearer") }
            .post(body)
            .build()
        return execute(req, http)
    }

    private fun postDrive(url: String, bearer: String?, jsonBody: String): JSONObject =
        post(url, bearer, jsonBody, driveFastClient)

    private fun postDriveCommand(url: String, bearer: String?, jsonBody: String): JSONObject =
        post(url, bearer, jsonBody, driveCommandClient)

    private fun delete(url: String, bearer: String?): JSONObject {
        val req = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .apply { if (!bearer.isNullOrBlank()) header("Authorization", "Bearer $bearer") }
            .delete()
            .build()
        return execute(req)
    }

    /** Log-safe request label: path + query (truncated), never Authorization or body (PINs/passwords). */
    private fun safeRequestLabel(req: Request): String {
        val q = req.url.encodedQuery
        val path =
            req.url.encodedPath +
                if (q.isNullOrBlank()) {
                    ""
                } else {
                    "?${q.take(80)}"
                }
        val auth = if (req.header("Authorization").isNullOrBlank()) "auth=no" else "auth=bearer"
        return "${req.method} $path $auth"
    }

    private fun execute(req: Request, http: OkHttpClient = client): JSONObject {
        val label = safeRequestLabel(req)
        NinaLog.debug("LinkClient", ">> $label")
        try {
            http.newCall(req).execute().use { resp ->
                val body = resp.body?.string().orEmpty()
                NinaLog.debug("LinkClient", "<< $label http=${resp.code} bytes=${body.length}")
                if (!resp.isSuccessful) {
                    val hint = httpErrorDetail(body, resp.code, resp.message)
                    val path = req.url.encodedPath
                    val quietSlamSnapshot =
                        resp.code == 404 &&
                            req.method == "GET" &&
                            path.endsWith("/v1/slam/snapshot")
                    if (!quietSlamSnapshot) {
                        NinaLog.warn(
                            "LinkClient",
                            "${req.method} ${req.url} -> ${resp.code} $hint",
                        )
                    }
                    throw LinkApiException(resp.code, hint)
                }
                return if (body.isBlank()) JSONObject() else JSONObject(body)
            }
        } catch (e: IOException) {
            NinaLog.warn("LinkClient", "<< $label IOException: ${e.message}")
            throw e
        }
    }

    /**
     * FastAPI often returns `detail` as a string, a list of validation objects, or nested JSON.
     * [JSONObject.optString] turns JSON null into the literal `"null"` — callers must use [jsonCleanString] instead.
     */
    private fun httpErrorDetail(body: String, httpCode: Int, httpMessage: String?): String {
        val fallback =
            httpMessage?.takeIf { it.isNotBlank() && !it.equals("null", ignoreCase = true) }
                ?: "HTTP $httpCode"
        if (body.isBlank()) return fallback
        return try {
            val j = JSONObject(body)
            when {
                j.has("detail") && !j.isNull("detail") -> {
                    when (val d = j.get("detail")) {
                        is String -> d.trim().ifBlank { fallback }
                        is JSONArray -> {
                            val parts = mutableListOf<String>()
                            for (i in 0 until d.length()) {
                                val item = d.optJSONObject(i)
                                val msg = item?.optString("msg")?.trim().orEmpty()
                                if (msg.isNotEmpty()) parts.add(msg)
                            }
                            parts.joinToString("; ").ifBlank { d.toString() }
                        }

                        else -> d.toString().trim().ifBlank { fallback }
                    }
                }

                j.has("message") && !j.isNull("message") ->
                    j.optString("message").trim().ifBlank { fallback }

                else -> j.toString().trim().ifBlank { fallback }
            }
        } catch (_: Exception) {
            body.trim().ifBlank { fallback }
        }
    }
}

/** JSON field safe for optional strings (never returns literal `"null"`). */
fun JSONObject.jsonCleanString(key: String): String? {
    if (!has(key) || isNull(key)) return null
    val s = optString(key).trim()
    if (s.isEmpty() || s.equals("null", ignoreCase = true)) return null
    return s
}

class LinkApiException(val code: Int, message: String) : Exception(message)

/** Raw SLAM occupancy grid bytes (``width * height`` uint8 cells). */
data class SlamOccupancyGrid(val bytes: ByteArray, val width: Int, val height: Int) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (javaClass != other?.javaClass) return false
        other as SlamOccupancyGrid
        if (width != other.width || height != other.height) return false
        if (!bytes.contentEquals(other.bytes)) return false
        return true
    }

    override fun hashCode(): Int {
        var result = width
        result = 31 * result + height
        result = 31 * result + bytes.contentHashCode()
        return result
    }
}
