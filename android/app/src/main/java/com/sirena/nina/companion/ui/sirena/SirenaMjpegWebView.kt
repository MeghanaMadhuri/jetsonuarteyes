package com.sirena.nina.companion.ui.sirena

import android.annotation.SuppressLint
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.view.View
import android.webkit.WebSettings
import android.webkit.WebView
import androidx.compose.runtime.Composable
import androidx.compose.runtime.key
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView

/** Document origin for [loadDataWithBaseURL] so `<img src="http://…">` loads on Android WebView. */
private fun httpOriginForStream(streamUrl: String): String {
    val u = Uri.parse(streamUrl.trim())
    val scheme = u.scheme?.lowercase()?.takeIf { it == "http" || it == "https" } ?: "http"
    val host = u.host?.takeIf { it.isNotBlank() } ?: return "http://127.0.0.1/"
    val port = u.port
    val def = if (scheme == "https") 443 else 80
    return if (port == -1 || port == def) {
        "$scheme://$host/"
    } else {
        "$scheme://$host:$port/"
    }
}

/** Minimal HTML attribute escaping for the MJPEG URL inside `<img src="…">`. */
private fun escapeAttrUrl(url: String): String =
    buildString(url.length + 8) {
        for (c in url) {
            when (c) {
                '&' -> append("&amp;")
                '"' -> append("&quot;")
                '\'' -> append("&#39;")
                '<' -> append("&lt;")
                else -> append(c)
            }
        }
    }

/**
 * Embeds an MJPEG multipart stream via a minimal HTML [img] tag.
 *
 * - **Letterboxed** (`object-fit: contain`, centered flex) so 16:9 (or arbitrary) frames fit
 *   Perception / Drive tiles without stretching (matches Compose [ContentScale.Fit] for bitmaps).
 * - **Reload only when [streamUrl] changes** — avoids restarting the decoder on every recomposition
 *   (a common cause of multi‑second “stalls” and choppy video).
 * - **Hardware layer + no HTTP cache** — smoother paint for live video.
 * - **HTTP(S) base URL** — `loadDataWithBaseURL(null, …)` makes the document a null/opaque origin on
 *   many WebViews, which blocks or mishandles `<img src="http://jetson:8787/…">` MJPEG. We set the
 *   base to the daemon origin (`http://host:port/`) so the stream loads like a normal page resource.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun SirenaMjpegWebView(
    streamUrl: String,
    modifier: Modifier = Modifier,
) {
    val safeUrl = remember(streamUrl) { escapeAttrUrl(streamUrl) }
    val baseUrl = remember(streamUrl) { httpOriginForStream(streamUrl) }
    val html =
        remember(safeUrl) {
            "<html><head><meta name=\"viewport\" content=\"width=device-width, initial-scale=1, maximum-scale=1\"/></head>" +
                "<body style=\"margin:0;padding:0;background:#000;width:100%;height:100vh;" +
                "display:flex;align-items:center;justify-content:center;overflow:hidden;\">" +
                "<img src=\"$safeUrl\" alt=\"\" " +
                "style=\"max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain;\"/>" +
                "</body></html>"
        }
    key(streamUrl) {
        AndroidView(
            factory = { context ->
                WebView(context).apply {
                    setBackgroundColor(Color.BLACK)
                    setLayerType(View.LAYER_TYPE_HARDWARE, null)
                    settings.javaScriptEnabled = false
                    settings.domStorageEnabled = false
                    settings.loadWithOverviewMode = true
                    settings.useWideViewPort = true
                    settings.cacheMode = WebSettings.LOAD_NO_CACHE
                    settings.blockNetworkLoads = false
                    settings.loadsImagesAutomatically = true
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                        settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                    }
                    tag = streamUrl
                    loadDataWithBaseURL(baseUrl, html, "text/html", "UTF-8", baseUrl)
                }
            },
            update = { wv ->
                if (wv.tag != streamUrl) {
                    wv.tag = streamUrl
                    wv.loadDataWithBaseURL(baseUrl, html, "text/html", "UTF-8", baseUrl)
                }
            },
            modifier = modifier,
        )
    }
}
