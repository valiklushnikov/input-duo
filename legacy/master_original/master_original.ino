/*
 * an arduino sketch to interface with a ps/2 mouse.
 * Also uses serial protocol to talk back to the host
 * and report what it finds.
 */

/*
 * Pin 5 is the mouse data pin, pin 6 is the clock pin
 * Feel free to use whatever pins are convenient.
 */
#define MDATA 9
#define MCLK 10
#include "Mouse.h"
#include <Wire.h>
#include <Keyboard.h>;


const int buttonPin1 = 4;  // Set a button to any pin
const int buttonPin2 = 5;
const int buttonPin3 = 6;

int buttonState1 = 0;
int buttonState2 = 0;
int buttonState3 = 0;

int flag = 1;
boolean buttonWasUp = true;
boolean temp_button = false;
/*
 * according to some code I saw, these functions will
 * correctly set the mouse clock and data pins for
 * various conditions.
 */
void gohi(int pin)
{
  pinMode(pin, INPUT);
  digitalWrite(pin, HIGH);
}

void golo(int pin)
{
  pinMode(pin, OUTPUT);
  digitalWrite(pin, LOW);
}

void mouse_write(char data)
{
  char i;
  char parity = 1;

  //  Serial.print("Sending ");
  //  Serial.print(data, HEX);
  //  Serial.print(" to mouse\n");
  //  Serial.print("RTS");
  /* put pins in output mode */
  gohi(MDATA);
  gohi(MCLK);
  delayMicroseconds(300);
  golo(MCLK);
  delayMicroseconds(300);
  golo(MDATA);
  delayMicroseconds(10);
  /* start bit */
  gohi(MCLK);
  /* wait for mouse to take control of clock); */
  while (digitalRead(MCLK) == HIGH)
    ;
  /* clock is low, and we are clear to send data */
  for (i=0; i < 8; i++) {
    if (data & 0x01) {
      gohi(MDATA);
    } 
    else {
      golo(MDATA);
    }
    /* wait for clock cycle */
    while (digitalRead(MCLK) == LOW)
      ;
    while (digitalRead(MCLK) == HIGH)
      ;
    parity = parity ^ (data & 0x01);
    data = data >> 1;
  }  
  /* parity */
  if (parity) {
    gohi(MDATA);
  } 
  else {
    golo(MDATA);
  }
  while (digitalRead(MCLK) == LOW)
    ;
  while (digitalRead(MCLK) == HIGH)
    ;
  /* stop bit */
  gohi(MDATA);
  delayMicroseconds(50);
  while (digitalRead(MCLK) == HIGH)
    ;
  /* wait for mouse to switch modes */
  while ((digitalRead(MCLK) == LOW) || (digitalRead(MDATA) == LOW))
    ;
  /* put a hold on the incoming data. */
  golo(MCLK);
  //  Serial.print("done.\n");
}

/*
 * Get a byte of data from the mouse
 */
char mouse_read(void)
{
  char data = 0x00;
  int i;
  char bit = 0x01;

  //  Serial.print("reading byte from mouse\n");
  /* start the clock */
  gohi(MCLK);
  gohi(MDATA);
  delayMicroseconds(50);
  while (digitalRead(MCLK) == HIGH)
    ;
  delayMicroseconds(5);  /* not sure why */
  while (digitalRead(MCLK) == LOW) /* eat start bit */
    ;
  for (i=0; i < 8; i++) {
    while (digitalRead(MCLK) == HIGH)
      ;
    if (digitalRead(MDATA) == HIGH) {
      data = data | bit;
    }
    while (digitalRead(MCLK) == LOW)
      ;
    bit = bit << 1;
  }
  /* eat parity bit, which we ignore */
  while (digitalRead(MCLK) == HIGH)
    ;
  while (digitalRead(MCLK) == LOW)
    ;
  /* eat stop bit */
  while (digitalRead(MCLK) == HIGH)
    ;
  while (digitalRead(MCLK) == LOW)
    ;
  /* put a hold on the incoming data. */
  golo(MCLK);
  //  Serial.print("Recvd data ");
  //  Serial.print(data, HEX);
  //  Serial.print(" from mouse\n");
  return data;
}

