#include <errno.h>

#include <Arduino.h>
#include <SPI.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include "board_pins.h"
#include "hal/hal_accel.h"

/*
 * Implements hal_accel.h for the KX134-1211 accelerometer over the XIAO
 * ESP32S3's hardware SPI bus (SCK/MISO/MOSI = D8/D9/D10, board_pins.h) +
 * a software chip-select (PIN_KX134_CS) - the Arduino/ESP32 port of
 * mcu/src/drivers/kx134.c. The register map, bit-field values, and
 * overall init/start/read sequencing below are carried over verbatim from
 * that file (same chip, same datasheet - KX134-1211 Technical Reference
 * Manual Rev 5.0, Specifications datasheet Rev 4.0), since none of that
 * is MCU-specific; only the SPI transaction mechanics and the ISR/
 * semaphore glue are re-expressed in Arduino/FreeRTOS terms instead of
 * Zephyr's spi_transceive_dt()/gpio_add_callback()/k_sem_*() calls.
 *
 * SPI mode: Mode 0 (CPOL=0, CPHA=0), MSB-first, <=10MHz - per the
 * datasheet's 4-wire timing diagram, same as mcu/'s kx134.c cites (this
 * is a chip-datasheet fact, not something mcu/'s hardware bring-up
 * discovered empirically, unlike that file's SCK/MISO/MOSI pin-routing
 * anecdote, which was genuinely STM32-specific and doesn't carry over).
 */

#define KX134_SPI_READ_BIT 0x80
#define KX134_SPI_CLOCK_HZ 10000000

/* Register map (TRM S1.5, S1.7, S1.13, S1.14, S1.23) - identical to
 * mcu/src/drivers/kx134.c's map. */
#define KX134_REG_WHO_AM_I     0x13
#define KX134_REG_CNTL1        0x1B
#define KX134_REG_ODCNTL       0x21
#define KX134_REG_INC1         0x22
#define KX134_REG_INC4         0x25
#define KX134_REG_BUF_CNTL1    0x5E
#define KX134_REG_BUF_CNTL2    0x5F
#define KX134_REG_BUF_STATUS_1 0x60
#define KX134_REG_BUF_STATUS_2 0x61
#define KX134_REG_BUF_CLEAR    0x62
#define KX134_REG_BUF_READ     0x63

#define KX134_WHO_AM_I_VALUE 0x46

#define KX134_CNTL1_PC1 (1 << 7)
#define KX134_CNTL1_RES (1 << 6) /* 1 = High-Performance mode */
/* GSEL1:0 = 00 -> +/-8g (TRM Table 7) - same choice as mcu/'s kx134.c,
 * carried over unchanged (chip config, not MCU-specific). */
#define KX134_CNTL1_GSEL_8G 0x00
#define KX134_CNTL1_CONFIG_BITS (KX134_CNTL1_RES | KX134_CNTL1_GSEL_8G)

/* ODR = 12800Hz (OSA<3:0> = 1110, TRM Table 13, High-Performance mode) -
 * matches mpu/tools/satellite_node_sim.py's DEFAULT_SAMPLE_RATE_HZ
 * exactly, so a real node's accel data rate is indistinguishable from the
 * simulator's from the base station's point of view. */
#define KX134_ODCNTL_OSA_12800HZ 0x0E
#define KX134_ODR_HZ             12800

#define KX134_INC1_IEN1 (1 << 5) /* physical INT1 pin enabled */
#define KX134_INC1_IEA1 (1 << 4) /* active HIGH */
#define KX134_INC1_IEL1 (1 << 3) /* pulsed, not latched */
#define KX134_INC1_CONFIG (KX134_INC1_IEN1 | KX134_INC1_IEA1 | KX134_INC1_IEL1)

#define KX134_INC4_BFI1 (1 << 6) /* Buffer Full Interrupt -> INT1 */
#define KX134_INC4_CONFIG KX134_INC4_BFI1

