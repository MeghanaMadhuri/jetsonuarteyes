package com.sirena.nina.companion.network

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import com.sirena.nina.companion.util.NinaLog
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.coroutines.resume

/**
 * mDNS browse for `_http._tcp`, resolve candidates that look like the Nina gateway
 * (default port **8787** or service name contains **"Nina"**). Returns base URLs
 * (``http://host:port``); callers should probe ``/health`` on a worker thread.
 */
object BonjourJetsonFinder {

    suspend fun discoverBaseUrls(context: Context, listenMs: Long = 3800L): List<String> =
        withContext(Dispatchers.Main) {
            suspendCancellableCoroutine { cont ->
                val nsd = context.applicationContext.getSystemService(Context.NSD_SERVICE) as? NsdManager
                if (nsd == null) {
                    cont.resume(emptyList())
                    return@suspendCancellableCoroutine
                }
                val urls = CopyOnWriteArrayList<String>()
                val finished = AtomicBoolean(false)

                fun complete(result: List<String>) {
                    if (finished.compareAndSet(false, true)) {
                        cont.resume(result)
                    }
                }

                val resolveListener =
                    object : NsdManager.ResolveListener {
                        override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
                            NinaLog.warn("BonjourJetsonFinder", "resolve failed code=$errorCode name=${serviceInfo.serviceName}")
                        }

                        override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                            val port = serviceInfo.port
                            val host = serviceInfo.host?.hostAddress ?: return
                            val name = serviceInfo.serviceName ?: ""
                            val likely =
                                port == 8787 ||
                                    name.contains("nina", ignoreCase = true)
                            if (!likely) return
                            urls.add("http://$host:$port".trimEnd('/'))
                        }
                    }

                val discoveryListener =
                    object : NsdManager.DiscoveryListener {
                        override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                            NinaLog.warn("BonjourJetsonFinder", "start discovery failed $errorCode")
                        }

                        override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}

                        override fun onDiscoveryStarted(serviceType: String) {}

                        override fun onDiscoveryStopped(serviceType: String) {}

                        override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                            try {
                                nsd.resolveService(serviceInfo, resolveListener)
                            } catch (e: Exception) {
                                NinaLog.warn("BonjourJetsonFinder", "resolve submit: ${e.message}")
                            }
                        }

                        override fun onServiceLost(serviceInfo: NsdServiceInfo) {}
                    }

                val serviceType = "_http._tcp"
                try {
                    @Suppress("DEPRECATION")
                    nsd.discoverServices(serviceType, NsdManager.PROTOCOL_DNS_SD, discoveryListener)
                } catch (e: Exception) {
                    NinaLog.warn("BonjourJetsonFinder", "discoverServices: ${e.message}")
                    cont.resume(emptyList())
                    return@suspendCancellableCoroutine
                }

                cont.invokeOnCancellation {
                    runCatching { nsd.stopServiceDiscovery(discoveryListener) }
                    complete(urls.distinct())
                }

                android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(
                    {
                        runCatching { nsd.stopServiceDiscovery(discoveryListener) }
                        complete(urls.distinct())
                    },
                    listenMs,
                )
            }
        }
}
