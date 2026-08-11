/*
 * accel_kx134_spi.c — KX134-1211 3-axis accelerometer over SPI, behind
 * hal_accel.h.  ESP-IDF native driver/spi_master.h + driver/gpio.h port of
 * the reference repo's satellite/src/drivers/kx134.cpp (Arduino/SPI.h +
 * attachInterrupt). Register map, bit-field values, and the CNTL1/ODCNTL/
 * INC1/INC4/BUF_CNTL1/BUF_CNTL2 init sequence are carried over verbatim —
 * same chip, same datasheet (KX134-1211 TRM Rev 5.0) — only the SPI
 * transaction mechanics and the ISR/semaphore glue are re-expressed in
 * ESP-IDF terms. The reference boards were hardware-validated at ODR=
 * 12800Hz (docs/KX134_Interface_Appendix.md, docs/SATELLITE_BRINGUP_GUIDE.md);
 * this driver instead runs the chip at 25600Hz (OSA=0x0F) to match
 * src/epm_config.h's IMU_FS_HZ and clear the reference project's reported
 * 8kHz accel ceiling with margin — see KX134_ODCNTL_OSA_25600HZ below for
 * why, and its own validation status.
 *
 * SPI mode: Mode 0 (CPOL=0, CPHA=0), MSB-first, <=10MHz, per the datasheet's
 * 4-wire timing diagram.
 *
 * Pin plan (docs/decisions/ADR-017): SCK/MISO/MOSI = GPIO7/8/9 (D8/D9/D10,
 * hardware SPI2, matches the reference's own XIAO ESP32S3 pin choice — no
 * conflict, these pins have no other claimant on our board either). CS and
 * INT1 do NOT reuse the reference's D3/D2 (GPIO4/GPIO3): those are occupied
 * on our board by the INMP441 mic (mic_inmp441_i2s.c: BCLK=GPIO2, WS=GPIO3,
 * SD=GPIO4 — a different, incompatible pin assignment from the reference's
 * own mic wiring). CS=GPIO43 (D6), INT1=GPIO44 (D7) instead — the XIAO's
 * UART0 TX/RX pins, confirmed free (platformio.ini: this board's console
 * runs over USB-JTAG/CDC, not UART0).
 *
 * hal_accel.h's contract is per-axis normalised-g float, deliberately
 * different from the reference's interleaved raw int32_t (see that
 * header's comment). The KX134's FIFO is physically interleaved
 * (X,Y,Z per frame) and a single BFI burst yields at most
 * KX134_FIFO_MAX_FRAMES samples — far fewer than one imu_task.c epoch
 * (FFT_IMU_N, currently 2048). src/threads/imu_task.c always calls this
 * driver X, then Y, then Z, once per epoch (verified live in that file):
 * the X call drains as many FIFO bursts as needed to fill max_samples,
 * caching the Y/Z decode alongside it, so the Y/Z calls that follow just
 * copy from that cache with no further hardware transaction. This ordering
 * is a documented requirement of this specific driver, not a general
 * hal_accel.h guarantee.
 */

#include <string.h>
#include <errno.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

#include "esp_attr.h"
#include "esp_log.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"

#include "hal/hal_accel.h"

static const char *TAG = "kx134";

/* ─── Pins ──────────────────────────────────────────────────────────────── */

#define KX134_PIN_SCK   GPIO_NUM_7   /* D8 — hardware SPI2, shared bus */
#define KX134_PIN_MISO  GPIO_NUM_8   /* D9 */
#define KX134_PIN_MOSI  GPIO_NUM_9   /* D10 */
#define KX134_PIN_CS    GPIO_NUM_43  /* D6 — UART0 TX, unused (USB-JTAG console) */
#define KX134_PIN_INT1  GPIO_NUM_44  /* D7 — UART0 RX, unused */

#define KX134_SPI_HOST      SPI2_HOST
#define KX134_SPI_CLOCK_HZ  10000000

/* ─── Register map (TRM S1.5, S1.7, S1.13, S1.14, S1.23) ───────────────── */

#define KX134_SPI_READ_BIT 0x80

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
/* GSEL1:0 = 00 -> +/-8g (TRM Table 7) */
#define KX134_CNTL1_GSEL_8G 0x00
#define KX134_CNTL1_CONFIG_BITS (KX134_CNTL1_RES | KX134_CNTL1_GSEL_8G)

