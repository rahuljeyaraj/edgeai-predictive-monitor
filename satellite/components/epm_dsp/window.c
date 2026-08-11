#include "dsp/window.h"

float epm_dsp_coherent_gain(const float *window, int n)
{
    float sum = 0.0f;
    for (int i = 0; i < n; i++) {
        sum += window[i];
    }
    return sum / (float)n;
}
