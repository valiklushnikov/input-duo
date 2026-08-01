#include <Wire.h>
#include "Mouse.h"
unsigned long timing;
int mx, my; int mz; int mstat;

void setup() {
  Wire.begin(0x02);
  Wire.onReceive(receiveEventX);
  Serial.begin(9600);
}

void loop() {
  
  if (micros() - timing > 8000){
  timing = micros();
  if(receiveEventX > 0){
  Mouse.move(mx, -(my), -(mz));
  if (bitRead(mstat, 0)){
    if(!Mouse.isPressed(MOUSE_LEFT)){
      Mouse.press(MOUSE_LEFT);
    }
  }
  else {
    if (Mouse.isPressed(MOUSE_LEFT)) {
      Mouse.release(MOUSE_LEFT);
    }
  }
  /*-----------------------Left Button-----------------------------*/
  if (bitRead(mstat, 1)) {
    if(!Mouse.isPressed(MOUSE_RIGHT)){
      Mouse.press(MOUSE_RIGHT);
    }
  }
  else {
    if (Mouse.isPressed(MOUSE_RIGHT)) {
      Mouse.release(MOUSE_RIGHT);
    }
  }
  /*-----------------------Right Button-----------------------------*/
  //if (bitRead(mstat, 2)) {
  //  if(!Mouse.isPressed(MOUSE_MIDDLE)){
  //    Mouse.press(MOUSE_MIDDLE);
   // }
  //}
  //else {
  //  if (Mouse.isPressed(MOUSE_MIDDLE)) {
   //   Mouse.release(MOUSE_MIDDLE);
   // }
 // }
  /*-----------------------Middle Button-----------------------------*/
 }
  } 
}

// function that executes whenever data is received from master
// this function is registered as an event, see setup()
void receiveEventX(int AsixX) {
  mx = Wire.read();
  my = Wire.read();
  mz = Wire.read();
  mstat = Wire.read();// receive byte as an integer
           // print the integer
}
