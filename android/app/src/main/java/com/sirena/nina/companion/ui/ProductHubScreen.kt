package com.sirena.nina.companion.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
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
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.DiscoveredDaemonUi
import com.sirena.nina.companion.R
import com.sirena.nina.companion.ui.sirena.SirenaCard
import com.sirena.nina.companion.ui.sirena.SirenaCardKind
import com.sirena.nina.companion.ui.sirena.SirenaColors
import com.sirena.nina.companion.ui.sirena.SirenaMutedText
import com.sirena.nina.companion.ui.sirena.SirenaDaemonConnectButton
import com.sirena.nina.companion.ui.sirena.SirenaPrimaryButton
import com.sirena.nina.companion.ui.sirena.SirenaType
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProductHubScreen(
    vm: CompanionViewModel,
    onOpenNina: () -> Unit,
) {
    val discovered by vm.discoveredDaemons.collectAsStateWithLifecycle()
    val diagnostics by vm.discoveryDiagnostics.collectAsStateWithLifecycle()
    val savedUrl by vm.savedDaemonUrl.collectAsStateWithLifecycle(initialValue = "")
    val jetsonLink by vm.jetsonLink.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()

    var showDiscovery by remember { mutableStateOf(false) }
    var showCarbotSoon by remember { mutableStateOf(false) }
    var showSirenaSoon by remember { mutableStateOf(false) }
    var discoveryError by remember { mutableStateOf<String?>(null) }
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    LaunchedEffect(showDiscovery) {
        if (showDiscovery) {
            discoveryError = null
            vm.scanForDaemons()
        }
    }

    if (showCarbotSoon) {
        AlertDialog(
            onDismissRequest = { showCarbotSoon = false },
            confirmButton = {
                TextButton(onClick = { showCarbotSoon = false }) { Text("OK") }
            },
            title = { Text("Carbot") },
            text = { Text("Coming soon.") },
        )
    }
    if (showSirenaSoon) {
        AlertDialog(
            onDismissRequest = { showSirenaSoon = false },
            confirmButton = {
                TextButton(onClick = { showSirenaSoon = false }) { Text("OK") }
            },
            title = { Text("Sirena") },
            text = { Text("Coming soon.") },
        )
    }

    if (showDiscovery) {
        ModalBottomSheet(
            onDismissRequest = { showDiscovery = false },
            sheetState = sheetState,
            containerColor = SirenaColors.panel,
        ) {
            Column(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 20.dp)
                    .padding(bottom = 24.dp),
            ) {
                Text(
                    "Nearby systems",
                    fontWeight = FontWeight.SemiBold,
                    color = SirenaColors.text,
                    fontSize = SirenaType.cardTitle,
                )
                Spacer(Modifier.height(6.dp))
                if (diagnostics.isScanning) {
                    LinearProgressIndicator(Modifier.fillMaxWidth())
                    Spacer(Modifier.height(8.dp))
                    SirenaMutedText("Scanning mDNS and LAN for link daemon (port 8787)…", maxLines = 2)
                } else {
                    SirenaMutedText(
                        "Tap Connect to save that daemon URL. Nothing is saved until you confirm.",
                        maxLines = 3,
                    )
                }
                discoveryError?.let {
                    Spacer(Modifier.height(8.dp))
                    Text(it, color = SirenaColors.danger, fontSize = SirenaType.muted)
                }
                Spacer(Modifier.height(12.dp))
                Column(
                    Modifier
                        .fillMaxWidth()
                        .heightIn(max = 400.dp)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    if (!diagnostics.isScanning && discovered.isEmpty()) {
                        SirenaMutedText(
                            "No systems found. Check that nina-link is running on the Jetson and you are on the same network.",
                            maxLines = 5,
                        )
                    } else {
                        discovered.forEach { d ->
                            DiscoveryRow(
                                d = d,
                                vm = vm,
                                savedUrl = savedUrl,
                                linkOnline = jetsonLink.isOnline,
                                onConnectError = { discoveryError = it },
                                onConnected = { showDiscovery = false },
                            )
                        }
                    }
                }
                Spacer(Modifier.height(12.dp))
                TextButton(
                    onClick = { showDiscovery = false },
                    modifier = Modifier.align(Alignment.End),
                ) {
                    Text("Close", color = SirenaColors.red)
                }
            }
        }
    }

    BoxWithConstraints(
        Modifier
            .fillMaxSize()
            .background(SirenaColors.cloud)
            .padding(20.dp),
    ) {
        val stack = maxWidth < 520.dp
        Column(Modifier.fillMaxSize()) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    // Same artwork as sirena_ui desktop branding (mirrored in res/drawable-nodpi/sirena_logo.png).
                    Image(
                        painter = painterResource(R.drawable.sirena_logo),
                        contentDescription = null,
                        modifier =
                            Modifier
                                .heightIn(max = 32.dp)
                                .widthIn(max = 100.dp),
                        contentScale = ContentScale.Fit,
                    )
                    Text(
                        "Products",
                        fontWeight = FontWeight.Bold,
                        color = SirenaColors.text,
                        fontSize = SirenaType.cardTitle,
                    )
                }
                AnimatedRadarFab(
                    onClick = { showDiscovery = true },
                    contentDescription = "Discover nearby systems",
                    energetic = showDiscovery && diagnostics.isScanning,
                    modifier = Modifier.padding(2.dp),
                )
            }
            Spacer(Modifier.height(16.dp))
            if (stack) {
                Column(
                    Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    ProductImageCard(
                        painter = painterResource(R.drawable.nina_robot),
                        label = "Nina",
                        onClick = onOpenNina,
                        modifier = Modifier.fillMaxWidth().heightIn(min = 160.dp),
                    )
                    ProductImageCard(
                        painter = painterResource(R.drawable.product_carbot),
                        label = "Carbot",
                        onClick = { showCarbotSoon = true },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 160.dp),
                    )
                    ProductImageCard(
                        painter = painterResource(R.drawable.sirena_logo),
                        label = "Coming soon",
                        onClick = { showSirenaSoon = true },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 160.dp),
                    )
                }
            } else {
                Row(
                    Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(16.dp),
                ) {
                    ProductImageCard(
                        painter = painterResource(R.drawable.nina_robot),
                        label = "Nina",
                        onClick = onOpenNina,
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    )
                    ProductImageCard(
                        painter = painterResource(R.drawable.product_carbot),
                        label = "Carbot",
                        onClick = { showCarbotSoon = true },
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    )
                    ProductImageCard(
                        painter = painterResource(R.drawable.sirena_logo),
                        label = "Coming soon",
                        onClick = { showSirenaSoon = true },
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    )
                }
            }
        }
    }
}

