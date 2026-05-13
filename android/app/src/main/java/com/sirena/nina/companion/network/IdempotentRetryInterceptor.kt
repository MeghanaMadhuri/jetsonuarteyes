package com.sirena.nina.companion.network

import okhttp3.Interceptor
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Retries idempotent GET/HEAD on transient Wi‑Fi I/O failures (ARP stall, first-packet loss).
 * POST bodies are not retried here (server may have applied side effects).
 */
class IdempotentRetryInterceptor(
    private val maxRetries: Int = 2,
    private val backoffStartMs: Long = 120L,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        if (request.method != "GET" && request.method != "HEAD") {
            return chain.proceed(request)
        }
        var last: IOException? = null
        repeat(maxRetries + 1) { attempt ->
            try {
                return chain.proceed(request)
            } catch (e: IOException) {
                last = e
                if (attempt >= maxRetries) throw e
                val sleepMs = backoffStartMs * (attempt + 1)
                try {
                    TimeUnit.MILLISECONDS.sleep(sleepMs)
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    throw e
                }
            }
        }
        throw last ?: IOException("retry exhausted")
    }
}