/* ODR = 25600Hz (OSA<3:0> = 1111, TRM Table 13, High-Performance mode).
 * Raised from 12800Hz (0x0E) to match src/epm_config.h's IMU_FS_HZ=25600 --
 * see docs/decisions/ADR-017's "ODR mismatch, out of scope to fix here".
 * IMU_FS_HZ was always meant to give a 12800Hz Nyquist (exceeding the
 * reference project's reported 8kHz accel ceiling); the chip was silently
 * running at half that rate since ADR-017 landed. Needs real-hardware
 * validation at this new rate (FIFO/SPI headroom, zero dropped frames)
 * before this is trusted -- doubling the ODR halves the time between BFI
 * bursts, which was not exercised by ADR-017's 12800Hz sustained run. */
#define KX134_ODCNTL_OSA_25600HZ 0x0F
#define KX134_ODR_HZ             25600

/* +/-8g range, 16-bit output -> 4096 counts/g (32768 / 8). TRM sensitivity
 * table for GSEL=00/BRES=1. Flagged for bring-up confirmation: at-rest Z
 * axis should read ~1.0g under this constant (docs/decisions/ADR-017). */
#define KX134_COUNTS_PER_G 4096.0f

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
#define KX134_BUF_CNTL2_CONFIG \
    (KX134_BUF_CNTL2_BUFE | KX134_BUF_CNTL2_BRES | KX134_BUF_CNTL2_BFIE | \
     KX134_BUF_CNTL2_BM_STREAM)

#define KX134_BUF_CNTL1_SMP_TH 32 /* valid mid-capacity value; BFI-driven, not watermark-driven */

#define KX134_FIFO_MAX_FRAMES      86
#define KX134_FIFO_BYTES_PER_FRAME 6 /* X_L,X_H,Y_L,Y_H,Z_L,Z_H per frame */

#define KX134_READ_TIMEOUT_MS 1000

/* Upper bound on samples per hal_accel_read_block() call this driver can
 * stage internally for the Y/Z cache (see file header). Not tied to
 * src/epm_config.h's FFT_IMU_N -- epm_drivers must not depend on the main
 * component's config, same rule accel_stub.c documents for IMU_FS_HZ.
 * Sized to the current FFT_IMU_N (2048) with no extra headroom: on-device
 * testing showed this board has too little free heap at boot for a larger
 * static cache (see s_stage_y/s_stage_z below) -- xQueueCreate() elsewhere
 * in the boot sequence (src/threads/imu_task.c) failed outright the first
 * time this was sized at 4096. If FFT_IMU_N is ever raised above this, the
 * bound check in kx134_fill_epoch() below fails loudly (-EINVAL) rather
 * than silently truncating. */
#define KX134_STAGE_MAX_SAMPLES 2048

/* ─── State ─────────────────────────────────────────────────────────────── */

static spi_device_handle_t s_spi = NULL;
static SemaphoreHandle_t   s_data_ready_sem = NULL;

/* DMA-capable: internal DRAM required, ESP32-S3 SPI DMA cannot address
 * PSRAM. Sized for one FIFO burst (address byte + up to 86 frames). */
static DMA_ATTR uint8_t s_burst_buf[1 + KX134_FIFO_MAX_FRAMES * KX134_FIFO_BYTES_PER_FRAME];

/* Y/Z cache filled by the X call in an epoch, drained by the Y and Z calls
 * that follow (see file header). Stored as raw sign-extended counts, not
 * pre-converted float g-values -- halves this cache's RAM footprint, which
 * matters on this board (see KX134_STAGE_MAX_SAMPLES above). Converted to
 * g in hal_accel_read_block() at copy-out time instead. */
static int16_t s_stage_y[KX134_STAGE_MAX_SAMPLES];
static int16_t s_stage_z[KX134_STAGE_MAX_SAMPLES];
static size_t  s_stage_n = 0;

/* Per-burst frame-decode scratch, copied out of s_burst_buf by
 * kx134_read_regs(). File-scope, not a kx134_fill_epoch() local: this
 * driver is only ever called from imu_task's stack (see the stack-overflow
 * incident documented in src/epm_config.h's stack-sizing comment), and a
 * 516-byte array here was silently eating into that task's stack budget on
 * every FIFO burst. */
static uint8_t s_raw_frame_buf[KX134_FIFO_MAX_FRAMES * KX134_FIFO_BYTES_PER_FRAME];

/* ── Stats (Part I: one <module>_get_stats() accessor per module) ────────── */

static uint32_t s_reads_ok      = 0;
static uint32_t s_read_errors   = 0;
static uint32_t s_fifo_max_hits = 0;

void hal_accel_get_stats(struct hal_accel_stats *out)
{
    if (out == NULL) return;
    out->reads_ok      = s_reads_ok;
    out->read_errors   = s_read_errors;
    out->fifo_max_hits = s_fifo_max_hits;
}

