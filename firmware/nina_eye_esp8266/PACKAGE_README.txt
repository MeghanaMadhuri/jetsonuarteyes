Nina ESP8266 Eye Display — attachment package
==============================================

Files in this zip:

  User_Setup_ST7735_Nina.h      -> copy to Arduino/libraries/TFT_eSPI/User_Setups/
  User_Setup_Select_Nina.patch.txt -> edit TFT_eSPI/User_Setup_Select.h
  nina_eye_esp8266.ino          -> open in Arduino IDE and upload
  README.md                     -> flash checklist

  ESP8266_EYE_DEPLOYMENT_GUIDE.md -> full formal setup (email-style)
  NINA_EYE_UART.md              -> Jetson env vars and Nina integration

Robot (validated): USB cable Jetson USB port -> NodeMCU micro-USB
  NINA_EYE_UART_PORT=/dev/ttyUSB0

Repository: https://github.com/MeghanaMadhuri/jetsonuarteyes.git
