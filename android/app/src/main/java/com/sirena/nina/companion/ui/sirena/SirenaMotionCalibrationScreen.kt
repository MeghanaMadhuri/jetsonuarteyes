package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.CompanionViewModel
import kotlinx.coroutines.launch
import org.json.JSONObject

@Composable
fun SirenaMotionCalibrationScreen(
    vm: CompanionViewModel,
    onBack: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val scroll = rememberScrollState()
    var err by remember { mutableStateOf<String?>(null) }
    var msg by remember { mutableStateOf<String?>(null) }
    var idLeft by remember { mutableIntStateOf(12) }
    var idRight by remember { mutableIntStateOf(13) }

    var fwdL by remember { mutableIntStateOf(2048) }
    var fwdR by remember { mutableIntStateOf(2048) }
    var backL by remember { mutableIntStateOf(2048) }
    var backR by remember { mutableIntStateOf(2048) }
    var tlL by remember { mutableIntStateOf(2048) }
    var tlR by remember { mutableIntStateOf(2048) }
    var trL by remember { mutableIntStateOf(2048) }
    var trR by remember { mutableIntStateOf(2048) }
    var turnDur by remember { mutableFloatStateOf(0.3f) }

    fun loadFrom(j: JSONObject) {
        idLeft = j.optInt("id_left", idLeft)
        idRight = j.optInt("id_right", idRight)
        fwdL = j.optInt("forward_pos_left", fwdL)
        fwdR = j.optInt("forward_pos_right", fwdR)
        backL = j.optInt("backward_pos_left", backL)
        backR = j.optInt("backward_pos_right", backR)
        tlL = j.optInt("turn_left_pos_left", tlL)
        tlR = j.optInt("turn_left_pos_right", tlR)
        trL = j.optInt("turn_right_pos_left", trL)
        trR = j.optInt("turn_right_pos_right", trR)
        turnDur = j.optDouble("turn_duration_sec", turnDur.toDouble()).toFloat()
    }

    LaunchedEffect(Unit) {
        val j = vm.fetchDriveCalibration()
        if (j != null && j.optBoolean("ok", true)) loadFrom(j)
    }

    Column(
        Modifier
            .fillMaxSize()
            .padding(horizontal = 10.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        SirenaSecondaryButton(
            text = "\u2190 Back to Drive",
            onClick = onBack,
            modifier = Modifier.fillMaxWidth(),
        )
        SirenaBreadcrumbLine(listOf("Nina", "Motion calibration"))
        SirenaMutedText(
            "Adjust lean goal ticks (0\u20134095). Live preview moves servos; release brake on Drive and avoid autonomy while tuning.",
            maxLines = 4,
        )
        err?.let { SirenaInlineStatusBanner(isError = true, title = "Calibration", message = it) }
        msg?.let { SirenaInlineStatusBanner(isError = false, title = "Saved", message = it) }

        Column(
            Modifier
                .weight(1f)
                .fillMaxWidth()
                .verticalScroll(scroll),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            CalSection(
                title = "Forward motion",
                leftId = idLeft,
                rightId = idRight,
                left = fwdL,
                right = fwdR,
                onLeft = { fwdL = it },
                onRight = { fwdR = it },
                onPreview = {
                    scope.launch {
                        vm.previewDriveCalibration(fwdL, fwdR)
                    }
                },
                onSave = {
                    scope.launch {
                        val body =
                            JSONObject()
                                .put("forward_pos_left", fwdL)
                                .put("forward_pos_right", fwdR)
                        val r = vm.saveDriveCalibration(body)
                        if (r?.optBoolean("ok") == true) {
                            msg = "Forward goals saved."
                            err = null
                            vm.fetchDriveCalibration()?.let { loadFrom(it) }
                        } else {
                            err = r?.optString("error") ?: "Save failed"
                        }
                    }
                },
            )
            CalSection(
                title = "Backward motion",
                leftId = idLeft,
                rightId = idRight,
                left = backL,
                right = backR,
                onLeft = { backL = it },
                onRight = { backR = it },
                onPreview = {
                    scope.launch {
                        vm.previewDriveCalibration(backL, backR)
                    }
                },
                onSave = {
                    scope.launch {
                        val body =
                            JSONObject()
                                .put("backward_pos_left", backL)
                                .put("backward_pos_right", backR)
                        val r = vm.saveDriveCalibration(body)
                        if (r?.optBoolean("ok") == true) {
                            msg = "Backward goals saved."
                            err = null
                        } else {
                            err = r?.optString("error") ?: "Save failed"
                        }
                    }
                },
            )
            CalSection(
                title = "Turn left (pivot lean)",
                leftId = idLeft,
                rightId = idRight,
                left = tlL,
                right = tlR,
                onLeft = { tlL = it },
                onRight = { tlR = it },
                onPreview = { scope.launch { vm.previewDriveCalibration(tlL, tlR) } },
                onSave = {
                    scope.launch {
                        val body =
                            JSONObject()
                                .put("turn_left_pos_left", tlL)
                                .put("turn_left_pos_right", tlR)
                        val r = vm.saveDriveCalibration(body)
                        if (r?.optBoolean("ok") == true) msg = "Turn left goals saved." else err = r?.optString("error")
                    }
                },
            )
            CalSection(
                title = "Turn right (pivot lean)",
                leftId = idLeft,
                rightId = idRight,
                left = trL,
                right = trR,
                onLeft = { trL = it },
                onRight = { trR = it },
                onPreview = { scope.launch { vm.previewDriveCalibration(trL, trR) } },
                onSave = {
                    scope.launch {
                        val body =
                            JSONObject()
                                .put("turn_right_pos_left", trL)
                                .put("turn_right_pos_right", trR)
                        val r = vm.saveDriveCalibration(body)
                        if (r?.optBoolean("ok") == true) msg = "Turn right goals saved." else err = r?.optString("error")
                    }
                },
            )
            SirenaCard {
                Text("Timed turn duration", fontWeight = FontWeight.Bold, color = SirenaColors.text)
                SirenaMutedText("Hold time for Drive Turn left/right (0.01\u20131.0 s).")
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SirenaSecondaryButton(
                        text = "\u2212",
                        onClick = { turnDur = (turnDur - 0.05f).coerceIn(0.01f, 1f) },
                    )
                    Text(
                        String.format("%.2f s", turnDur),
                        modifier = Modifier.weight(1f),
                        fontWeight = FontWeight.Bold,
                    )
                    SirenaSecondaryButton(
                        text = "+",
                        onClick = { turnDur = (turnDur + 0.05f).coerceIn(0.01f, 1f) },
                    )
                    SirenaPrimaryButton(
                        text = "Save duration",
                        onClick = {
                            scope.launch {
                                val r =
                                    vm.saveDriveCalibration(
                                        JSONObject().put("turn_duration_sec", turnDur.toDouble()),
                                    )
                                if (r?.optBoolean("ok") == true) msg = "Turn duration saved." else err = r?.optString("error")
                            }
                        },
                    )
                }
            }
            SirenaSecondaryButton(
                text = "Neutral (brake pose)",
                onClick = { scope.launch { vm.neutralDriveCalibration() } },
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun CalSection(
    title: String,
    leftId: Int,
    rightId: Int,
    left: Int,
    right: Int,
    onLeft: (Int) -> Unit,
    onRight: (Int) -> Unit,
    onPreview: () -> Unit,
    onSave: () -> Unit,
) {
    SirenaCard {
        Text(title, fontWeight = FontWeight.Bold, color = SirenaColors.text)
        TickRow("Motor $leftId", left, { onLeft((left - 1).coerceIn(0, 4095)) }, { onLeft((left + 1).coerceIn(0, 4095)) })
        TickRow("Motor $rightId", right, { onRight((right - 1).coerceIn(0, 4095)) }, { onRight((right + 1).coerceIn(0, 4095)) })
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SirenaSecondaryButton(text = "Preview", onClick = onPreview, modifier = Modifier.weight(1f))
            SirenaPrimaryButton(text = "Save", onClick = onSave, modifier = Modifier.weight(1f))
        }
    }
}

@Composable
private fun TickRow(
    label: String,
    value: Int,
    onMinus: () -> Unit,
    onPlus: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(label, modifier = Modifier.weight(1f), color = SirenaColors.text)
        SirenaSecondaryButton(text = "\u2212", onClick = onMinus)
        Text("$value", fontWeight = FontWeight.Bold)
        SirenaSecondaryButton(text = "+", onClick = onPlus)
    }
}
