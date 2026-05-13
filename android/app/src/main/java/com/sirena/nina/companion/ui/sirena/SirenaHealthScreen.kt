package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.R
import com.sirena.nina.companion.util.NinaLog
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import kotlin.math.max
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

/**
 * Health Check — mirrors [sirena_ui.screens.health_screen.HealthScreen]:
 * hero donut + summary, **Run all checks** / **Export report**, then **Subsystems** table rows.
 */
@Composable
fun SirenaHealthScreen(
    vm: CompanionViewModel,
    shellCompact: Boolean = false,
) {
    val scope = rememberCoroutineScope()
    var rows by remember { mutableStateOf<JSONArray?>(null) }
    var loadErr by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var lastRunLabel by remember { mutableStateOf("Last run \u2014 · 0 checks") }
    var exportOpen by remember { mutableStateOf(false) }
    var logsDialogKey by remember { mutableStateOf<String?>(null) }

    fun load() {
        scope.launch {
            loading = true
            loadErr = null
            try {
                val h = vm.fetchRobotHealth()
                if (h == null) {
                    loadErr = "Could not load health (daemon offline, wrong URL, or HTTP error)."
                    rows = null
                } else {
                    rows = h.optJSONArray("rows")
                    val n = rows?.length() ?: 0
                    val t = LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm"))
                    lastRunLabel = "Last run · $t · $n checks"
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                loadErr = e.message ?: "failed"
                rows = null
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) {
        load()
    }

    val summary = remember(rows) { summarizeHealthRows(rows) }

    Column(
        Modifier
            .fillMaxSize()
            .padding(horizontal = 10.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        SirenaBreadcrumbLine(listOf("Nina", "Health"))

        SirenaCard(kind = SirenaCardKind.Hero, contentPadding = PaddingValues(16.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                HealthDonutGauge(
                    ok = summary.ok,
                    warn = summary.warn,
                    err = summary.err,
                    total = summary.total,
                )
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(
                        summaryHeadline(summary, rows, loadErr),
                        fontWeight = FontWeight.Bold,
                        fontSize = 22.sp,
                        color = SirenaColors.text,
                    )
                    Text(
                        lastRunLabel,
                        fontSize = SirenaType.muted,
                        color = SirenaColors.muted,
                    )
                    loadErr?.let {
                        Text(it, fontSize = SirenaType.muted, color = SirenaColors.muted, modifier = Modifier.padding(top = 4.dp))
                    }
                }
                Column(
                    horizontalAlignment = Alignment.End,
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    SirenaPrimaryButton(
                        text = if (loading) "Running…" else "Run all checks",
                        onClick = { load() },
                        enabled = !loading,
                        modifier = Modifier.height(40.dp),
                    )
                    SirenaSecondaryButton(
                        text = "Export report",
                        onClick = { exportOpen = true },
                        modifier = Modifier.height(40.dp),
                    )
                }
            }
        }

        SirenaCard(modifier = Modifier.weight(1f), contentPadding = PaddingValues(12.dp)) {
            SirenaCardTitle("Subsystems")
            Spacer(Modifier.height(8.dp))
            val r = rows
            if (r == null || r.length() == 0) {
                SirenaMutedText(
                    if (loadErr != null) {
                        "Fix the connection above, then tap Run all checks."
                    } else {
                        "No rows yet — tap Run all checks."
                    },
                    maxLines = 3,
                )
            } else {
                LazyColumn(
                    Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    items(
                        count = r.length(),
                        key = { index ->
                            r.optJSONObject(index)?.optString("key")?.ifBlank { "$index" } ?: "$index"
                        },
                    ) { index ->
                        val o = r.optJSONObject(index) ?: return@items
                        HealthSubsystemRow(
                            o = o,
                            stripe = index % 2 == 0,
                            onViewLogs = { logsDialogKey = o.optString("key").ifBlank { o.optString("label") } },
                        )
                    }
                }
            }
        }
    }

    if (exportOpen) {
        AlertDialog(
            onDismissRequest = { exportOpen = false },
            title = { Text("Export report") },
            text = {
                Text(
                    "Health-report export will save a JSON snapshot to disk in a future build.",
                    color = SirenaColors.text,
                    fontSize = SirenaType.muted,
                )
            },
            confirmButton = {
                TextButton(onClick = { exportOpen = false }) {
                    Text("OK", color = SirenaColors.red, fontWeight = FontWeight.SemiBold)
                }
            },
        )
    }

    logsDialogKey?.let { key ->
        AlertDialog(
            onDismissRequest = { logsDialogKey = null },
            title = { Text("View logs") },
            text = {
                Text(
                    "Subsystem \"$key\": full logs live on the robot. Use SSH, the Jetson desktop, or the Sirena UI on the bot to open log files.",
                    color = SirenaColors.text,
                    fontSize = SirenaType.muted,
                )
            },
            confirmButton = {
                TextButton(onClick = { logsDialogKey = null }) {
                    Text("OK", color = SirenaColors.red, fontWeight = FontWeight.SemiBold)
                }
            },
        )
    }
}

private data class HealthSummary(val ok: Int, val warn: Int, val err: Int, val pending: Int, val total: Int)

private fun summarizeHealthRows(rows: JSONArray?): HealthSummary {
    if (rows == null) return HealthSummary(0, 0, 0, 0, 0)
    var ok = 0
    var warn = 0
    var err = 0
    var pending = 0
    for (i in 0 until rows.length()) {
        val o = rows.optJSONObject(i) ?: continue
        when (o.optString("status").trim().lowercase()) {
            "ok" -> ok++
            "warn" -> warn++
            "error" -> err++
            else -> pending++
        }
    }
    val total = rows.length()
    return HealthSummary(ok, warn, err, pending, total)
}

private fun summaryHeadline(s: HealthSummary, rows: JSONArray?, loadErr: String?): String =
    when {
        loadErr != null && (rows == null || rows.length() == 0) -> "Health unavailable"
        rows == null || rows.length() == 0 -> "Run a check to see status"
        s.err > 0 -> "Action required"
        s.warn > 0 -> "System degraded"
        s.pending > 0 -> "Partial integration"
        else -> "System healthy"
    }

private fun healthGlyphForKey(key: String): String =
    when (key) {
        "bus" -> "\u26A1"
        "ftdi" -> "\u2706"
        "camera" -> "\u25CE"
        "lidar" -> "\u25A6"
        "ir", "ultra", "depth" -> "\u25A6"
        "drive" -> "\u2B95"
        "battery" -> "\u2615"
        "wifi" -> "\u2706"
        "voice" -> "\u266B"
        "disk" -> "\u25A6"
        "cpu" -> "\u2699"
        "temp" -> "\u2615"
        "daemon" -> "\u26A0"
        "sirena" -> "\u2665"
        else -> "\u25CF"
    }

private fun statusPillLabel(st: String): String =
    when (st.trim().lowercase()) {
        "ok" -> "OK"
        "warn" -> "Warning"
        "error" -> "Error"
        else -> "Pending"
    }

@Composable
private fun HealthDonutGauge(
    ok: Int,
    warn: Int,
    err: Int,
    total: Int,
    modifier: Modifier = Modifier.size(130.dp),
) {
    val totalClamped = max(total, ok + warn + err).coerceAtLeast(1)
    val okC = SirenaColors.success
    val warnC = SirenaColors.warning
    val errC = SirenaColors.danger
    val track = SirenaColors.pillNeutralBg

    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val strokeW = 10.dp.toPx()
            val arcSize = Size(this.size.width - strokeW, this.size.height - strokeW)
            val topLeft = Offset(strokeW / 2f, strokeW / 2f)
            val stroke = Stroke(width = strokeW, cap = StrokeCap.Butt)
            drawArc(
                color = track,
                startAngle = -90f,
                sweepAngle = 360f,
                useCenter = false,
                topLeft = topLeft,
                size = arcSize,
                style = stroke,
            )
            var start = -90f
            for ((count, color) in listOf(ok to okC, warn to warnC, err to errC)) {
                if (count <= 0) continue
                val sweep = 360f * count / totalClamped.toFloat()
                drawArc(
                    color = color,
                    startAngle = start,
                    sweepAngle = sweep,
                    useCenter = false,
                    topLeft = topLeft,
                    size = arcSize,
                    style = stroke,
                )
                start += sweep
            }
        }
        Image(
            painter = painterResource(R.drawable.nina_robot),
            contentDescription = null,
            modifier =
                Modifier
                    .size(56.dp)
                    .clip(CircleShape)
                    .background(SirenaColors.panel),
            contentScale = ContentScale.Fit,
        )
        Text(
            "${ok}/${totalClamped}",
            fontWeight = FontWeight.Bold,
            fontSize = 12.sp,
            color = SirenaColors.text,
            modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 4.dp),
        )
    }
}

