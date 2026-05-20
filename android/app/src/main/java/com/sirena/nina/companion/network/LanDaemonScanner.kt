package com.sirena.nina.companion.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * Finds hosts on the same /24 as this tablet that expose [nina-link] on port 8787.
 *
 * Home routers (e.g. 192.168.1.1) often appear as the Wi‑Fi "gateway" but do **not** run the
 * daemon — probing the subnet avoids mistaking the router for the Jetson.
 */
object LanDaemonScanner {
    data class DiscoveredDaemon(
        val baseUrl: String,
        val systemId: String?,
        val hostname: String?,
        /** From GET ``/health`` ``display_name`` (Jetson ``NINA_LINK_ROBOT_NAME``). */
        val displayName: String? = null,
    )

    data class ScanReport(
        val discovered: List<DiscoveredDaemon>,
        val deviceIpv4: String?,
        val subnetPrefix: String?,
        val hostCount: Int,
        val probeAttempts: Int,
        val successfulHosts: Int,
        val failedProbes: Int,
        val durationMs: Long,
        val error: String? = null,
    )

    private val probeClient: OkHttpClient =
        OkHttpClient.Builder()
            // Discovery over real Wi-Fi can be bursty; very short timeouts miss healthy Jetsons.
            .connectTimeout(1200, TimeUnit.MILLISECONDS)
            .readTimeout(1200, TimeUnit.MILLISECONDS)
            .writeTimeout(1200, TimeUnit.MILLISECONDS)
            .callTimeout(1500, TimeUnit.MILLISECONDS)
            .build()

    /** Returns sorted daemon candidates with stable identity from `/health`. */
    suspend fun scanIpv4Subnet(deviceIpv4: String?): List<DiscoveredDaemon> =
        withContext(Dispatchers.IO) {
            scanIpv4SubnetWithReport(deviceIpv4).discovered
        }

    /** Returns detailed diagnostics for UI troubleshooting. */
    suspend fun scanIpv4SubnetWithReport(deviceIpv4: String?): ScanReport =
        withContext(Dispatchers.IO) {
            val startMs = System.currentTimeMillis()
            if (deviceIpv4.isNullOrBlank()) {
                return@withContext ScanReport(
                    discovered = emptyList(),
                    deviceIpv4 = null,
                    subnetPrefix = null,
                    hostCount = 0,
                    probeAttempts = 0,
                    successfulHosts = 0,
                    failedProbes = 0,
                    durationMs = System.currentTimeMillis() - startMs,
                    error = "No active IPv4 on this device",
                )
            }
            val parts = deviceIpv4.split(".")
            if (parts.size != 4) {
                return@withContext ScanReport(
                    discovered = emptyList(),
                    deviceIpv4 = deviceIpv4,
                    subnetPrefix = null,
                    hostCount = 0,
                    probeAttempts = 0,
                    successfulHosts = 0,
                    failedProbes = 0,
                    durationMs = System.currentTimeMillis() - startMs,
                    error = "Invalid device IP format",
                )
            }
            val prefix = "${parts[0]}.${parts[1]}.${parts[2]}"
            val ips =
                (1..254).map { host -> "$prefix.$host" }.filter {
                    !it.equals(deviceIpv4, ignoreCase = true)
                }

            val found = ConcurrentLinkedQueue<DiscoveredDaemon>()
            val attempts = AtomicInteger(0)
            coroutineScope {
                // Chunk to bound concurrency (avoid 254 simultaneous sockets).
                ips.chunked(48).forEach { chunk ->
                    chunk
                        .map { ip ->
                            async(Dispatchers.IO) {
                                val base = "http://$ip:8787"
                                try {
                                    // One retry smooths transient packet loss / ARP warmup on first contact.
                                    attempts.incrementAndGet()
                                    val first = probeHealth(base)
                                    when {
                                        first != null -> found.add(first)
                                        else -> {
                                            attempts.incrementAndGet()
                                            probeHealth(base)?.let { found.add(it) }
                                        }
                                    }
                                } catch (_: Exception) {
                                }
                                Unit
                            }
                        }.awaitAll()
                }
            }
            val discovered =
                found
                .groupBy { it.baseUrl.lowercase() }
                .map { it.value.first() }
                .sortedBy { it.baseUrl.lowercase() }
            val attemptCount = attempts.get()
            val successCount = discovered.size
            ScanReport(
                discovered = discovered,
                deviceIpv4 = deviceIpv4,
                subnetPrefix = prefix,
                hostCount = ips.size,
                probeAttempts = attemptCount,
                successfulHosts = successCount,
                failedProbes = (attemptCount - successCount).coerceAtLeast(0),
                durationMs = System.currentTimeMillis() - startMs,
            )
        }

    private fun probeHealth(baseUrl: String): DiscoveredDaemon? {
        val url = "$baseUrl/health".trimEnd('/')
        val req =
            Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .get()
                .build()
        probeClient.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) return null
            val body = resp.body?.string().orEmpty()
            val json = kotlin.runCatching { org.json.JSONObject(body) }.getOrNull()
            return DiscoveredDaemon(
                baseUrl = baseUrl.trimEnd('/'),
                systemId = json?.optString("system_id")?.takeIf { it.isNotBlank() },
                hostname = json?.optString("hostname")?.takeIf { it.isNotBlank() },
                displayName = json?.optString("display_name")?.takeIf { it.isNotBlank() },
            )
        }
    }
}