void mouse_init()
{ char mouseId;
   
  gohi(MCLK);
  gohi(MDATA);
  //  Serial.print("Sending reset to mouse\n");
  mouse_write(0xff);
  mouse_read();  /* ack byte */
  //  Serial.print("Read ack byte1\n");
  mouse_read();  /* blank */
  mouse_read();  /* blank */

  //  Serial.print("Setting sample rate 200\n");
  mouse_write(0xf3);  /* Set rate command */
  mouse_read();  /* ack */
  mouse_write(0xC8);  /* Set rate command */
  mouse_read();  /* ack */
  //  Serial.print("Setting sample rate 100\n");
  mouse_write(0xf3);  /* Set rate command */
  mouse_read();  /* ack */
  mouse_write(0x64);  /* Set rate command */
  mouse_read();  /* ack */
  //  Serial.print("Setting sample rate 80\n");
  mouse_write(0xf3);  /* Set rate command */
  mouse_read();  /* ack */
  mouse_write(0x50);  /* Set rate command */
  mouse_read();  /* ack */
  //  Serial.print("Read device type\n");
  mouse_write(0xf2);  /* Set rate command */
  mouse_read();  /* ack */
  mouse_read();  /* mouse id, if this value is 0x00 mouse is standard, if it is 0x03 mouse is Intellimouse */
  //  Serial.print("Setting wheel\n");
  mouse_write(0xe8);  /* Set wheel resolution */
  mouse_read();  /* ack */
  mouse_write(0x03);  /* 8 counts per mm */
  mouse_read();  /* ack */
  mouse_write(0xe6);  /* scaling 1:1 */
  mouse_read();  /* ack */
  mouse_write(0xf3);  /* Set sample rate */
  mouse_read();  /* ack */
  mouse_write(0x28);  /* Set sample rate */
  mouse_read();  /* ack */
  mouse_write(0xf4);  /* Enable device */
  mouse_read();  /* ack */

  //  Serial.print("Sending remote mode code\n");
  mouse_write(0xf0);  /* remote mode */
  mouse_read();  /* ack */
  //  Serial.print("Read ack byte2\n");
  delayMicroseconds(100);
}

void setup()
{
  Serial.begin(9600);
  mouse_init();
  Wire.begin();
  //pinMode(buttonPin1, INPUT_PULLUP);  // Set the button as an input 
  //pinMode(buttonPin2, INPUT_PULLUP);
  //pinMode(buttonPin3, INPUT_PULLUP);
}

/*
 * get a reading from the mouse and report it back to the
 * host via the serial line.
 */
void loop()
{
  char mstat;
  char mx;
  char my;
  char mz;
  
  
  //buttonState1 = digitalRead(buttonPin1);
  //buttonState2 = digitalRead(buttonPin2);
  //buttonState3 = digitalRead(buttonPin3);
  /* get a reading from the mouse */
  mouse_write(0xeb);  /* give me data! */
  mouse_read();      /* ignore ack */
  mstat = mouse_read();
  mx = mouse_read();
  my = mouse_read();
  mz = mouse_read();
  
  //if (buttonState1 == LOW)
  //{flag = 1;}
  //if (buttonState2 == LOW)
  //{flag = 2;}
  //if (buttonState3 == LOW)
  //{flag = 3;}
  
  /*if (flag == 1)  // if the button goes low
  {
   
  Wire.beginTransmission(0x02);
  Wire.write(mx);
  Wire.write(my);
  Wire.write(mz);
  Wire.write(mstat);
  Wire.endTransmission();


  
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
  //-----------------------Left Button-----------------------------
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
  //-----------------------Right Button-----------------------------
  if (bitRead(mstat, 2)) {
    if(!Mouse.isPressed(MOUSE_MIDDLE)){
      Mouse.press(MOUSE_MIDDLE);
    }
  }
  else {
    if (Mouse.isPressed(MOUSE_MIDDLE)) {
      Mouse.release(MOUSE_MIDDLE);
    }
  }
  //-----------------------Middle Button-----------------------------
  }*/
 boolean buttonIsUp = bitRead(mstat, 2);
 if (buttonWasUp && buttonIsUp)
 {
  delay(10);
  buttonIsUp = bitRead(mstat, 2);
  }
if (buttonIsUp)
{
  temp_button = !temp_button;
}

  
 if (bitRead(mstat, 2) && flag == 0 && buttonWasUp == 0){
  flag++;
  
  Serial.print("flag1 = ");
  Serial.println(flag, DEC);
  }
 else if (bitRead(mstat, 2) && flag == 1 && buttonWasUp == 0){
 flag=0;
 
 Serial.print("flag2 = ");
 Serial.println(flag, DEC); 
 }
 
switch(flag)
 {
  case 0:
  {
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
  //-----------------------Left Button-----------------------------
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
  //-----------------------Right Button-----------------------------
  //if (bitRead(mstat, 2)) {
  //  if(!Mouse.isPressed(MOUSE_MIDDLE)){
  //    Mouse.press(MOUSE_MIDDLE);
  //  }
  //}
  //else {
  //  if (Mouse.isPressed(MOUSE_MIDDLE)) {
  //    Mouse.release(MOUSE_MIDDLE);
  //  }
  //}
  //-----------------------Middle Button-----------------------------
  break;
  }
  
  case 1:
  {
  Wire.beginTransmission(0x02);
  Wire.write(mx);
  Wire.write(my);
  Wire.write(mz);
  Wire.write(mstat);
  Wire.endTransmission();
  break;
  }
 }
 buttonWasUp = buttonIsUp;
 }
 
