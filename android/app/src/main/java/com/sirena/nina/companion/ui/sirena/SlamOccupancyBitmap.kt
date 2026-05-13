package com.sirena.nina.companion.ui.sirena

import android.graphics.Bitmap
import com.sirena.nina.companion.data.SlamOccupancyGrid

/** Greyscale ARGB bitmap for Compose [androidx.compose.ui.graphics.asImageBitmap]. */
fun SlamOccupancyGrid.toBitmap(): Bitmap {
    val pixels = IntArray(width * height)
    var i = 0
    for (y in 0 until height) {
        for (x in 0 until width) {
            val v = bytes[y * width + x].toInt() and 0xFF
            pixels[i++] = (0xFF shl 24) or (v shl 16) or (v shl 8) or v
        }
    }
    return Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888).apply {
        setPixels(pixels, 0, width, 0, 0, width, height)
    }
}
