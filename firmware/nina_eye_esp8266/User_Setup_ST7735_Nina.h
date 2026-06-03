// TFT_eSPI user setup for Nina NodeMCU + 1.8" ST7735 (160x128, rotation 1 in sketch).
//
// INSTALL (Arduino IDE):
// 1. Library Manager -> install "TFT_eSPI" by Bodmer
// 2. Copy this file to:
//    .../TFT_eSPI/User_Setups/User_Setup_ST7735_Nina.h
// 3. In TFT_eSPI/User_Setup_Select.h (parent folder):
//    - Comment out:  #include <User_Setup.h>
//    - Add ONE line: #include <User_Setups/User_Setup_ST7735_Nina.h>
// 4. Open nina_eye_esp8266.ino, board "NodeMCU 1.0 (ESP-12E)", upload.

#define USER_SETUP_ID 773501

#define ST7735_DRIVER
#define TFT_WIDTH 128
#define TFT_HEIGHT 160

#define TFT_CS PIN_D8   // GPIO15
#define TFT_RST PIN_D0  // GPIO16
#define TFT_DC PIN_D4   // GPIO2
#define TFT_MOSI PIN_D7 // GPIO13
#define TFT_SCLK PIN_D5 // GPIO14

#define TFT_MISO -1
#define TFT_BL -1

#define LOAD_GLCD
#define LOAD_FONT2
#define LOAD_FONT4
#define LOAD_GFXFF
#define SMOOTH_FONT

#define SPI_FREQUENCY 27000000
#define SPI_READ_FREQUENCY 20000000