static IRAM_ATTR void kx134_int1_isr(void *arg)
{
    (void)arg;
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(s_data_ready_sem, &woken);
    if (woken) {
        portYIELD_FROM_ISR();
    }
}

static esp_err_t kx134_write_reg(uint8_t addr, uint8_t value)
{
    uint8_t tx[2] = { (uint8_t)(addr & 0x7F), value };
    spi_transaction_t t = {
        .length    = 16,
        .tx_buffer = tx,
    };
    return spi_device_polling_transmit(s_spi, &t);
}

/* Generic "send one command byte, then clock out len data bytes" read —
 * works for both ordinary auto-incrementing register reads and the FIFO's
 * BUF_READ streaming read. Hardware CS (spics_io_num) keeps nCS low for
 * the whole transaction, matching the TRM's "nCS can remain LOW until the
 * buffer is read" note for BUF_READ. */
static esp_err_t kx134_read_regs(uint8_t addr, uint8_t *data, size_t len)
{
    if (1 + len > sizeof(s_burst_buf)) {
        return ESP_ERR_INVALID_SIZE;
    }

    memset(s_burst_buf, 0, 1 + len);
    s_burst_buf[0] = (uint8_t)(KX134_SPI_READ_BIT | (addr & 0x7F));

    spi_transaction_t t = {
        .length    = (1 + len) * 8,
        .tx_buffer = s_burst_buf,
        .rx_buffer = s_burst_buf,
    };
    esp_err_t err = spi_device_polling_transmit(s_spi, &t);
    if (err == ESP_OK) {
        memcpy(data, &s_burst_buf[1], len);
    }
    return err;
}

/* WHO_AM_I readback — shared by hal_accel_init() (first boot) and
 * hal_accel_reinit() (post-fault recovery): both need to confirm the chip
 * is actually present/responding before programming registers into it. */
static int kx134_check_who_am_i(void)
{
    uint8_t who_am_i = 0;
    if (kx134_read_regs(KX134_REG_WHO_AM_I, &who_am_i, 1) != ESP_OK) {
        return -EIO;
    }
    if (who_am_i != KX134_WHO_AM_I_VALUE) {
        ESP_LOGE(TAG, "WHO_AM_I mismatch: got 0x%02x, expected 0x%02x",
                  who_am_i, KX134_WHO_AM_I_VALUE);
        return -ENODEV;
    }
    ESP_LOGI(TAG, "WHO_AM_I OK (0x%02x)", who_am_i);
    return 0;
}

/* CNTL1/ODCNTL/INC1/INC4/BUF_CNTL1/BUF_CNTL2 program sequence — the part of
 * bring-up that lives on the KX134 itself, not the ESP32 side (SPI bus,
 * semaphore, GPIO/ISR). A full power-loss to the sensor resets exactly this
 * state (interrupt routing included) and nothing else, so this is also the
 * whole of what hal_accel_reinit() below needs to re-run. */
static int kx134_program_registers(void)
{
    /* Standby (PC1=0) before touching any other register — required by the
     * TRM for CNTL1/ODCNTL/INC1/INC4 writes. */
    if (kx134_write_reg(KX134_REG_CNTL1, 0x00) != ESP_OK) return -EIO;
    if (kx134_write_reg(KX134_REG_ODCNTL, KX134_ODCNTL_OSA_25600HZ) != ESP_OK) return -EIO;
    if (kx134_write_reg(KX134_REG_INC1, KX134_INC1_CONFIG) != ESP_OK) return -EIO;
    if (kx134_write_reg(KX134_REG_INC4, KX134_INC4_CONFIG) != ESP_OK) return -EIO;
    if (kx134_write_reg(KX134_REG_CNTL1, KX134_CNTL1_CONFIG_BITS) != ESP_OK) return -EIO;
    if (kx134_write_reg(KX134_REG_BUF_CNTL1, KX134_BUF_CNTL1_SMP_TH) != ESP_OK) return -EIO;
    if (kx134_write_reg(KX134_REG_BUF_CNTL2, KX134_BUF_CNTL2_CONFIG) != ESP_OK) return -EIO;
    return 0;
}

