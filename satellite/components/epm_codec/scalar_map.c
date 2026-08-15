#include "frame_codec/scalar_map.h"

void scalar_map_build_axis(const struct axis_scalars *s, uint16_t id_base,
			    struct scalar_entry out[6])
{
	out[0] = (struct scalar_entry){.id = (uint16_t)(id_base + 0), .value = s->rms};
	out[1] = (struct scalar_entry){.id = (uint16_t)(id_base + 1), .value = s->kurtosis};
	out[2] = (struct scalar_entry){.id = (uint16_t)(id_base + 2), .value = s->std};
	out[3] = (struct scalar_entry){.id = (uint16_t)(id_base + 3), .value = s->peak};
	out[4] = (struct scalar_entry){.id = (uint16_t)(id_base + 4), .value = s->crest};
	out[5] = (struct scalar_entry){.id = (uint16_t)(id_base + 5), .value = s->skewness};
}
