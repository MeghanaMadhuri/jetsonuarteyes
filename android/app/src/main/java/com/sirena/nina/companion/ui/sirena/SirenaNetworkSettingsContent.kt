package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sirena.nina.companion.CompanionUiState
import com.sirena.nina.companion.CompanionViewModel

private val USER_MODES = listOf("boot_default", "force_ap", "force_sta")

/**
 * Jetson Wi‑Fi controls — layout aligned with [sirena_ui.screens.settings_screen.SettingsScreen._build_network_pane]
 * and shared with the companion **Network** tab.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SirenaNetworkSettingsScrollContent(
    vm: CompanionViewModel,
    state: CompanionUiState,
    breadcrumbParts: List<String>,
    modifier: Modifier = Modifier,
    /** On phones: list does not accept drag scroll (single-screen shell). */
    userScrollEnabled: Boolean = true,
) {
    val ready = state as? CompanionUiState.Ready
    val status = ready?.status
    var pairPin by rememberSaveable { mutableStateOf("") }
    var pairHint by remember { mutableStateOf<String?>(null) }
    var newSsid by rememberSaveable { mutableStateOf("") }
    var newPassword by rememberSaveable { mutableStateOf("") }
    var wifiFormHint by remember { mutableStateOf<String?>(null) }
    var modePick by rememberSaveable { mutableStateOf("boot_default") }
    var modeMenu by remember { mutableStateOf(false) }

    val shell = LocalSirenaShellCompact.current
    val listGap = if (shell) 5.dp else 8.dp
    val vSm = if (shell) 3.dp else 4.dp
    val vMd = if (shell) 4.dp else 6.dp
    val vLg = if (shell) 6.dp else 8.dp
    val vXl = if (shell) 7.dp else 10.dp
    val vXxl = if (shell) 8.dp else 12.dp
    val rowBtnGap = if (shell) 6.dp else 8.dp

    LazyColumn(
        modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(listGap),
        userScrollEnabled = userScrollEnabled,
    ) {
        item {
            SirenaBreadcrumbLine(breadcrumbParts)
        }
        item {
            SirenaCard {
                Text(
                    "Connectivity",
                    fontWeight = FontWeight.Bold,
                    color = SirenaColors.text,
                    fontSize = if (shell) 13.sp else 15.sp,
                )
                Spacer(Modifier.height(vSm))
                SirenaMutedText(
                    "Controls the Jetson Wi‑Fi role (access point vs home network). Uses the tablet HTTP API on your saved daemon URL.",
                    maxLines = 4,
                )
                Spacer(Modifier.height(vMd))
                val lines =
                    buildList {
                        add("Role: ${status?.wifiRole ?: "—"}")
                        add("IPv4: ${status?.ipv4 ?: "—"}")
                        add("AP SSID: ${status?.apSsid ?: "—"}")
                        add("Boot window remaining: ${status?.bootWaitRemainingSec ?: 0} s")
                        add("Client seen: ${status?.clientSeen ?: false}")
                        add("User mode: ${status?.userMode ?: "—"}")
                        status?.activeStaSsid?.takeIf { it.isNotBlank() }?.let { add("STA connected: $it") }
                        status?.activeStaProfile?.takeIf { it.isNotBlank() }?.let { add("NM profile: $it") }
                        val err = status?.lastError?.trim()?.takeIf { it.isNotEmpty() }
                        if (err != null) add("Last error: $err")
                        val saved = status?.savedNetworks.orEmpty()
                        if (saved.isNotEmpty()) {
                            val brief =
                                saved.take(8).joinToString(", ") { n ->
                                    "${n.ssid} (NM auto:${if (n.nmAutoconnect) "on" else "off"})"
                                }
                            add("Saved: $brief")
                        }
                    }
                Text(
                    lines.joinToString("\n"),
                    color = SirenaColors.text,
                    fontSize = if (shell) 10.sp else SirenaType.muted,
                    fontWeight = FontWeight.Normal,
                )
                Spacer(Modifier.height(vXl))
                Row(horizontalArrangement = Arrangement.spacedBy(rowBtnGap)) {
                    Button(onClick = { vm.refreshStatus() }) { Text("Refresh") }
                    Button(onClick = { vm.startApOnJetson() }) { Text("Start AP") }
                    Button(onClick = { vm.connectJetsonHome(null) }) { Text("Use home Wi‑Fi") }
                }
                Spacer(Modifier.height(vXxl))
                ExposedDropdownMenuBox(
                    expanded = modeMenu,
                    onExpandedChange = { modeMenu = it },
                ) {
                    OutlinedTextField(
                        value = modePick,
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("User mode") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = modeMenu) },
                        modifier =
                            Modifier
                                .menuAnchor()
                                .fillMaxWidth(),
                    )
                    ExposedDropdownMenu(
                        expanded = modeMenu,
                        onDismissRequest = { modeMenu = false },
                    ) {
                        USER_MODES.forEach { m ->
                            DropdownMenuItem(
                                text = { Text(m) },
                                onClick = {
                                    modePick = m
                                    modeMenu = false
                                },
                            )
                        }
                    }
                }
                Spacer(Modifier.height(vLg))
                Button(onClick = { vm.setMode(modePick) }) { Text("Apply mode") }
                Spacer(Modifier.height(vXxl))
                OutlinedTextField(
                    value = pairPin,
                    onValueChange = { pairPin = it; pairHint = null },
                    label = { Text("Pair PIN") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                pairHint?.let {
                    Spacer(Modifier.height(vSm))
                    SirenaMutedText(it, maxLines = 2)
                }
                Spacer(Modifier.height(vMd))
                Button(
                    onClick = {
                        pairHint = null
                        vm.pair(
                            pairPin.trim(),
                            onToken = {
                                pairHint = "Token saved on this tablet. PIN cleared."
                                pairPin = ""
                            },
                            onNoToken = {
                                pairHint = "No token returned — check the PIN and try again."
                            },
                        )
                    },
                    enabled = pairPin.length >= 4,
                ) {
                    Text("Pair session (token saved on tablet)")
                }
                Spacer(Modifier.height(vXl))
                OutlinedTextField(
                    value = newSsid,
                    onValueChange = { newSsid = it; wifiFormHint = null },
                    label = { Text("Home SSID") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(vMd))
                OutlinedTextField(
                    value = newPassword,
                    onValueChange = { newPassword = it; wifiFormHint = null },
                    label = { Text("Home password") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                wifiFormHint?.let {
                    Spacer(Modifier.height(vMd))
                    SirenaMutedText(it, maxLines = 2)
                }
                Spacer(Modifier.height(vLg))
                Button(
                    onClick = {
                        wifiFormHint = null
                        val s = newSsid.trim()
                        if (s.isEmpty()) {
                            wifiFormHint = "Enter a home SSID."
                            return@Button
                        }
                        vm.saveHomeAndOptionallyConnect(s, newPassword, connect = false)
                        wifiFormHint = "Home Wi‑Fi profile saved (Jetson)."
                        newPassword = ""
                    },
                    enabled = newSsid.isNotBlank(),
                ) {
                    Text("Save home credentials only")
                }
                Spacer(Modifier.height(vLg))
                SirenaHRule()
                Spacer(Modifier.height(vMd))
                SirenaMutedText(
                    "Saved profiles can be removed below. Full list also appears on the companion Network tab.",
                    maxLines = 3,
                )
            }
        }
        item {
            Text(
                "Saved profiles on Jetson (STA + AP)",
                color = SirenaColors.text,
                fontWeight = FontWeight.SemiBold,
                fontSize = if (shell) 12.sp else 14.sp,
            )
        }
        val nets = status?.savedNetworks.orEmpty()
        if (nets.isEmpty()) {
            item {
                SirenaCard(kind = SirenaCardKind.Subtle) {
                    SirenaMutedText(
                        "No 802.11 profiles yet. Pair if required, refresh, or save credentials above.",
                        maxLines = 4,
                    )
                }
            }
        } else {
            items(nets, key = { it.uuid }) { n ->
                val isAp = n.wifiMode == "ap"
                SirenaCard {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(
                                n.ssid,
                                fontWeight = FontWeight.SemiBold,
                                color = SirenaColors.text,
                                maxLines = 1,
                                fontSize = if (shell) 12.sp else SirenaType.cardTitle,
                            )
                            SirenaMutedText(
                                if (isAp) "Type: Access point (hotspot)" else "Type: Client (infrastructure)",
                                maxLines = 1,
                            )
                            SirenaMutedText("Profile: ${n.id}", maxLines = 1)
                            SirenaMutedText("UUID: ${n.uuid}", maxLines = 1)
                            SirenaMutedText("Autoconnect: ${if (n.nmAutoconnect) "on" else "off"}", maxLines = 1)
                        }
                    }
                    Spacer(Modifier.height(vLg))
                    Row(horizontalArrangement = Arrangement.spacedBy(rowBtnGap)) {
                        if (isAp) {
                            Button(onClick = { vm.startApOnJetson() }) { Text("Start AP") }
                        } else {
                            Button(onClick = { vm.connectJetsonHome(n.ssid) }) { Text("Connect") }
                        }
                        Button(onClick = { vm.deleteProfile(n.id) }) { Text("Remove") }
                    }
                }
            }
        }
    }
}
