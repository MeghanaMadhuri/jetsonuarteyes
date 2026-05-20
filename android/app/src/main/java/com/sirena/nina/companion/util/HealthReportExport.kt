package com.sirena.nina.companion.util

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import com.sirena.nina.companion.CompanionViewModel
import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

object HealthReportExport {
    private fun timestampLabel(): String =
        LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"))

    suspend fun buildReportJson(vm: CompanionViewModel): String =
        withContext(Dispatchers.IO) {
            val health = vm.fetchRobotHealth()
            val status = vm.fetchDaemonHealth()
            val root =
                JSONObject()
                    .put("exported_at", LocalDateTime.now().toString())
                    .put("source", "nina_android_companion")
            if (health != null) {
                root.put("robot_health", health)
            } else {
                root.put("robot_health_error", "unavailable")
            }
            if (status != null) {
                root.put("daemon_health", status)
            }
            root.toString(2)
        }

    fun defaultFilename(): String = "nina_health_${timestampLabel()}.json"

    fun writeToCache(context: Context, json: String, filename: String = defaultFilename()): File {
        val dir = File(context.cacheDir, "exports").apply { mkdirs() }
        val file = File(dir, filename)
        file.writeText(json)
        return file
    }

    fun shareFile(context: Context, file: File) {
        val uri =
            FileProvider.getUriForFile(
                context,
                "${context.packageName}.fileprovider",
                file,
            )
        val send =
            Intent(Intent.ACTION_SEND).apply {
                type = "application/json"
                putExtra(Intent.EXTRA_STREAM, uri)
                putExtra(Intent.EXTRA_SUBJECT, "Nina health report")
                putExtra(Intent.EXTRA_TITLE, "Share health report")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
        context.startActivity(Intent.createChooser(send, "Share health report"))
    }

    suspend fun share(context: Context, vm: CompanionViewModel) {
        val json = buildReportJson(vm)
        val file = writeToCache(context, json)
        shareFile(context, file)
    }
}
