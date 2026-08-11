#include "dsp/envelope.h"

#include <math.h>
#include <stddef.h>

static void biquad_set(epm_dsp_biquad_t *bq, float b0, float b1, float b2,
                        float a0, float a1, float a2)
{
    bq->b0 = b0 / a0;
    bq->b1 = b1 / a0;
    bq->b2 = b2 / a0;
    bq->a1 = a1 / a0;
    bq->a2 = a2 / a0;
    bq->z1 = 0.0f;
    bq->z2 = 0.0f;
}

/* RBJ Audio EQ Cookbook lowpass/highpass biquad design equations. */
void epm_dsp_biquad_init_lowpass(epm_dsp_biquad_t *bq, float fs_hz, float fc_hz, float q)
{
    float w0     = 2.0f * 3.14159265358979323846f * fc_hz / fs_hz;
    float cosw0  = cosf(w0);
    float alpha  = sinf(w0) / (2.0f * q);

    float b0 = (1.0f - cosw0) / 2.0f;
    float b1 =  1.0f - cosw0;
    float b2 = (1.0f - cosw0) / 2.0f;
    float a0 =  1.0f + alpha;
    float a1 = -2.0f * cosw0;
    float a2 =  1.0f - alpha;

    biquad_set(bq, b0, b1, b2, a0, a1, a2);
}

void epm_dsp_biquad_init_highpass(epm_dsp_biquad_t *bq, float fs_hz, float fc_hz, float q)
{
    float w0     = 2.0f * 3.14159265358979323846f * fc_hz / fs_hz;
    float cosw0  = cosf(w0);
    float alpha  = sinf(w0) / (2.0f * q);

    float b0 =  (1.0f + cosw0) / 2.0f;
    float b1 = -(1.0f + cosw0);
    float b2 =  (1.0f + cosw0) / 2.0f;
    float a0 =   1.0f + alpha;
    float a1 =  -2.0f * cosw0;
    float a2 =   1.0f - alpha;

    biquad_set(bq, b0, b1, b2, a0, a1, a2);
}

/* Direct Form II Transposed. */
float epm_dsp_biquad_process(epm_dsp_biquad_t *bq, float x)
{
    float y = bq->b0 * x + bq->z1;
    bq->z1 = bq->b1 * x - bq->a1 * y + bq->z2;
    bq->z2 = bq->b2 * x - bq->a2 * y;
    return y;
}

void epm_dsp_biquad_reset(epm_dsp_biquad_t *bq)
{
    bq->z1 = 0.0f;
    bq->z2 = 0.0f;
}

void epm_dsp_envelope_init(epm_dsp_envelope_t *env, float fs_hz,
                            float band_lo_hz, float band_hi_hz, float env_lp_hz)
{
    const float q = 0.70710678f; /* Butterworth, maximally flat */
    epm_dsp_biquad_init_highpass(&env->band_hp, fs_hz, band_lo_hz, q);
    epm_dsp_biquad_init_lowpass(&env->band_lp, fs_hz, band_hi_hz, q);
    epm_dsp_biquad_init_lowpass(&env->env_lp, fs_hz, env_lp_hz, q);
}

int epm_dsp_envelope_process(epm_dsp_envelope_t *env, const float *in, int in_n,
                              int decim_m, float *out, int out_n)
{
    if (env == NULL || in == NULL || out == NULL ||
        in_n <= 0 || decim_m <= 0 || out_n <= 0 || in_n != out_n * decim_m) {
        return -1;
    }

    int oi = 0;
    for (int i = 0; i < in_n; i++) {
        float x = in[i];
        x = epm_dsp_biquad_process(&env->band_hp, x);
        x = epm_dsp_biquad_process(&env->band_lp, x);
        x = fabsf(x);                                    /* full-wave rectify */
        x = epm_dsp_biquad_process(&env->env_lp, x);

        if ((i % decim_m) == decim_m - 1) {
            out[oi++] = x;
        }
    }

    return 0;
}