@Composable
private fun HealthSubsystemRow(
    o: JSONObject,
    stripe: Boolean,
    onViewLogs: () -> Unit,
) {
    val key = o.optString("key")
    val label = o.optString("label").ifBlank { key }
    val detail = o.optString("detail")
    val st = o.optString("status").trim().lowercase()
    val kind =
        when (st) {
            "ok" -> SirenaPillKind.Ok
            "warn" -> SirenaPillKind.Warn
            "error" -> SirenaPillKind.Error
            else -> SirenaPillKind.Neutral
        }
    val bg = if (stripe) SirenaColors.panel else Color(0xFFFAFAFC)
    Row(
        Modifier
            .fillMaxWidth()
            .background(bg, shape = RoundedCornerShape(6.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            healthGlyphForKey(key),
            fontSize = 18.sp,
            color = SirenaColors.muted,
            modifier = Modifier.width(28.dp),
        )
        Text(
            label,
            fontWeight = FontWeight.SemiBold,
            color = SirenaColors.text,
            fontSize = SirenaType.base,
            modifier = Modifier.width(180.dp),
        )
        Text(
            detail,
            color = SirenaColors.muted,
            fontSize = SirenaType.muted,
            modifier = Modifier.weight(1f),
            maxLines = 4,
        )
        SirenaStatusPill(text = statusPillLabel(st), kind = kind, maxLines = 1)
        TextButton(
            onClick = {
                NinaLog.tap("Health", "view_logs", key)
                onViewLogs()
            },
            modifier = Modifier.padding(start = 4.dp),
        ) {
            Text(
                "View logs",
                color = SirenaColors.red,
                fontSize = 12.sp,
                fontWeight = FontWeight.Normal,
            )
        }
    }
}
