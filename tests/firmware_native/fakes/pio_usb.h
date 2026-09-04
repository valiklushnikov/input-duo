#pragma once

#include <stdint.h>

typedef struct pio_usb_configuration {
    uint8_t pin_dp;
} pio_usb_configuration_t;

#define PIO_USB_DEFAULT_CONFIG pio_usb_configuration_t{}
