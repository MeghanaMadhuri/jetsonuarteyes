package com.sirena.nina.companion.ui.sirena

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
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
import androidx.compose.foundation.lazy.itemsIndexed
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
    var rows by remember { mutableStateOf<List<HealthRowUi>>(emptyList()) }
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
                    rows = emptyList()
                } else {
                    rows = parseHealthRows(h.optJSONArray("rows"))
                    val n = rows.size
                    val t = LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm"))
                    lastRunLabel = "Last run · $t · $n checks"
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                loadErr = e.message ?: "failed"
                rows = emptyList()
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
            if (shellCompact) {
                Column(
                    Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
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
                        HealthHeroSummaryColumn(
                            summary = summary,
                            rows = rows,
                            loadErr = loadErr,
                            lastRunLabel = lastRunLabel,
                        )
                    }
                    HealthHeroActionsRow(loading = loading, onRun = { load() }, onExport = { exportOpen = true })
                }
            } else {
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
                    HealthHeroSummaryColumn(
                        summary = summary,
                        rows = rows,
                        loadErr = loadErr,
                        lastRunLabel = lastRunLabel,
                        modifier = Modifier.weight(1f),
                    )
                    HealthHeroActionsColumn(loading = loading, onRun = { load() }, onExport = { exportOpen = true })
                }
            }
        }

        SirenaCard(
            modifier = Modifier.weight(1f).fillMaxWidth(),
            contentPadding = PaddingValues(12.dp),
        ) {
            Column(Modifier.fillMaxSize().fillMaxWidth()) {
                SirenaCardTitle("Subsystems")
                Spacer(Modifier.height(8.dp))
                val r = rows
                when {
                    loading -> {
                        LazyColumn(
                            Modifier.fillMaxSize().fillMaxWidth(),
                            verticalArrangement = Arrangement.spacedBy(2.dp),
                        ) {
                            items(8, key = { "skel_$it" }) { index ->
                                HealthSubsystemSkeletonRow(stripe = index % 2 == 0)
                            }
                        }
                    }
                    r.isEmpty() -> {
                        Box(
                            Modifier.fillMaxSize().fillMaxWidth(),
                            contentAlignment = Alignment.Center,
                        ) {
                            SirenaMutedText(
                                if (loadErr != null) {
                                    "Fix the connection above, then tap Run all checks."
                                } else {
                                    "No rows yet — tap Run all checks."
                                },
                                maxLines = 3,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                    else -> {
                        LazyColumn(
                            Modifier.fillMaxSize().fillMaxWidth(),
                            verticalArrangement = Arrangement.spacedBy(2.dp),
                        ) {
                            itemsIndexed(
                                items = r,
                                key = { _, row -> row.id },
                            ) { index, row ->
                                SirenaAnimatedEnter(
                                    visible = true,
                                    delayIndex = index.coerceAtMost(10),
                                ) {
                                    HealthSubsystemRow(
                                        row = row,
                                        stripe = index % 2 == 0,
                                        onViewLogs = { logsDialogKey = row.key.ifBlank { row.label } },
                                    )
                                }
                            }
                        }
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

private data class HealthRowUi(
    /** Stable LazyColumn key (index suffix — gateway may repeat subsystem keys). */
    val id: String,
    val key: String,
    val label: String,
    val detail: String,
    val status: String,
)

/** Parse JSON rows; assign unique [HealthRowUi.id] per index. */
private fun parseHealthRows(arr: JSONArray?): List<HealthRowUi> {
    if (arr == null || arr.length() == 0) return emptyList()
    val out = ArrayList<HealthRowUi>(arr.length())
    for (i in 0 until arr.length()) {
        val o = arr.optJSONObject(i) ?: continue
        val key = o.optString("key").ifBlank { "row" }
        out.add(
            HealthRowUi(
                id = "${key}_$i",
                key = key,
                label = o.optString("label").ifBlank { key },
                detail = o.optString("detail"),
                status = o.optString("status").trim().lowercase(),
            ),
        )
    }
    return out
}

@Composable
private fun HealthHeroSummaryColumn(
    summary: HealthSummary,
    rows: List<HealthRowUi>,
    loadErr: String?,
    lastRunLabel: String,
    modifier: Modifier = Modifier,
) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            summaryHeadline(summary, rows, loadErr),
            fontWeight = FontWeight.Bold,
            fontSize = 22.sp,
            color = SirenaColors.text,
        )
        Text(lastRunLabel, fontSize = SirenaType.muted, color = SirenaColors.muted)
        loadErr?.let {
            Text(it, fontSize = SirenaType.muted, color = SirenaColors.muted, modifier = Modifier.padding(top = 4.dp))
        }
    }
}

@Composable
private fun HealthHeroActionsColumn(
    loading: Boolean,
    onRun: () -> Unit,
    onExport: () -> Unit,
) {
    Column(
        horizontalAlignment = Alignment.End,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        SirenaPrimaryButton(
            text = if (loading) "Running…" else "Run all checks",
            onClick = onRun,
            enabled = !loading,
            modifier = Modifier.height(40.dp),
        )
        SirenaSecondaryButton(
            text = "Export report",
            onClick = onExport,
            modifier = Modifier.height(40.dp),
        )
    }
}

@Composable
private fun HealthHeroActionsRow(
    loading: Boolean,
    onRun: () -> Unit,
    onExport: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        SirenaPrimaryButton(
            text = if (loading) "Running…" else "Run all checks",
            onClick = onRun,
            enabled = !loading,
            modifier = Modifier.weight(1f).height(40.dp),
        )
        SirenaSecondaryButton(
            text = "Export report",
            onClick = onExport,
            modifier = Modifier.weight(1f).height(40.dp),
        )
    }
}

@Composable
private fun HealthSubsystemSkeletonRow(stripe: Boolean) {
    val transition = rememberInfiniteTransition(label = "healthSkel")
    val pulse by transition.animateFloat(
        initialValue = 0.35f,
        targetValue = 0.72f,
        animationSpec =
            infiniteRepeatable(
                animation = tween(900, easing = LinearEasing),
                repeatMode = RepeatMode.Reverse,
            ),
        label = "healthSkelPulse",
    )
    val skel = SirenaColors.muted.copy(alpha = pulse)
    val bg = if (stripe) SirenaColors.panel else Color(0xFFFAFAFC)
    Row(
        Modifier
            .fillMaxWidth()
            .background(bg, shape = RoundedCornerShape(6.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(Modifier.size(28.dp).clip(RoundedCornerShape(4.dp)).background(skel))
        Box(Modifier.width(180.dp).height(14.dp).clip(RoundedCornerShape(4.dp)).background(skel))
        Box(Modifier.weight(1f).height(14.dp).clip(RoundedCornerShape(4.dp)).background(skel))
        Box(Modifier.width(56.dp).height(22.dp).clip(RoundedCornerShape(11.dp)).background(skel))
        Box(Modifier.width(72.dp).height(14.dp).clip(RoundedCornerShape(4.dp)).background(skel))
    }
}

private data class HealthSummary(val ok: Int, val warn: Int, val err: Int, val pending: Int, val total: Int)

private fun summarizeHealthRows(rows: List<HealthRowUi>): HealthSummary {
    if (rows.isEmpty()) return HealthSummary(0, 0, 0, 0, 0)
    var ok = 0
    var warn = 0
    var err = 0
    var pending = 0
    for (row in rows) {
        when (row.status) {
            "ok" -> ok++
            "warn" -> warn++
            "error" -> err++
            else -> pending++
        }
    }
    return HealthSummary(ok, warn, err, pending, rows.size)
}

private fun summaryHeadline(s: HealthSummary, rows: List<HealthRowUi>, loadErr: String?): String =
    when {
        loadErr != null && rows.isEmpty() -> "Health unavailable"
        rows.isEmpty() -> "Run a check to see status"
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
    row: HealthRowUi,
    stripe: Boolean,
    onViewLogs: () -> Unit,
) {
    val key = row.key
    val label = row.label
    val detail = row.detail
    val st = row.status
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
