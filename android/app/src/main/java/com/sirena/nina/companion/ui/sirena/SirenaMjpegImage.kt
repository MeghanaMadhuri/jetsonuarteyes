package com.sirena.nina.companion.ui.sirena

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import android.os.SystemClock
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.util.concurrent.TimeUnit

/**
 * Client-side decode clamp (long edge in px). Pair with Jetson `NINA_MJPEG_MAX_WIDTH` and
 * `NINA_MJPEG_JPEG_QUALITY` (see repo `docs/COMPANION_APP.md`).
 */
const val SirenaMjpegAndroidMaxLongEdgeDefault = 1152

/** Smaller decode for in-app preview tiles (Vision / Perception / Drive camera card). */
const val SirenaMjpegPreviewMaxLongEdge = 768

/** Cap UI updates so Compose is not flooded (smoother than decoding every JPEG). */
const val SirenaMjpegDefaultTargetFps = 24

/** Cap multipart buffer so backlog does not delay first frame / cause stutter. */
private const val MJPEG_MAX_BUFFER_BYTES = 393_216
private const val MJPEG_TRIM_KEEP_BYTES = 393_216

/** Long-lived MJPEG reads: no body-level retry interceptor (multipart is not idempotent-safe). */
private val mjpegHttpClient: OkHttpClient =
    OkHttpClient.Builder()
        .protocols(listOf(Protocol.HTTP_1_1))
        .connectTimeout(25, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)
        .callTimeout(0, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

private fun ByteArray.indexOfJpegStart(from: Int): Int {
    for (i in from until size - 1) {
        if ((this[i].toInt() and 0xFF) == 0xFF && (this[i + 1].toInt() and 0xFF) == 0xD8) {
            return i
        }
    }
    return -1
}

private fun ByteArray.indexOfJpegEnd(from: Int): Int {
    for (i in from until size - 1) {
        if ((this[i].toInt() and 0xFF) == 0xFF && (this[i + 1].toInt() and 0xFF) == 0xD9) {
            return i
        }
    }
    return -1
}

/**
 * Extract complete JPEGs from a buffer; returns decoded tail (unconsumed bytes after last full JPEG).
 * If several JPEGs are present, decodes all and caller may keep only the last (smooth under backlog).
 */
private fun drainJpegs(
    buffer: ByteArray,
    maxLongEdge: Int,
): Pair<List<Bitmap>, ByteArray> {
    val out = mutableListOf<Bitmap>()
    var pos = 0
    while (pos < buffer.size) {
        val s = buffer.indexOfJpegStart(pos)
        if (s < 0) break
        val e = buffer.indexOfJpegEnd(s + 2)
        if (e < 0) break
        val jpeg = buffer.copyOfRange(s, e + 2)
        val bmp = decodeJpegScaled(jpeg, maxLongEdge)
        if (bmp != null) {
            out.add(bmp)
        }
        pos = e + 2
    }
    val tail = if (pos < buffer.size) buffer.copyOfRange(pos, buffer.size) else ByteArray(0)
    return Pair(out, tail)
}

private fun decodeJpegScaled(jpeg: ByteArray, maxLongEdge: Int): Bitmap? {
    val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, opts)
    if (opts.outWidth <= 0 || opts.outHeight <= 0) return null
    var sample = 1
    while (maxOf(opts.outWidth / sample, opts.outHeight / sample) > maxLongEdge) {
        sample *= 2
    }
    val load = BitmapFactory.Options().apply { inSampleSize = sample }
    return BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, load)
}

/**
 * Decodes MJPEG multipart (`multipart/x-mixed-replace`) and shows the latest frame.
 * Prefer over [SirenaMjpegWebView] for reliable video on Android WebView builds.
 */