#define KX134_BUF_CNTL2_BUFE (1 << 7)
#define KX134_BUF_CNTL2_BRES (1 << 6) /* 16-bit samples */
#define KX134_BUF_CNTL2_BFIE (1 << 5)
#define KX134_BUF_CNTL2_BM_STREAM 0x01 /* Stream mode: discard oldest when full */
#define KX134_BUF_CNTL2_CONFIG                                                                   \
	(KX134_BUF_CNTL2_BUFE | KX134_BUF_CNTL2_BRES | KX134_BUF_CNTL2_BFIE |                     \
	 KX134_BUF_CNTL2_BM_STREAM)

#define KX134_BUF_CNTL1_SMP_TH 32 /* valid mid-capacity value; BFI-driven, not watermark-driven */

#define KX134_FIFO_MAX_FRAMES      86
#define KX134_FIFO_BYTES_PER_FRAME 6 /* X_L,X_H,Y_L,Y_H,Z_L,Z_H per frame */

#define KX134_READ_TIMEOUT_MS 1000

static SPISettings kx134_spi_settings(KX134_SPI_CLOCK_HZ, MSBFIRST, SPI_MODE0);

/* Signaled by kx134_int1_isr() on every INT1 (Buffer Full) pulse, taken
 * by hal_accel_read_block(). Binary (not counting) semaphore - mirrors
 * mcu/'s kx134_data_ready_sem (K_SEM_DEFINE(..., 0, 1)): only whether a
 * frame is ready *now* matters, not how many BFI pulses fired since the
 * last read. */
static SemaphoreHandle_t kx134_data_ready_sem;

static void IRAM_ATTR kx134_int1_isr(void)
{
	BaseType_t woken = pdFALSE;

	xSemaphoreGiveFromISR(kx134_data_ready_sem, &woken);
	if (woken) {
		portYIELD_FROM_ISR();
	}
}

static inline void kx134_cs_select(void)
{
	digitalWrite(PIN_KX134_CS, LOW);
}

static inline void kx134_cs_deselect(void)
{
	digitalWrite(PIN_KX134_CS, HIGH);
}

static int kx134_write_reg(uint8_t addr, uint8_t value)
{
	SPI.beginTransaction(kx134_spi_settings);
	kx134_cs_select();
	SPI.transfer((uint8_t)(addr & 0x7F));
	SPI.transfer(value);
	kx134_cs_deselect();
	SPI.endTransaction();

	return 0;
}

/* Generic "send one command byte, then clock out len data bytes" read -
 * works for both ordinary auto-incrementing register reads and the
 * FIFO's BUF_READ streaming read, same as mcu/'s kx134_read_regs(). nCS
 * stays low for the whole transfer (SPI.transferBytes-style single
 * transaction), matching the TRM's "nCS can remain LOW until the buffer
 * is read" note for BUF_READ. */
static int kx134_read_regs(uint8_t addr, uint8_t *data, size_t len)
{
	SPI.beginTransaction(kx134_spi_settings);
	kx134_cs_select();
	SPI.transfer((uint8_t)(KX134_SPI_READ_BIT | (addr & 0x7F)));
	for (size_t i = 0; i < len; i++) {
		data[i] = SPI.transfer(0x00);
	}
	kx134_cs_deselect();
	SPI.endTransaction();

	return 0;
}