int hal_accel_init(void)
{
    esp_err_t err;

    s_data_ready_sem = xSemaphoreCreateBinary();
    if (s_data_ready_sem == NULL) {
        return -ENOMEM;
    }

    spi_bus_config_t buscfg = {
        .sclk_io_num = KX134_PIN_SCK,
        .miso_io_num = KX134_PIN_MISO,
        .mosi_io_num = KX134_PIN_MOSI,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = sizeof(s_burst_buf),
    };
    err = spi_bus_initialize(KX134_SPI_HOST, &buscfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "spi_bus_initialize failed: %s", esp_err_to_name(err));
        return -EIO;
    }

    spi_device_interface_config_t devcfg = {
        .clock_speed_hz = KX134_SPI_CLOCK_HZ,
        .mode = 0,
        .spics_io_num = KX134_PIN_CS,
        .queue_size = 1,
        /* HW-OPT (sdkconfig.defaults CONFIG_SPI_MASTER_ISR_IN_IRAM): keeps
         * this driver's completion ISR reachable during a WiFi TX burst's
         * flash-cache-disabled window, avoiding a transaction overrun. */
        .flags = 0,
    };
    err = spi_bus_add_device(KX134_SPI_HOST, &devcfg, &s_spi);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "spi_bus_add_device failed: %s", esp_err_to_name(err));
        return -EIO;
    }

    int rc = kx134_check_who_am_i();
    if (rc != 0) return rc;

    /* Arm the INT1 GPIO before enabling the chip's own INC1/INC4 interrupt
     * routing below. */
    gpio_config_t int1_cfg = {
        .pin_bit_mask = 1ULL << KX134_PIN_INT1,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_POSEDGE,
    };
    gpio_config(&int1_cfg);

    static bool isr_service_installed = false;
    if (!isr_service_installed) {
        err = gpio_install_isr_service(ESP_INTR_FLAG_IRAM);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            ESP_LOGE(TAG, "gpio_install_isr_service failed: %s", esp_err_to_name(err));
            return -EIO;
        }
        isr_service_installed = true;
    }
    gpio_isr_handler_add(KX134_PIN_INT1, kx134_int1_isr, NULL);

    return kx134_program_registers();
}

int hal_accel_start(void)
{
    if (kx134_write_reg(KX134_REG_BUF_CLEAR, 0x00) != ESP_OK) return -EIO;
    if (kx134_write_reg(KX134_REG_CNTL1, KX134_CNTL1_CONFIG_BITS | KX134_CNTL1_PC1) != ESP_OK) return -EIO;
    return 0;
}

int hal_accel_reinit(void)
{
    /* Drain a stale semaphore count that may have accumulated while the
     * sensor was faulted: INT1 (a real GPIO, still armed edge-triggered
     * the whole time) can float and pick up a spurious edge while the
     * KX134 is unpowered/disconnected. Without this, the first
     * xSemaphoreTake() after this reinit could return immediately on that
     * leftover count instead of waiting for a genuine post-reinit
     * interrupt, and kx134_fill_epoch() would read BUF_STATUS_1 from a
     * chip whose FIFO the BUF_CLEAR in hal_accel_start() below hasn't run
     * on yet. Non-blocking take, result deliberately discarded either way.
     *
     * s_stage_n/s_stage_y/s_stage_z need no reset here: kx134_fill_epoch()
     * already zeroes s_stage_n at the top of every call and both stage
     * arrays are only ever read back within that same call's bounds, so no
     * state carries across epochs or reinits (confirmed by reading that
     * function in full, not just its header comment). */
    xSemaphoreTake(s_data_ready_sem, 0);

    int rc = kx134_check_who_am_i();
    if (rc != 0) return rc;

    rc = kx134_program_registers();
    if (rc != 0) return rc;

    return hal_accel_start();
}

/* Drains FIFO bursts (blocking on the BFI semaphore each time) until
 * s_stage_n reaches want, decoding into out_x (caller's buffer, X axis
 * served directly) plus s_stage_y/s_stage_z (cached for the Y/Z calls that
 * follow this epoch's X call). */
