#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
  uint32_t next_sof_us;
  uint32_t last_sof_us;
  uint32_t interval_min_us;
  uint32_t interval_max_us;
  bool deadline_started;
} pio_usb_sof_scheduler_t;

static inline void pio_usb_sof_scheduler_record_frame(
    pio_usb_sof_scheduler_t *scheduler, uint32_t now_us) {
  if (scheduler->deadline_started) {
    uint32_t const interval = now_us - scheduler->last_sof_us;
    if (scheduler->interval_min_us == 0 ||
        interval < scheduler->interval_min_us) {
      scheduler->interval_min_us = interval;
    }
    if (interval > scheduler->interval_max_us) {
      scheduler->interval_max_us = interval;
    }
  }

  scheduler->deadline_started = true;
  scheduler->last_sof_us = now_us;
  scheduler->next_sof_us = now_us + 1000u;
}

static inline void pio_usb_sof_scheduler_record_ordinary_frame(
    pio_usb_sof_scheduler_t *scheduler, uint32_t now_us) {
  pio_usb_sof_scheduler_record_frame(scheduler, now_us);
}

static inline bool pio_usb_sof_scheduler_flash_frame_due(
    pio_usb_sof_scheduler_t *scheduler, uint32_t now_us) {
  if (scheduler->deadline_started &&
      (int32_t)(now_us - scheduler->next_sof_us) < 0) {
    return false;
  }

  pio_usb_sof_scheduler_record_frame(scheduler, now_us);
  return true;
}

static inline uint32_t pio_usb_sof_scheduler_interval_min_us(
    pio_usb_sof_scheduler_t const *scheduler) {
  return scheduler->interval_min_us;
}

static inline uint32_t pio_usb_sof_scheduler_interval_max_us(
    pio_usb_sof_scheduler_t const *scheduler) {
  return scheduler->interval_max_us;
}