int hal_accel_init(void)
{
	pinMode(PIN_KX134_CS, OUTPUT);
	kx134_cs_deselect();

	/* Attach the hardware SPI peripheral to D8/D9/D10 (board_pins.h) via
	 * the GPIO matrix - without this the SPI bus is never actually
	 * brought up, so beginTransaction()/transfer() below silently no-op
	 * instead of driving the pins. SS left unset (-1): CS is software-
	 * controlled (kx134_cs_select/deselect), not SPI.begin()'s hardware
	 * SS. */
	SPI.begin(PIN_KX134_SCK, PIN_KX134_MISO, PIN_KX134_MOSI, -1);

	kx134_data_ready_sem = xSemaphoreCreateBinary();
	if (kx134_data_ready_sem == NULL) {
		return -ENOMEM;
	}

	uint8_t who_am_i;
	int ret = kx134_read_regs(KX134_REG_WHO_AM_I, &who_am_i, 1);

	if (ret < 0) {
		return ret;
	}
	if (who_am_i != KX134_WHO_AM_I_VALUE) {
		Serial.printf("[kx134] WHO_AM_I mismatch: got 0x%02x, expected 0x%02x\n", who_am_i,
			      KX134_WHO_AM_I_VALUE);
		return -ENODEV;
	}

	/* Arm the INT1 GPIO before enabling the chip's own INC1/INC4
	 * interrupt routing below, mirroring mcu/'s ordering. */
	pinMode(PIN_KX134_INT1, INPUT);
	attachInterrupt(digitalPinToInterrupt(PIN_KX134_INT1), kx134_int1_isr, RISING);

	/* Standby (PC1=0) before touching any other register - required by
	 * the TRM for CNTL1/ODCNTL/INC1/INC4 writes. */
	ret = kx134_write_reg(KX134_REG_CNTL1, 0x00);
	if (ret < 0) {
		return ret;
	}

	ret = kx134_write_reg(KX134_REG_ODCNTL, KX134_ODCNTL_OSA_12800HZ);
	if (ret < 0) {
		return ret;
	}

	ret = kx134_write_reg(KX134_REG_INC1, KX134_INC1_CONFIG);
	if (ret < 0) {
		return ret;
	}

	ret = kx134_write_reg(KX134_REG_INC4, KX134_INC4_CONFIG);
	if (ret < 0) {
		return ret;
	}

	ret = kx134_write_reg(KX134_REG_CNTL1, KX134_CNTL1_CONFIG_BITS);
	if (ret < 0) {
		return ret;
	}

	ret = kx134_write_reg(KX134_REG_BUF_CNTL1, KX134_BUF_CNTL1_SMP_TH);
	if (ret < 0) {
		return ret;
	}

	return kx134_write_reg(KX134_REG_BUF_CNTL2, KX134_BUF_CNTL2_CONFIG);
}

int hal_accel_start(void)
{
	int ret = kx134_write_reg(KX134_REG_BUF_CLEAR, 0x00);

	if (ret < 0) {
		return ret;
	}

	return kx134_write_reg(KX134_REG_CNTL1, KX134_CNTL1_CONFIG_BITS | KX134_CNTL1_PC1);
}

int hal_accel_read_block(int32_t *out_samples, size_t max_samples)
{
	static uint8_t raw[KX134_FIFO_MAX_FRAMES * KX134_FIFO_BYTES_PER_FRAME];
	size_t frames;

	if (xSemaphoreTake(kx134_data_ready_sem, pdMS_TO_TICKS(KX134_READ_TIMEOUT_MS)) != pdTRUE) {
		Serial.printf("[kx134] no accel frame after %ums\n", KX134_READ_TIMEOUT_MS);
		return -ETIMEDOUT;
	}

	uint8_t status[2];
	int ret = kx134_read_regs(KX134_REG_BUF_STATUS_1, status, sizeof(status));

	if (ret < 0) {
		return ret;
	}

	uint16_t smp_lev = status[0] | ((uint16_t)(status[1] & 0x03) << 8);

	frames = smp_lev / KX134_FIFO_BYTES_PER_FRAME;
	frames = min(frames, min(max_samples, (size_t)KX134_FIFO_MAX_FRAMES));

	ret = kx134_read_regs(KX134_REG_BUF_READ, raw, frames * KX134_FIFO_BYTES_PER_FRAME);
	if (ret < 0) {
		return ret;
	}

	/* Each 6-byte frame is X_L,X_H,Y_L,Y_H,Z_L,Z_H, little-endian 2's
	 * complement per axis - sign-extend to int32_t. */
	for (size_t i = 0; i < frames; i++) {
		const uint8_t *p = &raw[i * KX134_FIFO_BYTES_PER_FRAME];
		int16_t x = (int16_t)(p[0] | (p[1] << 8));
		int16_t y = (int16_t)(p[2] | (p[3] << 8));
		int16_t z = (int16_t)(p[4] | (p[5] << 8));

		out_samples[i * 3 + 0] = x;
		out_samples[i * 3 + 1] = y;
		out_samples[i * 3 + 2] = z;
	}

	return (int)frames;
}

uint32_t hal_accel_get_sample_rate(void)
{
	return KX134_ODR_HZ;
}

void hal_accel_stop(void)
{
	(void)kx134_write_reg(KX134_REG_CNTL1, KX134_CNTL1_CONFIG_BITS);
}