@Composable
fun SirenaMjpegImage(
    streamUrl: String,
    bearer: String?,
    modifier: Modifier = Modifier,
    /** Downscale decoded frames so GPU upload stays smooth on tablets / Wi‑Fi. */
    maxLongEdge: Int = SirenaMjpegAndroidMaxLongEdgeDefault,
    /** When false, no HTTP stream is opened (saves battery when hardware is absent). */
    streamEnabled: Boolean = true,
    /** Stop spinner and show [idleMessage] if no frame arrives in time. */
    loadingTimeoutMs: Long = 12_000L,
    idleMessage: String? = null,
    /** Max frames pushed to the UI thread per second (drops extras for smooth playback). */
    targetFps: Int = SirenaMjpegDefaultTargetFps,
    /** Bump to restart the HTTP reader (e.g. after camera resolution change). */
    streamGeneration: Int = 0,
) {
    var image by remember { mutableStateOf<ImageBitmap?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    val minFrameIntervalMs = (1000f / targetFps.coerceIn(8, 60)).toLong()

    LaunchedEffect(streamUrl, bearer, maxLongEdge, streamEnabled, streamGeneration) {
        loading = streamEnabled && streamUrl.isNotBlank()
        error = null
        image = null
        if (!streamEnabled || streamUrl.isBlank()) {
            loading = false
            return@LaunchedEffect
        }
        var frameReceived = false
        var lastUiFrameMs = 0L
        val timeoutJob =
            launch {
                delay(loadingTimeoutMs)
                if (!frameReceived && isActive) {
                    error = idleMessage ?: "No video signal — open camera on Jetson or check USB"
                    loading = false
                }
            }
        try {
            withContext(Dispatchers.IO) {
                val reqBuilder = Request.Builder().url(streamUrl).get()
                bearer?.trim()?.takeIf { it.isNotEmpty() }?.let {
                    reqBuilder.header("Authorization", "Bearer $it")
                }
                val call = mjpegHttpClient.newCall(reqBuilder.build())
                try {
                    val resp = call.execute()
                    if (!resp.isSuccessful) {
                        withContext(Dispatchers.Main) {
                            error = "HTTP ${resp.code}"
                            loading = false
                        }
                        resp.close()
                        return@withContext
                    }
                    val body = resp.body ?: return@withContext
                    body.byteStream().use { raw ->
                        val input = BufferedInputStream(raw, 32768)
                        val accum = ByteArrayOutputStream(65536)
                        val chunk = ByteArray(16384)
                        while (isActive) {
                            val n = input.read(chunk)
                            if (n <= 0) break
                            accum.write(chunk, 0, n)
                            var data = accum.toByteArray()
                            if (data.size > MJPEG_MAX_BUFFER_BYTES) {
                                val cut =
                                    data.copyOfRange(
                                        (data.size - MJPEG_TRIM_KEEP_BYTES).coerceAtLeast(0),
                                        data.size,
                                    )
                                accum.reset()
                                accum.write(cut)
                                data = cut
                            }
                            val (frames, tail) = drainJpegs(data, maxLongEdge)
                            accum.reset()
                            accum.write(tail)
                            if (frames.isNotEmpty()) {
                                val latest = frames.last()
                                for (i in 0 until frames.size - 1) {
                                    frames[i].recycle()
                                }
                                frameReceived = true
                                val now = SystemClock.uptimeMillis()
                                if (now - lastUiFrameMs >= minFrameIntervalMs) {
                                    lastUiFrameMs = now
                                    withContext(Dispatchers.Main) {
                                        image = latest.asImageBitmap()
                                        loading = false
                                        error = null
                                    }
                                } else {
                                    latest.recycle()
                                }
                            }
                        }
                    }
                } finally {
                    call.cancel()
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            withContext(Dispatchers.Main) {
                error = e.message ?: "stream error"
                loading = false
            }
        } finally {
            timeoutJob.cancel()
        }
    }

    Box(
        modifier
            .background(SirenaColors.cloud)
            .fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        when {
            image != null ->
                Image(
                    bitmap = image!!,
                    contentDescription = null,
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Fit,
                )
            error != null ->
                Text(
                    error!!,
                    color = SirenaColors.muted,
                    fontSize = SirenaType.muted,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(8.dp),
                )
            loading -> CircularProgressIndicator(color = SirenaColors.red)
        }
    }
}