static int kx134_fill_epoch(float *out_x, size_t want)
{
    if (want > KX134_STAGE_MAX_SAMPLES) {
        ESP_LOGE(TAG, "requested %u samples exceeds stage cap %u",
                  (unsigned)want, (unsigned)KX134_STAGE_MAX_SAMPLES);
        return -EINVAL;
    }

    s_stage_n = 0;
    double   sum_x = 0.0, sum_y = 0.0, sum_z = 0.0;
    uint16_t max_smp_lev = 0;

    while (s_stage_n < want) {
        if (xSemaphoreTake(s_data_ready_sem, pdMS_TO_TICKS(KX134_READ_TIMEOUT_MS)) != pdTRUE) {
            ESP_LOGE(TAG, "no accel frame after %ums", KX134_READ_TIMEOUT_MS);
            return -ETIMEDOUT;
        }

        uint8_t status[2];
        if (kx134_read_regs(KX134_REG_BUF_STATUS_1, status, sizeof(status)) != ESP_OK) {
            return -EIO;
        }
        uint16_t smp_lev = status[0] | ((uint16_t)(status[1] & 0x03) << 8);
        if (smp_lev > max_smp_lev) max_smp_lev = smp_lev;

        size_t frames = smp_lev / KX134_FIFO_BYTES_PER_FRAME;
        if (frames > KX134_FIFO_MAX_FRAMES) frames = KX134_FIFO_MAX_FRAMES;
        if (frames > want - s_stage_n) frames = want - s_stage_n;
        if (frames == 0) continue;

        if (kx134_read_regs(KX134_REG_BUF_READ, s_raw_frame_buf, frames * KX134_FIFO_BYTES_PER_FRAME) != ESP_OK) {
            return -EIO;
        }

        /* Each 6-byte frame is X_L,X_H,Y_L,Y_H,Z_L,Z_H, little-endian 2's
         * complement per axis. Convert counts -> normalised g here, since
         * hal_accel.h requires g-units (the reference passes raw counts
         * through unconverted — that conversion has no source to port). */
        for (size_t i = 0; i < frames; i++) {
            const uint8_t *p = &s_raw_frame_buf[i * KX134_FIFO_BYTES_PER_FRAME];
            int16_t x = (int16_t)(p[0] | (p[1] << 8));
            int16_t y = (int16_t)(p[2] | (p[3] << 8));
            int16_t z = (int16_t)(p[4] | (p[5] << 8));

            out_x[s_stage_n + i]      = (float)x / KX134_COUNTS_PER_G;
            s_stage_y[s_stage_n + i]  = y;
            s_stage_z[s_stage_n + i]  = z;

            sum_x += out_x[s_stage_n + i];
            sum_y += (float)y / KX134_COUNTS_PER_G;
            sum_z += (float)z / KX134_COUNTS_PER_G;
        }
        s_stage_n += frames;
    }

    /* Bring-up diagnostic, not a hardware-reported drop counter (the KX134
     * has no dropped-sample register): BUF_CNTL1_SMP_TH=32 is the BFI
     * watermark, well below the 86-frame physical capacity, so a burst
     * observed at/near max capacity here means this task fell behind the
     * ODR far enough that BM_STREAM's discard-oldest-on-overflow policy may
     * have already silently dropped data before this poll. */
    size_t max_smp_frames = max_smp_lev / KX134_FIFO_BYTES_PER_FRAME;
    if (max_smp_frames >= KX134_FIFO_MAX_FRAMES) {
        s_fifo_max_hits++;
        ESP_LOGW(TAG, "FIFO seen at max capacity (%u/%u frames) -- possible dropped samples",
                  (unsigned)max_smp_frames, (unsigned)KX134_FIFO_MAX_FRAMES);
    }
    ESP_LOGI(TAG, "epoch: n=%u mean_g x=%.3f y=%.3f z=%.3f max_fifo=%u/%u",
              (unsigned)s_stage_n, (float)(sum_x / s_stage_n), (float)(sum_y / s_stage_n),
              (float)(sum_z / s_stage_n), (unsigned)max_smp_frames, (unsigned)KX134_FIFO_MAX_FRAMES);

    return (int)s_stage_n;
}

int hal_accel_read_block(enum hal_accel_axis axis, float *out_samples, size_t max_samples)
{
    int ret;

    if (axis == HAL_ACCEL_AXIS_X) {
        ret = kx134_fill_epoch(out_samples, max_samples);
    } else if (axis == HAL_ACCEL_AXIS_Y || axis == HAL_ACCEL_AXIS_Z) {
        /* Y/Z: served from the cache the X call just filled. Per this
         * driver's documented ordering requirement, X is always called
         * first each epoch (verified against src/threads/imu_task.c).
         * Cache holds raw counts (see s_stage_y/s_stage_z above),
         * converted to g here. */
        size_t n = (max_samples < s_stage_n) ? max_samples : s_stage_n;
        if (axis == HAL_ACCEL_AXIS_Y) {
            for (size_t i = 0; i < n; i++) out_samples[i] = (float)s_stage_y[i] / KX134_COUNTS_PER_G;
        } else {
            for (size_t i = 0; i < n; i++) out_samples[i] = (float)s_stage_z[i] / KX134_COUNTS_PER_G;
        }
        ret = (int)n;
    } else {
        ret = -1;
    }

    if (ret >= 0) s_reads_ok++; else s_read_errors++;
    return ret;
}

uint32_t hal_accel_get_sample_rate(void)
{
    return KX134_ODR_HZ;
}

void hal_accel_stop(void)
{
    (void)kx134_write_reg(KX134_REG_CNTL1, KX134_CNTL1_CONFIG_BITS);
}
