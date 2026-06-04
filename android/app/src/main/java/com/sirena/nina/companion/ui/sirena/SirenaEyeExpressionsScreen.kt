package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.CompanionViewModel
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

private data class EyeExprRow(
    val id: Int,
    val name: String,
    val mood: String,
    val label: String,
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SirenaEyeExpressionsScreen(
    vm: CompanionViewModel,
    shellCompact: Boolean = false,
) {
    @Suppress("UNUSED_PARAMETER")
    val _compact = shellCompact

    val scope = rememberCoroutineScope()
    var rows by remember { mutableStateOf<List<EyeExprRow>>(emptyList()) }
    var status by remember { mutableStateOf("Loading expressions…") }
    var busy by remember { mutableStateOf(false) }
    var selectedId by remember { mutableStateOf<Int?>(null) }

    fun parseList(payload: JSONObject?): List<EyeExprRow> {
        if (payload == null) return emptyList()
        val arr = payload.optJSONArray("expressions") ?: JSONArray()
        val out = mutableListOf<EyeExprRow>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val id = o.optInt("id", -1)
            if (id < 0) continue
            val name = o.optString("name", "expr$id")
            val mood = o.optString("mood", "~")
            val label = o.optString("label", "$id $name")
            out.add(EyeExprRow(id, name, mood, label))
        }
        return out.sortedBy { it.id }
    }

    fun refresh() {
        scope.launch {
            busy = true
            status = "Loading…"
            try {
                rows = parseList(vm.fetchEyeExpressions())
                status =
                    if (rows.isEmpty()) {
                        "No expressions from robot."
                    } else {
                        "Tap an expression — sent over UART to the face display."
                    }
            } catch (e: Exception) {
                status = e.message ?: "Failed to load"
            } finally {
                busy = false
            }
        }
    }

    LaunchedEffect(Unit) {
        refresh()
    }

    androidx.compose.foundation.layout.Column(
        modifier =
            Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text(
            "Eye expressions",
            fontWeight = FontWeight.SemiBold,
        )
        Text(status)

        FlowRow(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            for (row in rows) {
                val sel = selectedId == row.id
                FilterChip(
                    selected = sel,
                    onClick = {
                        if (busy) return@FilterChip
                        scope.launch {
                            busy = true
                            status = "Sending ${row.id} ${row.name}…"
                            try {
                                vm.sendEyeExpression(row.id)
                                selectedId = row.id
                                status = "Showing ${row.id} ${row.name}"
                            } catch (e: Exception) {
                                status = e.message ?: "Send failed"
                            } finally {
                                busy = false
                            }
                        }
                    },
                    label = {
                        Text(
                            row.label.replace('_', ' '),
                            maxLines = 1,
                        )
                    },
                )
            }
        }
    }
}
