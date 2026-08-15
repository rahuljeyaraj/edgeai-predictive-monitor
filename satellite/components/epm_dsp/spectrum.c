#include "dsp/spectrum.h"

#include <math.h>

int epm_dsp_welch_hop_size(int fft_n, int overlap_pct)
{
    int overlap_n = (overlap_pct * fft_n) / 100;
    int hop_n     = fft_n - overlap_n;
    if (hop_n < 1) hop_n = 1;
    return hop_n;
}

void epm_dsp_accumulate_power(const float *fft_interleaved, float *pwr_acc,
                               int half_n, float norm_factor)
{
    for (int i = 0; i < half_n; i++) {
        float re = fft_interleaved[2 * i]     * norm_factor;
        float im = fft_interleaved[2 * i + 1] * norm_factor;
        pwr_acc[i] += re * re + im * im;
    }
}

void epm_dsp_power_to_db(float *pwr_acc, float *mag_db_out, int half_n, float inv_n)
{
    for (int i = 0; i < half_n; i++) {
        mag_db_out[i] = 10.0f * log10f(pwr_acc[i] * inv_n + 1e-12f);
        pwr_acc[i]    = 0.0f;
    }
    mag_db_out[0] = -120.0f;   /* DC bin */
}

int epm_dsp_reduce_bins(const float *in_db, int in_n, float *out_db, int out_n)
{
    if (in_n <= 0 || out_n <= 0 || out_n > in_n || in_n % out_n != 0) {
        return -1;
    }

    int band = in_n / out_n;

    for (int i = 0; i < out_n; i++) {
        float pwr_sum = 0.0f;

        for (int j = 0; j < band; j++) {
            pwr_sum += powf(10.0f, in_db[i * band + j] / 10.0f);
        }

        float avg_pwr = pwr_sum / (float)band;
        out_db[i]     = 10.0f * log10f(avg_pwr + 1e-12f);
    }

    return 0;
}