@Composable
private fun ProductImageCard(
    painter: Painter,
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    SirenaCard(
        modifier =
            modifier
                .clip(RoundedCornerShape(14.dp))
                .clickable(onClick = onClick),
        kind = SirenaCardKind.Hero,
    ) {
        Column(
            Modifier
                .fillMaxSize()
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Image(
                painter = painter,
                contentDescription = null,
                modifier =
                    Modifier
                        .heightIn(min = 72.dp, max = 200.dp)
                        .fillMaxWidth(0.9f),
                contentScale = ContentScale.Fit,
            )
            Spacer(Modifier.height(12.dp))
            Text(
                label,
                fontWeight = FontWeight.SemiBold,
                color = SirenaColors.text,
                fontSize = SirenaType.base,
            )
        }
    }
}

@Composable
private fun DiscoveryRow(
    d: DiscoveredDaemonUi,
    vm: CompanionViewModel,
    savedUrl: String,
    linkOnline: Boolean,
    onConnectError: (String) -> Unit,
    onConnected: () -> Unit,
) {
    val title =
        d.displayName?.trim()?.takeIf { it.isNotEmpty() }
            ?: d.hostname?.trim()?.takeIf { it.isNotEmpty() }
            ?: d.systemId?.trim()?.takeIf { it.isNotEmpty() }
            ?: "Link daemon"
    SirenaCard(kind = SirenaCardKind.Subtle) {
        Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        title,
                        fontWeight = FontWeight.SemiBold,
                        color = SirenaColors.text,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    d.hostname?.takeIf { it.isNotBlank() && it != title }?.let {
                        SirenaMutedText(it, maxLines = 1)
                    }
                    Text(
                        d.baseUrl,
                        fontSize = SirenaType.muted,
                        color = SirenaColors.muted,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    d.systemId?.takeIf { it.isNotBlank() }?.let {
                        SirenaMutedText("System ID · $it", maxLines = 1)
                    }
                }
                SirenaDaemonConnectButton(
                    vm = vm,
                    daemonBaseUrl = d.baseUrl,
                    savedUrl = savedUrl,
                    linkOnline = linkOnline,
                    modifier = Modifier.padding(start = 8.dp),
                    onConnected = onConnected,
                    onConnectError = onConnectError,
                )
            }
        }
    }
}
