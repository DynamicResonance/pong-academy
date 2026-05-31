#pragma once

#include <pthread.h>
#include <stdint.h>

#define BSC_FIFO_SIZE 512

typedef struct {
    uint16_t seqno;
    uint16_t flags;
    uint32_t tick;
    uint32_t level;
} gpioReport_t;

typedef struct {
    uint32_t gpioOn;
    uint32_t gpioOff;
    uint32_t usDelay;
} gpioPulse_t;

typedef struct {
    uint32_t control;
    int rxCnt;
    char rxBuf[BSC_FIFO_SIZE];
    int txCnt;
    char txBuf[BSC_FIFO_SIZE];
} bsc_xfer_t;

typedef void *(gpioThreadFunc_t)(void *);
