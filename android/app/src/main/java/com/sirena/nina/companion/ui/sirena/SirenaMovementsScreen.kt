package com.sirena.nina.companion.ui.sirena

import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.CompanionViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import kotlin.math.max

private data class MovementRow(
    val id: String,
    val name: String,
    val summary: String,
)

private data class StepDraft(
    val kind: String,
    val seconds: Double = 0.0,
    val degrees: Double = 0.0,
    val uturnDirection: String = "right",
) {
    fun summary(): String =
        when (kind) {
            "forward" -> "Forward ${seconds}s"
            "backward" -> "Backward ${seconds}s"
            "turn_left" -> "Turn left ${degrees.toInt()}°"
            "turn_right" -> "Turn right ${degrees.toInt()}°"
            "uturn" -> "U-turn 180° ($uturnDirection)"
            "brake" -> "Brake (hold pose)"
            else -> kind
        }

    fun toJson(): JSONObject =
        JSONObject()
            .put("kind", kind)
            .put("seconds", seconds)
            .put("degrees", degrees)
            .put("uturn_direction", uturnDirection)
}

private enum class MovementsPage { List, Editor }

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SirenaMovementsScreen(
    vm: CompanionViewModel,
    shellCompact: Boolean = false,
) {
    @Suppress("UNUSED_PARAMETER")
    val _compact = shellCompact

    val scope = rememberCoroutineScope()
    var page by remember { mutableStateOf(MovementsPage.List) }
    var rows by remember { mutableStateOf<List<MovementRow>>(emptyList()) }
    var selectedIndex by remember { mutableIntStateOf(-1) }
    var status by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var running by remember { mutableStateOf(false) }

    var editId by remember { mutableStateOf<String?>(null) }
    var editName by remember { mutableStateOf("") }
    var editSteps by remember { mutableStateOf<List<StepDraft>>(emptyList()) }
    var selectedStep by remember { mutableIntStateOf(-1) }

    var dialogKind by remember { mutableStateOf<String?>(null) }
    var dialogValue by remember { mutableStateOf("1.0") }
    var dialogUturnRight by remember { mutableStateOf(true) }

    fun parseList(payload: JSONObject?): List<MovementRow> {
        if (payload == null) return emptyList()
        val arr = payload.optJSONArray("movements") ?: JSONArray()
        val out = mutableListOf<MovementRow>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val id = o.optString("id").ifBlank { o.optString("movement_id") }
            if (id.isBlank()) continue
            val name = o.optString("name", id)
            val steps = o.optJSONArray("steps")
            val n = steps?.length() ?: 0
            out.add(MovementRow(id, name, "$n step${if (n == 1) "" else "s"}"))
        }
        return out.sortedBy { it.name.lowercase() }
    }

    fun parseSteps(arr: JSONArray?): List<StepDraft> {
        if (arr == null) return emptyList()
        val out = mutableListOf<StepDraft>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            out.add(
                StepDraft(
                    kind = o.optString("kind"),
                    seconds = o.optDouble("seconds", 0.0),
                    degrees = o.optDouble("degrees", 0.0),
                    uturnDirection = o.optString("uturn_direction", "right"),
                ),
            )
        }
        return out
    }

    fun refresh() {
        scope.launch {
            busy = true
            status = "Loading…"
            try {
                rows = parseList(vm.fetchMovements())
                status =
                    if (rows.isEmpty()) {
                        "No saved movements — tap Create new."
                    } else {
                        "Select a movement, then Run or Edit."
                    }
            } catch (e: Exception) {
                status = e.message ?: "Failed to load movements"
            } finally {
                busy = false
            }
        }
    }

    fun openEditor(id: String?, name: String, steps: List<StepDraft>) {
        editId = id
        editName = name
        editSteps = steps
        selectedStep = -1
        page = MovementsPage.Editor
    }

    fun startCreate() {
        openEditor(null, "New movement", emptyList())
    }

    fun startEdit(row: MovementRow) {
        scope.launch {
            busy = true
            try {
                val payload = vm.fetchMovement(row.id)
                val mv = payload?.optJSONObject("movement")
                if (mv != null) {
                    openEditor(
                        row.id,
                        mv.optString("name", row.name),
                        parseSteps(mv.optJSONArray("steps")),
                    )
                } else {
                    status = "Movement not found"
                }
            } catch (e: Exception) {
                status = e.message ?: "Load failed"
            } finally {
                busy = false
            }
        }
    }

    LaunchedEffect(Unit) {
        refresh()
    }

    LaunchedEffect(running) {
        if (!running) return@LaunchedEffect
        while (isActive) {
            delay(400L)
            val st = vm.fetchMovementRunStatus()
            when (st?.optString("status").orEmpty()) {
                "ok" -> {
                    running = false
                    busy = false
                    status = "Sequence finished."
                    refresh()
                    return@LaunchedEffect
                }
                "failed" -> {
                    running = false
                    busy = false
                    status = st?.optString("error")?.takeIf { it.isNotBlank() }
                        ?: "Movement failed"
                    return@LaunchedEffect
                }
            }
        }
    }

    if (dialogKind != null) {
        AlertDialog(
            onDismissRequest = { dialogKind = null },
            title = {
                Text(
                    when (dialogKind) {
                        "forward", "backward" -> "Duration (seconds)"
                        "turn_left", "turn_right" -> "Turn angle (degrees)"
                        "uturn" -> "U-turn direction"
                        else -> "Step"
                    },
                )
            },
            text = {
                when (dialogKind) {
                    "uturn" ->
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            SirenaSecondaryButton(
                                text = "Right",
                                onClick = { dialogUturnRight = true },
                            )
                            SirenaSecondaryButton(
                                text = "Left",
                                onClick = { dialogUturnRight = false },
                            )
                        }
                    else ->
                        OutlinedTextField(
                            value = dialogValue,
                            onValueChange = { dialogValue = it },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val kind = dialogKind ?: return@TextButton
                        when (kind) {
                            "forward", "backward" -> {
                                val sec = dialogValue.toDoubleOrNull() ?: 1.0
                                editSteps =
                                    editSteps +
                                        StepDraft(
                                            kind,
                                            seconds = max(0.1, sec),
                                        )
                            }
                            "turn_left", "turn_right" -> {
                                val deg = dialogValue.toDoubleOrNull() ?: 90.0
                                editSteps =
                                    editSteps +
                                        StepDraft(
                                            kind,
                                            degrees = max(1.0, deg),
                                        )
                            }
                            "uturn" ->
                                editSteps =
                                    editSteps +
                                        StepDraft(
                                            "uturn",
                                            degrees = 180.0,
                                            uturnDirection =
                                                if (dialogUturnRight) "right" else "left",
                                        )
                        }
                        dialogKind = null
                    },
                ) {
                    Text("Add")
                }
            },
            dismissButton = {
                TextButton(onClick = { dialogKind = null }) {
                    Text("Cancel")
                }
            },
        )
    }

    Column(
        Modifier
            .fillMaxSize()
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        val crumbs =
            if (page == MovementsPage.List) {
                listOf("Nina", "Movements")
            } else {
                listOf("Nina", "Movements", if (editId == null) "Create" else "Edit")
            }
        SirenaBreadcrumbLine(crumbs)

        AnimatedContent(targetState = page, label = "movementsPage") { p ->
            when (p) {
                MovementsPage.List ->
                    Column(
                        Modifier.fillMaxSize(),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Text(
                            "Saved drive sequences",
                            color = SirenaColors.text,
                            fontWeight = FontWeight.Bold,
                        )
                        if (status.isNotBlank()) {
                            Text(status, color = SirenaColors.muted)
                        }
                        FlowRow(
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            SirenaPrimaryButton(
                                text = if (running) "Running…" else "Run",
                                enabled = !busy && !running && selectedIndex in rows.indices,
                                onClick = {
                                    val row = rows[selectedIndex]
                                    scope.launch {
                                        busy = true
                                        running = true
                                        status = "Starting ${row.name}…"
                                        try {
                                            val j = vm.runMovement(row.id)
                                            if (!j.optBoolean("ok", false) &&
                                                !j.optBoolean("accepted", false)
                                            ) {
                                                running = false
                                                busy = false
                                                status =
                                                    j.optString("error", "Run rejected")
                                            }
                                        } catch (e: CancellationException) {
                                            throw e
                                        } catch (e: Exception) {
                                            running = false
                                            busy = false
                                            status = e.message ?: "Run failed"
                                        }
                                    }
                                },
                            )
                            SirenaSecondaryButton(
                                text = "Edit",
                                enabled = !busy && !running && selectedIndex in rows.indices,
                                onClick = { startEdit(rows[selectedIndex]) },
                            )
                            SirenaSecondaryButton(
                                text = "Delete",
                                enabled = !busy && !running && selectedIndex in rows.indices,
                                onClick = {
                                    val row = rows[selectedIndex]
                                    scope.launch {
                                        busy = true
                                        try {
                                            val j = vm.deleteMovement(row.id)
                                            if (j.optBoolean("ok", false)) {
                                                selectedIndex = -1
                                                refresh()
                                            } else {
                                                status = j.optString("error", "Delete failed")
                                            }
                                        } catch (e: Exception) {
                                            status = e.message ?: "Delete failed"
                                        } finally {
                                            busy = false
                                        }
                                    }
                                },
                            )
                            SirenaSecondaryButton(
                                text = "Create new",
                                enabled = !busy && !running,
                                onClick = { startCreate() },
                            )
                            SirenaSecondaryButton(
                                text = "Refresh",
                                enabled = !busy && !running,
                                onClick = { refresh() },
                            )
                        }
                        SirenaCard(
                            modifier = Modifier
                                .fillMaxWidth()
                                .weight(1f),
                        ) {
                            if (rows.isEmpty()) {
                                Text("No movements yet.", color = SirenaColors.muted)
                            } else {
                                LazyColumn(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                    itemsIndexed(rows) { idx, row ->
                                        val selected = idx == selectedIndex
                                        Row(
                                            Modifier
                                                .fillMaxWidth()
                                                .clickable(enabled = !busy) {
                                                    selectedIndex = idx
                                                }
                                                .padding(vertical = 6.dp),
                                            horizontalArrangement = Arrangement.SpaceBetween,
                                        ) {
                                            Column {
                                                Text(
                                                    row.name,
                                                    color = SirenaColors.text,
                                                    fontWeight =
                                                        if (selected) {
                                                            FontWeight.Bold
                                                        } else {
                                                            FontWeight.Normal
                                                        },
                                                )
                                                Text(row.summary, color = SirenaColors.muted)
                                            }
                                            if (selected) {
                                                Text("\u2713", color = SirenaColors.red)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                MovementsPage.Editor ->
                    Column(
                        Modifier
                            .fillMaxSize()
                            .verticalScroll(rememberScrollState()),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Text(
                            if (editId == null) "Create movement" else "Edit movement",
                            color = SirenaColors.text,
                            fontWeight = FontWeight.Bold,
                        )
                        Text(
                            "Forward/back use seconds; turns use IMU degrees; Brake stops and holds pose (same as kiosk).",
                            color = SirenaColors.muted,
                        )
                        OutlinedTextField(
                            value = editName,
                            onValueChange = { editName = it },
                            label = { Text("Name") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Text("Steps (in order)", fontWeight = FontWeight.SemiBold)
                        SirenaCard(modifier = Modifier.fillMaxWidth()) {
                            if (editSteps.isEmpty()) {
                                Text("No steps yet — add one below.", color = SirenaColors.muted)
                            } else {
                                editSteps.forEachIndexed { idx, step ->
                                    val sel = idx == selectedStep
                                    Text(
                                        "${idx + 1}. ${step.summary()}",
                                        color = SirenaColors.text,
                                        fontWeight = if (sel) FontWeight.Bold else FontWeight.Normal,
                                        modifier =
                                            Modifier
                                                .fillMaxWidth()
                                                .clickable { selectedStep = idx }
                                                .padding(vertical = 4.dp),
                                    )
                                }
                            }
                        }
                        FlowRow(
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            listOf(
                                "+ Fwd" to "forward",
                                "+ Back" to "backward",
                                "+ Left" to "turn_left",
                                "+ Right" to "turn_right",
                                "+ U-turn" to "uturn",
                                "+ Brake" to "brake",
                            ).forEach { (label, kind) ->
                                SirenaSecondaryButton(
                                    text = label,
                                    enabled = !busy,
                                    onClick = {
                                        if (kind == "brake") {
                                            editSteps = editSteps + StepDraft("brake")
                                            return@forEach
                                        }
                                        dialogKind = kind
                                        dialogValue =
                                            when (kind) {
                                                "forward", "backward" -> "1.0"
                                                "turn_left", "turn_right" -> "90"
                                                else -> ""
                                            }
                                        dialogUturnRight = true
                                    },
                                )
                            }
                            SirenaSecondaryButton(
                                text = "Remove step",
                                enabled = !busy && selectedStep in editSteps.indices,
                                onClick = {
                                    editSteps = editSteps.toMutableList().also {
                                        it.removeAt(selectedStep)
                                    }
                                    selectedStep = -1
                                },
                            )
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            SirenaSecondaryButton(
                                text = "Cancel",
                                enabled = !busy,
                                onClick = {
                                    page = MovementsPage.List
                                    refresh()
                                },
                            )
                            SirenaPrimaryButton(
                                text = "Save",
                                enabled = !busy && editName.isNotBlank() && editSteps.isNotEmpty(),
                                onClick = {
                                    scope.launch {
                                        busy = true
                                        val id = editId ?: UUID.randomUUID().toString().take(12)
                                        val stepsArr = JSONArray()
                                        editSteps.forEach { stepsArr.put(it.toJson()) }
                                        val body =
                                            JSONObject()
                                                .put("id", id)
                                                .put("movement_id", id)
                                                .put("name", editName.trim())
                                                .put("steps", stepsArr)
                                        try {
                                            val j = vm.upsertMovement(body)
                                            if (j.optBoolean("ok", false)) {
                                                page = MovementsPage.List
                                                status = "Saved."
                                                refresh()
                                            } else {
                                                status = j.optString("error", "Save failed")
                                            }
                                        } catch (e: Exception) {
                                            status = e.message ?: "Save failed"
                                        } finally {
                                            busy = false
                                        }
                                    }
                                },
                            )
                        }
                    }
            }
        }
    }
}
